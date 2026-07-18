"""
Analytical stacking quality assessment.

Runs on a completed stack FITS and returns physics-grounded metrics:

  sigma_sky        Background noise floor (sigma-clipped std, ADU)
  snr_stack        signal_rms / sigma_sky — matches image_analyzer.py per-frame SNR definition
  fwhm_stack       Median star FWHM in pixels (alignment quality)
  ecc_stack        Median star eccentricity in the stack
  flatness_rms     RMS of SEP background map (gradient indicator)
  clipping_frac    Fraction of pixels within 0.1% of data extremes (float-safe)
  star_count       Number of point-like sources detected in the stack
  efficiency       SNR_stack / (median_single_SNR × √N)
                   Physics-grounded: 1.0 = perfect, <0.7 = something wrong

efficiency is the primary comparison metric between engines on the same inputs.

SNR definition matches image_analyzer._noise() so efficiency is meaningful:
  per-frame SNR  = std(full_frame) / sigma_clipped_std(full_frame)
  stack SNR      = std(data_sub)   / sigma_sky
  efficiency     = snr_stack / (median_frame_snr × √N)
  → 1.0 when stack noise drops exactly as √N theory predicts.
"""
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def assess_stack(fits_path: Path | str,
                 frame_count: int,
                 single_frame_snrs: list[float] | None = None,
                 mask_zero_border: bool = False) -> dict:
    """
    Measure stacking quality from a completed stack FITS file.

    Args:
        fits_path:         Path to the stacked FITS.
        frame_count:       Number of frames included in the stack.
        single_frame_snrs: Per-frame SNR values for frames that went into
                           the stack (from light_files.snr). Used to compute
                           efficiency. If None or empty, efficiency is None.
        mask_zero_border:  If True, exclude exact-zero pixels (the black border
                           of a max-framing union canvas) from all measurements.
                           Without this, the large zero-border inflates sigma_sky
                           and makes efficiency misleadingly low for mosaics.

    Returns dict with all metrics (None for any that fail).
    """
    fits_path = Path(fits_path)
    result = {
        "sigma_sky": None,
        "snr_stack": None,
        "fwhm_stack": None,
        "ecc_stack": None,
        "flatness_rms": None,
        "clipping_frac": None,
        "star_count": None,
        "efficiency": None,
    }

    try:
        import sep
        from astropy.io import fits as _fits
        from astropy.stats import sigma_clipped_stats

        with _fits.open(str(fits_path)) as hdul:
            raw = hdul[0].data.astype(np.float32)

        # CHW → HWC for colour FITS — use luminance (mean of RGB) for photometry
        if raw.ndim == 3:
            data = np.mean(np.transpose(raw, (1, 2, 0)), axis=2)
        else:
            data = raw

        data = np.ascontiguousarray(data)

        # For max-framing union canvases, mask the exact-zero border regions so they
        # don't inflate sky noise or drag down efficiency.
        if mask_zero_border:
            valid_mask = data != 0.0
            valid_frac = float(valid_mask.mean())
            log.info(f"[assess] zero-border masking: {valid_frac:.1%} of canvas has data")
            if valid_frac < 0.05:
                log.warning("[assess] >95% of canvas is zero — disabling mask (unexpected)")
                valid_mask = None
        else:
            valid_mask = None

        # --- Background map ---
        # bw=256 so extended objects (galaxies, nebulae) don't bias local sky estimates
        bkg = sep.Background(data, bw=256, bh=256, fw=3, fh=3)
        data_sub_2d = data - bkg.back()           # always 2D — needed for SEP extraction
        data_stats = data_sub_2d[valid_mask] if valid_mask is not None else data_sub_2d.ravel()
        result["flatness_rms"] = float(np.std(bkg.back()))

        # --- Sky noise (sigma-clipped std of background-subtracted image) ---
        _, _, sky_std = sigma_clipped_stats(data_stats, sigma=3.0, maxiters=5)
        sky_std = max(float(sky_std), 1e-12)
        result["sigma_sky"] = float(sky_std)

        # --- SNR: same definition as image_analyzer._noise() for efficiency consistency ---
        # signal_rms / sigma_sky — scale-independent, works for any float FITS range
        signal_rms = float(np.std(data_stats))
        result["snr_stack"] = signal_rms / sky_std

        # --- Clipping fraction (float-safe: relative to dynamic range, not ±1 ADU) ---
        lo = np.percentile(data_stats, 0.05)
        hi = np.percentile(data_stats, 99.95)
        data_range = max(hi - lo, 1e-12)
        tol = 0.001 * data_range  # within 0.1% of extremes counts as clipped
        result["clipping_frac"] = float(
            np.mean((data_stats <= lo + tol) | (data_stats >= hi - tol))
        )

        # --- Source extraction for FWHM / ecc (star quality metrics) ---
        # Try progressively higher thresholds — extended targets flood low thresholds
        sep.set_extract_pixstack(10_000_000)
        sep.set_sub_object_limit(65536)
        objects = None
        used_thresh = None
        for thresh_sigma in (5.0, 10.0, 20.0):
            thresh = max(thresh_sigma * sky_std, 1e-9)
            try:
                objects = sep.extract(data_sub_2d, thresh, err=bkg.rms(),
                                      minarea=9, deblend_nthresh=8,
                                      deblend_cont=0.01, clean=True)
                used_thresh = thresh_sigma
                break
            except Exception as e:
                log.warning(f"[assess] extraction at {thresh_sigma}×σ failed: {e}")

        if objects is None or len(objects) == 0:
            log.warning("[assess] source extraction failed at all thresholds — FWHM/ecc unavailable")
        else:
            a = objects["a"]
            b = objects["b"]
            fwhm = 2.355 * np.sqrt(a * b)
            ecc = np.sqrt(1.0 - (b / np.maximum(a, 1e-6)) ** 2)

            # Strict point-source filter to exclude galaxy structure / nebulosity
            mask = (fwhm > 1.5) & (fwhm < 15.0) & (ecc < 0.5)
            if mask.sum() < 5:
                mask = (fwhm > 1.0) & (fwhm < 20.0) & (ecc < 0.6)  # relax if too few stars

            if mask.sum() > 0:
                result["fwhm_stack"] = float(np.median(fwhm[mask]))
                result["ecc_stack"] = float(np.median(ecc[mask]))
                result["star_count"] = int(mask.sum())
                log.info(f"[assess] {mask.sum()} point sources at {used_thresh}×σ threshold")

        # --- Integration efficiency ---
        if (result["snr_stack"] is not None
                and frame_count > 0
                and single_frame_snrs
                and len(single_frame_snrs) > 0):
            valid_snrs = [s for s in single_frame_snrs if s and s > 0]
            if valid_snrs:
                snr_single = float(np.median(valid_snrs))
                snr_ideal = snr_single * np.sqrt(frame_count)
                if snr_ideal > 0:
                    result["efficiency"] = float(result["snr_stack"] / snr_ideal)

        log.info(
            f"[assess] sigma_sky={result['sigma_sky']:.2e} "
            f"snr={result['snr_stack']:.1f} "
            f"fwhm={result['fwhm_stack']}px ecc={result['ecc_stack']} "
            f"flatness={result['flatness_rms']:.2e} "
            f"efficiency={result['efficiency']}"
        )

    except Exception as e:
        log.error(f"[assess] assessment failed for {fits_path.name}: {e}")

    return result


def get_frame_snrs(file_paths: list[str]) -> list[float]:
    """Look up stored SNR values for the given frame file paths."""
    if not file_paths:
        return []
    try:
        from nas_server.database import get_conn
        placeholders = ",".join("?" * len(file_paths))
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT snr FROM light_files WHERE file_path IN ({placeholders}) "
                f"AND snr IS NOT NULL",
                file_paths,
            ).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception as e:
        log.warning(f"[assess] could not fetch frame SNRs: {e}")
        return []

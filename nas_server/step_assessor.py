"""
Before/after physics-grounded metrics for each processing step variant.

assess_step(input_fits, output_fits, step, object_type) -> dict

Universal metrics (all steps):
  bg_sigma_before/after   Background noise sigma (sigma-clipped std)
  bg_sigma_ratio          after / before  (<1 = noise reduced, >1 = noise increased)
  bg_median_shift         Signed median drift of background level
  clip_lo_pct             Fraction of pixels at low extreme (< p0.05)
  clip_hi_pct             Fraction of pixels at high extreme (> p99.95)
  entropy_before/after    Shannon entropy of 8-bit histogram

Step-specific metrics and auto-reject rules:
  denoise_linear/nonlinear:
    fwhm_before/after, fwhm_delta_pct, snr_before/after, ssim
    REJECT: fwhm_after > fwhm_before × 1.15
  deconvolution:
    fwhm_before/after, fwhm_delta_pct, ringing_score
    REJECT: fwhm_after > fwhm_before  OR  ringing_score > 1.35
  background_extraction:
    gradient_severity_before/after, nebulosity_leakage_score
    REJECT: nebulosity_leakage_score > 0.25
  stretch:
    bg_level_before/after, p95_before/after, dynamic_range_ratio
    REJECT: clip_hi_pct > 0.05

analytically_failed: bool — top-level flag; set by auto-reject rules above.
All metric values are JSON-serializable floats or None.
"""
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_STEP_DENOISE = {"denoise_linear", "denoise_nonlinear"}
_STEP_DECONV  = {"deconvolution"}
_STEP_BG      = {"background_extraction"}
_STEP_STRETCH = {"stretch"}


def _load_fits_mono(path: Path) -> np.ndarray:
    """Load FITS as float32 mono (mean of channels if colour)."""
    from astropy.io import fits as _fits
    with _fits.open(str(path)) as hdul:
        raw = hdul[0].data.astype(np.float32)
    if raw.ndim == 3:
        return np.ascontiguousarray(np.mean(np.transpose(raw, (1, 2, 0)), axis=2))
    return np.ascontiguousarray(raw)


def _bg_stats(data: np.ndarray):
    """Return (bkg_object, data_sub, sky_median, sky_sigma)."""
    import sep
    from astropy.stats import sigma_clipped_stats
    bkg = sep.Background(data, bw=256, bh=256, fw=3, fh=3)
    data_sub = data - bkg.back()
    med, _, std = sigma_clipped_stats(data_sub, sigma=3.0, maxiters=5)
    return bkg, data_sub, float(med), max(float(std), 1e-12)


def _entropy(data: np.ndarray) -> float:
    """Shannon entropy of 8-bit histogram."""
    lo, hi = float(data.min()), float(data.max())
    if hi <= lo:
        return 0.0
    scaled = ((data - lo) / (hi - lo) * 255).astype(np.uint8)
    counts = np.bincount(scaled.ravel(), minlength=256).astype(np.float64)
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


def _clipping(data: np.ndarray):
    """Return (clip_lo_pct, clip_hi_pct) fractions."""
    lo = np.percentile(data, 0.05)
    hi = np.percentile(data, 99.95)
    r  = max(hi - lo, 1e-12)
    tol = 0.001 * r
    clip_lo = float(np.mean(data <= lo + tol))
    clip_hi = float(np.mean(data >= hi - tol))
    return clip_lo, clip_hi


def _extract_stars(data_sub: np.ndarray, sky_std: float):
    """Extract point-like sources; return arrays (fwhm, ecc) or (None, None)."""
    import sep
    sep.set_extract_pixstack(10_000_000)
    sep.set_sub_object_limit(65536)
    for thresh_sigma in (5.0, 10.0, 20.0):
        thresh = max(thresh_sigma * sky_std, 1e-9)
        try:
            objs = sep.extract(data_sub, thresh, err=sky_std,
                               minarea=9, deblend_nthresh=8,
                               deblend_cont=0.01, clean=True)
            if len(objs) == 0:
                continue
            a = objs["a"]
            b = objs["b"]
            fwhm = 2.355 * np.sqrt(np.maximum(a * b, 1e-12))
            ecc  = np.sqrt(1.0 - (b / np.maximum(a, 1e-6)) ** 2)
            mask = (fwhm > 1.5) & (fwhm < 15.0) & (ecc < 0.5)
            if mask.sum() < 5:
                mask = (fwhm > 1.0) & (fwhm < 20.0) & (ecc < 0.6)
            if mask.sum() > 0:
                return fwhm[mask], ecc[mask]
        except Exception as e:
            log.debug(f"[step_assessor] extraction at {thresh_sigma}×σ failed: {e}")
    return None, None


def _fwhm_snr(data: np.ndarray):
    """Return (median_fwhm, snr) or (None, None)."""
    try:
        bkg, data_sub, _, sky_std = _bg_stats(data)
        fwhm_arr, _ = _extract_stars(data_sub, sky_std)
        fwhm = float(np.median(fwhm_arr)) if fwhm_arr is not None else None
        snr  = float(np.std(data_sub)) / sky_std
        return fwhm, snr
    except Exception as e:
        log.debug(f"[step_assessor] _fwhm_snr failed: {e}")
        return None, None


def _ringing_score(data_sub: np.ndarray, sky_std: float) -> float | None:
    """
    Ringing score: median ratio of local-ring brightness vs. star centre.
    High values (> 1.35) indicate deconvolution overshoot / ringing artefacts.
    """
    try:
        import sep
        fwhm_arr, _ = _extract_stars(data_sub, sky_std)
        if fwhm_arr is None or len(fwhm_arr) < 5:
            return None
        median_fwhm = float(np.median(fwhm_arr))
        # Re-extract to get positions
        thresh = max(5.0 * sky_std, 1e-9)
        objs = sep.extract(data_sub, thresh, err=sky_std,
                           minarea=9, deblend_nthresh=8,
                           deblend_cont=0.01, clean=True)
        if len(objs) == 0:
            return None
        h, w = data_sub.shape
        ratios = []
        r_inner = max(1.5, median_fwhm * 1.5)
        r_outer = r_inner + max(2.0, median_fwhm * 0.8)
        for obj in objs[:200]:
            cx, cy = float(obj["x"]), float(obj["y"])
            # Build a small cutout
            pad = int(r_outer) + 2
            x0 = max(0, int(cx) - pad);  x1 = min(w, int(cx) + pad + 1)
            y0 = max(0, int(cy) - pad);  y1 = min(h, int(cy) + pad + 1)
            cut = data_sub[y0:y1, x0:x1]
            if cut.size == 0:
                continue
            ys, xs = np.mgrid[y0:y1, x0:x1]
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            centre_mask = dist <= r_inner * 0.5
            ring_mask   = (dist >= r_inner) & (dist <= r_outer)
            if centre_mask.sum() < 3 or ring_mask.sum() < 5:
                continue
            centre_val = float(np.mean(cut[centre_mask[y0:y1, x0:x1] if False else
                                          (dist[: , :] <= r_inner * 0.5)[
                                              :cut.shape[0], :cut.shape[1]]]))
            # Recompute with local dist
            local_dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            c_mask = local_dist <= r_inner * 0.5
            r_mask = (local_dist >= r_inner) & (local_dist <= r_outer)
            if c_mask.sum() < 3 or r_mask.sum() < 5:
                continue
            centre_val = float(np.mean(cut[c_mask]))
            ring_val   = float(np.mean(np.abs(cut[r_mask])))
            if centre_val > 1e-9:
                ratios.append(ring_val / centre_val)
        if not ratios:
            return None
        return float(np.median(ratios))
    except Exception as e:
        log.debug(f"[step_assessor] ringing_score failed: {e}")
        return None


def _gradient_severity(data: np.ndarray) -> float | None:
    """RMS of the SEP background map — higher = worse gradient."""
    try:
        import sep
        bkg = sep.Background(data, bw=128, bh=128, fw=3, fh=3)
        return float(np.std(bkg.back()))
    except Exception as e:
        log.debug(f"[step_assessor] gradient_severity failed: {e}")
        return None


def _nebulosity_leakage(before: np.ndarray, after: np.ndarray) -> float | None:
    """
    FFT low-frequency power fraction of the difference image.
    High values indicate that background extraction removed real nebulosity.
    """
    try:
        diff = (after - before).astype(np.float32)
        F = np.fft.fft2(diff)
        Fshift = np.fft.fftshift(F)
        power = np.abs(Fshift) ** 2
        h, w = power.shape
        cy, cx = h // 2, w // 2
        low_r = min(h, w) // 8  # inner 12.5% of frequency space = low freq
        ys, xs = np.ogrid[:h, :w]
        low_mask = (ys - cy) ** 2 + (xs - cx) ** 2 <= low_r ** 2
        total = power.sum()
        if total < 1e-30:
            return 0.0
        return float(power[low_mask].sum() / total)
    except Exception as e:
        log.debug(f"[step_assessor] nebulosity_leakage failed: {e}")
        return None


def assess_step(input_fits: "Path | str",
                output_fits: "Path | str",
                step: str,
                object_type: str = "unknown") -> dict:
    """
    Compute before/after physics metrics for a single processing step.

    Args:
        input_fits:   FITS going into the step (before).
        output_fits:  FITS produced by the step (after).
        step:         Step name from the ontology (e.g. 'denoise_linear').
        object_type:  'galaxy', 'nebula', 'star_cluster', etc. (not yet used but reserved).

    Returns dict with all metrics (None for any that fail) plus:
        analytically_failed: bool — True if auto-reject rules triggered.
    """
    input_fits  = Path(input_fits)
    output_fits = Path(output_fits)

    result: dict = {
        # Universal
        "bg_sigma_before":  None,
        "bg_sigma_after":   None,
        "bg_sigma_ratio":   None,
        "bg_median_shift":  None,
        "clip_lo_pct":      None,
        "clip_hi_pct":      None,
        "entropy_before":   None,
        "entropy_after":    None,
        # Step-specific — populated below
        "fwhm_before":              None,
        "fwhm_after":               None,
        "fwhm_delta_pct":           None,
        "snr_before":               None,
        "snr_after":                None,
        "ssim":                     None,
        "ringing_score":            None,
        "gradient_severity_before": None,
        "gradient_severity_after":  None,
        "nebulosity_leakage_score": None,
        "bg_level_before":          None,
        "bg_level_after":           None,
        "p95_before":               None,
        "p95_after":                None,
        "dynamic_range_ratio":      None,
        # Summary
        "analytically_failed": False,
    }

    try:
        before = _load_fits_mono(input_fits)
        after  = _load_fits_mono(output_fits)

        # --- Universal metrics ---
        try:
            _, _, bmed_b, bstd_b = _bg_stats(before)
            _, _, bmed_a, bstd_a = _bg_stats(after)
            result["bg_sigma_before"] = bstd_b
            result["bg_sigma_after"]  = bstd_a
            result["bg_sigma_ratio"]  = bstd_a / bstd_b if bstd_b > 0 else None
            result["bg_median_shift"] = bmed_a - bmed_b
        except Exception as e:
            log.debug(f"[step_assessor] universal bg stats failed: {e}")

        try:
            clip_lo, clip_hi = _clipping(after)
            result["clip_lo_pct"] = clip_lo
            result["clip_hi_pct"] = clip_hi
        except Exception as e:
            log.debug(f"[step_assessor] clipping failed: {e}")

        try:
            result["entropy_before"] = _entropy(before)
            result["entropy_after"]  = _entropy(after)
        except Exception as e:
            log.debug(f"[step_assessor] entropy failed: {e}")

        # --- Step-specific metrics ---
        if step in _STEP_DENOISE:
            fwhm_b, snr_b = _fwhm_snr(before)
            fwhm_a, snr_a = _fwhm_snr(after)
            result["fwhm_before"] = fwhm_b
            result["fwhm_after"]  = fwhm_a
            result["snr_before"]  = snr_b
            result["snr_after"]   = snr_a
            if fwhm_b and fwhm_a and fwhm_b > 0:
                result["fwhm_delta_pct"] = (fwhm_a - fwhm_b) / fwhm_b * 100.0
            try:
                from skimage.metrics import structural_similarity as _ssim
                # Resize to same shape if different (shouldn't happen in practice)
                b8 = ((before - before.min()) / max(before.max() - before.min(), 1e-9) * 255).astype(np.uint8)
                a8 = ((after  - after.min())  / max(after.max()  - after.min(),  1e-9) * 255).astype(np.uint8)
                if b8.shape == a8.shape:
                    result["ssim"] = float(_ssim(b8, a8, data_range=255))
            except Exception as e:
                log.debug(f"[step_assessor] SSIM failed: {e}")
            # Auto-reject: FWHM grew by more than 15%
            if fwhm_b and fwhm_a and fwhm_a > fwhm_b * 1.15:
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: fwhm {fwhm_b:.2f}→{fwhm_a:.2f} px (+15%+ growth)")

        elif step in _STEP_DECONV:
            fwhm_b, snr_b = _fwhm_snr(before)
            fwhm_a, snr_a = _fwhm_snr(after)
            result["fwhm_before"] = fwhm_b
            result["fwhm_after"]  = fwhm_a
            result["snr_before"]  = snr_b
            result["snr_after"]   = snr_a
            if fwhm_b and fwhm_a and fwhm_b > 0:
                result["fwhm_delta_pct"] = (fwhm_a - fwhm_b) / fwhm_b * 100.0
            try:
                _, data_sub_a, _, sky_std_a = _bg_stats(after)
                result["ringing_score"] = _ringing_score(data_sub_a, sky_std_a)
            except Exception as e:
                log.debug(f"[step_assessor] ringing_score failed: {e}")
            # Auto-reject: FWHM grew at all, or ringing too high
            if fwhm_b and fwhm_a and fwhm_a > fwhm_b:
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: fwhm {fwhm_b:.2f}→{fwhm_a:.2f} (grew)")
            elif result["ringing_score"] is not None and result["ringing_score"] > 1.35:
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: ringing_score={result['ringing_score']:.3f} > 1.35")

        elif step in _STEP_BG:
            result["gradient_severity_before"] = _gradient_severity(before)
            result["gradient_severity_after"]  = _gradient_severity(after)
            result["nebulosity_leakage_score"] = _nebulosity_leakage(before, after)
            if (result["nebulosity_leakage_score"] is not None
                    and result["nebulosity_leakage_score"] > 0.25):
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: nebulosity_leakage={result['nebulosity_leakage_score']:.3f} > 0.25")

        elif step in _STEP_STRETCH:
            try:
                _, _, bg_b, _ = _bg_stats(before)
                _, _, bg_a, _ = _bg_stats(after)
                result["bg_level_before"] = bg_b
                result["bg_level_after"]  = bg_a
                result["p95_before"] = float(np.percentile(before, 95))
                result["p95_after"]  = float(np.percentile(after,  95))
                dr_b = float(np.percentile(before, 99.5) - np.percentile(before, 0.5))
                dr_a = float(np.percentile(after,  99.5) - np.percentile(after,  0.5))
                result["dynamic_range_ratio"] = dr_a / dr_b if dr_b > 0 else None
            except Exception as e:
                log.debug(f"[step_assessor] stretch metrics failed: {e}")
            if result["clip_hi_pct"] is not None and result["clip_hi_pct"] > 0.05:
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: clip_hi_pct={result['clip_hi_pct']:.4f} > 0.05")

        log.info(
            f"[step_assessor] {step} bg_σ_ratio={result['bg_sigma_ratio']} "
            f"fwhm_Δ%={result['fwhm_delta_pct']} snr_after={result['snr_after']} "
            f"failed={result['analytically_failed']}"
        )

    except Exception as e:
        log.error(f"[step_assessor] assessment failed: {e}", exc_info=True)
        result["analytically_failed"] = False  # don't reject on assessor crash

    return result

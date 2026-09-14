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
    REJECT: fwhm_after > fwhm_before  OR  ringing_score > 1.35 sigma
  background_extraction:
    gradient_severity_before/after, nebulosity_leakage_score
    REJECT: removed signal is >1.5x stronger on the object than in the sky
  stretch:
    bg_level_before/after, p95_before/after, dynamic_range_ratio
    REJECT: clip_hi_pct > 0.05
  color_calibration:
    color_b_over_r_before/after, color_g_over_r_before/after
    (B/R, G/R in the brightest 3% of pixels by luminance -- the target's own
    rendered color, not sky neutrality). Informational only, no REJECT rule:
    unlike denoise/deconv this step's job is to change color, so a shift
    alone isn't a failure signal -- the value is making each candidate's own
    ratio visible for comparison against its peers. Real motivating case:
    M51 2026-09-12, an SSSC candidate crushed B/R to 0.56 while three
    independent candidates all agreed at ~1.0, invisible in a stretched
    preview but unmistakable as a number.

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
_STEP_COLOR   = {"color_calibration"}


def _load_fits_mono(path: Path) -> np.ndarray:
    """Load FITS as float32 mono (mean of channels if colour)."""
    from astropy.io import fits as _fits
    with _fits.open(str(path)) as hdul:
        raw = hdul[0].data.astype(np.float32)
    if raw.ndim == 3:
        return np.ascontiguousarray(np.mean(np.transpose(raw, (1, 2, 0)), axis=2))
    return np.ascontiguousarray(raw)


def _load_fits_color(path: Path) -> "np.ndarray | None":
    """Load FITS as float32 (H,W,3); None if not a real 3-channel image."""
    from astropy.io import fits as _fits
    with _fits.open(str(path)) as hdul:
        raw = hdul[0].data.astype(np.float32)
    if raw.ndim == 3 and raw.shape[0] == 3:
        return np.ascontiguousarray(np.transpose(raw, (1, 2, 0)))
    if raw.ndim == 3 and raw.shape[2] == 3:
        return np.ascontiguousarray(raw)
    return None


def _bright_region_color_ratios(data: np.ndarray, percentile: float = 97.0) -> "tuple[float, float] | tuple[None, None]":
    """B/R and G/R in the brightest `100-percentile`% of pixels by luminance.

    Real motivating case (M51, 2026-09-12): a color_calibration candidate
    (SSSC) crushed B/R to 0.56 in the target's own bright structure while
    three independent candidates (SPCC, PI, no-calibration control) all
    agreed at ~1.0 -- invisible by eye in a stretched preview, unmistakable
    as a number. Bright-region rather than whole-frame or sky-only, because
    the failure mode this exists to catch is about the TARGET's own
    rendered color, not sky neutrality (a separate, already-measured thing).
    """
    lum = 0.2126 * data[..., 0] + 0.7152 * data[..., 1] + 0.0722 * data[..., 2]
    threshold = np.percentile(lum, percentile)
    mask = lum >= threshold
    if not mask.any():
        return None, None
    bright = data[mask]
    r = float(bright[:, 0].mean())
    if r <= 1e-9:
        return None, None
    return float(bright[:, 2].mean() / r), float(bright[:, 1].mean() / r)


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
    Measure star-ring contrast in units of local sky noise.

    Only high-SNR, point-like detections participate.  For each one, compare
    the median of the expected ringing annulus with the immediately adjacent
    outer annulus.  This deliberately does *not* divide by the star core:
    doing so hides a perceptually obvious halo around a bright star and lets
    marginal detections dominate the aggregate.  The median across the
    brightest usable stars keeps one contaminated cutout from vetoing a frame.
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
        contrasts = []
        r_inner = max(1.5, median_fwhm * 1.5)
        r_outer = r_inner + max(2.0, median_fwhm * 0.8)
        r_reference = r_outer + max(2.0, median_fwhm * 0.8)
        # SEP's extraction order is not a brightness guarantee.  Rank before
        # limiting so faint threshold detections cannot crowd out real stars.
        ranked = sorted(objs, key=lambda obj: float(obj["peak"]), reverse=True)
        for obj in ranked:
            cx, cy = float(obj["x"]), float(obj["y"])
            peak_snr = float(obj["peak"]) / sky_std
            if peak_snr < 20.0:
                continue
            # Build a small cutout
            pad = int(np.ceil(r_reference)) + 2
            x0 = max(0, int(cx) - pad);  x1 = min(w, int(cx) + pad + 1)
            y0 = max(0, int(cy) - pad);  y1 = min(h, int(cy) + pad + 1)
            cut = data_sub[y0:y1, x0:x1]
            if cut.size == 0:
                continue
            ys, xs = np.mgrid[y0:y1, x0:x1]
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            ring_mask = (dist >= r_inner) & (dist <= r_outer)
            reference_mask = (dist > r_outer) & (dist <= r_reference)
            if ring_mask.sum() < 5 or reference_mask.sum() < 5:
                continue
            ring_level = float(np.median(cut[ring_mask]))
            reference_level = float(np.median(cut[reference_mask]))
            # Deconvolution's characteristic failure here is an undershoot:
            # a dark annulus outside the bright PSF.  Ordinary positive PSF
            # wings must not be mistaken for that defect.
            contrasts.append(max(0.0, reference_level - ring_level) / sky_std)
            if len(contrasts) >= 25:
                break
        if len(contrasts) < 5:
            return None
        return float(np.median(contrasts))
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
    Ratio of removed-signal magnitude on astronomical structure versus sky.

    A background model is expected to be smooth, so whole-frame low-frequency
    power cannot distinguish a good gradient correction from removed nebulosity.
    Instead, derive an object mask from significant positive structure in the
    pre-extraction image and compare ``abs(after - before)`` inside and outside
    that mask.  Values above one mean the change is concentrated on the object;
    values below one mean it is concentrated in the sky.

    Return ``None`` when a trustworthy object/sky split cannot be formed.  The
    assessor deliberately fails open in that case rather than rejecting a real
    candidate on ambiguous evidence.
    """
    try:
        removed = np.abs(after.astype(np.float32) - before.astype(np.float32))
        if float(np.max(removed)) < 1e-15:
            return 0.0

        _, data_sub, _, sky_std = _bg_stats(before)
        object_mask = data_sub > (2.5 * sky_std)

        # Include the faint outskirts around detected structure instead of
        # measuring only bright cores.  scipy is already a processing runtime
        # dependency and this operation is linear in the image size.
        from scipy.ndimage import binary_dilation, binary_opening
        object_mask = binary_opening(object_mask, iterations=2)
        object_mask = binary_dilation(object_mask, iterations=4)

        coverage = float(np.mean(object_mask))
        if coverage < 0.002 or coverage > 0.60:
            return None

        object_change = float(np.mean(removed[object_mask]))
        sky_change = float(np.mean(removed[~object_mask]))
        scale = max(float(np.max(removed)), 1e-15)
        if sky_change <= scale * 1e-9:
            return 1_000_000.0 if object_change > scale * 1e-9 else 0.0
        return object_change / sky_change
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
        "color_b_over_r_before":    None,
        "color_g_over_r_before":    None,
        "color_b_over_r_after":     None,
        "color_g_over_r_after":     None,
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
                    and result["nebulosity_leakage_score"] > 1.5):
                result["analytically_failed"] = True
                log.info(f"[step_assessor] REJECT {step}: object/sky removed-signal "
                         f"ratio={result['nebulosity_leakage_score']:.3f} > 1.5")

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

        elif step in _STEP_COLOR:
            # Informational only -- color_calibration's whole job is to change
            # color, so a before/after shift is not itself a failure signal the
            # way it is for denoise/deconv. The value here is making each
            # candidate's own target-region color ratio visible for comparison
            # ACROSS candidates (in the evaluator prompt and manual-review
            # collage), not an auto-reject rule for any single one.
            try:
                color_before = _load_fits_color(input_fits)
                color_after  = _load_fits_color(output_fits)
                if color_before is not None:
                    b_r, g_r = _bright_region_color_ratios(color_before)
                    result["color_b_over_r_before"] = b_r
                    result["color_g_over_r_before"] = g_r
                if color_after is not None:
                    b_r, g_r = _bright_region_color_ratios(color_after)
                    result["color_b_over_r_after"] = b_r
                    result["color_g_over_r_after"] = g_r
            except Exception as e:
                log.debug(f"[step_assessor] color ratio measurement failed: {e}")

        log.info(
            f"[step_assessor] {step} bg_σ_ratio={result['bg_sigma_ratio']} "
            f"fwhm_Δ%={result['fwhm_delta_pct']} snr_after={result['snr_after']} "
            f"failed={result['analytically_failed']}"
        )

    except Exception as e:
        log.error(f"[step_assessor] assessment failed: {e}", exc_info=True)
        result["analytically_failed"] = False  # don't reject on assessor crash

    return result

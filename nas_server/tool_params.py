"""
Compute tool-specific processing parameters from image statistics.

Every function signature is:
    compute_<tool>(stats, object_type="unknown", history=None) -> dict

`stats`       — output of image_analyzer.analyze()
`object_type` — galaxy | emission_nebula | reflection_nebula | globular_cluster
                planetary_nebula | open_cluster | unknown
`history`     — list of past {"params": {...}, "delta": {...}} dicts for this tool+type.
                When None or empty, pure data-driven defaults are used.
                When populated, history nudges parameters toward what has worked.

Return dicts map directly onto pixinsight.run_postprocess() kwargs,
seti_astro function kwargs, or GraXpert CLI flags — whatever the caller needs.
"""
import logging
import math

log = logging.getLogger(__name__)

_NEBULA_TYPES = {"emission_nebula", "reflection_nebula", "planetary_nebula"}
_GALAXY_TYPES = {"galaxy"}
_CLUSTER_TYPES = {"globular_cluster", "open_cluster"}


# ---------------------------------------------------------------------------
# Background / gradient tools
# ---------------------------------------------------------------------------

def compute_gradient_correction(stats, object_type="unknown", history=None) -> dict:
    """GradientCorrection (PI) or GraXpert background extraction."""
    bg = stats["background"]
    sev = bg["gradient_severity"]

    # scale: spatial frequency of gradient model (lower = large-scale only)
    # More severe gradients need a finer scale to capture them
    scale = max(256, int(1024 * (1.0 - sev)))

    # smoothness: regularisation — reduce for complex gradients
    smoothness = max(0.3, 1.0 - sev * 0.7)

    # Use DBE (slower, more accurate) when gradient is very strong
    use_dbe = sev > 0.4

    correction = bg["correction"]   # "subtraction" | "division"

    params = {
        "dbe":             use_dbe,
        "gradient_correction": not use_dbe,
        "dbe_correction":  correction,
        # GradientCorrection props
        "gc_scale":        scale,
        "gc_smoothness":   round(smoothness, 2),
        # GraXpert mirror
        "graxpert_correction": correction.capitalize(),
        "graxpert_smoothing":  round(smoothness, 2),
    }
    return _apply_history(params, history, ["gc_smoothness", "graxpert_smoothing"])


# ---------------------------------------------------------------------------
# Noise reduction
# ---------------------------------------------------------------------------

def compute_tgv(stats, object_type="unknown", history=None) -> dict:
    """TGVDenoise (PI, CPU) — driven by background RMS / SNR."""
    noise = stats["noise"]
    bg_rms = noise["background_rms"]
    snr    = noise["snr"]

    # More noise (lower SNR) → stronger reduction
    # SNR < 10: very noisy; SNR > 50: clean
    strength = _clamp(2.0 / max(snr / 15.0, 0.1), 0.5, 6.0)

    # Edge protection: reduce when image is very smooth (don't erode real structure)
    edge = _clamp(bg_rms * 10.0, 0.0005, 0.01)

    # Iterations: more for very noisy data
    iterations = 100 if snr > 20 else 150

    params = {
        "tgv":             True,
        "tgv_strength":    round(strength, 2),
        "tgv_edge":        round(edge, 5),
        "tgv_iterations":  iterations,
    }
    return _apply_history(params, history, ["tgv_strength", "tgv_edge"])


def compute_mlt(stats, object_type="unknown", history=None) -> dict:
    """MultiscaleLinearTransform — sharpening + noise reduction."""
    noise  = stats["noise"]
    spatial = stats["spatial_freq"]
    snr    = noise["snr"]

    # Sharpen more on high-SNR images; less when noisy
    sharpen = _clamp(0.05 + 0.25 * min(snr / 40.0, 1.0), 0.0, 0.30)

    # Denoise more when noisy
    denoise = _clamp(0.8 - snr / 80.0, 0.20, 0.80)

    # Nebulae and galaxies: more layers to affect larger scales
    layers = 5 if object_type in _NEBULA_TYPES | _GALAXY_TYPES else 4

    params = {
        "mlt":         True,
        "mlt_sharpen": round(sharpen, 3),
        "mlt_denoise": round(denoise, 3),
        "mlt_layers":  layers,
    }
    return _apply_history(params, history, ["mlt_sharpen", "mlt_denoise"])


def compute_cosmic_clarity_denoise(stats, object_type="unknown", history=None) -> dict:
    """CosmicClarity denoise (seti_astro.denoise)."""
    snr = stats["noise"]["snr"]
    luma  = _clamp(1.0 - snr / 60.0, 0.3, 1.0)
    color = _clamp(luma * 0.8, 0.2, 0.9)
    params = {
        "denoise_luma":  round(luma, 2),
        "denoise_color": round(color, 2),
    }
    return _apply_history(params, history, ["denoise_luma", "denoise_color"])


# ---------------------------------------------------------------------------
# Sharpening / deconvolution
# ---------------------------------------------------------------------------

def compute_bxt(stats, object_type="unknown", history=None) -> dict:
    """BlurXTerminator — uses measured PSF diameter directly."""
    psf   = stats["psf"]
    noise = stats["noise"]
    ecc   = psf["eccentricity"]

    psf_diam = psf["psf_diameter"]   # arcsec, measured from stars

    # Sharpen stars more when they're very elongated (tracking error)
    sharpen_stars = _clamp(0.3 + ecc * 0.5, 0.2, 0.80)

    # Sharpen non-stellar more for galaxies/nebulae with fine structure
    if object_type in _GALAXY_TYPES:
        sharpen_nonstellar = _clamp(0.5 + noise["snr"] / 100.0, 0.3, 0.70)
    elif object_type in _NEBULA_TYPES:
        sharpen_nonstellar = _clamp(0.3 + noise["snr"] / 120.0, 0.2, 0.55)
    else:
        sharpen_nonstellar = 0.30

    params = {
        "bxt":                 True,
        "bxt_psf":             round(psf_diam, 1),
        "bxt_auto_psf":        False,
        "bxt_stars":           round(sharpen_stars, 2),
        "bxt_nonstellar":      round(sharpen_nonstellar, 2),
        "bxt_adjust_halos":    0.0,
    }
    return _apply_history(params, history, ["bxt_psf", "bxt_stars", "bxt_nonstellar"])


def compute_cosmic_clarity_sharpen(stats, object_type="unknown", history=None) -> dict:
    """CosmicClarity sharpen (seti_astro.sharpen)."""
    psf = stats["psf"]
    snr = stats["noise"]["snr"]
    stellar     = _clamp(0.4 + psf["eccentricity"] * 0.3, 0.2, 0.8)
    nonstellar  = _clamp(0.3 + snr / 120.0, 0.1, 0.7)
    params = {
        "stellar_amount":    round(stellar, 2),
        "nonstellar_amount": round(nonstellar, 2),
    }
    return _apply_history(params, history, ["stellar_amount", "nonstellar_amount"])


# ---------------------------------------------------------------------------
# Stretch
# ---------------------------------------------------------------------------

def compute_ht(stats, object_type="unknown", history=None) -> dict:
    """HistogramTransformation auto-stretch from image statistics.

    Shadow clip uses background-only sky statistics (sigma-clipped cell medians
    from image_analyzer._background) rather than whole-image median/MAD, which
    is contaminated by bright galaxy cores or extended nebulae. k=2.8 matches
    PixInsight's own STF Auto Stretch formula.
    """
    h  = stats["histogram"]
    bg = stats.get("background", {})

    # Prefer background-only sky stats (target cells excluded via sigma-clip).
    # Fall back to whole-image median/MAD if image_analyzer didn't produce them.
    sky_bg    = bg.get("sky_background", h["median"])
    sky_noise = bg.get("sky_noise",      h["mad"])

    # Shadow clip: PI STF standard uses k=2.8 (was 2.0 — too aggressive)
    clip_low = max(0.0, sky_bg - 2.8 * sky_noise)

    # Target background after stretch — galaxies prefer darker backgrounds
    if object_type in _GALAXY_TYPES:
        target_bg = 0.10
    elif object_type in _CLUSTER_TYPES:
        target_bg = 0.08
    else:
        target_bg = 0.12   # nebulae

    params = {
        "ht":           True,
        "ht_clip_low":  round(clip_low, 5),
        "ht_target_bg": target_bg,
    }
    return _apply_history(params, history, ["ht_clip_low", "ht_target_bg"])


def compute_stat_stretch(stats, object_type="unknown", history=None) -> dict:
    """seti_astro.stat_stretch parameters."""
    h = stats["histogram"]
    # Sky-adaptive target: stat_stretch sets the whole-image median.
    # For sky-dominated frames (galaxies/clusters) this ≈ output sky level.
    # Target: sky 0.055–0.085 for galaxies, 0.07–0.11 for nebulae/other.
    sky = stats.get("background", {}).get("sky_mean", 0.02)
    if object_type in _GALAXY_TYPES:
        target = max(0.055, min(sky * 3.0, 0.085))
    else:
        target = max(0.07, min(sky * 3.5, 0.11))
    # Tighter blackpoint clipping: better black point, truer shadow separation
    bp_sigma = 3.5 if h["p01"] < 0.005 else 4.0 if h["p01"] < 0.01 else 4.5

    params = {
        "target_median":    round(target, 4),
        "blackpoint_sigma": bp_sigma,
        "linked":           True,
    }
    return _apply_history(params, history, ["target_median", "blackpoint_sigma"])


def compute_stf_params(stats, object_type="unknown", history=None) -> dict:
    """STF stretch parameters derived from stack physics."""
    bg_sigma = stats.get("background", {}).get("sigma", 0.002)
    REFERENCE_SIGMA = 0.002
    # Noisier stacks → clip less aggressively to protect faint signal
    shadow_clip_k = max(0.5, min(2.0, 1.25 * (REFERENCE_SIGMA / max(bg_sigma, 1e-6))))
    target_bg = 0.07 if object_type in (*_GALAXY_TYPES, *_CLUSTER_TYPES) else 0.09

    params = {
        "target_bg":      round(target_bg, 3),
        "shadow_clip_k":  round(shadow_clip_k, 3),
        "linked":         True,
    }
    return _apply_history(params, history, ["target_bg", "shadow_clip_k"])


def compute_ghs(stats, object_type="unknown", history=None) -> dict:
    """seti_astro.ghs_stretch parameters."""
    h = stats["histogram"]

    if h.get("is_linear", True):
        # Linear pre-stretch image: pivot MUST be near the sky background, not 0.1+.
        # Setting pivot at 3x background keeps sky near-black while stretching signal.
        pivot = _clamp(h["median"] * 3.0, h["median"], 0.05)
        # Alpha from dynamic range: p99/median ratio drives how much stretch is needed.
        dr = h["p99"] / max(h["median"], 1e-9)
        alpha = _clamp(3.5 * math.log10(max(dr, 2.0)), 3.0, 20.0)
    else:
        # Already-stretched image (rare — GHS usually applied pre-stretch)
        pivot = _clamp(h["median"] * 0.5, 0.05, 0.35)
        alpha = _clamp(20.0 * h["mad"] / max(h["median"], 1e-6), 3.0, 15.0)

    if object_type in _GALAXY_TYPES:
        alpha = min(alpha * 1.2, 20.0)

    params = {
        "alpha": round(alpha, 2),
        "beta":  0.0,
        "gamma": 3.0,
        "pivot": round(pivot, 6),   # 6 dp needed for sub-0.01 linear values
    }
    return _apply_history(params, history, ["alpha", "pivot"])


# ---------------------------------------------------------------------------
# Color tools
# ---------------------------------------------------------------------------

def compute_scnr(stats, object_type="unknown", history=None) -> dict:
    """SCNR — scale amount to relative green excess (normalised to sky level)."""
    color  = stats["color"]
    bg_sky = stats["background"]["sky_mean"]
    ge_abs = color.get("green_excess", 0.0)

    # Normalise green excess relative to sky level so dark linear images compare fairly
    ge = ge_abs / max(bg_sky, 1e-9)

    if ge <= 0.05:
        amount = 0.3
    elif ge < 0.30:
        amount = 0.6
    elif ge < 0.80:
        amount = 0.85
    else:
        amount = 0.95

    params = {
        "scnr":        True,
        "scnr_amount": round(amount, 2),
    }
    return _apply_history(params, history, ["scnr_amount"])


def compute_color_sat(stats, object_type="unknown", history=None) -> dict:
    """ColorSaturation boost — derived from current saturation level."""
    color = stats["color"]
    if not color.get("is_color"):
        return {"color_sat": False, "color_sat_boost": 0.0}

    # After SCNR the channels will be roughly balanced — estimate saturation
    # from the min/max ratio rather than absolute deviation (avoids green-cast inflation)
    r, g, b  = color["r_median"], color["g_median"], color["b_median"]
    ch_vals  = sorted([r, g, b])
    ch_range = ch_vals[-1] - ch_vals[0]
    ch_mean  = (r + g + b) / 3.0
    # Saturation proxy: how spread the channels are AFTER imagined green removal
    rb_mean  = (r + b) / 2.0
    sat_est  = (rb_mean - ch_vals[0]) / max(rb_mean, 1e-9)

    # Under-saturated images (sat_est < 0.3) need more boost
    boost = _clamp(0.35 - sat_est * 0.3, 0.10, 0.50)

    if object_type in _NEBULA_TYPES:
        boost = min(boost * 1.3, 0.60)   # nebulae benefit from more colour pop

    params = {
        "color_sat":       True,
        "color_sat_boost": round(boost, 2),
    }
    return _apply_history(params, history, ["color_sat_boost"])


# ---------------------------------------------------------------------------
# Non-linear enhancement
# ---------------------------------------------------------------------------

def compute_hdrmt(stats, object_type="unknown", history=None) -> dict:
    """HDRMultiscaleTransform — driven by dynamic range."""
    h = stats["histogram"]
    dr = h["dynamic_range"]

    # Number of layers: more for high dynamic range
    layers = 6 if dr > 0.7 else (5 if dr > 0.4 else 4)

    # More iterations for very high DR
    iterations = 4 if dr > 0.8 else 3

    # Overdrive: slight boost to recovery — more for bright nebulae
    overdrive = 0.1 if object_type in _NEBULA_TYPES else 0.0

    params = {
        "hdrmt":            True,
        "hdrmt_layers":     layers,
        "hdrmt_iterations": iterations,
        "hdrmt_overdrive":  overdrive,
    }
    return _apply_history(params, history, ["hdrmt_layers", "hdrmt_iterations"])


def compute_lhe(stats, object_type="unknown", history=None) -> dict:
    """LocalHistogramEqualization — kernel size from image scale."""
    meta = stats["meta"]
    w, h = meta["width"], meta["height"]

    # Kernel radius scales with image size — ~1/12 of shorter dimension
    kernel_r = _clamp(min(w, h) // 12, 32, 128)

    # Slope limit (CLAHE clip): lower for smooth nebulae to avoid noise amplification
    if object_type in _NEBULA_TYPES:
        slope = 1.5
    elif object_type in _GALAXY_TYPES:
        slope = 2.5
    else:
        slope = 2.0

    params = {
        "lhe":             True,
        "lhe_kernel_r":    kernel_r,
        "lhe_slope_limit": slope,
        "lhe_amount":      0.5,
    }
    return _apply_history(params, history, ["lhe_kernel_r", "lhe_slope_limit", "lhe_amount"])


def compute_morph(stats, object_type="unknown", history=None) -> dict:
    """MorphologicalTransformation — star size reduction."""
    stars = stats["stars"]
    fwhm  = stars["fwhm_median"]
    large = stars["large_star_fraction"]

    # Only apply when stars are notably large or bloated
    if fwhm < 4.0 and large < 0.3:
        return {"morph": False, "morph_amount": 0.3, "morph_iterations": 1}

    # More iterations for larger / more bloated stars
    iterations = min(3, max(1, int(fwhm / 3.0)))
    amount     = _clamp(0.2 + large * 0.3, 0.2, 0.6)

    params = {
        "morph":            True,
        "morph_amount":     round(amount, 2),
        "morph_iterations": iterations,
    }
    return _apply_history(params, history, ["morph_amount", "morph_iterations"])


def compute_lum_masks(stats: dict, object_type: str = "unknown") -> dict:
    """
    Compute luminance mask parameters for non-linear post-stretch processing steps.

    Works on a stretched (non-linear) image where median ≈ 0.20-0.30.
    Returns {fn_name: {lower, upper, fuzziness, blur}} for each step that
    benefits from masking. Steps not present in the dict run unmasked.
    """
    h = stats.get("histogram", {})
    median = float(h.get("median", 0.20))
    if median < 0.05:
        median = 0.20   # fall back if stats are from a linear pre-stretch image

    # Midtone mask: above background noise floor, below clipped highlights
    mid_lo = round(_clamp(median * 1.3, 0.08, 0.30), 3)

    # Highlight mask: only the top tonal region (for HDR compression)
    hi_lo  = round(_clamp(median * 3.5, 0.50, 0.75), 3)

    # Shadow-structure mask: low-to-mid tones for dark structure enhancement
    sh_lo  = round(_clamp(median * 0.5, 0.03, 0.10), 3)
    sh_hi  = round(_clamp(median * 2.0, 0.20, 0.45), 3)

    midtone   = {"lower": mid_lo, "upper": 0.85, "fuzziness": 0.06, "blur": 4}
    highlight = {"lower": hi_lo,  "upper": 1.0,  "fuzziness": 0.08, "blur": 4}
    shadow    = {"lower": sh_lo,  "upper": sh_hi, "fuzziness": 0.05, "blur": 4}

    return {
        # seti_astro fn names
        "clahe":           midtone,
        "curves":          midtone,
        "color_boost":     midtone,   # protect dark background from saturation-amplified chroma noise
        "hdr_compression": highlight,
        "dark_enhance":    shadow,
        # PI js step names
        "lhe":             midtone,
        "color_sat":       midtone,
        "curves_pi":       midtone,
        "hdrmt":           highlight,
        "usm":             midtone,
    }


def compute_usm(stats, object_type="unknown", history=None) -> dict:
    """UnsharpMask — post-stretch luminance sharpening."""
    spatial = stats["spatial_freq"]
    noise   = stats["noise"]

    # Use USM only if image is reasonably sharp already (fine detail present)
    if spatial["sharpness_index"] < 0.3 or noise["snr"] < 15:
        return {"usm": False}

    # sigma ≈ half the typical star FWHM
    sigma = _clamp(stats["psf"]["fwhm_median"] * 0.5, 1.0, 4.0)

    params = {
        "usm":           True,
        "usm_sigma":     round(sigma, 1),
        "usm_amount":    0.7,
        "usm_threshold": 0.02,
    }
    return _apply_history(params, history, ["usm_sigma", "usm_amount"])


def compute_clahe(stats, object_type="unknown", history=None) -> dict:
    """CLAHE clip_limit and tile_size from SNR and spatial detail."""
    noise   = stats["noise"]
    spatial = stats["spatial_freq"]
    snr     = noise["snr"]

    # Higher SNR → can clip more contrast without amplifying noise
    clip_limit = _clamp(1.0 + snr / 18.0, 1.0, 5.0)

    if object_type in _NEBULA_TYPES:
        clip_limit = min(clip_limit, 2.5)   # smooth structure — avoid noise spotting
    elif object_type in _GALAXY_TYPES:
        clip_limit = min(clip_limit * 1.1, 5.0)   # fine structure → tolerate more clip

    # Smaller tiles = finer enhancement; useful for galaxies with spiral arm detail
    sharpness = spatial.get("sharpness_index", 0.5)
    tile_size = 4 if (object_type in _GALAXY_TYPES and sharpness > 0.35) else 8

    params = {"clip_limit": round(clip_limit, 1), "tile_size": tile_size}
    return _apply_history(params, history, ["clip_limit"])


def _clean_curve_points(pts: list) -> list:
    """
    Sanitise a list of [input, output] curve control points:
    1. Sort by input value.
    2. Merge points whose inputs are within 0.03 of each other (keep average).
    3. Enforce strict monotonic increase on both axes — clamp output to be
       at least 0.005 above the previous output so PI doesn't get a flat/reversed segment.
    4. Clamp all values to [0, 1].
    """
    if not pts:
        return [[0, 0], [1, 1]]
    pts = sorted([[float(x), float(y)] for x, y in pts], key=lambda p: p[0])
    # Merge close inputs — but NEVER merge the x=0 / x=1 anchors away. PI's
    # CurvesTransformation.K requires both endpoints; averaging a near-black
    # data point into [0,0] (NGC 7000 NBN: sky 0.05 → point at x=0.027 →
    # merged first point x=0.013) makes PI reject the WHOLE curve
    # ("CurvesTransformation.K(): the instance is not valid") and pi_curves
    # silently no-ops — the 2026-07-06 critique-batch cross-run finding.
    merged = [pts[0]]
    for p in pts[1:]:
        near = p[0] - merged[-1][0] < 0.03
        if near and merged[-1][0] <= 1e-9:
            continue                      # drop the near-black point, keep [0,*]
        if near and p[0] >= 1.0 - 1e-9:
            merged[-1] = p                # keep the [1,*] anchor, drop its neighbour
        elif near:
            merged[-1] = [(merged[-1][0] + p[0]) / 2, (merged[-1][1] + p[1]) / 2]
        else:
            merged.append(p)
    # Enforce monotone output
    clean = [[round(_clamp(merged[0][0], 0.0, 1.0), 4),
              round(_clamp(merged[0][1], 0.0, 1.0), 4)]]
    for p in merged[1:]:
        x = _clamp(p[0], 0.0, 1.0)
        y = _clamp(p[1], clean[-1][1] + 0.005, 1.0)
        clean.append([round(x, 4), round(y, 4)])
    # Guarantee the PI-required endpoints survive whatever the callers passed.
    if clean[0][0] > 1e-9:
        clean.insert(0, [0.0, 0.0])
    if clean[-1][0] < 1.0 - 1e-9:
        clean.append([1.0, round(min(1.0, clean[-1][1] + 0.005), 4)])
    return clean


def compute_curves(stats, object_type="unknown", history=None) -> dict:
    """
    Compute CurvesTransformation control points directly from measured pixel statistics.

    Philosophy: every image has a unique pixel distribution. Instead of mapping the
    image into a named preset category and hoping the preset fits, we measure the
    actual sky level, halo zone, highlight clipping fraction, and near-white extent,
    then compute a bespoke curve that achieves specific numeric targets for each zone.

    Zones and their targets
    -----------------------
    Black anchor   (input 0)            → output 0             always pure black
    Shadow pull    (~50% of sky_bg)     → proportional pull    drag very dark sky down
    Sky anchor     (measured sky_bg)    → target_sky           object-type specific
    Halo zone      (p80 – p95)          → near-identity        preserve structure gradient
    Shoulder onset (~midpoint p95–p99)  → start compressing    compress before clipping
    Core           (p99)                → moderate rolloff      reduce bright core
    Near-clip      (p999)               → target_p999          tightest rolloff
    Top cap        (1.0)                → target_p999 + 0.01   flat top shoulder

    Target sky levels (empirically calibrated 2026-05-29)
    -----------------------------------------------------
    galaxy          0.09  — dark for IGL / tidal streams / outer spiral arms
    globular_cluster 0.10  — dark for star-to-background contrast
    open_cluster    0.10  — same reason
    emission_nebula  0.09  — leave room for faint Ha halos
    default          0.09
    """
    h   = stats.get("histogram", {})
    bg  = stats.get("background", {})

    # ── Measured sky level ────────────────────────────────────────────────
    # Prefer background analyzer's sigma-clipped sky_background (excludes target
    # core cells).  Fall back to p40 which is below the median in most deep-sky images.
    sky_bg = bg.get("sky_background") or h.get("p40") or h.get("median", 0.15)
    sky_bg = _clamp(float(sky_bg), 0.03, 0.50)

    # ── Full percentile profile ───────────────────────────────────────────
    # image_analyzer now always provides these; fall back gracefully for old stats dicts.
    p80  = float(h.get("p80",  sky_bg * 1.30))
    p90  = float(h.get("p90",  sky_bg * 1.65))
    p95  = float(h.get("p95",  p90 * 1.80))
    p99  = float(h.get("p99",  0.90))
    p995 = float(h.get("p995", p99 * 1.06))
    p999 = float(h.get("p999", p99 * 1.09))

    # ── Target sky level by object type ──────────────────────────────────
    _SKY_TARGETS = {
        "galaxy":             0.09,
        "globular_cluster":   0.10,
        "open_cluster":       0.10,
        "emission_nebula":    0.09,
        "reflection_nebula":  0.09,
        "planetary_nebula":   0.08,
    }
    target_sky = _SKY_TARGETS.get(object_type, 0.09)

    # If sky is already darker than target, don't lift it — but don't darken
    # it into the p01 noise floor either.
    target_sky = _clamp(target_sky, h.get("p05", 0.04) + 0.01, sky_bg)

    # ── Shadow pull ───────────────────────────────────────────────────────
    # At 50% of sky_bg we apply a proportional downward pull so dark nebula patches
    # and inter-star sky also get darker, not just the median sky level.
    # Extended objects (large galaxies) carry their faintest spiral arms / tidal
    # streams just above the sky floor; a hard pull (×0.45) drags that gradient
    # into the black and the arms vanish. Use a gentler factor so the faint
    # outer structure survives, while point-source fields (clusters) and small
    # nebulae can be pulled harder for star-to-sky contrast.
    _SHADOW_PULL = {
        "galaxy":             0.70,   # preserve faint outer arms / IFN
        "globular_cluster":   0.45,
        "open_cluster":       0.45,
        "emission_nebula":    0.55,   # leave room for faint Ha halos
        "reflection_nebula":  0.55,
        "planetary_nebula":   0.45,
    }
    shadow_in  = sky_bg * 0.50
    shadow_out = target_sky * _SHADOW_PULL.get(object_type, 0.50)

    # ── Halo zone — near-identity ─────────────────────────────────────────
    # Between p80 and p95 lives the outer halo / spiral arms / emission fringes.
    # We apply only 2-3% compression here — enough to subtly deepen contrast
    # without crushing the gradient that the stretch worked hard to reveal.
    p80_out = p80 * 0.97
    p90_out = p90 * 0.97
    p95_out = p95 * 0.98   # nearly neutral just before shoulder

    # ── Shoulder onset ────────────────────────────────────────────────────
    # Start rolling off partway between p95 (inner structure) and p99 (core).
    # The exact fraction depends on how steeply the histogram climbs in that range:
    # a large gap (p99 >> p95) means sparse bright stars → start shoulder later.
    # A small gap means the core is densely packed → start earlier.
    p95_to_p99_ratio = (p99 / max(p95, 0.01)) if p95 > 0 else 2.0
    shoulder_fraction = _clamp(0.30 + (p95_to_p99_ratio - 1.0) * 0.08, 0.20, 0.50)
    shoulder_in  = p95 + (p99 - p95) * shoulder_fraction
    # Compression at shoulder onset scales with how much headroom is left above p999
    clip_squeeze  = _clamp(1.0 - (1.0 - p999) * 3.0, 0.88, 0.96)
    shoulder_out = shoulder_in * clip_squeeze

    # ── Near-clipping cap (p999) ──────────────────────────────────────────
    # Target where near-white pixels land after the curve.
    # NOTE: we do NOT add a separate p99 control point between shoulder and cap.
    # Doing so forces the spline to create a kink: the slope compresses hard to p99
    # then must expand again to reach the cap, producing an S-inversion in the
    # highlight region.  Letting PI's Akima spline interpolate from shoulder → cap
    # gives a smooth, monotone ramp that distributes compression evenly.
    _P999_TARGETS = {
        "galaxy":             0.90,   # galaxy cores can glow a little
        "globular_cluster":   0.87,   # tight core — pull back more
        "open_cluster":       0.88,
        "emission_nebula":    0.89,
        "reflection_nebula":  0.90,
        "planetary_nebula":   0.88,
    }
    p999_target = _P999_TARGETS.get(object_type, 0.88)
    p999_out    = min(p999, p999_target)    # only compress, never stretch
    top_out     = _clamp(p999_target + 0.01, p999_out + 0.005, 0.99)

    # ── Assemble and sanitise ─────────────────────────────────────────────
    pts = _clean_curve_points([
        [0.0,         0.0],
        [shadow_in,   shadow_out],
        [sky_bg,      target_sky],
        [p80,         p80_out],
        [p90,         p90_out],
        [p95,         p95_out],
        [shoulder_in, shoulder_out],
        [p999,        p999_out],
        [1.0,         top_out],
    ])

    return {"curves_points": pts, "curves_shape": "custom"}


def compute_hdr_compression(stats, object_type="unknown", history=None) -> dict:
    """Seti_astro wavelet HDR compression — highlight fraction drives strength."""
    h   = stats["histogram"]
    dr  = h.get("dynamic_range", 0.5)
    p99 = h.get("p99", 0.80)

    # Fraction of image that's genuinely bright (above 0.70)
    bright_frac = max(0.0, p99 - 0.70) / 0.30

    compression_factor = _clamp(1.1 + bright_frac * 1.0 + dr * 0.4, 1.1, 2.8)

    if object_type in _NEBULA_TYPES:
        compression_factor = min(compression_factor * 1.15, 2.8)   # bright emission cores
    elif object_type in _CLUSTER_TYPES:
        compression_factor = min(compression_factor, 1.4)          # mostly stars

    n_scales   = 6 if dr > 0.65 else 5
    mask_gamma = _clamp(1.0 + bright_frac * 0.5, 1.0, 1.5)

    params = {
        "n_scales":           n_scales,
        "compression_factor": round(compression_factor, 2),
        "mask_gamma":         round(mask_gamma, 2),
    }
    return _apply_history(params, history, ["compression_factor", "n_scales"])


def compute_dark_enhance(stats, object_type="unknown", history=None) -> dict:
    """Wavelet shadow boost — shadow-SNR proxy drives boost_factor."""
    h       = stats["histogram"]
    noise   = stats["noise"]
    spatial = stats["spatial_freq"]
    snr     = noise["snr"]
    median  = h.get("median", 0.20)

    # Shadow SNR proxy: scale true SNR by how much of the image is shadow
    shadow_snr = snr * _clamp(median / 0.20, 0.3, 1.5)

    boost_factor = _clamp(1.5 + shadow_snr / 4.0, 1.5, 10.0)

    if object_type in _GALAXY_TYPES:
        boost_factor = min(boost_factor * 1.25, 10.0)  # IFN / outer arms
    elif object_type in _CLUSTER_TYPES:
        boost_factor = min(boost_factor * 0.6, 4.0)    # minimal dark structure

    sharpness = spatial.get("sharpness_index", 0.5)
    n_scales  = 7 if sharpness < 0.3 else 6            # more scales for diffuse structure
    mask_gamma = _clamp(1.1 + snr / 80.0, 1.0, 1.6)

    params = {
        "n_scales":    n_scales,
        "boost_factor": round(boost_factor, 1),
        "mask_gamma":   round(mask_gamma, 2),
    }
    return _apply_history(params, history, ["boost_factor", "n_scales"])


def compute_halo_suppress(stats, object_type="unknown", history=None) -> dict:
    """HaloBGon halo reduction level — FWHM and large-star fraction drive severity."""
    stars = stats.get("stars", {})
    fwhm  = stars.get("fwhm_median", 3.0)
    large = stars.get("large_star_fraction", 0.1)

    severity = _clamp(fwhm / 6.0 + large * 1.5, 0.0, 3.0)

    if severity < 0.5:
        level = 1
    elif severity < 1.5:
        level = 2
    else:
        level = 3

    params = {"reduction_level": level, "is_linear": False}
    return _apply_history(params, history, ["reduction_level"])


def compute_ihdr(stats, object_type="unknown", history=None) -> dict:
    """iHDR (Uri Darom) — multiscale iterative HDR compression.

    Highly parameter-dependent:
      iterations    — compression strength (3-9, more = stronger)
      preservation  — detail protection (3-8, higher = gentler)
      mask_strength — how hard to protect bright regions (0.8-2.0)
    """
    h     = stats["histogram"]
    noise = stats["noise"]
    dr    = h.get("dynamic_range", 0.5)
    p99   = h.get("p99", 0.80)
    snr   = noise["snr"]

    # More iterations for high dynamic range (needs stronger compression)
    iterations = _clamp(int(3 + dr * 7), 3, 9)

    # Noisy images need higher preservation to avoid amplifying noise texture
    preservation = _clamp(int(9 - snr / 7.0), 3, 8)

    # Stronger mask for images with very bright cores
    bright_frac   = max(0.0, p99 - 0.70) / 0.30
    mask_strength = _clamp(0.8 + bright_frac * 1.2, 0.8, 2.0)

    if object_type in _NEBULA_TYPES:
        iterations    = min(iterations + 1, 9)
        mask_strength = min(mask_strength * 1.1, 2.0)
    elif object_type in _CLUSTER_TYPES:
        iterations    = max(iterations - 2, 3)
        mask_strength = min(mask_strength, 1.0)

    params = {
        "ihdr":               True,
        "ihdr_iterations":    iterations,
        "ihdr_preservation":  preservation,
        "ihdr_mask_strength": round(mask_strength, 2),
    }
    return _apply_history(params, history,
                          ["ihdr_iterations", "ihdr_preservation", "ihdr_mask_strength"])


def compute_background_neutralize(stats, object_type="unknown", history=None) -> dict:
    """Post-stretch background neutralization pivot mode from color cast severity."""
    color = stats.get("color", {})
    r, g, b = (color.get("r_median", 0.20),
               color.get("g_median", 0.20),
               color.get("b_median", 0.20))

    ch_mean  = (r + g + b) / 3.0
    cast_rel = max(abs(r - ch_mean), abs(g - ch_mean), abs(b - ch_mean)) / max(ch_mean, 1e-9)

    # Stronger cast → more aggressive pivot
    if cast_rel > 0.25:
        mode = "pivot1"
    elif cast_rel > 0.10:
        mode = "pivot2"
    else:
        mode = "pivot3"

    return _apply_history({"mode": mode}, history, [])


# ---------------------------------------------------------------------------
# Full recommended parameter set for a given tool
# ---------------------------------------------------------------------------

# Maps tool name → compute function
TOOL_COMPUTERS = {
    "gradient_correction": compute_gradient_correction,
    "dbe":                 compute_gradient_correction,
    "tgv":                 compute_tgv,
    "mlt":                 compute_mlt,
    "bxt":                 compute_bxt,
    "nxt":                 compute_cosmic_clarity_denoise,
    "cosmic_denoise":      compute_cosmic_clarity_denoise,
    "cosmic_sharpen":      compute_cosmic_clarity_sharpen,
    "ht":                  compute_ht,
    "stat_stretch":        compute_stat_stretch,
    "ghs":                 compute_ghs,
    "scnr":                compute_scnr,
    "color_sat":           compute_color_sat,
    "hdrmt":               compute_hdrmt,
    "lhe":                 compute_lhe,
    "morph":               compute_morph,
    "usm":                 compute_usm,
    # Non-linear post-stretch tools
    "clahe":               compute_clahe,
    "curves":              compute_curves,
    "hdr_compression":     compute_hdr_compression,
    "dark_enhance":        compute_dark_enhance,
    "halo_suppress":       compute_halo_suppress,
    "ihdr":                compute_ihdr,
    "background_neutralize": compute_background_neutralize,
}


def compute_all(stats, object_type="unknown", history_by_tool: dict = None) -> dict:
    """
    Compute recommended parameters for every tool.
    Returns a merged flat dict ready to pass to pixinsight.run_postprocess().
    """
    history_by_tool = history_by_tool or {}
    result = {}
    for tool, fn in TOOL_COMPUTERS.items():
        h = history_by_tool.get(tool, [])
        try:
            p = fn(stats, object_type, h)
            result.update(p)
        except Exception as e:
            log.warning(f"[tool_params] {tool} compute failed: {e}")
    return result


# ---------------------------------------------------------------------------
# History-based nudging
# ---------------------------------------------------------------------------

def _apply_history(params: dict, history: list, nudge_keys: list) -> dict:
    """
    Given past runs [(params_used, delta_scores)], nudge the continuous
    parameters in nudge_keys toward the direction that increased quality.

    For each key, computes the weighted average of past param values,
    weighted by their delta score improvement. Then blends it with the
    data-driven value at 30% historical weight (more with more data).
    """
    if not history:
        return params

    # Clamp blend weight to 50% max to avoid over-committing to history
    blend = min(0.5, 0.1 * len(history))

    for key in nudge_keys:
        if key not in params:
            continue
        current = params[key]
        if not isinstance(current, (int, float)):
            continue

        # Collect past (value, delta) pairs where delta > 0
        weighted_vals = []
        total_weight  = 0.0
        for entry in history[-20:]:   # look back at most 20 runs
            past_val   = entry.get("params", {}).get(key)
            past_delta = entry.get("delta", {}).get("overall", 0.0)
            if past_val is None or past_delta <= 0:
                continue
            weighted_vals.append(past_val * past_delta)
            total_weight += past_delta

        if total_weight > 0:
            hist_val = sum(weighted_vals) / total_weight
            # Blend: mostly data-driven, nudged by history
            blended  = current * (1 - blend) + hist_val * blend
            params[key] = type(current)(round(blended, 4) if isinstance(current, float) else round(blended))

    return params


# ---------------------------------------------------------------------------
# compute_all — convenience wrapper for adaptive linear planner
# ---------------------------------------------------------------------------

def compute_all(fits_path: str, object_type: str | None = None) -> dict:
    """
    Run pixel analysis on the FITS at `fits_path` and compute suggested parameters
    for the key LINEAR processing steps. Returns a dict keyed by step name.

    Used by the adaptive planning system to provide physics-grounded suggestions
    to Claude before the linear phase begins.
    """
    try:
        from nas_server.image_analyzer import analyze as _analyze
        stats = _analyze(fits_path)
    except Exception:
        stats = {}

    otype = object_type or "unknown"
    result: dict = {}

    try:
        result["background_extraction"] = compute_gradient_correction(stats, otype)
    except Exception:
        pass
    try:
        result["deconvolution"] = compute_bxt(stats, otype)
    except Exception:
        pass
    try:
        result["denoise_linear"] = compute_cosmic_clarity_denoise(stats, otype)
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

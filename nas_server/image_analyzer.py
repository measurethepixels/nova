"""
Analyze a FITS or XISF file and return a structured statistics dict that
drives parameter computation for every processing tool (PI, GraXpert,
CosmicClarity, stretch functions, etc.).

All measurements are instrument-agnostic — the same stats feed both
the PixInsight JS parameters and the seti_astro Python wrappers.
"""
import logging
import math
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats, mad_std

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze(fits_path: str) -> dict:
    """
    Read a FITS file and return a comprehensive statistics dict.

    Returns
    -------
    dict with keys grouped by measurement type:
        meta          — width, height, channels, bitpix, object, exptime
        background    — sky level, gradient severity, recommended correction type
        noise         — SNR, background RMS, signal RMS
        psf           — FWHM, eccentricity, star count, PSF diameter estimate
        histogram     — median, mad, p01, p99, dynamic_range, is_linear
        color         — r_median, g_median, b_median, green_excess, color_cast_severity
        stars         — fwhm_median, fwhm_p90, star_density, large_star_fraction
        spatial_freq  — sharpness_index, fine_detail_ratio
    """
    path = Path(fits_path)
    if not path.exists():
        raise FileNotFoundError(fits_path)

    data, header = _load_fits(path)

    stats: dict = {}
    stats["meta"]        = _meta(data, header)
    stats["histogram"]   = _histogram(data)
    stats["background"]  = _background(data)
    stats["noise"]       = _noise(data, stats["background"])
    stats["color"]       = _color(data)
    stats["psf"]         = _psf(data, stats["background"])
    stats["stars"]       = _stars(stats["psf"])
    stats["spatial_freq"] = _spatial_freq(data)

    log.info(f"[analyzer] {path.name}: "
             f"SNR={stats['noise']['snr']:.1f} "
             f"gradient={stats['background']['gradient_severity']:.2f} "
             f"fwhm={stats['psf']['fwhm_median']:.2f}px "
             f"green_excess={stats['color']['green_excess']:.4f}")
    return stats


def ha_dominance_ratio(fits_path: str) -> float:
    """
    Ratio of Hα (red) to OIII (blue) signal in the bright/nebula regions of a
    stretched colour image. Background-subtracts each channel by its median, then
    compares mean red vs blue over the brightest 10% of pixels — so the result
    reflects the nebula, not the sky (whose median is near-neutral and would mask
    Hα dominance).

    >1 = Hα-dominant (NGC 7000 ≈ 2.0); ≈1 = Hα/OIII balanced. Returns 1.0 for
    mono images or on failure (neutral → no dominance claimed).
    """
    try:
        data, _ = _load_fits(Path(fits_path))
        if data.shape[0] < 3:
            return 1.0
        r, g, b = data[0], data[1], data[2]
        lum = 0.3 * r + 0.5 * g + 0.2 * b
        mask = lum >= np.percentile(lum, 90)
        r_sig = float((r - np.median(r))[mask].mean())
        b_sig = float((b - np.median(b))[mask].mean())
        if b_sig <= 1e-6:
            return 3.0 if r_sig > 1e-6 else 1.0
        return max(0.0, r_sig / b_sig)
    except Exception as e:
        log.warning(f"[image_analyzer] ha_dominance_ratio failed: {e}")
        return 1.0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_fits(path: Path):
    """Return (data_float32_CxHxW_or_HxW, header). Rescales integers to [0,1]."""
    with fits.open(str(path)) as hdul:
        hdu = hdul[0]
        header = hdu.header
        data = hdu.data.astype(np.float32)

    # FITS stores as BITPIX=16 with BZERO/BSCALE — already decoded by astropy
    # Normalise to [0, 1]
    dmin, dmax = data.min(), data.max()
    if dmax > 1.0:
        data = (data - dmin) / max(dmax - dmin, 1e-9)

    # Ensure shape is (C, H, W) — FITS can be (H,W) mono or (C,H,W) colour
    if data.ndim == 2:
        data = data[np.newaxis]   # mono → (1, H, W)
    elif data.ndim == 3 and data.shape[2] <= 4:
        # Some FITS are (H, W, C) — transpose to (C, H, W)
        data = np.transpose(data, (2, 0, 1))
    # data is now always (C, H, W)
    return data, header


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def _meta(data: np.ndarray, header) -> dict:
    c, h, w = data.shape
    return {
        "width":    w,
        "height":   h,
        "channels": c,
        "bitpix":   header.get("BITPIX", 16),
        "object":   str(header.get("OBJECT", "")).strip(),
        "exptime":  float(header.get("EXPTIME", 0)),
        "creator":  str(header.get("CREATOR", "")).strip(),
    }


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------

def _histogram(data: np.ndarray) -> dict:
    lum = _luminance(data)
    mean, median, std = sigma_clipped_stats(lum, sigma=3.0)
    mad = float(mad_std(lum))

    # Full percentile profile — every compute_* function in tool_params.py uses
    # these directly to compute data-driven parameters instead of named presets.
    pcts = np.percentile(lum, [1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99, 99.5, 99.9, 99.99])
    p01, p05, p10, p20, p30, p40, p50, p60, p70, p80, p90, p95, p99, p995, p999, p9999 = (
        float(x) for x in pcts
    )
    dr = (p99 - p01) if p99 > p01 else 1e-6

    # A linear (un-stretched) image has median well below 0.2
    is_linear = p50 < 0.15

    return {
        "mean":          float(mean),
        "median":        float(median),
        "std":           float(std),
        "mad":           mad,
        "p01":  p01,  "p05":  p05,  "p10":  p10,  "p20":  p20,
        "p30":  p30,  "p40":  p40,  "p50":  p50,  "p60":  p60,
        "p70":  p70,  "p80":  p80,  "p90":  p90,  "p95":  p95,
        "p99":  p99,  "p995": p995, "p999": p999, "p9999": p9999,
        "dynamic_range": float(dr),
        "is_linear":     is_linear,
    }


# ---------------------------------------------------------------------------
# Background / gradient
# ---------------------------------------------------------------------------

def _background(data: np.ndarray) -> dict:
    """
    Divide luminance into a NxN grid, compute sigma-clipped median per cell,
    fit a low-order polynomial surface, measure residuals.

    gradient_severity  : 0–1, where 0 = flat, 1 = strong gradient
    recommended_correction : "subtraction" | "division"
    """
    lum = _luminance(data)
    grid_n = 8
    h, w = lum.shape
    ch, cw = h // grid_n, w // grid_n

    cell_medians = []
    for gy in range(grid_n):
        for gx in range(grid_n):
            cell = lum[gy*ch:(gy+1)*ch, gx*cw:(gx+1)*cw]
            _, med, _ = sigma_clipped_stats(cell, sigma=2.5)
            cell_medians.append(float(med))

    arr = np.array(cell_medians).reshape(grid_n, grid_n)

    # Gradient severity = normalised range of cell medians
    sky_min  = float(arr.min())
    sky_max  = float(arr.max())
    sky_mean = float(arr.mean())
    gradient_severity = (sky_max - sky_min) / max(sky_mean, 1e-6)
    gradient_severity = min(gradient_severity, 1.0)

    # Division correction is better when the gradient is multiplicative
    # (sky_max / sky_min > 2). Otherwise subtraction is fine.
    ratio = sky_max / max(sky_min, 1e-9)
    correction = "division" if ratio > 2.0 else "subtraction"

    # Background-only sky level: sigma-clip the 64 cell medians to reject
    # bright cells that overlap the imaging target (galaxy core, bright nebula).
    # Remaining cells are genuine sky — use their median and MAD for accurate
    # pre-stretch shadow-clip computation.
    flat = arr.flatten()
    _, bg_clipped_med, _ = sigma_clipped_stats(flat, sigma=2.5, maxiters=5)
    bg_scatter = float(mad_std(flat))
    bg_mask = flat < (bg_clipped_med + 1.5 * bg_scatter)
    bg_cells = flat[bg_mask] if bg_mask.sum() >= 4 else flat  # fallback if too few
    sky_background = float(np.median(bg_cells))
    sky_noise      = float(mad_std(bg_cells)) if len(bg_cells) > 1 else bg_scatter

    # Sky-only gradient: the all-cells metric above is dominated by cells sitting on
    # the target (M 81/M 82 cores), so a background step that perfectly flattens the
    # SKY barely moves it — and can even look like a regression once the pedestal
    # drops. Measure flatness over the object-excluded bg_cells instead.
    gradient_severity_sky = (float(bg_cells.max()) - float(bg_cells.min())) \
        / max(sky_background, 1e-6)
    gradient_severity_sky = min(gradient_severity_sky, 1.0)

    return {
        "sky_mean":           sky_mean,
        "sky_min":            sky_min,
        "sky_max":            sky_max,
        "sky_background":     sky_background,   # background-only median (target excluded)
        "sky_noise":          sky_noise,         # background-only MAD (for shadow clip)
        "gradient_severity":  gradient_severity,
        "gradient_severity_sky": gradient_severity_sky,
        "correction":         correction,
        "cell_medians":       arr.tolist(),
    }


# ---------------------------------------------------------------------------
# Noise / SNR
# ---------------------------------------------------------------------------

def _noise(data: np.ndarray, bg: dict) -> dict:
    """
    Estimate noise from sigma-clipped background RMS.
    SNR = signal_rms / background_rms.
    """
    lum = _luminance(data)
    _, _, bg_rms = sigma_clipped_stats(lum, sigma=2.5)
    bg_rms = max(float(bg_rms), 1e-9)

    sky_mean = bg["sky_mean"]
    signal_rms = float(np.std(lum))
    snr = signal_rms / bg_rms

    return {
        "background_rms": bg_rms,
        "signal_rms":     signal_rms,
        "snr":            float(snr),
    }


# ---------------------------------------------------------------------------
# PSF / star measurement
# ---------------------------------------------------------------------------

def _psf(data: np.ndarray, bg: dict) -> dict:
    """
    Detect stars with SEP and measure FWHM and eccentricity.
    Falls back to a rough estimate if SEP fails.
    """
    try:
        import sep
    except ImportError:
        return _psf_fallback()

    lum = _luminance(data).astype(np.float64)
    sky_mean = bg["sky_mean"]
    sky_rms  = float(np.sqrt(max(sky_mean, 1e-9)))

    # SEP needs C-contiguous array
    lum_c = np.ascontiguousarray(lum)
    try:
        bkg = sep.Background(lum_c)
        lum_sub = lum_c - bkg.back()

        threshold = 3.0 * bkg.globalrms
        objects = sep.extract(lum_sub, threshold, minarea=9)
    except Exception as e:
        log.debug(f"[analyzer] SEP extraction failed: {e}")
        return _psf_fallback()

    if len(objects) == 0:
        return _psf_fallback()

    # FWHM ≈ 2 * sqrt(2 * ln2) * sigma ≈ 2.355 * mean(a, b)
    fwhm_vals = 2.355 * 0.5 * (objects["a"] + objects["b"])
    ecc_vals  = np.sqrt(1 - (objects["b"] / np.maximum(objects["a"], 1e-6))**2)

    # Keep only well-measured, unsaturated, non-edge stars
    h, w = lum.shape
    edge_mask = (
        (objects["x"] > 50) & (objects["x"] < w - 50) &
        (objects["y"] > 50) & (objects["y"] < h - 50) &
        (fwhm_vals > 1.0) & (fwhm_vals < 30.0)
    )
    fwhm_clean = fwhm_vals[edge_mask]
    ecc_clean  = ecc_vals[edge_mask]

    if len(fwhm_clean) == 0:
        return _psf_fallback()

    fwhm_med = float(np.median(fwhm_clean))
    fwhm_p90 = float(np.percentile(fwhm_clean, 90))
    ecc_med  = float(np.median(ecc_clean))

    # PSF diameter for BXT: FWHM(px) → arcsec via the S50 plate scale.
    # SeeStar S50 native plate scale = 2.37 arcsec/px (measured from plate-solved
    # WCS on native stacks; physics: 2.9µm IMX462 px / 250mm FL ≈ 2.39). The old
    # 2.55 was a stale estimate. See reference-s50-pixel-scale memory.
    psf_arcsec = fwhm_med * 2.37

    return {
        "star_count":    int(len(fwhm_clean)),
        "fwhm_median":   fwhm_med,
        "fwhm_p90":      fwhm_p90,
        "fwhm_std":      float(np.std(fwhm_clean)),
        "eccentricity":  ecc_med,
        "psf_arcsec":    psf_arcsec,
        "psf_diameter":  psf_arcsec,   # alias — used directly as BXT nonstellar_psf_diameter
    }


def _psf_fallback() -> dict:
    return {
        "star_count":   0,
        "fwhm_median":  4.0,
        "fwhm_p90":     6.0,
        "fwhm_std":     1.0,
        "eccentricity": 0.2,
        "psf_arcsec":   10.2,
        "psf_diameter": 10.2,
    }


# ---------------------------------------------------------------------------
# Star aggregate stats
# ---------------------------------------------------------------------------

def _stars(psf: dict) -> dict:
    fwhm_med = psf["fwhm_median"]
    fwhm_p90 = psf["fwhm_p90"]
    # large_star_fraction: if P90 >> median, there are many bloated stars
    large_star_fraction = max(0.0, (fwhm_p90 - fwhm_med) / max(fwhm_med, 1.0))
    return {
        "fwhm_median":         fwhm_med,
        "fwhm_p90":            fwhm_p90,
        "large_star_fraction": float(large_star_fraction),
        "star_density":        psf["star_count"],
    }


# ---------------------------------------------------------------------------
# Color channel analysis
# ---------------------------------------------------------------------------

def _color(data: np.ndarray) -> dict:
    if data.shape[0] < 3:
        return {
            "r_median": 0.0, "g_median": 0.0, "b_median": 0.0,
            "green_excess": 0.0, "color_cast_severity": 0.0,
            "is_color": False,
        }

    _, r_med, _ = sigma_clipped_stats(data[0], sigma=2.5)
    _, g_med, _ = sigma_clipped_stats(data[1], sigma=2.5)
    _, b_med, _ = sigma_clipped_stats(data[2], sigma=2.5)
    r_med, g_med, b_med = float(r_med), float(g_med), float(b_med)

    # Green excess: how much G exceeds the average of R and B
    rb_mean = (r_med + b_med) / 2.0
    green_excess = g_med - rb_mean   # positive = teal/green cast

    # Overall color cast: how far any channel deviates from the mean
    ch_mean = (r_med + g_med + b_med) / 3.0
    color_cast_severity = max(
        abs(r_med - ch_mean),
        abs(g_med - ch_mean),
        abs(b_med - ch_mean),
    ) / max(ch_mean, 1e-9)

    # Sky-corner per-channel balance. The whole-frame medians above include target
    # signal (nebula Ha/OIII), so they are NOT a clean read of the SKY colour. The
    # darkest corners are. A residual cast in the sky (e.g. NGC 2244 blue sky B/R 1.46,
    # or a green cast a neutralize step INTRODUCES) shows up here as B/R or G/R drifting
    # from 1.0 even when green_excess looks fine. background_neutralize is gated on this.
    sky_r = sky_g = sky_b = 0.0
    sky_b_over_r = sky_g_over_r = 1.0
    try:
        h_, w_ = data.shape[1], data.shape[2]
        m = max(h_ // 20, w_ // 20, 50)
        def _corner_med(ch):
            c = np.concatenate([
                ch[:m, :m].ravel(), ch[:m, -m:].ravel(),
                ch[-m:, :m].ravel(), ch[-m:, -m:].ravel()])
            return float(np.median(c))
        sky_r, sky_g, sky_b = _corner_med(data[0]), _corner_med(data[1]), _corner_med(data[2])
        sky_b_over_r = sky_b / sky_r if sky_r > 1e-6 else 1.0
        sky_g_over_r = sky_g / sky_r if sky_r > 1e-6 else 1.0
    except Exception:
        pass

    return {
        "r_median":            r_med,
        "g_median":            g_med,
        "b_median":            b_med,
        "green_excess":        green_excess,
        "color_cast_severity": float(color_cast_severity),
        "sky_r":               sky_r,
        "sky_g":               sky_g,
        "sky_b":               sky_b,
        "sky_b_over_r":        sky_b_over_r,
        "sky_g_over_r":        sky_g_over_r,
        "is_color":            True,
    }


# ---------------------------------------------------------------------------
# Spatial frequency (sharpness)
# ---------------------------------------------------------------------------

def _spatial_freq(data: np.ndarray) -> dict:
    """
    FFT-based sharpness: ratio of high-frequency power to total power.
    Higher = more fine detail / sharper image.
    """
    lum = _luminance(data)
    # Downsample for speed
    h, w = lum.shape
    scale = max(1, min(h, w) // 512)
    small = lum[::scale, ::scale]

    f = np.fft.fft2(small)
    fshift = np.fft.fftshift(f)
    mag = np.abs(fshift)

    ch, cw = mag.shape[0] // 2, mag.shape[1] // 2
    r_hi = min(ch, cw)
    r_lo = r_hi // 4

    total_power = float(np.sum(mag))
    # High-frequency annulus (outer 3/4 of spectrum)
    y_idx, x_idx = np.ogrid[:mag.shape[0], :mag.shape[1]]
    dist = np.sqrt((y_idx - ch)**2 + (x_idx - cw)**2)
    hi_mask = dist > r_lo
    hi_power = float(np.sum(mag[hi_mask]))

    sharpness_index = hi_power / max(total_power, 1e-9)

    # Laplacian variance — complementary fast sharpness metric
    from scipy.ndimage import laplace
    lap_var = float(np.var(laplace(small)))

    return {
        "sharpness_index":   sharpness_index,
        "laplacian_variance": lap_var,
        "fine_detail_ratio": sharpness_index,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luminance(data: np.ndarray) -> np.ndarray:
    """Return 2D luminance (H, W). For mono, that's the single channel."""
    if data.shape[0] == 1:
        return data[0]
    # Standard luminance weights
    return 0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python image_analyzer.py <file.fit>")
        sys.exit(1)
    result = analyze(sys.argv[1])
    print(json.dumps(result, indent=2))

"""Synthetic Gaia star-field references for canonical registration (Stage 2).

Builds a per-target reference frame that StarAlignment can register real S50 subs
against, so every session lands on one fixed canonical grid (enabling cumulative
master stacks). The reference is a synthetic image: Gaia DR3 sources rendered as
Gaussian PSF blobs at the canonical per-target WCS (from
`canonical_frame.canonical_target_wcs`).

Pipeline:
  1. `canonical_target_wcs(target)` → fixed WCS + (h, w).
  2. Cone-search the local Gaia DR3 XPSD catalog (via `pi_gaia_starfield.js`).
  3. Project sources with the canonical WCS, stamp Gaussian PSFs (brightness from
     Gaia G mag), write FITS with the canonical WCS header.

Output: `nas_server/target_references/<sanitized-target>.fits`.

Prototype status: validate StarAlignment match-rate on real subs (M 33) before
routing folio targets through this as `SA.referenceImage`.
"""

from __future__ import annotations

import json
import logging
import math
import re
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger("seestar.target_ref")

REF_DIR = Path(__file__).parent / "target_references"
PI_GAIA_STARFIELD_JS = str(Path(__file__).parent / "pi_gaia_starfield.js")

# Faint limit ~ what S50 30s subs reach per-frame; brighter than the stacked depth so
# the reference isn't dominated by stars too faint to detect in a single sub.
_DEFAULT_MAG_LIMIT = 16.5
_DEFAULT_SOURCE_LIMIT = 60000

# --- Synthetic-star rendering, ported from PixInsight's CatalogStarGenerator ---
# A sharp noise-free Gaussian field does NOT register: PI StarDetector / the SA
# descriptor matcher lock onto real S50 frames (20/20) but reject the synthetic
# field (0/20). The fix is to render stars the way CatalogStarGenerator does —
# Moffat PSFs with saturated cores over a faint background pedestal — so the
# synthetic frame "looks like" a real exposure to StarDetector.
#   beta=2 Moffat (S50 scale < 15"/px), supersampled, per-star absolute flux
#   A = fluxFactor * 100^((minMag - mag)/5) clamped to 1 (saturation),
#   minMag = maxMag-9, backgroundMag = maxMag+1 → backgroundLevel = 1e-4.
_MOFFAT_BETA = 2.0
_RENDER_SUPERSAMPLE = 3
# FWHM of synthetic stars in pixels. Matched to real S50 subs (~3 px) rather than
# CatalogStarGenerator's autoGraphics value (= pixel scale → 1 px, too sharp).
_RENDER_FWHM_PX = 3.0


def _sanitize(target: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", target.strip()) or "target"


def _cone_radius_deg(h: int, w: int, scale_deg: float) -> float:
    """Half-diagonal of the canonical canvas, plus 10% margin."""
    half_diag_px = 0.5 * math.hypot(w, h)
    return half_diag_px * scale_deg * 1.10


def _run_gaia_query(center_ra: float, center_dec: float, radius_deg: float,
                    out_csv: str, mag_limit: float = _DEFAULT_MAG_LIMIT,
                    source_limit: int = _DEFAULT_SOURCE_LIMIT,
                    timeout: int = 600) -> dict:
    """Invoke pi_gaia_starfield.js to dump Gaia sources to CSV. Returns job dict."""
    from nas_server.pixinsight import _run_pi

    job = {
        "center_ra": float(center_ra),
        "center_dec": float(center_dec),
        "radius_deg": float(radius_deg),
        "mag_limit": float(mag_limit),
        "source_limit": int(source_limit),
        "output_csv": str(out_csv),
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_gaia.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    # Automation mode: PI hangs at boot without --automation-mode on this VM.
    pi_ok, log_text = _run_pi(PI_GAIA_STARFIELD_JS, job_path, timeout=timeout,
                              use_xvfb=True, use_automation_mode=True)
    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}
    Path(job_path).unlink(missing_ok=True)

    result["_pi_ok"] = pi_ok
    if not result.get("ok"):
        logger.warning(f"[target_ref] Gaia query failed: {result.get('error')}; "
                       f"log tail: {log_text[-300:]}")
    return result


def _moffat_pixel_mean(dx: np.ndarray, dy: np.ndarray, sigma: float,
                       beta: float, ss: int) -> np.ndarray:
    """Mean of a unit (peak=1) Moffat over each pixel, by ss×ss supersampling.

    `dx`, `dy` are pixel-center offsets from the star center. Returns the area-
    averaged Moffat value per pixel — matching CatalogStarGenerator's supersampled
    PaintStar (which sums Evaluate()/ss² over ss² subpixels = mean over the pixel).
    """
    off = (np.arange(ss) - (ss - 1) / 2.0) / ss  # subpixel offsets within a pixel
    acc = np.zeros_like(dx, dtype=np.float64)
    s2 = sigma * sigma
    for oy in off:
        for ox in off:
            rx = dx + ox
            ry = dy + oy
            acc += np.power(1.0 + (rx * rx + ry * ry) / s2, -beta)
    return acc / (ss * ss)


def _render_starfield(wcs, shape, ra: np.ndarray, dec: np.ndarray,
                      magG: np.ndarray, mag_limit: float) -> np.ndarray:
    """Render a CatalogStarGenerator-style synthetic frame: Moffat stars with
    saturated cores over a faint background pedestal + uniform noise.

    Returns a float32 (h, w) image in [0, 1].
    """
    h, w = shape

    # world_to_pixel_values returns 0-based pixel coords (x, y) in FITS convention
    # (y increases upward). We stamp into a numpy array whose row 0 is the TOP, so the
    # y axis must be flipped to match how PixInsight (and real plate-solved S50 subs)
    # orient the data. Without this, the rendered field is a vertical mirror of reality
    # — a parity inversion StarAlignment's (mirror-free) similarity transform can never
    # register, which is exactly why the first renderer scored 0/20. Empirically, the
    # flipped field cross-matches PI's CatalogStarGenerator field to ~1px.
    x, y = wcs.world_to_pixel_values(ra, dec)
    x = np.asarray(x, dtype=np.float64)
    y = (h - 1.0) - np.asarray(y, dtype=np.float64)
    mag = np.asarray(magG, dtype=np.float64)

    beta = _MOFFAT_BETA
    fwhm_pix = _RENDER_FWHM_PX
    # Moffat sigma from FWHM: FWHM = 2σ·sqrt(2^(1/β) − 1).
    sigma = fwhm_pix / (2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0))

    # CatalogStarGenerator magnitude ladder (autoGraphics): brightest stars
    # saturate, background sits 10 mag below the bright clamp.
    max_mag = float(mag_limit)
    min_mag = max_mag - 9.0
    background_mag = max_mag + 1.0
    background_level = 100.0 ** ((min_mag - background_mag) / 5.0)  # = 1e-4

    # Saturation calibration: flux factor that makes a star at min_mag fill the
    # central pixel to 1.0 (integrate the unit Moffat over the central pixel).
    central = _moffat_pixel_mean(np.array([0.0]), np.array([0.0]), sigma, beta,
                                 ss=100)[0]
    flux_factor = 1.0 / max(central, 1e-12)

    # Per-star peak amplitude (absolute, magnitude-based — NOT global-peak norm).
    amp = flux_factor * np.power(100.0, (min_mag - mag) / 5.0)

    img = np.full((h, w), background_level, dtype=np.float64)

    # Stamp radius: out to where a saturated star falls back near the background.
    aperture = int(math.ceil(max(6.0, 4.0 * fwhm_pix)))
    ys, xs = np.mgrid[-aperture:aperture + 1, -aperture:aperture + 1]
    xs = xs.astype(np.float64)
    ys = ys.astype(np.float64)

    n_drawn = 0
    for xi, yi, ai in zip(x, y, amp):
        if not (np.isfinite(xi) and np.isfinite(yi) and np.isfinite(ai)) or ai <= 0:
            continue
        cx, cy = int(round(xi)), int(round(yi))
        if cx < -aperture or cy < -aperture or cx >= w + aperture or cy >= h + aperture:
            continue
        # Subpixel offset of star center from the stamp's integer center pixel.
        fx = xi - cx
        fy = yi - cy
        stamp = ai * _moffat_pixel_mean(xs - fx, ys - fy, sigma, beta,
                                        _RENDER_SUPERSAMPLE)
        x0, x1 = cx - aperture, cx + aperture + 1
        y0, y1 = cy - aperture, cy + aperture + 1
        sx0 = max(0, -x0); sy0 = max(0, -y0)
        ix0 = max(0, x0); iy0 = max(0, y0)
        ix1 = min(w, x1); iy1 = min(h, y1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        sub = stamp[sy0:sy0 + (iy1 - iy0), sx0:sx0 + (ix1 - ix0)]
        region = img[iy0:iy1, ix0:ix1]
        np.minimum(region + sub, 1.0, out=region)  # additive + saturation clamp
        n_drawn += 1

    # Faint uniform background noise (CatalogStarGenerator: amount = backgroundLevel).
    rng = np.random.default_rng(12345)
    img += rng.uniform(0.0, background_level, size=img.shape)
    np.clip(img, 0.0, 1.0, out=img)

    logger.info(f"[target_ref] rendered {n_drawn}/{len(x)} Moffat stars onto {w}x{h} "
                f"(fwhm={fwhm_pix}px beta={beta} bg={background_level:.1e})")
    return img.astype(np.float32)


def generate_reference(target: str, drizzled: bool = False,
                       mag_limit: float = _DEFAULT_MAG_LIMIT,
                       overwrite: bool = False) -> str | None:
    """Build a synthetic Gaia reference FITS for `target`. Returns path or None.

    Skips (returns existing) if already built and not `overwrite`. Returns None
    when canonical WCS is unavailable (missing DB coords / folio size) or the
    Gaia query yields nothing.
    """
    from astropy.io import fits

    from nas_server.canonical_frame import canonical_target_wcs

    REF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REF_DIR / f"{_sanitize(target)}.fits"
    if out_path.exists() and not overwrite:
        logger.info(f"[target_ref] {target}: reference exists → {out_path.name}")
        return str(out_path)

    cw = canonical_target_wcs(target, drizzled=drizzled)
    if cw is None:
        logger.info(f"[target_ref] {target}: no canonical WCS — cannot build reference")
        return None
    wcs, (h, w) = cw

    center_ra, center_dec = (float(v) for v in wcs.wcs.crval)
    scale_deg = abs(float(wcs.wcs.cdelt[1]))
    radius = _cone_radius_deg(h, w, scale_deg)

    csv_path = REF_DIR / f"{_sanitize(target)}_gaia.csv"
    res = _run_gaia_query(center_ra, center_dec, radius, str(csv_path),
                          mag_limit=mag_limit)
    if not res.get("ok") or not csv_path.exists():
        return None

    try:
        arr = np.genfromtxt(str(csv_path), delimiter=",", names=True)
        ra = np.atleast_1d(arr["ra"])
        dec = np.atleast_1d(arr["dec"])
        magG = np.atleast_1d(arr["magG"])
    except Exception as e:
        logger.warning(f"[target_ref] {target}: failed to read Gaia CSV: {e}")
        return None
    if ra.size == 0:
        logger.warning(f"[target_ref] {target}: Gaia CSV empty")
        return None

    img = _render_starfield(wcs, (h, w), ra, dec, magG, mag_limit)

    hdr = wcs.to_header()
    hdr["OBJECT"] = target
    hdr["REFTYPE"] = ("gaia_synth", "Synthetic Gaia DR3 StarAlignment reference")
    hdr["NGAIA"] = (int(ra.size), "Gaia sources queried")
    hdr["MAGLIM"] = (float(mag_limit), "Gaia G faint limit")
    fits.writeto(str(out_path), np.ascontiguousarray(img.astype(np.float32)),
                 header=hdr, overwrite=True)
    logger.info(f"[target_ref] {target}: wrote synthetic reference {out_path.name} "
                f"({w}x{h}, {ra.size} sources)")
    return str(out_path)

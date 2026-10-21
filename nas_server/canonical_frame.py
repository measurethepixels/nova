"""Canonical per-target framing + coverage-driven crop bounds.

Two jobs:
  1. `coverage_crop_bounds` — turn a frame-coverage map (from stacker
     `<stack>_coverage.fit`) into a crop rectangle at a chosen coverage
     fraction (≈0.80 → coverage candidate, ≈1.0 → intersection candidate).
  2. `canonical_target_wcs` / `reproject_to_canonical` — reproject a
     plate-solved stack onto a fixed per-target WCS + dimensions so every
     session of the same target shares one frame.

SeeStar S50 plate scale is fixed: 2.9"/px native, 1.45"/px at 2× drizzle.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("seestar.canonical")

# SeeStar S50 plate scale (arcsec/px), measured empirically from plate-solved
# stacks: every M 33 PCC frame solves to 2.374"/px (1080x1920, non-drizzled),
# matching the optics (250mm FL, 2.9um pixels → 206.265*2.9/250 = 2.39"/px).
# NOTE: the "2.9"/px" figure elsewhere is the pixel PITCH in microns, not the
# plate scale — using it gives a ~22% scale error that breaks StarAlignment of
# real subs against a synthetic canonical reference.
S50_SCALE_NATIVE_ARCSEC = 2.374
S50_SCALE_DRIZZLE_ARCSEC = 1.187
# Canonical canvas size cap (arcmin, per axis). Single-pointing targets sit well under
# this; the cap only bites large mosaic targets (e.g. M 31, 178' major axis) where we
# deliberately want a big square canvas covering the whole galaxy at any position angle.
# 240' covers M 31's ~205' (size×margin) square while still rejecting an absurd canvas.
# Square-span logic (below) keeps both axes equal, so a tilted galaxy is fully contained
# regardless of its on-sky orientation.
_CANON_MAX_W_ARCMIN = 240.0
_CANON_MAX_H_ARCMIN = 240.0
_CANON_MARGIN = 1.15


def coverage_crop_bounds(cov_map: np.ndarray, n_frames: int, frac: float,
                         tile: int = 16):
    """Crop rectangle where every kept pixel has coverage ≥ frac·n_frames.

    The coverage map is reduced to tiles (a tile is valid only if its *least*-
    covered pixel meets the threshold, guaranteeing the kept rectangle is fully
    covered) then the Largest Inscribed Rectangle of valid tiles is found.

    Returns (top, bottom, left, right, info) in full-resolution pixel indices,
    or None if no valid region exists.
    """
    from nas_server.seti_astro import _largest_inscribed_rect

    if cov_map is None or n_frames <= 0:
        return None
    cov = np.asarray(cov_map)
    if cov.ndim != 2:
        return None
    h, w = cov.shape
    thresh = max(1.0, frac * n_frames)

    nty = max(1, h // tile)
    ntx = max(1, w // tile)
    tile_min = np.zeros((nty, ntx), dtype=np.float32)
    for ty in range(nty):
        y0 = ty * tile
        y1 = h if ty == nty - 1 else (ty + 1) * tile
        for tx in range(ntx):
            x0 = tx * tile
            x1 = w if tx == ntx - 1 else (tx + 1) * tile
            tile_min[ty, tx] = float(cov[y0:y1, x0:x1].min())

    valid = tile_min >= thresh
    try:
        from scipy.ndimage import binary_fill_holes
        filled = binary_fill_holes(valid)
        if filled is not None:
            valid = filled
    except Exception:
        pass

    rect = _largest_inscribed_rect(valid)
    if rect is None:
        return None
    r0, r1, c0, c1 = rect
    top = r0 * tile
    bottom = h if r1 == nty - 1 else (r1 + 1) * tile
    left = c0 * tile
    right = w if c1 == ntx - 1 else (c1 + 1) * tile

    kept = max(0, bottom - top) * max(0, right - left)
    info = {
        "method": "coverage_map",
        "frac": frac,
        "threshold_frames": round(thresh, 2),
        "n_frames": int(n_frames),
        "kept_frac_of_frame": round(kept / float(h * w), 3),
        "rect_tiles": [r0, r1, c0, c1],
    }
    return top, bottom, left, right, info


def _target_radec(target_name: str):
    """(ra_deg, dec_deg) from targets table, falling back to light_files."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT ra, dec FROM targets WHERE target=?", (target_name,)
            ).fetchone()
            if row and row[0] is not None:
                return float(row[0]), float(row[1])
            row = conn.execute(
                "SELECT ra, dec FROM light_files WHERE target=? AND ra IS NOT NULL LIMIT 1",
                (target_name,)
            ).fetchone()
            if row:
                return float(row[0]), float(row[1])
    except Exception as e:
        logger.warning(f"[canonical] RA/Dec lookup failed for {target_name}: {e}")
    return None, None


def canonical_target_wcs(target_name: str, drizzled: bool = False):
    """Fixed per-target WCS + (h, w) for canonical framing, or None.

    Center from the DB target RA/Dec; pixel scale 2.9"/px native (1.45"/px when
    the stack is drizzled); dimensions from the folio angular size × margin,
    capped to the 2× frame. North-up (data row 0 = north) east-left, matching
    the pipeline's post-stack orientation. Returns None when coords or folio
    size are missing → canonical candidate is simply skipped.
    """
    from astropy.wcs import WCS

    ra, dec = _target_radec(target_name)
    if ra is None or dec is None:
        return None

    try:
        from nas_server.planner import _folio_info
        size_arcmin = _folio_info(target_name).get("angular_size_arcmin")
    except Exception:
        size_arcmin = None
    if not size_arcmin or size_arcmin <= 0:
        return None

    scale_arcsec = S50_SCALE_DRIZZLE_ARCSEC if drizzled else S50_SCALE_NATIVE_ARCSEC
    scale_deg = scale_arcsec / 3600.0

    # Square canvas at folio size × margin, capped to the 2× frame.
    span_arcmin = float(size_arcmin) * _CANON_MARGIN
    w_arcmin = min(span_arcmin, _CANON_MAX_W_ARCMIN)
    h_arcmin = min(span_arcmin, _CANON_MAX_H_ARCMIN)
    w = max(64, int(round(w_arcmin * 60.0 / scale_arcsec)))
    h = max(64, int(round(h_arcmin * 60.0 / scale_arcsec)))

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [w / 2.0 + 0.5, h / 2.0 + 0.5]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    # CDELT1 < 0 (east left), CDELT2 > 0 (Dec increases up) → det(CD) < 0, the
    # standard non-mirrored sky parity. Must match real plate-solved S50 frames
    # (which solve to CDELT2 > 0); a flipped parity here mirrors the synthetic
    # StarAlignment reference and makes registration fail (StarAlignment does not
    # try mirror transforms). Display "north-up" is handled at render/view time,
    # not by flipping the WCS.
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    logger.info(f"[canonical] {target_name}: WCS {w}x{h} @ {scale_arcsec}\"/px "
                f"center ({ra:.4f}, {dec:.4f}) drizzled={drizzled}")
    return wcs, (h, w)


def supported_crop_bounds(valid: np.ndarray):
    """Exact maximum supported rectangle, without tile rounding or hole filling.

    Partial coverage uses an 80% retained-valid-area floor; callers can reject
    the rectangle rather than over-trim a rotated/wedge-shaped field.
    """
    valid = np.asarray(valid, dtype=bool)
    if valid.ndim != 2 or not valid.size:
        return None
    # Histogram maximal rectangle, O(H*W); vectorize height updates.
    heights = np.zeros(valid.shape[1], dtype=int)
    best, best_area = None, 0
    for y, row in enumerate(valid):
        heights = np.where(row, heights + 1, 0)
        stack = []
        for x in range(len(heights) + 1):
            height = int(heights[x]) if x < len(heights) else 0
            left = x
            while stack and stack[-1][1] >= height:
                origin, previous = stack.pop()
                area = previous * (x - origin)
                if area > best_area:
                    best_area = area
                    best = (y - previous + 1, y + 1, origin, x)
                left = origin
            stack.append((left, height))
    return best


def crop_coverage_provenance(path: str) -> dict:
    """Audit science validity (all channels), including saved/manual crop paths."""
    from astropy.io import fits
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
        valid = np.isfinite(data)
        if data.ndim == 3:
            valid = valid.all(axis=0)
        if "VALIDITY" in hdul:
            valid &= hdul["VALIDITY"].data.astype(bool)
        hdr = hdul[0].header
        return {
            "coverage_fraction": float(hdr.get("COVFRAC", valid.mean())),
            "unsupported_fraction": float(1.0 - valid.mean()),
            "retained_area_fraction": float(hdr.get("COVKEEP", 1.0)),
            "retained_canvas_fraction": float(hdr.get("COVAREA", 1.0)),
            "coverage_path": hdr.get("COVPATH", "finite_input"),
            "shape": list(valid.shape),
        }


def reproject_to_canonical(stack_fits: str, wcs, shape, out_fits: str,
                           *, preserve_frame: bool = False) -> str | None:
    """Resample with explicit validity; crop automatic frames, preserve saved ones.

    VALIDITY records the delivered support, REPROJ_VALID the original canvas.
    Saved frames retain NaNs and geometry, with a warning; the pipeline crop
    checkpoint refuses unsupported science before any downstream modelling.
    Automatic partial frames must retain >=80% of supported area and clear
    the existing S50 native-resolution floor, otherwise the candidate is skipped.
    """
    from astropy.io import fits
    from astropy.wcs import WCS
    from reproject import reproject_interp

    h, w = shape
    try:
        with fits.open(str(stack_fits), memmap=False) as hdul:
            data = np.asarray(hdul[0].data, dtype=np.float32)
            src_wcs = WCS(hdul[0].header, naxis=2)
        if not src_wcs.has_celestial or data.ndim not in (2, 3):
            logger.warning("[canonical] no celestial WCS or unsupported dimensions: %s", stack_fits)
            return None
        channels = data if data.ndim == 3 else data[None, ...]
        arrays = []
        valid = np.ones((h, w), dtype=bool)
        for channel in channels:
            arr, footprint = reproject_interp((channel, src_wcs), wcs, shape_out=(h, w))
            valid &= np.isfinite(arr) & np.isfinite(footprint) & (footprint > 0)
            arrays.append(arr.astype(np.float32))
        out = np.stack(arrays)
        out[:, ~valid] = np.nan
        coverage = float(valid.mean())
        bounds = (0, h, 0, w)
        retained = 1.0
        path = "full_coverage"
        if not valid.all():
            if preserve_frame:
                path = "saved_frame_unsupported"
                logger.warning("[canonical] saved framing unchanged: unsupported_fraction=%.6f; "
                               "downstream processing must stop", 1.0 - coverage)
            else:
                bounds = supported_crop_bounds(valid)
                if bounds is None:
                    logger.warning("[canonical] no supported pixels; skipping candidate")
                    return None
                top, bottom, left, right = bounds
                retained = (bottom - top) * (right - left) / float(valid.sum())
                floor = max(900, int(0.45 * min(data.shape[-2:])))
                if retained < 0.80 or min(bottom - top, right - left) < floor:
                    logger.warning("[canonical] skip lossy candidate: coverage=%.6f "
                                   "retained_valid_area=%.6f short=%s floor=%s",
                                   coverage, retained, min(bottom-top, right-left), floor)
                    return None
                path = "supported_rectangle"
        top, bottom, left, right = bounds
        delivered_valid = valid[top:bottom, left:right]
        out = out[:, top:bottom, left:right]
        hdr = wcs.slice((slice(top, bottom), slice(left, right))).to_header()
        hdr["COVFRAC"] = (coverage, "Supported fraction of reprojection canvas")
        hdr["COVKEEP"] = (retained, "Retained fraction of supported canvas pixels")
        hdr["COVAREA"] = ((bottom-top) * (right-left) / float(h*w), "Retained fraction of canvas")
        hdr["COVPATH"] = path
        hdr["COVTOP"], hdr["COVLEFT"] = top, left
        science = out if data.ndim == 3 else out[0]
        fits.HDUList([
            fits.PrimaryHDU(np.ascontiguousarray(science), header=hdr),
            fits.ImageHDU(delivered_valid.astype(np.uint8), name="VALIDITY"),
            fits.ImageHDU(valid.astype(np.uint8), header=wcs.to_header(), name="REPROJ_VALID"),
        ]).writeto(str(out_fits), overwrite=True)
        logger.info("[canonical] %s -> %s coverage=%.6f retained_valid_area=%.6f "
                    "path=%s bounds=%s", Path(stack_fits).name, Path(out_fits).name,
                    coverage, retained, path, bounds)
        return out_fits
    except Exception as e:
        logger.warning(f"[canonical] reproject failed for {stack_fits}: {e}")
        return None

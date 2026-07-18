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


def reproject_to_canonical(stack_fits: str, wcs, shape, out_fits: str) -> str | None:
    """Reproject a plate-solved stack onto the canonical WCS/shape → out_fits.

    Handles 2D (H,W) and 3D (C,H,W) data, reprojecting each channel. Writes the
    canonical WCS into the output header. Returns out_fits, or None on failure.
    """
    from astropy.io import fits
    from astropy.wcs import WCS
    from reproject import reproject_interp

    h, w = shape
    try:
        with fits.open(str(stack_fits), memmap=False) as hdul:
            data = np.asarray(hdul[0].data, dtype=np.float32)
            # naxis=2 selects the two celestial axes directly. Plain WCS(header)
            # on a 3-axis FITS with SIP raises ("SIP only works in 2 dimensions"),
            # and .celestial/.sub trip the same error — naxis=2 avoids it.
            src_wcs = WCS(hdul[0].header, naxis=2)
        if not src_wcs.has_celestial:
            logger.warning(f"[canonical] {stack_fits}: no celestial WCS — cannot reproject")
            return None

        if data.ndim == 3:
            out = np.zeros((data.shape[0], h, w), dtype=np.float32)
            for c in range(data.shape[0]):
                arr, _ = reproject_interp((data[c], src_wcs), wcs, shape_out=(h, w))
                out[c] = np.nan_to_num(arr, nan=0.0).astype(np.float32)
        else:
            arr, _ = reproject_interp((data, src_wcs), wcs, shape_out=(h, w))
            out = np.nan_to_num(arr, nan=0.0).astype(np.float32)

        hdr = wcs.to_header()
        fits.writeto(str(out_fits), np.ascontiguousarray(out), header=hdr, overwrite=True)
        logger.info(f"[canonical] reprojected {Path(stack_fits).name} → "
                    f"{Path(out_fits).name} ({w}x{h})")
        return out_fits
    except Exception as e:
        logger.warning(f"[canonical] reproject failed for {stack_fits}: {e}")
        return None

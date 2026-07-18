"""Per-target saved crop: persist the user's chosen crop and reuse it forever.

The first time a target is auto-processed the crop step opens a manual review;
whatever the user picks (a generated candidate or a hand-drawn crop) is recorded
here as a WCS *sky-box* — center RA/Dec, on-sky size, and position angle — so the
identical patch of sky is framed on every later session regardless of how the new
stack's pixel canvas grew (mosaic) or shifted. Fractional pixel bounds + rotation
are stored as a fallback for the rare stack with no usable WCS.

Reuse is a reproject onto the saved box (same machinery as canonical_frame), which
gives pixel-for-pixel identical framing across nights.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("seestar.target_crop")


def cropper_box_to_array(x: float, y: float, w: float, h: float,
                         rotate_deg: float, natural_w: float, natural_h: float,
                         orig_w: int, orig_h: int,
                         flipped: bool) -> tuple[int, int, int, int, float]:
    """Map a Cropper.js getData() box onto FITS array pixel coords.

    Two coordinate-space corrections the naive scale missed (both produced
    misaligned manual crops):
      1. When the editor preview was flipped north-up (generate_preview_stf flips
         for CDELT2 > 0), the box's y must be mirrored back into array row order
         and the rotation direction negated.
      2. When rotated, getData() x/y are in the ROTATED bounding-canvas space
         (Cropper reproduces a crop by rotating the image first, then cutting the
         axis-aligned box) — the box center must be mapped back through the
         inverse rotation about the canvas/display centers.

    Returns (cx, cy, cw, ch, rot_array): the axis-aligned box top-left/size in
    array pixels plus the array-space rotation to feed rotated_crop().
    """
    import math
    sx = orig_w / max(natural_w, 1.0)
    sy = orig_h / max(natural_h, 1.0)
    theta = math.radians(rotate_deg)

    # Box center in canvas coords (canvas == display when not rotated)
    bc_row, bc_col = y + h / 2.0, x + w / 2.0
    if abs(rotate_deg) > 0.05:
        cos_a, sin_a = math.cos(theta), math.sin(theta)
        canvas_w = natural_w * abs(cos_a) + natural_h * abs(sin_a)
        canvas_h = natural_w * abs(sin_a) + natural_h * abs(cos_a)
        # canvas → display: inverse of the CW screen rotation, in (row, col)
        dr, dc = bc_row - canvas_h / 2.0, bc_col - canvas_w / 2.0
        bc_row = cos_a * dr - sin_a * dc + natural_h / 2.0
        bc_col = sin_a * dr + cos_a * dc + natural_w / 2.0

    rot_array = rotate_deg
    if flipped:
        bc_row = natural_h - bc_row   # preview row order is mirrored vs array
        rot_array = -rotate_deg       # mirror reverses rotation direction

    ac_row, ac_col = bc_row * sy, bc_col * sx
    cw = max(1, int(round(w * sx)))
    ch = max(1, int(round(h * sy)))
    cx = int(round(ac_col - cw / 2.0))
    cy = int(round(ac_row - ch / 2.0))
    if abs(rot_array) <= 0.05:
        # No-rotation path slices directly — keep the box inside the frame
        cw = min(cw, orig_w)
        ch = min(ch, orig_h)
        cx = max(0, min(cx, orig_w - cw))
        cy = max(0, min(cy, orig_h - ch))
    return cx, cy, cw, ch, rot_array


def rotated_crop(data: np.ndarray, cx: int, cy: int, cw: int, ch: int,
                 rotate_deg: float) -> np.ndarray:
    """Extract a `cw`×`ch` axis-aligned box whose content is the source rotated
    by `rotate_deg` about the box center — filled entirely with real pixels.

    Rotate-then-crop: we sample the rotated source directly into the output box
    via affine_transform, so the result has no black corner wedges (the bug from
    the old crop-then-rotate-with-reshape path). `cx,cy` = box top-left, `cw,ch` =
    box size, all in `data` pixel coords. Sign matches the prior convention
    (Cropper.js +angle = clockwise; scipy/affine sampling uses -angle).
    """
    cx = int(cx); cy = int(cy); cw = int(cw); ch = int(ch)
    if abs(rotate_deg) <= 0.05:
        if data.ndim == 3:
            return np.ascontiguousarray(data[:, cy:cy + ch, cx:cx + cw])
        return np.ascontiguousarray(data[cy:cy + ch, cx:cx + cw])

    import math
    from scipy.ndimage import affine_transform

    # Inverse-map output coords into the source. theta = +rotate reproduces the
    # prior scipy.ndimage.rotate(-rotate) visual direction (Cropper.js +angle=CW),
    # just without the reshape black wedges.
    theta = math.radians(rotate_deg)
    cos_a, sin_a = math.cos(theta), math.sin(theta)
    # Sampling rotation in (row, col) = (y, x) image coords.
    R = np.array([[cos_a, -sin_a], [sin_a, cos_a]], dtype=np.float64)
    out_center = np.array([ch / 2.0, cw / 2.0])
    box_center = np.array([cy + ch / 2.0, cx + cw / 2.0])
    offset = box_center - R @ out_center

    def _one(plane: np.ndarray) -> np.ndarray:
        return affine_transform(plane, R, offset=offset, output_shape=(ch, cw),
                                order=1, mode="constant", cval=0.0)

    if data.ndim == 3:
        out = np.stack([_one(data[c]) for c in range(data.shape[0])])
    else:
        out = _one(data)
    return np.ascontiguousarray(out.astype(np.float32))


# --------------------------------------------------------------------------- DB

def get_target_crop(target: str) -> dict | None:
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM target_crops WHERE target=?", (target,)
        ).fetchone()
    return dict(row) if row else None


def has_target_crop(target: str) -> bool:
    return get_target_crop(target) is not None


def clear_target_crop(target: str) -> bool:
    from nas_server.database import get_conn
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM target_crops WHERE target=?", (target,))
        return cur.rowcount > 0


def _save_target_crop(target: str, box: dict, frac: dict | None,
                      rotate_deg: float, source: str) -> None:
    from nas_server.database import get_conn
    frac = frac or {}
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO target_crops
                (target, center_ra, center_dec, width_arcmin, height_arcmin,
                 pa_deg, scale_arcsec, frac_top, frac_bottom, frac_left, frac_right,
                 rotate_deg, source, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(target) DO UPDATE SET
                center_ra=excluded.center_ra, center_dec=excluded.center_dec,
                width_arcmin=excluded.width_arcmin, height_arcmin=excluded.height_arcmin,
                pa_deg=excluded.pa_deg, scale_arcsec=excluded.scale_arcsec,
                frac_top=excluded.frac_top, frac_bottom=excluded.frac_bottom,
                frac_left=excluded.frac_left, frac_right=excluded.frac_right,
                rotate_deg=excluded.rotate_deg, source=excluded.source,
                updated_at=datetime('now')
        """, (
            target,
            box.get("center_ra"), box.get("center_dec"),
            box.get("width_arcmin"), box.get("height_arcmin"),
            box.get("pa_deg"), box.get("scale_arcsec"),
            frac.get("top"), frac.get("bottom"), frac.get("left"), frac.get("right"),
            float(rotate_deg), source,
        ))


# ------------------------------------------------------------------- geometry

def _box_from_fits(fits_path: str) -> dict | None:
    """Derive a sky-box (center RA/Dec, on-sky size, scale, PA) from a cropped FITS.

    Reads the FITS WCS: center = world coords of the central pixel; scale from the
    pixel scale; PA from the CD matrix; size = dims × scale. Returns None if the
    FITS has no celestial WCS.
    """
    from astropy.io import fits as afits
    from astropy.wcs import WCS

    try:
        with afits.open(str(fits_path), memmap=False) as hdul:
            data = np.asarray(hdul[0].data)
            wcs = WCS(hdul[0].header, naxis=2)
        if not wcs.has_celestial:
            return None
        h = int(data.shape[-2])
        w = int(data.shape[-1])

        sky = wcs.pixel_to_world(w / 2.0 - 0.5, h / 2.0 - 0.5)
        center_ra = float(sky.ra.deg)
        center_dec = float(sky.dec.deg)

        scales = wcs.proj_plane_pixel_scales()  # Quantity per axis (deg)
        scale_deg = float(np.mean([abs(s.to("deg").value) for s in scales]))
        scale_arcsec = scale_deg * 3600.0

        # Position angle of the +x (sample) axis from the CD/PC matrix.
        cd = wcs.pixel_scale_matrix  # 2x2 deg/pix
        pa_deg = float(np.degrees(np.arctan2(cd[1, 0], cd[0, 0])))

        return {
            "center_ra": center_ra,
            "center_dec": center_dec,
            "width_arcmin": w * scale_arcsec / 60.0,
            "height_arcmin": h * scale_arcsec / 60.0,
            "scale_arcsec": scale_arcsec,
            "pa_deg": pa_deg,
            "pix_w": w,
            "pix_h": h,
        }
    except Exception as e:
        logger.warning(f"[target_crop] box-from-FITS failed for {fits_path}: {e}")
        return None


def save_crop_from_fits(target: str, cropped_fits: str, source: str,
                        frac: dict | None = None, rotate_deg: float = 0.0) -> bool:
    """Persist the chosen crop for `target` from the winning cropped FITS.

    Stores the WCS sky-box (primary) plus fractional bounds + rotation (fallback).
    Returns True if at least one of the two representations was saved.
    """
    box = _box_from_fits(cropped_fits) or {}
    if box:
        # Fold any manual rotation into the stored PA so the reproject box matches
        # the hand-rotated framing.
        box["pa_deg"] = float(box.get("pa_deg", 0.0)) + float(rotate_deg)
    if not box and not frac:
        logger.warning(f"[target_crop] {target}: nothing to save (no WCS, no frac bounds)")
        return False
    _save_target_crop(target, box, frac, rotate_deg, source)
    logger.info(f"[target_crop] {target}: saved crop source='{source}' "
                f"box={'yes' if box else 'no'} "
                f"({box.get('width_arcmin', 0):.1f}'×{box.get('height_arcmin', 0):.1f}' "
                f"@ {box.get('scale_arcsec', 0):.2f}\"/px PA={box.get('pa_deg', 0):.1f}°)")
    return True


def _wcs_from_box(box: dict):
    """Build (WCS, (h, w)) for a saved sky-box. Rotated TAN frame at the saved PA."""
    from astropy.wcs import WCS

    ra = box["center_ra"]
    dec = box["center_dec"]
    scale_arcsec = box["scale_arcsec"]
    if not scale_arcsec or scale_arcsec <= 0:
        return None
    scale_deg = scale_arcsec / 3600.0
    w = max(16, int(round(box["width_arcmin"] * 60.0 / scale_arcsec)))
    h = max(16, int(round(box["height_arcmin"] * 60.0 / scale_arcsec)))

    th = np.radians(float(box.get("pa_deg", 0.0)))
    cos_t, sin_t = np.cos(th), np.sin(th)
    # Base CD: east-left (CD1_1<0), Dec-up (CD2_2>0), standard sky parity; then
    # rotate by the stored PA.
    base = np.array([[-scale_deg, 0.0], [0.0, scale_deg]])
    rot = np.array([[cos_t, -sin_t], [sin_t, cos_t]])
    cd = rot @ base

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [w / 2.0 + 0.5, h / 2.0 + 0.5]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cd = cd
    return wcs, (h, w)


def apply_saved_crop(target: str, stack_fits: str, out_fits: str) -> dict:
    """Apply the saved crop for `target` to a new stack.

    Primary path: reproject the plate-solved stack onto the saved WCS sky-box →
    identical framing every session. Fallback: fractional pixel bounds (+ rotation)
    when the box can't be built or the stack lacks a celestial WCS.
    Returns {"ok", "output_path", "method"} or {"ok": False, "error"}.
    """
    rec = get_target_crop(target)
    if not rec:
        return {"ok": False, "error": "no saved crop"}

    # --- Primary: WCS sky-box reproject ---
    if rec.get("center_ra") is not None and rec.get("scale_arcsec"):
        try:
            built = _wcs_from_box(rec)
            if built:
                wcs, shape = built
                from nas_server.canonical_frame import reproject_to_canonical
                if reproject_to_canonical(stack_fits, wcs, shape, out_fits):
                    # The box WCS is constructed astropy-consistent (standard sky
                    # parity), so stamp the PLTSOLVD marker: the renderer then uses
                    # the theoretical flip rule, keeping every step's preview
                    # orientation-consistent (audit F4 gate finding, 2026-07-02).
                    try:
                        from astropy.io import fits as _pf
                        with _pf.open(out_fits, mode="update", memmap=False) as _hd:
                            _hd[0].header["PLTSOLVD"] = (True, "box WCS astropy-consistent")
                            _hd.flush()
                    except Exception:
                        pass
                    logger.info(f"[target_crop] {target}: applied saved sky-box "
                                f"({shape[1]}x{shape[0]}) via reproject")
                    return {"ok": True, "output_path": out_fits, "method": "skybox"}
        except Exception as e:
            logger.warning(f"[target_crop] {target}: sky-box reproject failed: {e}")

    # --- Fallback: fractional bounds (+ rotation) ---
    if rec.get("frac_left") is not None:
        try:
            res = _apply_frac_crop(stack_fits, out_fits, rec)
            if res:
                logger.info(f"[target_crop] {target}: applied saved fractional crop")
                return {"ok": True, "output_path": out_fits, "method": "frac"}
        except Exception as e:
            logger.warning(f"[target_crop] {target}: fractional crop failed: {e}")

    return {"ok": False, "error": "saved crop could not be applied"}


def _apply_frac_crop(stack_fits: str, out_fits: str, rec: dict) -> bool:
    from astropy.io import fits as afits

    with afits.open(str(stack_fits), memmap=False) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
        header = hdul[0].header.copy()
    h = int(data.shape[-2])
    w = int(data.shape[-1])

    top = int(round(rec["frac_top"] * h))
    bottom = int(round((1.0 - rec["frac_bottom"]) * h))
    left = int(round(rec["frac_left"] * w))
    right = int(round((1.0 - rec["frac_right"]) * w))
    top = max(0, min(top, h - 2))
    left = max(0, min(left, w - 2))
    bottom = max(top + 1, min(bottom, h))
    right = max(left + 1, min(right, w))

    rot = float(rec.get("rotate_deg") or 0.0)
    cropped = rotated_crop(data, left, top, right - left, bottom - top, rot)

    if "CRPIX1" in header:
        header["CRPIX1"] = float(header["CRPIX1"]) - left
    if "CRPIX2" in header:
        header["CRPIX2"] = float(header["CRPIX2"]) - top

    afits.PrimaryHDU(data=np.ascontiguousarray(cropped), header=header).writeto(
        str(out_fits), overwrite=True)
    return True


def frac_bounds_from_crop(orig_fits: str, cropped_fits: str) -> dict | None:
    """Estimate fractional crop bounds by comparing original vs cropped dims.

    Symmetric estimate (centered) — exact enough as a no-WCS fallback. Returns
    {top, bottom, left, right} as fractions removed from each edge, or None.
    """
    from astropy.io import fits as afits
    try:
        oh = afits.getheader(str(orig_fits))
        ch = afits.getheader(str(cropped_fits))
        ow, oh_ = int(oh.get("NAXIS1", 0)), int(oh.get("NAXIS2", 0))
        cw, ch_ = int(ch.get("NAXIS1", 0)), int(ch.get("NAXIS2", 0))
        if ow <= 0 or oh_ <= 0 or cw <= 0 or ch_ <= 0:
            return None
        dx = max(0.0, (ow - cw) / (2.0 * ow))
        dy = max(0.0, (oh_ - ch_) / (2.0 * oh_))
        return {"top": dy, "bottom": dy, "left": dx, "right": dx}
    except Exception:
        return None

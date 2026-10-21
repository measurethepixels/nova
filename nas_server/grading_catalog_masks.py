"""Frozen catalog/WCS masks for the non-authoritative epoch/1 pilot."""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np

VERSION = "catalog_ellipse_v1"
TARGET_FACTOR = 1.0
SKY_INNER = 1.5
SKY_OUTER = 2.5
CORE_FRACTION = 0.35
MIN_PIXELS = 64
MIN_SKY_FRACTION = 0.01
STAR_SIGMA = 5.0
STAR_RADIUS_SIGMA = 2.5


class MaskUnavailable(ValueError):
    """The frozen mask definition cannot support this artifact."""


def catalog_masks(image, header, geometry: dict):
    """Return disjoint sky, target-ring, core and star masks plus provenance.

    The only image-derived part is the compact-star exclusion. Target geometry
    depends solely on the frozen catalogue row and the artifact's WCS.
    """
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from astropy.wcs import WCS
    import sep
    from nas_server.image_analyzer import _compact_object_mask

    if geometry.get("geometry_rule") not in {"ellipse", "circle"}:
        raise MaskUnavailable("missing catalog geometry")
    h, w = image.shape[:2]
    if (int(header.get("NAXIS1", -1)), int(header.get("NAXIS2", -1))) != (w, h):
        raise MaskUnavailable("FITS and presentation dimensions differ")
    try:
        center = SkyCoord(float(geometry["ra_deg"]) * u.deg,
                          float(geometry["dec_deg"]) * u.deg)
        major = float(geometry["major_arcmin"]) / 2 * TARGET_FACTOR
        ratio = float(geometry["axis_ratio"])
        pa = float(geometry["pa_deg"])
        if not all(math.isfinite(x) for x in (major, ratio, pa)) or major <= 0 or ratio < 1:
            raise ValueError("invalid catalog ellipse")
        minor = major / ratio
        wcs = WCS(header, naxis=2).celestial
        if not wcs.has_celestial or wcs.pixel_n_dim != 2:
            raise ValueError("missing celestial WCS")
        x0, y0 = wcs.world_to_pixel(center)
        major_end = center.directional_offset_by(pa * u.deg, major * u.arcmin)
        minor_end = center.directional_offset_by((pa + 90) * u.deg, minor * u.arcmin)
        x1, y1 = wcs.world_to_pixel(major_end)
        x2, y2 = wcs.world_to_pixel(minor_end)
        basis = np.array([[x1-x0, x2-x0], [y1-y0, y2-y0]], dtype=float)
        if not np.isfinite(basis).all() or abs(np.linalg.det(basis)) < 1:
            raise ValueError("degenerate WCS projection")
        # Reject clipping before looking at pixels. This also rejects a
        # frame-filling target whose sky annulus cannot be defended.
        extent_x = SKY_OUTER * np.linalg.norm(basis[0])
        extent_y = SKY_OUTER * np.linalg.norm(basis[1])
        if not (extent_x <= x0 < w - extent_x and extent_y <= y0 < h - extent_y):
            raise ValueError("catalog sky annulus clips frame")
        yy, xx = np.indices((h, w))
        uv = np.linalg.solve(basis, np.stack(((xx-x0).ravel(), (yy-y0).ravel())))
        radius = np.hypot(uv[0], uv[1]).reshape(h, w)
    except Exception as exc:
        raise MaskUnavailable(str(exc)) from exc

    # SEP's robust background is used only for compact-star exclusion, never
    # to move the catalog-derived target boundary.
    try:
        lum = np.ascontiguousarray(image.mean(axis=2), dtype=np.float32)
        background = sep.Background(lum)
        objects = sep.extract(lum - background.back(), STAR_SIGMA,
                              err=background.globalrms, minarea=9,
                              deblend_nthresh=8, deblend_cont=0.01, clean=True)
        compact = objects[_compact_object_mask(objects)]
        stars = np.zeros((h, w), dtype=bool)
        for obj in compact:
            sx, sy = float(obj["x"]), float(obj["y"])
            r = max(2.0, STAR_RADIUS_SIGMA * float(obj["a"]))
            left, right = max(0, int(sx-r)), min(w, int(sx+r+1))
            top, bottom = max(0, int(sy-r)), min(h, int(sy+r+1))
            if left < right and top < bottom:
                ys, xs = np.ogrid[top:bottom, left:right]
                stars[top:bottom, left:right] |= (xs-sx)**2 + (ys-sy)**2 <= r*r
    except Exception as exc:
        raise MaskUnavailable(f"star exclusion failed: {exc}") from exc

    core = (radius <= CORE_FRACTION) & ~stars
    target = (radius > CORE_FRACTION) & (radius <= 1) & ~stars
    sky = (radius >= SKY_INNER) & (radius <= SKY_OUTER) & ~stars
    if min(core.sum(), target.sum(), sky.sum()) < MIN_PIXELS or sky.mean() < MIN_SKY_FRACTION:
        raise MaskUnavailable("insufficient pixels after star exclusion")
    wcs_cards = {key: header.get(key) for key in header if key.startswith(
        ("CTYPE", "CRPIX", "CRVAL", "CD", "PC", "CDELT", "A_", "B_", "AP_", "BP_"))}
    provenance = {"mask_source": VERSION, "geometry": geometry,
        "wcs_sha256": hashlib.sha256(json.dumps(wcs_cards, sort_keys=True,
            default=str).encode()).hexdigest(),
        "center_pixel": [float(x0), float(y0)], "star_count": len(compact),
        "coverage": {"sky": float(sky.mean()), "target": float(target.mean()),
                     "core": float(core.mean()), "stars": float(stars.mean())}}
    return sky, target, core, stars, provenance

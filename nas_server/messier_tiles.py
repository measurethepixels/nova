"""Messier Wall tiles — WCS-centered crops, with multi-object harvesting.

Two problems this solves (Henry, 2026-07-13):
1. Off-center targets: the wall's `background-size: cover` blindly crops the image
   center; a target framed off-center (or a small target in a wide field) shows as
   empty sky. Fix: locate the object's exact pixel position via plate solve and cut
   a centered square tile sized from its apparent size.
2. Multi-object fields: one processed image can serve every Messier object inside
   its footprint — M 32 and M 110 from an M 31 final, M 65 from the M 66 field,
   M 82 from M 81 — filling wall tiles for objects that never had their own run.

Geometry always comes from a fresh ASTAP solve of a TEMP COPY of the final (never
the artifact itself): ASTAP writes astropy-consistent WCS, sidestepping the
Siril/PI convention mismatch that mirrors astropy reads (see the astap-wcs-audit
memory). Solutions are cached in the manifest keyed by the final's path+mtime, so
each final is solved once.

Outputs: JPGs + manifest.json in ~/seestar_database/messier_tiles/.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

TILE_DIR = Path.home() / "seestar_database" / "messier_tiles"
MANIFEST = TILE_DIR / "manifest.json"
DB = str(Path.home() / "seestar_database" / "astro_data.db")

EDGE_MARGIN_PX = 100     # object must sit this far inside the host frame
TILE_RENDER_PX = 512     # output JPG side
MIN_CROP_PX = 220        # never crop tighter than this (S50 resolution floor)
PAD = 1.6                # crop = apparent size × PAD


def _load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        return {"tiles": {}, "wcs_cache": {}}


def _save_manifest(m: dict) -> None:
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1, default=str))


def _solve_wcs(final_path: Path, cache: dict) -> dict | None:
    """ASTAP-solve a temp copy of the final; return compact WCS dict (cached)."""
    key = f"{final_path}:{int(final_path.stat().st_mtime)}"
    if key in cache:
        return cache[key] or None
    from nas_server.seti_astro import astap_solve
    from astropy.io import fits
    from astropy.wcs import WCS
    result = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            tmp = Path(tf.name)
        shutil.copy2(final_path, tmp)
        try:
            # Hint: finals often lack CRVAL but keep RA/DEC-ish keys; astap_solve
            # falls back to blind (-r 180) when hint-less — slower but correct.
            r = astap_solve(tmp, fov_deg=1.3, timeout=180)
            if r.get("ok"):
                hdr = fits.getheader(str(tmp))
                w = WCS(hdr, naxis=2)
                if w.has_celestial:
                    result = {
                        "header": {k: hdr[k] for k in
                                   ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
                                    "CD1_1", "CD1_2", "CD2_1", "CD2_2",
                                    "CTYPE1", "CTYPE2") if k in hdr},
                        "nx": int(hdr.get("NAXIS1", 0)),
                        "ny": int(hdr.get("NAXIS2", 0)),
                    }
        finally:
            tmp.unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"[messier_tiles] solve failed for {final_path.name}: {e}")
    cache[key] = result
    return result


def _pixel_scale_arcsec(whdr: dict) -> float:
    cd11 = float(whdr.get("CD1_1", 0)); cd12 = float(whdr.get("CD1_2", 0))
    s = math.hypot(cd11, cd12) * 3600.0
    return s if s > 0.05 else 2.37


def _world_to_pixel(whdr: dict, nx: int, ny: int, ra: float, dec: float):
    from astropy.wcs import WCS
    from astropy.io.fits import Header
    h = Header()
    for k, v in whdr.items():
        h[k] = v
    h["NAXIS"] = 2; h["NAXIS1"] = nx; h["NAXIS2"] = ny
    w = WCS(h, naxis=2)
    x, y = w.celestial.world_to_pixel_values(ra, dec)
    return float(x), float(y)


def _render_tile(final_path: Path, cx: float, cy: float, half_px: int,
                 out_jpg: Path) -> bool:
    import numpy as np
    from astropy.io import fits
    from PIL import Image
    try:
        d = fits.getdata(str(final_path)).astype("float32")
        if d.ndim == 3 and d.shape[0] in (3, 4):
            d = np.moveaxis(d[:3], 0, -1)
        if d.max() > 1.5:
            d = d / d.max()
        ny, nx = d.shape[:2]
        # FITS pixel (0-based) → array row = y
        x0 = int(max(0, min(nx - 2 * half_px, cx - half_px)))
        y0 = int(max(0, min(ny - 2 * half_px, cy - half_px)))
        crop = d[y0:y0 + 2 * half_px, x0:x0 + 2 * half_px]
        if crop.size == 0:
            return False
        img = Image.fromarray((np.clip(crop, 0, 1) * 255).astype("uint8"))
        img = img.resize((TILE_RENDER_PX, TILE_RENDER_PX), Image.LANCZOS)
        TILE_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(out_jpg), quality=88)
        return True
    except Exception as e:
        log.warning(f"[messier_tiles] render failed ({out_jpg.name}): {e}")
        return False


def build_tiles(force: bool = False) -> dict:
    """(Re)build centered tiles for all 110 Messier objects. Returns a summary."""
    from nas_server.messier import _CATALOG
    from nas_server.database import get_worklist

    t0 = time.time()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    coords = {}
    for r in db.execute("SELECT target, ra, dec FROM targets"):
        m = re.fullmatch(r"M ?(\d{1,3})", (r["target"] or "").strip())
        if m and r["ra"] is not None:
            coords[int(m.group(1))] = (float(r["ra"]), float(r["dec"]))

    manifest = _load_manifest()
    if force:
        manifest["tiles"] = {}
    wcs_cache = manifest.setdefault("wcs_cache", {})
    tiles = manifest.setdefault("tiles", {})

    # Host images: every worklist best final (any target — that's what enables
    # guest harvesting), own-target finals sorted first per object below.
    hosts = []
    for row in get_worklist():
        p = row.get("best_output_path") or ""
        if not p:
            continue
        fp = Path(p)
        if not fp.exists():
            # runs store final as 22_final.fit sometimes; try sibling
            alt = fp.parent / "22_final.fit"
            fp = alt if alt.exists() else None
        if fp:
            hosts.append({"target": row["target"], "final": fp,
                          "score": row.get("best_overall")})

    sizes = {num: size for num, _, _, size, _ in
             [(c[0], c[1], c[2], c[3], c[4]) for c in _CATALOG]}

    built = skipped = failed = 0
    for num, (ra, dec) in sorted(coords.items()):
        name = f"M {num}"
        out_jpg = TILE_DIR / f"m{num}.jpg"
        # candidates: own final first, then guests
        cands = ([h for h in hosts if h["target"] == name]
                 + [h for h in hosts if h["target"] != name])
        entry = None
        for h in cands:
            key = f"{h['final']}:{int(h['final'].stat().st_mtime)}"
            prev = tiles.get(str(num))
            if (not force and prev and prev.get("host_key") == key
                    and out_jpg.exists()):
                entry = prev
                skipped += 1
                break
            wcs = _solve_wcs(h["final"], wcs_cache)
            if not wcs:
                continue
            try:
                x, y = _world_to_pixel(wcs["header"], wcs["nx"], wcs["ny"], ra, dec)
            except Exception:
                continue
            if not (EDGE_MARGIN_PX <= x < wcs["nx"] - EDGE_MARGIN_PX
                    and EDGE_MARGIN_PX <= y < wcs["ny"] - EDGE_MARGIN_PX):
                continue
            scale = _pixel_scale_arcsec(wcs["header"])
            size_arcmin = sizes.get(num) or 10.0
            half = int(max(MIN_CROP_PX,
                           min(size_arcmin * 60.0 * PAD / scale / 2.0,
                               min(wcs["nx"], wcs["ny"]) / 2.0)))
            if _render_tile(h["final"], x, y, half, out_jpg):
                entry = {"host_target": h["target"], "host_key": key,
                         "final": str(h["final"]), "px": [round(x), round(y)],
                         "half_px": half, "score": h["score"],
                         "guest": h["target"] != name,
                         "built_at": time.strftime("%Y-%m-%d %H:%M")}
                tiles[str(num)] = entry
                built += 1
                log.info(f"[messier_tiles] M {num} ← {h['target']}"
                         f"{' (guest)' if entry['guest'] else ''}")
                break
        if entry is None:
            failed += 1
    _save_manifest(manifest)
    summary = {"built": built, "cached": skipped, "no_tile": failed,
               "elapsed_s": round(time.time() - t0, 1)}
    log.info(f"[messier_tiles] build complete: {summary}")
    return summary


def get_tiles() -> dict:
    """Manifest tiles keyed by Messier number (str) — for the wall page."""
    return _load_manifest().get("tiles", {})

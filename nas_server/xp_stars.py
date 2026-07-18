"""
xp_stars — shared Gaia XP star plumbing for NBExtract + SSSC headless integration.

Pipeline: detect stars (SEP) on a plate-solved linear stack → WCS px→sky →
match to the local Gaia XP spectral library → aperture photometry →
enrich with XP spectra integrals.

CLI:
    python -m nas_server.xp_stars probe <fits>     # match/stage report on a real stack
    python -m nas_server.xp_stars --selftest       # synthetic math validation
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import warnings
from pathlib import Path

import numpy as np

logger = logging.getLogger("xp_stars")

# Line windows (nm) — same constants NBExtract's dialog uses
LINE_CENTERS_NM = {"Ha": 656.28, "OIII": 500.7, "SII": 671.64, "Hb": 486.13}
DEFAULT_BW_NM = {"Ha": 7.0, "OIII": 6.5, "SII": 7.0, "Hb": 6.5}

# SSSC bootstrap stage thresholds (mirror sssc.py _STAGE2_MIN/_STAGE3_MIN)
STAGE2_MIN_STARS = 50
STAGE3_MIN_STARS = 200
STAGE3_MIN_BV_SPAN = 1.5

MIN_MATCHED_STARS = 20          # below this, callers should fall back
COND_SEVERE = 100.0             # nbextract.condition_number_warning severe threshold

_SYNTH_MAG_ZP = 25.0            # arbitrary but FIXED zero point for synthetic B/V

# SSSC per-channel system throughput curve sets (EXTNAMEs in SASP_data.fits).
# The curves MUST differ per channel: with identical (e.g. identity) curves the
# expected R/G and B/G integrals are equal for EVERY star, and the SSSC Stage-2
# color model degenerates to gray-world (forces R=G=B per pixel). What the
# curves get wrong (QE shape, band-width error) lands in the solved R(λ) and
# per-channel gains — that is the whole point of SSSC.
# SeeStar S50 = Sony IMX462 OSC behind a UV/IR cut. Its LP filter is the
# internal dual-band (Ha 20nm + OIII 30nm); the closest shipped curve set is
# Sony CMOS Bayer × Optolong L-eNhance (Ha 10nm + OIII/Hb ~24nm — the width
# mismatch is constant over stellar continua, absorbed by the solved gains).
SSSC_CURVES_BROADBAND = ("SONY_COLOR_SENSOR_R-UVIRCUT",
                         "SONY_COLOR_SENSOR_G-UVIRCUT",
                         "SONY_COLOR_SENSOR_B-UVIRCUT")
SSSC_CURVES_LP = ("SONY_CMOS_R-UVIRCUT_/_OPT._L-ENHANCE",
                  "SONY_CMOS_G-UVIRCUT_/_OPT._L-ENHANCE",
                  "SONY_CMOS_B-UVIRCUT_/_OPT._L-ENHANCE")


# ---------------------------------------------------------------------------
# Library / image loading
# ---------------------------------------------------------------------------

def library_status() -> dict:
    """Report whether the local Gaia XP spectral library is usable."""
    try:
        from setiastro.saspro.gaia_database import get_library
        lib = get_library()
        bands = lib.installed_bands()
        return {"installed": bool(bands), "bands": bands}
    except Exception as e:
        return {"installed": False, "bands": [], "error": str(e)}


def load_image(path: str | Path):
    """
    Load a FITS stack → (img (H,W,3) float32 [0,1], header, WCS-or-None).

    WCS is returned only when the header carries a celestial solution
    (post-stack ImageSolver / seqplatesolve). Raw SeeStar headers from
    pre-EQ location-spoofed captures never reach this point — the pipeline
    only calls this on solved stacks.
    """
    from astropy.io import fits
    from astropy.wcs import WCS

    with fits.open(str(path)) as hdul:
        hdr = hdul[0].header.copy()
        data = hdul[0].data.astype(np.float32)

    if data.ndim == 3 and data.shape[0] in (1, 3):
        data = np.moveaxis(data, 0, -1)            # (C,H,W) → (H,W,C)
    if data.ndim == 2:
        data = data[:, :, None]

    lo, hi = float(np.nanmin(data)), float(np.nanmax(data))
    if hi > lo:
        data = (data - lo) / (hi - lo)
    data = np.nan_to_num(data, nan=0.0)

    wcs = None
    try:
        w = WCS(hdr, naxis=2)
        if w.has_celestial:
            wcs = w
    except Exception:
        pass

    return data, hdr, wcs


def load_throughput_curves(extnames, wl_grid_ang: np.ndarray) -> list[np.ndarray]:
    """Load throughput curves from SASP_data.fits, interpolated onto an
    Angstrom wavelength grid (zero outside the tabulated range)."""
    import setiastro
    from astropy.io import fits

    data_path = Path(setiastro.__file__).parent / "data" / "SASP_data.fits"
    out = []
    with fits.open(str(data_path), memmap=False) as hdul:
        for ext in extnames:
            d = hdul[ext].data
            wl = d["WAVELENGTH"].astype(np.float64)
            if wl.max() < 2000.0:           # tabulated in nm → Angstrom
                wl = wl * 10.0
            tp = d["THROUGHPUT"].astype(np.float64)
            out.append(np.interp(wl_grid_ang, wl, tp, left=0.0, right=0.0))
    return out


# ---------------------------------------------------------------------------
# Detection / matching / photometry
# ---------------------------------------------------------------------------

def detect_stars(img: np.ndarray, max_n: int = 300, edge_px: int = 20) -> list[dict]:
    """SEP detection on the luminance, brightest-first, NBExtract-style filters."""
    import sep

    gray = np.ascontiguousarray(np.mean(img, axis=2), dtype=np.float32)
    # Large mosaics with bright nebulosity overflow SEP's default 300k pixel stack.
    sep.set_extract_pixstack(max(300000, gray.size // 5))
    sep.set_sub_object_limit(8192)
    bkg = sep.Background(gray)
    sub = gray - bkg.back()
    sources = None
    # Bright nebulosity can overflow SEP's deblender; we only need the brightest
    # few hundred stars, so raising the threshold on overflow is safe.
    for k in (3.0, 5.0, 8.0, 12.0, 20.0):
        try:
            sources = sep.extract(sub, k * bkg.globalrms, minarea=9)
            break
        except Exception:
            if k == 20.0:
                raise
    assert sources is not None
    if len(sources) == 0:
        return []

    r_fluxrad, _ = sep.flux_radius(
        sub, sources["x"], sources["y"], 2.0 * sources["a"], 0.5,
        normflux=sources["flux"], subpix=5,
    )
    h, w = gray.shape
    keep = (
        (r_fluxrad > 0.2) & (r_fluxrad <= 10)
        & (sources["x"] > edge_px) & (sources["x"] < w - edge_px)
        & (sources["y"] > edge_px) & (sources["y"] < h - edge_px)
    )
    sources = sources[keep]

    order = np.argsort(sources["flux"])[::-1][:max_n]
    return [
        {
            "x": float(sources["x"][i]),
            "y": float(sources["y"][i]),
            "flux": float(sources["flux"][i]),
            "a": float(sources["a"][i]),
        }
        for i in order
    ]


def match_xp(wcs, stars: list[dict], radius_arcsec: float = 10.0) -> list[dict]:
    """
    Match detections to the XP library via WCS. Annotates matched stars with
    gaia_source_id + match_arcsec; one detection per source (nearest wins).
    """
    from setiastro.saspro.gaia_database import get_library

    if not stars:
        return []
    lib = get_library()
    if not lib.installed_bands():
        return []

    xs = np.array([s["x"] for s in stars])
    ys = np.array([s["y"] for s in stars])
    sky = wcs.pixel_to_world(xs, ys)
    coords = list(zip(np.atleast_1d(sky.ra.deg).tolist(),
                      np.atleast_1d(sky.dec.deg).tolist()))

    raw = lib.find_nearest_batch(coords, radius_arcsec=radius_arcsec)

    best_for_source: dict[int, tuple[int, float]] = {}
    for det_i, (sid, sep_as) in raw.items():
        cur = best_for_source.get(sid)
        if cur is None or sep_as < cur[1]:
            best_for_source[sid] = (det_i, sep_as)

    matched = []
    for sid, (det_i, sep_as) in best_for_source.items():
        s = dict(stars[det_i])
        s["gaia_source_id"] = int(sid)
        s["match_arcsec"] = float(sep_as)
        matched.append(s)
    matched.sort(key=lambda s: s["flux"], reverse=True)
    return matched


def measure_stars_rgb(img: np.ndarray, stars: list[dict]) -> list[dict]:
    """
    Aperture photometry per channel on background-subtracted data.
    (The pedestal would bias R/G/B ratios, so each channel gets its own
    SEP background model removed before sum_circle.)
    """
    import sep
    from setiastro.saspro.sfcc import measure_star_rgb_raw_aperture

    if img.shape[2] != 3:
        return []

    sub = np.empty_like(img)
    for c in range(3):
        ch = np.ascontiguousarray(img[:, :, c], dtype=np.float32)
        sub[:, :, c] = ch - sep.Background(ch).back()
    sub = np.ascontiguousarray(sub, dtype=np.float32)

    H, W = img.shape[:2]
    out = []
    for s in stars:
        r = float(np.clip(2.0 * s["a"], 2.0, 10.0))
        phot = measure_star_rgb_raw_aperture(sub, s["x"], s["y"], r)
        if phot is None:
            continue
        Rm, Gm, Bm = (float(phot["R_raw"]), float(phot["G_raw"]),
                      float(phot["B_raw"]))
        if not all(np.isfinite(v) and v > 0 for v in (Rm, Gm, Bm)):
            continue
        # LOCAL annulus correction (2026-07-03, M 42 SSSC red-crush post-mortem):
        # the global SEP background can't follow bright structured nebulosity (Ha)
        # at star scales, so stars embedded in emission carry residual per-channel
        # background — biasing colour ratios and hence the solved gains (M 42:
        # star R/G 0.30 vs SPCC 1.49 = red crushed ~4x). Subtract the residual
        # measured in a local annulus (2.5r..4r) around each star, per channel.
        xi, yi = int(s["x"]), int(s["y"])
        r_in, r_out = int(np.ceil(2.5 * r)), int(np.ceil(4.0 * r))
        if r_out < xi < W - r_out and r_out < yi < H - r_out:
            patch = sub[yi - r_out:yi + r_out + 1, xi - r_out:xi + r_out + 1, :]
            yy, xx = np.mgrid[-r_out:r_out + 1, -r_out:r_out + 1]
            ann = (yy * yy + xx * xx >= r_in * r_in) & (yy * yy + xx * xx <= r_out * r_out)
            if ann.sum() > 20:
                resid = np.median(patch[ann], axis=0)          # per-channel residual bg
                area = np.pi * r * r
                Rm, Gm, Bm = (Rm - float(resid[0]) * area,
                              Gm - float(resid[1]) * area,
                              Bm - float(resid[2]) * area)
                if not all(np.isfinite(v) and v > 0 for v in (Rm, Gm, Bm)):
                    continue                                    # star drowned in nebula — drop
        s = dict(s)
        s.update({"R_meas": Rm, "G_meas": Gm, "B_meas": Bm, "ap_r": r})
        out.append(s)
    return out


def fetch_spectra(stars: list[dict]) -> list[dict]:
    """Attach the XP spectrum to each star: xp_flux (raw, ≥0) + xp_flux_norm
    (unit total flux). Drops stars whose spectrum is missing from the library."""
    from setiastro.saspro.gaia_database import get_library

    lib = get_library()
    out = []
    for s in stars:
        spec = lib.get_spectrum(int(s["gaia_source_id"]))
        if spec is None:
            continue
        wl = np.asarray(spec.wavelengths, dtype=np.float64)
        fl = np.clip(np.asarray(spec.flux, dtype=np.float64), 0.0, None)
        total = float(np.trapezoid(fl, wl))
        if total <= 0:
            continue
        s = dict(s)
        s["xp_wl_nm"] = wl
        s["xp_flux"] = fl
        s["xp_flux_norm"] = fl / total
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_for_nbextract(stars: list[dict], line1: str = "Ha",
                         line2: str = "OIII",
                         bw1_nm: float | None = None,
                         bw2_nm: float | None = None) -> list[dict]:
    """Build NBExtract star_records (input to fit_mixing_matrix)."""
    from setiastro.saspro.nbextract import integrate_sed_over_window

    c1 = LINE_CENTERS_NM[line1]
    c2 = LINE_CENTERS_NM[line2]
    b1 = bw1_nm if bw1_nm is not None else DEFAULT_BW_NM[line1]
    b2 = bw2_nm if bw2_nm is not None else DEFAULT_BW_NM[line2]

    records = []
    for s in stars:
        S1 = integrate_sed_over_window(s["xp_wl_nm"], s["xp_flux_norm"], c1, b1)
        S2 = integrate_sed_over_window(s["xp_wl_nm"], s["xp_flux_norm"], c2, b2)
        if S1 <= 0.0 or S2 <= 0.0:
            continue
        records.append({
            "template": f"GaiaXP_{s['gaia_source_id']}",
            "x": s["x"], "y": s["y"],
            "R_meas": s["R_meas"], "G_meas": s["G_meas"], "B_meas": s["B_meas"],
            "S_line1": float(S1), "S_line2": float(S2),
        })
    return records


def _synthetic_bv(wl_nm: np.ndarray, flux: np.ndarray) -> tuple[float, float]:
    """Synthetic Johnson B/V mags from an XP spectrum (fixed arbitrary ZP —
    only B−V differences/span matter to the SSSC solver, so consistency is
    what counts, not absolute calibration)."""
    from setiastro.saspro.sfcc import _johnson_bvr_passbands_on_gaia_grid

    T_B, T_V, _T_R = _johnson_bvr_passbands_on_gaia_grid(wl_nm)
    fb = float(np.trapezoid(flux * T_B, wl_nm))
    fv = float(np.trapezoid(flux * T_V, wl_nm))
    if fb <= 0 or fv <= 0:
        return float("nan"), float("nan")
    return (_SYNTH_MAG_ZP - 2.5 * np.log10(fb),
            _SYNTH_MAG_ZP - 2.5 * np.log10(fv))


def enrich_for_sssc(stars: list[dict], wl_grid_ang: np.ndarray,
                    T_R: np.ndarray, T_G: np.ndarray,
                    T_B: np.ndarray) -> list[dict]:
    """
    Build SSSC enriched records (input to _solve_system_response).

    T_R/T_G/T_B: per-channel system throughput on wl_grid_ang (Angstrom) —
    see SSSC_CURVES_*. S_star integrals follow the SSSC dialog's recipe
    (∫ flux × T_sys dλ, no QE — that is what gets solved). xp_flux is the
    spectrum interpolated onto wl_grid_ang so the Stage-3 solver can use it
    (it requires len(xp_flux) == len(its wavelength grid)).
    """
    records = []
    for s in stars:
        wl_ang = s["xp_wl_nm"] * 10.0
        fl = np.interp(wl_grid_ang, wl_ang, s["xp_flux"], left=0.0, right=0.0)
        fl = np.where(fl > 0, fl, 0.0)
        S_r = float(np.trapezoid(fl * T_R, wl_grid_ang))
        S_g = float(np.trapezoid(fl * T_G, wl_grid_ang))
        S_b = float(np.trapezoid(fl * T_B, wl_grid_ang))
        if min(S_r, S_g, S_b) <= 0:
            continue
        bmag, vmag = _synthetic_bv(s["xp_wl_nm"], s["xp_flux"])
        records.append({
            "template": f"GaiaXP_{s['gaia_source_id']}",
            "x": s["x"], "y": s["y"],
            "R_meas": s["R_meas"], "G_meas": s["G_meas"], "B_meas": s["B_meas"],
            "S_star_R": S_r, "S_star_G": S_g, "S_star_B": S_b,
            "gaia_B": None if np.isnan(bmag) else float(bmag),
            "gaia_V": None if np.isnan(vmag) else float(vmag),
            "xp_flux": fl,
        })
    return records


def predict_sssc_stage(records: list[dict]) -> dict:
    n = len(records)
    bv = [r["gaia_B"] - r["gaia_V"] for r in records
          if r["gaia_B"] is not None and r["gaia_V"] is not None]
    span = (max(bv) - min(bv)) if bv else 0.0
    if n >= STAGE3_MIN_STARS and span >= STAGE3_MIN_BV_SPAN:
        stage = 3
    elif n >= STAGE2_MIN_STARS:
        stage = 2
    elif n > 0:
        stage = 1
    else:
        stage = 0
    return {"n_stars": n, "bv_span": round(span, 3), "stage": stage}


# ---------------------------------------------------------------------------
# One-call orchestration (what the pipeline wrappers use)
# ---------------------------------------------------------------------------

def gather_calibration_stars(fits_path: str | Path, max_n: int = 300,
                             radius_arcsec: float = 10.0,
                             return_image: bool = False) -> dict:
    """
    Full chain: load → detect → match → photometry → spectra.
    Returns {"ok", "stars", "counts", ...}; ok=False carries a reason the
    caller can use to fall back gracefully.
    return_image=True adds "image" (H,W,3 float32 [0,1]) and "header" so a
    caller that processes the pixels doesn't load the FITS twice.
    """
    t0 = time.time()
    lib = library_status()
    if not lib["installed"]:
        return {"ok": False, "error": "gaia_xp_library_missing", "counts": {}}

    img, hdr, wcs = load_image(fits_path)
    if img.shape[2] != 3:
        return {"ok": False, "error": "not_rgb", "counts": {}}
    if wcs is None:
        return {"ok": False, "error": "no_wcs_plate_solve_required", "counts": {}}

    detected = detect_stars(img, max_n=max_n)
    matched = match_xp(wcs, detected, radius_arcsec=radius_arcsec)
    measured = measure_stars_rgb(img, matched)
    with_spec = fetch_spectra(measured)

    counts = {
        "detected": len(detected),
        "matched": len(matched),
        "measured": len(measured),
        "with_spectra": len(with_spec),
    }
    ok = len(with_spec) >= MIN_MATCHED_STARS
    res = {
        "ok": ok,
        "error": None if ok else f"too_few_stars ({len(with_spec)} < {MIN_MATCHED_STARS})",
        "stars": with_spec,
        "counts": counts,
        "image_shape": list(img.shape),
        "elapsed_s": round(time.time() - t0, 1),
    }
    if return_image:
        res["image"] = img
        res["header"] = hdr
    return res


# ---------------------------------------------------------------------------
# CLI: probe + selftest
# ---------------------------------------------------------------------------

def probe(fits_path: str, max_n: int = 300, radius_arcsec: float = 10.0) -> dict:
    import contextlib
    from setiastro.saspro.nbextract import fit_mixing_matrix as _fmm

    # SASpro's fit_mixing_matrix print()s progress to stdout, which would corrupt
    # the JSON this CLI emits — divert it to stderr.
    def fit_mixing_matrix(records):
        with contextlib.redirect_stdout(sys.stderr):
            return _fmm(records)

    res = gather_calibration_stars(fits_path, max_n=max_n,
                                   radius_arcsec=radius_arcsec)
    report = {
        "fits": str(fits_path),
        "library": library_status()["bands"],
        "counts": res.get("counts", {}),
        "ok": res["ok"],
        "error": res.get("error"),
        "elapsed_s": res.get("elapsed_s"),
    }
    if not res["ok"]:
        return report

    stars = res["stars"]
    nb_records = enrich_for_nbextract(stars)
    A, n_used = fit_mixing_matrix(nb_records)
    report["nbextract"] = {
        "records": len(nb_records),
        "n_used": n_used,
        "A": None if A is None else np.round(A, 5).tolist(),
        "cond": None if A is None else round(float(np.linalg.cond(A)), 1),
        "cond_ok": A is not None and float(np.linalg.cond(A)) < COND_SEVERE,
    }
    if A is not None:
        # measured Ha/OIII flux ratio over the field (column-sum proxy)
        col = np.abs(A).sum(axis=0)
        report["nbextract"]["ha_oiii_response_ratio"] = (
            round(float(col[0] / col[1]), 3) if col[1] > 0 else None)

    from setiastro.saspro.sssc import _WL_GRID
    is_lp = False
    try:
        from astropy.io import fits as _f
        filt = str(_f.getheader(str(fits_path)).get("FILTER", "")).upper()
        is_lp = any(k in filt for k in ("LP", "DUAL", "NARROW", "NB"))
    except Exception:
        pass
    curves = SSSC_CURVES_LP if is_lp else SSSC_CURVES_BROADBAND
    T_R, T_G, T_B = load_throughput_curves(curves, _WL_GRID)
    sssc_records = enrich_for_sssc(stars, _WL_GRID, T_R, T_G, T_B)
    report["sssc"] = predict_sssc_stage(sssc_records)
    report["sssc"]["lp_curves"] = is_lp
    return report


def selftest() -> bool:
    from setiastro.saspro.nbextract import fit_mixing_matrix, extract_channels_nnls

    rng = np.random.default_rng(42)
    failures = []

    # 1 — mixing-matrix recovery from synthetic star records
    A_true = np.array([[0.90, 0.05], [0.25, 0.55], [0.05, 0.80]])
    S1 = rng.uniform(0.5, 5.0, 60)
    S2 = rng.uniform(0.5, 5.0, 60)
    meas = (A_true @ np.vstack([S1, S2])).T * rng.uniform(0.98, 1.02, (60, 3))
    recs = [{"template": f"t{i}", "x": 0, "y": 0,
             "R_meas": meas[i, 0], "G_meas": meas[i, 1], "B_meas": meas[i, 2],
             "S_line1": S1[i], "S_line2": S2[i]} for i in range(60)]
    A_fit, n_used = fit_mixing_matrix(recs)
    if A_fit is None:
        failures.append("fit_mixing_matrix returned None")
    else:
        pred = (A_fit @ np.vstack([S1, S2])).T
        corr = float(np.corrcoef(pred.ravel(), meas.ravel())[0, 1])
        if corr < 0.999:
            failures.append(f"A recovery corr {corr:.4f} < 0.999")
        print(f"  [1] mixing-matrix recovery: corr={corr:.5f} n_used={n_used}")

    # 2 — NNLS channel extraction round trip
    h, w = 64, 64
    ha = rng.uniform(0, 1, (h, w)).astype(np.float32)
    o3 = rng.uniform(0, 1, (h, w)).astype(np.float32)
    rgb = np.stack([A_true[c, 0] * ha + A_true[c, 1] * o3
                    for c in range(3)], axis=-1)
    l1, l2 = extract_channels_nnls(rgb, A_true)
    c1 = float(np.corrcoef(l1.ravel(), ha.ravel())[0, 1])
    c2 = float(np.corrcoef(l2.ravel(), o3.ravel())[0, 1])
    if min(c1, c2) < 0.999:
        failures.append(f"NNLS round trip corr ({c1:.4f}, {c2:.4f}) < 0.999")
    print(f"  [2] NNLS extraction round trip: corr Ha={c1:.5f} OIII={c2:.5f}")

    # 3 — SSSC solver on synthetic stars: blackbodies seen through Bayer-like
    #     T_sys curves × an unknown smooth system response R(λ). The solver
    #     must explain the color-dependent flux deficit (small residual RMS).
    try:
        import contextlib
        from setiastro.saspro.sssc import _solve_system_response, _WL_GRID

        wl = _WL_GRID                            # Angstrom
        wl_nm = wl / 10.0
        n_stars = 250

        def planck(T):
            x = 1.4388e7 / (wl_nm * T)           # hc/kλT with λ in nm
            return 1.0 / (wl_nm ** 5 * np.expm1(np.clip(x, 1e-6, 50)))

        # per-channel system throughput (Bayer-like bumps, Angstrom centers)
        def bump(center, width):
            return np.exp(-0.5 * ((wl - center) / width) ** 2)
        T_R3, T_G3, T_B3 = bump(6000, 600), bump(5300, 500), bump(4600, 450)
        # "true" system response the solver has to discover
        R_true = 0.35 + 0.65 * np.exp(-0.5 * ((wl - 5500) / 2200) ** 2)

        recs3 = []
        temps = rng.uniform(3000, 10000, n_stars)
        for i, T in enumerate(temps):
            fl = planck(T)
            fl = fl / float(np.trapezoid(fl, wl)) * rng.uniform(1, 100)
            bmag, vmag = _synthetic_bv(wl_nm, fl)
            recs3.append({
                "template": f"bb{i}", "x": 0, "y": 0,
                "R_meas": float(np.trapezoid(fl * T_R3 * R_true, wl)),
                "G_meas": float(np.trapezoid(fl * T_G3 * R_true, wl)),
                "B_meas": float(np.trapezoid(fl * T_B3 * R_true, wl)),
                "S_star_R": float(np.trapezoid(fl * T_R3, wl)),
                "S_star_G": float(np.trapezoid(fl * T_G3, wl)),
                "S_star_B": float(np.trapezoid(fl * T_B3, wl)),
                "gaia_B": bmag, "gaia_V": vmag,
                "xp_flux": fl,
            })
        stage_info = predict_sssc_stage(recs3)
        with contextlib.redirect_stdout(sys.stderr):
            sr = _solve_system_response(recs3, wl, T_R3, T_G3, T_B3,
                                        session_id="xp_stars_selftest")
        print(f"  [3] SSSC synthetic solve: predicted_stage={stage_info['stage']} "
              f"bv_span={stage_info['bv_span']} solved_stage={sr.stage} "
              f"rms={sr.residual_rms:.4f}")
        if stage_info["stage"] < 2:
            failures.append(f"synthetic B-V span too small ({stage_info['bv_span']})")
        if not np.isfinite(sr.residual_rms) or sr.residual_rms > 0.05:
            failures.append(f"SSSC residual RMS too high ({sr.residual_rms:.4f})")
    except Exception as e:
        failures.append(f"SSSC synthetic solve raised: {e}")

    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return False
    print("SELFTEST PASSED")
    return True


def main():
    ap = argparse.ArgumentParser(description="Gaia XP star plumbing")
    ap.add_argument("command", nargs="?", choices=["probe"])
    ap.add_argument("fits", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--max-stars", type=int, default=300)
    ap.add_argument("--radius", type=float, default=10.0)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # astropy's logger writes INFO to stdout, which corrupts the probe JSON output
    from astropy import log as astropy_log
    astropy_log.setLevel("ERROR")
    warnings.filterwarnings("ignore")

    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.command == "probe" and args.fits:
        print(json.dumps(probe(args.fits, args.max_stars, args.radius), indent=2))
        return
    ap.print_help()


if __name__ == "__main__":
    main()


def verify_and_repair_wcs(fits_path: str | Path, max_n: int = 120,
                          radius_arcsec: float = 8.0, min_matches: int = 12,
                          dominance: float = 1.8) -> dict:
    """Verify the header WCS matches the DATA orientation via Gaia star-matching;
    repair the header axes in place when a flip hypothesis clearly wins.

    Root cause (2026-07-02, SH2-101 green-cast + mirror-preview post-mortem): Siril
    writes the plate solution in FITS bottom-up row convention while astropy reads
    top-down, so the header's Y axis is inverted vs the data. Consequences: previews
    mirrored (worked around at render), and — far worse — SSSC/XP star matching maps
    detected stars to the WRONG catalog stars. Sparse fields fail closed (M 42: 4
    matches → SPCC fallback), but dense fields (SH2-101, Cygnus) get enough wrong-star
    coincidences to pass the RMS gate → garbage gains (R halved → green nebula).

    Method: SEP-detect the brightest stars, then count Gaia XP matches under 4 axis
    hypotheses (identity / flip-x / flip-y / rot180) applied to the header. If a
    non-identity hypothesis beats identity by `dominance`× with ≥ min_matches, rewrite
    the header (CRPIX + PC/CD row negation) so header == data. Returns
    {ok, applied, hypothesis, matches:{...}} — never raises; no-ops without WCS/library.
    """
    from astropy.io import fits as _f
    from astropy.wcs import WCS as _W
    try:
        img, hdr, wcs = load_image(fits_path)
        if wcs is None:
            return {"ok": True, "applied": False, "reason": "no_wcs"}
        stars = detect_stars(img, max_n=max_n)
        if len(stars) < min_matches:
            return {"ok": True, "applied": False, "reason": f"few_stars ({len(stars)})"}
        ny, nx = img.shape[:2]

        def _flipped_header(fx: bool, fy: bool):
            h = hdr.copy()
            # negate the matrix COLUMN for a flipped pixel axis + move CRPIX
            for a, flip, n in (("1", fx, nx), ("2", fy, ny)):
                if not flip:
                    continue
                h[f"CRPIX{a}"] = (n + 1) - float(h.get(f"CRPIX{a}", 0))
                for r in ("1", "2"):
                    for form in (f"PC{r}_{a}", f"CD{r}_{a}"):
                        if form in h:
                            h[form] = -float(h[form])
            return h

        counts = {}
        for fx in (False, True):
            for fy in (False, True):
                try:
                    w = _W(_flipped_header(fx, fy), naxis=2)
                    counts[(fx, fy)] = len(match_xp(w, stars, radius_arcsec))
                except Exception:
                    counts[(fx, fy)] = -1
        ident = counts[(False, False)]
        best = max(counts, key=counts.get)
        info = {"ok": True, "matches": {f"fx={k[0]},fy={k[1]}": v for k, v in counts.items()},
                "n_stars": len(stars)}
        if best == (False, False) or counts[best] < min_matches \
           or counts[best] < dominance * max(ident, 1):
            info.update({"applied": False, "hypothesis": "identity"})
            return info
        # Repair in place
        newh = _flipped_header(*best)
        with _f.open(str(fits_path), mode="update", memmap=False) as hd:
            for k in newh:
                if k.startswith(("CRPIX", "PC", "CD1", "CD2")):
                    try:
                        hd[0].header[k] = newh[k]
                    except Exception:
                        pass
            hd[0].header["WCSFIXED"] = (f"fx={best[0]},fy={best[1]}",
                                        "axis repair: header now matches data")
            hd.flush()
        info.update({"applied": True, "hypothesis": f"fx={best[0]},fy={best[1]}"})
        return info
    except Exception as e:
        return {"ok": False, "applied": False, "error": str(e)[:200]}

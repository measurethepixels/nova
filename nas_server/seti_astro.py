"""
Wrappers for SetiAstro Suite Pro and GraXpert processing tools.

All functions return:
  {"ok": True, "output_path": ..., "elapsed_s": ...}
  {"ok": False, "error": ..., "elapsed_s": ...}

All functions are non-fatal — callers should log failures and continue.

Tool locations:
  cosmicclarity — seestar-venv/bin/cosmicclarity (CLI subprocess)
  stat_stretch / ghs — setiastro.saspro.imageops (direct Python, no Qt)
  graxpert — ~/tools/graxpert/GraXpert-linux/GraXpert (standalone binary)
"""

import logging
import subprocess
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

from nas_server.config import settings as _bin_settings
COSMICCLARITY_BIN = _bin_settings.get("cosmicclarity_bin", "cosmicclarity")
GRAXPERT_BIN = _bin_settings.get("graxpert_bin", "GraXpert")


def _reconcile_cc_output(output_path: str | Path) -> bool:
    """CosmicClarity forces a .fits extension regardless of the requested name
    (e.g. asked for foo.fit, it writes foo.fits). If the requested path is missing
    but the .fits sibling exists, rename it into place. Returns True if a usable
    file now exists at output_path."""
    out_p = Path(output_path)
    if not out_p.exists():
        alt = out_p.with_suffix(".fits")
        if alt.exists():
            alt.rename(out_p)
    return out_p.exists()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], timeout: int = 1800) -> tuple[int, str, str, int]:
    t0 = time.time()
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout
    )
    elapsed = int(time.time() - t0)
    return result.returncode, result.stdout, result.stderr, elapsed


def _load_fits(path: str | Path) -> tuple[np.ndarray, object]:
    """Load a FITS file, return (float32 array normalised 0-1, header)."""
    from astropy.io import fits as _fits
    with _fits.open(str(path)) as hdul:
        hdr = hdul[0].header.copy()
        data = hdul[0].data.astype(np.float32)
    # Normalise to [0, 1] based on BZERO/BSCALE already applied by astropy.
    # Most Siril stacks are already in [0,1]; clamp to be safe.
    lo, hi = float(np.nanmin(data)), float(np.nanmax(data))
    if hi > lo:
        data = (data - lo) / (hi - lo)
    return data, hdr


def _save_fits(data: np.ndarray, header, path: str | Path) -> None:
    """Save a float32 [0-1] array back to FITS, preserving the original header."""
    from astropy.io import fits as _fits
    hdu = _fits.PrimaryHDU(data.astype(np.float32), header=header)
    hdu.writeto(str(path), overwrite=True)


def _northup_flips(header) -> tuple[bool, bool]:
    """Return (flip_vertical, flip_horizontal) to render a FITS north-up/east-left.

    Reads the FULL CD/PC matrix — NOT the naive CDELT2 sign. Siril/crop headers use
    the PC-matrix form with CDELT=1.0 placeholders, so the old `CDELT2 > 0` check
    read a meaningless placeholder and flipud'ed data that was already north-up,
    producing LEFT-RIGHT MIRRORED previews (Henry's NGC 7000 continent, 2026-07-01;
    both native 2.37"/px and drizzled 1.19"/px are affected — header form, not scale).
    Empirically calibrated (Henry-confirmed NGC 7000 continent, 2026-07-01): Siril
    writes the solution in FITS bottom-up row convention while astropy reads top-down,
    so the sign rule that renders our stacks correctly is flip_v = CD2_2 < 0 and
    flip_h = CD1_1 > 0 (for our PC-form headers PC1_1>0/PC2_2<0 this = rot180, which
    is the confirmed-correct orientation on BOTH native and drizzled stacks).
    No WCS -> no flips (backward-compat)."""
    try:
        cd11 = header.get("CD1_1")
        cd22 = header.get("CD2_2")
        if cd11 is None or cd22 is None:
            pc11, pc22 = header.get("PC1_1"), header.get("PC2_2")
            cdelt1 = header.get("CDELT1", 1.0) or 1.0
            cdelt2 = header.get("CDELT2", 1.0) or 1.0
            if pc11 is not None and pc22 is not None:
                cd11, cd22 = pc11 * cdelt1, pc22 * cdelt2
            elif header.get("CTYPE1"):
                cd11, cd22 = cdelt1, cdelt2      # bare-CDELT WCS
            else:
                return False, False               # no WCS
        if header.get("PLTSOLVD"):
            # ASTAP solution — astropy-consistent (verified: 119/120 identity matches).
            # Pure theory applies: flipud iff Dec increases with row (CD2_2>0);
            # fliplr iff RA increases with col (CD1_1>0, east-right needs mirror).
            return (cd22 > 0), (cd11 > 0)
        # Legacy Siril/PI headers (axis-inverted vs data): empirically calibrated
        # rule (Henry-confirmed NGC 7000, 2026-07-01) — rot180 for this family.
        return (cd22 < 0), (cd11 > 0)
    except Exception:
        return False, False



def generate_preview_nonlinear(fits_path: str | Path, jpg_path: str | Path) -> bool:
    """
    Generate a JPEG preview for already-stretched (non-linear) data.

    Simply clips to [0, 1] and saves — no STF re-stretch. Use this for any
    step that runs after the initial stretch (noise_reduction, curves,
    halo_suppression, combine_stars_screen, final, etc.).

    North-up correction: FITS standard stores row 1 at the bottom of the
    astronomical image. Astropy loads row 1 into data[0], PIL renders data[0]
    at the top — so images with CDELT2 > 0 (north up in FITS convention) come
    out south-up. We flip vertically when CDELT2 > 0 to produce north-up previews.
    No WCS = no flip (backward-compat with pre-plate-solve stacks).
    """
    try:
        from astropy.io import fits as _fits
        from PIL import Image as _Image
        with _fits.open(str(fits_path)) as hdul:
            data = hdul[0].data.astype(np.float32)
            _hdr = hdul[0].header
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        elif data.ndim == 3 and data.shape[2] not in (1, 3):
            data = np.moveaxis(data, 0, -1)
        # North-up/east-left from the FULL CD/PC matrix (see _northup_flips).
        _fv, _fh = _northup_flips(_hdr)
        if _fv:
            data = np.flipud(data)
        if _fh:
            data = np.fliplr(data)
        # Handle ADU-scale data (e.g. linear stacks accidentally passed here)
        hi = float(data.max())
        if hi > 1.5:
            lo = float(data.min())
            data = (data - lo) / max(hi - lo, 1e-9)
        out = (np.clip(data, 0.0, 1.0) * 255).astype(np.uint8)
        if out.ndim == 2:
            _Image.fromarray(out, mode="L").save(str(jpg_path), quality=90)
        else:
            _Image.fromarray(out, mode="RGB").save(str(jpg_path), quality=90)
        return True
    except Exception as e:
        logger.debug(f"[seti_astro] generate_preview_nonlinear failed: {e}")
        return False


def generate_preview_image(img_path: str | Path, jpg_path: str | Path) -> bool:
    """Generate a JPEG preview for an already-display-ready raster (TIFF/PNG/JPEG).

    For Photoshop exports (TIFF) which are already stretched and oriented — no STF,
    no north-up flip. Handles 8/16-bit and flattens alpha; caps width at 1600 px.
    """
    try:
        from PIL import Image as _Image
        img = _Image.open(str(img_path))
        # 16-bit / non-8-bit modes (I;16, I, F) → normalise to 8-bit
        if img.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
            arr = np.asarray(img).astype(np.float32)
            hi, lo = float(arr.max()), float(arr.min())
            arr = (arr - lo) / max(hi - lo, 1e-9)
            img = _Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.width > 1600:
            ratio = 1600 / img.width
            img = img.resize((1600, int(img.height * ratio)), _Image.LANCZOS)
        img.save(str(jpg_path), quality=90)
        return True
    except Exception as e:
        logger.warning(f"[seti_astro] image preview failed for {img_path}: {e}")
        return False


def generate_preview_stf(fits_path: str | Path, jpg_path: str | Path,
                          target_bg: float = 0.30, shadow_clip_k: float = 2.8,
                          scnr: bool = False) -> bool:
    """
    Generate a JPEG preview using PixInsight's ScreenTransferFunction algorithm.

    Applies per-channel (unlinked) STF to linear data so each channel is
    independently shadow-clipped and midtone-stretched. Already-stretched
    data (median > 0.15) is normalised and saved directly without re-stretching.

    target_bg:    desired output background level. 0.30 empirically matches PI's
                  STF with Target=0.25 + Boost background=2.00 on post-GraXpert data.
    shadow_clip_k: shadow clip in sigma units (mad * 1.4826). PI default = 2.8.
                   Lower values clip more shadows; higher values reveal faint detail.
    scnr:         apply average-neutral green suppression after stretch
    """
    try:
        from astropy.io import fits as _fits
        from PIL import Image as _Image

        with _fits.open(str(fits_path)) as hdul:
            data = hdul[0].data.astype(np.float32)
            _hdr = hdul[0].header
        if data.ndim == 3:
            data = np.transpose(data, (1, 2, 0))

        # North-up/east-left from the FULL CD/PC matrix (see _northup_flips).
        _fv, _fh = _northup_flips(_hdr)
        if _fv:
            data = np.flipud(data)
        if _fh:
            data = np.fliplr(data)

        # Normalise ADU-scale images to [0, 1]
        hi = float(data.max())
        if hi > 1.5:
            lo = float(data.min())
            data = (data - lo) / max(hi - lo, 1e-9)

        if float(np.median(data)) > 0.15:
            # Already stretched — just clip and save
            out = np.clip(data, 0.0, 1.0)
        else:
            # Apply PI STF per-channel (unlinked)
            n_ch = data.shape[2] if data.ndim == 3 else 1
            out = np.zeros_like(data)
            for c in range(n_ch):
                ch = data[..., c] if data.ndim == 3 else data
                m = float(np.median(ch))
                mad = float(np.median(np.abs(ch - m))) * 1.4826
                c0 = max(0.0, m - shadow_clip_k * mad)
                denom0 = max(1.0 - c0, 1e-9)
                x = np.clip((ch - c0) / denom0, 0.0, 1.0)
                m_shift = (m - c0) / denom0
                mt_denom = m_shift * (2.0 * target_bg - 1.0) - target_bg
                mt = float(np.clip(m_shift * (target_bg - 1.0) / mt_denom, 1e-6, 1.0 - 1e-6)) \
                    if abs(mt_denom) > 1e-9 and m_shift > 1e-9 else 0.5
                denom_mtf = (2.0 * mt - 1.0) * x - mt
                stretched = np.where(np.abs(denom_mtf) > 1e-9,
                                     (mt - 1.0) * x / denom_mtf, 0.5)
                if data.ndim == 3:
                    out[..., c] = np.clip(stretched, 0.0, 1.0)
                else:
                    out = np.clip(stretched, 0.0, 1.0)

        # Average-neutral SCNR: G = min(G, (R+B)/2)
        if scnr and out.ndim == 3:
            out[:, :, 1] = np.minimum(out[:, :, 1], (out[:, :, 0] + out[:, :, 2]) / 2)

        # Cap at 1600 px wide for browser performance
        img = _Image.fromarray((out * 255).astype(np.uint8))
        if img.width > 1600:
            ratio = 1600 / img.width
            img = img.resize((1600, int(img.height * ratio)), _Image.LANCZOS)
        img.save(str(jpg_path), quality=90)
        return True
    except Exception as e:
        logger.warning(f"[seti_astro] STF preview failed for {fits_path}: {e}")
        return False


def stf_stretch(input_path: str | Path, output_path: str | Path,
                target_bg: float = 0.07, shadow_clip_k: float = 1.25,
                linked: bool = False) -> dict:
    """
    PI-STF stretch saved to FITS.

    Applies PixInsight's ScreenTransferFunction to linear data, targeting output
    background at target_bg. Default 0.07 puts sky near-black for galaxies; use
    0.09-0.10 for nebulae.

    linked=True: single transfer curve computed from the luminance (mean of all
    channels) applied identically to R, G, B — preserves SPCC color calibration.
    linked=False (default): per-channel, which can correct color casts but will
    override a prior SPCC white-balance.
    """
    t0 = time.time()

    def _stf_curve(ch_norm, m_shift, target_bg):
        mt_denom = m_shift * (2.0 * target_bg - 1.0) - target_bg
        mt = float(np.clip(m_shift * (target_bg - 1.0) / mt_denom, 0.001, 0.999)) \
            if abs(mt_denom) > 1e-9 and m_shift > 1e-9 else 0.5
        denom_mtf = (2.0 * mt - 1.0) * ch_norm - mt
        return np.where(np.abs(denom_mtf) > 1e-9, (mt - 1.0) * ch_norm / denom_mtf, 0.5)

    try:
        from astropy.io import fits as _fits
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)

        if float(np.median(data)) > 0.08:
            # Already stretched — normalize and save
            out = np.clip(data, 0.0, 1.0)
        else:
            n_ch = data.shape[2] if data.ndim == 3 else 1
            out = np.zeros_like(data)

            if linked and data.ndim == 3:
                # Compute shadow_clip and midtone from luminance — one curve for all channels
                lum = data.mean(axis=2)
                m = float(np.median(lum))
                mad = float(np.median(np.abs(lum - m))) * 1.4826
                c0 = max(0.0, m - shadow_clip_k * mad)
                denom0 = max(1.0 - c0, 1e-9)
                m_shift = (m - c0) / denom0
                for c in range(n_ch):
                    ch = data[..., c]
                    x = np.clip((ch - c0) / denom0, 0.0, 1.0)
                    out[..., c] = np.clip(_stf_curve(x, m_shift, target_bg), 0.0, 1.0)
            else:
                for c in range(n_ch):
                    ch = data[..., c] if data.ndim == 3 else data
                    m = float(np.median(ch))
                    mad = float(np.median(np.abs(ch - m))) * 1.4826
                    c0 = max(0.0, m - shadow_clip_k * mad)
                    denom0 = max(1.0 - c0, 1e-9)
                    x = np.clip((ch - c0) / denom0, 0.0, 1.0)
                    m_shift = (m - c0) / denom0
                    stretched = np.clip(_stf_curve(x, m_shift, target_bg), 0.0, 1.0)
                    if data.ndim == 3:
                        out[..., c] = stretched
                    else:
                        out = stretched

        if out.ndim == 3:
            out = np.moveaxis(out, -1, 0)  # (H,W,C) → (C,H,W)
        _save_fits(out, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] stf_stretch done in {elapsed}s (target_bg={target_bg}): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] stf_stretch exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def score_frames(light_dir: str | Path, db_path: str | None = None,
                 target: str = "", bottom_pct: float = 0.10,
                 min_stars: int = 20) -> dict:
    """Delegate to stacker.score_and_cull_frames — exists here for ontology fn lookup."""
    from nas_server.stacker import score_and_cull_frames
    return score_and_cull_frames(
        target_name=target,
        light_dir=Path(light_dir),
        db_path=db_path,
        bottom_pct=bottom_pct,
        min_stars=min_stars,
    )


class _MockDoc:
    """Minimal stand-in for SASpro ImageDocument — no Qt required."""
    def __init__(self, image: np.ndarray):
        self.image = image.copy()

    def apply_edit(self, new_image, metadata=None, step_name="edit"):
        self.image = new_image


# ---------------------------------------------------------------------------
# Statistical Stretch  (direct Python — no subprocess, no Qt)
# ---------------------------------------------------------------------------

def stat_stretch(input_path: str | Path, output_path: str | Path,
                 target_median: float = 0.15, linked: bool = True,
                 luma_only: bool = False, blackpoint_sigma: float = 5.0,
                 curves_boost: float = 0.0) -> dict:
    """
    Statistical stretch on a FITS file using SASpro's stretch engine.
    linked=True stretches all RGB channels together (matches PI StatisticalStretch.js).
    curves_boost: mild S-curve applied after stretch (0.05 matches PI default curvesBoost).
    """
    t0 = time.time()
    try:
        from setiastro.saspro.imageops.stretch import stretch_color_image, stretch_mono_image
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)   # (C,H,W) → (H,W,C)
        if data.ndim == 3 and data.shape[2] == 3:
            result = stretch_color_image(data, target_median=target_median,
                                         linked=linked, luma_only=luma_only,
                                         blackpoint_sigma=blackpoint_sigma)
        else:
            if data.ndim == 3:
                data = data[:, :, 0]
            result = stretch_mono_image(data, target_median=target_median,
                                        blackpoint_sigma=blackpoint_sigma)
        # Optional mild S-curve boost (matches PI StatisticalStretch curvesBoost param)
        if curves_boost > 0.0:
            result = np.clip(result, 0.0, 1.0).astype(np.float32)
            b = float(curves_boost)
            result = result + b * result * (1.0 - result)  # symmetric S-push
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)  # (H,W,C) → (C,H,W)
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] stat_stretch done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] stat_stretch exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# GHS Stretch  (direct Python via SASpro ghs_preset — no Qt)
# ---------------------------------------------------------------------------

def ghs_stretch(input_path: str | Path, output_path: str | Path,
                alpha: float = 5.0, beta: float = 0.0, gamma: float = 3.0,
                pivot: float = 0.25, lp: float = 0.0, hp: float = 1.0,
                channel: str = "K") -> dict:
    """
    Generalised Hyperbolic Stretch via SASpro. channel: K (luminance), R, G, B.
    alpha controls strength (higher = more aggressive), gamma controls pivot shape.
    Defaults are a mild stretch suitable for a linear stack.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.ghs_preset import apply_ghs_via_preset
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        doc = _MockDoc(data)
        preset = {"alpha": alpha, "beta": beta, "gamma": gamma,
                  "pivot": pivot, "lp": lp, "hp": hp, "channel": channel}
        apply_ghs_via_preset(None, doc, preset)
        result = doc.image
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] ghs_stretch done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] ghs_stretch exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# ADBE — SASpro Automatic Dynamic Background Extraction  (headless Python)
# ---------------------------------------------------------------------------

def adbe(input_path: str | Path, output_path: str | Path,
         degree: int = 2, num_samples: int = 100,
         use_rbf: bool = True, rbf_smooth: float = 0.1) -> dict:
    """
    SASpro ADBE: polynomial (degree 1–6) + optional RBF refinement background model.

    degree: polynomial degree (1=linear plane, 2=quadratic, 3=cubic …6). Use 2-3
            for typical Seestar gradients; higher for severe light pollution.
    num_samples: auto-placed background sample points (avoid stars/nebulae).
    use_rbf: apply RBF refinement after the polynomial stage (recommended).
    rbf_smooth: RBF smoothness 0.01 (very tight) – 1.0 (very smooth). 0.1 is safe.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.abe import abe_run

        data, hdr = _load_fits(input_path)
        # abe_run expects (H, W, 3) or (H, W) float32 in [0, 1]
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)   # (C, H, W) → (H, W, C)

        result_arr, _ = abe_run(
            data,
            degree=degree,
            num_samples=num_samples,
            use_rbf=use_rbf,
            rbf_smooth=rbf_smooth,
            return_background=True,
            legacy_prestretch=True,
        )
        result_arr = np.clip(result_arr, 0.0, 1.0)
        if result_arr.ndim == 3:
            result_arr = np.moveaxis(result_arr, -1, 0)   # (H, W, C) → (C, H, W)
        _save_fits(result_arr, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] adbe done in {elapsed}s "
                    f"(degree={degree} rbf={use_rbf}): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] adbe exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# XP channel extraction — true Ha/OIII unmixing via Gaia XP spectra (NBExtract)
# ---------------------------------------------------------------------------

def xp_fit_matrix(input_path: str | Path,
                  line1: str = "Ha", line2: str = "OIII",
                  max_stars: int = 300, radius_arcsec: float = 10.0) -> dict:
    """
    Solve the camera's line-mixing matrix A from Gaia XP star spectra on a
    plate-solved linear RGB image (stars must still be PRESENT — the fit is
    star photometry). Returns A without applying it, so the unmixing can later
    run on the STARLESS linear image (xp_apply_matrix).

    ok=False reasons (callers no-op and keep the approximation path):
    gaia_xp_library_missing / not_rgb / no_wcs_plate_solve_required /
    too_few_stars / fit_failed / ill_conditioned.
    """
    import contextlib
    import io

    t0 = time.time()
    try:
        from nas_server import xp_stars
        from setiastro.saspro.nbextract import fit_mixing_matrix

        res = xp_stars.gather_calibration_stars(
            input_path, max_n=max_stars, radius_arcsec=radius_arcsec)
        if not res["ok"]:
            return {"ok": False, "error": res["error"],
                    "counts": res.get("counts", {}),
                    "elapsed_s": int(time.time() - t0)}

        records = xp_stars.enrich_for_nbextract(res["stars"], line1=line1, line2=line2)

        # SASpro prints fit progress to stdout; capture it into the log instead.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            A, n_used = fit_mixing_matrix(records)
        for ln in buf.getvalue().splitlines():
            logger.debug(f"[seti_astro] {ln}")
        if A is None:
            return {"ok": False, "error": "fit_failed",
                    "counts": res["counts"], "elapsed_s": int(time.time() - t0)}
        cond = float(np.linalg.cond(A))
        if cond >= xp_stars.COND_SEVERE:
            return {"ok": False, "error": f"ill_conditioned (cond={cond:.0f})",
                    "counts": res["counts"], "elapsed_s": int(time.time() - t0)}

        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] xp_fit_matrix done in {elapsed}s "
                    f"(stars={len(records)} used={n_used} cond={cond:.1f})")
        return {
            "ok": True,
            "A": np.round(A, 5).tolist(),
            "cond": round(cond, 1),
            "n_stars": len(records),
            "n_used": n_used,
            "counts": res["counts"],
            "line1": line1, "line2": line2,
            "elapsed_s": elapsed,
        }
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] xp_fit_matrix exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def xp_apply_matrix(input_path: str | Path, output_dir: str | Path, A,
                    line1: str = "Ha", line2: str = "OIII") -> dict:
    """
    NNLS-unmix a linear RGB image into two emission-line mono channels using a
    previously fitted mixing matrix A (xp_fit_matrix). Intended for the
    STARLESS linear image so the channels feed nb_palette star-free. Writes
    xp_{line}.fit into output_dir, preserving the input header and the
    channels' relative flux scale.
    """
    import contextlib
    import io

    t0 = time.time()
    try:
        from nas_server import xp_stars
        from setiastro.saspro.nbextract import extract_channels_nnls

        img, hdr, _wcs = xp_stars.load_image(input_path)
        if img.ndim != 3 or img.shape[2] != 3:
            return {"ok": False, "error": "not_rgb",
                    "elapsed_s": int(time.time() - t0)}

        A = np.asarray(A, dtype=np.float64)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chan1, chan2 = extract_channels_nnls(img, A)
        chan1 = np.clip(chan1, 0.0, None).astype(np.float32)
        chan2 = np.clip(chan2, 0.0, None).astype(np.float32)

        # Measured line-flux ratio (>1 = line1/Ha dominant): mean background-
        # subtracted signal over the brightest-10% pixels. On a starless image
        # this reflects nebula, not stars.
        lum = chan1 + chan2
        mask = lum >= np.percentile(lum, 90)
        s1 = float((chan1 - np.median(chan1))[mask].mean())
        s2 = float((chan2 - np.median(chan2))[mask].mean())
        if s2 <= 1e-6:
            flux_ratio = 3.0 if s1 > 1e-6 else 1.0
        else:
            flux_ratio = round(max(0.0, s1 / s2), 3)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path1 = out_dir / f"xp_{line1.lower()}.fit"
        path2 = out_dir / f"xp_{line2.lower()}.fit"
        _save_fits(chan1, hdr, path1)
        _save_fits(chan2, hdr, path2)

        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] xp_apply_matrix done in {elapsed}s "
                    f"({line1}/{line2} flux_ratio={flux_ratio}): {path1}, {path2}")
        return {
            "ok": True,
            "line1_path": str(path1),
            "line2_path": str(path2),
            "flux_ratio": flux_ratio,
            "elapsed_s": elapsed,
        }
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] xp_apply_matrix exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def xp_extract_channels(input_path: str | Path, output_dir: str | Path,
                        line1: str = "Ha", line2: str = "OIII",
                        max_stars: int = 300, radius_arcsec: float = 10.0) -> dict:
    """Fit A and unmix the SAME image in one go (CLI / offline use). The
    pipeline instead fits on the star-full image (xp_fit_matrix after
    background_extraction) and applies to the starless one (xp_apply_matrix
    at the stretch boundary)."""
    fit = xp_fit_matrix(input_path, line1=line1, line2=line2,
                        max_stars=max_stars, radius_arcsec=radius_arcsec)
    if not fit["ok"]:
        return fit
    applied = xp_apply_matrix(input_path, output_dir, fit["A"],
                              line1=line1, line2=line2)
    if not applied["ok"]:
        applied["counts"] = fit.get("counts", {})
        return applied
    merged = {**fit, **applied}
    merged["elapsed_s"] = fit["elapsed_s"] + applied["elapsed_s"]
    return merged


def nb_palette(input_path: str | Path, output_path: str | Path,
               palette: str = "hoo", s_mix: float = 0.8,
               stretch_mode: str = "linked",
               highlight_percentile: float = 99.5,
               highlight_target: float = 0.80,
               sky_percentile: float = 10.0,
               sky_floor: float = 0.01) -> dict:
    """
    Narrowband palette composite from the xp_channel_extract Ha/OIII channels.

    input_path is the current post-stretch STARLESS working image — it supplies
    the header (WCS) and the expected dimensions; its pixels are REPLACED by the
    composite. The linear xp_ha.fit / xp_oiii.fit are located near input_path
    (run dir), jointly normalised, sky-anchored, stretched, then composited
    (hoo / sho / foraxx — see nas_server/nb_palette.py).

    Sky handling (the palette sky MUST be black — Henry, 2026-06-12): each
    channel carries its OWN background pedestal (NNLS dumps the B-channel sky
    offset into OIII), so any shared blackpoint leaves a COLORED sky. We anchor
    per channel: low-percentile sky → 0 before the stretch, and re-anchor to
    sky_floor after it. The midtone is solved from the signal high-percentile
    (→ highlight_target), NOT the sky median — anchoring the median targets the
    sky, which is exactly the gray/navy-sky + under-stretched-nebula failure.

    stretch_mode:
      "linked"      — one transfer (anchored on the stronger channel's signal)
                      applied identically to both. Preserves the TRUE line
                      ratio: a near-empty OIII channel stays dark.
      "per_channel" — classic false-color NB look: each channel stretched to
                      its own signal anchor. Surfaces faint OIII aggressively;
                      only sane on OIII-strong fields.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.imageops.stretch import numba_mono_from_img
        from nas_server import nb_palette as nbp

        ha_p, o3_p = nbp.find_xp_channels(input_path)
        if ha_p is None:
            return {"ok": False, "error": "xp_channels_missing",
                    "elapsed_s": int(time.time() - t0)}

        ref, hdr = _load_fits(input_path)
        if ref.ndim == 3 and ref.shape[0] in (1, 3):
            ref = np.moveaxis(ref, 0, -1)
        ref_hw = ref.shape[:2]

        # Load RAW (no per-file min-max — that would destroy the channels'
        # shared NNLS flux scale) and normalise JOINTLY so Ha vs OIII keep
        # their true relative strength.
        def _mono_raw(p):
            from astropy.io import fits as _fits
            with _fits.open(str(p)) as hdul:
                d = hdul[0].data.astype(np.float32)
            if d.ndim == 3:
                d = d[0] if d.shape[0] in (1, 3) else d[:, :, 0]
            return d

        ha, oiii = _mono_raw(ha_p), _mono_raw(o3_p)
        if ha.shape != ref_hw or oiii.shape != ref_hw:
            return {"ok": False,
                    "error": f"shape_mismatch xp={ha.shape} working={ref_hw}",
                    "elapsed_s": int(time.time() - t0)}
        joint_max = float(max(ha.max(), oiii.max()))
        if joint_max <= 0:
            return {"ok": False, "error": "empty_channels",
                    "elapsed_s": int(time.time() - t0)}
        ha = np.clip(ha / joint_max, 0.0, 1.0)
        oiii = np.clip(oiii / joint_max, 0.0, 1.0)

        def _sub_sky(c: np.ndarray) -> np.ndarray:
            bp = float(np.percentile(c, float(sky_percentile)))
            return np.clip((c - bp) / max(1.0 - bp, 1e-12), 0.0, 1.0)

        def _signal_anchor(c: np.ndarray) -> float:
            return max(float(np.percentile(c, float(highlight_percentile))), 1e-6)

        def _mtf(c: np.ndarray, anchor: float) -> np.ndarray:
            return np.clip(numba_mono_from_img(c, 0.0, 1.0, float(anchor),
                                               float(highlight_target)), 0.0, 1.0)

        def _anchor_black(c: np.ndarray) -> np.ndarray:
            # Guarantee the FINAL sky lands at sky_floor: residual pedestal
            # (sky_percentile residual × midtone gain) can still tint the sky.
            sky = float(np.percentile(c, float(sky_percentile)))
            if sky <= float(sky_floor):
                return c
            off = sky - float(sky_floor)
            return np.clip((c - off) / max(1.0 - off, 1e-12), 0.0, 1.0)

        ha, oiii = _sub_sky(ha), _sub_sky(oiii)
        if stretch_mode == "per_channel":
            ha_s = _mtf(ha, _signal_anchor(ha))
            o3_s = _mtf(oiii, _signal_anchor(oiii))
        else:
            # Linked: one transfer anchored on the stronger channel's signal,
            # applied to both — preserves the true line ratio (a near-empty
            # OIII stays dark instead of having its noise boosted to "signal").
            anchor = max(_signal_anchor(ha), _signal_anchor(oiii))
            ha_s, o3_s = _mtf(ha, anchor), _mtf(oiii, anchor)
        ha_s, o3_s = _anchor_black(ha_s), _anchor_black(o3_s)
        rgb = nbp.compose(ha_s, o3_s, palette=palette, s_mix=s_mix)

        _save_fits(np.moveaxis(rgb, -1, 0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] nb_palette({palette}, {stretch_mode}) "
                    f"done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path),
                "palette": palette, "stretch_mode": stretch_mode,
                "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] nb_palette exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# SSSC — spectrophotometric color calibration via solved system response
# ---------------------------------------------------------------------------

SSSC_CAMERA_LABEL = "SeeStar S50"


def _signal_gr_ratio(path) -> float | None:
    """Median G/R over signal pixels (above sky+5σ) — SSSC sanity-gate metric."""
    try:
        d, _ = _load_fits(path)
        if d.ndim == 3 and d.shape[0] in (1, 3):
            d = np.moveaxis(d, 0, -1)
        if d.ndim != 3:
            return None
        L = d.mean(-1)
        corner = np.concatenate([L[:60, :60].ravel(), L[-60:, -60:].ravel()])
        m = L > (np.median(corner) + 5 * np.std(corner))
        if m.sum() < 500:
            return None
        med = np.median(d[m], axis=0)
        return float(med[1] / max(med[0], 1e-6))
    except Exception:
        return None



def sssc_calibrate(input_path: str | Path, output_path: str | Path,
                   lp: bool | None = None, max_stars: int = 500,
                   radius_arcsec: float = 10.0, n_ctrl: int = 8) -> dict:
    """
    SASpro SSSC color calibration on a plate-solved linear RGB stack: solves
    the system response R(λ) from Gaia XP star photometry (Riello et al. 2021
    formulation), then applies the per-channel color correction. Unlike SPCC
    this needs no sensor/filter transmission data — only per-channel Bayer
    curve SHAPES (xp_stars.SSSC_CURVES_*); the rest is solved. This is the
    only real color calibration available for LP dual-band data.

    lp=None auto-detects the dual-band filter from the FITS FILTER header;
    the pipeline passes it explicitly. Solutions persist per session_id in
    gaia_xp_cache.sqlite next to the XP library — later runs with the same
    config seed the Stage-3 optimizer from the prior solution (self-improving).
    """
    import contextlib
    import io

    t0 = time.time()
    try:
        from nas_server import xp_stars
        from setiastro.saspro import sssc as _sssc
        from setiastro.saspro.gaia_database import get_library_dir

        res = xp_stars.gather_calibration_stars(
            input_path, max_n=max_stars, radius_arcsec=radius_arcsec,
            return_image=True)
        if not res["ok"]:
            return {"ok": False, "error": res["error"],
                    "counts": res.get("counts", {}),
                    "elapsed_s": int(time.time() - t0)}

        img, hdr = res["image"], res["header"]
        if lp is None:
            filt = str(hdr.get("FILTER", "")).upper()
            lp = any(k in filt for k in ("LP", "DUAL", "NARROW", "NB"))
        curves = xp_stars.SSSC_CURVES_LP if lp else xp_stars.SSSC_CURVES_BROADBAND
        wl_grid = _sssc._WL_GRID
        T_R, T_G, T_B = xp_stars.load_throughput_curves(curves, wl_grid)

        enriched = xp_stars.enrich_for_sssc(res["stars"], wl_grid, T_R, T_G, T_B)
        if len(enriched) < xp_stars.MIN_MATCHED_STARS:
            return {"ok": False,
                    "error": f"too_few_enriched ({len(enriched)} < "
                             f"{xp_stars.MIN_MATCHED_STARS})",
                    "counts": res["counts"],
                    "elapsed_s": int(time.time() - t0)}

        session_id = _sssc.make_session_id(
            curves[0], curves[1], curves[2], "(None)", "(None)",
            camera_label=SSSC_CAMERA_LABEL)

        cache = prior = None
        try:
            cache = _sssc.SessionResponseCache(
                str(get_library_dir() / "gaia_xp_cache.sqlite"))
            prior = cache.load_latest(session_id)
        except Exception as e:
            logger.warning(f"[seti_astro] sssc session cache unavailable: {e}")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sr = _sssc._solve_system_response(
                enriched, wl_grid, T_R, T_G, T_B, session_id,
                prior_response=prior, n_ctrl=n_ctrl,
                status_cb=lambda m: logger.info(f"[seti_astro] {m}"))
            calibrated = _sssc.apply_sssc_correction(
                img, sr, enriched, wl_grid, T_R, T_G, T_B,
                status_cb=lambda m: logger.info(f"[seti_astro] {m}"))
        for ln in buf.getvalue().splitlines():
            logger.debug(f"[seti_astro] {ln}")

        if not np.isfinite(sr.residual_rms):
            return {"ok": False, "error": "solve_degenerate (rms not finite)",
                    "counts": res["counts"],
                    "elapsed_s": int(time.time() - t0)}

        # The pixel correction applies Stage 2 quadratic gains (Stage 3 only
        # refines R(lambda) for diagnostics/seeding), so quality-gate on the
        # Stage 2 sigma-clipped RMS. 2.0 mirrors SASpro's own prior-trust
        # threshold; the reported residual_rms is the unclipped all-star value
        # and is outlier-dominated on nebula-rich fields.
        applied_rms = float(sr.stage_rms.get(2, sr.residual_rms)) \
            if sr.stage >= 2 else float(sr.residual_rms)
        if applied_rms >= 2.0:
            return {"ok": False,
                    "error": f"rms_too_high (applied-stage rms {applied_rms:.3f} >= 2.0)",
                    "stage": sr.stage,
                    "stage_rms": {int(k): round(float(v), 4)
                                  for k, v in sr.stage_rms.items()},
                    "counts": res["counts"],
                    "elapsed_s": int(time.time() - t0)}

        if cache is not None:
            try:
                cache.save(sr)
            finally:
                cache.close()

        _save_fits(np.moveaxis(calibrated, -1, 0), hdr, output_path)
        try:
            (Path(output_path).parent / ".sssc_applied").touch()
        except Exception:
            pass
        bv_span = round(float(sr.bv_range[1] - sr.bv_range[0]), 3)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] sssc_calibrate done in {elapsed}s "
                    f"(stage={sr.stage} stars={sr.n_stars} bv_span={bv_span} "
                    f"rms={sr.residual_rms:.4f} lp={lp})")
        return {
            "ok": True,
            "output_path": str(output_path),
            "stage": sr.stage,
            "n_stars": sr.n_stars,
            "bv_span": bv_span,
            "residual_rms": round(float(sr.residual_rms), 4),
            "stage_rms": {int(k): round(float(v), 4)
                          for k, v in sr.stage_rms.items()},
            "applied_rms": round(applied_rms, 4),
            "gains": [round(float(g), 4) for g in sr.gains[:3]],
            "session_id": session_id,
            "lp": bool(lp),
            "curves": list(curves),
            "prior_seeded": prior is not None,
            "counts": res["counts"],
            "elapsed_s": elapsed,
        }
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] sssc_calibrate exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Background Extraction via GraXpert  (standalone binary subprocess)
# ---------------------------------------------------------------------------

def background_extract(input_path: str | Path, output_path: str | Path,
                       correction: str = "Subtraction", smoothing: float = 0.5,
                       gpu: bool = True) -> dict:
    """
    AI gradient/background removal using the GraXpert standalone binary.
    correction: 'Subtraction' (additive gradients) or 'Division' (multiplicative).
    smoothing: 0.0 (aggressive) – 1.0 (gentle), default 0.5.
    """
    t0 = time.time()
    try:
        out = Path(output_path)
        inp = Path(input_path)
        # GraXpert 3.0.2: -output flag causes silent no-output; use default naming instead
        args = [
            GRAXPERT_BIN, str(inp),
            "-cli",
            "-cmd", "background-extraction",
            "-correction", correction,
            "-smoothing", str(smoothing),
            "-gpu", "true" if gpu else "false",
        ]
        rc, stdout, stderr, elapsed = _run(args, timeout=1800)
        if rc != 0:
            logger.warning(f"[seti_astro] graxpert failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        # GraXpert writes {stem}_GraXpert.fits next to the input
        graxpert_out = inp.parent / (inp.stem + "_GraXpert.fits")
        if graxpert_out.exists():
            import shutil
            shutil.move(str(graxpert_out), str(out))
        elif not out.exists():
            return {"ok": False, "error": "GraXpert produced no output file", "elapsed_s": elapsed}
        logger.info(f"[seti_astro] background_extract done in {elapsed}s: {out}")
        return {"ok": True, "output_path": str(out), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] background_extract exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def _run(args: list[str], timeout: int = 1800) -> tuple[int, str, str, int]:
    t0 = time.time()
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout
    )
    elapsed = int(time.time() - t0)
    return result.returncode, result.stdout, result.stderr, elapsed


# ---------------------------------------------------------------------------
# Frame Quality Scorer  (headless equivalent of SASpro Blink Comparator)
# Uses SEP (Source Extractor Python) + OpenCV — no Qt required.
# ---------------------------------------------------------------------------

def _score_one_frame(file_path: Path) -> dict | None:
    """
    Compute quality metrics for a single FITS frame.
    Returns dict with fwhm, eccentricity, background, star_count, score — or None on error.
    Mirrors the logic in SASpro BlinkComparatorPro._compute_one().
    """
    try:
        import cv2
        import sep
        from astropy.io import fits as _fits

        with _fits.open(str(file_path)) as hdul:
            raw = hdul[0].data.astype(np.float32)

        # Collapse colour axes to mono for metric computation
        if raw.ndim == 3:
            if raw.shape[0] == 3:
                raw = 0.2126 * raw[0] + 0.7152 * raw[1] + 0.0722 * raw[2]
            else:
                raw = raw[0]

        # 2× downsample for speed (same as SASpro)
        h, w = raw.shape
        small = cv2.resize(raw, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        data = np.ascontiguousarray(small, dtype=np.float32)

        # SEP background subtraction
        bkg = sep.Background(data)
        back_level = float(bkg.globalback)
        data_sub = data - bkg

        # Source extraction
        objects = sep.extract(data_sub, thresh=3.0, err=bkg.globalrms)

        if len(objects) == 0:
            return {"file": file_path.name, "fwhm": None, "eccentricity": None,
                    "background": back_level, "star_count": 0, "score": 0.0}

        # FWHM from half-light radius (a/b axes)
        a, b = objects["a"], objects["b"]
        fwhm_vals = 2.355 * np.sqrt((a ** 2 + b ** 2) / 2)
        fwhm = float(np.median(fwhm_vals))

        # Eccentricity: 0 = perfect circle, 1 = line
        ecc = float(np.median(np.sqrt(1 - (b / np.maximum(a, 1e-6)) ** 2)))

        star_count = len(objects)

        # Weighted score (lower fwhm + lower ecc + more stars = better)
        # Normalised so ~1.0 is a good frame; used for ranking, not absolute quality
        score = float(star_count / max(fwhm * (1 + ecc), 1e-6))

        return {"file": file_path.name, "fwhm": round(fwhm, 3),
                "eccentricity": round(ecc, 3), "background": round(back_level, 1),
                "star_count": star_count, "score": round(score, 3)}
    except Exception as e:
        logger.warning(f"[seti_astro] score_frames: error on {file_path.name}: {e}")
        return None


def score_frames(folder: str | Path, workers: int = 4,
                 bottom_pct: float = 0.10) -> dict:
    """
    Score all FITS frames in a folder using SEP quality metrics (FWHM, eccentricity,
    background, star count). Mirrors SASpro Blink Comparator metrics.

    Returns:
      {"ok": True, "frames": [...sorted best-first...],
       "flagged": [...bottom bottom_pct% by score...],
       "total": N, "elapsed_s": T}

    Frames already in an _exclude subfolder are reported as pre-excluded.
    """
    t0 = time.time()
    try:
        from concurrent.futures import ThreadPoolExecutor
        folder = Path(folder)
        exclude_dir = folder / "_exclude"
        pre_excluded = {f.name for f in exclude_dir.glob("*")} if exclude_dir.exists() else set()

        fits_files = sorted(folder.glob("*.fit")) + sorted(folder.glob("*.fits"))
        fits_files = [f for f in fits_files if f.name not in pre_excluded]

        if not fits_files:
            return {"ok": False, "error": f"No FITS frames found in {folder}", "elapsed_s": 0}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_score_one_frame, fits_files))

        scored = [r for r in results if r is not None]
        scored.sort(key=lambda r: r["score"], reverse=True)

        cutoff = max(1, int(len(scored) * bottom_pct))
        flagged = [r["file"] for r in scored[-cutoff:]]

        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] score_frames: {len(scored)}/{len(fits_files)} frames scored "
                    f"in {elapsed}s, {len(flagged)} flagged as bottom {bottom_pct*100:.0f}%")
        return {
            "ok": True,
            "frames": scored,
            "flagged": flagged,
            "pre_excluded": list(pre_excluded),
            "total": len(fits_files),
            "elapsed_s": elapsed,
        }
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] score_frames exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Veralux HyperMetric Stretch  (pure Python/numpy — no subprocess, no Qt)
# Extracted from the Veralux Siril script by Riccardo Paterniti (GPL-3.0)
# ---------------------------------------------------------------------------

def _veralux_stretch_channel(data: np.ndarray, D: float, SP: float = 0.0,
                              b: float = 0.001) -> np.ndarray:
    """Apply HyperMetric arcsinh stretch to a single channel [0,1]."""
    a1 = np.arcsinh(D * (data - SP) + b)
    a2 = np.arcsinh(b)
    norm = np.arcsinh(D * (1.0 - SP) + b) - a2
    return np.clip((a1 - a2) / norm, 0.0, 1.0)


def _veralux_sample_background(data: np.ndarray) -> float:
    """
    Estimate sky background from four 64×64 corner regions — median of the two
    middle values (same method as JS sampleBackground() and _sample_fits_background()).
    Works on (H,W) or (H,W,C); uses the luminance channel for colour images.
    """
    if data.ndim == 3:
        lum = (0.2126 * data[:, :, 0] + 0.7152 * data[:, :, 1] + 0.0722 * data[:, :, 2])
    else:
        lum = data
    h, w = lum.shape
    sz = max(16, min(64, min(h, w) // 8))
    corners = [
        float(np.median(lum[:sz, :sz])),
        float(np.median(lum[:sz, w - sz:])),
        float(np.median(lum[h - sz:, :sz])),
        float(np.median(lum[h - sz:, w - sz:])),
    ]
    corners.sort()
    return (corners[1] + corners[2]) / 2.0


def veralux_stretch(input_path: str | Path, output_path: str | Path,
                    log_d: float = 4.0, sp: float = 0.0,
                    color_grip: float = 0.5, shadow_convergence: float = 0.5,
                    target_bg: float = 0.08) -> dict:
    """
    Veralux HyperMetric Stretch — arcsinh-based with colour preservation.

    sp=0.0 (default) → auto-detect background from image corners and use it as the
    symmetry point.  This is CRITICAL: with SP = background_level the formula maps
    sky → 0 and all signal is relative to the true zero point.  Without it, even a
    modest background of 0.002 maps to ~0.30–0.54, producing the characteristic
    flat-grey sky this function was giving before this fix.

    log_d (0–7): stretch intensity after background removal.  With auto-SP, log_d=4.0
    is a balanced default: faint halo at 3× background → ~0.45, bright stars → ~0.80.
    sp (float): explicit symmetry point.  Only used if > 0; set 0.0 for auto-detect.
    color_grip (0–1): blends vector-normalised (1.0) vs scalar (0.0) RGB stretch.
        1.0 = best colour preservation at star edges.
        0.0 = each channel stretched independently (can shift hue).
    shadow_convergence (0–1): dampens chromatic noise in deep shadow areas.
    target_bg: after stretching, shift the output so the background sits at this
        level (default 0.08 ≈ near-black).  Set 0.0 to skip the shift.
    """
    t0 = time.time()
    try:
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)   # (C,H,W) → (H,W,C)

        # --- Auto-detect background as symmetry point ---
        if sp <= 0.0:
            sp = _veralux_sample_background(data)
            logger.info(f"[seti_astro] veralux: auto-detected background SP={sp:.4f}")
        else:
            logger.info(f"[seti_astro] veralux: using explicit SP={sp:.4f}")

        D = 10 ** log_d
        b = 0.001  # softening factor

        if data.ndim == 2:
            result = _veralux_stretch_channel(data, D, sp, b)
        else:
            # Colour — blend scalar and vector stretch via color_grip
            scalar = np.stack([_veralux_stretch_channel(data[:, :, c], D, sp, b)
                               for c in range(3)], axis=2)
            lum = (0.2126 * data[:, :, 0] + 0.7152 * data[:, :, 1]
                   + 0.0722 * data[:, :, 2])
            stretched_lum = _veralux_stretch_channel(lum, D, sp, b)
            ratio = np.where(lum > 1e-6, stretched_lum / lum, 0.0)[:, :, np.newaxis]
            vector = np.clip(data * ratio, 0.0, 1.0)
            # Shadow convergence: dampen colour in deep shadows (relative to SP)
            shadow_ref = max(sp + 0.02, 0.02)
            shadow_mask = np.clip((lum - sp) / shadow_ref, 0.0, 1.0)[:, :, np.newaxis]
            shadow_mask = shadow_mask ** (1.0 + shadow_convergence * 3.0)
            result = color_grip * vector + (1.0 - color_grip) * scalar
            result = result * shadow_mask + scalar * (1.0 - shadow_mask)
            result = np.clip(result, 0.0, 1.0)

        # --- Shift output so background sits at target_bg ---
        if target_bg > 0.0:
            # The background (which was at SP pre-stretch) now maps to ~0 after stretch.
            # Sample it again in the stretched image and shift up to target_bg.
            stretched_bg = _veralux_sample_background(result if result.ndim == 2
                                                      else np.moveaxis(result, -1, 0)
                                                      if result.ndim == 3 and result.shape[0] == 3
                                                      else result)
            shift = target_bg - stretched_bg
            if abs(shift) > 0.005:
                result = np.clip(result + shift, 0.0, 1.0)
                logger.info(f"[seti_astro] veralux: bg shift {stretched_bg:.4f} → {target_bg:.4f} (Δ{shift:+.4f})")

        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] veralux_stretch done in {elapsed}s (log_d={log_d} sp={sp:.4f}): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "sp_used": sp, "log_d": log_d}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] veralux_stretch exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Cosmic Clarity CLI wrappers
# ---------------------------------------------------------------------------
# smart_stretch — adaptive multi-tool stretch with verify-and-correct loop
# ---------------------------------------------------------------------------

def _profile_linear_image(data: np.ndarray) -> dict:
    """
    Profile a linear FITS array to drive stretch strategy selection.
    data must be (H,W) or (H,W,C) float32 in [0,1].

    Returns:
      bg            — sky background level (4-corner median of luminance)
      sky_sigma     — noise in background (MAD*1.4826 of corner region)
      dynamic_range — (p99_signal - bg) / sky_sigma  [signal-to-noise ratio at 99th pct]
      p95, p99      — 95th and 99th percentile of entire image
      peak          — max pixel value (luminance)
      core_fraction — fraction of pixels above 40% of peak (compact bright core indicator)
      star_frac     — fraction of pixels > bg + 5*sky_sigma (rough star density)
    """
    if data.ndim == 3:
        lum = (0.2126 * data[:, :, 0] + 0.7152 * data[:, :, 1] + 0.0722 * data[:, :, 2])
    else:
        lum = data
    h, w = lum.shape
    sz = max(16, min(64, min(h, w) // 8))
    # Background from four corners
    corners = np.concatenate([
        lum[:sz, :sz].ravel(), lum[:sz, w-sz:].ravel(),
        lum[h-sz:, :sz].ravel(), lum[h-sz:, w-sz:].ravel()
    ])
    bg = float(np.median(corners))
    sky_sigma = float(np.median(np.abs(corners - bg))) * 1.4826

    flat = lum.ravel()
    p95  = float(np.percentile(flat, 95))
    p99  = float(np.percentile(flat, 99))
    p999 = float(np.percentile(flat, 99.9))
    peak = float(flat.max())
    core_fraction = float((lum > 0.40 * peak).sum()) / lum.size
    star_frac     = float((lum > bg + 5.0 * sky_sigma).sum()) / lum.size

    # Dynamic range: use p99.9 for compact targets (tiny globular core, planetary nebula)
    # where p99 mostly captures faint halo stars, not the true signal peak.
    # core_fraction < 0.0002 → target occupies < 0.02% of pixels → use p99.9 or peak.
    if core_fraction < 0.0002:
        bright_end = p999
    elif core_fraction < 0.005:
        bright_end = max(p99, p999 * 0.8)
    else:
        bright_end = p99
    dr = (bright_end - bg) / max(sky_sigma, 1e-9)

    return {
        "bg": bg, "sky_sigma": sky_sigma, "dynamic_range": dr,
        "p95": p95, "p99": p99, "p999": p999, "peak": peak,
        "core_fraction": core_fraction, "star_frac": star_frac,
        "bright_end_used": bright_end,
    }


def _verify_stretch_output(data: np.ndarray, target_bg: float = 0.08) -> dict:
    """
    Measure quality metrics on a stretched image.
    data must be (H,W) or (H,W,C) float32 in [0,1].

    Returns:
      bg          — actual sky background (4-corner median)
      midtone     — median of pixels in (bg+0.05 .. 0.95) — the "subject" zone
      clip_pct    — fraction of pixels ≥ 0.999 (highlight clipping)
      shadow_pct  — fraction of pixels ≤ 0.002 (shadow depth)
      bg_ok       — bg within ±0.025 of target
      midtone_ok  — midtone in (0.25, 0.60)
      clip_ok     — clip_pct < 0.002
    """
    if data.ndim == 3:
        lum = (0.2126 * data[:, :, 0] + 0.7152 * data[:, :, 1] + 0.0722 * data[:, :, 2])
    else:
        lum = data
    h, w = lum.shape
    sz = max(16, min(64, min(h, w) // 8))
    corners = np.concatenate([
        lum[:sz, :sz].ravel(), lum[:sz, w-sz:].ravel(),
        lum[h-sz:, :sz].ravel(), lum[h-sz:, w-sz:].ravel()
    ])
    bg = float(np.median(corners))
    flat = lum.ravel()
    subject = flat[(flat > bg + 0.05) & (flat < 0.95)]
    midtone   = float(np.median(subject)) if len(subject) > 100 else 0.5
    clip_pct  = float((flat >= 0.999).sum()) / flat.size
    shadow_pct = float((flat <= 0.002).sum()) / flat.size
    return {
        "bg": bg, "midtone": midtone, "clip_pct": clip_pct, "shadow_pct": shadow_pct,
        "bg_ok":      abs(bg - target_bg) <= 0.025,
        "midtone_ok": 0.25 <= midtone <= 0.60,
        "clip_ok":    clip_pct < 0.002,
    }


def smart_stretch(input_path: str | Path, output_path: str | Path,
                  target_bg: float = 0.08,
                  max_iterations: int = 3,
                  allow_ghs_boost: bool = True,
                  allow_stat_bg: bool = True) -> dict:
    """
    Adaptive multi-tool stretch with verify-and-correct loop.

    Strategy selection based on image profile:
      DR < 150  (faint/extended target) → STF linked (most reliable bg placement)
      DR 150–1500 (typical galaxy/cluster) → veralux arcsinh (soft highlights)
      DR > 1500 (bright compact core)    → GHS with auto-pivot (independent shoulder control)
      star_frac > 0.08 (star-rich field)  → stat_stretch (excludes stars from bg estimate)

    Correction passes (in order, up to max_iterations):
      1. Background off → shift output up/down to hit target_bg
      2. Midtone too low (< 0.25) → GHS midtone boost (pivot=bg+0.05, alpha=4, beta=-0.1)
      3. Midtone too high (> 0.60) → GHS compress (alpha=2, beta=0.2)
      4. bg still wrong after correction → re-stretch with STF fallback

    Returns full diagnostics: strategy chosen, profile, metrics after each pass,
    corrections applied, and final pass/fail for each quality metric.
    """
    t0 = time.time()
    diag = {
        "strategy": None, "profile": None, "iterations": [],
        "corrections": [], "final_metrics": None, "ok": False,
    }

    try:
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)   # (C,H,W) → (H,W,C)

        # ── Phase 1: Profile ─────────────────────────────────────────────────
        profile = _profile_linear_image(data)
        diag["profile"] = profile
        dr   = profile["dynamic_range"]
        bg   = profile["bg"]
        sf   = profile["star_frac"]
        logger.info(f"[smart_stretch] profile: bg={bg:.4f} DR={dr:.0f} "
                    f"core_frac={profile['core_fraction']:.4f} star_frac={sf:.4f}")

        # ── Phase 2: Choose primary stretch ──────────────────────────────────
        if allow_stat_bg and sf > 0.08:
            strategy = "stat"
            logger.info(f"[smart_stretch] → stat_stretch (star-rich field, star_frac={sf:.3f})")
        elif dr > 1500:
            strategy = "ghs"
            logger.info(f"[smart_stretch] → ghs_stretch (high DR={dr:.0f})")
        elif dr > 150:
            strategy = "veralux"
            logger.info(f"[smart_stretch] → veralux_stretch (medium DR={dr:.0f})")
        else:
            strategy = "stf"
            logger.info(f"[smart_stretch] → stf_stretch (low DR={dr:.0f})")
        diag["strategy"] = strategy

        # ── Phase 3: Apply primary stretch in memory ──────────────────────────
        b = 0.001  # veralux softening factor

        def _apply_stf(arr, t_bg=target_bg, clip_k=1.25):
            """STF midtone transfer applied in-memory."""
            def _stf_curve(ch_norm, m_shift, t_bg):
                mt_denom = m_shift * (2.0 * t_bg - 1.0) - t_bg
                mt = float(np.clip(m_shift * (t_bg - 1.0) / mt_denom, 0.001, 0.999)) \
                    if abs(mt_denom) > 1e-9 and m_shift > 1e-9 else 0.5
                denom_mtf = (2.0 * mt - 1.0) * ch_norm - mt
                return np.where(np.abs(denom_mtf) > 1e-9, (mt - 1.0) * ch_norm / denom_mtf, 0.5)
            n_ch = arr.shape[2] if arr.ndim == 3 else 1
            # Linked: single curve from luminance
            lum_ = arr.mean(axis=2) if arr.ndim == 3 else arr
            m_ = float(np.median(lum_))
            mad_ = float(np.median(np.abs(lum_ - m_))) * 1.4826
            c0_ = max(0.0, m_ - clip_k * mad_)
            denom0 = max(1.0 - c0_, 1e-9)
            m_shift_ = (m_ - c0_) / denom0
            out_ = np.zeros_like(arr)
            for c_ in range(n_ch):
                ch_ = arr[..., c_] if arr.ndim == 3 else arr
                x_ = np.clip((ch_ - c0_) / denom0, 0.0, 1.0)
                s_ = np.clip(_stf_curve(x_, m_shift_, t_bg), 0.0, 1.0)
                if arr.ndim == 3:
                    out_[..., c_] = s_
                else:
                    out_ = s_
            return out_

        def _apply_veralux(arr, ld=None):
            sp_ = _veralux_sample_background(arr)
            ld_ = ld if ld is not None else (4.0 if dr <= 500 else 3.5)
            D_  = 10 ** ld_
            if arr.ndim == 2:
                return np.clip(_veralux_stretch_channel(arr, D_, sp_, b), 0.0, 1.0)
            scalar_ = np.stack([_veralux_stretch_channel(arr[:,:,c], D_, sp_, b)
                                 for c in range(3)], axis=2)
            lum_ = 0.2126*arr[:,:,0] + 0.7152*arr[:,:,1] + 0.0722*arr[:,:,2]
            sl_  = _veralux_stretch_channel(lum_, D_, sp_, b)
            ratio_ = np.where(lum_ > 1e-6, sl_ / lum_, 0.0)[:,:,np.newaxis]
            vector_ = np.clip(arr * ratio_, 0.0, 1.0)
            shadow_ref_ = max(sp_ + 0.02, 0.02)
            smask_ = np.clip((lum_ - sp_) / shadow_ref_, 0.0, 1.0)[:,:,np.newaxis] ** 2.2
            result_ = np.clip(vector_ * smask_ + scalar_ * (1.0 - smask_), 0.0, 1.0)
            return result_

        def _apply_ghs(arr, alpha=5.0, beta=0.0, gamma=3.0, pivot=None):
            pivot_ = pivot if pivot is not None else max(bg, 0.001)
            try:
                from setiastro.saspro.ghs_preset import apply_ghs_via_preset
                doc_ = _MockDoc(arr)
                apply_ghs_via_preset(None, doc_, {
                    "alpha": alpha, "beta": beta, "gamma": gamma,
                    "pivot": pivot_, "lp": 0.0, "hp": 1.0, "channel": "K"
                })
                return np.clip(doc_.image, 0.0, 1.0)
            except Exception as _ge:
                logger.warning(f"[smart_stretch] GHS failed ({_ge}), falling back to STF")
                return _apply_stf(arr)

        def _apply_stat(arr):
            try:
                from setiastro.saspro.imageops.stretch import stretch_color_image, stretch_mono_image
                if arr.ndim == 3 and arr.shape[2] == 3:
                    r_ = stretch_color_image(arr, target_median=target_bg + 0.08,
                                             linked=True, blackpoint_sigma=5.0)
                else:
                    a_ = arr[:,:,0] if arr.ndim == 3 else arr
                    r_ = stretch_mono_image(a_, target_median=target_bg + 0.08,
                                            blackpoint_sigma=5.0)
                return np.clip(r_, 0.0, 1.0)
            except Exception as _se:
                logger.warning(f"[smart_stretch] stat failed ({_se}), falling back to STF")
                return _apply_stf(arr)

        if strategy == "stf":
            result = _apply_stf(data)
        elif strategy == "veralux":
            result = _apply_veralux(data)
        elif strategy == "ghs":
            result = _apply_ghs(data, alpha=7.0, beta=-0.15, gamma=3.5, pivot=bg)
        else:  # stat
            result = _apply_stat(data)

        # ── Phase 4: Verify + correct loop ───────────────────────────────────
        # Compact target (globular core, planetary nebula etc.) = core_fraction < 0.005.
        # For compact targets, midtone is meaningless (cluster is <1% of frame pixels),
        # so we skip midtone correction and only fix background and clipping.
        compact_target = profile["core_fraction"] < 0.005

        for iteration in range(max_iterations):
            metrics = _verify_stretch_output(result, target_bg)
            diag["iterations"].append({"pass": iteration, "metrics": metrics})
            logger.info(f"[smart_stretch] iter {iteration}: bg={metrics['bg']:.3f} "
                        f"midtone={metrics['midtone']:.3f} clip={metrics['clip_pct']:.4f} "
                        f"ok={metrics['bg_ok']}/{metrics['midtone_ok']}/{metrics['clip_ok']} "
                        f"compact={compact_target}")

            bg_ok      = metrics["bg_ok"]
            midtone_ok = metrics["midtone_ok"] or compact_target   # skip for compact
            clip_ok    = metrics["clip_ok"]
            if bg_ok and midtone_ok and clip_ok:
                break

            corrections_this_iter = []

            # Correction A: Clipping — re-stretch softer FIRST (before other corrections)
            if not clip_ok and iteration == 0 and strategy != "veralux":
                logger.info(f"[smart_stretch] clip ({metrics['clip_pct']:.4f}) → re-stretch veralux")
                result = _apply_veralux(data, ld=3.5)
                corrections_this_iter.append("rstretch_veralux_softer")
                diag["corrections"].extend(corrections_this_iter)
                continue   # Re-measure from scratch after re-stretch

            # Correction B: Background wrong → additive shift
            # Do this BEFORE GHS so GHS pivot uses the updated background level
            if not bg_ok:
                shift = target_bg - metrics["bg"]
                if abs(shift) > 0.003:
                    result = np.clip(result + shift, 0.0, 1.0)
                    corrections_this_iter.append(f"bg_shift({shift:+.3f})")
                    logger.info(f"[smart_stretch] bg shift {metrics['bg']:.3f}→{target_bg:.3f}")

            # Correction C: Midtone too low (extended target only) → GHS boost
            # Pivot is target_bg + 0.05 (post-correction background, not stale metrics["bg"])
            current_bg_estimate = target_bg if not bg_ok else metrics["bg"]
            if (not midtone_ok and metrics["midtone"] < 0.25
                    and allow_ghs_boost and not compact_target):
                boost_pivot = max(current_bg_estimate + 0.05, 0.02)
                result = _apply_ghs(result, alpha=2.5, beta=-0.05, gamma=2.0,
                                    pivot=boost_pivot)
                corrections_this_iter.append(f"ghs_boost(pivot={boost_pivot:.3f})")
                logger.info(f"[smart_stretch] GHS midtone boost pivot={boost_pivot:.3f} "
                            f"(midtone was {metrics['midtone']:.3f})")

            # Correction D: Midtone too high (extended target only) → GHS compress
            elif (not midtone_ok and metrics["midtone"] > 0.60
                  and allow_ghs_boost and not compact_target):
                compress_pivot = max(current_bg_estimate + 0.08, 0.03)
                result = _apply_ghs(result, alpha=1.5, beta=0.15, gamma=1.5,
                                    pivot=compress_pivot)
                corrections_this_iter.append(f"ghs_compress(pivot={compress_pivot:.3f})")
                logger.info(f"[smart_stretch] GHS compress pivot={compress_pivot:.3f}")

            if corrections_this_iter:
                diag["corrections"].extend(corrections_this_iter)
            else:
                break   # Nothing changed — stop

        # ── Phase 5: Final background fine-tune (single additive shift only) ─
        final_metrics = _verify_stretch_output(result, target_bg)
        diag["final_metrics"] = final_metrics
        if abs(final_metrics["bg"] - target_bg) > 0.003:
            result = np.clip(result + (target_bg - final_metrics["bg"]), 0.0, 1.0)
            diag["corrections"].append("final_bg_fine_tune")
            # Re-measure after fine-tune
            final_metrics = _verify_stretch_output(result, target_bg)
            diag["final_metrics"] = final_metrics

        # ── Save ──────────────────────────────────────────────────────────────
        out = np.moveaxis(result, -1, 0) if result.ndim == 3 else result
        _save_fits(out, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[smart_stretch] done in {elapsed}s — strategy={strategy} "
                    f"corrections={diag['corrections']} "
                    f"final: bg={final_metrics['bg']:.3f} "
                    f"midtone={final_metrics['midtone']:.3f} "
                    f"clip={final_metrics['clip_pct']:.4f}")
        diag.update({"ok": True, "output_path": str(output_path),
                     "elapsed_s": elapsed, "strategy": strategy})
        return diag

    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[smart_stretch] exception: {e}", exc_info=True)
        diag.update({"ok": False, "error": str(e), "elapsed_s": elapsed})
        return diag


# ---------------------------------------------------------------------------

def denoise(input_path: str | Path, output_path: str | Path,
            denoise_luma: float = 0.9, denoise_color: float = 0.7,
            gpu: bool = True) -> dict:
    """Apply AI denoise to an image. Works on FITS, TIFF, PNG, JPEG."""
    try:
        args = [
            COSMICCLARITY_BIN, "denoise",
            "-i", str(input_path),
            "-o", str(output_path),
            f"--denoise-luma={denoise_luma}",
            f"--denoise-color={denoise_color}",
        ]
        if not gpu:
            args.append("--no-gpu")
        rc, stdout, stderr, elapsed = _run(args)
        if rc != 0:
            logger.warning(f"[seti_astro] denoise failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        if not _reconcile_cc_output(output_path):
            logger.warning(f"[seti_astro] denoise produced no file at {output_path}")
            return {"ok": False, "error": "denoise produced no output file", "elapsed_s": elapsed}
        logger.info(f"[seti_astro] denoise done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] denoise exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": 0}


def sharpen(input_path: str | Path, output_path: str | Path,
            stellar_amount: float = 0.8, nonstellar_amount: float = 0.4,
            mode: str = "Both", gpu: bool = True) -> dict:
    """Apply AI sharpening. mode: Both | Stellar Only | Non-Stellar Only"""
    try:
        args = [
            COSMICCLARITY_BIN, "sharpen",
            "-i", str(input_path),
            "-o", str(output_path),
            f"--sharpening-mode={mode}",
            f"--stellar-amount={stellar_amount}",
            f"--nonstellar-amount={nonstellar_amount}",
        ]
        if not gpu:
            args.append("--no-gpu")
        rc, stdout, stderr, elapsed = _run(args)
        if rc != 0:
            logger.warning(f"[seti_astro] sharpen failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        if not _reconcile_cc_output(output_path):
            logger.warning(f"[seti_astro] sharpen produced no file at {output_path}")
            return {"ok": False, "error": "sharpen produced no output file", "elapsed_s": elapsed}
        logger.info(f"[seti_astro] sharpen done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] sharpen exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": 0}


def denoise_and_sharpen(input_path: str | Path, output_path: str | Path,
                        denoise_luma: float = 0.9, denoise_color: float = 0.7,
                        stellar_amount: float = 0.8, nonstellar_amount: float = 0.4,
                        gpu: bool = True) -> dict:
    """Sharpen then denoise in a single pass (cosmicclarity 'both' mode)."""
    try:
        args = [
            COSMICCLARITY_BIN, "both",
            "-i", str(input_path),
            "-o", str(output_path),
            f"--denoise-luma={denoise_luma}",
            f"--denoise-color={denoise_color}",
            f"--stellar-amount={stellar_amount}",
            f"--nonstellar-amount={nonstellar_amount}",
        ]
        if not gpu:
            args.append("--no-gpu")
        rc, stdout, stderr, elapsed = _run(args)
        if rc != 0:
            logger.warning(f"[seti_astro] both failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        if not _reconcile_cc_output(output_path):
            logger.warning(f"[seti_astro] both produced no file at {output_path}")
            return {"ok": False, "error": "both produced no output file", "elapsed_s": elapsed}
        logger.info(f"[seti_astro] both done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] both exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": 0}


def remove_satellites(input_path: str | Path, output_path: str | Path,
                      sensitivity: float = 0.5, skip_if_none: bool = True,
                      gpu: bool = True) -> dict:
    """Remove satellite trails. skip_if_none=True avoids saving if no trail detected."""
    try:
        args = [
            COSMICCLARITY_BIN, "satellite",
            "-i", str(input_path),
            "-o", str(output_path),
            f"--sensitivity={sensitivity}",
        ]
        if skip_if_none:
            args.append("--skip-save")
        if not gpu:
            args.append("--no-gpu")
        rc, stdout, stderr, elapsed = _run(args)
        if rc != 0:
            logger.warning(f"[seti_astro] satellite failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        skipped = "skip-save" in str(args) and not Path(output_path).exists()
        logger.info(f"[seti_astro] satellite done in {elapsed}s (trail_found={not skipped})")
        return {"ok": True, "output_path": str(output_path) if not skipped else None,
                "trail_found": not skipped, "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] satellite exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": 0}


def remove_stars(input_path: str | Path, output_path: str | Path,
                 extraction_mode: str = "unscreen", gpu: bool = True) -> dict:
    """Remove stars (DarkStar). extraction_mode: unscreen | additive"""
    try:
        args = [
            COSMICCLARITY_BIN, "darkstar",
            "-i", str(input_path),
            "-o", str(output_path),
            f"--star-removal-mode={extraction_mode}",
        ]
        if not gpu:
            args.append("--no-gpu")
        rc, stdout, stderr, elapsed = _run(args)
        if rc != 0:
            logger.warning(f"[seti_astro] darkstar failed (rc={rc}): {stderr[:200]}")
            return {"ok": False, "error": stderr[:300], "elapsed_s": elapsed}
        logger.info(f"[seti_astro] darkstar done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] darkstar exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": 0}


# ---------------------------------------------------------------------------
# SCNR — Selective Colour Noise Reduction (green cast removal)
# ---------------------------------------------------------------------------

def scnr(input_path: str | Path, output_path: str | Path,
         amount: float = 0.9, mode: str = "avg",
         preserve_lightness: bool = True) -> dict:
    """
    Remove green channel cast (teal artifact from Seestar alt-az OSC tracking).
    mode: 'avg' (average mask), 'max' (max mask), 'min' (min mask).
    Works post-stretch on nonlinear data.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.remove_green import _apply_scnr_rgb
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        if data.ndim != 3 or data.shape[2] != 3:
            raise ValueError("SCNR requires a 3-channel RGB image")
        result = _apply_scnr_rgb(data, amount=amount, mode=mode,
                                 preserve_lightness=preserve_lightness)
        result = np.moveaxis(np.clip(result, 0.0, 1.0), -1, 0)
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] scnr done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] scnr exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Background Neutralize — post-stretch colour pedestal removal
# ---------------------------------------------------------------------------

def background_neutralize(input_path: str | Path, output_path: str | Path,
                           mode: str = "pivot1") -> dict:
    """
    Post-stretch background neutralisation. Samples an auto-detected dark sky
    patch and shifts channel offsets so the background is colour-neutral.
    mode: 'pivot1' (shift), 'pivot2' (scale), 'pivot3' (subtract).
    """
    t0 = time.time()
    try:
        from setiastro.saspro.backgroundneutral import (
            background_neutralize_rgb, auto_rect_50x50)
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        if data.ndim != 3 or data.shape[2] != 3:
            raise ValueError("Background neutralize requires a 3-channel RGB image")
        rect = auto_rect_50x50(data)
        result = background_neutralize_rgb(data, rect_xywh=rect, mode=mode)
        # Completeness pass (#9): drive the DARK-CORNER sky to neutral grey. The primary
        # pass above samples a single auto-detected 50x50 patch, which on frame-filling
        # targets (IC 1805 — the Heart fills the centre) can land on an already-neutral
        # spot while the dark corners still carry the cast (IC 1805 corner sky B/R 1.25
        # survived the patch neutralize; the patch read ~neutral). Measure the four dark
        # corners — the SAME region image_analyzer gates background_neutralize on — and
        # subtract the per-channel offset that drives each corner median onto the darkest
        # channel's. A uniform per-channel shift neutralises the sky to grey without
        # altering structure above it. No-op when the corners are already balanced, so
        # other paths are unaffected. Dual-band NBN data, where OIII spans G+B, is the
        # classic cast source (NGC 2244 NBN sky B/R 1.46).
        try:
            _h, _w = result.shape[0], result.shape[1]
            _m = max(_h // 20, _w // 20, 50)
            def _corner_med(_c: int) -> float:
                ch = result[:, :, _c]
                return float(np.median(np.concatenate([
                    ch[:_m, :_m].ravel(), ch[:_m, -_m:].ravel(),
                    ch[-_m:, :_m].ravel(), ch[-_m:, -_m:].ravel()])))
            _med = [_corner_med(_c) for _c in range(3)]
            _ref = min(_med)
            _imbal = (max(_med) - _ref) / max(_ref, 1e-5)
            if _imbal > 0.05:
                for _c in range(3):
                    result[:, :, _c] = result[:, :, _c] - (_med[_c] - _ref)
                logger.info(f"[seti_astro] background_neutralize corner cast "
                            f"{_imbal:.2f} corrected (corner med "
                            f"{[round(m, 4) for m in _med]} → {_ref:.4f})")
        except Exception as _nce:
            logger.debug(f"[seti_astro] background_neutralize completeness pass skipped: {_nce}")
        result = np.moveaxis(np.clip(result, 0.0, 1.0), -1, 0)
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] background_neutralize done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] background_neutralize exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# CLAHE — Contrast Limited Adaptive Histogram Equalisation
# ---------------------------------------------------------------------------

def clahe(input_path: str | Path, output_path: str | Path,
          clip_limit: float = 2.0, tile_size: int = 8) -> dict:
    """
    Local contrast enhancement via CLAHE. Works on both linear and stretched data.
    clip_limit: higher = more aggressive (risk of noise amplification).
    tile_size: grid tile in pixels — smaller = finer local adaptation.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.clahe import apply_clahe
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        result = apply_clahe(data, clip_limit=clip_limit,
                             tile_grid_size=(tile_size, tile_size))
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] clahe done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] clahe exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Halo Suppression — HaloBGon (SASpro port of SASv2)
# ---------------------------------------------------------------------------

def halo_suppress(input_path: str | Path, output_path: str | Path,
                  reduction_level: int = 1, is_linear: bool = False) -> dict:
    """
    Reduce colour halos and bloat around bright stars.
    reduction_level: 0-3 (0=minimal, 3=aggressive).
    is_linear: set True if applying to linear (unstretched) data.

    NOTE: upstream compute_halo_b_gon() builds its lightness mask by dividing an
    already-[0,1] image by 255, which collapses the local star-halo mask to ~0.
    The operation then degenerates into a *global* gamma darkening (gamma 1.5 at
    level 1 → median ~0.17 collapses to ~0.07), crushing the whole final image.
    In the SASpro GUI this is hidden because the user applies a star mask so the
    effect only lands on star halos; headless there is no mask. We replicate that
    intended behaviour by generating a bright-star-halo mask and blending the
    halo-reduced result in *only* around stars, leaving the galaxy/background tone
    untouched.
    """
    t0 = time.time()
    try:
        import cv2
        from setiastro.saspro.halobgon import compute_halo_b_gon
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        img = np.clip(data.astype(np.float32), 0.0, 1.0)

        result = compute_halo_b_gon(img, reduction_level=reduction_level,
                                    is_linear=is_linear)

        # ── Confine the effect to bright-star halos ──────────────────────────
        # Build a luminance map, threshold the brightest cores, then dilate via a
        # Gaussian to cover the surrounding halo annulus. Higher reduction levels
        # widen the annulus. The galaxy/background (mask≈0) keeps its original tone.
        lum = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
        thr = float(np.percentile(lum, 99.5))
        bright = (lum > thr).astype(np.float32)
        sigma = 6.0 + 3.0 * float(max(0, min(3, int(reduction_level))))
        halo_mask = cv2.GaussianBlur(bright, (0, 0), sigmaX=sigma)
        _mx = float(halo_mask.max())
        if _mx > 1e-6:
            halo_mask = np.power(np.clip(halo_mask / _mx, 0.0, 1.0), 0.6)
        if img.ndim == 3:
            halo_mask = halo_mask[:, :, None]
        result = np.clip(img * (1.0 - halo_mask) + result * halo_mask, 0.0, 1.0)

        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] halo_suppress (star-masked) done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] halo_suppress exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Curves — parametric tone curve via SASpro LUT engine
# ---------------------------------------------------------------------------

def curves(input_path: str | Path, output_path: str | Path,
           shape: str = "s_med", amount: float = 0.5,
           channel: str = "all") -> dict:
    """
    Apply a parametric tone curve using SASpro's LUT engine.

    shape: 'linear'|'s_mild'|'s_med'|'s_strong'|'lift_shadows'|'crush_shadows'|
           'fade_blacks'|'rolloff_highlights'
    amount: 0.0-1.0 curve strength.
    channel: 'all' (RGB linked) | 'L' (luminance only) | 'R'|'G'|'B' (single).
    """
    t0 = time.time()
    try:
        from setiastro.saspro.curves_preset import (
            _shape_points_norm, _points_norm_to_scene,
            _interpolator_from_scene_points, build_curve_lut)
        import scipy.interpolate  # noqa — ensure available

        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)

        pts_norm = _shape_points_norm(shape, amount)
        pts_scene = _points_norm_to_scene(pts_norm)
        interp = _interpolator_from_scene_points(pts_scene)
        lut = build_curve_lut(interp, size=65536)  # lut[i] = output for input i/65535

        def _apply_lut(arr: np.ndarray) -> np.ndarray:
            idx = np.clip((arr * 65535).astype(np.int32), 0, 65535)
            return lut[idx].astype(np.float32)

        ch = channel.lower()
        if data.ndim == 2 or ch == "all":
            result = _apply_lut(data)
        elif ch == "l" and data.ndim == 3:
            import cv2
            lab = cv2.cvtColor((np.clip(data, 0, 1) * 255).astype(np.uint8),
                               cv2.COLOR_RGB2LAB).astype(np.float32)
            lab[:, :, 0] = _apply_lut(lab[:, :, 0] / 100.0) * 100.0
            result = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB) / 255.0
        else:
            cmap = {"r": 0, "g": 1, "b": 2}
            result = data.copy()
            if ch in cmap:
                result[:, :, cmap[ch]] = _apply_lut(data[:, :, cmap[ch]])
            else:
                result = _apply_lut(data)

        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] curves ({shape} x{amount:.2f} ch={channel}) "
                    f"done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] curves exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# HDR Compression — SASpro WaveScale HDR
# ---------------------------------------------------------------------------

def hdr_compression(input_path: str | Path, output_path: str | Path,
                    n_scales: int = 5, compression_factor: float = 1.5,
                    mask_gamma: float = 1.0) -> dict:
    """
    Wavelet-based HDR compression. Compresses highlights while preserving
    shadow and midtone detail. Best applied after stretch.

    n_scales: number of wavelet scales (3-8). More = affects broader structures.
    compression_factor: >1 compresses highlights, <1 boosts them.
    mask_gamma: luminance mask curve. >1 = protect more highlights; 1 = neutral.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.wavescale_hdr import compute_wavescale_hdr
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        result, _ = compute_wavescale_hdr(data, n_scales=n_scales,
                                          compression_factor=compression_factor,
                                          mask_gamma=mask_gamma)
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] hdr_compression done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] hdr_compression exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def hdr_core_blend(input_path: str | Path, output_path: str | Path,
                   threshold: float = 0.72, n_scales: int = 5,
                   compression_factor: float = 1.5, mask_gamma: float = 1.0,
                   core_sigma: float = 6.0, feather: float = 20.0,
                   **_kwargs) -> dict:
    """
    HDR compression applied ONLY under a feathered bright-core luminance mask.

    Global wavelet HDR tames a blown core but pulls contrast out of everything
    else — faint outer signal dims, so a whole-frame before/after comparison
    reads "no improvement" even when the core genuinely recovered detail
    (M 42 / M 31 critiques). This computes the same compute_wavescale_hdr layer
    as hdr_compression, then blends it in only where the smoothed luminance
    exceeds `threshold` (feathered), leaving the rest of the frame untouched.
    Typical mask coverage is 0.3–2.7% of the frame.

    threshold: core mask onset on smoothed luminance (0.72 nebula / 0.80 galaxy).
    core_sigma: luminance pre-smooth so single stars don't trigger the mask.
    feather: gaussian feather of the mask edge.
    Self-gating: passes through unchanged when no region exceeds the threshold.
    """
    t0 = time.time()
    try:
        from scipy import ndimage as _ndi  # noqa: PLC0415
        from setiastro.saspro.wavescale_hdr import compute_wavescale_hdr
        data, hdr = _load_fits(input_path)
        data = np.nan_to_num(data.astype(np.float32))
        if data.ndim == 3 and data.shape[0] == 3:
            L = 0.2126 * data[0] + 0.7152 * data[1] + 0.0722 * data[2]
        else:
            L = data if data.ndim == 2 else data[0]
        Ls = _ndi.gaussian_filter(L, core_sigma)
        denom = max(0.97 - threshold, 1e-6)
        M = np.clip((Ls - threshold) / denom, 0.0, 1.0)
        M = _ndi.gaussian_filter(M, feather).astype(np.float32)
        core_cov = float((M > 0.5).mean())
        if float(M.max()) < 0.05:
            logger.info(f"[seti_astro] hdr_core_blend: no core above threshold "
                        f"{threshold} — passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path),
                    "skipped": "no-core", "threshold": threshold,
                    "elapsed_s": int(time.time() - t0)}

        hwc = np.moveaxis(data, 0, -1) if data.ndim == 3 else data
        hdr_layer, _ = compute_wavescale_hdr(hwc, n_scales=n_scales,
                                             compression_factor=compression_factor,
                                             mask_gamma=mask_gamma)
        if hdr_layer.ndim == 3:
            hdr_layer = np.moveaxis(hdr_layer, -1, 0)
        hdr_layer = np.clip(np.nan_to_num(hdr_layer.astype(np.float32)), 0.0, 1.0)
        if data.ndim == 3:
            # Chroma-preserving: apply the HDR as a LUMINANCE ratio so the core
            # compresses and recovers structure but RGB ratios are untouched. A direct
            # per-channel blend hue-shifts the very center (M 31 bulge went blue-grey).
            Lh = (0.2126 * hdr_layer[0] + 0.7152 * hdr_layer[1]
                  + 0.0722 * hdr_layer[2])
            ratio = np.clip(Lh / np.maximum(L, 1e-5), 0.0, 1.5)
            Lnew = (L * (1.0 - M + M * ratio)).astype(np.float32)
            # Near-clipped pixels carry MEANINGLESS hue — the white clip washes colour
            # well before full saturation (M 31's bulge is near-neutral out to L≈0.8,
            # so the dimmed core read grey-blue). Refill chroma from the surrounding
            # WELL-EXPOSED region via wide normalized convolution: hue is trusted
            # below L≈0.78 and progressively replaced above it, fill sampled only
            # from trusted pixels (σ wide enough to reach past the washed zone).
            Wraw = np.clip((L - 0.78) / 0.14, 0.0, 1.0)
            Wraw = Wraw * Wraw * (3.0 - 2.0 * Wraw)
            Wclip = Wraw * M
            Ld = np.maximum(L, 1e-5)
            c = data / Ld[None, ...]
            if float(Wclip.max()) > 0.01:
                wgt = 1.0 - Wraw
                den = _ndi.gaussian_filter(L * wgt, 100.0)
                c_fill = np.stack([
                    _ndi.gaussian_filter(data[i] * wgt, 100.0)
                    / np.maximum(den, 1e-6) for i in range(3)])
                c = c * (1.0 - Wclip)[None, ...] + c_fill * Wclip[None, ...]
            out = np.clip(Lnew[None, ...] * c, 0.0, 1.0).astype(np.float32)
        else:
            out = np.clip(data * (1.0 - M) + hdr_layer * M, 0.0, 1.0).astype(np.float32)
        _save_fits(out, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] hdr_core_blend done in {elapsed}s "
                    f"(thr={threshold}, core coverage {100 * core_cov:.2f}%): "
                    f"{output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "threshold": threshold, "core_coverage": core_cov}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] hdr_core_blend exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Dark Enhancer — SASpro WaveScale Dark Structure Enhancer
# ---------------------------------------------------------------------------

def dark_enhance(input_path: str | Path, output_path: str | Path,
                 n_scales: int = 6, boost_factor: float = 5.0,
                 mask_gamma: float = 1.0) -> dict:
    """
    Wavelet dark-structure enhancer. Brings out faint detail in shadow areas
    (dust lanes, outer galaxy arms, IFN) without blowing highlights.

    n_scales: wavelet decomposition depth.
    boost_factor: darkness boost multiplier (3-10). Higher = more aggressive.
    mask_gamma: darkness mask curve; higher protects more midtones.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.wavescalede import compute_wavescale_dse
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        result, _ = compute_wavescale_dse(data, n_scales=n_scales,
                                          boost_factor=boost_factor,
                                          mask_gamma=mask_gamma)
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] dark_enhance done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] dark_enhance exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Hue-Selective Color Boost — vibrant galaxy arms / nebula emission
# ---------------------------------------------------------------------------

def _get_preset_hue_boosts(preset: str) -> list[tuple[float, float, float]]:
    """
    Returns list of (center_deg, width_deg, amount) Gaussian saturation boosts.
    Negative amount = suppression.
    """
    if preset == "galaxy":
        # Halved from the original (+0.30 blue) on 2026-06-03: even with the midtone
        # luminance mask, +0.30 over-saturated the noisy arm/disk into blue speckle
        # (M51 A/B review). +0.15 keeps the blue spiral arms visible without speckle.
        return [
            (240, 35,  0.15),   # blue spiral arms (210–270°)
            (280, 20,  0.075),  # blue-purple transition
            (340, 25,  0.22),   # Ha/HII pink-magenta knots (Henry's A/B pick 2026-06-10)
            ( 10, 20,  0.06),   # red HII regions
            ( 60, 20, -0.025),  # suppress yellow (prevents core going orange)
        ]
    elif preset == "nebula":
        return [
            (  0, 25,  0.35),   # Ha red (0°=360°, wraps)
            (350, 20,  0.35),   # Ha red bleed
            (300, 25,  0.20),   # magenta (Ha+OIII blend zone)
            (190, 30,  0.20),   # cyan (OIII 501nm)
        ]
    elif preset == "nbn":
        # Post-NarrowbandNormalization HOO palette: warm gold/copper dust + teal OIII
        # core. Validated on IC 1805 / Rosette trials (2026-06-08) at o3Boost≈1.3–1.5.
        return [
            ( 35, 30,  0.40),   # gold/copper dust (dominant nbn hue)
            ( 18, 20,  0.22),   # amber bleed
            (195, 30,  0.32),   # teal-blue OIII core
        ]
    return []


def color_boost(input_path: str | Path, output_path: str | Path,
                preset: str = "galaxy",
                hue_boosts: list | None = None,
                global_sat_lift: float = 0.0,
                **_kwargs) -> dict:
    """
    Hue-selective saturation enhancement using HSV Gaussian kernels.

    preset="galaxy":  +0.15 at 240° (blue arms), +0.22 at 340° (Hα/HII pink knots,
                      with an 8% red lift on the same band), −0.025 at 60° (dampen yellow)
    preset="nebula":  +0.35 at 0°/350° (Ha red), +0.20 at 300° (magenta), +0.20 at 190° (OIII cyan)
    hue_boosts: override preset with [(center_deg, width_deg, amount), ...] list
    global_sat_lift: uniform saturation delta added on top of hue-selective boost
    """
    t0 = time.time()
    try:
        from skimage.color import rgb2hsv, hsv2rgb  # noqa: PLC0415
        data, header = _load_fits(input_path)
        data = data.astype(np.float32)
        img_hwc = np.moveaxis(np.clip(data, 0.0, 1.0), 0, -1)   # CHW → HWC
        hsv = rgb2hsv(img_hwc)
        h = hsv[:, :, 0]   # 0–1 maps to 0–360°

        boosts = _get_preset_hue_boosts(preset) if hue_boosts is None else list(hue_boosts)
        delta = np.zeros_like(h)
        for center_deg, width_deg, amount in boosts:
            c = center_deg / 360.0
            w = (width_deg  / 360.0)
            d = np.abs(h - c)
            d = np.minimum(d, 1.0 - d)          # handle hue wrap-around at 0/1
            delta += float(amount) * np.exp(-(d ** 2) / (2.0 * w ** 2))

        # Hue is noisy in faint regions, so a per-pixel delta posterizes the color
        # into hard orange/blue patches. Smoothing the boost field (not the image)
        # keeps luminance detail while making the saturation boost spatially gradual.
        from scipy.ndimage import gaussian_filter  # noqa: PLC0415
        delta = gaussian_filter(delta, sigma=4.0)

        hsv[:, :, 1] = np.clip(hsv[:, :, 1] + delta + float(global_sat_lift), 0.0, 1.0)
        out = hsv2rgb(hsv).astype(np.float32)

        # Hα knot red lift (galaxy preset): saturation alone deepens the pink but the
        # knots stay dim; a gentle red-channel gain where the magenta band fires pushes
        # them toward the reference HII pink. Uses the ORIGINAL hue field + the same
        # σ4 smoothing so it cannot re-introduce posterized edges.
        if preset == "galaxy" and hue_boosts is None:
            c = 340.0 / 360.0
            w = 25.0 / 360.0
            d = np.abs(h - c)
            d = np.minimum(d, 1.0 - d)
            lift = np.exp(-(d ** 2) / (2.0 * w ** 2))
            lift = gaussian_filter(lift, sigma=4.0)
            out[:, :, 0] = np.clip(out[:, :, 0] * (1.0 + 0.08 * lift), 0.0, 1.0)

        _save_fits(np.moveaxis(out, -1, 0), header, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] color_boost ({preset}) done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path),
                "preset": preset, "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] color_boost exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Narrowband Normalization — PI NarrowbandNormalization (Ha/OIII balance)
# ---------------------------------------------------------------------------

def narrowband_normalize(input_path: str | Path, output_path: str | Path,
                         method: str = "Equalize",
                         o3_boost: float = 1.0,
                         hoo_boost: float = 0.0,
                         **_kwargs) -> dict:
    """
    PI NarrowbandNormalization on a stretched starless image.

    method="Equalize":      equalize sky background per channel (best for Ha-OIII balance)
    method="MaximumStars":  maximize stellar brightness (brighter palette pop)
    o3_boost:  OIII boost in the NBN HOO palette (PI o3Boost; 1.0=neutral, >1 pushes the
               teal-blue OIII core). The key vibrancy lever — validated 1.3–1.5 on S50 LP.
    hoo_boost: in-JS post-NBN ColorSaturation pass (PI nbn_hoo_boost). Default 0.0 disables
               it so the Python "nbn" color_boost preset is the only saturation pass (avoids
               double/triple saturation).

    Works on broadband OSC (normalizes Ha bleed into red), duo-band Ha+OIII composites,
    and full SHO composites.
    """
    from nas_server.pixinsight import run_postprocess   # noqa: PLC0415
    t0 = time.time()
    try:
        result = run_postprocess(
            target="narrowband_norm",
            input_fits=str(input_path),
            output_path=str(output_path),
            nbn=True,
            nbn_method=method,
            nbn_o3_boost=float(o3_boost),
            nbn_hoo_boost=float(hoo_boost),
            # disable everything else
            gradient_correction=False, color_calibration=False, bgn=False,
            spcc=False, mlt=False, tgv=False, bxt=False, nxt=False,
            ht=False, scnr=False, cms=False,
        )
        elapsed = int(time.time() - t0)
        if not result.get("ok"):
            logger.warning(f"[seti_astro] narrowband_normalize: PI returned not-ok: {result.get('error')}")
            return {"ok": False, "error": result.get("error", "NBN failed"), "elapsed_s": elapsed}
        # Safety net: PI has saved integer-scaled FITS here (uint32 0..4.29e9 on the
        # 1.7.0 NBN runs), which broke every downstream consumer that assumes [0,1].
        # Rewrite as normalized float32 if the output came back out of range.
        try:
            from astropy.io import fits as _fits  # noqa: PLC0415
            with _fits.open(str(output_path)) as _h:
                _d = _h[0].data
                _hdr = _h[0].header.copy()
                _needs = (not np.issubdtype(_d.dtype, np.floating)) or float(np.nanmax(_d)) > 1.001
                _d = _d.astype(np.float32)
            if _needs:
                _lo, _hi = float(np.nanmin(_d)), float(np.nanmax(_d))
                if _hi > _lo:
                    _d = (_d - _lo) / (_hi - _lo)
                _save_fits(_d, _hdr, output_path)
                logger.info("[seti_astro] narrowband_normalize: PI output was non-float/out-of-range — "
                            "rewritten as normalized float32")
        except Exception as _se:
            logger.warning(f"[seti_astro] narrowband_normalize: float sanitize failed: {_se}")
        logger.info(f"[seti_astro] narrowband_normalize ({method}) o3Boost={o3_boost} done in {elapsed}s")
        return {"ok": True, "output_path": str(output_path), "method": method,
                "o3_boost": float(o3_boost), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] narrowband_normalize exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def narrowband_hoo(input_path: str | Path, output_path: str | Path,
                   run_dir: str | Path | None = None,
                   oiii_cap: float = 0.85, oiii_g: float = 0.90,
                   **_kwargs) -> dict:
    """HOO-preserving palette — Ha stays the dominant RED, OIII a bounded blue/teal accent.

    The alternative to narrowband_normalize's PI "Equalize" (which lifts weak OIII up to
    MATCH Ha, giving the SHO/gold-blue look — great on some targets, wrong when you want a
    natural Ha-red-dominant HOO). Design (validated IC 1805 2026-07-08):

      R = Ha ;  G = 0.15·Ha + oiii_g·O ;  B = O
      where O = min(OIII, oiii_cap · Ha)  ← the load-bearing cap.

    Capping OIII at a fraction of the LOCAL Ha guarantees Ha dominates everywhere and, where
    there is no Ha (true sky), forces O→0 — which automatically kills the field-flood that a
    naive OIII surface produces on Ha-dominant fields (the exact failure of RGB-proxy OIII and
    of raw extracted OIII, whose low-level signal covers ~half the frame). oiii_cap is the one
    real knob: 0.6 ≈ near-pure red, 1.0 ≈ maximal teal while still Ha-led.

    OIII source, in order of preference:
      1. run_dir/xp_oiii.fit + xp_ha.fit  (real Gaia-XP-unmixed channels, when present)
      2. RGB proxy from the stretched input (Ha≈R; OIII≈blue-cyan excess over the Ha continuum)
    """
    t0 = time.time()
    try:
        from scipy import ndimage as _ndi
        data, hdr = _load_fits(input_path)
        data = np.nan_to_num(data.astype(np.float32))
        if data.ndim != 3 or data.shape[0] != 3:
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "non-rgb",
                    "elapsed_s": int(time.time() - t0)}
        rgb = np.clip(np.moveaxis(data, 0, -1), 0, 1)
        R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]

        def _stf(x, t=0.20, k=2.0):
            m = float(np.median(x)); s = float(np.median(np.abs(x - m))) * 1.4826
            lo = m - k * s
            y = np.clip((x - lo) / max(x.max() - lo, 1e-9), 0, 1)
            md = float(np.median(y))
            mt = (md * (t - 1)) / ((2 * t - 1) * md - t) if md > 0 else 0.5
            return np.clip(((mt - 1) * y) / (((2 * mt - 1) * y) - mt + 1e-9), 0, 1)

        # Ha and OIII MUST be on the same stretch scale or the cap misfires. When the
        # real XP channels are used, take BOTH from them (xp_ha as the Ha reference, not
        # the brighter RGB red — that mismatch crushed OIII to ~4% coverage). Only the
        # RGB-proxy fallback uses the stretched red as Ha.
        Ha = R
        src = "rgb_proxy"
        oiii = None
        if run_dir:
            _hp = Path(run_dir) / "xp_ha.fit"
            _op = Path(run_dir) / "xp_oiii.fit"
            if _hp.exists() and _op.exists():
                try:
                    _h = _load_fits(_hp)[0].astype(np.float32)
                    _o = _load_fits(_op)[0].astype(np.float32)
                    Ha = _stf(_h, 0.20, 2.0)
                    oiii = _stf(_ndi.gaussian_filter(_o, 2.0), 0.16, 1.5)
                    src = "xp_oiii"
                except Exception as _xe:
                    logger.warning(f"[seti_astro] narrowband_hoo: xp channel load failed ({_xe}) — RGB proxy")
        if oiii is None:
            _pr = np.clip((0.5 * G + 0.5 * B) - 0.55 * Ha, 0, None)
            _pr = _ndi.gaussian_filter(_pr, 1.2)
            _p = float(np.percentile(_pr, 99.5))
            oiii = np.clip(_pr / _p, 0, 1) if _p > 1e-9 else _pr

        O = np.minimum(oiii, float(oiii_cap) * Ha)   # ← Ha-dominance cap
        out = np.stack([Ha,
                        np.clip(0.15 * Ha + float(oiii_g) * O, 0, 1),
                        O], axis=0).astype(np.float32)
        _save_fits(out, hdr, output_path)
        cov = float((O > 0.15).mean())
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] narrowband_hoo done in {elapsed}s "
                    f"(src={src} oiii_cap={oiii_cap} OIII_cover={cov*100:.0f}%): {output_path}")
        return {"ok": True, "output_path": str(output_path), "method": "hoo",
                "oiii_source": src, "oiii_cap": float(oiii_cap),
                "oiii_coverage": cov, "elapsed_s": elapsed}
    except Exception as e:
        logger.error(f"[seti_astro] narrowband_hoo exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": int(time.time() - t0)}


# ---------------------------------------------------------------------------
# SHO / Foraxx Palette Compositing — mono narrowband or duo-band FITS → RGB
# ---------------------------------------------------------------------------

def _load_mono_fits(path: str | Path) -> np.ndarray:
    """Load a FITS file and return a 2D float32 array (takes first channel if RGB)."""
    data, _ = _load_fits(path)
    if data.ndim == 3:
        data = data[0]
    return data.astype(np.float32)


def _norm_channel(ch: np.ndarray) -> np.ndarray:
    """Normalize a 2D array to [0, 1] by its 99.9th-percentile value."""
    p = float(np.percentile(ch, 99.9))
    return np.clip(ch / p if p > 1e-9 else ch, 0.0, 1.0)


def narrowband_composite_dispatch(input_path: str | Path, output_path: str | Path,
                                  palette: str = "foraxx",
                                  ha_path: str | Path | None = None,
                                  oiii_path: str | Path | None = None,
                                  sii_path: str | Path | None = None,
                                  **_kwargs) -> dict:
    """
    Dispatcher for the narrowband_composite ontology step.
    Routes to sho_composite() or foraxx_composite() based on palette param.
    ha_path / oiii_path / sii_path are injected from job extra_params by auto_process.
    input_path is the pipeline's current_path at that point (unused — composite creates a fresh output).
    """
    if ha_path is None or oiii_path is None:
        return {"ok": False, "error": "narrowband_composite: ha_path and oiii_path are required"}
    if palette.lower() == "sho":
        return sho_composite(ha_path=ha_path, oiii_path=oiii_path,
                             output_path=output_path, sii_path=sii_path)
    else:
        return foraxx_composite(ha_path=ha_path, oiii_path=oiii_path,
                                output_path=output_path, sii_path=sii_path)


def sho_composite(ha_path: str | Path, oiii_path: str | Path,
                  output_path: str | Path,
                  sii_path: str | Path | None = None,
                  **_kwargs) -> dict:
    """
    HST / Hubble palette (SHO):
      R = SII  (or Ha proxy when SII not available)
      G = Ha
      B = OIII
    Each channel independently normalised to 99.9th-percentile before combination.
    """
    t0 = time.time()
    try:
        ha   = _norm_channel(_load_mono_fits(ha_path))
        oiii = _norm_channel(_load_mono_fits(oiii_path))
        sii  = _norm_channel(_load_mono_fits(sii_path)) if sii_path else ha
        rgb  = np.stack([sii, ha, oiii], axis=0)   # CHW
        _, header = _load_fits(ha_path)
        header["PALETTE"] = "SHO"
        _save_fits(rgb, header, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] sho_composite done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "palette": "SHO",
                "has_sii": sii_path is not None, "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] sho_composite exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def foraxx_composite(ha_path: str | Path, oiii_path: str | Path,
                     output_path: str | Path,
                     sii_path: str | Path | None = None,
                     **_kwargs) -> dict:
    """
    Foraxx palette (natural-colour HOO variant):
      R = Ha*0.76 + OIII*0.24
      G = OIII*0.85 + Ha*0.15
      B = OIII
    Produces crimsons/pinks for Ha-dominated regions, blue-greens for OIII zones,
    and more natural star colours than SHO.  If SII provided, blended into R at 10%.
    Magenta-star correction is handled downstream by the CMS step.
    """
    t0 = time.time()
    try:
        ha   = _norm_channel(_load_mono_fits(ha_path))
        oiii = _norm_channel(_load_mono_fits(oiii_path))
        R = np.clip(ha * 0.76 + oiii * 0.24, 0.0, 1.0)
        G = np.clip(oiii * 0.85 + ha * 0.15, 0.0, 1.0)
        B = oiii.copy()
        if sii_path:
            sii = _norm_channel(_load_mono_fits(sii_path))
            R = np.clip(R + sii * 0.10, 0.0, 1.0)
        rgb = np.stack([R, G, B], axis=0)
        _, header = _load_fits(ha_path)
        header["PALETTE"] = "FORAXX"
        _save_fits(rgb, header, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] foraxx_composite done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "palette": "Foraxx",
                "has_sii": sii_path is not None, "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] foraxx_composite exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Deconvolution — Richardson-Lucy (headless scipy implementation)
# ---------------------------------------------------------------------------

def deconvolve_rl(input_path: str | Path, output_path: str | Path,
                  iterations: int = 30, psf_sigma: float = 1.5,
                  psf_size: int = 7) -> dict:
    """
    Richardson-Lucy deconvolution with a Gaussian PSF (blind estimate).
    Sharpens without introducing BXT-style AI artifacts — useful for
    comparison in experiment mode.

    iterations: more = sharper but more ring artefacts (20-50 typical).
    psf_sigma: Gaussian sigma in pixels — estimate of seeing disc radius.
    psf_size: kernel size (odd number, psf_size >= 2*psf_sigma+1).
    """
    t0 = time.time()
    try:
        from scipy.signal import fftconvolve
        from scipy.ndimage import gaussian_filter

        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)

        # Gaussian PSF
        psf_size = max(3, psf_size | 1)  # ensure odd
        center = psf_size // 2
        yy, xx = np.ogrid[:psf_size, :psf_size]
        psf = np.exp(-((xx - center)**2 + (yy - center)**2) / (2 * psf_sigma**2))
        psf /= psf.sum()
        psf_flip = psf[::-1, ::-1]

        def _rl_channel(ch: np.ndarray) -> np.ndarray:
            est = ch.copy()
            for _ in range(iterations):
                blur = fftconvolve(est, psf, mode='same')
                ratio = ch / np.maximum(blur, 1e-10)
                est *= fftconvolve(ratio, psf_flip, mode='same')
                est = np.clip(est, 0.0, 1.0)
            return est

        if data.ndim == 2:
            result = _rl_channel(data)
        else:
            result = np.stack([_rl_channel(data[:, :, c]) for c in range(data.shape[2])],
                              axis=2)
            result = np.moveaxis(result, -1, 0)

        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] deconvolve_rl ({iterations} iters σ={psf_sigma}) "
                    f"done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] deconvolve_rl exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Star Removal — SASpro DarkStar (headless in-process)
# ---------------------------------------------------------------------------

def remove_stars_inprocess(input_path: str | Path, output_path: str | Path,
                            mode: str = "unscreen", use_gpu: bool = True,
                            chunk_size: int = 512) -> dict:
    """
    Remove stars using SASpro DarkStar AI (in-process, no CLI subprocess).
    Produces a starless image; stars can optionally be re-added later.

    mode: 'unscreen' (recommended for most targets) | 'additive'
    Returns starless FITS at output_path.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.remove_stars import darkstar_starless_from_array
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        _noop = lambda *a, **kw: None
        starless, _, _ok = darkstar_starless_from_array(
            data,
            use_gpu=use_gpu,
            chunk_size=chunk_size,
            mode=mode,
            output_stars_only=False,
            status_cb=_noop,
            progress_cb=_noop,
        )
        if starless.ndim == 3:
            starless = np.moveaxis(starless, -1, 0)
        _save_fits(np.clip(starless, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] remove_stars_inprocess done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] remove_stars_inprocess exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Cosmic Clarity in-process — sharpen or denoise via SASpro headless engine
# ---------------------------------------------------------------------------

def cc_sharpen_inprocess(input_path: str | Path, output_path: str | Path,
                         stellar_amount: float = 0.5, nonstellar_amount: float = 0.3,
                         sharpening_mode: str = "Both",
                         chunk_size: int = 256, use_gpu: bool = True) -> dict:
    """
    Cosmic Clarity sharpening via SASpro headless engine (no CLI subprocess).
    Useful for comparing against BXT in experiment mode.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.cosmicclarity_headless import run_cosmicclarity_on_array
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        preset = {
            "mode": "sharpen",
            "stellar_amount": stellar_amount,
            "nonstellar_amount": nonstellar_amount,
            "sharpening_mode": sharpening_mode,
            "chunk_size": chunk_size,
            "gpu": use_gpu,
        }
        result = run_cosmicclarity_on_array(data, preset)
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] cc_sharpen_inprocess done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] cc_sharpen_inprocess exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def bxt_star_correct(input_path: str | Path, output_path: str | Path,
                     **_kwargs) -> dict:
    """
    BXT correct-only mode — fixes star shapes (roundness/elongation) without
    applying any deblurring or sharpening to the background nebulosity.

    Ideal as a post-denoise step for globular clusters and dense star fields:
    denoise inflates star FWHM slightly; correct_only=True rounds them back up
    without touching the background that was just cleaned.

    Uses BXT's AI model with auto PSF detection. sharpen_stars=0, sharpen_nonstellar=0
    are set explicitly to suppress any enhancement — pure geometric correction only.
    """
    from nas_server.pixinsight import run_postprocess   # noqa: PLC0415
    t0 = time.time()
    try:
        result = run_postprocess(
            target="bxt_star_correct",
            input_fits=str(input_path),
            output_path=str(output_path),
            # Enable BXT in correct-only mode
            bxt=True,
            bxt_correct_only=True,
            bxt_auto_psf=True,
            bxt_stars=0.0,
            bxt_nonstellar=0.0,
            bxt_adjust_halos=0.0,
            # Disable all other processing
            gradient_correction=False, color_calibration=False, bgn=False,
            spcc=False, mlt=False, tgv=False, nxt=False,
            ht=False, scnr=False, cms=False,
        )
        elapsed = int(time.time() - t0)
        if not result.get("ok"):
            logger.warning(f"[seti_astro] bxt_star_correct: PI returned not-ok: {result.get('error')}")
            return {"ok": False, "error": result.get("error", "BXT correct-only failed"), "elapsed_s": elapsed}
        logger.info(f"[seti_astro] bxt_star_correct done in {elapsed}s")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] bxt_star_correct exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def bxt_deconvolve(input_path: str | Path, output_path: str | Path,
                   stellar_amount: float | None = None,
                   nonstellar_amount: float | None = None,
                   bxt_psf: float = 4.0, bxt_stars: float = 0.5,
                   bxt_nonstellar: float = 0.3, bxt_auto_psf: bool = True,
                   **_kwargs) -> dict:
    """
    Full BlurXTerminator deconvolution on linear data — best-in-class PSF
    deconvolution for SeeStar frames and the standard-mode default for the
    deconvolution step. Replaces cc_sharpen_inprocess, which can look sharper on a
    linear preview but rings stars into halos that turn catastrophic after stretch +
    star removal (NGC 6914).

    Falls back to cc_sharpen_inprocess when PixInsight/BXT is unavailable or fails, so
    a worker without PI still produces a sharpened result. Accepts the ontology's
    stellar_amount/nonstellar_amount and maps them onto BXT's stars/nonstellar.
    """
    from nas_server.pixinsight import run_postprocess   # noqa: PLC0415
    t0 = time.time()
    if stellar_amount is not None:
        bxt_stars = float(stellar_amount)
    if nonstellar_amount is not None:
        bxt_nonstellar = float(nonstellar_amount)
    try:
        result = run_postprocess(
            target="bxt_deconvolve",
            input_fits=str(input_path),
            output_path=str(output_path),
            bxt=True,
            bxt_correct_only=False,
            bxt_auto_psf=bxt_auto_psf,
            bxt_psf=bxt_psf,
            bxt_stars=bxt_stars,
            bxt_nonstellar=bxt_nonstellar,
            bxt_adjust_halos=0.0,
            gradient_correction=False, color_calibration=False, bgn=False,
            spcc=False, mlt=False, tgv=False, nxt=False,
            ht=False, scnr=False, cms=False,
        )
        elapsed = int(time.time() - t0)
        if result.get("ok") and Path(output_path).exists():
            _preserve_celestial_wcs(input_path, output_path)  # PI drops WCS → preview flip
            logger.info(f"[seti_astro] bxt_deconvolve done in {elapsed}s")
            return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
        logger.warning(f"[seti_astro] bxt_deconvolve PI not-ok "
                       f"({result.get('error')}) — falling back to cc_sharpen_inprocess")
    except Exception as e:
        logger.warning(f"[seti_astro] bxt_deconvolve exception ({e}) — "
                       f"falling back to cc_sharpen_inprocess")
    _sa = bxt_stars if stellar_amount is None else float(stellar_amount)
    _na = bxt_nonstellar if nonstellar_amount is None else float(nonstellar_amount)
    return cc_sharpen_inprocess(input_path, output_path,
                                stellar_amount=_sa, nonstellar_amount=_na)


def cc_denoise_inprocess(input_path: str | Path, output_path: str | Path,
                         denoise_luma: float = 0.9, denoise_color: float = 0.7,
                         chunk_size: int = 256, use_gpu: bool = True) -> dict:
    """
    Cosmic Clarity denoising via SASpro headless engine (in-process, no subprocess).
    Allows direct comparison against NXT (PI) in experiment mode.
    """
    t0 = time.time()
    try:
        from setiastro.saspro.cosmicclarity_headless import run_cosmicclarity_on_array
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)
        preset = {
            "mode": "denoise",
            "denoise_luma": denoise_luma,
            "denoise_color": denoise_color,
            "chunk_size": chunk_size,
            "gpu": use_gpu,
        }
        result = run_cosmicclarity_on_array(data, preset)
        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] cc_denoise_inprocess done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] cc_denoise_inprocess exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def syqon_prism_denoise(input_path: str | Path, output_path: str | Path,
                        strength: float = 0.85, tile: int = 512,
                        overlap: int = 64, pad: int = 64,
                        use_mtf: bool = True, mtf_target: float = 0.10) -> dict:
    """
    SyQon Prism Mini denoising — pure in-process numpy path, no GUI objects.
    Experiment alternative to cc_denoise_inprocess for the noise_reduction step.

    Model weights: ~/.local/share/SASpro/runtime/py312/models/syqon_denoise/prism_mini.pt
    Download: github.com/setiastro/setiastrosuitepro/releases/download/benchmarkFIT/prism_mini
    """
    t0 = time.time()
    try:
        from setiastro.saspro.denoise_engines.syqon_prism_engine import prism_denoise_rgb01
        from setiastro.saspro.syqon_paths import syqon_prism_model_path
        from setiastro.saspro.syqon_tools import _mtf_params_unlinked, _apply_mtf_unlinked_rgb, _invert_mtf_unlinked_rgb

        ckpt = syqon_prism_model_path("prism_mini")
        if not ckpt.exists():
            return {"ok": False,
                    "error": f"Prism Mini weights not found at {ckpt}. "
                             "Download from: github.com/setiastro/setiastrosuitepro/"
                             "releases/download/benchmarkFIT/prism_mini",
                    "elapsed_s": 0}

        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)   # CHW → HWC

        x = np.clip(np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0).astype(np.float32)
        x_in = x

        mtf_params = None
        if use_mtf:
            mtf_params = _mtf_params_unlinked(x, shadows_clipping=-2.8, targetbg=mtf_target)
            x_in = _apply_mtf_unlinked_rgb(x, mtf_params)

        denoised, info = prism_denoise_rgb01(
            x_in, str(ckpt),
            tile=tile, overlap=overlap, use_gpu=False,
            model_variant="free",
        )

        if use_mtf and mtf_params is not None:
            denoised = _invert_mtf_unlinked_rgb(denoised, mtf_params)

        # Blend at requested strength
        result = np.clip((1.0 - strength) * x + strength * denoised, 0.0, 1.0).astype(np.float32)

        if result.ndim == 3:
            result = np.moveaxis(result, -1, 0)   # HWC → CHW for FITS
        _save_fits(result, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] syqon_prism_denoise done in {elapsed}s "
                    f"(device={info.get('device')}, variant={info.get('variant')}): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "syqon_info": {k: info.get(k) for k in ("device", "variant", "base_ch")}}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] syqon_prism_denoise exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# SASpro Image MM — Multi-Frame Deconvolution Stacking
# ---------------------------------------------------------------------------

def imagemm_stack(registered_paths: list[str], output_path: str | Path,
                  iters: int = 20, kappa: float = 2.0,
                  color_mode: str = "PerChannel",
                  use_star_masks: bool = False,
                  rejection_strength: float = 1.0,
                  status_cb=None) -> dict:
    """
    Stack pre-registered FITS files using SASpro Image MM (multi-frame deconvolution).

    Requires QT_QPA_PLATFORM=offscreen (set automatically). No display needed.
    Inputs must be calibrated and registered (e.g. by Siril seqapplyreg).

    color_mode: "PerChannel" for RGB, "luma" for luminance-only deconvolution.
    """
    import os, sys
    t0 = time.time()

    if status_cb is None:
        status_cb = lambda s: logger.info(f"[imagemm] {s}")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication(sys.argv)

        from setiastro.saspro.mfdeconv import multiframe_deconv_normal_rebuild

        logger.info(f"[imagemm] Starting Image MM on {len(registered_paths)} frames "
                    f"(iters={iters}, kappa={kappa}, mode={color_mode})")

        out = multiframe_deconv_normal_rebuild(
            registered_paths,
            str(output_path),
            iters=iters,
            kappa=kappa,
            color_mode=color_mode,
            use_star_masks=use_star_masks,
            rejection_strength=rejection_strength,
            status_cb=status_cb,
        )
        elapsed = round(time.time() - t0, 1)
        result_path = out or str(output_path)
        logger.info(f"[imagemm] Done in {elapsed}s → {result_path}")
        return {"ok": True, "output_path": result_path, "elapsed_s": elapsed}
    except Exception as e:
        import traceback as _tb
        elapsed = round(time.time() - t0, 1)
        tb_str = _tb.format_exc()
        logger.error("[imagemm] Exception: %s\n%s", e, tb_str)
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def _detect_crop_bounds(data: np.ndarray, threshold_frac: float = 0.85,
                        buffer_frac: float = 0.02) -> tuple[int, int, int, int]:
    """
    Detect actual stacking coverage boundary by finding where row/column means
    drop below threshold_frac of the centre region mean.

    Returns (top, bottom, left, right) pixel indices — the safe interior region.
    buffer_frac adds a safety margin beyond the detected boundary.
    """
    lum = data.mean(axis=0) if data.ndim == 3 else data.astype(np.float32)
    h, w = lum.shape

    # Reference: centre 40% of the image (well clear of any edge artifacts)
    cy0, cy1 = int(h * 0.30), int(h * 0.70)
    cx0, cx1 = int(w * 0.30), int(w * 0.70)
    centre_mean = float(lum[cy0:cy1, cx0:cx1].mean())
    if centre_mean <= 0:
        # Degenerate image — fall back to 5% hard crop
        dy, dx = int(h * 0.05), int(w * 0.05)
        return dy, h - dy, dx, w - dx

    threshold = centre_mean * threshold_frac

    row_means = lum.mean(axis=1)   # shape (H,)
    col_means = lum.mean(axis=0)   # shape (W,)

    # Scan inward from each edge until mean exceeds threshold
    top = 0
    while top < h // 3 and row_means[top] < threshold:
        top += 1

    bottom = h - 1
    while bottom > 2 * h // 3 and row_means[bottom] < threshold:
        bottom -= 1

    left = 0
    while left < w // 3 and col_means[left] < threshold:
        left += 1

    right = w - 1
    while right > 2 * w // 3 and col_means[right] < threshold:
        right -= 1

    # Safety buffer beyond the detected boundary
    buf_y = max(4, int(h * buffer_frac))
    buf_x = max(4, int(w * buffer_frac))
    top    = min(top    + buf_y, h // 4)
    bottom = max(bottom - buf_y, 3 * h // 4)
    left   = min(left   + buf_x, w // 4)
    right  = max(right  - buf_x, 3 * w // 4)

    return top, bottom, left, right


# Aggressiveness presets for coverage-based cropping.
#   noise_factor: a tile is rejected when its noise proxy exceeds this multiple
#                 of the interior median noise (low-coverage borders are noisier).
#   blank_frac:   a tile is rejected when its mean luminance falls below this
#                 fraction of the interior mean (true blank / black borders).
_CROP_PRESETS = {
    "conservative": {"noise_factor": 1.85, "blank_frac": 0.06},
    "balanced":     {"noise_factor": 1.50, "blank_frac": 0.10},
    "aggressive":   {"noise_factor": 1.28, "blank_frac": 0.15},
}


def _largest_inscribed_rect(mask: np.ndarray):
    """
    Largest all-True axis-aligned rectangle in a binary mask (True = valid).

    Classic O(H*W) maximal-rectangle-in-histogram DP. Returns inclusive
    (r0, r1, c0, c1) grid indices, or None if the mask is empty.
    """
    H, W = mask.shape
    heights = [0] * W
    best_area = 0
    best = None
    for r in range(H):
        row = mask[r]
        for c in range(W):
            heights[c] = heights[c] + 1 if row[c] else 0
        stack = []  # (start_col, height)
        for c in range(W + 1):
            cur = heights[c] if c < W else 0
            start = c
            while stack and stack[-1][1] >= cur:
                s_c, s_h = stack.pop()
                area = s_h * (c - s_c)
                if area > best_area:
                    best_area = area
                    best = (r - s_h + 1, r, s_c, c - 1)
                start = s_c
            stack.append((start, cur))
    return best


def _coverage_tile_mask(lum: np.ndarray, tile: int = 32,
                        noise_factor: float = 1.5, blank_frac: float = 0.10):
    """
    Build a tile-resolution validity mask for stacking coverage.

    A tile is INVALID when it is either:
      * blank   — mean luminance < blank_frac * interior mean (black/empty borders), or
      * noisy   — local noise proxy > noise_factor * interior median noise
                  (low-coverage regions reduce noise by less than the interior, so
                   under-stacked borders and low-framed subs read as noisier).

    The noise proxy is a robust MAD of the per-tile Laplacian — it ignores smooth
    large-scale signal, so a bright but well-covered nebula core is NOT rejected.

    Returns (valid_tiles[bool, nty, ntx], stats dict).
    """
    h, w = lum.shape
    nty = max(1, h // tile)
    ntx = max(1, w // tile)
    mean_t = np.zeros((nty, ntx), dtype=np.float32)
    noise_t = np.zeros((nty, ntx), dtype=np.float32)
    for ty in range(nty):
        y0 = ty * tile
        y1 = h if ty == nty - 1 else (ty + 1) * tile
        for tx in range(ntx):
            x0 = tx * tile
            x1 = w if tx == ntx - 1 else (tx + 1) * tile
            t = lum[y0:y1, x0:x1]
            mean_t[ty, tx] = float(t.mean())
            if t.shape[0] >= 3 and t.shape[1] >= 3:
                lap = (4.0 * t[1:-1, 1:-1]
                       - t[:-2, 1:-1] - t[2:, 1:-1]
                       - t[1:-1, :-2] - t[1:-1, 2:])
                noise_t[ty, tx] = float(np.median(np.abs(lap - np.median(lap))))
            else:
                noise_t[ty, tx] = 0.0

    # Interior reference from the central 50% of tiles (clear of any border).
    cy0, cy1 = int(nty * 0.25), max(int(nty * 0.75), int(nty * 0.25) + 1)
    cx0, cx1 = int(ntx * 0.25), max(int(ntx * 0.75), int(ntx * 0.25) + 1)
    centre_mean = mean_t[cy0:cy1, cx0:cx1]
    centre_noise = noise_t[cy0:cy1, cx0:cx1]
    interior_mean = float(np.median(centre_mean)) if centre_mean.size else float(np.median(mean_t))
    interior_noise = float(np.median(centre_noise)) if centre_noise.size else float(np.median(noise_t))

    blank_thresh = max(1e-7, blank_frac * interior_mean)
    noise_thresh = noise_factor * interior_noise if interior_noise > 0 else float("inf")

    nonblank = mean_t > blank_thresh
    quiet = noise_t <= noise_thresh
    valid = nonblank & quiet

    stats = {
        "tile": tile, "grid": [nty, ntx],
        "interior_mean": interior_mean, "interior_noise": interior_noise,
        "blank_thresh": blank_thresh, "noise_thresh": noise_thresh,
        "valid_tiles": int(valid.sum()), "total_tiles": int(valid.size),
        "blank_rejected": int((~nonblank).sum()),
        "noise_rejected": int((nonblank & ~quiet).sum()),
    }
    return valid, stats


def _detect_crop_bounds_coverage(data: np.ndarray, aggressiveness: str = "balanced",
                                 tile: int = 32, inset_frac: float = 0.0):
    """
    Coverage-aware crop detection for irregular (max-framing / multi-session) stacks.

    Builds a tile-level validity mask (blank + low-coverage-noise rejection) then
    finds the Largest Inscribed Rectangle of valid tiles — the biggest fully-covered
    region, even when the coverage footprint is a non-rectangular diamond/staircase.

    Returns (top, bottom, left, right, info).
    """
    lum = data.mean(axis=0) if data.ndim == 3 else data.astype(np.float32)
    h, w = lum.shape
    preset = _CROP_PRESETS.get(aggressiveness, _CROP_PRESETS["balanced"])
    valid, stats = _coverage_tile_mask(
        lum, tile=tile,
        noise_factor=preset["noise_factor"], blank_frac=preset["blank_frac"])

    # Fill interior holes: a bright compact feature (star/blob) can spike the local
    # noise proxy and get falsely rejected, punching a hole that would split the LIR
    # into a thin strip. True low-coverage borders connect to the frame edge, so
    # filling only fully-enclosed holes keeps borders rejected but heals interior holes.
    try:
        from scipy.ndimage import binary_fill_holes
        filled = binary_fill_holes(valid)
        if filled is not None:
            stats["holes_filled"] = int(filled.sum() - valid.sum())
            valid = filled
    except Exception:
        pass

    rect = _largest_inscribed_rect(valid)
    nty, ntx = valid.shape
    if rect is None:
        # Degenerate — fall back to a modest fixed crop.
        dy, dx = int(h * 0.05), int(w * 0.05)
        info = {"method": "coverage", "fallback": "fixed_5pct", **stats}
        return dy, h - dy, dx, w - dx, info

    r0, r1, c0, c1 = rect
    top = r0 * tile
    bottom = h if r1 == nty - 1 else (r1 + 1) * tile
    left = c0 * tile
    right = w if c1 == ntx - 1 else (c1 + 1) * tile

    # Optional inset to pull the crop just inside the last valid tile fringe.
    if inset_frac > 0:
        iy = int((bottom - top) * inset_frac)
        ix = int((right - left) * inset_frac)
        top += iy; bottom -= iy; left += ix; right -= ix

    # Coverage of the original frame retained, and the blank fraction we removed.
    kept = max(0, bottom - top) * max(0, right - left)
    info = {
        "method": "coverage", "aggressiveness": aggressiveness,
        "rect_tiles": [r0, r1, c0, c1],
        "kept_frac_of_frame": round(kept / float(h * w), 3),
        **stats,
    }
    return top, bottom, left, right, info


def remove_pedestal(
    input_path: str | Path,
    output_path: str | Path,
    **_kwargs,
) -> dict:
    """
    Remove image pedestal (ADC bias offset) from a linear stacked FITS.

    Computes the global minimum across all channels and subtracts it uniformly,
    setting the absolute black point to zero without clipping any data and without
    altering channel ratios (colour balance is preserved).

    This is equivalent to the SETI Astro Suite Pro PixelMath step:
        slot0 - min(slot0)

    Run after crop, before background extraction. Quick numpy op — no PI needed.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    data, header = _load_fits(input_path)
    data = data.astype(np.float32)

    pedestal = float(np.min(data))
    if abs(pedestal) < 1e-9:
        _log.info(f"[seti_astro] remove_pedestal: pedestal ≈ 0, skipping shift")
        import shutil
        shutil.copy2(input_path, output_path)
        return {"ok": True, "output_path": str(output_path), "pedestal": pedestal, "skipped": True}

    data -= pedestal
    np.clip(data, 0.0, None, out=data)   # safety — shouldn't clip anything
    _save_fits(data, header, output_path)
    _log.info(f"[seti_astro] remove_pedestal: removed pedestal {pedestal:.6f} from {Path(input_path).name}")
    return {"ok": True, "output_path": str(output_path), "pedestal": pedestal}


def cosmetic_correction(
    input_path: str | Path,
    output_path: str | Path,
    sigma: float = 5.0,       # replace pixels deviating > sigma * MAD from local median
    kernel_size: int = 5,     # local median kernel (pixels); must be odd
    max_defect_size: int = 3, # only fix connected components of this many pixels or fewer
    **_kwargs,                # true hot pixels are 1 pixel; stars are much larger
) -> dict:
    """
    Remove residual hot and cold pixels from a linear stacked FITS.

    For each channel independently:
      1. Compute a local median map (kernel_size × kernel_size box)
      2. Residual = |channel - local_median|
      3. MAD = median(residual) — robust noise estimate independent of stars
      4. Flag pixels where residual > sigma * MAD
      5. Connectivity filter: discard flagged regions > max_defect_size pixels
         (stars always produce multi-pixel PSFs; hot pixels are 1-pixel spikes)
      6. Replace remaining flagged pixels with the local median

    The connectivity filter is the key safeguard — without it sigma=5 flags star
    cores. With it, only truly isolated single-pixel spikes are corrected.
    """
    from scipy.ndimage import median_filter, label as _nd_label
    import logging as _logging
    _log = _logging.getLogger(__name__)

    data, header = _load_fits(input_path)
    data = data.astype(np.float32)

    kernel_size = kernel_size if kernel_size % 2 == 1 else kernel_size + 1  # must be odd
    total_fixed = 0

    channels = range(data.shape[0]) if data.ndim == 3 else [None]

    for ch in channels:
        plane = data[ch] if ch is not None else data
        local_med = median_filter(plane, size=kernel_size)
        residual = np.abs(plane - local_med)
        mad = float(np.median(residual))
        if mad < 1e-9:
            continue  # uniform plane — skip

        # Raw outlier mask (includes star cores)
        raw_mask = residual > (sigma * mad)

        # Connectivity filter: keep only isolated defects (size ≤ max_defect_size)
        # Stars are PSF-shaped (many connected pixels); hot pixels are 1-pixel spikes
        labeled, _ = _nd_label(raw_mask)
        comp_sizes  = np.bincount(labeled.ravel())  # index 0 = background
        # Boolean array: True for component IDs whose size ≤ max_defect_size
        small_comp  = comp_sizes <= max_defect_size
        small_comp[0] = False  # background is never a defect
        isolated_mask = small_comp[labeled]

        n_fixed = int(np.sum(isolated_mask))
        if n_fixed > 0:
            plane[isolated_mask] = local_med[isolated_mask]
            if ch is not None:
                data[ch] = plane
            total_fixed += n_fixed

    _save_fits(data, header, output_path)
    pct_fixed = 100.0 * total_fixed / max(data.size, 1)
    _log.info(f"[seti_astro] cosmetic_correction: fixed {total_fixed:,} px "
              f"({pct_fixed:.4f}%) sigma={sigma} kernel={kernel_size} max_size={max_defect_size}")
    return {"ok": True, "output_path": str(output_path),
            "pixels_fixed": total_fixed, "pct_fixed": round(pct_fixed, 4)}


def crop(
    input_path: str | Path,
    output_path: str | Path,
    method: str = "auto",
    pct: float = 0.05,
    threshold_frac: float = 0.85,
    buffer_frac: float = 0.02,
    aggressiveness: str = "balanced",
    target: str = "",
) -> dict:
    """
    Crop stacking edge artifacts. Should be the first processing step on a fresh stack.

    method="auto"  — coverage-aware: build a tile-level validity mask (rejecting blank
                     and low-coverage/noisy borders) and keep the Largest Inscribed
                     Rectangle of valid tiles. Handles non-rectangular max-framing
                     footprints and low-framed subs. (recommended)
    method="bright"— legacy row/column brightness threshold (rectangular only)
    method="pct"   — fixed percentage from each edge (legacy fallback)

    aggressiveness: "conservative" | "balanced" | "aggressive" — how readily a border
                    tile is rejected as low-coverage.
    """
    t0 = time.time()
    try:
        import numpy as np
        from astropy.io import fits as afits
        input_path = Path(input_path)
        output_path = Path(output_path)

        with afits.open(input_path) as hdul:
            data = hdul[0].data.copy().astype(np.float32)
            header = hdul[0].header.copy()

        if data.ndim not in (2, 3):
            return {"ok": False, "error": f"Unexpected data ndim: {data.ndim}",
                    "elapsed_s": round(time.time() - t0, 1)}

        h = data.shape[-2]
        w = data.shape[-1]

        cov_info = None
        if method == "auto":
            top, bottom, left, right, cov_info = _detect_crop_bounds_coverage(
                data, aggressiveness=aggressiveness
            )
            pct_top    = top / h
            pct_bottom = (h - bottom) / h
            pct_left   = left / w
            pct_right  = (w - right) / w
            logger.info(
                f"[crop] coverage bounds ({aggressiveness}): top={pct_top:.1%} "
                f"bottom={pct_bottom:.1%} left={pct_left:.1%} right={pct_right:.1%} "
                f"| valid_tiles={cov_info.get('valid_tiles')}/{cov_info.get('total_tiles')} "
                f"(blank={cov_info.get('blank_rejected')} noise={cov_info.get('noise_rejected')}) "
                f"kept={cov_info.get('kept_frac_of_frame')}")
        elif method == "bright":
            # Legacy brightness-threshold detector (rectangular only)
            top, bottom, left, right = _detect_crop_bounds(
                data, threshold_frac=threshold_frac, buffer_frac=buffer_frac
            )
        else:
            # Fixed-percentage fallback
            dy = max(1, int(h * pct))
            dx = max(1, int(w * pct))
            top, bottom, left, right = dy, h - dy, dx, w - dx

        # Apply crop
        if data.ndim == 3:
            cropped = data[:, top:bottom, left:right]
        else:
            cropped = data[top:bottom, left:right]

        # Update WCS reference pixel for the new origin so plate solution stays valid
        if "CRPIX1" in header:
            header["CRPIX1"] = header["CRPIX1"] - left
        if "CRPIX2" in header:
            header["CRPIX2"] = header["CRPIX2"] - top

        hdu = afits.PrimaryHDU(data=cropped, header=header)
        hdu.writeto(str(output_path), overwrite=True)

        elapsed = round(time.time() - t0, 1)
        logger.info(f"[crop] {input_path.name} → {output_path.name} "
                    f"({data.shape} → {cropped.shape}) in {elapsed}s")
        return {
            "ok": True,
            "output_path": str(output_path),
            "elapsed_s": elapsed,
            "bounds": {"top": top, "bottom": bottom, "left": left, "right": right},
            "original_shape": list(data.shape),
            "cropped_shape": list(cropped.shape),
            "coverage": cov_info,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed_s": round(time.time() - t0, 1)}


def _crop_to_file(data, header, top, bottom, left, right, out_path) -> tuple:
    """Crop data[...,top:bottom,left:right], fix CRPIX, write FITS. Returns shape."""
    import numpy as np
    from astropy.io import fits as afits
    cropped = data[:, top:bottom, left:right] if data.ndim == 3 else data[top:bottom, left:right]
    hdr = header.copy()
    if "CRPIX1" in hdr:
        hdr["CRPIX1"] = hdr["CRPIX1"] - left
    if "CRPIX2" in hdr:
        hdr["CRPIX2"] = hdr["CRPIX2"] - top
    afits.PrimaryHDU(data=np.ascontiguousarray(cropped), header=hdr).writeto(
        str(out_path), overwrite=True)
    return cropped.shape


def _artifact_trim_bounds(data, low_k: float = 0.55, hi_k: float = 0.18,
                          noise_k: float = 1.6, clean_run: float = 0.02,
                          cap: float = 0.35):
    """Edge-artifact crop bounds from the stretched-luminance row/col profiles.

    The crop step's real job is removing edge stacking artifacts — registration/
    dither falloff, low-coverage grainy borders, black no-data bands — while
    keeping the FULL target (not the strict inscribed rectangle, which clips the
    subject on tilted mosaics, nor canonical reproject, which over-sizes bordered
    targets). We STF-stretch a luminance, then for each edge scan inward and trim
    while the band is anomalous by EITHER signal:
      - brightness: median below ``low_k``·plateau (falloff/black band) or above
        plateau by ``hi_k`` (residual gradient), or
      - noise: high-frequency residual above ``noise_k``·interior — catches grainy
        low-frame-coverage border strips whose median sits near the interior.
    Stops after a clean run, capped per-edge at ``cap`` so the subject is never
    eaten. Coverage-map-independent (works on linear stacks and manual XISF
    masters alike). See [[feedback-crop-edge-artifacts]].

    Returns (top, bottom, left, right) as slice bounds (top/left inclusive,
    bottom/right exclusive), matching ``_crop_to_file``.
    """
    import numpy as np
    from scipy.ndimage import median_filter, uniform_filter
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 3:
        rgb = np.moveaxis(arr, 0, -1) if arr.shape[0] == 3 else arr
        if rgb.shape[-1] == 3:
            lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        else:
            lum = rgb[..., 0]
    else:
        lum = arr
    lo = float(np.percentile(lum, 25)); hi = float(np.percentile(lum, 99.7))
    L = np.clip((lum - lo) / (hi - lo + 1e-6), 0.0, 1.0) ** 0.45
    h, w = L.shape
    nm = uniform_filter(np.abs(L - median_filter(L, size=3)), size=5)
    cy = slice(int(0.25 * h), int(0.75 * h)); cx = slice(int(0.25 * w), int(0.75 * w))
    rowp = np.median(L[:, cx], axis=1); colp = np.median(L[cy, :], axis=0)
    rown = np.median(nm[:, cx], axis=1); coln = np.median(nm[cy, :], axis=0)

    def _edge(prof, noise, n):
        plo, phi = int(0.3 * n), int(0.7 * n)
        plat = float(np.median(prof[plo:phi]))
        mad = 1.4826 * float(np.median(np.abs(prof[plo:phi] - plat))) + 1e-6
        nplat = float(np.median(noise[plo:phi]))
        lowthr = max(low_k * plat, plat - 6 * mad)
        hithr = plat + max(hi_k, 5 * mad)
        nthr = noise_k * nplat
        run = max(3, int(clean_run * n)); capn = int(cap * n)

        def _anom(i):
            return (prof[i] < lowthr) or (prof[i] > hithr) or (noise[i] > nthr)

        def _scan(idxs):
            clean = 0; last = idxs[0]
            for i in idxs:
                if _anom(i):
                    clean = 0; last = i
                else:
                    clean += 1
                    if clean >= run:
                        break
                if abs(i - idxs[0]) >= capn:
                    break
            return last

        s = _scan(range(n))
        a = s + 1 if s > 0 else (1 if _anom(0) else 0)
        e = _scan(range(n - 1, -1, -1))
        b = e if e < n - 1 else (n - 1 if _anom(n - 1) else n)
        return max(0, a), min(n, b)

    top, bottom = _edge(rowp, rown, h)
    left, right = _edge(colp, coln, w)
    # Guard against a degenerate/inverted box (e.g. a near-black or all-noise frame):
    # never trim below a quarter of either dimension — fall back to the full frame.
    if bottom - top < int(0.25 * h) or right - left < int(0.25 * w):
        return 0, h, 0, w
    return int(top), int(bottom), int(left), int(right)


def crop_multi(
    input_path: str | Path,
    output_path: str | Path,
    target: str = "",
    coverage_path: str = "",
    drizzled: bool | None = None,
    aggressiveness: str = "balanced",
    **_kwargs,
) -> dict:
    """Multi-candidate crop generator.

    Generates up to five candidates, each written beside output_path:
      - artifact     — stretched-luminance edge-profile + noise trim (PREFERRED)
      - canonical    — reproject onto the fixed per-target WCS (folio targets only)
      - coverage     — largest rect with ≥80% frame coverage (needs coverage_path)
      - intersection — largest rect covered by all frames (needs coverage_path)
      - lir          — existing coverage-mask largest inscribed rectangle (always)

    Default selection (used when no manual review intervenes): prefer the
    artifact-trim candidate, else canonical, else coverage → intersection → lir.
    The candidate FITS files are left on disk so the auto_process crop step can
    open a manual review and let the user pick / draw their own.
    """
    t0 = time.time()
    try:
        import numpy as np
        import shutil as _sh
        from astropy.io import fits as afits
        input_path = Path(input_path)
        output_path = Path(output_path)
        cand_dir = output_path.parent

        with afits.open(input_path) as hdul:
            data = hdul[0].data.copy().astype(np.float32)
            header = hdul[0].header.copy()
        if data.ndim not in (2, 3):
            return {"ok": False, "error": f"Unexpected data ndim: {data.ndim}",
                    "elapsed_s": round(time.time() - t0, 1)}
        h = data.shape[-2]
        w = data.shape[-1]

        # Drizzle detection from the solved plate scale (1.45"/px drizzled vs 2.9 native).
        if drizzled is None:
            _cd = header.get("CDELT2") or header.get("CD2_2")
            try:
                _arcsec = abs(float(_cd)) * 3600.0 if _cd else None
            except Exception:
                _arcsec = None
            drizzled = bool(_arcsec is not None and _arcsec < 2.1)

        candidates: dict = {}

        # (e) artifact trim — PREFERRED. Stretched-luminance edge profile + noise
        # trim: removes ragged/black/grainy edge stacking artifacts while keeping
        # the full target. Coverage-map-independent. See [[feedback-crop-edge-artifacts]].
        try:
            at, ab, al, ar = _artifact_trim_bounds(data)
            art_path = cand_dir / "auto_crop_artifact.fit"
            _crop_to_file(data, header, at, ab, al, ar, art_path)
            candidates["artifact"] = {"path": art_path, "area": max(0, ab - at) * max(0, ar - al),
                                      "short": min(max(0, ab - at), max(0, ar - al)), "kind": "crop"}
        except Exception as _ae:
            logger.warning(f"[crop_multi] artifact-trim candidate failed: {_ae}")

        # (d) LIR — always available
        try:
            t, b, l, r, _info = _detect_crop_bounds_coverage(data, aggressiveness=aggressiveness)
            lir_path = cand_dir / "auto_crop_lir.fit"
            _crop_to_file(data, header, t, b, l, r, lir_path)
            candidates["lir"] = {"path": lir_path, "area": max(0, b - t) * max(0, r - l),
                                 "short": min(max(0, b - t), max(0, r - l)), "kind": "crop"}
        except Exception as _le:
            logger.warning(f"[crop_multi] LIR candidate failed: {_le}")

        # (b)(c) coverage ≥80% + intersection — from the stack coverage map
        if coverage_path and Path(coverage_path).exists():
            try:
                from nas_server.canonical_frame import coverage_crop_bounds
                with afits.open(coverage_path) as ch:
                    cov = np.asarray(ch[0].data)
                    nfrm = int(ch[0].header.get("COVNFRM", 0)) or (int(cov.max()) if cov.size else 0)
                if cov.shape == (h, w) and nfrm > 0:
                    for name, frac in (("coverage", 0.80), ("intersection", 0.999)):
                        cb = coverage_crop_bounds(cov, nfrm, frac)
                        if cb:
                            ct, cbm, cl, cr, _ci = cb
                            cpath = cand_dir / f"auto_crop_{name}.fit"
                            _crop_to_file(data, header, ct, cbm, cl, cr, cpath)
                            candidates[name] = {"path": cpath,
                                                "area": max(0, cbm - ct) * max(0, cr - cl),
                                                "short": min(max(0, cbm - ct), max(0, cr - cl)),
                                                "kind": "crop"}
                else:
                    logger.warning(f"[crop_multi] coverage map shape {cov.shape} != "
                                   f"data {(h, w)} or nfrm={nfrm} — skipping coverage candidates")
            except Exception as _ce:
                logger.warning(f"[crop_multi] coverage candidates failed: {_ce}")

        # (a) canonical — reproject onto the fixed per-target frame
        canonical_ok = False
        try:
            from nas_server.canonical_frame import canonical_target_wcs, reproject_to_canonical
            cw = canonical_target_wcs(target, drizzled=bool(drizzled))
            if cw:
                wcs, shape = cw
                canon_path = cand_dir / "auto_crop_canonical.fit"
                if reproject_to_canonical(str(input_path), wcs, shape, str(canon_path)):
                    candidates["canonical"] = {"path": canon_path,
                                               "area": int(shape[0] * shape[1]),
                                               "short": int(min(shape[0], shape[1])),
                                               "kind": "reproject"}
                    canonical_ok = True
        except Exception as _ke:
            logger.warning(f"[crop_multi] canonical candidate failed: {_ke}")

        if not candidates:
            logger.info("[crop_multi] no candidates generated — falling back to plain auto crop")
            return crop(input_path, output_path, method="auto",
                        aggressiveness=aggressiveness, target=target)

        # --- Selection ---
        # The crop's real job is removing edge stacking artifacts while keeping the
        # full target, so the artifact-trim candidate is preferred. Canonical is the
        # wrong default for bordered/mosaic targets (over-sizes onto a fixed WCS,
        # re-introduces noise borders) and the strict LIR clips the subject on tilted
        # mosaics. See [[feedback-crop-edge-artifacts]]. Canonical/coverage/lir are
        # still generated for the record/video; they're only chosen if artifact is
        # unavailable, falling back to the prior canonical-preferred logic.
        chosen = None
        if "artifact" in candidates:
            chosen = "artifact"
        elif canonical_ok:
            chosen = "canonical"
        else:
            for name in ("coverage", "intersection", "lir"):
                if name in candidates:
                    chosen = name
                    break

        # --- Native-resolution floor (don't over-crop small targets) ---
        # The S50 (2.9"/px native) lacks the resolution to survive a tight crop:
        # a small-angular-size target sized from its catalog angular size collapses
        # the canonical box far below native pixel count (M 57 -> ~76px, M 109 ->
        # ~442px) and renders as a soft thumbnail. Never keep a candidate whose
        # short side falls below ~45% of the native frame's short side; fall back
        # to the largest-area candidate that clears the floor (the LIR/full-frame),
        # preserving subject pixels over tight framing.
        native_short = min(h, w)
        res_floor = max(900, int(0.45 * native_short))
        chosen_short = int(candidates[chosen].get("short", 0) or 0)
        if chosen_short and chosen_short < res_floor:
            above = [n for n in candidates if int(candidates[n].get("short", 0) or 0) >= res_floor]
            pool = above if above else list(candidates)
            alt = max(pool, key=lambda n: candidates[n]["area"])
            if alt != chosen:
                logger.info(f"[crop_multi] '{chosen}' short={chosen_short}px below "
                            f"native-res floor {res_floor}px (native short={native_short}) "
                            f"— using '{alt}' to preserve resolution")
                chosen = alt

        _sh.copy2(str(candidates[chosen]["path"]), str(output_path))
        elapsed = round(time.time() - t0, 1)
        logger.info(f"[crop_multi] {input_path.name}: chose '{chosen}' from "
                    f"{list(candidates)} (drizzled={drizzled}) in {elapsed}s")
        return {
            "ok": True,
            "output_path": str(output_path),
            "chosen": chosen,
            "candidates": {k: {"area": v["area"], "short": v.get("short"),
                               "kind": v["kind"], "path": str(v["path"])}
                           for k, v in candidates.items()},
            "res_floor": res_floor,
            "native_short": native_short,
            "drizzled": bool(drizzled),
            "elapsed_s": elapsed,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed_s": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------
# Starless / star-split workflow
# ---------------------------------------------------------------------------

def remove_stars_split(
    input_path: str | Path,
    starless_path: str | Path,
    stars_path: str | Path,
    mode: str = "unscreen",
    use_gpu: bool = True,
    chunk_size: int = 512,
) -> dict:
    """
    Split an image into a starless FITS and a stars-only FITS.

    Stars are extracted as: stars = screen_inverse(original, starless)
    i.e. stars = 1 - (1 - original) / (1 - starless), clipped to [0, 1].
    This is the correct inverse of screen blend used to recombine them.

    mode: 'unscreen' | 'additive'
    Returns {"ok": True, "starless_path": ..., "stars_path": ..., "elapsed_s": ...}
    """
    t0 = time.time()
    try:
        from setiastro.saspro.remove_stars import darkstar_starless_from_array
        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1)

        _noop = lambda *a, **kw: None
        starless, _, _ok = darkstar_starless_from_array(
            data, use_gpu=use_gpu, chunk_size=chunk_size,
            mode=mode, output_stars_only=False,
            status_cb=_noop, progress_cb=_noop,
        )
        # Compute stars-only via screen inverse so screen blend reconstructs original
        eps = 1e-6
        denom = np.clip(1.0 - starless.astype(np.float32), eps, 1.0)
        stars = np.clip(1.0 - (1.0 - data.astype(np.float32)) / denom, 0.0, 1.0)

        for arr, path in [(starless, starless_path), (stars, stars_path)]:
            out = np.moveaxis(arr, -1, 0) if arr.ndim == 3 else arr
            _save_fits(np.clip(out, 0.0, 1.0), hdr, path)

        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] remove_stars_split done in {elapsed}s")
        return {"ok": True, "starless_path": str(starless_path),
                "stars_path": str(stars_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] remove_stars_split exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def star_stretch(
    input_path: str | Path,
    output_path: str | Path,
    stretch_factor: float = 1.0,
    saturation: float = 1.2,
    do_scnr: bool = True,
    scnr_amount: float = 0.9,
    gamma: float = 1.0,
) -> dict:
    """
    Apply SASpro Star Stretch to a stars-only FITS.

    stretch_factor: 0.0–8.0 maps to the SASpro slider (default 1.0 is gentle)
    saturation: multiplier for colour saturation after stretch (1.0 = unchanged)
    do_scnr: apply Average Neutral SCNR to remove green cast
    scnr_amount: SCNR strength 0–1 (should match the amount used on the starless image)
    gamma: brightness curves adjustment — values < 1.0 brighten midtones (e.g. 0.85)
    """
    t0 = time.time()
    try:
        from setiastro.saspro.star_stretch import _saturation_boost
        try:
            from setiastro.saspro.legacy.numba_utils import applyPixelMath_numba
        except Exception:
            def applyPixelMath_numba(img: np.ndarray, factor: float) -> np.ndarray:
                f = 3.0 ** factor
                return np.clip(img * f / (img * f + 1.0), 0.0, 1.0)

        data, hdr = _load_fits(input_path)
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.moveaxis(data, 0, -1).astype(np.float32)
        else:
            data = data.astype(np.float32)

        # Auto-scale: if the data is crushed near zero (e.g. PI uint32 StarXT output
        # normalised by 4294967295), rescale so the 99.9th percentile = 1.0.
        # This ensures stretch_factor has the same effect regardless of input bit depth.
        p999 = float(np.percentile(data, 99.9))
        if 0 < p999 < 0.1:
            data = np.clip(data / p999, 0.0, 1.0)

        out = applyPixelMath_numba(data, float(stretch_factor))

        if data.ndim == 3 and data.shape[-1] == 3 and abs(saturation - 1.0) > 1e-6:
            out = _saturation_boost(out, saturation)

        if do_scnr and out.ndim == 3 and out.shape[-1] == 3:
            r = out[..., 0]; g = out[..., 1]; b = out[..., 2]
            neutral = np.minimum(g, 0.5 * (r + b))
            out[..., 1] = (1.0 - scnr_amount) * g + scnr_amount * neutral

        if gamma != 1.0:
            out = np.power(np.clip(out, 0.0, 1.0), float(gamma))

        result = np.moveaxis(out, -1, 0) if out.ndim == 3 else out
        _save_fits(np.clip(result, 0.0, 1.0), hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] star_stretch done in {elapsed}s: {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] star_stretch exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def _floor_star_layer(stars: np.ndarray, k: float) -> tuple[np.ndarray, dict]:
    """Black-point the inter-star background of a stretched star layer.

    stretch_stars amplifies the near-black sky between stars with no floor, so
    screen-combining it injects that grain straight into the final background
    (screen ≈ additive for small values). We push the inter-star sky to ~0 with a
    robust median + k·MAD black point, soft-rescaling so bright star cores (which
    sit far above the floor) are preserved. Computed per-channel on color data so
    residual chroma speckle is removed too.
    """
    if k <= 0:
        return stars, {"applied": False}
    out = stars.copy()
    # Locate a length-3 channel axis (color); else treat as mono (single pass).
    ch_axis = next((ax for ax, n in enumerate(stars.shape) if n == 3), None)
    if ch_axis is None:
        chans = [(Ellipsis,)]
    else:
        chans = [tuple(slice(None) if a != ch_axis else c for a in range(stars.ndim))
                 for c in range(3)]
    floors = []
    for sl in chans:
        ch = out[sl] if sl != (Ellipsis,) else out
        med = float(np.median(ch))
        mad = 1.4826 * float(np.median(np.abs(ch - med)))
        floor = med + k * mad
        denom = max(1.0 - floor, 1e-6)
        rescaled = np.clip((ch - floor) / denom, 0.0, 1.0)
        if sl == (Ellipsis,):
            out = rescaled
        else:
            out[sl] = rescaled
        floors.append(round(floor, 5))
    return out.astype(np.float32), {"applied": True, "k": k, "floors": floors}


def combine_stars_screen(
    starless_path: str | Path,
    stars_path: str | Path,
    output_path: str | Path,
    star_floor_k: float = 2.0,
) -> dict:
    """
    Recombine a stretched starless image with a stretched stars-only image
    using screen blend: result = 1 - (1 - starless) * (1 - stars)

    Both inputs must be in 0–1 float range (i.e. already stretched).

    star_floor_k black-points the star layer's inter-star background at
    median + k·MAD before screening, suppressing grain the star stretch
    amplified. Set <= 0 to disable.
    """
    t0 = time.time()
    try:
        starless, hdr = _load_fits(starless_path)
        stars, _ = _load_fits(stars_path)
        starless = starless.astype(np.float32)
        stars = stars.astype(np.float32)
        stars, floor_info = _floor_star_layer(stars, star_floor_k)
        combined = np.clip(1.0 - (1.0 - starless) * (1.0 - stars), 0.0, 1.0)
        _save_fits(combined, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(
            f"[seti_astro] combine_stars_screen done in {elapsed}s "
            f"(star_floor={floor_info}): {output_path}"
        )
        return {
            "ok": True,
            "output_path": str(output_path),
            "elapsed_s": elapsed,
            "star_floor": floor_info,
        }
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] combine_stars_screen exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def green_cap_masked(input_path: str | Path, output_path: str | Path,
                     amount: float = 0.7, min_gr: float = 1.02,
                     lo_sigma: float = 2.0, hi_sigma: float = 8.0,
                     feather: float = 4.0, **_kwargs) -> dict:
    """
    Remove residual green excess from the GALAXY SIGNAL only, in linear space.

    SPCC's linked white balance neutralises the sky perfectly but can leave the
    object signal ~10% green-heavy on S50 OSC data (M 81 trace: sky-subtracted
    galaxy G/R 1.04–1.12 post-SPCC where an Sab bulge should be ~0.9), which the
    nonlinear stretch then amplifies into a lime cast. Unlike SCNR this operates
    on SKY-SUBTRACTED signal inside a feathered luminance mask, so the
    SPCC-neutral sky pedestal is byte-identical on output — the IC 434
    sky-crush failure mode of full-frame SCNR cannot occur.

    Self-gating: measures the masked galaxy-signal G/R first and passes through
    unchanged when it is already <= min_gr (no excess to remove).
    """
    t0 = time.time()
    try:
        from scipy import ndimage as _ndi  # noqa: PLC0415
        data, hdr = _load_fits(input_path)
        data = np.nan_to_num(data.astype(np.float32))
        if data.ndim != 3 or data.shape[0] != 3:
            logger.info("[seti_astro] green_cap_masked: non-RGB input, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "non-rgb",
                    "elapsed_s": int(time.time() - t0)}
        R, G, B = data[0], data[1], data[2]
        L = 0.2126 * R + 0.7152 * G + 0.0722 * B

        m = L.ravel().copy()
        for _ in range(5):
            med = float(np.median(m)); sd = float(m.std())
            m = m[m < med + 2.5 * sd]
        sky_l = float(np.median(m)); sig = float(m.std())
        sky_mask = L < sky_l + 2.0 * sig
        sky = np.array([float(np.median(c[sky_mask])) for c in (R, G, B)],
                       dtype=np.float32)

        lo = sky_l + lo_sigma * sig
        hi = sky_l + hi_sigma * sig
        M = np.clip((L - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        M = M * M * (3.0 - 2.0 * M)
        M = _ndi.gaussian_filter(M, feather)

        core = M > 0.6
        if not core.any():
            logger.info("[seti_astro] green_cap_masked: no signal mask, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "no-signal",
                    "elapsed_s": int(time.time() - t0)}
        sig_med = np.array([float(np.median(c[core])) for c in (R, G, B)]) - sky
        gr_before = float(sig_med[1] / max(sig_med[0], 1e-9))
        if gr_before <= min_gr:
            logger.info(f"[seti_astro] green_cap_masked: signal G/R {gr_before:.3f} "
                        f"<= {min_gr} — no green excess, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "no-excess",
                    "gr_before": gr_before, "elapsed_s": int(time.time() - t0)}

        g_sig = G - sky[1]
        cap = np.maximum((R - sky[0]) + (B - sky[2]), 0.0) * 0.5
        excess = np.maximum(g_sig - cap, 0.0)
        out = data.copy()
        out[1] = G - (amount * M * excess).astype(np.float32)
        out = np.clip(out, 0.0, 1.0)

        sig_after = np.array([float(np.median(c[core])) for c in
                              (out[0], out[1], out[2])]) - sky
        gr_after = float(sig_after[1] / max(sig_after[0], 1e-9))
        _save_fits(out, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] green_cap_masked done in {elapsed}s "
                    f"(amount={amount}; signal G/R {gr_before:.3f}->{gr_after:.3f}; "
                    f"galcov {100 * float(core.mean()):.1f}%): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "amount": amount, "gr_before": gr_before, "gr_after": gr_after,
                "galaxy_coverage": float(core.mean())}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] green_cap_masked exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def sky_mute_masked(input_path: str | Path, output_path: str | Path,
                    lo_sigma: float = 1.5, hi_sigma: float = 5.0,
                    bp_sigma: float = 1.0, feather: float = 3.0,
                    dilate: int = 3, **_kwargs) -> dict:
    """
    Darken the sky background while leaving the extended galaxy untouched.

    Companion to the galaxy stretch directive (workflow 1.7.0): the stretch is chosen
    on galaxy-detail retention and deliberately leaves the sky bright; this step then
    mutes that sky. An auto-tuned luminance mask protects everything above the sky
    noise floor and applies a black-point lift only to the sky pixels, so faint galaxy
    structure (arms, tidal bridges) is preserved while the background goes dark.

    All thresholds are RELATIVE to the image's own sigma-clipped sky statistics, so it
    self-tunes per target (a sky-dominated frame and a galaxy-filling frame both work):
      mask ramps from sky+lo_sigma·σ (just above noise) to sky+hi_sigma·σ (solid galaxy),
      smoothstep + gaussian feather + slight dilate; sky black-point = sky-bp_sigma·σ.

    Only meaningful on a 3-channel stretched RGB frame; mono / 2-D input is passed
    through unchanged.
    """
    t0 = time.time()
    try:
        from scipy import ndimage as _ndi  # noqa: PLC0415
        data, hdr = _load_fits(input_path)
        data = data.astype(np.float32)
        if data.ndim != 3 or data.shape[0] != 3:
            logger.info("[seti_astro] sky_mute_masked: non-RGB input, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "non-rgb",
                    "elapsed_s": int(time.time() - t0)}
        R, G, B = data[0], data[1], data[2]
        L = 0.2126 * R + 0.7152 * G + 0.0722 * B

        # Sigma-clipped sky estimate (robust whether the frame is sky- or galaxy-dominated)
        m = L.ravel().copy()
        for _ in range(5):
            med = float(np.median(m)); sd = float(m.std())
            m = m[m < med + 2.5 * sd]
        sky = float(np.median(m)); sig = float(m.std())

        lo = sky + lo_sigma * sig
        hi = sky + hi_sigma * sig
        M = np.clip((L - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        M = M * M * (3.0 - 2.0 * M)                       # smoothstep
        M = _ndi.gaussian_filter(M, feather)
        if dilate and dilate > 0:
            M = np.clip(_ndi.maximum_filter(M, int(dilate)) * 0.5 + M * 0.5, 0.0, 1.0)

        bp = max(sky - bp_sigma * sig, 0.0)               # black-point just below sky
        denom = max(1.0 - bp, 1e-6)
        dark = np.clip((data - bp) / denom, 0.0, 1.0)     # broadcast over channels
        out = np.clip(M * data + (1.0 - M) * dark, 0.0, 1.0).astype(np.float32)

        _save_fits(out, hdr, output_path)
        # Report the actual sky-darkening achieved (median luminance in the masked-out sky)
        sky_mask = M < 0.1
        Lo = 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
        sky_before = float(np.median(L[sky_mask])) if sky_mask.any() else 0.0
        sky_after = float(np.median(Lo[sky_mask])) if sky_mask.any() else 0.0
        galcov = float((M > 0.7).mean())
        elapsed = int(time.time() - t0)
        logger.info(
            f"[seti_astro] sky_mute_masked done in {elapsed}s "
            f"(sky {sky:.3f}±{sig:.3f}; sky L {sky_before:.3f}->{sky_after:.3f}; "
            f"galcov {100*galcov:.1f}%): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "sky_level": sky, "sky_sigma": sig,
                "sky_before": sky_before, "sky_after": sky_after, "galaxy_coverage": galcov}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] sky_mute_masked exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


def sky_green_rebalance(input_path: str | Path, output_path: str | Path,
                        amount: float = 0.85, min_gr: float = 1.03,
                        lo_sigma: float = 1.5, hi_sigma: float = 5.0,
                        feather: float = 3.0, smooth_sigma: float = 6.0,
                        **_kwargs) -> dict:
    """
    Neutralize residual green excess in the SKY only — the SPCC-safe SCNR substitute.

    The SPCC-success rule skips SCNR (full-frame SCNR crushed IC 434 — see
    [[feedback-linked-color]]), so a small post-SPCC sky green tint goes unpolished
    (M 31 sky G/R 1.12, M 42 1.06). This caps green at max(R, B) inside an inverted
    feathered luminance mask: only pixels at/below the sky noise floor are touched,
    so object signal colour and SPCC-calibrated star colours pass through unchanged.
    The max(R, B) cap (not SCNR's (R+B)/2) means green is only trimmed down to the
    dominant other channel — it can neutralize a green tint but never invert it
    into magenta (the (R+B)/2 cap drove M 31's sky to G/R 0.84 when sky B was weak).
    The excess field is computed on gaussian-smoothed channels (smooth_sigma) so the
    SYSTEMATIC tint is subtracted rather than one-sidedly clipping green noise — a
    pointwise cap clamps only the high tail of the green noise and drags the sky
    median below neutral (M 31 went 1.06 → 0.93 with the pointwise version).

    Self-gating: measures the sky-masked G/R first and passes through unchanged
    when it is already <= min_gr (no excess to remove).
    """
    t0 = time.time()
    try:
        from scipy import ndimage as _ndi  # noqa: PLC0415
        data, hdr = _load_fits(input_path)
        data = np.nan_to_num(data.astype(np.float32))
        if data.ndim != 3 or data.shape[0] != 3:
            logger.info("[seti_astro] sky_green_rebalance: non-RGB input, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "non-rgb",
                    "elapsed_s": int(time.time() - t0)}
        R, G, B = data[0], data[1], data[2]
        L = 0.2126 * R + 0.7152 * G + 0.0722 * B

        # Sigma-clipped sky estimate (same recipe as sky_mute_masked)
        m = L.ravel().copy()
        for _ in range(5):
            med = float(np.median(m)); sd = float(m.std())
            m = m[m < med + 2.5 * sd]
        sky_l = float(np.median(m)); sig = float(m.std())

        lo = sky_l + lo_sigma * sig
        hi = sky_l + hi_sigma * sig
        M = np.clip((L - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        M = M * M * (3.0 - 2.0 * M)                       # smoothstep
        M = _ndi.gaussian_filter(M, feather)
        W = (1.0 - M).astype(np.float32)                  # sky weight

        sky_core = W > 0.9
        if not sky_core.any():
            logger.info("[seti_astro] sky_green_rebalance: no sky region, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "no-sky",
                    "elapsed_s": int(time.time() - t0)}
        r_med = float(np.median(R[sky_core])); g_med = float(np.median(G[sky_core]))
        gr_before = g_med / max(r_med, 1e-9)

        # Signal-green cap (workflow 1.22.0, batch evals 07-07 P3 + 07-13 P3): the
        # sky-only design correctly leaves OBJECT colour alone, but that means a
        # genuine green cast in the SIGNAL (colour_boost/curves amplification —
        # NGC 281: G p99 0.882 vs R p99 0.766) sails through untouched while this
        # step reports "no excess". When the caller opts in (allow_signal_green —
        # auto_process gates it on the folio colour prior, so legitimately
        # teal/OIII-dominant targets like Thor's Helmet are NEVER capped), measure
        # the signal-region p99 lead and, above signal_trigger, cap G toward
        # max(R, B) inside the SIGNAL mask (M) with the same smoothed-excess /
        # never-invert-to-magenta machinery as the sky pass. Bounded amount.
        allow_signal_green = bool(_kwargs.get("allow_signal_green", False))
        signal_trigger = float(_kwargs.get("signal_trigger", 0.06))
        signal_amount = float(_kwargs.get("signal_amount", 0.6))
        sig_mask = M > 0.5
        signal_lead = 0.0
        if sig_mask.any():
            gp = float(np.percentile(G[sig_mask], 99))
            rp = float(np.percentile(R[sig_mask], 99))
            bp = float(np.percentile(B[sig_mask], 99))
            signal_lead = (gp - max(rp, bp)) / max(gp, 1e-9)
        do_signal = allow_signal_green and signal_lead > signal_trigger

        if gr_before <= min_gr and not do_signal:
            logger.info(f"[seti_astro] sky_green_rebalance: sky G/R {gr_before:.3f} "
                        f"<= {min_gr}, signal lead {signal_lead:.3f} — no green "
                        f"excess, passing through")
            _save_fits(data, hdr, output_path)
            return {"ok": True, "output_path": str(output_path), "skipped": "no-excess",
                    "gr_before": gr_before, "signal_lead": round(signal_lead, 4),
                    "elapsed_s": int(time.time() - t0)}

        Rs = _ndi.gaussian_filter(R, smooth_sigma)
        Gs = _ndi.gaussian_filter(G, smooth_sigma)
        Bs = _ndi.gaussian_filter(B, smooth_sigma)
        excess = np.maximum(Gs - np.maximum(Rs, Bs), 0.0)
        out = data.copy()
        _weight = (amount * W) if gr_before > min_gr else np.zeros_like(W)
        if do_signal:
            _weight = _weight + signal_amount * M.astype(np.float32)
            logger.info(f"[seti_astro] sky_green_rebalance: signal-green cap ON "
                        f"(lead {signal_lead:.3f} > {signal_trigger}, "
                        f"amount {signal_amount})")
        out[1] = np.clip(G - (np.clip(_weight, 0.0, 1.0) * excess), 0.0, 1.0
                         ).astype(np.float32)

        gr_after = float(np.median(out[1][sky_core])) / max(r_med, 1e-9)
        _save_fits(out, hdr, output_path)
        elapsed = int(time.time() - t0)
        logger.info(f"[seti_astro] sky_green_rebalance done in {elapsed}s "
                    f"(amount={amount}; sky G/R {gr_before:.3f}->{gr_after:.3f}; "
                    f"signal_lead={signal_lead:.3f} signal_cap={do_signal}; "
                    f"skycov {100 * float(sky_core.mean()):.1f}%): {output_path}")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "amount": amount, "gr_before": gr_before, "gr_after": gr_after,
                "signal_lead": round(signal_lead, 4), "signal_cap": do_signal,
                "sky_coverage": float(sky_core.mean())}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.error(f"[seti_astro] sky_green_rebalance exception: {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}


# SeeStar S50 fixed optics — 250mm focal, IMX462 sensor. Authoritative plate scale:
# 2.9"/px native, 1.45"/px at 2x drizzle (see reference_s50_pixel_scale memory).
_S50_FOCALLEN_MM = 250.0
_S50_SCALE_NATIVE_ASPP = 2.9
_S50_SCALE_DRIZZLE_ASPP = 1.45


def _ensure_plate_solve_scale_hints(input_path) -> bool:
    """Seed S50 scale metadata so SPCC's in-PI re-solver can run on a master that lacks it.

    The re-solver (plateSolveForSPCC in pi_postprocess.js) fires whenever the image has no
    WCS, but ImageSolver needs an initial resolution within ~2x of correct, which it derives
    from FOCALLEN + XPIXSZ. Normal pipeline stacks (Siril/PI) keep those; crop strips only
    the WCS keywords, so the re-solver succeeds. But a manual/external master (e.g. a
    hand-made .xisf) converted to FITS keeps RA/DEC pointing yet loses FOCALLEN/XPIXSZ, so
    the solve has no scale seed and silently fails -> SPCC runs unsolved -> dead color.

    When the input has no WCS AND no scale, inject the known fixed S50 values. Seed at the
    geometric mean of native/drizzled (~2.05"/px) so a single value lands within ~1.41x of
    both cases (inside the 2x tolerance) without needing to know whether the master was
    drizzled. Returns True if hints were injected.
    """
    import math
    from astropy.io import fits as _fits
    try:
        with _fits.open(str(input_path), mode="update") as hdul:
            hdr = hdul[0].header
            if "CTYPE1" in hdr:
                return False  # already solved — re-solver will skip
            if "FOCALLEN" in hdr and "XPIXSZ" in hdr:
                return False  # has a scale seed already — nothing to do
            seed_aspp = math.sqrt(_S50_SCALE_NATIVE_ASPP * _S50_SCALE_DRIZZLE_ASPP)
            xpixsz_um = seed_aspp * _S50_FOCALLEN_MM / 206.265
            hdr["FOCALLEN"] = (_S50_FOCALLEN_MM, "S50 (injected: SPCC plate-solve scale seed)")
            hdr["XPIXSZ"] = (xpixsz_um, "S50 (injected: SPCC plate-solve scale seed)")
            hdr["YPIXSZ"] = (xpixsz_um, "S50 (injected: SPCC plate-solve scale seed)")
            hdul.flush()
        logger.info(
            f"[seti_astro] SPCC: input lacked WCS+scale; injected S50 FOCALLEN="
            f"{_S50_FOCALLEN_MM}mm XPIXSZ={xpixsz_um:.3f}um (~{seed_aspp:.2f}\"/px seed) "
            f"so the in-PI plate-solver can run")
        return True
    except Exception as _e:
        logger.warning(f"[seti_astro] SPCC: could not inject scale hints into {input_path}: {_e}")
        return False


def _preserve_celestial_wcs(src_path: str | Path, dst_path: str | Path) -> bool:
    """Copy the celestial WCS cards from src into dst when dst is missing them.

    The PI color-calibration path (run_postprocess → PI) drops the astropy-readable
    WCS from its output. The preview renderer flips the image to north-up from the
    WCS (CDELT2 sign), so a WCS-less color_calibration output renders VERTICALLY
    FLIPPED relative to the WCS-bearing upstream stages (background_extraction) —
    the "orientation changes after color calibration" bug. Re-injecting the input
    WCS keeps orientation consistent through the pipeline and restores the plate
    solve for any downstream WCS-dependent step. No-op if dst already has a WCS.
    """
    try:
        from astropy.io import fits as _fits
        dh = _fits.getheader(str(dst_path), memmap=False)
        if "CTYPE1" in dh:            # already has a WCS — never clobber a real one
            return False
        sh = _fits.getheader(str(src_path), memmap=False)
        if "CTYPE1" not in sh:        # nothing to copy
            return False
        _PFX = ("CTYPE", "CRVAL", "CRPIX", "CD1_", "CD2_", "PC1_", "PC2_",
                "CDELT", "CROTA", "CUNIT")
        _EXACT = {"WCSAXES", "LONPOLE", "LATPOLE", "EQUINOX", "RADESYS", "PLTSOLVD"}
        cards = [k for k in sh.keys()
                 if k in _EXACT or any(k.startswith(p) for p in _PFX)]
        if not cards:
            return False
        with _fits.open(str(dst_path), mode="update", memmap=False) as h:
            for k in cards:
                h[0].header[k] = sh[k]
            h.flush()
        logger.info(f"[seti_astro] re-injected {len(cards)} WCS cards into "
                    f"{Path(dst_path).name} (color-cal dropped them)")
        return True
    except Exception as e:
        logger.warning(f"[seti_astro] WCS preserve failed: {e}")
        return False


def spcc(
    input_path: str | Path,
    output_path: str | Path,
    spcc_lp_filter: bool = False,
    target: str = "",
    allow_sssc: bool = False,
    **_kwargs,
) -> dict:
    """Run PixInsight SPCC with automatic fallback to ColorCalibration if SPCC fails.

    SPCC can fail transiently (Gaia DB query bailing under VM load) even with a
    valid WCS + catalog, so we retry once before degrading. When SPCC ultimately
    fails we drop a `.spcc_failed` sentinel in the run dir — the stretch step reads
    it and switches to an UNLINKED (per-channel) stretch to neutralise the green
    cast that SPCC would otherwise have removed. A successful SPCC clears it.
    """
    from nas_server.pixinsight import run_postprocess
    from nas_server.config import settings as _cfg_settings
    t0 = time.time()

    _sentinel = Path(output_path).parent / ".spcc_failed"

    # LP / dual-band data: SSSC only when the caller opts in (1.16.0). SSSC solves
    # the system response from Gaia-XP star spectra — spectrally faithful, which is
    # what the NBN/palette branch needs (workflow 1.9.0). But on the STANDARD chain
    # that faithfulness renders dual-band nebulae green-teal (M 42 bright-nebula
    # G/R 1.21 vs PI CC's 0.71 = the Henry-approved 8.2 look), so the standard
    # chain passes allow_sssc=False and goes straight to the PI SPCC/CC path.
    # Any SSSC failure still falls through to the legacy PI path unchanged.
    # (Drizzle gate removed 2026-07-03: the 'drizzle photometry bias' was actually
    # NEBULOSITY contamination of star backgrounds — fixed at the root by the
    # per-star local-annulus photometry in xp_stars.measure_stars_rgb. Henry
    # approved the fixed SSSC colour on drizzled M 42 side-by-side vs SPCC.)
    if spcc_lp_filter and allow_sssc:
        logger.info("[seti_astro] LP data — attempting SSSC calibration before SPCC")
        _sres = sssc_calibrate(input_path, output_path, lp=True)
        if _sres.get("ok"):
            # SANITY GATE (2026-07-02, SH2-101 post-mortem): in dense fields the XP
            # matcher can pair detected stars with WRONG catalog stars (the WCS axis/
            # offset mismatch) yet still pass the RMS gate — SSSC then solves garbage
            # gains (SH2-101: signal G/R 1.43→3.01, R halved → green nebula, final 3.5).
            # A real calibration never pushes the signal colour ratio sharply AWAY from
            # neutral. Measure signal-region G/R before/after; reject on a big move away.
            try:
                _rb = _signal_gr_ratio(input_path)
                _ra = _signal_gr_ratio(output_path)
                if _rb and _ra and abs(_ra - 1.0) > abs(_rb - 1.0) + 0.5 and _ra > 1.6:
                    logger.warning(f"[seti_astro] SSSC sanity gate REJECT: signal G/R "
                                   f"{_rb:.2f}→{_ra:.2f} (moved away from neutral) — "
                                   "discarding SSSC, falling back to SPCC/CC")
                    _sres = {"ok": False, "error": f"sanity_gate G/R {_rb:.2f}->{_ra:.2f}"}
                    try:
                        (Path(output_path).parent / ".sssc_applied").unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception as _sg:
                logger.warning(f"[seti_astro] SSSC sanity gate check failed ({_sg}) — accepting")
        if _sres.get("ok"):
            try:
                _sentinel.unlink(missing_ok=True)
            except Exception:
                pass
            _preserve_celestial_wcs(input_path, output_path)
            _sres.update({"method": "sssc", "spcc_fell_back": False})
            return _sres
        logger.warning(f"[seti_astro] SSSC failed ({_sres.get('error')}) — "
                       "falling back to PI SPCC/CC path")

    # Allow worker settings to override the GAIA catalog path (e.g. laptop pointing at NAS)
    _gaia_db_path = _cfg_settings.get("gaia_db_path") or None

    # If the input has no WCS and no scale metadata (manual/external master), seed the
    # known S50 FOCALLEN/XPIXSZ so the in-PI plate-solver has a resolution hint to solve
    # from — otherwise SPCC runs unsolved and degrades to generic CC (dead color).
    _ensure_plate_solve_scale_hints(input_path)

    # Mutilated-WCS rescue (workflow 1.24.0, root cause of the 2026-07-16 batch SPCC
    # failures — M 85/88/91/97/102/109): some intermediates carry CTYPE/CRVAL/CRPIX
    # but no CD matrix, plus a junk CDELT=1 deg/px. PI parses that into an internal
    # 3600"/px scale seed its solver can't recover from (keyword-level sanitizing is
    # invisible to it), so SPCC always fell back to generic CC. Fix upstream: if the
    # WCS is absent or insane, ASTAP-solve a temp copy (astropy-consistent, ~0.2 s
    # with the CRVAL hint that DOES survive) and feed PI the properly-solved file.
    def _wcs_scale_arcsec(p) -> float | None:
        try:
            import numpy as _np
            from astropy.wcs import WCS as _WCS
            from astropy.io import fits as _f
            _hd = _f.getheader(str(p), memmap=False)
            _w = _WCS(_hd)
            if not _w.has_celestial:
                return None
            return float(_np.sqrt(abs(_np.linalg.det(
                _w.celestial.pixel_scale_matrix)))) * 3600.0
        except Exception:
            return None

    def _pc_form(p) -> bool:
        # astropy WCS.to_header() emits PC1_1..+CDELT=1.0; astropy reads it fine
        # (scale = CDELT×PC) but PI 1.9.3 takes CDELT=1 deg/px literally → its
        # solver seeds 3600"/px and SPCC dies. Detect that exact poison form.
        try:
            from astropy.io import fits as _f
            _h = _f.getheader(str(p), memmap=False)
            return ("PC1_1" in _h and "CD1_1" not in _h
                    and abs(float(_h.get("CDELT1", 0))) >= 0.01)
        except Exception:
            return False

    _scale = _wcs_scale_arcsec(input_path)
    if _scale and 0.3 < _scale < 30.0 and _pc_form(input_path):
        # Valid solution in PC form — pure header transform, no re-solve needed.
        try:
            import shutil as _sh
            from astropy.io import fits as _f
            # NOT "*_norm.fit" — the PI cache cleanup deletes that pattern
            _norm = Path(output_path).parent / "_spcc_wcs_cd.fit"
            _sh.copy2(str(input_path), str(_norm))
            with _f.open(str(_norm), mode="update", memmap=False) as _hd:
                _h = _hd[0].header
                cd11 = float(_h["CDELT1"]) * float(_h["PC1_1"])
                cd12 = float(_h["CDELT1"]) * float(_h.get("PC1_2", 0.0))
                cd21 = float(_h["CDELT2"]) * float(_h.get("PC2_1", 0.0))
                cd22 = float(_h["CDELT2"]) * float(_h["PC2_2"])
                for k in ("PC1_1", "PC1_2", "PC2_1", "PC2_2",
                          "CDELT1", "CDELT2", "CROTA1", "CROTA2"):
                    _h.remove(k, ignore_missing=True)
                _h["CD1_1"], _h["CD1_2"] = cd11, cd12
                _h["CD2_1"], _h["CD2_2"] = cd21, cd22
                _hd.flush()
            logger.info(f"[seti_astro] SPCC input WCS normalized PC→CD "
                        f"({_scale:.2f}\"/px) — PI misreads the PC+CDELT=1 form")
            input_path = _norm
        except Exception as _ne:
            logger.warning(f"[seti_astro] PC→CD normalize failed ({_ne}) — proceeding")
    elif _scale is None or not (0.3 < _scale < 30.0):
        logger.warning(f"[seti_astro] SPCC input WCS unusable "
                       f"(scale={_scale if _scale is None else round(_scale, 1)}\"/px) "
                       "— ASTAP pre-solve on temp copy")
        try:
            import shutil as _sh
            _solved = Path(output_path).parent / "_spcc_astap_solved.fit"
            _sh.copy2(str(input_path), str(_solved))
            _as = astap_solve(_solved)
            _s2 = _wcs_scale_arcsec(_solved)
            if _as.get("ok") and _s2 and 0.3 < _s2 < 30.0:
                logger.info(f"[seti_astro] ASTAP pre-solve OK ({_s2:.2f}\"/px) — "
                            "SPCC will use the solved copy")
                input_path = _solved
            else:
                logger.warning(f"[seti_astro] ASTAP pre-solve failed "
                               f"({_as.get('error') or _as}) — SPCC proceeding unsolved")
        except Exception as _ae:
            logger.warning(f"[seti_astro] ASTAP pre-solve error ({_ae}) — proceeding")

    # LP standard chain: skip SPCC entirely (1.16.0). SPCC's broadband white
    # reference is wrong for dual-narrowband (see [[reference-seestar-lp-filter]]:
    # "don't SPCC it") — historically it always FAILED on LP data, which routed
    # every approved M 42 run to PI CC + the .spcc_failed unlinked/SCNR semantics.
    # With the corrected ASTAP WCS SPCC might now "succeed" and produce an
    # untested colour while suppressing SCNR — so make the proven path explicit:
    # straight to PI ColorCalibration, sentinel set ("no calibrated WB to protect").
    if spcc_lp_filter:
        logger.info("[seti_astro] LP data on standard chain — skipping SPCC "
                    "(broadband white ref wrong for dual-band), using PI CC path")
        try:
            _sentinel.write_text(f"lp_no_spcc target={target} t={time.time():.0f}\n")
        except Exception as _se:
            logger.warning(f"[seti_astro] could not write sentinel: {_se}")
        _lp_cc = run_postprocess(
            target=target or "spcc",
            input_fits=str(input_path),
            output_path=str(output_path),
            color_calibration=True,
            gradient_correction=False,
            mlt=False, tgv=False, bxt=False, nxt=False, cms=False,
            timeout=300,
        )
        elapsed = round(time.time() - t0, 1)
        _ok = _lp_cc.get("ok", False) and not _lp_cc.get("cc_failed")
        if _ok:
            _preserve_celestial_wcs(input_path, output_path)
            logger.info(f"[seti_astro] LP ColorCalibration done in {elapsed}s")
        else:
            logger.warning(f"[seti_astro] LP ColorCalibration failed in {elapsed}s")
        return {"ok": _ok, "output_path": str(output_path) if _ok else None,
                "elapsed_s": elapsed, "method": "pi_cc_lp", "spcc_fell_back": True}

    # Retry SPCC once on failure — the failure mode is often a transient catalog
    # query bail (fast ~25s exit), not a config problem.
    _SPCC_ATTEMPTS = 2
    result = {}
    for _attempt in range(1, _SPCC_ATTEMPTS + 1):
        result = run_postprocess(
            target=target or "spcc",
            input_fits=str(input_path),
            output_path=str(output_path),
            spcc=True,
            spcc_lp_filter=spcc_lp_filter,
            gaia_db_path=_gaia_db_path,
            # SPCC-only: disable all other linear tools — they run as separate pipeline steps
            gradient_correction=False,
            mlt=False,
            tgv=False,
            bxt=False,
            nxt=False,
            cms=False,  # CorrectMagentaStars is a saturation curve — wrong on linear data
            timeout=300,
        )
        if result.get("ok") and not result.get("spcc_failed"):
            break
        if _attempt < _SPCC_ATTEMPTS:
            logger.warning(
                f"[seti_astro] SPCC attempt {_attempt}/{_SPCC_ATTEMPTS} failed "
                f"(spcc_failed={result.get('spcc_failed')}) — retrying")
            time.sleep(3)
    elapsed = round(time.time() - t0, 1)

    if result.get("ok") and not result.get("spcc_failed"):
        logger.info(f"[seti_astro] SPCC done in {elapsed}s")
        try:
            _sentinel.unlink(missing_ok=True)  # real calibration applied — clear any stale flag
        except Exception:
            pass
        _preserve_celestial_wcs(input_path, output_path)
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed,
                "method": "spcc", "spcc_fell_back": False}

    # SPCC failed (no WCS or catalog miss, or transient catalog bail) after retries —
    # fall back to PI ColorCalibration and flag the run for an unlinked stretch.
    logger.warning(
        f"[seti_astro] SPCC FAILED after {_SPCC_ATTEMPTS} attempts — falling back to PI "
        f"ColorCalibration; flagging run for UNLINKED stretch ({_sentinel})")
    try:
        _sentinel.write_text(f"spcc_failed target={target} t={time.time():.0f}\n")
    except Exception as _se:
        logger.warning(f"[seti_astro] could not write spcc_failed sentinel: {_se}")
    result2 = run_postprocess(
        target=target or "spcc",
        input_fits=str(input_path),
        output_path=str(output_path),
        color_calibration=True,
        gradient_correction=False,
        mlt=False,
        tgv=False,
        bxt=False,
        nxt=False,
        cms=False,
        timeout=300,
    )
    elapsed = round(time.time() - t0, 1)
    ok = result2.get("ok", False) and not result2.get("cc_failed")
    if ok:
        _preserve_celestial_wcs(input_path, output_path)
        logger.info(f"[seti_astro] ColorCalibration fallback done in {elapsed}s")
    else:
        logger.warning(f"[seti_astro] ColorCalibration fallback also failed in {elapsed}s")
    return {"ok": ok, "output_path": str(output_path) if ok else None,
            "elapsed_s": elapsed, "method": "pi_cc_fallback", "spcc_fell_back": True}


def astap_solve(fits_path: str | Path, fov_deg: float = 1.3,
                search_deg: float = 10.0, timeout: int = 120) -> dict:
    """Blind/near plate-solve with ASTAP CLI, updating the FITS header IN PLACE.

    Why (2026-07-02): Siril/PI write their plate solutions in a convention astropy
    misreads (axis-inverted, sometimes offset) — the SASpro/XP path (SSSC, NBExtract)
    and every astropy reader then mis-maps stars: SH2-101's SSSC matched wrong stars
    → garbage gains → green cast; previews rendered mirrored. ASTAP writes an
    astropy-consistent solution: verified on SH2-101, XP matching went from a
    non-discriminating 39/120 (any orientation) to 119/120 identity vs 4-5 flipped.
    Star DB: /opt/astap D20 (installed 2026-07-02). Solve time ~0.2 s with header hints.
    """
    import subprocess
    t0 = time.time()
    exe = "/opt/astap/astap_cli"
    if not Path(exe).exists():
        exe = "astap"
    try:
        # No usable position hint in the header (manual/external master, or a pre-EQ
        # location-spoofed capture whose RA/DEC are FAKE) -> full BLIND solve. This is
        # the long-standing "fix = blind solve" for the pre-EQ spoof cases.
        from astropy.io import fits as _fh
        _h0 = _fh.getheader(str(fits_path), memmap=False)
        if _h0.get("CRVAL1") is None and _h0.get("RA") is None:
            search_deg = 180
        # FoV from actual image geometry when the header carries a scale (1.16.2):
        # the 1.3 deg default is a single-panel assumption — maxframing MOSAICS span
        # several degrees and ASTAP fails in seconds on a wrong FoV hint (NGC 7000
        # 60 MP mosaic: ingest solve FAILED -> old WCS -> XP matching stuck at 10
        # stars). Same derivation as scripts/migrate_saved_crops.py.
        try:
            _sc = None
            for _k in ("CD1_1", "CDELT1", "PC1_1"):
                _v = _h0.get(_k)
                if _v not in (None, 0, 1.0):
                    _sc = abs(float(_v)); break
            if _sc and 1e-5 < _sc < 0.1:
                fov_deg = round(min(max((_h0.get("NAXIS2") or 1920) * _sc, 0.4), 8.0), 2)
        except Exception:
            pass
        r = subprocess.run(
            [exe, "-f", str(fits_path), "-r", str(int(search_deg)),
             "-fov", str(fov_deg), "-update"],
            capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        from astropy.io import fits as _f
        h = _f.getheader(str(fits_path), memmap=False)
        ok = ("Solution found" in out or h.get("PLTSOLVD")) and h.get("CD1_1") is not None
        elapsed = round(time.time() - t0, 1)
        if ok:
            logger.info(f"[seti_astro] astap_solve OK in {elapsed}s: {Path(fits_path).name}")
            return {"ok": True, "elapsed_s": elapsed,
                    "crval": [h.get("CRVAL1"), h.get("CRVAL2")]}
        logger.warning(f"[seti_astro] astap_solve failed in {elapsed}s: {out[-200:]}")
        return {"ok": False, "error": out[-300:], "elapsed_s": elapsed}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "elapsed_s": round(time.time() - t0, 1)}

"""Siril stacking automation for SeeStar library."""
import os
import json
import subprocess
import shutil
import logging
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from nas_server import telegram

logger = logging.getLogger(__name__)

SCRIPT_PATH = Path(__file__).parent / "seestar_stack.ssf"
SCRIPT_PATH_FAST = Path(__file__).parent / "seestar_stack_fast.ssf"
SCRIPT_PATH_HERO = Path(__file__).parent / "seestar_stack_hero.ssf"
SCRIPT_PATH_MAXFRAMING = Path(__file__).parent / "seestar_stack_maxframing.ssf"
SCRIPT_PATH_MAXFRAMING_HERO = Path(__file__).parent / "seestar_stack_maxframing_hero.ssf"
SCRIPT_PATH_DRIZZLE = Path(__file__).parent / "seestar_stack_drizzle.ssf"
SCRIPT_PATH_DRIZZLE_HERO = Path(__file__).parent / "seestar_stack_drizzle_hero.ssf"
SCRIPT_PATH_DRIZZLE_MAXFRAMING = Path(__file__).parent / "seestar_stack_drizzle_maxframing.ssf"
SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO = Path(__file__).parent / "seestar_stack_drizzle_maxframing_hero.ssf"
REGISTER_SCRIPT_PATH = Path(__file__).parent / "seestar_register.ssf"
REGISTER_SCRIPT_PATH_HERO = Path(__file__).parent / "seestar_register_hero.ssf"
REGISTER_SCRIPT_PATH_MAXFRAMING = Path(__file__).parent / "seestar_register_maxframing.ssf"
REGISTER_SCRIPT_PATH_MAXFRAMING_HERO = Path(__file__).parent / "seestar_register_maxframing_hero.ssf"
REGISTER_SCRIPT_PATH_DRIZZLE = Path(__file__).parent / "seestar_register_drizzle.ssf"
REGISTER_SCRIPT_PATH_DRIZZLE_HERO = Path(__file__).parent / "seestar_register_drizzle_hero.ssf"
REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING = Path(__file__).parent / "seestar_register_drizzle_maxframing.ssf"
REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO = Path(__file__).parent / "seestar_register_drizzle_maxframing_hero.ssf"
# Drizzle Phase A: convert + seqplatesolve only (expected to crash on .seq write; WCS in headers)
PLATESOLVE_DRIZZLE_SCRIPT_PATH = Path(__file__).parent / "seestar_platesolve_drizzle.ssf"
# Drizzle maxframing Phase B SSFs (start at process/, no platesolve)
REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_PHASE_B = Path(__file__).parent / "seestar_register_drizzle_maxframing_phase_b.ssf"
REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO_PHASE_B = Path(__file__).parent / "seestar_register_drizzle_maxframing_hero_phase_b.ssf"
# Fallback apply-only SSFs — run after seqplatesolve if that step fails (no WCS, image matching only)
REGISTER_SCRIPT_PATH_MF_APPLY = Path(__file__).parent / "seestar_register_mf_apply.ssf"
REGISTER_SCRIPT_PATH_MF_APPLY_HERO = Path(__file__).parent / "seestar_register_mf_apply_hero.ssf"
# No-plate-solve maxframing — used for multi-panel stacks where frames span different pointings
REGISTER_SCRIPT_PATH_MF_NOPS = Path(__file__).parent / "seestar_register_maxframing_nops.ssf"
REGISTER_SCRIPT_PATH_MF_NOPS_HERO = Path(__file__).parent / "seestar_register_maxframing_nops_hero.ssf"

# Stack-only SSFs — used by two-phase Siril engine after register SSF completes.
STACK_ONLY_SCRIPT_PATH = Path(__file__).parent / "seestar_stack_only.ssf"
STACK_ONLY_SCRIPT_PATH_MAXFRAMING = Path(__file__).parent / "seestar_stack_only_maxframing.ssf"


def make_processed_filename(target: str, obs_date: str | None, total_secs: float | None,
                             temp_c: float | None, tool: str, step: str, ext: str) -> str:
    """Generate a standardized processed filename.
    Example: C_77_20260419_6510s_0C_siril_stack.fit
    """
    safe = target.replace(" ", "_").replace("/", "_")
    date = obs_date[:8] if obs_date and len(obs_date) >= 8 else "unknown"
    integ = f"{int(round(total_secs))}s" if total_secs else "0s"
    temp = f"{int(round(temp_c))}C" if temp_c is not None else "nC"
    return f"{safe}_{date}_{integ}_{temp}_{tool}_{step}.{ext}"


def _read_first_frame_meta(work_light: Path) -> dict:
    """Read DATE-OBS, EXPTIME, CCD-TEMP from the first light frame."""
    fits_files = sorted(work_light.glob("*.fit")) + sorted(work_light.glob("*.fits"))
    if not fits_files:
        return {}
    try:
        from astropy.io import fits
        h = fits.getheader(str(fits_files[0]), ext=0)
        date_obs = h.get("DATE-OBS", "")
        if date_obs and len(date_obs) >= 10:
            obs_date = date_obs[:10].replace("-", "")
        else:
            obs_date = None
        return {
            "obs_date": obs_date,
            "exptime": h.get("EXPTIME"),
            "sensor_temp": h.get("CCD-TEMP") or h.get("SET-TEMP"),
        }
    except Exception as e:
        logger.warning(f"Could not read first frame meta: {e}")
        return {}


def _apply_nina_calibration(
    indexed: list[tuple[Path, Path]],
    nina_src_paths: set[str],
) -> tuple[int, str | None]:
    """Apply dark subtraction and flat normalization to NINA raw frames in-place.

    indexed: list of (work_dest_path, original_src_path) — the already-copied light frames.
    nina_src_paths: set of original file_path strings that have source='nina' in the DB.

    Modifies only the copies in work_light, never the originals.
    Returns (n_calibrated, warning_message_or_None).

    # TODO: test this path end-to-end once first NINA subs arrive.
    """
    import numpy as np
    from astropy.io import fits as _afits
    from nas_server.database import get_calibration_master, get_conn

    nina_pairs = [(dst, src) for dst, src in indexed if str(src) in nina_src_paths]
    if not nina_pairs:
        return 0, None

    # Read headers from the first NINA frame to get gain / temp / exptime / filter.
    first_dst = nina_pairs[0][0]
    try:
        with _afits.open(str(first_dst)) as hdul:
            h = hdul[0].header
            gain    = h.get("GAIN")
            temp_c  = h.get("CCD-TEMP")
            exptime = h.get("EXPTIME")
            filt    = h.get("FILTER") or "none"
    except Exception as e:
        return 0, f"NINA calibration skipped — could not read frame headers: {e}"

    dark_path = get_calibration_master("dark", gain=int(gain) if gain is not None else None,
                                       temp_c=float(temp_c) if temp_c is not None else None,
                                       exposure_time=float(exptime) if exptime is not None else None)
    flat_path = get_calibration_master("flat", gain=int(gain) if gain is not None else None,
                                       filter=filt)

    if not dark_path and not flat_path:
        return 0, (f"NINA calibration skipped — no masters found "
                   f"(gain={gain} temp={temp_c}°C exp={exptime}s filter={filt})")

    dark_data = flat_norm = None
    if dark_path:
        try:
            with _afits.open(dark_path) as hdul:
                dark_data = hdul[0].data.astype(np.float32)
        except Exception as e:
            logger.warning(f"[cal] could not load dark master {dark_path}: {e}")

    if flat_path:
        try:
            with _afits.open(flat_path) as hdul:
                flat_raw = hdul[0].data.astype(np.float32)
            flat_med = np.median(flat_raw)
            if flat_med > 0:
                flat_norm = flat_raw / flat_med
        except Exception as e:
            logger.warning(f"[cal] could not load flat master {flat_path}: {e}")

    n = 0
    for dst, _ in nina_pairs:
        try:
            with _afits.open(str(dst), mode="update") as hdul:
                data = hdul[0].data.astype(np.float32)
                if dark_data is not None and dark_data.shape == data.shape:
                    data = data - dark_data
                if flat_norm is not None and flat_norm.shape == data.shape:
                    data = np.where(flat_norm > 0.1, data / flat_norm, data)
                data = np.clip(data, 0, 65535).astype(np.uint16)
                hdul[0].data = data
                hdul[0].header["CALSTAT"] = "DF"  # D=dark, F=flat applied
            n += 1
        except Exception as e:
            logger.warning(f"[cal] failed to calibrate {dst.name}: {e}")

    parts = []
    if dark_path:
        parts.append("dark")
    if flat_path:
        parts.append("flat")
    logger.info(f"[cal] applied {'+'.join(parts)} calibration to {n}/{len(nina_pairs)} NINA frames")
    return n, None


def score_and_cull_frames(
    target_name: str,
    candidate_paths: list[Path],
    db_path: str | None,
    bottom_pct: float = 0.10,
    min_stars: int = 20,
) -> dict:
    """
    Measure every light frame and store FWHM / eccentricity / SNR / star_count in the DB.

    candidate_paths: all frame paths for this target (from DB), regardless of subfolder.

    Does NOT set exclude flags — thresholds are applied dynamically at stack time via
    get_frames_for_stack(), so the cutoff can be changed without re-analyzing frames.

    Returns {"measured": int, "fwhm_median": float, "fwhm_mad": float,
             "projected_kept": int, "projected_rejected": int, "cached": bool}
    """
    from nas_server.image_analyzer import analyze
    from nas_server.database import (get_scored_frame_count, update_light_frame_scores,
                                      mark_frames_scored)

    fits_files = [p for p in candidate_paths if p.exists()]

    if not fits_files:
        return {"measured": 0, "fwhm_median": 0.0, "fwhm_mad": 0.0,
                "projected_kept": 0, "projected_rejected": 0, "cached": False}

    # Load already-scored paths and check for missing sky_level (new column)
    try:
        from nas_server.database import get_conn as _db_conn
        with _db_conn() as _c:
            already_scored = {
                r[0] for r in _c.execute(
                    "SELECT file_path FROM light_files WHERE target=? AND scored_at IS NOT NULL",
                    (target_name,)
                ).fetchall()
            }
            missing_sky = {
                r[0] for r in _c.execute(
                    "SELECT file_path FROM light_files WHERE target=? AND scored_at IS NOT NULL "
                    "AND sky_level IS NULL",
                    (target_name,)
                ).fetchall()
            }
    except Exception:
        already_scored = set()
        missing_sky = set()

    # Skip re-analysis only if all frames are scored AND have sky_level
    try:
        total, scored_count = get_scored_frame_count(target_name)
        if total > 0 and total == scored_count and not missing_sky:
            logger.info(f"[cull] {target_name}: {total} frames already measured — skipping analysis")
            telegram.send(
                f"✅ <b>Measurements cached</b>: <code>{target_name}</code>\n"
                f"{total} frames already scored — threshold applied at stack time"
            )
            return {"measured": total, "fwhm_median": 0.0, "fwhm_mad": 0.0,
                    "projected_kept": total, "projected_rejected": 0, "cached": True}
    except Exception as e:
        logger.warning(f"[cull] cached-score check failed: {e}")

    # Measure frames not yet scored, plus any missing sky_level
    to_measure = [f for f in fits_files if str(f) not in already_scored or str(f) in missing_sky]
    already_n = len(fits_files) - len(to_measure)

    telegram.send(
        f"🔬 <b>Measuring {len(to_measure)} frames</b>: <code>{target_name}</code>\n"
        f"{already_n} already scored, {len(to_measure)} remaining…"
        if already_n else
        f"🔬 <b>Measuring {len(to_measure)} frames</b>: <code>{target_name}</code>\n"
        f"Storing FWHM, eccentricity, SNR, star count…"
    )
    logger.info(f"[cull] {target_name}: measuring {len(to_measure)} frames "
                f"({already_n} already scored)")

    scores = []
    for f in fits_files:
        if str(f) in already_scored and str(f) not in missing_sky:
            # Load cached measurements for projection calculation
            try:
                with _db_conn() as _c:
                    row = _c.execute(
                        "SELECT fwhm, eccentricity, snr, star_count, sky_level, gradient_severity "
                        "FROM light_files WHERE file_path=?",
                        (str(f),)
                    ).fetchone()
                if row and row[0] is not None:
                    scores.append({"path": f, "fwhm": row[0], "ecc": row[1] or 0,
                                   "stars": row[3] or 0, "sky_level": row[4],
                                   "gradient_severity": row[5],
                                   "composite": row[0] * (1.0 + (row[1] or 0))})
            except Exception:
                pass
            continue
        try:
            stats = analyze(str(f))
            fwhm  = stats["psf"]["fwhm_median"]
            ecc   = stats["psf"]["eccentricity"]
            stars = stats["psf"]["star_count"]
            snr   = stats.get("noise", {}).get("snr")
            sky_level = stats.get("background", {}).get("sky_mean")
            gradient  = stats.get("background", {}).get("gradient_severity")
        except Exception as e:
            logger.warning(f"[cull] {f.name}: analyze failed ({e}) — storing as failed")
            fwhm, ecc, stars, snr, sky_level, gradient = 999.0, 1.0, 0, None, None, None

        update_light_frame_scores(str(f), fwhm=fwhm, eccentricity=ecc,
                                  snr=snr, star_count=stars,
                                  sky_level=sky_level, gradient_severity=gradient)
        scores.append({"path": f, "fwhm": fwhm, "ecc": ecc, "stars": stars,
                        "sky_level": sky_level, "gradient_severity": gradient,
                        "composite": fwhm * (1.0 + ecc)})

    mark_frames_scored(target_name)

    # Compute FWHM distribution for reporting (threshold not stored — applied at stack time)
    fwhm_arr = np.array([s["fwhm"] for s in scores if s["fwhm"] < 900])
    fwhm_median = float(np.median(fwhm_arr)) if len(fwhm_arr) else 0.0
    fwhm_mad = float(np.median(np.abs(fwhm_arr - fwhm_median))) * 1.4826 if len(fwhm_arr) else 0.0

    # Project what will be kept at the requested threshold (informational only)
    sorted_by_score = sorted(scores, key=lambda s: s["composite"])
    n_pct_reject = int(len(sorted_by_score) * bottom_pct)
    pct_reject_ids = {id(s) for s in sorted_by_score[:n_pct_reject]}
    projected_rej = sum(
        1 for s in scores
        if s["stars"] < min_stars or s["ecc"] > 0.66 or id(s) in pct_reject_ids
    )
    projected_kept = len(scores) - projected_rej

    telegram.send(
        f"✅ <b>Measurements done</b>: <code>{target_name}</code>\n"
        f"{len(scores)} frames measured\n"
        f"FWHM median={fwhm_median:.2f}px MAD={fwhm_mad:.2f}px\n"
        f"At {int(bottom_pct*100)}% threshold: ~{projected_kept} kept / {projected_rej} rejected\n"
        f"Threshold can be changed without re-measuring."
    )
    logger.info(f"[cull] {target_name}: measured={len(scores)} "
                f"fwhm_med={fwhm_median:.2f} fwhm_mad={fwhm_mad:.2f} "
                f"projected_kept={projected_kept} at {bottom_pct:.0%}")

    return {
        "measured": len(scores),
        "fwhm_median": fwhm_median,
        "fwhm_mad": fwhm_mad,
        "projected_kept": projected_kept,
        "projected_rejected": projected_rej,
        "cached": False,
    }


def stack_target(target_name: str, library_path: str, db_path: str | None = None,
                 engine: str = "siril", cull: bool = True,
                 bottom_pct: float = 0.10, min_stars: int = 20,
                 fast: bool = False, framing: str = "min",
                 hero: bool = False, drizzle: bool = False,
                 exptime: int | None = None,
                 eq_only: bool = True,
                 ecc_threshold: float = 0.66,
                 sky_level_factor: float = 3.0,
                 gradient_threshold: float = 0.5) -> dict:
    """
    Stack all light frames for a target.

    engine: "siril" (default) — Siril calibrate+register+stack
            "imagemm" — Siril calibrate+register, SASpro Image MM deconvolution stack
            "pixinsight_wbpp" — Siril register, PI ImageIntegration (SNR-weighted)
            "pixinsight_register" — PI Debayer + StarAlignment + ImageIntegration (headless-safe)

    cull: if True (default), score frames first and exclude outliers before stacking.

    fast: if True, skip per-frame plate solve (seqplatesolve). Uses image-based registration
          only — faster, slightly less precise. Good for re-stacks and single-night sessions.
          Mutually exclusive with hero (fast takes priority).

    framing: "min" (default) — intersection of all frames (standard)
             "max" — union of all frames (mosaic / multi-session coverage)
             Requires plate solve per frame; ignored when fast=True.

    hero: if True, maximise quality over speed. Siril uses Lanczos4 interpolation
          (sharper stars, less ringing). PI uses IKSS weight scale (most accurate
          per-frame SNR weighting) and enables SNR evaluation. Ignored when fast=True.

    drizzle: if True, use Siril's drizzle registration (scale=2x, pixfrac=0.5/0.4 hero).
             Produces 4x more pixels per frame (5120x3840 for SeeStar S50) by combining
             sub-pixel samples from natural alt-az field rotation. Recommended 100+ frames.
             Incompatible with fast mode (fast takes priority). Space estimate 5x instead of 3x.
             For imagemm/wbpp engines: drizzle happens in Siril registration step; the stacker
             receives 2x-upsampled frames and integrates normally.

    Note: seqplatesolve is intentionally omitted from all non-maxframing SSFs.
    It triggers execute_idle_and_wait_for_it in Siril 1.4.2 headless on large sequences —
    the same bug as calibrate. register -2pass handles rotation via image matching (Naztronomy
    validated this fallback). Maxframing variants keep seqplatesolve for astrometric union framing.

    Copies frames to local /tmp (SMB mounts don't support symlinks),
    moves results into {target_dir}/_processed/ with standardized names, cleans up.

    Returns dict with keys: success, processed_fit, preview_jpg, frames, elapsed, error
    """
    from nas_server.config import settings
    from nas_server.database import upsert_processed_file, set_pipeline_stage, DB_PATH as NAS_DB

    _stack_params = {
        "hero": hero, "drizzle": drizzle, "bottom_pct": bottom_pct,
        "ecc_threshold": ecc_threshold, "exptime": exptime, "framing": framing,
        "eq_only": eq_only,
    }

    lib_root = Path(library_path)
    target_dir = lib_root / target_name

    # --- Build candidate frame list from DB (all subfolders, any path) ---
    from nas_server.database import get_frames_by_target as _get_all_frames, get_conn as _db_conn
    _all_db_frames = _get_all_frames(target_name)
    if not _all_db_frames:
        return {"success": False, "error": f"No frames in DB for target: {target_name}"}

    # Include frames from confirmed associated targets (set via /associations page)
    _included_targets = {target_name}
    try:
        with _db_conn() as _c:
            _assoc_row = _c.execute(
                "SELECT association FROM targets WHERE target=?", (target_name,)
            ).fetchone()
        if _assoc_row and _assoc_row[0]:
            for _at in _assoc_row[0].split(","):
                _at = _at.strip()
                if _at and _at not in _included_targets:
                    _all_db_frames.extend(_get_all_frames(_at))
                    _included_targets.add(_at)
                    logger.info(f"[stack] {target_name}: including associated target '{_at}'")
    except Exception as _ae:
        logger.warning(f"[stack] {target_name}: association lookup failed: {_ae}")

    # Include mosaic panel frames: all targets that list this target as their mosaic_association.
    # Automatically forces framing=max so panels are stitched into a union frame.
    _has_mosaic_panels = False
    try:
        from nas_server.database import get_mosaic_panel_targets
        _panels = get_mosaic_panel_targets(target_name)
        if _panels:
            _has_mosaic_panels = True
            for _at in _panels:
                if _at not in _included_targets:
                    _all_db_frames.extend(_get_all_frames(_at))
                    _included_targets.add(_at)
                    logger.info(f"[stack] {target_name}: including mosaic panel '{_at}'")
            if framing != "max":
                logger.info(f"[stack] {target_name}: mosaic panels found — overriding framing to max")
                framing = "max"
    except Exception as _ae:
        logger.warning(f"[stack] {target_name}: mosaic panel lookup failed: {_ae}")

    # Force framing=max if the target itself is flagged as a mosaic capture (mosaic=1),
    # even when using Strategy 1 (same-name capture, no separate panel targets).
    if framing != "max":
        try:
            with _db_conn() as _c:
                _mrow = _c.execute(
                    "SELECT mosaic FROM targets WHERE target=?", (target_name,)
                ).fetchone()
            if _mrow and _mrow[0]:
                logger.info(f"[stack] {target_name}: mosaic flag set — overriding framing to max")
                framing = "max"
        except Exception as _ae:
            logger.warning(f"[stack] {target_name}: mosaic flag lookup failed: {_ae}")

    candidate_paths = [Path(f["file_path"]) for f in _all_db_frames if not f.get("exclude")]

    # --- Canonical framing (PI register engine only) ---
    # When the PI register engine is selected for a folio target with valid coords, build
    # (cached) a synthetic Gaia reference on the fixed canonical grid and register real subs
    # onto it, so every session lands on identical pixels (cumulative cross-night master
    # stacks). The engine choice is NOT overridden — a Siril/ImageMM/WBPP stack of the same
    # target runs normally — so the UI can default folios-with-a-reference to PI register
    # while still allowing a quick Siril stack. Skipped for mosaics (framing=max / panels;
    # the canonical fixed grid is single-pointing) and fast mode (incompatible with PI).
    canonical_ref = None
    if (engine == "pixinsight_register"
            and settings.get("canonical_framing_auto", True)
            and not fast and framing != "max" and not _has_mosaic_panels):
        canonical_ref = _maybe_build_canonical_reference(target_name, drizzle)

    started_at = datetime.now().isoformat(timespec="seconds")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = target_name.replace(" ", "_").replace("/", "_")
    work_dir = None
    work_light = None

    try:
        # --- Starting message with ETA ---
        frame_total = len(_all_db_frames)
        eta_min = _estimate_stack_minutes(target_name, frame_total, engine)
        _set_stack_phase(target_name, "culling" if cull else "copying")
        _drizzle_note = " | drizzle 2x" if drizzle else ""
        _exptime_note = f" | {exptime}s subs only" if exptime else ""
        telegram.send(
            f"🚀 <b>Stack starting</b>: <code>{target_name}</code>\n"
            f"Engine: {engine}{_drizzle_note}{_exptime_note} | ~{frame_total} frames | ETA ~{eta_min}min"
        )
        if drizzle and frame_total < 100:
            telegram.send(
                f"⚠️ <b>Drizzle warning</b>: <code>{target_name}</code>\n"
                f"Only {frame_total} frames — drizzle works best with 100+. "
                f"SeeStar alt-az rotation helps, but fewer frames may leave gaps in the output grid."
            )

        # --- Phase 0: Measure frames (store quality metrics, threshold applied below) ---
        # Run per-target so panel frames are scored and qualified under their own target name.
        if cull:
            _set_stack_phase(target_name, "culling")
            for _ct in _included_targets:
                _ct_paths = [Path(f["file_path"]) for f in _all_db_frames
                             if f.get("target") == _ct and not f.get("exclude")]
                if _ct_paths:
                    score_and_cull_frames(_ct, _ct_paths, db_path,
                                          bottom_pct=bottom_pct, min_stars=min_stars)

        # Apply threshold dynamically from stored measurements.
        # exclude=1 in DB means manual user exclusion only (not auto-culling).
        from nas_server.database import get_frames_for_stack
        if cull:
            qualifying_paths = set()
            for _ct in _included_targets:
                qualifying_paths.update(get_frames_for_stack(
                    _ct, bottom_pct=bottom_pct, min_stars=min_stars,
                    ecc_threshold=ecc_threshold, sky_level_factor=sky_level_factor,
                    gradient_threshold=gradient_threshold))
        else:
            qualifying_paths = {f["file_path"] for f in _all_db_frames if not f.get("exclude")}

        # Optional exposure-time filter (e.g. exptime=30 for EQ-mode subs only)
        if exptime is not None:
            with _db_conn() as _c:
                exptime_paths = {r[0] for r in _c.execute(
                    "SELECT file_path FROM light_files WHERE target=? AND ROUND(exposure_time)=?",
                    (target_name, round(exptime))
                ).fetchall()}
            qualifying_paths = qualifying_paths & exptime_paths
            logger.info(f"[stack] {target_name}: exptime={exptime}s filter → {len(qualifying_paths)} frames")

        # ── Alignment-outlier filter ──────────────────────────────────────
        # Drop subs whose TRUE solved position sits far from their panel/target
        # siblings (solve_status='outlier' — e.g. a pre-EQ ALP-spoofed frame ~50°
        # off-pointing). These would otherwise drag registration off the cluster or
        # smear the max-frame mosaic. Gated by a setting in case the data is wanted.
        if settings.get("exclude_alignment_outliers", True):
            with _db_conn() as _c:
                _outlier_paths = {r[0] for r in _c.execute(
                    "SELECT file_path FROM light_files WHERE solve_status='outlier'"
                ).fetchall()}
            if _outlier_paths:
                _before = len(qualifying_paths)
                qualifying_paths = {p for p in qualifying_paths
                                    if str(p) not in _outlier_paths}
                _dropped = _before - len(qualifying_paths)
                if _dropped:
                    logger.info(f"[stack] {target_name}: dropped {_dropped} alignment "
                                f"outlier(s) from stack (solved position off-cluster)")

        # ── EQ-mode detection & optional filter ───────────────────────────
        # Read EQMODE from FITS headers for qualifying frames.
        # EQMODE=1 → equatorial mode (consistent framing)
        # EQMODE=0 → alt-az (field rotation; mixing causes diagonal artifacts)
        # EQMODE absent → old firmware predating the flag: infer from EXPTIME. The S50
        #   cannot expose 30s in alt-az (field rotation smears stars), so any ≥30s sub is
        #   EQ; shorter flagless subs are alt-az.
        # Always detect mixing; filter to EQ-only when eq_only=True.
        _EQ_MIN_EXP_S = 29.5  # ≥30s subs require an EQ mount on the S50
        try:
            from astropy.io import fits as _af_eq
            _eq_count = _altaz_count = _unknown_count = 0
            _altaz_paths: set[str] = set()
            _eq_resolved: dict[str, int] = {}
            for _fp in list(qualifying_paths):
                try:
                    _hdr = _af_eq.getheader(str(_fp), ext=0)
                    _em = _hdr.get("EQMODE")
                    if _em == 1:
                        _is_eq = True
                    elif _em == 0:
                        _is_eq = False
                    else:
                        # EQMODE absent (pre-flag firmware): infer EQ from exposure time.
                        try:
                            _exp = float(_hdr.get("EXPTIME") or 0)
                        except Exception:
                            _exp = 0.0
                        _is_eq = _exp >= _EQ_MIN_EXP_S
                    if _is_eq:
                        _eq_count += 1
                        _eq_resolved[str(_fp)] = 1
                    else:
                        _altaz_count += 1
                        _altaz_paths.add(str(_fp))
                        _eq_resolved[str(_fp)] = 0
                except Exception:
                    _unknown_count += 1

            # Persist resolved eq_mode to DB lazily (best-effort, won't block stacking)
            try:
                with _db_conn() as _c_eq:
                    for _fp, _val in _eq_resolved.items():
                        _c_eq.execute(
                            "UPDATE light_files SET eq_mode=? WHERE file_path=?",
                            (_val, _fp)
                        )
            except Exception as _db_eq_err:
                logger.debug(f"[stack] {target_name}: eq_mode DB update skipped: {_db_eq_err}")

            total_q = len(qualifying_paths)
            if _altaz_count > 0 and _eq_count > 0:
                _mix_pct = _altaz_count / total_q * 100
                logger.warning(
                    f"[stack] {target_name}: MIXED MOUNT MODE — "
                    f"{_eq_count} EQ + {_altaz_count} alt-az frames ({_mix_pct:.0f}% alt-az). "
                    f"This causes diagonal banding and low efficiency. "
                    f"Re-stack with eq_only=True or exptime filter to use EQ frames only."
                )
                if not eq_only:
                    telegram.send(
                        f"⚠️ <b>{target_name}</b> stack: mixed EQ + alt-az frames detected "
                        f"({_eq_count} EQ / {_altaz_count} alt-az). "
                        f"Consider re-stacking with <b>EQ only</b> to avoid diagonal banding."
                    )

            if eq_only and _eq_count > 0:
                qualifying_paths -= _altaz_paths
                _removed = _altaz_count
                logger.info(
                    f"[stack] {target_name}: eq_only=True — excluded {_removed} alt-az frames "
                    f"(EQMODE=0 or flagless <30s), {len(qualifying_paths)} EQ frames remain"
                )
                if len(qualifying_paths) == 0:
                    return {"success": False, "error": "eq_only=True but no equatorial-mode frames found"}
            elif eq_only and _eq_count == 0:
                # Pure alt-az target — no EQ frames to keep, so eq_only filtering is a no-op.
                # Banding only arises from MIXING EQ + alt-az; an all-alt-az stack is fine.
                logger.info(
                    f"[stack] {target_name}: eq_only=True but target is 100% alt-az "
                    f"({_altaz_count} frames) — no mixing risk, stacking all alt-az frames"
                )

        except Exception as _eq_err:
            logger.debug(f"[stack] {target_name}: eq_mode detection failed: {_eq_err}")

        # Resolve to existing paths; sort for reproducible sequential naming
        to_copy = sorted(Path(p) for p in qualifying_paths if Path(p).exists())
        n_missing = len(qualifying_paths) - len(to_copy)

        logger.info(f"[stack] {target_name}: {len(to_copy)} frames pass threshold "
                    f"(bottom_pct={bottom_pct:.0%} min_stars={min_stars} ecc<{ecc_threshold} "
                    f"sky_factor={sky_level_factor} grad<{gradient_threshold}"
                    f"{f' exptime={exptime}s' if exptime else ''})"
                    f"{f' — {n_missing} paths missing from disk' if n_missing else ''}")

        if not to_copy:
            return {"success": False, "error": "No non-excluded frames to stack"}

        # PI ImageIntegration peak RAM ≈ baseline + (canvas_Mpx × ~10.7 MB) × N_frames.
        # Driver is CANVAS SIZE × N — one resident RGB-float frame per sub — NOT the
        # per-file buffer. Measured at 2122² (4.5 Mpx): 199→15.4 GB, 531→31.4 GB → linear
        # fit 48.2 MB/frame (10.7 MB/Mpx/frame), baseline ≈ 5.85 GB. stackSizeMB/bufferSizeMB
        # are INERT under --automation-mode (verified), so this is the real cap. Cull, eq_only,
        # add RAM, or stack per-panel to stay under budget; single-pass cannot be tuned around.
        if engine == "pixinsight_register":
            _canvas_mpx = 4.5  # fallback: native-ish S50 canonical canvas
            try:
                from nas_server.canonical_frame import canonical_target_wcs
                _cw = canonical_target_wcs(target_name, drizzled=drizzle)
                if _cw:
                    _h, _w = _cw[1]
                    _canvas_mpx = (_h * _w) / 1e6
            except Exception as _cmx_err:
                logger.debug(f"[stack] {target_name}: canvas-size lookup failed ({_cmx_err}); "
                             f"using {_canvas_mpx} Mpx fallback")
            _budget_mb = float(settings.get("pi_register_mem_budget_gb", 32.0)) * 1024
            _baseline_mb = 5850.0
            _per_frame_mb = _canvas_mpx * 10.7
            _pi_max = max(2, int((_budget_mb - _baseline_mb) / _per_frame_mb))
            if len(to_copy) > _pi_max:
                msg = (f"PI register engine: {len(to_copy)} frames exceeds the memory-safe "
                       f"cap of {_pi_max} for a {_canvas_mpx:.1f} Mpx canvas "
                       f"({_per_frame_mb:.0f} MB/frame + {_baseline_mb/1024:.1f} GB baseline "
                       f"under a {_budget_mb/1024:.0f} GB budget). Raise cull/eq_only, "
                       f"stack per-panel, or use another engine.")
                telegram.send(f"⚠️ <b>Stack aborted</b>: <code>{target_name}</code>\n{msg}")
                logger.error(f"[stack] {target_name}: {msg}")
                return {"success": False, "error": msg}

        # Choose work directory: local SSD first (fast; QEMU discard reclaims weekly via fstrim),
        # fall back to NAS HDD if local is low, abort if neither has space.
        _frame_size_mb = 14
        # PI drizzle produces registered .xisf (4x raw) + output; needs 6x headroom.
        # Siril drizzle registered frames are 4x larger → 5x; standard → 3x.
        if engine == "pixinsight_register":
            _space_mult = 6
        elif drizzle:
            _space_mult = 5
        else:
            _space_mult = 3
        _needed_mb = len(to_copy) * _frame_size_mb * _space_mult
        _local_free_mb = shutil.disk_usage("/tmp").free // (1024 * 1024)
        nas_work_root = Path(settings.get("nas_work_path", "/mnt/nas_data/_stack_work"))
        try:
            _nas_free_mb = shutil.disk_usage("/mnt/nas_data").free // (1024 * 1024)
        except Exception:
            _nas_free_mb = 0

        if _local_free_mb >= _needed_mb:
            work_dir = Path(f"/tmp/seestar_stack_{safe_name}_{timestamp}")
            logger.info(f"[stack] {target_name}: local work dir → {work_dir}")
        elif _nas_free_mb >= _needed_mb:
            nas_work_root.mkdir(parents=True, exist_ok=True)
            work_dir = nas_work_root / f"seestar_stack_{safe_name}_{timestamp}"
            telegram.send(
                f"⚠️ <b>Local disk low ({_local_free_mb // 1024} GB free)</b>: "
                f"<code>{target_name}</code>\n"
                f"Falling back to NAS work dir — stack will be slower."
            )
            logger.warning(f"[stack] {target_name}: local disk low, NAS fallback → {work_dir}")
        else:
            msg = (
                f"⚠️ <b>Stack aborted</b>: <code>{target_name}</code>\n"
                f"Insufficient space on local ({_local_free_mb // 1024} GB) "
                f"and NAS ({_nas_free_mb // 1024} GB).\n"
                f"Need ~{_needed_mb // 1024} GB."
            )
            telegram.send(msg)
            logger.error(f"[stack] disk check failed: need {_needed_mb}MB, "
                         f"local={_local_free_mb}MB, nas={_nas_free_mb}MB")
            return {"success": False,
                    "error": f"Insufficient space: need ~{_needed_mb // 1024}GB, "
                             f"local={_local_free_mb // 1024}GB, nas={_nas_free_mb // 1024}GB"}

        work_light = work_dir / "light"

        # --- Phase 1: Copy ---
        _set_stack_phase(target_name, "copying")
        telegram.send(
            f"\U0001f4c2 <b>Copying {len(to_copy)} frames</b>: <code>{target_name}</code>"
        )
        logger.info(f"[stack] {target_name}: copying {len(to_copy)} frames to {work_dir}")
        work_light.mkdir(parents=True, exist_ok=True)
        # Sequential naming prevents collisions when frames come from multiple subfolders
        indexed = [(src, work_light / f"light_{i+1:06d}.fit")
                   for i, src in enumerate(to_copy)]
        def _copy_frame(pair):
            src, dest = pair
            if not dest.exists():
                shutil.copy2(src, dest)
        with ThreadPoolExecutor(max_workers=8) as _pool:
            list(_pool.map(_copy_frame, indexed))

        # --- Phase 1b: Calibrate NINA raw frames ---
        # Frames with source='nina' are raw CFA; subtract dark + divide flat before Siril runs.
        # seestar_app frames are already calibrated by the SeeStar firmware — skip them.
        try:
            with _db_conn() as _c:
                _nina_src = {r[0] for r in _c.execute(
                    "SELECT file_path FROM light_files WHERE target=? AND source='nina'",
                    (target_name,)
                ).fetchall()}
            # Also check associated targets (mosaic panels may be NINA-sourced)
            for _ct in _included_targets - {target_name}:
                with _db_conn() as _c:
                    _nina_src.update(r[0] for r in _c.execute(
                        "SELECT file_path FROM light_files WHERE target=? AND source='nina'",
                        (_ct,)
                    ).fetchall())

            if _nina_src:
                _n_cal, _cal_warn = _apply_nina_calibration(indexed, _nina_src)
                if _cal_warn:
                    telegram.send(f"⚠️ <b>{target_name}</b>: {_cal_warn}")
                elif _n_cal:
                    logger.info(f"[stack] {target_name}: calibrated {_n_cal} NINA frames")
        except Exception as _ce:
            logger.warning(f"[stack] {target_name}: NINA calibration step failed: {_ce}")

        # Read first frame metadata for naming
        frame_meta = _read_first_frame_meta(work_light)
        obs_date = frame_meta.get("obs_date")
        exptime = frame_meta.get("exptime") or 0
        sensor_temp = frame_meta.get("sensor_temp")
        # Sum actual per-frame exposure times from DB (handles mixed sub lengths correctly).
        # Fall back to first-frame × count if DB lookup fails or returns nothing.
        try:
            _to_copy_strs = [str(p) for p in to_copy]
            _placeholders = ",".join("?" * len(_to_copy_strs))
            with _db_conn() as _c:
                _row = _c.execute(
                    f"SELECT SUM(exposure_time) FROM light_files WHERE file_path IN ({_placeholders})",
                    _to_copy_strs
                ).fetchone()
            total_integration = float(_row[0]) if (_row and _row[0] is not None) else exptime * len(to_copy)
            logger.info(f"[stack] {target_name}: total integration from DB sum = {total_integration:.1f}s "
                        f"(first-frame exptime={exptime}s × {len(to_copy)} = {exptime * len(to_copy):.0f}s)")
        except Exception as _tie:
            logger.warning(f"[stack] {target_name}: DB exposure sum failed ({_tie}), using exptime×count")
            total_integration = exptime * len(to_copy)

        # --- Phase 2: Stack ---
        # PI drizzle requires full plate solve per frame \u2014 incompatible with fast mode.
        if engine == "pixinsight_register" and fast:
            logger.warning("[stack] fast=True incompatible with pixinsight_register \u2014 using siril")
            engine = "siril"

        if engine == "imagemm":
            telegram.send(
                f"\u2699\ufe0f <b>Stacking (Image MM)</b>: <code>{target_name}</code>\n"
                f"Siril register + SASpro deconvolution on {len(to_copy)} frames\u2026"
            )
            tool_label = "saspro"
        elif engine == "pixinsight_wbpp":
            telegram.send(
                f"\u2699\ufe0f <b>Stacking (PI ImageIntegration)</b>: <code>{target_name}</code>\n"
                f"Siril register + PI SNR-weighted integration on {len(to_copy)} frames\u2026"
            )
            tool_label = "pi_wbpp"
        elif engine == "pixinsight_register":
            _canon_note = ("\n\U0001f9ed <i>Canonical framing</i> \u2014 registering onto the fixed "
                           "per-target grid" if canonical_ref else "")
            telegram.send(
                f"\u2699\ufe0f <b>Stacking (PI Register+Stack)</b>: <code>{target_name}</code>\n"
                f"PI Debayer + StarAlignment + ImageIntegration on {len(to_copy)} frames\u2026"
                f"{_canon_note}"
            )
            tool_label = "pi_register_canonical" if canonical_ref else "pi_register"
        else:
            telegram.send(
                f"\u2699\ufe0f <b>Stacking</b>: <code>{target_name}</code>\n"
                f"Running Siril 1.4 on {len(to_copy)} frames\u2026"
            )
            tool_label = "siril"

        _set_stack_phase(target_name, "stacking")
        logger.info(f"[stack] {target_name}: launching {engine} stack")
        t0 = datetime.now()

        if engine == "imagemm":
            result_ok, log_output = _run_imagemm_engine(
                target_name, work_dir, len(to_copy), framing=framing, hero=hero, drizzle=drizzle
            )
        elif engine == "pixinsight_wbpp":
            result_ok, log_output = _run_wbpp_engine(
                target_name, work_dir, len(to_copy), framing=framing, hero=hero, drizzle=drizzle
            )
        elif engine == "pixinsight_register":
            result_ok, log_output = _run_pi_drizzle_engine(
                target_name, work_dir, len(to_copy),
                reference_image=canonical_ref, drizzle=drizzle
            )
        else:
            # Two-phase Siril: register-only SSF → stack-only SSF.
            # Avoids the execute_idle_and_wait_for_it headless bug that silently
            # skips seqapplyreg in a combined SSF, causing "stack r_light" to fail.
            if drizzle and framing == "max" and hero:
                _reg_ssf   = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO
                _stack_ssf = STACK_ONLY_SCRIPT_PATH_MAXFRAMING
            elif drizzle and framing == "max":
                _reg_ssf   = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING
                _stack_ssf = STACK_ONLY_SCRIPT_PATH_MAXFRAMING
            elif drizzle and hero:
                _reg_ssf   = REGISTER_SCRIPT_PATH_DRIZZLE_HERO
                _stack_ssf = STACK_ONLY_SCRIPT_PATH
            elif drizzle:
                _reg_ssf   = REGISTER_SCRIPT_PATH_DRIZZLE
                _stack_ssf = STACK_ONLY_SCRIPT_PATH
            elif framing == "max" and hero:
                _reg_ssf   = REGISTER_SCRIPT_PATH_MAXFRAMING_HERO
                _stack_ssf = STACK_ONLY_SCRIPT_PATH_MAXFRAMING
            elif framing == "max":
                _reg_ssf   = REGISTER_SCRIPT_PATH_MAXFRAMING
                _stack_ssf = STACK_ONLY_SCRIPT_PATH_MAXFRAMING
            elif hero:
                _reg_ssf   = REGISTER_SCRIPT_PATH_HERO
                _stack_ssf = STACK_ONLY_SCRIPT_PATH
            else:
                _reg_ssf   = REGISTER_SCRIPT_PATH
                _stack_ssf = STACK_ONLY_SCRIPT_PATH

            # All drizzle stacks use two-phase approach (Naztronomy-style):
            # Phase A: convert + seqplatesolve (expected to crash on .seq write;
            #          WCS data IS written to individual FITS headers before crash).
            # Parse log for "N images platesolved" — if N>0, WCS exists → run Phase B.
            # If N=0 (plate solver truly failed), fall back to non-drizzle registration.
            if drizzle:
                import re as _re
                logger.info(f"[stack] {target_name}: drizzle Phase A — convert + platesolve")
                proc_ps = subprocess.Popen(
                    ["siril-cli", "-s", str(PLATESOLVE_DRIZZLE_SCRIPT_PATH), "-d", str(work_dir)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                _register_stack(target_name, proc_ps, len(to_copy), t0, work_dir)
                stdout_ps, stderr_ps = proc_ps.communicate()
                _unregister_stack(target_name)
                log_output = stdout_ps + stderr_ps

                # Extract N from "N images successfully platesolved out of M included"
                _ps_match = _re.search(r"(\d+) images? successfully platesolved", log_output)
                _n_solved = int(_ps_match.group(1)) if _ps_match else 0
                logger.info(f"[stack] {target_name}: platesolve: {_n_solved} frames solved")

                _process_dir = work_dir / "process"
                if _n_solved > 0:
                    # WCS in FITS headers — select Phase B SSF and run register + apply
                    if framing == "max" and hero:
                        _phase_b_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO_PHASE_B
                    elif framing == "max":
                        _phase_b_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_PHASE_B
                    else:
                        _phase_b_ssf = _reg_ssf  # non-maxframing Phase B SSF
                    logger.info(f"[stack] {target_name}: drizzle Phase B — {_phase_b_ssf.name}")
                    proc_reg = subprocess.Popen(
                        ["siril-cli", "-s", str(_phase_b_ssf), "-d", str(work_dir)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    _register_stack(target_name, proc_reg, len(to_copy), t0, work_dir)
                    stdout_reg, stderr_reg = proc_reg.communicate()
                    _unregister_stack(target_name)
                    log_output += "\n[drizzle-reg] " + stdout_reg + stderr_reg
                    result_ok = (proc_reg.returncode == 0
                                 and "Script execution failed" not in (stdout_reg + stderr_reg))
                    # Also treat as success if r_light frames exist (apply ran before crash)
                    if not result_ok:
                        _r_light = list(_process_dir.glob("r_light_*.fit")) if _process_dir.exists() else []
                        if _r_light:
                            logger.info(f"[stack] {target_name}: drizzle reg crashed but "
                                        f"{len(_r_light)} r_light frames exist — treating as success")
                            result_ok = True
                else:
                    # Plate solver couldn't solve any frame — fall back to non-drizzle registration
                    logger.warning(f"[stack] {target_name}: 0 frames platesolved, "
                                   f"falling back to {'image-matching maxframing' if framing == 'max' else 'standard 1× registration'}")
                    telegram.send(
                        f"⚠️ <b>Drizzle unavailable</b>: <code>{target_name}</code>\n"
                        f"Plate solver solved 0 frames (dense/bright field). "
                        f"Falling back to {'image-matching max-framing' if framing == 'max' else 'standard 1× stacking'}."
                    )
                    if framing == "max":
                        # MF_APPLY starts at cd process and uses the existing light.seq +
                        # light_*.fit that Phase A's convert step created — don't delete them.
                        _fallback_reg = (REGISTER_SCRIPT_PATH_MF_APPLY_HERO if hero
                                         else REGISTER_SCRIPT_PATH_MF_APPLY)
                    else:
                        # Standard register SSF re-does convert -debayer from cd light,
                        # so the raw (non-debayered) Phase A converts must be removed first.
                        if _process_dir.exists():
                            for _f in (list(_process_dir.glob("light_*.fit"))
                                       + list(_process_dir.glob("*.seq"))):
                                try:
                                    _f.unlink()
                                except Exception:
                                    pass
                        _fallback_reg = REGISTER_SCRIPT_PATH_HERO if hero else REGISTER_SCRIPT_PATH
                    proc_fb = subprocess.Popen(
                        ["siril-cli", "-s", str(_fallback_reg), "-d", str(work_dir)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    _register_stack(target_name, proc_fb, len(to_copy), t0, work_dir)
                    stdout_fb, stderr_fb = proc_fb.communicate()
                    _unregister_stack(target_name)
                    log_output += "\n[drizzle-fallback] " + stdout_fb + stderr_fb
                    result_ok = (proc_fb.returncode == 0
                                 and "Script execution failed" not in (stdout_fb + stderr_fb))

            else:
                # Non-drizzle: single-phase register SSF (convert + seqplatesolve + register + seqapplyreg).
                logger.info(f"[stack] {target_name}: Siril register via {_reg_ssf.name}")
                proc = subprocess.Popen(
                    ["siril-cli", "-s", str(_reg_ssf), "-d", str(work_dir)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                _register_stack(target_name, proc, len(to_copy), t0, work_dir)
                stdout, stderr = proc.communicate()
                _unregister_stack(target_name)
                log_output = stdout + stderr
                result_ok = proc.returncode == 0 and "Script execution failed" not in log_output

                # Standard non-maxframing recovery: if seqplatesolve .seq write crashed
                # (Siril regression) but r_light frames exist, registration succeeded.
                if not result_ok and framing != "max":
                    _process_dir = work_dir / "process"
                    _r_light = list(_process_dir.glob("r_light_*.fit")) if _process_dir.exists() else []
                    if _r_light:
                        logger.info(f"[stack] {target_name}: SSF failed but "
                                    f"{len(_r_light)} r_light frames exist — treating as success")
                        result_ok = True

                # Maxframing fallback: if seqplatesolve+seqapplyreg crashed but the convert
                # step produced frames in process/, attempt image-matching register+apply.
                # Note: register -2pass is removed from maxframing SSFs (it overwrites the
                # WCS cross-panel offsets seqplatesolve writes to light_.seq). This fallback
                # uses seestar_register_mf_apply.ssf which starts at cd process (no re-convert)
                # and runs image-matching register — works for well-overlapping panels only.
                if not result_ok and framing == "max":
                    _process_dir = work_dir / "process"
                    _r_light = list(_process_dir.glob("r_light_*.fit")) if _process_dir.exists() else []
                    _converted = list(_process_dir.glob("light_*.fit")) if _process_dir.exists() else []
                    if _r_light:
                        logger.info(f"[stack] {target_name}: maxframing SSF failed but "
                                    f"{len(_r_light)} r_light frames exist — treating as success")
                        result_ok = True
                    elif _converted:
                        logger.warning(f"[stack] {target_name}: seqplatesolve failed "
                                       f"({len(_converted)} frames converted); falling back to "
                                       f"image-matching maxframing")
                        telegram.send(
                            f"⚠️ <b>Registration failed</b>: <code>{target_name}</code>\n"
                            f"Plate solve or seqapplyreg failed. "
                            f"Falling back to image-matching max-framing (single-panel only)."
                        )
                        # Reset frame selection in light_.seq — seqplatesolve marks only
                        # solved frames as selected, so the fallback register would only
                        # process those. Re-select all frames before re-registering.
                        _seq_path = _process_dir / "light_.seq"
                        if _seq_path.exists():
                            try:
                                _seq_lines = _seq_path.read_text().splitlines()
                                _fixed = []
                                for _sl in _seq_lines:
                                    if _sl.startswith("I ") and len(_sl.split()) >= 3:
                                        parts = _sl.split()
                                        parts[2] = "1"
                                        _fixed.append(" ".join(parts))
                                    else:
                                        _fixed.append(_sl)
                                _seq_path.write_text("\n".join(_fixed) + "\n")
                                logger.info(f"[stack] {target_name}: reset light_.seq selection "
                                            f"to all {len(_converted)} frames for fallback register")
                            except Exception as _se:
                                logger.warning(f"[stack] {target_name}: could not reset seq: {_se}")
                        _apply_ssf = (REGISTER_SCRIPT_PATH_MF_APPLY_HERO if hero
                                      else REGISTER_SCRIPT_PATH_MF_APPLY)
                        proc_fb = subprocess.Popen(
                            ["siril-cli", "-s", str(_apply_ssf), "-d", str(work_dir)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        )
                        _register_stack(target_name, proc_fb, len(to_copy), t0, work_dir)
                        stdout_fb, stderr_fb = proc_fb.communicate()
                        _unregister_stack(target_name)
                        log_output += "\n[fallback] " + stdout_fb + stderr_fb
                        result_ok = (proc_fb.returncode == 0
                                     and "Script execution failed" not in (stdout_fb + stderr_fb))

            # Harvest per-sub plate-solve positions (near-zero cost — seqplatesolve
            # already wrote the WCS into process/light_*.fit). Only the maxframing/
            # drizzle SSFs run seqplatesolve; on other paths frames have no absolute
            # WCS and are skipped (mark_failed=False), leaving them for idle/on-demand.
            if result_ok:
                try:
                    from nas_server.sub_solver import harvest_solves, flag_alignment_outliers
                    _nh = harvest_solves(work_dir / "process", to_copy, mark_failed=False)
                    if _nh:
                        logger.info(f"[stack] {target_name}: harvested {_nh} sub plate-solves")
                        try:
                            flag_alignment_outliers(target_name)
                        except Exception as _fe:
                            logger.debug(f"[stack] {target_name}: outlier flag skipped: {_fe}")
                except Exception as _he:
                    logger.debug(f"[stack] {target_name}: solve harvest skipped: {_he}")

            # Phase B: stack (only if register succeeded)
            if result_ok:
                logger.info(f"[stack] {target_name}: Siril stack via {_stack_ssf.name}")
                proc2 = subprocess.Popen(
                    ["siril-cli", "-s", str(_stack_ssf), "-d", str(work_dir)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                _register_stack(target_name, proc2, len(to_copy), t0, work_dir)
                stdout2, stderr2 = proc2.communicate()
                _unregister_stack(target_name)
                log_output += "\n" + stdout2 + stderr2
                _stack_out = stdout2 + stderr2
                result_ok = (proc2.returncode == 0
                             and "Script execution failed" not in _stack_out)
                logger.info(f"[stack] {target_name}: stack returncode={proc2.returncode} "
                            f'script_failed={"Script execution failed" in _stack_out}')
                # Siril 1.4.x headless crash at post-stack finalization (execute_idle_and_wait_for_it /
                # save_undo_state bug): result.fit is written before crash → treat as success if present.
                # Brief sleep allows OS to flush any buffered write before we check.
                if not result_ok:
                    import time as _time
                    _time.sleep(2)
                    _result_path = work_dir / "result.fit"
                    _result_sz = _result_path.stat().st_size if _result_path.exists() else 0
                    logger.info(f"[stack] {target_name}: recovery check — "
                                f"result.fit={'exists' if _result_path.exists() else 'missing'} "
                                f"size={_result_sz} bytes")
                    if _result_sz > 0:
                        logger.info(f"[stack] {target_name}: siril crashed after stack "
                                    f"(headless finalization bug) but result.fit exists "
                                    f"({_result_sz} bytes) — treating as success")
                        result_ok = True

        elapsed = (datetime.now() - t0).total_seconds()

        if not result_ok:
            logger.error(f"[stack] {engine} failed for {target_name}:\n{log_output[-2000:]}")
            telegram.send(
                f"❌ <b>Stack failed</b>: <code>{target_name}</code> [{engine}]\n"
                f"<pre>{log_output[-600:]}</pre>"
            )
            try:
                from nas_server.database import save_stacking_run
                save_stacking_run(target_name, engine, started_at, len(to_copy),
                                  elapsed, success=False,
                                  error=f"{engine} stack failed",
                                  log_tail=log_output,
                                  params=_stack_params)
            except Exception:
                pass
            # Cleanup via subprocess: survives daemon-thread kill when uvicorn crashes on failure.
            # The finally block below also runs normally, but won't execute if the process dies first.
            if work_dir is not None and work_dir.exists():
                subprocess.Popen(["rm", "-rf", str(work_dir)])
                logger.info(f"[stack] {target_name}: async cleanup of {work_dir}")
            return {"success": False, "error": f"{engine} stack failed", "log": log_output[-2000:]}

        # --- Phase 3: Move to _processed/ ---
        _set_stack_phase(target_name, "moving")
        processed_dir = target_dir / "_processed"
        # parents=True: the target may be a catalog/alias name (e.g. "Flame Nebula")
        # whose frames live under associated folders ("IC 434"/"NGC 2024") and which
        # has no library directory of its own — without parents this raised
        # "No such file or directory" after a full stack run. See database alias.
        processed_dir.mkdir(parents=True, exist_ok=True)

        stack_name = make_processed_filename(
            target_name, obs_date, total_integration, sensor_temp,
            tool_label, "stack", "fit"
        )
        preview_name = make_processed_filename(
            target_name, obs_date, total_integration, sensor_temp,
            tool_label, "stack", "jpg"
        )

        result_fit = work_dir / "result.fit"
        preview_jpg = work_dir / "preview.jpg"

        # Track every flip applied to result.fit so the coverage map (built from
        # the unflipped registered frames) can be replayed into the same
        # orientation as the finished stack.
        _cov_flip_ops: list[tuple] = []

        # --- Phase 2.5: Plate solve + orientation detection ---
        _set_stack_phase(target_name, "solving")
        # Siril leaves BAYERPAT in the header of debayered stacks. PI interprets this
        # as a raw CFA mosaic, causing ImageSolver and later ColorCalibration to fail.
        _strip_bayerpat(result_fit)
        # Solve first, then detect orientation from the job JSON.
        # pi_solve.js now reads CDELT1/CDELT2 from PI's own SaveKeywords (not hardcoded),
        # so job["south_up"] and job["mirrored"] carry real parity info.
        # If solve fails, fall back to empirical always-flip for max framing.
        _solve_result = _flip_and_solve(result_fit, target_name, notify=False)
        _did_flip = False
        if framing == "max":
            if _solve_result.get("ok"):
                _south_up = _solve_result.get("south_up")
                _mirrored = _solve_result.get("mirrored")
                # south_up may be None if pi_solve.js is an older build (fall back to FITS check)
                if _south_up is None:
                    _south_up = _is_south_up(result_fit)
                logger.info(f"[stack] {target_name}: orientation — south_up={_south_up} mirrored={_mirrored}")
                if _south_up:
                    logger.info(f"[stack] {target_name}: south-up — flipping vertically")
                    _flip_fits_inplace(result_fit, axes=(-2,))
                    _cov_flip_ops.append((-2,))
                    _did_flip = True
                if _mirrored:
                    logger.info(f"[stack] {target_name}: east-right mirror — flipping horizontally")
                    _flip_fits_inplace(result_fit, axes=(-1,))
                    _cov_flip_ops.append((-1,))
                    _did_flip = True
                if _did_flip:
                    _solve_result = _flip_and_solve(result_fit, target_name, notify=True)
                else:
                    logger.info(f"[stack] {target_name}: maxframing image already correct orientation — no flip")
                    # Still send solved notification
                    telegram.send(
                        f"🔭 <b>Plate solved</b>: <code>{target_name}</code>\n"
                        f"RA={_solve_result.get('ra_solved', 0):.4f}° "
                        f"Dec={_solve_result.get('dec_solved', 0):.4f}° "
                        f"res={_solve_result.get('resolution_arcsec', 0):.2f}\"/px"
                    )
            else:
                # Solve failed — apply empirical vertical flip (reliable for Siril 1.4.x max framing)
                logger.info(f"[stack] {target_name}: solve failed; applying empirical maxframing flip")
                _flip_fits_inplace(result_fit, axes=(-2,))
                _cov_flip_ops.append((-2,))
                _did_flip = True
                _flip_and_solve(result_fit, target_name, notify=True)
        elif _solve_result.get("ok"):
            telegram.send(
                f"🔭 <b>Plate solved</b>: <code>{target_name}</code>\n"
                f"RA={_solve_result.get('ra_solved', 0):.4f}° "
                f"Dec={_solve_result.get('dec_solved', 0):.4f}° "
                f"res={_solve_result.get('resolution_arcsec', 0):.2f}\"/px"
            )

        # North-up correction for non-maxframing stacks.
        # Siril seqplatesolve writes CDELT2 > 0 (standard FITS convention: north up
        # in astronomical display). But astropy loads FITS row 1 → data[0], and PIL
        # renders data[0] at the top — so CDELT2 > 0 means south-up in PIL output.
        # Flip data and negate CDELT2 so the file is north-up for all consumers.
        # Maxframing stacks are excluded here — they have their own orientation
        # detection + flip + re-solve loop above that normalises orientation correctly.
        if framing != "max":
            if _north_up_fits_inplace(result_fit, target_name):
                _cov_flip_ops.append((-2,))

        # Generate preview for all engines (removed from inside engine functions)
        if not preview_jpg.exists():
            _generate_preview_jpg(result_fit, preview_jpg)

        out_fit = processed_dir / stack_name
        out_jpg = processed_dir / preview_name

        if result_fit.exists():
            shutil.move(str(result_fit), str(out_fit))
        else:
            return {"success": False, "error": "Siril produced no result.fit"}
        if preview_jpg.exists():
            shutil.move(str(preview_jpg), str(out_jpg))

        # Embed integration metadata into the stack header for every engine. Siril
        # writes STACKCNT/LIVETIME natively, but SASpro (Image MM) and PI stacks do
        # not — so the downstream process step (which may run on a worker node with an
        # empty local DB) has no authoritative source for frame count / integration.
        # Writing them here makes the FITS header the single source of truth.
        try:
            from astropy.io import fits as _meta_fits
            with _meta_fits.open(str(out_fit), mode="update", memmap=False) as _mh:
                _hdr = _mh[0].header
                _hdr["STACKCNT"] = (int(len(to_copy)), "Number of integrated frames")
                _hdr["LIVETIME"] = (float(total_integration), "Total integration time [s]")
                _mh.flush()
        except Exception as _mhe:
            logger.warning(f"[stack] {target_name}: failed to embed STACKCNT/LIVETIME header: {_mhe}")

        # --- Frame-coverage map (built before work_dir cleanup) ---
        # Persist beside the stack as <stem>_coverage.fit for the auto-process
        # crop step (coverage / intersection candidates). Non-fatal.
        try:
            _cov, _cov_n = _build_coverage_map(work_dir / "process", target_name)
            if _cov is not None:
                _cov_path = out_fit.with_name(out_fit.stem + "_coverage.fit")
                _persist_coverage_map(_cov, _cov_n, _cov_path, flip_ops=_cov_flip_ops)
                logger.info(f"[stack] {target_name}: coverage map → {_cov_path.name}")
        except Exception as _ce:
            logger.warning(f"[stack] {target_name}: coverage map failed (non-fatal): {_ce}")

        # --- Write DB row ---
        flags = json.dumps({"debayer": True})
        notes = f"{len(to_copy)} frames \u00b7 {elapsed:.0f}s stack time"
        upsert_processed_file(
            target=target_name,
            file_path=str(out_fit),
            filename=stack_name,
            tool="Siril",
            step="stack",
            total_integration=total_integration,
            frame_count=len(to_copy),
            sensor_temp=sensor_temp,
            obs_date=obs_date,
            flags=flags,
            notes=notes,
            is_auto=1,
        )

        # Update pipeline stage
        set_pipeline_stage(target_name, "stacked", notes=notes)

        # --- Physics metrics (before Claude so they feed into the prompt) ---
        metrics = {}
        try:
            from nas_server.stack_assessor import assess_stack, get_frame_snrs
            snrs = get_frame_snrs([str(f) for f in to_copy])
            metrics = assess_stack(out_fit, len(to_copy), snrs,
                                   mask_zero_border=(framing == "max"))
            logger.info(f"[stack] {target_name}: efficiency={metrics.get('efficiency')} "
                        f"snr={metrics.get('snr_stack')} fwhm={metrics.get('fwhm_stack')}")
        except Exception as _pe:
            logger.warning(f"[stack] physics assessment failed (non-fatal): {_pe}")

        # --- Claude assessment (non-fatal) ---
        _set_stack_phase(target_name, "assessing")
        processed_id = _get_processed_id(str(out_fit))
        scores = _run_claude_assessment(target_name, out_jpg, processed_id,
                                        len(to_copy), total_integration, obs_date,
                                        physics=metrics or None)

        # --- Send preview photo to Telegram ---
        if out_jpg.exists():
            hours = total_integration / 3600
            caption_parts = [
                f"<b>{target_name}</b> — stack complete",
                f"{len(to_copy)} frames · {hours:.1f}h integration · {elapsed:.0f}s stack time",
            ]
            # Physics line
            if metrics:
                phys_parts = []
                if metrics.get("snr_stack") is not None:
                    phys_parts.append(f"SNR={metrics['snr_stack']:.1f}")
                if metrics.get("fwhm_stack") is not None:
                    phys_parts.append(f"FWHM={metrics['fwhm_stack']:.2f}px")
                if metrics.get("ecc_stack") is not None:
                    phys_parts.append(f"ecc={metrics['ecc_stack']:.3f}")
                if metrics.get("sigma_sky") is not None:
                    phys_parts.append(f"sky={metrics['sigma_sky']:.1f}")
                if metrics.get("efficiency") is not None:
                    eff_note = " (union canvas)" if framing == "max" else ""
                    phys_parts.append(f"eff={metrics['efficiency']:.2f}{eff_note}")
                if metrics.get("star_count_stack") is not None:
                    phys_parts.append(f"stars={metrics['star_count_stack']:,}")
                if phys_parts:
                    caption_parts.append("📐 " + " · ".join(phys_parts))
            # Claude scores line
            if scores:
                overall = scores.get("overall", "?")
                noise = scores.get("noise", "?")
                color = scores.get("color_balance", "?")
                gradient = scores.get("gradient", "?")
                star_r = scores.get("star_roundness", "?")
                stretch = scores.get("stretch_quality", "?")
                caption_parts.append(
                    f"Claude: {overall}/10 · noise={noise} · color={color} · "
                    f"gradient={gradient} · stars={star_r} · stretch={stretch}"
                )
                for issue in (scores.get("issues") or [])[:2]:
                    caption_parts.append(f"⚠️ {issue}")
            telegram.send_photo(str(out_jpg), caption="\n".join(caption_parts))

        # --- Cosmic Clarity post-processing (non-fatal, opt-in) ---
        _run_cosmic_clarity(target_name, out_fit, processed_id)

        logger.info(f"[stack] {target_name}: done in {elapsed:.0f}s → {out_fit.name}")
        try:
            from nas_server.database import save_stacking_run
            save_stacking_run(target_name, engine, started_at, len(to_copy),
                              elapsed, success=True, output_path=str(out_fit),
                              metrics=metrics, params=_stack_params)
        except Exception as _ae:
            logger.warning(f"[stack] save_stacking_run failed: {_ae}")
            try:
                from nas_server.database import save_stacking_run
                save_stacking_run(target_name, engine, started_at, len(to_copy),
                                  elapsed, success=True, output_path=str(out_fit),
                                  params=_stack_params)
            except Exception:
                pass
        return {
            "success": True,
            "processed_fit": str(out_fit),
            "preview_jpg": str(out_jpg) if out_jpg.exists() else None,
            "frames": len(to_copy),
            "elapsed": elapsed,
        }

    except subprocess.TimeoutExpired:
        try:
            from nas_server.database import save_stacking_run
            save_stacking_run(target_name, engine, started_at, 0, 0,
                              success=False, error="Siril timed out after 1 hour",
                              params=_stack_params)
        except Exception:
            pass
        return {"success": False, "error": "Siril timed out after 1 hour"}
    except Exception as e:
        logger.exception(f"[stack] Unexpected error for {target_name}")
        try:
            from nas_server.database import save_stacking_run
            save_stacking_run(target_name, engine, started_at, 0, 0,
                              success=False, error=str(e), params=_stack_params)
        except Exception:
            pass
        return {"success": False, "error": str(e)}
    finally:
        _unregister_stack(target_name)
        # --- Phase 4: Cleanup ---
        telegram.send(f"\U0001f9f9 <b>Cleaning up temp files</b>: <code>{target_name}</code>")
        try:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
        # Reclaim qcow2 blocks immediately after cleanup so NAS Volume 2 shrinks
        # without waiting for the weekly fstrim.timer
        try:
            import subprocess as _sp
            _sp.run(["sudo", "fstrim", "-v", "/"], capture_output=True, timeout=120)
            logger.info("[stack] fstrim / complete — qcow2 blocks reclaimed")
        except Exception as _fe:
            logger.warning(f"[stack] fstrim failed (non-fatal): {_fe}")


def _run_imagemm_engine(target_name: str, work_dir: Path, frame_count: int,
                        framing: str = "min", hero: bool = False,
                        drizzle: bool = False) -> tuple[bool, str]:
    """Run Siril registration then SASpro Image MM. Returns (success, log_text)."""
    logs = []

    # Step A: Siril calibrate + register (no stack)
    if drizzle and framing == "max" and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO
    elif drizzle and framing == "max":
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING
    elif drizzle and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_HERO
    elif drizzle:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE
    elif framing == "max" and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_MAXFRAMING_HERO
    elif framing == "max":
        _reg_ssf = REGISTER_SCRIPT_PATH_MAXFRAMING
    elif hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_HERO
    else:
        _reg_ssf = REGISTER_SCRIPT_PATH
    logger.info(f"[stack] {target_name}: Siril calibrate+register via {_reg_ssf}")
    proc = subprocess.Popen(
        ["siril-cli", "-s", str(_reg_ssf), "-d", str(work_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stdout, stderr = proc.communicate()
    log_output = stdout + stderr
    logs.append(log_output)

    if proc.returncode != 0 or "Script execution failed" in log_output:
        # Defensive recovery: seqplatesolve .seq crash (Siril regression) — if r_light frames
        # exist, registration succeeded despite the error exit.
        _pd = work_dir / "process"
        _rl = list(_pd.glob("r_light_*.fit")) if _pd.exists() else []
        if not _rl:
            return False, log_output
        logger.info(f"[stack] {target_name}: imagemm register SSF failed but "
                    f"{len(_rl)} r_light frames exist — continuing")

    # Step B: Collect registered frames
    process_dir = work_dir / "process"
    reg_frames = sorted(process_dir.glob("r_light*.fit")) + \
                 sorted(process_dir.glob("r_light*.fits"))
    if not reg_frames:
        return False, f"No registered frames found in {process_dir}"

    # Free the original light/ copies — registered frames are in process/
    shutil.rmtree(work_dir / "light", ignore_errors=True)
    logger.info(f"[stack] {target_name}: {len(reg_frames)} registered frames → Image MM")

    # Step C: Image MM deconvolution stack
    from nas_server.seti_astro import imagemm_stack
    result = imagemm_stack(
        [str(f) for f in reg_frames],
        str(work_dir / "result.fit"),
        iters=20, kappa=2.0, color_mode="PerChannel",
        status_cb=lambda s: logger.info(f"[imagemm] {s}"),
    )
    logs.append(result.get("error", ""))

    if not result["ok"]:
        return False, "\n".join(logs)

    # Step D: Rename MFDeconv output to result.fit (SASpro always prefixes with MFDeconv_)
    actual_output = Path(result["output_path"])
    expected_result = work_dir / "result.fit"
    if actual_output.exists() and actual_output != expected_result:
        shutil.move(str(actual_output), str(expected_result))
        logger.info(f"[stack] Renamed {actual_output.name} → result.fit")

    return True, "\n".join(logs)


def _run_wbpp_engine(target_name: str, work_dir: Path, frame_count: int,
                     framing: str = "min", hero: bool = False,
                     drizzle: bool = False) -> tuple[bool, str]:
    """Siril calibrate+register (optionally with drizzle), then PI ImageIntegration."""
    logs = []

    # Step A: Siril calibrate + register (same as imagemm — no stack)
    if drizzle and framing == "max" and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING_HERO
    elif drizzle and framing == "max":
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_MAXFRAMING
    elif drizzle and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE_HERO
    elif drizzle:
        _reg_ssf = REGISTER_SCRIPT_PATH_DRIZZLE
    elif framing == "max" and hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_MAXFRAMING_HERO
    elif framing == "max":
        _reg_ssf = REGISTER_SCRIPT_PATH_MAXFRAMING
    elif hero:
        _reg_ssf = REGISTER_SCRIPT_PATH_HERO
    else:
        _reg_ssf = REGISTER_SCRIPT_PATH
    logger.info(f"[stack] {target_name}: Siril calibrate+register via {_reg_ssf}")
    proc = subprocess.Popen(
        ["siril-cli", "-s", str(_reg_ssf), "-d", str(work_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stdout, stderr = proc.communicate()
    log_output = stdout + stderr
    logs.append(log_output)

    if proc.returncode != 0 or "Script execution failed" in log_output:
        # Defensive recovery: seqplatesolve .seq crash (Siril regression) — if r_light frames
        # exist, registration succeeded despite the error exit.
        _pd = work_dir / "process"
        _rl = list(_pd.glob("r_light_*.fit")) if _pd.exists() else []
        if not _rl:
            return False, log_output
        logger.info(f"[stack] {target_name}: wbpp register SSF failed but "
                    f"{len(_rl)} r_light frames exist — continuing")

    # Step B: Collect registered frames
    process_dir = work_dir / "process"
    reg_frames = sorted(process_dir.glob("r_light*.fit")) + \
                 sorted(process_dir.glob("r_light*.fits"))
    if not reg_frames:
        return False, f"No registered frames in {process_dir}"

    # Free the original light/ copies — registered frames are in process/
    shutil.rmtree(work_dir / "light", ignore_errors=True)
    logger.info(f"[stack] {target_name}: {len(reg_frames)} registered frames → PI ImageIntegration")

    # Step C: PI ImageIntegration
    # Timeout: 2s/frame non-drizzle, 4s/frame drizzle (4× pixels per frame).
    # 180 min floor covers even large stacks where per-frame time dominates startup.
    _pi_timeout = max(21600, len(reg_frames) * (6 if drizzle else 4))
    result_fit = work_dir / "result.fit"
    from nas_server.pixinsight import run_stack
    pi_result = run_stack(
        input_files=[str(f) for f in reg_frames],
        output_path=str(result_fit),
        rejection="winsorized",
        sigma_low=3.0,
        sigma_high=3.0,
        weight_mode="snr",
        weight_scale="ikss" if hero else "avgdev",
        normalization="additive_scaling",
        evaluate_snr=hero,
        timeout=_pi_timeout,
    )
    logs.append(pi_result.get("log", ""))

    if not pi_result["ok"]:
        return False, "\n".join(logs)

    logger.info(f"[stack] {target_name}: PI ImageIntegration done — {pi_result.get('frames_used')} frames")

    return True, "\n".join(logs)


def _xisf_to_fits(xisf_path: str, fits_path: str) -> None:
    """Convert an XISF file to FITS using siril-cli load/save commands."""
    import tempfile
    xisf_name = Path(xisf_path).name
    fits_stem = Path(fits_path).stem
    work = str(Path(xisf_path).parent)
    ssf = f"load {xisf_name}\nsave {fits_stem}\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ssf",
                                     delete=False, prefix="/tmp/") as tf:
        tf.write(ssf)
        ssf_path = tf.name
    try:
        subprocess.run(
            ["siril-cli", "-s", ssf_path, "-d", work],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        Path(ssf_path).unlink(missing_ok=True)


def _build_coverage_map(process_dir: Path, target_name: str = "") -> tuple["np.ndarray | None", int]:
    """Per-pixel frame-coverage map from registered frames in work_dir/process.

    Sums per-frame valid-pixel masks (finite & non-zero). Siril leaves
    `r_light_*.fit` (astropy); the PI register engine leaves `_d_r.xisf`
    (read via xisf_io.read_xisf). Returns (coverage uint16, n_frames) or
    (None, 0) if no registered frames are found. Streams frames one at a time
    to keep memory flat; the first frame fixes the canonical shape and any
    mismatched frame is skipped.
    """
    import numpy as _np
    from astropy.io import fits as _fits

    if not process_dir.exists():
        return None, 0

    fits_frames = sorted(process_dir.glob("r_light_*.fit"))
    xisf_frames = sorted(process_dir.glob("*_d_r.xisf")) if not fits_frames else []
    frames = fits_frames or xisf_frames
    if not frames:
        return None, 0

    def _load_2d(path: Path) -> "_np.ndarray | None":
        try:
            if path.suffix.lower() == ".xisf":
                from nas_server.xisf_io import read_xisf
                data, _ = read_xisf(str(path))
            else:
                with _fits.open(str(path), memmap=False) as h:
                    data = h[0].data
            if data is None:
                return None
            data = _np.asarray(data)
            if data.ndim == 3:  # (C, H, W) → any channel valid
                valid = _np.isfinite(data) & (data != 0)
                return _np.any(valid, axis=0)
            valid = _np.isfinite(data) & (data != 0)
            return valid
        except Exception as _e:
            logger.warning(f"[stack] coverage: failed to read {path.name}: {_e}")
            return None

    cov = None
    n = 0
    for f in frames:
        mask = _load_2d(f)
        if mask is None:
            continue
        if cov is None:
            cov = _np.zeros(mask.shape, dtype=_np.uint16)
        if mask.shape != cov.shape:
            logger.warning(f"[stack] coverage: shape mismatch {f.name} "
                           f"{mask.shape} != {cov.shape} — skipping")
            continue
        cov += mask.astype(_np.uint16)
        n += 1

    if cov is None or n == 0:
        return None, 0
    logger.info(f"[stack] {target_name}: coverage map built from {n} frames "
                f"(shape {cov.shape}, max {int(cov.max())})")
    return cov, n


def _persist_coverage_map(cov: "np.ndarray", n_frames: int, out_path: Path,
                          flip_ops: "list[tuple]" = None) -> None:
    """Write a coverage map to FITS (uint16), replaying the same orientation
    flips that were applied to the finished stack so the map stays aligned."""
    import numpy as _np
    from astropy.io import fits as _fits
    for axes in (flip_ops or []):
        cov = _np.flip(cov, axis=axes)
    hdr = _fits.Header()
    hdr["COVNFRM"] = (int(n_frames), "Number of registered frames in coverage")
    _fits.writeto(str(out_path), _np.ascontiguousarray(cov.astype(_np.uint16)),
                  header=hdr, overwrite=True)


def _run_pi_drizzle_engine(target_name: str, work_dir: Path,
                            frame_count: int,
                            reference_image: str | None = None,
                            drizzle: bool = False) -> tuple[bool, str]:
    """PI-native pipeline: Debayer + StarAlignment + ImageIntegration in ONE PI session.

    Note: DrizzleIntegration.executeGlobal() silently fails in PI 1.9.3 --automation-mode.
    ImageIntegration works headlessly and gives equivalent quality for this use case.
    Produces result.xisf then converts to result.fit for downstream processing.
    """
    from nas_server.pixinsight import run_register_and_drizzle

    # Collect raw CFA light frames from work_dir/light/
    light_dir = work_dir / "light"
    raw_frames = sorted(set(
        list(light_dir.glob("*.fit")) + list(light_dir.glob("*.fits"))
    ))
    if not raw_frames:
        return False, f"No light frames in {light_dir}"
    logger.info(f"[stack] {target_name}: PI drizzle engine — {len(raw_frames)} CFA frames")

    reg_dir = work_dir / "process"
    reg_dir.mkdir(parents=True, exist_ok=True)
    # Pass .fit path — PI uses file extension to determine format, saves as FITS directly.
    result_fit = work_dir / "result.fit"

    _timeout = max(10800, len(raw_frames) * 20)
    if reference_image:
        logger.info(f"[stack] {target_name}: using canonical SA reference {reference_image}")
    logger.info(f"[stack] {target_name}: PI engine drizzle={'2x' if drizzle else 'off (1x)'}")
    rad_result = run_register_and_drizzle(
        input_files=[str(f) for f in raw_frames],
        output_dir=str(reg_dir),
        output_xisf=str(result_fit),
        timeout=_timeout,
        reference_image=reference_image,
        drizzle=drizzle,
    )
    log_text = rad_result.get("log", "")
    if not rad_result["ok"]:
        logger.error(f"[stack] {target_name}: PI register+drizzle failed")
        return False, log_text
    logger.info(f"[stack] {target_name}: {rad_result['frames_registered']} registered, "
                f"{rad_result['frames_used']} integrated")

    actual_fit = rad_result.get("output_xisf") or str(result_fit)
    logger.info(f"[stack] {target_name}: ImageIntegration done → {Path(actual_fit).name}")

    if not Path(actual_fit).exists():
        logger.error(f"[stack] {target_name}: result FITS not found at {actual_fit}")
        return False, f"Result FITS missing\n{log_text}"

    logger.info(f"[stack] {target_name}: PI drizzle complete — {Path(actual_fit).name} ready")
    return True, log_text


def _maybe_build_canonical_reference(target_name: str, drizzle: bool) -> str | None:
    """Build (or reuse cached) synthetic Gaia reference for canonical framing.

    Returns the reference FITS path when the target has a folio + valid coords so a
    canonical WCS can be derived, else None. The reference renders the Gaia field on
    the fixed per-target canonical grid; feeding it to StarAlignment lands every
    session of the target on identical pixels (cumulative cross-night master stacks).

    First call per target invokes a PI Gaia cone search (~minutes); thereafter the
    cached FITS is reused. Returns None on any failure so the caller falls back to
    the default Siril path — never blocks a stack.
    """
    try:
        from nas_server.target_references import generate_reference
        ref = generate_reference(target_name, drizzled=drizzle)
        if ref:
            logger.info(f"[stack] {target_name}: canonical reference ready → {Path(ref).name}")
        else:
            logger.info(f"[stack] {target_name}: no canonical reference (missing folio/coords)")
        return ref
    except Exception as e:
        logger.warning(f"[stack] {target_name}: canonical reference build failed ({e}) — "
                       f"falling back to default registration")
        return None


def _get_target_radec(target_name: str) -> tuple[float | None, float | None]:
    """Return (ra_deg, dec_deg) hint from targets or light_files table."""
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
        logger.warning(f"[stack] RA/Dec lookup failed for {target_name}: {e}")
    return None, None


def _flip_fits_inplace(fits_path: Path, axes: tuple = (-2,)) -> bool:
    """Flip a FITS file in-place along the given axes.
    axis=-2 = vertical (north-south); axis=-1 = horizontal (east-west / parity).
    Pass axes=(-2, -1) for a 180° rotation (both).
    Handles both 2D (H,W) and 3D (C,H,W) arrays."""
    try:
        from astropy.io import fits as _fits
        import numpy as _np
        with _fits.open(str(fits_path), mode="update", memmap=False) as hdul:
            for hdu in hdul:
                if hdu.data is not None and hdu.data.ndim >= 2:
                    hdu.data = _np.flip(hdu.data, axis=axes)
            hdul.flush()
        return True
    except Exception as e:
        logger.warning(f"[stack] Python FITS flip failed: {e}")
        return False


_BAYER_CV2_STACK = {
    "GRBG": "COLOR_BayerGR2RGB",
    "RGGB": "COLOR_BayerRG2RGB",
    "BGGR": "COLOR_BayerBG2RGB",
    "GBRG": "COLOR_BayerGB2RGB",
}


def _strip_bayerpat(fits_path: Path) -> None:
    """Ensure the stacked FITS is RGB and has no Bayer keywords.

    Siril stacks OSC frames in CFA mode (NAXIS=2, BAYERPAT present). Downstream
    PI steps (SPCC, ColorCalibration) require a 3-channel RGB image. If data is
    2D + BAYERPAT, debayer with OpenCV and overwrite the file as RGB (NAXIS=3).
    If already 3D, just strip the keyword.
    """
    try:
        import numpy as _np
        from astropy.io import fits as _fits
        with _fits.open(str(fits_path)) as hdus:
            hdr = hdus[0].header
            data = hdus[0].data.astype(_np.float32)
            bayerpat = str(hdr.get("BAYERPAT", "")).strip().upper()

        if data.ndim == 2 and bayerpat in _BAYER_CV2_STACK:
            import cv2 as _cv2
            cv2_code = getattr(_cv2, _BAYER_CV2_STACK[bayerpat], None)
            if cv2_code is not None:
                lo, hi = float(data.min()), float(data.max())
                scale = max(hi - lo, 1e-9)
                raw16 = (((data - lo) / scale) * 65535).astype(_np.uint16)
                rgb16 = _cv2.cvtColor(raw16, cv2_code)
                rgb_f = rgb16.astype(_np.float32) / 65535.0 * scale + lo
                rgb_chw = _np.moveaxis(rgb_f, -1, 0)
                with _fits.open(str(fits_path)) as hdus:
                    new_hdr = hdus[0].header.copy()
                for key in ("BAYERPAT", "BAYER", "XBAYEROFF", "YBAYEROFF"):
                    new_hdr.remove(key, ignore_missing=True)
                _fits.PrimaryHDU(rgb_chw, header=new_hdr).writeto(str(fits_path), overwrite=True)
                logger.info(f"[stack] {fits_path.name}: debayered CFA ({bayerpat}) → RGB")
                return

        # 3D or no BAYERPAT — strip keyword in-place
        with _fits.open(str(fits_path), mode="update") as hdus:
            hdr = hdus[0].header
            removed = []
            for key in ("BAYERPAT", "BAYER", "XBAYEROFF", "YBAYEROFF"):
                if key in hdr:
                    hdr.remove(key, ignore_missing=True)
                    removed.append(key)
            if removed:
                hdus.flush()
                logger.info(f"[stack] {fits_path.name}: stripped Bayer keywords: {removed}")
    except Exception as e:
        logger.warning(f"[stack] _strip_bayerpat failed for {fits_path.name}: {e}")


def _north_up_fits_inplace(fits_path: Path, target_name: str = "") -> bool:
    """
    Ensure FITS pixel data is stored north-up (data[0] = north edge, CDELT2 < 0).

    FITS standard stores row 1 at the bottom of the astronomical display. With
    CDELT2 > 0, row 1 = south → astropy loads south into data[0] → PIL renders
    south at top. Flip data vertically and negate CDELT2 so PIL/numpy consumers
    see north at data[0] without needing WCS awareness.

    If CDELT2 is absent or already ≤ 0: no-op (returns False).
    Does not re-solve — just transforms data + headers consistently.
    """
    try:
        from astropy.io import fits as _fits
        import numpy as _np
        with _fits.open(str(fits_path)) as hdul:
            cdelt2 = hdul[0].header.get("CDELT2", None)
        if cdelt2 is None or float(cdelt2) <= 0:
            return False  # Already north-up (or no WCS)
        with _fits.open(str(fits_path), mode="update", memmap=False) as hdul:
            for hdu in hdul:
                if hdu.data is not None and hdu.data.ndim >= 2:
                    hdu.data = _np.flip(hdu.data, axis=-2)
            hdr = hdul[0].header
            naxis2 = int(hdr.get("NAXIS2", 0))
            hdr["CDELT2"] = -float(hdr["CDELT2"])
            if "CRPIX2" in hdr and naxis2 > 0:
                hdr["CRPIX2"] = naxis2 + 1.0 - float(hdr["CRPIX2"])
            # Handle CD / PC matrix variants (negate y-row entries)
            for k in ("CD1_2", "CD2_2", "PC1_2", "PC2_2"):
                if k in hdr:
                    hdr[k] = -float(hdr[k])
            hdul.flush()
        if target_name:
            logger.info(f"[stack] {target_name}: north-up correction applied "
                        f"(CDELT2 was +{abs(float(cdelt2)):.6f}, now negated)")
        return True
    except Exception as e:
        logger.warning(f"[stack] _north_up_fits_inplace failed for "
                       f"{getattr(fits_path, 'name', fits_path)}: {e}")
        return False


def _is_south_up(fits_path: Path) -> bool:
    """
    Return True if the FITS image has south-up orientation (CDELT2 < 0 or CROTA2 ≈ 180°).
    Used after plate-solving to detect whether seqapplyreg -framing=max flipped the output.
    Returns False on any read error (conservative — avoids spurious flips).
    """
    try:
        from astropy.io import fits as _fits
        with _fits.open(str(fits_path)) as hdul:
            hdr = hdul[0].header
            cdelt2 = hdr.get("CDELT2")
            crota2 = float(hdr.get("CROTA2", 0.0))
        south_by_cdelt = cdelt2 is not None and float(cdelt2) < 0
        south_by_rot   = abs(crota2 % 360 - 180) < 10
        return south_by_cdelt or south_by_rot
    except Exception as e:
        logger.warning(f"[stack] WCS orientation check failed for {fits_path.name}: {e}")
        return False


def _flip_and_solve(result_fit: Path, target_name: str, notify: bool = True) -> dict:
    """
    Plate-solve via PI (optional, non-fatal).
    notify=False suppresses the Telegram message (used on the detection-pass solve).
    """
    if not result_fit.exists():
        logger.warning(f"[stack] {target_name}: plate-solve skipped — result.fit missing")
        return {"ok": False, "error": "result.fit not found"}

    ra_hint, dec_hint = _get_target_radec(target_name)
    if ra_hint is not None:
        logger.info(f"[stack] {target_name}: plate-solve hint RA={ra_hint:.4f} Dec={dec_hint:.4f}")
    else:
        logger.info(f"[stack] {target_name}: plate-solve with no RA/Dec hint (blind solve)")

    try:
        from nas_server.pixinsight import run_solve
        result = run_solve(str(result_fit), ra_hint=ra_hint, dec_hint=dec_hint)
        logger.info(f"[stack] {target_name}: solve={'OK' if result['ok'] else 'FAILED'}")
        if result["ok"] and notify:
            telegram.send(
                f"🔭 <b>Plate solved</b>: <code>{target_name}</code>\n"
                f"RA={result.get('ra_solved', 0):.4f}° "
                f"Dec={result.get('dec_solved', 0):.4f}° "
                f"res={result.get('resolution_arcsec', 0):.2f}\"/px"
            )
        elif not result["ok"]:
            logger.warning(f"[stack] {target_name}: plate solve failed (non-fatal): "
                           f"{result.get('error', 'unknown')}")
        return result
    except Exception as e:
        logger.warning(f"[stack] {target_name}: plate solve exception (non-fatal): {e}")
        return {"ok": False, "error": str(e)}


def _generate_preview_jpg(fits_path: Path, jpg_path: Path) -> None:
    """STF unlinked preview of a FITS stack."""
    try:
        from nas_server.seti_astro import generate_preview_stf
        generate_preview_stf(fits_path, jpg_path)
        logger.info(f"[stack] Preview saved: {jpg_path}")
    except Exception as e:
        logger.warning(f"[stack] Preview generation failed (non-fatal): {e}")



def _get_processed_id(file_path: str) -> int | None:
    """Fetch the processed_files.id for a given file_path."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM processed_files WHERE file_path=?", (file_path,)
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _run_claude_assessment(target_name: str, out_jpg, processed_id,
                            frame_count: int, total_integration: float,
                            obs_date: str, physics: dict | None = None) -> None:
    """Run Claude post-stack assessment. Non-fatal — logs and returns on any error."""
    try:
        from nas_server.config import settings
        if not settings.get("auto_assess", True) or not settings.get("anthropic_api_key"):
            return
        if not out_jpg.exists():
            logger.warning(f"[claude] No preview JPEG for {target_name}, skipping assessment")
            return
        from nas_server import claude_client
        from nas_server.database import save_claude_assessment, get_conn
        from nas_server.folio_generator import load_folio
        import json as _json

        _folio = load_folio(target_name)
        if _folio is None:
            try:
                from nas_server.database import add_agent_suggestion
                _fname = target_name.replace(" ", "_").replace("/", "_") + ".json"
                add_agent_suggestion(
                    description=f"Create folio for {target_name} — stack completed but no folio exists",
                    file_hint=f"nas_server/target_folios/{_fname}",
                    source="planner",
                    dedup_key=f"folio:{target_name}",
                )
            except Exception:
                pass

        meta = {
            "stackcnt": frame_count,
            "total_hours": round(total_integration / 3600, 2),
            "obs_date": obs_date,
            "filter": "IRCUT",
            "object_type": _get_object_type(target_name),
        }
        scores = claude_client.assess_stacked_image(target_name, str(out_jpg), meta,
                                                      physics=physics,
                                                      reference_folio=_folio)
        if not scores:
            return

        # Store assessment row
        save_claude_assessment(
            target=target_name,
            processed_id=processed_id,
            phase="post_stack",
            scores={k: v for k, v in scores.items()
                    if k not in ("raw_response", "input_tokens", "output_tokens")},
            raw=scores.get("raw_response"),
            input_tokens=scores.get("input_tokens"),
            output_tokens=scores.get("output_tokens"),
        )

        # Merge Claude scores into processed_files.flags and notes
        if processed_id:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT flags FROM processed_files WHERE id=?", (processed_id,)
                ).fetchone()
                flags = _json.loads(row[0]) if row else {}
                flags.update({
                    "claude_overall": scores.get("overall"),
                    "claude_noise": scores.get("noise"),
                    "claude_gradient": scores.get("gradient"),
                    "claude_stars": scores.get("star_roundness"),
                    "claude_stretch": scores.get("stretch_quality"),
                    "claude_color": scores.get("color_balance"),
                })
                issues = scores.get("issues") or []
                suggestions = scores.get("suggestions") or []
                issue_str = "; ".join(issues[:3]) if issues else "none"
                suggest_str = "; ".join(suggestions[:3]) if suggestions else ""
                notes = f"Claude {scores.get('overall')}/10 · issues: {issue_str}"
                if suggest_str:
                    notes += f" · suggestions: {suggest_str}"
                conn.execute(
                    "UPDATE processed_files SET flags=?, notes=?, updated_at=datetime('now') WHERE id=?",
                    (_json.dumps(flags), notes, processed_id)
                )
        logger.info(f"[claude] {target_name}: overall={scores.get('overall')}/10")

        # Auto-trigger stretch optimizer if stretch quality is low
        if settings.get("stretch_auto_optimize", True) and scores.get("stretch_quality", 10) < 6:
            logger.info(f"[claude] {target_name}: stretch_quality={scores.get('stretch_quality')}, "
                        f"stretch optimization queued (implement Phase 2)")

        return scores

    except Exception as e:
        logger.warning(f"[claude] Assessment failed for {target_name} (non-fatal): {e}")
    return None


def _get_object_type(target_name: str) -> str | None:
    """Fetch object type from targets table."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT type FROM targets WHERE target=?", (target_name,)
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


# --- Stack process registry (for status/kill endpoints) ---
import threading as _threading
_stack_lock = _threading.Lock()
_active_stacks: dict = {}  # target -> {proc, started_at, frames, pid, phase}


def _estimate_stack_minutes(target: str, frame_count: int, engine: str) -> int:
    """Estimate stack duration in minutes. Queries processing_runs history, falls back to defaults."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT AVG(elapsed_s) FROM processing_runs WHERE target=? AND workflow LIKE ? LIMIT 5",
                (target, f"%{engine}%")
            ).fetchone()
            if row and row[0]:
                return max(1, int(row[0] / 60))
    except Exception:
        pass
    # Default estimates by engine
    if engine == "imagemm":
        return max(5, frame_count // 60)
    if engine == "pixinsight_wbpp":
        return max(3, 2 + frame_count // 200)
    if engine == "pixinsight_register":
        return max(10, frame_count // 20)  # ~8s/frame SA + ~12s/frame DI
    return max(3, frame_count // 120)  # siril: ~0.5s/frame


def _register_stack(target: str, proc, frames: int, started_at, work_dir=None) -> None:
    with _stack_lock:
        _active_stacks[target] = {
            "proc": proc,
            "work_dir": str(work_dir) if work_dir else None,
            "started_at": started_at,
            "frames": frames,
            "pid": proc.pid,
            "phase": "stacking",
        }


def _set_stack_phase(target: str, phase: str) -> None:
    with _stack_lock:
        if target in _active_stacks:
            _active_stacks[target]["phase"] = phase
        else:
            _active_stacks[target] = {"phase": phase, "started_at": datetime.now(),
                                       "frames": 0, "pid": None, "proc": None}


def _unregister_stack(target: str) -> None:
    with _stack_lock:
        _active_stacks.pop(target, None)


def _read_seq_frame_count(work_dir: str | None, seq_name: str = "r_light_") -> int | None:
    """Read nb_selected from a Siril .seq file in process/. Returns None if not found."""
    if not work_dir:
        return None
    seq_path = Path(work_dir) / "process" / f"{seq_name}.seq"
    if not seq_path.exists():
        return None
    try:
        for line in seq_path.read_text().splitlines():
            if line.startswith("S "):
                # S 'name' start nb_images nb_selected ...
                parts = line.split()
                if len(parts) >= 5:
                    return int(parts[4])
    except Exception:
        pass
    return None


def get_stack_status(target: str) -> dict:
    """Return status dict for a running stack, or None if not running."""
    with _stack_lock:
        entry = _active_stacks.get(target)
    if not entry:
        return None
    from datetime import datetime
    elapsed = int((datetime.now() - entry["started_at"]).total_seconds())
    mins, secs = divmod(elapsed, 60)
    work_dir = entry.get("work_dir")
    registered = _read_seq_frame_count(work_dir, "r_light_")
    total = entry.get("frames", 0)
    return {
        "target": target,
        "running": entry["proc"].poll() is None if entry.get("proc") else True,
        "pid": entry.get("pid"),
        "frames": total,
        "frames_registered": registered,
        "frames_registered_pct": round(100 * registered / total, 1) if registered and total else None,
        "phase": entry.get("phase", "running"),
        "elapsed_s": elapsed,
        "elapsed_human": f"{mins}m {secs}s",
        "work_dir": work_dir,
    }


def get_all_stack_statuses() -> list:
    with _stack_lock:
        targets = list(_active_stacks.keys())
    return [s for t in targets if (s := get_stack_status(t))]


def kill_stack(target: str) -> bool:
    """Kill the running Siril process for a target. Returns True if killed."""
    with _stack_lock:
        entry = _active_stacks.get(target)
    if not entry:
        return False
    try:
        entry["proc"].terminate()
        logger.warning(f"[stack] {target}: killed by user request (pid {entry['pid']})")
        return True
    except Exception as e:
        logger.error(f"[stack] kill failed for {target}: {e}")
        return False


def _run_cosmic_clarity(target_name: str, fits_path, processed_id) -> None:
    """Run Cosmic Clarity denoise on stacked FITS if enabled in config (non-fatal)."""
    from nas_server.config import settings
    if not settings.get("cosmic_clarity_enabled", False):
        return
    try:
        from nas_server.seti_astro import denoise
        from nas_server.database import get_conn
        gpu = settings.get("cosmic_clarity_gpu", True)
        out_path = fits_path.parent / (fits_path.stem + "_cc_denoised" + fits_path.suffix)
        result = denoise(str(fits_path), str(out_path), gpu=gpu)
        if result["ok"]:
            logger.info(f"[seti_astro] {target_name}: CC denoise done in {result['elapsed_s']}s")
            if processed_id:
                with get_conn() as conn:
                    import json as _json
                    row = conn.execute(
                        "SELECT flags FROM processed_files WHERE id=?", (processed_id,)
                    ).fetchone()
                    flags = _json.loads(row[0] or "{}") if row else {}
                    flags["cc_denoised"] = True
                    flags["cc_output"] = str(out_path)
                    flags["cc_elapsed_s"] = result["elapsed_s"]
                    conn.execute(
                        "UPDATE processed_files SET flags=? WHERE id=?",
                        (_json.dumps(flags), processed_id)
                    )
        else:
            logger.warning(f"[seti_astro] {target_name}: CC denoise failed: {result.get('error','')}")
    except Exception as e:
        logger.warning(f"[seti_astro] {target_name}: exception (non-fatal): {e}")

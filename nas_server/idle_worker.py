"""
Idle background enrichment worker.

Runs as a low-priority daemon thread. When no stacking or processing jobs are
active, it works through enrichment tasks in priority order:

  1. Score unscored light frames (FWHM, eccentricity, SNR via image_analyzer)
  2. Generate missing JPEG previews for processed FITS files

Checks _any_active() before each work unit — yields immediately if a real job
starts. Throttled to one frame per 30s to stay well under 10% CPU.
"""
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_stop_event: threading.Event | None = None


def _any_active() -> bool:
    try:
        from nas_server.queue_manager import _any_active as _qa
        return _qa()
    except Exception:
        return False


def _score_one_frame() -> bool:
    """Score one light frame. Returns True if work was done.

    Computes the FULL measurement set (FWHM, eccentricity, SNR, star_count, sky_level,
    gradient_severity) — analyze() returns all of them in one pass, so storing sky_level
    + gradient_severity costs nothing extra and means the stack cull step treats the frame
    as fully measured (no re-measure at stack time). Prefers never-scored frames, then
    backfills frames scored before sky_level/gradient were captured.
    """
    try:
        from nas_server.database import (get_unscored_light_frames,
                                         get_unmeasured_light_frames,
                                         update_light_frame_scores)
        from nas_server.image_analyzer import analyze
        frames = get_unscored_light_frames(limit=1) or get_unmeasured_light_frames(limit=1)
        if not frames:
            return False
        row = frames[0]
        fpath = row["file_path"]
        if not Path(fpath).exists():
            # Mark as scored anyway so we don't keep retrying missing files
            update_light_frame_scores(fpath, fwhm=None, eccentricity=None, snr=None)
            return True
        stats = analyze(fpath)
        fwhm = stats.get("psf", {}).get("fwhm_median")
        ecc = stats.get("psf", {}).get("eccentricity")
        snr = stats.get("noise", {}).get("snr")
        stars = stats.get("psf", {}).get("star_count")
        sky = stats.get("background", {}).get("sky_mean")
        grad = stats.get("background", {}).get("gradient_severity")
        update_light_frame_scores(fpath, fwhm=fwhm, eccentricity=ecc, snr=snr,
                                  star_count=stars, sky_level=sky, gradient_severity=grad)
        log.debug(f"[idle] scored {Path(fpath).name}: fwhm={fwhm:.2f} ecc={ecc:.2f} "
                  f"snr={snr:.1f} stars={stars} sky={sky} grad={grad}")
        return True
    except Exception as e:
        log.debug(f"[idle] score_one_frame failed: {e}")
        return False


def _solve_one_batch() -> bool:
    """Plate-solve a batch of unsolved subs for one target. Returns True if work done.

    Solving runs Siril (~tens of seconds per batch) so it is capped and batched per
    target — solving a coherent group lets flag_alignment_outliers judge each panel
    against its own siblings. Lower priority than per-frame scoring.

    Uses claim_solve_batch (a 'solving' lease) so the VM and the laptop worker never
    grab the same frames; on any failure the lease is released so the frames return to
    the unsolved pool instead of being stuck. solve_subs writes its own final
    ok/failed/outlier status, superseding the lease.
    """
    try:
        from nas_server.database import claim_solve_batch, release_solve_claims
        from nas_server.sub_solver import solve_subs, solve_subs_astap
        target, paths = claim_solve_batch(limit=40)
        if not target or not paths:
            return False
        existing = [p for p in paths if Path(p).exists()]
        # Mark missing files failed so we don't reselect them forever.
        missing = [p for p in paths if not Path(p).exists()]
        if missing:
            from nas_server.database import update_light_frame_solve
            for p in missing:
                update_light_frame_solve(p, None, None, solve_status="failed")
        if not existing:
            return True
        try:
            # ASTAP first (fast, VM-side, DB-only writes); Siril batch as fallback
            # if ASTAP produced nothing (e.g. binary/DB missing).
            res = solve_subs_astap(existing)
            if not any(v == "ok" for v in res.values()):
                log.info("[idle] astap batch yielded 0 — falling back to siril solve")
                res = solve_subs(existing)
            ok = sum(1 for v in res.values() if v == "ok")
            log.info(f"[idle] solved {ok}/{len(existing)} subs for {target}")
        except Exception:
            release_solve_claims(existing)
            raise
        return True
    except Exception as e:
        log.debug(f"[idle] solve_one_batch failed: {e}")
        return False


def _generate_preview(fits_path: Path, out_path: Path) -> bool:
    """Generate arcsinh-stretch JPEG preview for a FITS file."""
    try:
        import numpy as np
        from astropy.io import fits as afits
        from PIL import Image as _Image
        with afits.open(str(fits_path)) as hdul:
            data = hdul[0].data.astype(np.float32)
        if data.ndim == 3:
            data = np.transpose(data, (1, 2, 0))
        p40 = np.percentile(data, 40)
        p999 = np.percentile(data, 99.9)
        if p999 > p40:
            data = np.arcsinh((data - p40) / (p999 - p40) * 3) / np.arcsinh(3)
        data = np.clip(data, 0, 1)
        _Image.fromarray((data * 255).astype(np.uint8)).save(str(out_path), quality=90)
        return True
    except Exception as e:
        log.debug(f"[idle] preview generation failed for {fits_path.name}: {e}")
        return False


def _generate_one_preview() -> bool:
    """Generate one missing JPEG preview for a processed FITS. Returns True if work was done."""
    try:
        from nas_server.config import settings
        lib = Path(settings.get("seestar_library_path", ""))
        if not lib.is_dir():
            return False
        for proc_dir in sorted(lib.glob("*/_processed")):
            existing_jpgs = {p.stem for p in proc_dir.glob("*.jpg")}
            for fits_path in sorted(proc_dir.glob("*.fit")) + sorted(proc_dir.glob("*.fits")):
                if fits_path.stem + "_preview" not in existing_jpgs:
                    out = proc_dir / (fits_path.stem + "_preview.jpg")
                    ok = _generate_preview(fits_path, out)
                    if ok:
                        log.info(f"[idle] generated preview: {out.name}")
                    return True  # one unit of work per call
        return False
    except Exception as e:
        log.debug(f"[idle] generate_one_preview failed: {e}")
        return False


def _idle_loop(stop_event: threading.Event):
    log.info("[idle] worker started")
    while not stop_event.is_set():
        if _any_active():
            stop_event.wait(30)
            continue
        try:
            did_work = _score_one_frame()
            if not did_work:
                did_work = _solve_one_batch()
            if not did_work:
                did_work = _generate_one_preview()
        except Exception as e:
            log.debug(f"[idle] loop error: {e}")
            did_work = False
        # Stay responsive to active jobs: short sleep while working, longer when idle
        stop_event.wait(5 if did_work else 30)
    log.info("[idle] worker stopped")


def start_idle_worker() -> threading.Event:
    """Start the idle enrichment worker daemon thread. Returns the stop event."""
    global _stop_event
    _stop_event = threading.Event()
    t = threading.Thread(target=_idle_loop, args=(_stop_event,),
                         daemon=True, name="idle-worker")
    t.start()
    return _stop_event

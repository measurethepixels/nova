"""
SeeStar NAS Server
------------------
Run on the Ubuntu NAS VM:
    uvicorn nas_server.main:app --host 0.0.0.0 --port 8000

Or use the systemd service file in nas_server/deploy/.
"""

import collections
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from nas_server import database, scheduler as sched, watcher
from nas_server.config import settings
from nas_server.organizer import organize_session
from nas_server import telegram

# ---------------------------------------------------------------------------
# Global serialization lock for PI-based processing pipelines.
# PixInsight headless cannot run concurrent instances — two simultaneous
# autoprocess calls will collide and corrupt each other's SPCC/BXT results.
# PIPELINE_LOCK lives in auto_process.py so queue_manager can also use it.
# ---------------------------------------------------------------------------
from nas_server.auto_process import PIPELINE_LOCK as _PIPELINE_LOCK  # noqa: E402

# ---------------------------------------------------------------------------
# In-memory log buffer — stores last 200 entries for GET /logs
# ---------------------------------------------------------------------------

class _LogBuffer(logging.Handler):
    def __init__(self, maxlen=200):
        super().__init__()
        self._records = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record):
        with self._lock:
            self._records.append({
                "time": self.formatter.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            })

    def entries(self, n=50):
        with self._lock:
            items = list(self._records)
        return items[-n:]


_log_buffer = _LogBuffer()
_log_buffer.setFormatter(logging.Formatter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger().addHandler(_log_buffer)

log = logging.getLogger(__name__)

_observer = None
_stop_event = None
_scheduler = None
_pending_sessions = None  # shared set from watcher for forced checks
_idle_stop = None
_nina_cap_stop = None
_nina_cal_stop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _observer, _stop_event, _scheduler, _pending_sessions, _idle_stop, _nina_cap_stop, _nina_cal_stop

    log.info("Starting SeeStar NAS Server")
    database.init_database()

    # Delete any orphaned stack work dirs left by previous crashes/restarts.
    # The finally-block cleanup in stacker.py doesn't run when systemd kills uvicorn.
    import shutil as _shutil
    for _d in Path("/tmp").glob("seestar_stack_*"):
        if _d.is_dir():
            _shutil.rmtree(_d, ignore_errors=True)
            log.info(f"[startup] removed orphaned work dir: {_d.name}")
    from nas_server.config import settings as _settings
    _nas_work = Path(_settings.get("nas_work_path", "/mnt/nas_data/_stack_work"))
    if _nas_work.exists():
        for _d in _nas_work.glob("seestar_stack_*"):
            if _d.is_dir():
                _shutil.rmtree(_d, ignore_errors=True)
                log.info(f"[startup] removed orphaned NAS work dir: {_d.name}")

    # settings.json uses "telegram_token" (not "telegram_bot_token")
    telegram.configure(
        settings.get("telegram_token", ""),
        settings.get("telegram_chat_id", ""),
    )
    from nas_server.agent import run_agent as _agent_fn
    telegram.start_polling(_agent_fn)

    # Build RAG embedding index in background — skips already-indexed records
    import threading as _threading
    from nas_server import rag as _rag
    _threading.Thread(target=_rag.build_index, daemon=True, name="rag-index").start()

    _observer, _stop_event, _pending_sessions = watcher.start_watcher()
    _scheduler = sched.start_scheduler()

    from nas_server.queue_manager import start_worker
    start_worker()

    from nas_server.idle_worker import start_idle_worker
    _idle_stop = start_idle_worker()

    # NINA file watchers (capture + calibration)
    from nas_server import nina_watcher
    _nina_cap_stop, _ = nina_watcher.start_capture_watcher()
    _nina_cal_stop, _ = nina_watcher.start_calibration_watcher()

    # NINA WebSocket event listener (fail-safe if VM is offline)
    try:
        from nas_server import nina_client

        def _on_image_ready(target: str, file_path: str):
            log.info(f"[nina_ws] ImageSaved: {target} → {file_path}")

        def _on_sequence_done(target: str):
            log.info(f"[nina_ws] SequenceFinished: {target}")
            telegram.send(f"🎯 <b>NINA sequence complete: {target}</b>")

        nina_client.start_event_listener(
            on_image_ready=_on_image_ready,
            on_sequence_done=_on_sequence_done,
        )
    except Exception as e:
        log.warning(f"[startup] NINA WebSocket listener failed to start: {e}")

    yield

    log.info("Shutting down")
    telegram.stop_polling()
    if _idle_stop:
        _idle_stop.set()
    if _nina_cap_stop:
        _nina_cap_stop.set()
    if _nina_cal_stop:
        _nina_cal_stop.set()
    try:
        from nas_server import nina_client
        nina_client.stop_event_listener()
    except Exception:
        pass
    _stop_event.set()
    if _observer is not None and _observer.is_alive():
        _observer.stop()
        _observer.join()
    _scheduler.shutdown(wait=False)


app = FastAPI(title="SeeStar NAS Server", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health & status
# ---------------------------------------------------------------------------

@app.get("/status")
def get_status():
    return {
        "status": "online",
        "incoming": settings["seestar_incoming_path"],
        "library": settings["seestar_library_path"],
        "db": settings["db_path"],
    }


@app.get("/mounts")
def get_mounts():
    """Check whether the SMB mount points are accessible."""
    paths = {
        "incoming": settings["seestar_incoming_path"],
        "library": settings["seestar_library_path"],
    }
    result = {}
    for name, path in paths.items():
        try:
            accessible = os.path.isdir(path)
            contents = len(os.listdir(path)) if accessible else None
            result[name] = {"path": path, "accessible": accessible, "items": contents}
        except Exception as e:
            result[name] = {"path": path, "accessible": False, "error": str(e)}
    return result


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@app.get("/logs")
def get_logs(n: int = 50):
    """Return the last n log entries (max 200)."""
    return _log_buffer.entries(min(n, 200))


# ---------------------------------------------------------------------------
# Targets & pipeline
# ---------------------------------------------------------------------------

@app.get("/targets")
def get_targets():
    return database.get_targets()


@app.get("/pipeline")
def get_pipeline():
    return database.get_pipeline()


@app.get("/target/{target}/canonical")
def get_target_canonical(target: str):
    """Report whether a synthetic canonical reference frame exists for this target.

    The Add Job form uses this to default folios-with-a-reference to the PI register
    engine (canonical framing) while still allowing other engines to be chosen.
    """
    import re as _re
    from nas_server.target_references import REF_DIR
    sanitized = _re.sub(r"[^A-Za-z0-9_.-]+", "_", target.strip()) or "target"
    ref_path = REF_DIR / f"{sanitized}.fits"
    return {"target": target, "has_reference": ref_path.exists()}


@app.get("/pipeline/{target}")
def get_pipeline_target(target: str):
    rows = database.get_pipeline()
    for row in rows:
        if row["target"] == target:
            return row
    raise HTTPException(status_code=404, detail=f"Target '{target}' not found")


class PipelineUpdate(BaseModel):
    stage: str
    notes: str = None


@app.post("/pipeline/{target}")
def update_pipeline(target: str, body: PipelineUpdate):
    try:
        database.set_pipeline_stage(target, body.stage, body.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"target": target, "stage": body.stage}


# ---------------------------------------------------------------------------
# Manual triggers
# ---------------------------------------------------------------------------

@app.post("/sync")
def trigger_sync():
    """Trigger a full library re-scan (same as the hourly job)."""
    from nas_server.scheduler import hourly_scan
    from nas_server.processed_scanner import scan_processed_folders
    from nas_server.config import settings as cfg
    threading.Thread(target=hourly_scan, daemon=True).start()
    added = scan_processed_folders(cfg["seestar_library_path"], cfg["db_path"])
    return {"message": "Library scan started", "new_processed": added}


@app.get("/watcher/status")
def watcher_status():
    """Return current watcher state: pending sessions, staleness, stability threshold."""
    incoming = settings["seestar_incoming_path"]
    stability_wait = settings.get("stability_wait_seconds", 600)
    mounted = os.path.isdir(incoming)
    sessions = []
    if mounted and _pending_sessions is not None:
        for target in sorted(_pending_sessions):
            target_dir = os.path.join(incoming, target)
            subs_dir   = os.path.join(incoming, f"{target}_sub")
            target_exists = os.path.isdir(target_dir)
            subs_exists   = os.path.isdir(subs_dir)
            entry = {"target": target, "target_exists": target_exists,
                     "subs_exists": subs_exists, "age_s": None,
                     "stable": False, "capturing": False}
            # Track age from whichever folder(s) exist
            check_dirs = [d for d in [target_dir, subs_dir]
                          if os.path.isdir(d)]
            if check_dirs:
                mtime = max(watcher._latest_mtime(d) for d in check_dirs)
                age = round(time.time() - mtime, 1)
                entry["age_s"] = age
                entry["stable"] = age >= stability_wait and (target_exists or subs_exists)
                entry["remaining_s"] = max(0, round(stability_wait - age, 1))
                entry["capturing"] = age < 120  # file written in last 2 min
            sessions.append(entry)
    return {
        "mounted": mounted,
        "incoming": incoming,
        "stability_wait_s": stability_wait,
        "pending": len(sessions),
        "sessions": sessions,
    }


@app.post("/check")
def trigger_check():
    """Force the watcher to immediately evaluate all pending sessions."""
    if _pending_sessions is None:
        raise HTTPException(status_code=503, detail="Watcher not running")
    # Seed pending with anything currently in incoming
    incoming = settings["seestar_incoming_path"]
    if os.path.isdir(incoming):
        for name in os.listdir(incoming):
            if os.path.isdir(os.path.join(incoming, name)):
                base = name[:-4] if name.endswith("_sub") else name
                _pending_sessions.add(base)
    count = len(_pending_sessions)
    log.info(f"Forced watcher check — {count} session(s) pending")
    return {"message": f"Watcher check triggered", "pending": count}


class OrganizeRequest(BaseModel):
    target: str


@app.post("/organize")
def trigger_organize(req: OrganizeRequest):
    """Manually organize a specific session from incoming → library."""
    success = organize_session(
        req.target,
        settings["seestar_incoming_path"],
        settings["seestar_library_path"],
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"Session '{req.target}' not found in incoming")
    return {"message": f"Organized: {req.target}"}


# ---------------------------------------------------------------------------
# Downloaded to sort
# ---------------------------------------------------------------------------

def _sort_path() -> str:
    return os.path.join(settings["seestar_library_path"], "_Downloaded to sort")


def _list_sort_sessions() -> list[str]:
    """Return base target names found in _Downloaded to sort (excluding _sub folders)."""
    sort_dir = _sort_path()
    if not os.path.isdir(sort_dir):
        return []
    names = []
    for name in sorted(os.listdir(sort_dir)):
        if name.startswith("_") or name.startswith("."):
            continue
        if name.endswith("_sub"):
            continue
        if os.path.isdir(os.path.join(sort_dir, name)):
            names.append(name)
    return names


@app.get("/sort")
def list_sort():
    """List sessions available in _Downloaded to sort."""
    sessions = _list_sort_sessions()
    return {"path": _sort_path(), "sessions": sessions, "count": len(sessions)}


class SortRequest(BaseModel):
    target: str = None  # None means process all


@app.post("/sort")
def run_sort(req: SortRequest = None):
    """Organize sessions from _Downloaded to sort into the library."""
    sort_dir = _sort_path()
    library = settings["seestar_library_path"]

    targets = [req.target] if (req and req.target) else _list_sort_sessions()
    if not targets:
        return {"message": "Nothing to organize", "processed": [], "failed": []}

    processed, failed = [], []

    def _do_sort():
        for target in targets:
            log.info(f"Organizing from sort: {target}")
            try:
                ok = organize_session(target, sort_dir, library)
                if ok:
                    processed.append(target)
                else:
                    failed.append(target)
            except Exception as e:
                log.error(f"Sort failed for {target}: {e}")
                failed.append(target)
        summary = 'Sort complete: ' + str(len(processed)) + ' organized'
        if failed:
            summary += ', ' + str(len(failed)) + ' failed: ' + ', '.join(failed)
        telegram.send('📦 <b>' + summary + '</b>')

    threading.Thread(target=_do_sort, daemon=True).start()
    return {
        "message": f"Organizing {len(targets)} session(s) from Downloaded to sort",
        "targets": targets,
    }


class StackRequest(BaseModel):
    notes: str = None


@app.post("/stack/{target}")
def stack_target_endpoint(target: str, engine: str = "siril",
                          cull: bool = True, bottom_pct: float = 0.10,
                          min_stars: int = 20, fast: bool = False, framing: str = "min",
                          hero: bool = False, drizzle: bool = False,
                          exptime: int = None, eq_only: bool = True,
                          ecc_threshold: float = 0.66,
                          sky_level_factor: float = 3.0, gradient_threshold: float = 0.5,
                          post_autoprocess_workflow: str = None,
                          post_autoprocess_experiment: bool = False):
    """Queue a stacking job. Serialized with processing jobs to prevent resource contention.
    engine: siril | imagemm | pixinsight_wbpp | pixinsight_register
    cull: score frames and exclude outliers before stacking (default True)
    framing: min (default, intersection) | max (union, for mosaics/multi-session)
    hero: maximise quality — Lanczos4 interpolation (Siril) + IKSS weight scale (PI)
    drizzle: 2x upsampling via Siril drizzle registration (scale=2, pixfrac=0.5/0.4 hero).
             Recommended 100+ frames. Incompatible with fast mode.
    exptime: if set, only stack frames whose exposure_time rounds to this value (seconds).
    eq_only: if True, exclude frames captured in alt-az mode (EQMODE!=1 in FITS header).
             Prevents diagonal banding artifacts when mixing old alt-az and new EQ sessions.
    ecc_threshold: eccentricity gate (default 0.6). Raise to 0.8 to recover EQ tracking
                   frames with moderate polar-alignment drift.
    sky_level_factor: reject frames where sky > median_sky × factor (default 3.0). 0 disables.
    gradient_threshold: reject frames with gradient_severity > threshold (default 0.5). 0 disables.
    post_autoprocess_workflow: if set, auto-queues an autoprocess job after stack completes
    """
    from nas_server.queue_manager import add_stack_job
    item = add_stack_job(target, engine=engine, cull=cull,
                         bottom_pct=bottom_pct, min_stars=min_stars, fast=fast, framing=framing,
                         hero=hero, drizzle=drizzle, exptime=exptime, eq_only=eq_only,
                         ecc_threshold=ecc_threshold,
                         sky_level_factor=sky_level_factor, gradient_threshold=gradient_threshold,
                         post_autoprocess_workflow=post_autoprocess_workflow,
                         post_autoprocess_experiment=post_autoprocess_experiment)
    return {"message": f"Stacking '{target}' queued at position {item['position']}", **item}


@app.post("/solve/{target}")
def solve_target_endpoint(target: str, background: bool = True):
    """Plate-solve a target's unsolved subs (and its mosaic panels / associations)
    to capture each sub's TRUE sky position for alignment QA and association.

    background=True (default) runs the solve in a daemon thread and returns at once;
    poll GET /solve/{target} for the alignment summary. background=False solves inline
    and returns the summary (only for small targets — Siril takes ~tens of seconds
    per 40-sub batch).
    """
    from nas_server.database import get_unsolved_light_frames
    from nas_server.sub_solver import _expand_targets, solve_target, alignment_summary
    names = _expand_targets(target)
    pending = 0
    for n in names:
        pending += len(get_unsolved_light_frames(target=n, limit=100000))
    if pending == 0:
        return {"message": f"No unsolved subs for '{target}'", "pending": 0,
                "summary": alignment_summary(target)}
    if background:
        import threading
        threading.Thread(target=solve_target, args=(target,),
                         daemon=True, name=f"solve-{target}").start()
        return {"message": f"Solving {pending} subs for '{target}' in background "
                           f"(poll GET /solve/{target})", "pending": pending}
    return {"message": f"Solved '{target}'", **solve_target(target)}


@app.get("/solve/{target}")
def solve_status_endpoint(target: str):
    """Alignment QA report from stored solves: per-panel cluster center, solved/
    failed counts, and off-pointing outlier subs (the pre-EQ spoof / mis-slew case)."""
    from nas_server.database import get_unsolved_light_frames
    from nas_server.sub_solver import _expand_targets, alignment_summary
    names = _expand_targets(target)
    pending = sum(len(get_unsolved_light_frames(target=n, limit=100000)) for n in names)
    return {"pending": pending, **alignment_summary(target)}


@app.get("/stack")
def all_stack_statuses():
    """List all currently running stacks."""
    from nas_server.stacker import get_all_stack_statuses
    return {"running": get_all_stack_statuses()}


@app.get("/stack/{target}")
def stack_status(target: str):
    """Check running status and whether processed results exist for a target."""
    from nas_server.stacker import get_stack_status
    from nas_server.database import get_processed_files
    status = get_stack_status(target)
    processed = get_processed_files(target)
    return {
        "target": target,
        "active": status,
        "processed_files": len(processed),
        "latest": processed[0]["filename"] if processed else None,
    }


@app.delete("/stack/{target}")
def kill_stack_endpoint(target: str):
    """Kill the running Siril process for a target."""
    from nas_server.stacker import kill_stack as _kill
    killed = _kill(target)
    if killed:
        return {"message": f"Stack process for {target} terminated"}
    return {"message": f"No active stack found for {target}"}


class ProcessedUpdate(BaseModel):
    step: str = None
    flags: str = None
    notes: str = None


@app.get("/processed/{target}")
def get_processed(target: str, include_stack_params: bool = False):
    """List all processed files for a target."""
    from nas_server.database import get_processed_files
    return {"target": target, "files": get_processed_files(target, include_stack_params=include_stack_params)}


@app.patch("/processed/{file_id}")
def update_processed(file_id: int, req: ProcessedUpdate):
    """Update step, flags, or notes for a processed file."""
    from nas_server.database import update_processed_file
    update_processed_file(file_id, step=req.step, flags=req.flags, notes=req.notes)
    return {"updated": file_id}


# --- Claude assessment endpoints ---

@app.post("/assess/{target}")
def trigger_assess(target: str):
    """Re-run Phase 1 Claude quality assessment on the latest processed stack."""
    from nas_server.database import get_processed_files
    from nas_server.stacker import _get_processed_id
    from pathlib import Path
    files = get_processed_files(target)
    if not files:
        return {"error": f"No processed files found for {target}"}
    latest = files[0]
    jpg = latest.get("filename", "").replace(".fit", "_preview.jpg").replace(".fits", "_preview.jpg")
    jpg_path = Path("/mnt/nas_data") / target / "_processed" / Path(jpg).name
    # Fall back to any .jpg in the _processed dir
    if not jpg_path.exists():
        proc_dir = Path("/mnt/nas_data") / target / "_processed"
        jpgs = sorted(proc_dir.glob("*.jpg")) if proc_dir.exists() else []
        jpg_path = jpgs[-1] if jpgs else jpg_path
    try:
        from nas_server.claude_client import assess_stacked_image
        from nas_server.stacker import _merge_claude_scores
        scores = assess_stacked_image(
            target, str(jpg_path),
            {"stackcnt": latest.get("frame_count"), "total_hours": None,
             "obs_date": None, "filter": "IRCUT", "object_type": None}
        )
        if scores is None:
            return {"message": "No API key configured — assessment skipped"}
        _merge_claude_scores(target, latest["id"], scores)
        return {"target": target, "scores": scores}
    except Exception as e:
        return {"error": str(e)}


@app.get("/assess/{target}")
def get_assessments(target: str, limit: int = 10):
    """Return stored Claude assessments for a target."""
    from nas_server.database import get_claude_history
    return {"target": target, "assessments": get_claude_history(target, limit=limit)}


# --- Cosmic Clarity on-demand endpoints ---

@app.post("/cc/{target}")
def run_cosmic_clarity(target: str, mode: str = "denoise"):
    """Run Cosmic Clarity on latest stacked FITS. mode: denoise | sharpen | both | satellite | darkstar"""
    from nas_server.config import settings
    from nas_server.database import get_processed_files
    from pathlib import Path
    files = get_processed_files(target)
    if not files:
        return {"error": f"No processed files for {target}"}
    fits_name = files[0].get("filename", "")
    fits_path = Path("/mnt/nas_data") / target / "_processed" / fits_name
    if not fits_path.exists():
        return {"error": f"FITS not found: {fits_path}"}
    out_path = fits_path.parent / (fits_path.stem + f"_cc_{mode}" + fits_path.suffix)
    try:
        from nas_server import seti_astro
        gpu = settings.get("cosmic_clarity_gpu", True)
        fn = {
            "denoise": lambda: seti_astro.denoise(fits_path, out_path, gpu=gpu),
            "sharpen": lambda: seti_astro.sharpen(fits_path, out_path, gpu=gpu),
            "both": lambda: seti_astro.denoise_and_sharpen(fits_path, out_path, gpu=gpu),
            "satellite": lambda: seti_astro.remove_satellites(fits_path, out_path, gpu=gpu),
            "darkstar": lambda: seti_astro.remove_stars(fits_path, out_path, gpu=gpu),
        }.get(mode)
        if fn is None:
            return {"error": f"Unknown mode: {mode}. Use denoise|sharpen|both|satellite|darkstar"}
        result = fn()
        return {"target": target, "mode": mode, **result}
    except Exception as e:
        return {"error": str(e)}


# --- Stretch endpoints ---

@app.post("/stretch/{target}")
def stretch_image(target: str, mode: str = "stat", target_median: float = 0.25,
                  linked: bool = True, alpha: float = 5.0, gamma: float = 3.0):
    """
    Stretch the latest stacked FITS for a target.
    mode: stat (statistical stretch) | ghs (Generalised Hyperbolic Stretch)
    stat params: target_median, linked
    ghs params: target_median (used as pivot), alpha, gamma
    """
    from nas_server.database import get_processed_files
    from pathlib import Path
    files = get_processed_files(target)
    if not files:
        return {"error": f"No processed files for {target}"}
    fits_name = files[0].get("filename", "")
    fits_path = Path("/mnt/nas_data") / target / "_processed" / fits_name
    if not fits_path.exists():
        return {"error": f"FITS not found: {fits_path}"}
    out_path = fits_path.parent / (fits_path.stem + f"_stretched_{mode}" + fits_path.suffix)
    try:
        from nas_server import seti_astro
        if mode == "stat":
            result = seti_astro.stat_stretch(fits_path, out_path,
                                             target_median=target_median, linked=linked)
        elif mode == "ghs":
            result = seti_astro.ghs_stretch(fits_path, out_path,
                                            alpha=alpha, gamma=gamma, pivot=target_median)
        elif mode == "veralux":
            result = seti_astro.veralux_stretch(fits_path, out_path,
                                               log_d=alpha, target_median=target_median)
        else:
            return {"error": f"Unknown mode '{mode}'. Use: stat | ghs | veralux"}
        return {"target": target, "mode": mode, **result}
    except Exception as e:
        return {"error": str(e)}


@app.post("/bgextract/{target}")
def background_extract(target: str, correction: str = "Subtraction",
                       smoothing: float = 0.5, gpu: bool = True):
    """AI background/gradient extraction using GraXpert on the latest stacked FITS."""
    from nas_server.database import get_processed_files
    from pathlib import Path
    files = get_processed_files(target)
    if not files:
        return {"error": f"No processed files for {target}"}
    fits_name = files[0].get("filename", "")
    fits_path = Path("/mnt/nas_data") / target / "_processed" / fits_name
    if not fits_path.exists():
        return {"error": f"FITS not found: {fits_path}"}
    out_path = fits_path.parent / (fits_path.stem + "_bgextracted" + fits_path.suffix)
    try:
        from nas_server.seti_astro import background_extract as _bgextract
        result = _bgextract(fits_path, out_path, correction=correction,
                            smoothing=smoothing, gpu=gpu)
        return {"target": target, **result}
    except Exception as e:
        return {"error": str(e)}


# --- Auto-process (Phase 5 ontology-driven pipeline) ---

@app.post("/autoprocess/{target}")
def autoprocess_target(
    target: str,
    workflow: str = "seestar_broadband",
    dry_run: bool = False,
    experiment_mode: bool = False,
    source_file: str | None = None,
):
    """
    Kick off the automated processing pipeline for a target.
    workflow: seestar_broadband (default) | seestar_fast | linear_only
    dry_run: if true, plan steps without writing any files.
    experiment_mode: try all variants per step; Claude picks best; stores results for learning.
    source_file: specific stack filename to process (default: most recently added).
    """
    from nas_server.auto_process import auto_process
    global _pipeline_active_target

    def _run():
        global _pipeline_active_target
        with _PIPELINE_LOCK:
            _pipeline_active_target = target
            from nas_server import auto_process as _ap_mod
            _ap_mod.mark_pipeline_lock_held()
            try:
                auto_process(target, workflow=workflow, dry_run=dry_run,
                             experiment_mode=experiment_mode, source_file=source_file)
            finally:
                _pipeline_active_target = None
                _ap_mod.clear_pipeline_lock_held()

    threading.Thread(target=_run, daemon=True).start()
    return {"message": f"AutoProcess started for {target}", "workflow": workflow,
            "dry_run": dry_run, "experiment_mode": experiment_mode,
            "source_file": source_file}


@app.get("/autoprocess/{target}")
def autoprocess_status(target: str):
    """Check autoprocess status for a target."""
    from nas_server.auto_process import get_autoprocess_status
    status = get_autoprocess_status(target)
    if not status:
        return {"target": target, "active": False}
    return {"target": target, "active": True, **status}


@app.get("/autoprocess")
def autoprocess_all():
    """List all autoprocess jobs (running or recently completed)."""
    from nas_server.auto_process import get_all_autoprocess_statuses
    return {"jobs": get_all_autoprocess_statuses()}


# --- Processing queue ---

@app.post("/queue")
def queue_add(target: str, workflow: str = "seestar_broadband",
              experiment_mode: bool = False, dry_run: bool = False,
              source_file: str = None, manual_review: bool = False,
              force_nbn: bool = False, force_nb_palette: bool = False,
              force_hoo: bool = False,
              physics_only: bool = False,
              re_crop: bool = False):
    """Add a target to the processing queue.

    force_nbn: force the narrowband_normalization step to run even if Claude
    would normally skip it (Mode 1 — fresh stack with NBN guaranteed).
    force_nb_palette: force the nb_palette bicolor step (needs XP channels;
    eligible now that real Ha/OIII flux ratios are measurable — 1.16.2 WCS fix).
    force_hoo: force the narrowband_hoo step — Ha-red-dominant palette with OIII
    as a bounded teal accent (the natural-HOO alternative to narrowband_norm's
    OIII-equalizing SHO look). Best on OIII-distinct targets (PNe/Veil).
    physics_only: disable all AI calls for this run; grades and step decisions
    come from pixel metrics only (deterministic no-API path).
    re_crop: force a fresh manual crop review even if a saved crop exists for
    this target (otherwise the saved crop is reused without review).
    """
    from nas_server.queue_manager import add_job
    extra_params: dict | None = None
    if force_nbn:
        extra_params = {"force_steps": ["narrowband_norm"]}
    if force_nb_palette:
        extra_params = {"force_steps":
                        (extra_params or {}).get("force_steps", []) + ["nb_palette"]}
    if force_hoo:
        extra_params = {"force_steps":
                        (extra_params or {}).get("force_steps", []) + ["narrowband_hoo"]}
    if physics_only:
        extra_params = {**(extra_params or {}), "physics_only": True}
    if re_crop:
        extra_params = {**(extra_params or {}), "re_crop": True}
    item = add_job(target, workflow=workflow, experiment_mode=experiment_mode,
                   dry_run=dry_run, source_file=source_file,
                   manual_review=manual_review, extra_params=extra_params)
    return {"message": f"Queued '{target}'", **item}


@app.get("/queue")
def queue_list():
    """List all pending queue items, plus pause/restart state."""
    from nas_server.queue_manager import get_queue, is_paused, is_restart_pending
    items = get_queue()
    return {
        "count": len(items),
        "paused": is_paused(),
        "restart_pending": is_restart_pending(),
        "queue": items,
    }


@app.post("/queue/clear-stuck")
def queue_clear_stuck():
    """
    Force-resolve remote jobs that are stuck in inflight tracking because the worker
    moved on without the VM seeing a clean 'done' status.  Safe to call at any time.
    Jobs with a recent NAS output are marked done; jobs with no output are re-queued.
    """
    from nas_server.queue_manager import clear_stuck_inflight
    result = clear_stuck_inflight()
    log.info(f"[queue] clear-stuck: cleared={result['cleared']} requeued={result['requeued']}")
    return result


@app.get("/queue/status")
def queue_status():
    """Return whether any stack or autoprocess job is currently running."""
    from nas_server.queue_manager import is_running
    return {"running": is_running()}


@app.post("/queue/pause")
def queue_pause():
    from nas_server.queue_manager import pause_queue, is_paused
    pause_queue()
    return {"paused": is_paused()}


@app.post("/queue/resume")
def queue_resume():
    from nas_server.queue_manager import resume_queue, is_paused
    resume_queue()
    return {"paused": is_paused()}


@app.post("/admin/restart")
def admin_restart():
    """Graceful restart: pause queue, wait for active job to finish, restart service."""
    from nas_server.queue_manager import request_graceful_restart
    return request_graceful_restart()


@app.delete("/queue")
def queue_clear():
    """Clear all pending queue items."""
    from nas_server.queue_manager import clear_queue
    count = clear_queue()
    return {"message": f"Cleared {count} pending job(s)"}


@app.delete("/queue/{position}")
def queue_remove(position: int):
    """Remove a specific queue item by 1-based position."""
    from nas_server.queue_manager import remove_job
    if remove_job(position):
        return {"message": f"Removed item at position {position}"}
    raise HTTPException(status_code=404, detail=f"No item at position {position}")


# Remote worker completion callback
# POSTed by laptop_worker when a dispatched job finishes.
# The primary notification path is polling (_dispatch_and_monitor every 30s);
# this callback is an optional fast-path so the VM learns instantly.
@app.post("/jobs/{job_id}/complete")
async def job_complete_callback(job_id: str, request: Request):
    """
    Callback POSTed by a remote worker when it finishes a job.
    Logs the result and acknowledges receipt.  The queue monitor thread
    (_dispatch_and_monitor) also polls independently every 30 s, so
    this endpoint is a latency optimisation only — not the only path.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    target   = body.get("target", job_id)
    done     = body.get("done", False)
    err      = body.get("error")
    worker   = body.get("worker_name", "remote")

    if done and not err:
        log.info(f"[callback] job {job_id} ({target}) completed ok on {worker}")
    elif err:
        log.warning(f"[callback] job {job_id} ({target}) failed on {worker}: {err}")
    else:
        log.debug(f"[callback] job {job_id} progress update from {worker}")

    # Authoritatively resolve the dispatch monitor: a worker-confirmed finish
    # (success OR error) must never be re-queued.
    if done or err:
        try:
            from nas_server.queue_manager import mark_remote_done
            mark_remote_done(job_id, target=target, error=err)
        except Exception as _e:
            log.warning(f"[callback] mark_remote_done failed: {_e}")

    # Update worker heartbeat so the DB record stays fresh
    try:
        from nas_server.database import update_worker_heartbeat
        update_worker_heartbeat(worker)
    except Exception:
        pass

    return {"ok": True, "received": job_id}


@app.post("/workers/{name}/toggle")
def toggle_worker(name: str, enabled: bool | None = None):
    """
    Enable/disable dispatch to a remote worker (e.g. the laptop). When disabled,
    the VM stops sending auto_process jobs to it and runs them locally instead.
    Persists in the remote_workers DB table so it survives restarts. Omit
    `enabled` to flip the current state.
    """
    from nas_server.database import get_worker_enabled, set_worker_enabled
    from nas_server.config import settings
    url = ""
    for _w in settings.get("remote_workers", []):
        if _w.get("name") == name:
            url = _w.get("url", "")
            break
    new_state = (not get_worker_enabled(name)) if enabled is None else bool(enabled)
    set_worker_enabled(name, new_state, url)
    log.info(f"[workers] dispatch to '{name}' {'ENABLED' if new_state else 'DISABLED'} via UI")
    return {"ok": True, "name": name, "enabled": new_state}


@app.post("/autoprocess/{target}/abort")
def autoprocess_abort(target: str):
    """
    Cooperatively abort a running auto_process pipeline for `target`. The job
    bails at the next step boundary and is marked aborted. If the target is
    running on a remote worker, the abort is forwarded there.
    """
    from nas_server import auto_process
    from nas_server.queue_manager import is_remote_inflight, abort_remote
    if is_remote_inflight(target):
        resp = abort_remote(target)
        if resp.get("ok"):
            return {"ok": True, "target": target, "where": "remote",
                    "worker": resp.get("worker")}
        # Fall through to local flag too, in case the monitor is local-side
        auto_process.request_abort(target)
        return {"ok": True, "target": target, "where": "remote_pending",
                "note": resp.get("error", "")}
    status = auto_process.get_autoprocess_status(target)
    if not status or status.get("phase") in ("done", "error", "aborted", None):
        return {"ok": False, "target": target, "error": "not_running"}
    auto_process.request_abort(target)
    log.info(f"[autoprocess] abort requested for '{target}' via UI")
    return {"ok": True, "target": target, "where": "local"}


# --- PixInsight post-processing (Phase 4a) ---

@app.post("/postprocess/{target}")
def postprocess(
    target: str,
    engine: str = "pixinsight",
    # background
    dbe: bool = False,
    dbe_correction: str = "subtraction",
    gradient_correction: bool = True,
    # color
    color_calibration: bool = True,
    bgn: bool = False,
    spcc: bool = False,
    nbn: bool = False,
    nbn_method: str = "MaximumStars",
    # linear sharp/denoise (CPU-based)
    mlt: bool = True,
    mlt_sharpen: float = 0.20,
    mlt_denoise: float = 0.50,
    tgv: bool = True,
    tgv_strength: float = 1.0,
    # AI plugins — GPU required; TF CPU models fail on this VM
    bxt: bool = False,
    bxt_psf: float = 4.0,
    bxt_nonstellar: float = 0.30,
    bxt_stars: float = 0.50,
    bxt_auto_psf: bool = False,
    nxt: bool = False,
    nxt_denoise: float = 0.70,
    nxt_detail: float = 0.15,
    starxt: bool = False,
    # stretch
    ht: bool = False,
    ht_target_bg: float = 0.12,
    # non-linear
    scnr: bool = False,
    scnr_amount: float = 0.9,
    cms: bool = True,
    morph: bool = False,
    morph_amount: float = 0.3,
    morph_iterations: int = 2,
    hdrmt: bool = False,
    lhe: bool = False,
    lhe_amount: float = 0.5,
    color_sat: bool = False,
    color_sat_boost: float = 0.3,
    curves: bool = False,
    curves_shape: str = "s_med",
    usm: bool = False,
    usm_sigma: float = 2.0,
    usm_amount: float = 0.7,
    usm_threshold: float = 0.02,
    ihdr: bool = False,
    ihdr_iterations: int = 5,
    ihdr_preservation: int = 5,
    ihdr_mask_strength: float = 1.25,
    # Python-side pre-processing
    adbe: bool = False,
    adbe_degree: int = 2,
    adbe_rbf_smooth: float = 0.1,
):
    """
    Run PI post-processing on the latest stacked FITS for target.

    Linear:  DBE|GC → CC → BGN → SPCC → MLT → TGV → BXT → NXT
    Stretch: HT (optional auto-stretch)
    Non-lin: StarXT → SCNR → HDRMT → LHE → ColorSat → Curves
    Save:    XISF

    Runs in background thread; check GET /postprocess/{target} for status.
    """
    import threading
    from nas_server.database import get_processed_files
    from nas_server.config import settings

    files = get_processed_files(target)
    if not files:
        return {"error": f"No processed files for {target}"}

    latest = files[0]
    lib = settings["seestar_library_path"]
    proc_dir = Path(lib) / target / "_processed"
    source_fits = str(proc_dir / latest["filename"])

    if engine != "pixinsight":
        return {"error": f"Unknown engine: {engine}"}

    status_key = f"pi:{target}"
    _pi_status[status_key] = {"phase": "starting", "target": target}

    def _run():
        from nas_server import pixinsight
        _pi_status[status_key]["phase"] = "running"
        result = pixinsight.run_postprocess(
            target=target,
            input_fits=source_fits,
            output_path=str(proc_dir / "pi_processed.xisf"),
            dbe=dbe, dbe_correction=dbe_correction,
            gradient_correction=gradient_correction,
            color_calibration=color_calibration, bgn=bgn, spcc=spcc,
            mlt=mlt, mlt_sharpen=mlt_sharpen, mlt_denoise=mlt_denoise,
            tgv=tgv, tgv_strength=tgv_strength,
            nbn=nbn, nbn_method=nbn_method,
            bxt=bxt, bxt_psf=bxt_psf, bxt_nonstellar=bxt_nonstellar,
            bxt_stars=bxt_stars, bxt_auto_psf=bxt_auto_psf,
            nxt=nxt, nxt_denoise=nxt_denoise, nxt_detail=nxt_detail,
            starxt=starxt,
            ht=ht, ht_target_bg=ht_target_bg,
            scnr=scnr, scnr_amount=scnr_amount,
            cms=cms, morph=morph, morph_amount=morph_amount, morph_iterations=morph_iterations,
            hdrmt=hdrmt, lhe=lhe, lhe_amount=lhe_amount,
            color_sat=color_sat, color_sat_boost=color_sat_boost,
            curves=curves, curves_shape=curves_shape,
            usm=usm, usm_sigma=usm_sigma, usm_amount=usm_amount, usm_threshold=usm_threshold,
            ihdr=ihdr, ihdr_iterations=ihdr_iterations,
            ihdr_preservation=ihdr_preservation, ihdr_mask_strength=ihdr_mask_strength,
            adbe=adbe, adbe_degree=adbe_degree, adbe_rbf_smooth=adbe_rbf_smooth,
        )
        _pi_status[status_key].update(result)
        _pi_status[status_key]["phase"] = "done" if result.get("ok") else "error"

        from nas_server import telegram
        steps = result.get("steps") or []
        elapsed = result.get("elapsed", 0)
        failed = [k.replace("_failed", "") for k, v in result.items()
                  if k.endswith("_failed") and v]
        adbe_note = " (+ADBE)" if result.get("adbe_applied") else ""
        if result.get("ok"):
            fail_note = f"\n⚠️ Failed steps: {', '.join(failed)}" if failed else ""
            telegram.send(
                f"✅ <b>PI postprocess complete</b>: <code>{target}</code>\n"
                f"Steps: {', '.join(steps)}{adbe_note} | {elapsed:.0f}s"
                f"{fail_note}\nOutput: {result.get('output_path', '?')}"
            )
        else:
            telegram.send(f"❌ PI postprocess failed for <code>{target}</code>\n"
                          f"Log: {result.get('log', '')[-500:]}")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "target": target, "engine": engine,
            "adbe": adbe, "source": source_fits}


@app.get("/postprocess/{target}")
def postprocess_status(target: str):
    """Get PI post-processing status for target."""
    key = f"pi:{target}"
    return _pi_status.get(key) or {"phase": "idle"}


_pi_status: dict = {}


# --- Experiment mode (Phase 5+) ---

@app.post("/experiment/{target}/{step}")
def experiment_run(
    target: str,
    step: str,
    input_fits: str = None,
    dry_run: bool = False,
):
    """
    Run experiment mode for a single processing step on target.
    Tries all ontology-defined variants, Claude picks best, stores results for learning.

    step: e.g. background_extraction, sharpen_linear, denoise_linear
    input_fits: absolute path; defaults to latest stacked FITS for target
    """
    import threading
    from nas_server.database import get_processed_files
    from nas_server.config import settings as cfg
    from nas_server.experiments import run_experiment

    if input_fits is None:
        files = get_processed_files(target)
        if not files:
            return {"error": f"No processed files for {target}"}
        lib = cfg["seestar_library_path"]
        proc_dir = Path(lib) / target / "_processed"
        input_fits = str(proc_dir / files[0]["filename"])

    if not Path(input_fits).exists():
        return {"error": f"Input not found: {input_fits}"}

    def _bg():
        result = run_experiment(
            target=target,
            step=step,
            input_fits=input_fits,
            dry_run=dry_run,
        )
        telegram.send(
            f"🔬 <b>Experiment complete</b>: <code>{target}</code> / {step}\n"
            f"Winner: <b>{result.get('winner', '?')}</b>\n"
            f"{result.get('winner_description', '')}\n"
            f"Reasoning: {result.get('reasoning', '')[:120]}\n"
            + (f"Prior: {result.get('learning_note', '')}" if result.get("learning_note") else "")
        )

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    return {"status": "started", "target": target, "step": step, "dry_run": dry_run}


@app.get("/experiment/{target}/{step}")
def experiment_status(target: str, step: str):
    """Get current experiment status for target/step."""
    from nas_server.experiments import get_experiment_status
    return get_experiment_status(target) or {"phase": "idle"}


@app.get("/experiment/{target}")
def experiment_status_all(target: str):
    """Get all experiment statuses for target."""
    from nas_server.experiments import get_experiment_status
    return get_experiment_status(target) or {"phase": "idle"}


@app.get("/learning/{step}")
def learning_priors(step: str, object_type: str = None):
    """
    Return accumulated learning priors for a processing step.
    Shows win rates, best variant, averaged winning parameters.
    """
    from nas_server.database import get_experiment_priors, get_all_experiment_steps
    from nas_server.experiments import get_learned_defaults

    priors = get_experiment_priors(step, object_type)
    learned = get_learned_defaults(step, object_type or "unknown")
    return {
        "step": step,
        "object_type": object_type,
        "priors": priors,
        "learned_defaults": learned,
    }


@app.get("/learning")
def learning_summary():
    """Summary of all steps with accumulated learning data."""
    from nas_server.database import get_all_experiment_steps, get_experiment_priors
    from nas_server.experiments import get_learned_defaults

    steps = get_all_experiment_steps()
    return {
        "steps_with_data": steps,
        "summary": [
            {
                "step": s,
                "learned": get_learned_defaults(s),
            }
            for s in steps
        ],
    }


# --- Story page (Phase 6) ---

_story_regen_status: dict = {"running": False, "done": 0, "total": 0, "errors": 0}


def _regen_narratives_bg(target: str | None):
    import time as _time
    from nas_server.database import get_story_data, get_conn
    from nas_server.claude_client import write_story_entry

    data = get_story_data(target)
    _story_regen_status["running"] = True
    _story_regen_status["done"] = 0
    _story_regen_status["errors"] = 0
    _story_regen_status["total"] = len(data)

    for t in data:
        if not _story_regen_status["running"]:
            break
        tname = t["target"]
        # Skip if already narrated
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM claude_assessments WHERE target=? AND phase='story_narrative' LIMIT 1",
                (tname,)
            ).fetchone()
        if row:
            _story_regen_status["done"] += 1
            continue

        try:
            narrative = write_story_entry(tname, t)
            if narrative:
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO claude_assessments "
                        "(target, phase, model, scores, recommendation, created_at) "
                        "VALUES (?, 'story_narrative', ?, '{}', ?, datetime('now'))",
                        (tname, "claude-sonnet-4-6", narrative)
                    )
        except Exception as e:
            log.warning(f"[story] narrative failed for {tname}: {e}")
            _story_regen_status["errors"] += 1

        _story_regen_status["done"] += 1
        _time.sleep(1.3)  # stay under 50 req/min rate limit

    _story_regen_status["running"] = False


@app.post("/story/regenerate")
def story_regenerate(target: str = None):
    """
    Start background narrative regeneration for all targets (or one target).
    Throttled to 50 req/min. Skips targets that already have a cached narrative.
    """
    import threading
    if _story_regen_status.get("running"):
        return {"status": "already_running", **_story_regen_status}
    t = threading.Thread(target=_regen_narratives_bg, args=(target,), daemon=True)
    t.start()
    return {"status": "started", "total": _story_regen_status.get("total", 0)}


@app.get("/story/regenerate")
def story_regenerate_status():
    """Check narrative regeneration progress."""
    return _story_regen_status


@app.get("/story", response_class=HTMLResponse)
def story_page(target: str = None):
    """Astrophotography story page. target= for single-target view."""
    from nas_server.story import generate_story_html
    return generate_story_html(target=target)


@app.get("/story/export")
def story_export(embed_images: bool = False):
    """Download the story as a self-contained HTML file."""
    from nas_server.story import generate_story_html
    import io
    html = generate_story_html(embed_images=embed_images)
    buf = io.BytesIO(html.encode())
    return StreamingResponse(
        buf,
        media_type="text/html",
        headers={"Content-Disposition": "attachment; filename=astro_story.html"},
    )


@app.post("/story/previews")
def story_generate_previews(target: str = None):
    """
    Generate arcsinh-stretch JPEG previews for all (or one) target whose _processed/
    dir contains FITS files but no preview JPEG. Used to populate story thumbnails.
    """
    import numpy as np
    from pathlib import Path as _Path
    from astropy.io import fits as afits
    from PIL import Image as _Image

    lib = _Path(settings["seestar_library_path"])
    targets_to_scan = []
    if target:
        targets_to_scan = [lib / target / "_processed"]
    else:
        targets_to_scan = sorted(lib.glob("*/_processed"))

    generated = []
    skipped = []
    errors = []

    for proc_dir in targets_to_scan:
        tname = proc_dir.parent.name
        if not proc_dir.is_dir():
            continue
        # Skip if any JPEG already exists
        existing_jpgs = list(proc_dir.glob("*.jpg"))
        if existing_jpgs:
            skipped.append(tname)
            continue
        # Pick best FITS: largest file (most data integrated)
        fits_files = sorted(
            [f for f in proc_dir.glob("*.fit")] + [f for f in proc_dir.glob("*.fits")],
            key=lambda p: p.stat().st_size, reverse=True
        )
        if not fits_files:
            continue
        src = fits_files[0]
        out = proc_dir / "preview.jpg"
        try:
            with afits.open(src) as hdul:
                data = hdul[0].data.astype(np.float32)
            if data.ndim == 3:
                data = np.transpose(data, (1, 2, 0))
            p40, p999 = np.percentile(data, 40), np.percentile(data, 99.9)
            if p999 > p40:
                data = np.arcsinh((data - p40) / (p999 - p40) * 3) / np.arcsinh(3)
            data = np.clip(data, 0, 1)
            _Image.fromarray((data * 255).astype(np.uint8)).save(str(out), quality=90)
            generated.append({"target": tname, "fits": src.name, "preview": out.name})
            log.info(f"[story/previews] {tname}: generated {out.name} from {src.name}")
        except Exception as e:
            errors.append({"target": tname, "error": str(e)})
            log.warning(f"[story/previews] {tname}: {e}")

    return {
        "generated": len(generated),
        "skipped_already_have_jpg": len(skipped),
        "errors": len(errors),
        "details": generated,
        "error_details": errors,
    }


@app.get("/image/{target}/{filename:path}")
def serve_image(target: str, filename: str):
    """Serve a processed JPEG from _processed/ (including experiment subdirs)."""
    proc_dir = Path(settings["seestar_library_path"]) / target / "_processed"
    img_path = proc_dir / filename
    if not img_path.exists() or img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(img_path), media_type="image/jpeg")


@app.get("/report/{target}", response_class=HTMLResponse)
def report_list(target: str):
    """List all processing run reports for a target."""
    import urllib.parse as _uparse
    from nas_server.database import get_processing_runs
    from nas_server.story import _page_shell
    from nas_server.folio_generator import get_hero
    runs = get_processing_runs(target)
    if not runs:
        return _page_shell(
            title=f"{target} — Reports",
            body=f'<div style="padding:3rem;text-align:center;color:#8b949e">'
                 f'No processing runs recorded for {target}. '
                 f'Run <code>seestar autoprocess "{target}"</code> first.</div>',
        )
    hero = get_hero(target) or {}
    hero_rid = hero.get("run_id")
    tgt_enc = _uparse.quote(target, safe="")
    rows = ""
    for r in runs:
        rid = r["id"]
        ts = (r.get("finished_at") or "")[:16].replace("T", " ")
        wf = r.get("workflow", "")
        elapsed = r.get("elapsed_s", 0)
        fs = r.get("final_scores") or {}
        overall = fs.get("overall", "—")
        dry = " (dry run)" if r.get("dry_run") else ""
        if r.get("dry_run"):
            hero_cell = '<span style="color:#8b949e">—</span>'
        elif rid == hero_rid:
            hero_cell = '<span style="color:#bc8cff;font-weight:600">★ hero</span>'
        else:
            hero_cell = (f'<button onclick="setHero(this,{rid})" '
                         f'style="background:#161b22;border:1px solid #30363d;color:#8b949e;'
                         f'border-radius:5px;padding:.2rem .5rem;font-size:.78rem;cursor:pointer">'
                         f'★ set hero</button>')
        rows += (
            f'<tr>'
            f'<td><a href="/report/{target}/{rid}">{ts}</a></td>'
            f'<td>{wf}{dry}</td>'
            f'<td>{elapsed:.0f}s</td>'
            f'<td>{overall}</td>'
            f'<td>{hero_cell}</td>'
            f'</tr>'
        )
    body = f"""
<div style="max-width:800px;margin:0 auto;padding:2rem">
  <div style="font-size:.82rem;color:#8b949e;margin-bottom:1rem">
    <a href="/story">← Story</a> · <a href="/gallery">Gallery →</a>
  </div>
  <h1 style="font-size:1.5rem;margin-bottom:1.5rem">{target} — Processing Runs</h1>
  <table style="width:100%;border-collapse:collapse;font-size:.88rem">
    <thead><tr style="color:#8b949e;border-bottom:1px solid #30363d">
      <th style="text-align:left;padding:.4rem .6rem">Date</th>
      <th style="text-align:left;padding:.4rem .6rem">Workflow</th>
      <th style="text-align:left;padding:.4rem .6rem">Duration</th>
      <th style="text-align:left;padding:.4rem .6rem">Final Score</th>
      <th style="text-align:left;padding:.4rem .6rem">Hero</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<script>
async function setHero(btn, rid) {{
  btn.disabled = true; btn.textContent = 'Setting…';
  try {{
    const r = await fetch('/hero/set?target={tgt_enc}&run_id=' + rid, {{method:'POST'}});
    if (r.ok) location.reload();
    else {{ btn.textContent = 'Failed'; btn.disabled = false; }}
  }} catch (e) {{ btn.textContent = 'Failed'; btn.disabled = false; }}
}}
</script>"""
    return _page_shell(title=f"{target} — Reports", body=body)


@app.get("/report/{target}/{run_id}", response_class=HTMLResponse)
def report_detail(target: str, run_id: int):
    """Full processing run report — steps, settings, images, scores, critical eval."""
    from nas_server.database import get_processing_runs
    from nas_server.story import render_run_report_html
    runs = get_processing_runs(target)
    run = next((r for r in runs if r["id"] == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found for {target}")
    return render_run_report_html(run, lib_path=settings["seestar_library_path"])


@app.get("/devlog", response_class=HTMLResponse)
def devlog_page():
    """Pipeline development journal — decisions, changes, and architectural notes."""
    from nas_server.devlog import get_entries
    from nas_server.story import render_devlog_html
    return render_devlog_html(get_entries())


@app.post("/devlog")
def devlog_add(title: str, body: str, category: str = "decision",
               files: str = ""):
    """Add a new entry to the development journal."""
    from nas_server.devlog import add_entry
    file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []
    entry = add_entry(title=title, body=body, category=category, files=file_list)
    return {"ok": True, "id": entry["id"]}


# --- Frame scoring (Phase 3 sub-frame selection) ---

@app.get("/score/{target}")
def score_frames(target: str, bottom_pct: float = 0.10):
    """Score all raw FITS frames for a target using SEP quality metrics."""
    from pathlib import Path
    from nas_server.database import get_conn
    folder = None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT path FROM targets WHERE target=?", (target,)
            ).fetchone()
            if row:
                folder = Path(row[0])
    except Exception:
        pass
    if not folder or not folder.exists():
        # Fall back to NAS library path
        folder = Path("/mnt/nas_data") / target
    if not folder.exists():
        return {"error": f"Cannot find frame folder for {target}"}
    try:
        from nas_server.seti_astro import score_frames as _score
        return {"target": target, **_score(folder, bottom_pct=bottom_pct)}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# FITS on-demand preview
# ---------------------------------------------------------------------------

_PREVIEW_CACHE = Path(settings.get("db_path", "~/seestar_database/astro_data.db")).parent / "preview_cache"


@app.get("/fits-preview/{target}/{path:path}")
def fits_preview_endpoint(target: str, path: str,
                          target_bg: float = 0.30, shadow_clip_k: float = 2.8,
                          stf: bool = True):
    """Return a cached JPEG preview of any FITS or XISF file in the library.

    stf=True (default): PixInsight STF auto-stretch. stf=False: raw linear view
    (min/max normalised, no midtone stretch) so the unstretched data can be
    inspected on the web. XISF files (PixInsight output) are converted to a
    temporary FITS first, then rendered with the same generators as FITS.
    """
    import hashlib
    from nas_server.seti_astro import (generate_preview_stf, generate_preview_nonlinear,
                                       generate_preview_image)
    lib = Path(settings["seestar_library_path"])
    fits_path = lib / target / path
    if not fits_path.exists():
        raise HTTPException(404, detail=f"FITS not found: {path}")

    suffix = fits_path.suffix.lower()
    is_raster = suffix in (".tif", ".tiff")  # already-display-ready (Photoshop export)

    _PREVIEW_CACHE.mkdir(parents=True, exist_ok=True)
    if is_raster:
        key = hashlib.md5(f"{fits_path}|raster".encode()).hexdigest()
    elif stf:
        key = hashlib.md5(f"{fits_path}|stf|{target_bg:.4f}|{shadow_clip_k:.3f}".encode()).hexdigest()
    else:
        key = hashlib.md5(f"{fits_path}|linear".encode()).hexdigest()
    jpg_path = _PREVIEW_CACHE / f"{key}.jpg"

    if not jpg_path.exists():
        # XISF has no astropy reader — convert to a temp FITS first.
        render_path = fits_path
        tmp_fits = None
        try:
            if is_raster:
                generate_preview_image(fits_path, jpg_path)
            else:
                if suffix == ".xisf":
                    from nas_server.xisf_io import xisf_to_fits
                    tmp_fits = _PREVIEW_CACHE / f"{key}_src.fit"
                    xisf_to_fits(str(fits_path), str(tmp_fits))
                    render_path = tmp_fits
                if stf:
                    generate_preview_stf(render_path, jpg_path,
                                         target_bg=target_bg, shadow_clip_k=shadow_clip_k)
                else:
                    generate_preview_nonlinear(render_path, jpg_path)
        except Exception as e:
            log.error(f"[fits-preview] {fits_path.name}: {e}")
            raise HTTPException(500, detail=str(e))
        finally:
            if tmp_fits is not None and tmp_fits.exists():
                tmp_fits.unlink()

    return FileResponse(str(jpg_path), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@app.get("/fits/{target}/{path:path}", response_class=HTMLResponse)
def fits_viewer_route(target: str, path: str):
    """FITS detail viewer page with stretch controls and prev/next nav."""
    return _web.fits_viewer_page(target, path)


@app.get("/fits/{target}", response_class=HTMLResponse)
def fits_viewer_default(target: str):
    """Redirect to the first FITS file found for a target."""
    from fastapi.responses import RedirectResponse
    lib = Path(settings["seestar_library_path"])
    tdir = lib / target
    # Prefer _processed files, fall back to raw stacks
    proc = sorted((tdir / "_processed").glob("*.fit")) + sorted((tdir / "_processed").glob("*.fits"))
    raw = sorted(tdir.glob("*.fit")) + sorted(tdir.glob("*.fits"))
    candidates = proc or raw
    if not candidates:
        raise HTTPException(404, detail=f"No FITS files found for {target}")
    first = candidates[0].relative_to(tdir)
    import urllib.parse
    return RedirectResponse(f"/fits/{urllib.parse.quote(target, safe='')}/{urllib.parse.quote(str(first), safe='/')}")


# ---------------------------------------------------------------------------
# Web UI pages
# ---------------------------------------------------------------------------

from nas_server import web as _web


@app.get("/", response_class=HTMLResponse)
def home():
    return _web.home_page()


@app.get("/targets-view", response_class=HTMLResponse)
def targets_view():
    return _web.targets_page()


@app.get("/worklist", response_class=HTMLResponse)
def worklist_view():
    return _web.worklist_page()


@app.get("/messier", response_class=HTMLResponse)
def messier_view():
    from nas_server.messier import messier_page
    return messier_page()


@app.get("/messier-tile/{name}")
def messier_tile(name: str):
    """Serve a cached WCS-centered Messier Wall tile JPG."""
    from fastapi.responses import FileResponse
    from nas_server.messier_tiles import TILE_DIR
    p = (TILE_DIR / name).resolve()
    if not str(p).startswith(str(TILE_DIR)) or not p.exists():
        raise HTTPException(status_code=404, detail="no tile")
    return FileResponse(str(p), media_type="image/jpeg")


@app.post("/messier/rebuild-tiles")
def messier_rebuild_tiles(force: bool = False):
    """Rebuild the Messier Wall tiles in a background thread (WCS-centered crops
    + guest harvesting). Solves each new final once with ASTAP; cached after."""
    from nas_server.messier_tiles import build_tiles
    def _run():
        try:
            build_tiles(force=force)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"messier tile build failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"message": "tile rebuild started", "force": force}


@app.get("/manual-processing", response_class=HTMLResponse)
def manual_processing_view():
    return _web.manual_processing_page()


@app.get("/manual-processing/folder/{target}", response_class=HTMLResponse)
def manual_folder_view(target: str):
    return _web.manual_folder_page(target)


@app.get("/manual-report/{run_id}", response_class=HTMLResponse)
def manual_report_view(run_id: int):
    return _web.manual_report_page(run_id)


@app.post("/manual-processing/flag")
def manual_flag(target: str = Body(...), filename: str = Body(...)):
    """Flag one file as the manual final for a target, then grade it off-thread."""
    from nas_server.manual_capture import flag_manual_final, grade_run
    run_id = flag_manual_final(target, filename)
    if run_id is None:
        raise HTTPException(status_code=404, detail="File not found")
    threading.Thread(target=grade_run, args=(run_id,), daemon=True).start()
    return {"ok": True, "run_id": run_id}


@app.post("/manual-processing/unflag")
def manual_unflag(run_id: int = Body(..., embed=True)):
    """Remove a mistakenly-flagged manual final."""
    from nas_server.manual_capture import unflag_manual_final
    if not unflag_manual_final(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True}


@app.post("/manual-processing/done")
def manual_done(target: str = Body(..., embed=True)):
    """Finish a folder after flagging one or more finals — clears it from the queue."""
    from nas_server.manual_capture import finish_folder
    finish_folder(target)
    return {"ok": True}


@app.post("/manual-processing/skip")
def manual_skip(target: str = Body(..., embed=True)):
    """Mark a folder reviewed with no manual final."""
    from nas_server.manual_capture import skip_folder
    skip_folder(target)
    return {"ok": True}


@app.post("/manual-processing/reopen")
def manual_reopen(target: str = Body(..., embed=True)):
    """Return a reviewed folder to the candidate queue."""
    from nas_server.manual_capture import reopen_folder
    reopen_folder(target)
    return {"ok": True}


@app.post("/manual-processing/grade")
def manual_processing_grade():
    from nas_server.manual_capture import grade_pending_manual_runs
    threading.Thread(target=grade_pending_manual_runs, daemon=True).start()
    return {"message": "Grading started"}


@app.post("/list/add")
def list_add_route(list: str, target: str):
    from nas_server.database import list_add
    return {"ok": list_add(list, target)}


@app.post("/list/remove")
def list_remove_route(list: str, target: str):
    from nas_server.database import list_remove
    list_remove(list, target)
    return {"ok": True}


@app.get("/gallery", response_class=HTMLResponse)
def gallery_view():
    return _web.gallery_page()


@app.post("/hero/set")
def hero_set_route(target: str, run_id: int):
    from nas_server.database import get_processing_runs
    from nas_server.folio_generator import set_hero
    runs = get_processing_runs(target)
    run = next((r for r in runs if r["id"] == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found for {target}")
    fs = run.get("final_scores") or {}
    ov = fs.get("overall")
    out_path = run.get("output_path") or ""
    preview = _web._worklist_thumb_url(target, out_path)
    set_hero(target, run_id, out_path, ov if isinstance(ov, (int, float)) else None,
             preview, chosen_by="user")
    return {"ok": True, "hero_run_id": run_id}


@app.get("/queue-view", response_class=HTMLResponse)
def queue_view():
    return _web.queue_page()


@app.get("/queue-view/rows", response_class=HTMLResponse)
def queue_rows():
    return _web.queue_rows_partial()


@app.get("/learning-view", response_class=HTMLResponse)
def learning_view():
    return _web.learning_page()


@app.get("/calendar", response_class=HTMLResponse)
def calendar_current():
    return _web.calendar_page()


@app.get("/calendar/{year}/{month}", response_class=HTMLResponse)
def calendar_month(year: int, month: int):
    return _web.calendar_page(year, month)


@app.get("/help", response_class=HTMLResponse)
def help_page():
    return _web.help_page()


@app.get("/workflows-doc", response_class=HTMLResponse)
def workflows_doc_view():
    from nas_server import workflow_docs
    return workflow_docs.workflow_docs_page()


@app.get("/frames/{target}", response_class=HTMLResponse)
def frames_page(target: str):
    return _web.frames_page(target)


@app.get("/pipeline-view", response_class=HTMLResponse)
def pipeline_view():
    return _web.pipeline_page()


@app.get("/stack-history", response_class=HTMLResponse)
def stack_history_all():
    return _web.stack_history_page()


@app.get("/stack-history/{target}", response_class=HTMLResponse)
def stack_history_target(target: str):
    return _web.stack_history_page(target)


@app.post("/light_files/toggle_exclude")
def toggle_exclude_route(file_path: str):
    new_val = database.toggle_frame_exclude(file_path)
    return {"excluded": new_val}


# ---------------------------------------------------------------------------
# Target detail + Associations pages
# ---------------------------------------------------------------------------

@app.get("/target/{target}", response_class=HTMLResponse)
def target_detail(target: str):
    return _web.target_detail_page(target)


@app.post("/target/{target}/clear-crop")
def target_clear_crop(target: str):
    """Forget the saved per-target crop. The next process opens a fresh crop review."""
    from nas_server.target_crop import clear_target_crop
    cleared = clear_target_crop(target)
    log.info(f"[crop] cleared saved crop for '{target}': {cleared}")
    return {"ok": True, "cleared": cleared, "target": target}


@app.get("/folio/{target}", response_class=HTMLResponse)
def folio_view(target: str):
    return _web.folio_page(target)


@app.get("/associations", response_class=HTMLResponse)
def associations_view():
    return _web.associations_page()


@app.post("/targets/{target}/association")
async def update_target_association(target: str, request: Request):
    # Read body once as raw dict — Pydantic would consume it before request.json() could
    raw = await request.json()
    sent_fields = set(raw.keys()) & {"association", "mosaic_association", "mosaic"}
    association = raw.get("association")
    mosaic_association = raw.get("mosaic_association")
    mosaic = raw.get("mosaic")
    database.set_target_association(target, association, mosaic_association,
                                    mosaic=mosaic, _fields=sent_fields or None)
    return {"ok": True, "target": target,
            "association": association,
            "mosaic_association": mosaic_association,
            "mosaic": mosaic}


@app.get("/associations/suggest")
def suggest_associations():
    """Scan all targets for catalog-based association suggestions (Messier/Caldwell + name variants)."""
    return {"suggestions": database.suggest_associations()}


class LinkRequest(BaseModel):
    target_a: str
    target_b: str


@app.post("/associations/link")
def link_association(body: LinkRequest):
    """Bidirectionally link two targets as associations."""
    database.link_association(body.target_a, body.target_b)
    return {"ok": True, "target_a": body.target_a, "target_b": body.target_b}


@app.post("/targets/{target}/comments")
async def add_comment(target: str, body: dict):
    """Add a user comment/feedback note for a target (optionally linked to a run_id)."""
    comment = (body.get("comment") or "").strip()
    if not comment:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="comment is required")
    run_id = body.get("run_id") or None
    from nas_server.database import add_target_comment
    result = add_target_comment(target, comment, run_id=run_id)
    return result


@app.get("/targets/{target}/comments")
def list_comments(target: str, limit: int = 20):
    """List user comments for a target, newest first."""
    from nas_server.database import get_target_comments
    return get_target_comments(target, limit=limit)


@app.delete("/targets/{target}/comments/{comment_id}")
def delete_comment(target: str, comment_id: int):
    """Delete a user comment."""
    from nas_server.database import delete_target_comment
    deleted = delete_target_comment(comment_id)
    return {"ok": deleted}


@app.post("/targets/transient")
async def set_transient(body: dict):
    target = body.get("target", "")
    val = int(bool(body.get("transient", False)))
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE targets SET transient=? WHERE target=?", (val, target))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Manual review endpoints
# ---------------------------------------------------------------------------

@app.get("/review", response_class=HTMLResponse)
def review_list():
    from nas_server.review_web import review_list_page
    return review_list_page()


@app.get("/review-view/rows", response_class=HTMLResponse)
def review_rows():
    from nas_server.review_web import review_rows_partial
    return review_rows_partial()


@app.get("/review/{review_id}", response_class=HTMLResponse)
def review_detail(review_id: int):
    from nas_server.review_web import review_detail_page
    return review_detail_page(review_id)


@app.get("/review/{review_id}/variant-image/{label}")
def review_variant_image(review_id: int, label: str):
    """Serve variant preview JPEG for the review detail page."""
    from fastapi.responses import FileResponse
    from nas_server.database import get_manual_review
    r = get_manual_review(review_id)
    if not r:
        raise HTTPException(status_code=404, detail="Review not found")
    variants = r.get("variants_json") or []
    for v in variants:
        if v.get("label") == label:
            jp = v.get("jpeg_path", "")
            if jp and os.path.exists(jp):
                return FileResponse(jp, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Image not found")


@app.post("/review/{review_id}/decide", response_class=HTMLResponse)
async def review_decide(review_id: int, request: Request):
    from nas_server.database import get_manual_review, decide_manual_review
    from nas_server import review_events
    from fastapi.responses import RedirectResponse

    form = await request.form()
    winner_label = (form.get("winner_label") or "").strip()
    user_reasoning = (form.get("user_reasoning") or "").strip()

    r = get_manual_review(review_id)
    if not r or r.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Review not pending")
    if not winner_label:
        raise HTTPException(status_code=400, detail="No variant selected")

    variants = r.get("variants_json") or []
    final_variant = None
    for v in variants:
        if v.get("label") == winner_label:
            final_variant = v.get("variant_id")
            break
    if not final_variant:
        raise HTTPException(status_code=400, detail="Unknown variant label")

    claude_label = r.get("claude_winner_label")
    agreed = (winner_label == claude_label)

    if not agreed and claude_label and claude_label != winner_label:
        # Show disagreement panel — let user confirm or switch before locking in
        from nas_server.review_web import render_disagree_confirm
        claude_note = r.get("claude_reasoning") or ""
        return HTMLResponse(
            render_disagree_confirm(review_id, winner_label, claude_label,
                                    claude_note, user_reasoning)
        )

    decide_manual_review(review_id, winner_label, user_reasoning, final_variant, agreed)
    review_events.signal(review_id)
    from fastapi.responses import Response
    return Response(
        status_code=200,
        headers={"HX-Redirect": f"/review/{review_id}"},
    )


@app.post("/review/{review_id}/decide-final", response_class=HTMLResponse)
async def review_decide_final(review_id: int, request: Request):
    """Finalise a decision after the disagreement confirmation panel."""
    from nas_server.database import get_manual_review, decide_manual_review
    from nas_server import review_events
    from fastapi.responses import Response

    form = await request.form()
    winner_label   = (form.get("winner_label") or "").strip()
    user_reasoning = (form.get("user_reasoning") or "").strip()

    r = get_manual_review(review_id)
    if not r or r.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Review not pending")
    if not winner_label:
        raise HTTPException(status_code=400, detail="No variant selected")

    variants = r.get("variants_json") or []
    final_variant = None
    for v in variants:
        if v.get("label") == winner_label:
            final_variant = v.get("variant_id")
            break
    if not final_variant:
        raise HTTPException(status_code=400, detail="Unknown variant label")

    claude_label = r.get("claude_winner_label")
    agreed = (winner_label == claude_label)
    decide_manual_review(review_id, winner_label, user_reasoning, final_variant, agreed)
    review_events.signal(review_id)
    return Response(
        status_code=200,
        headers={"HX-Redirect": f"/review/{review_id}"},
    )


@app.post("/review/{review_id}/manual-edit")
async def review_manual_edit(review_id: int, request: Request):
    """Add a manually-edited FITS as a new variant to the review."""
    from datetime import datetime, timedelta, timezone
    from nas_server.database import get_manual_review, add_review_manual_edit

    form = await request.form()
    fits_path = (form.get("fits_path") or "").strip()
    if not fits_path or not os.path.exists(fits_path):
        raise HTTPException(status_code=400, detail="FITS path not found on server")

    r = get_manual_review(review_id)
    if not r or r.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Review not pending")

    variants = r.get("variants_json") or []
    next_label = chr(ord("A") + len(variants))
    new_expires = (datetime.now(timezone.utc) + timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Generate a preview JPEG in a background thread
    import threading
    from pathlib import Path

    def _gen_preview():
        try:
            from nas_server.experiments import _generate_linear_composite, _generate_preview
            jp = Path(fits_path).with_suffix("_manual_preview.jpg")
            jp = jp.parent / (jp.stem + "_manual_preview.jpg")
            _generate_linear_composite(Path(fits_path), jp)
            return str(jp) if jp.exists() else ""
        except Exception:
            return ""

    jpeg_path = _gen_preview()

    new_entry = {
        "label": next_label,
        "variant_id": f"manual_{next_label.lower()}",
        "jpeg_path": jpeg_path,
        "metrics": {},
    }
    add_review_manual_edit(review_id, fits_path, new_entry, new_expires)
    return {"ok": True, "label": next_label}


@app.get("/review/{review_id}/source-image")
def review_source_image(review_id: int):
    """Serve a JPEG preview of the source FITS for the crop editor."""
    from nas_server.database import get_manual_review
    from fastapi.responses import FileResponse
    from pathlib import Path
    import tempfile

    r = get_manual_review(review_id)
    if not r:
        raise HTTPException(status_code=404, detail="Review not found")
    input_fits = r.get("input_fits_path", "")
    if not input_fits or not os.path.exists(input_fits):
        raise HTTPException(status_code=404, detail="Source FITS not found")

    cache_path = Path(tempfile.gettempdir()) / f"seestar_source_preview_{review_id}.jpg"
    if not cache_path.exists():
        from nas_server.seti_astro import generate_preview_stf
        ok = generate_preview_stf(input_fits, str(cache_path))
        if not ok or not cache_path.exists():
            raise HTTPException(status_code=500, detail="Preview generation failed")

    return FileResponse(str(cache_path), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@app.get("/review/{review_id}/crop", response_class=HTMLResponse)
def review_crop_page(review_id: int):
    """Manual crop editor."""
    from nas_server.database import get_manual_review
    from nas_server.crop_web import crop_editor_page

    r = get_manual_review(review_id)
    if not r:
        raise HTTPException(status_code=404, detail="Review not found")
    if r.get("status") != "pending":
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"/review/{review_id}", status_code=303)
    return HTMLResponse(crop_editor_page(review_id, r))


@app.post("/review/{review_id}/apply-crop")
async def review_apply_crop(review_id: int, request: Request):
    """Apply manual crop+rotation to source FITS and decide the review."""
    import numpy as np
    from pathlib import Path
    from astropy.io import fits as afits
    from nas_server.database import get_manual_review, decide_manual_review
    from nas_server import review_events

    form = await request.form()
    x          = float(form.get("x",              0))
    y          = float(form.get("y",              0))
    w          = float(form.get("width",           0))
    h          = float(form.get("height",          0))
    rotate     = float(form.get("rotate",          0))
    natural_w  = float(form.get("natural_width",   1)) or 1
    natural_h  = float(form.get("natural_height",  1)) or 1

    r = get_manual_review(review_id)
    if not r or r.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Review not pending")

    input_fits = r.get("input_fits_path", "")
    if not input_fits or not os.path.exists(input_fits):
        raise HTTPException(status_code=400, detail="Source FITS not found")

    # Derive exp_dir from any variant's jpeg_path (variants live in exp_dir)
    variants = r.get("variants_json") or []
    exp_dir: Path | None = None
    for v in variants:
        jp = v.get("jpeg_path", "")
        if jp:
            exp_dir = Path(jp).parent
            break
    if exp_dir is None:
        step = r.get("step", "crop")
        exp_dir = Path(input_fits).parent / "experiments" / step
    exp_dir.mkdir(parents=True, exist_ok=True)

    # Load FITS
    with afits.open(input_fits) as hdul:
        data   = hdul[0].data.copy().astype(np.float32)
        header = hdul[0].header.copy()

    orig_h = int(data.shape[-2])
    orig_w = int(data.shape[-1])

    # Map the Cropper.js box into array coords. The editor preview
    # (generate_preview_stf) is flipped north-up when CDELT2 > 0, and under
    # rotation getData() x/y live in the rotated bounding-canvas space —
    # cropper_box_to_array undoes both (mirrored/misaligned manual crops
    # otherwise: M 31/M 42 2026-06-10).
    _cdelt2 = header.get("CDELT2", None)
    flipped = _cdelt2 is not None and float(_cdelt2) > 0
    from nas_server.target_crop import cropper_box_to_array, rotated_crop
    cx, cy, cw, ch, rot_array = cropper_box_to_array(
        x, y, w, h, rotate, natural_w, natural_h, orig_w, orig_h, flipped)

    # Rotate-then-crop: extract the box from the rotated source so the result is
    # filled with real pixels (no black corner wedges).
    cropped = rotated_crop(data, cx, cy, cw, ch, rot_array)

    # Update WCS reference pixel for the new origin
    if "CRPIX1" in header:
        header["CRPIX1"] = float(header["CRPIX1"]) - cx
    if "CRPIX2" in header:
        header["CRPIX2"] = float(header["CRPIX2"]) - cy

    # Write directly to winner.fit — pipeline checks this path after ev.wait() returns
    winner_path = exp_dir / "winner.fit"
    afits.PrimaryHDU(data=cropped, header=header).writeto(
        str(winner_path), overwrite=True
    )

    # Generate JPEG preview (for the decided-view card)
    from nas_server.seti_astro import generate_preview_stf
    jpg_path = exp_dir / "manual_crop_preview.jpg"
    generate_preview_stf(str(winner_path), str(jpg_path))

    rot_note = f", rot={rotate:.1f}°" if abs(rotate) > 0.05 else ""
    reasoning = f"Manual crop ({cx},{cy})+{cw}×{ch}{rot_note}"

    # Persist the chosen crop for this target so future runs reuse it (crop step
    # reviews only). The winner FITS carries the rectangular sky-box via its WCS;
    # the manual rotation is folded into the stored PA, and fractional bounds +
    # rotation are kept as the no-WCS fallback.
    if (r.get("step") or "") == "crop" and r.get("target"):
        try:
            from nas_server import target_crop as _tcrop
            _frac = {
                "top": cy / orig_h,
                "bottom": (orig_h - (cy + ch)) / orig_h,
                "left": cx / orig_w,
                "right": (orig_w - (cx + cw)) / orig_w,
            }
            # rot_array (array-space) — the frac fallback replays it via
            # rotated_crop on raw array data, where display-space sign is wrong
            # for flipped previews.
            _tcrop.save_crop_from_fits(r["target"], str(winner_path),
                                       source="manual", frac=_frac,
                                       rotate_deg=rot_array)
        except Exception as _pe:
            import logging as _lg
            _lg.getLogger("seestar").warning(
                f"[apply-crop] persist saved crop failed for {r.get('target')}: {_pe}")

    decide_manual_review(review_id, "Manual", reasoning, "manual_crop", agreed=False)
    review_events.signal(review_id)

    from fastapi.responses import Response
    return Response(status_code=200, headers={"HX-Redirect": f"/review/{review_id}"})


@app.post("/review/{review_id}/retry")
def review_retry(review_id: int):
    from nas_server.database import set_review_status
    from nas_server import review_events
    set_review_status(review_id, "retried")
    review_events.signal(review_id)
    return {"ok": True}


@app.post("/review/{review_id}/abort")
def review_abort(review_id: int):
    from nas_server.database import set_review_status
    from nas_server import review_events
    set_review_status(review_id, "aborted")
    review_events.signal(review_id)
    return {"ok": True}


# ── Chat & AI Agent ──────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
def chat_page_route():
    from nas_server.web import chat_page
    return chat_page()


@app.post("/chat/session/new")
def new_chat_session():
    sid = database.create_chat_session()
    return {"session_id": sid}


@app.get("/chat/history")
def chat_history(session_id: int):
    return {"messages": database.get_chat_history(session_id)}


@app.get("/chat/sessions")
def chat_sessions_list():
    return {"sessions": database.list_chat_sessions()}


@app.post("/chat")
def chat_api(body: dict):
    from nas_server.agent import run_agent
    message    = body.get("message", "").strip()
    image_b64  = body.get("image_b64")
    session_id = body.get("session_id")

    if not message and not image_b64:
        return {"response": "(empty message)", "tool_calls": [], "session_id": session_id}

    if not session_id:
        session_id = database.create_chat_session()

    history = database.get_chat_history(session_id)

    if message and not history:
        database.update_session_title(session_id, message[:60])

    response = run_agent(message, image_b64, history=history)

    database.append_chat_message(session_id, "user", message or "[image]")
    database.append_chat_message(session_id, "assistant", response)

    return {"response": response, "tool_calls": [], "session_id": session_id}


# ── Planner ───────────────────────────────────────────────────────────────────

@app.get("/planner", response_class=HTMLResponse)
def planner_route():
    from nas_server.web import planner_page
    return planner_page()


@app.post("/planner/compute")
async def planner_compute(body: dict):
    from nas_server.planner import compute_plan, compute_schedule, get_narrative
    from starlette.concurrency import run_in_threadpool
    date_from = body.get("date_from", "")
    date_to   = body.get("date_to") or date_from
    lat  = float(body.get("lat",  settings.get("observer_lat", 33.18)))
    lon  = float(body.get("lon",  settings.get("observer_lon", -111.57)))
    elev = float(body.get("elevation", settings.get("observer_elevation_m", 350)))
    horizon_raw = body.get("horizon")  # [[az, alt], ...] or null
    horizon = [(float(p[0]), float(p[1])) for p in horizon_raw] if horizon_raw else None
    selected = set(body.get("selected", []))  # manual target override for Replan
    results   = await run_in_threadpool(compute_plan, date_from, date_to, lat, lon, elev, horizon)
    schedule  = await run_in_threadpool(compute_schedule, results, horizon, selected or None)
    # Mark which results made the schedule so get_narrative() knows what's actually planned
    scheduled_set = {s["target"] for s in schedule}
    for r in results:
        r["scheduled"] = r.get("target") in scheduled_set
    narrative = await run_in_threadpool(get_narrative, results, date_from, date_to, schedule)
    return {"results": results, "schedule": schedule, "narrative": narrative, "count": len(results)}


_AUTOFLAGS_PATH = os.path.join(os.path.expanduser("~"), "seestar_database", "planner_autoflags.json")


def _load_autoflags() -> dict:
    import json as _json
    if os.path.exists(_AUTOFLAGS_PATH):
        try:
            with open(_AUTOFLAGS_PATH) as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def _save_autoflags(flags: dict):
    import json as _json
    with open(_AUTOFLAGS_PATH, "w") as f:
        _json.dump(flags, f, indent=2)


@app.get("/planner/autoflags")
def get_autoflags():
    return _load_autoflags()


@app.post("/planner/autoflags")
async def set_autoflag(body: dict):
    target = (body.get("target") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target required")
    flags = _load_autoflags()
    entry = flags.get(target, {"auto_stack": False, "auto_process": False})
    if "auto_stack" in body:
        entry["auto_stack"] = bool(body["auto_stack"])
    if "auto_process" in body:
        entry["auto_process"] = bool(body["auto_process"])
    flags[target] = entry
    _save_autoflags(flags)
    return {"ok": True, "target": target, **entry}


@app.post("/planner/save-tonight")
async def save_plan_tonight(body: dict):
    """Override the stored nightly plan with the schedule from a manual compute."""
    from nas_server.database import save_planner_run
    date = (body.get("date") or "").strip()
    schedule = body.get("schedule") or []
    if not date or not schedule:
        raise HTTPException(status_code=400, detail="date and schedule required")
    save_planner_run(date, schedule, source="user")
    return {"ok": True, "date": date, "slots": len(schedule)}


@app.post("/planner/record-selection")
async def record_selection(body: dict):
    """Record a manual Replan selection event for learning."""
    from nas_server.database import record_replan_selection
    all_ranked = body.get("all_ranked") or []
    selected   = body.get("selected") or []
    if not all_ranked:
        return {"ok": True, "skipped": True}
    record_replan_selection(all_ranked, selected)
    return {"ok": True, "appearances": len(all_ranked), "selected": len(selected)}


@app.post("/planner/send-nightly")
def trigger_nightly_plan():
    """Manually trigger the nightly plan Telegram message."""
    import threading
    from nas_server.scheduler import nightly_plan
    threading.Thread(target=nightly_plan, daemon=True).start()
    return {"ok": True, "message": "nightly plan triggered"}


@app.get("/planner/stored-plan")
def get_stored_plan():
    """Return the latest saved nightly plan enriched with live int_hours and folio rec_h."""
    from nas_server.database import get_latest_planner_run, get_conn
    from nas_server.folio_generator import load_folio

    run = get_latest_planner_run()
    if not run:
        return {"plan": None}

    plan_date, slots = run
    if not slots:
        return {"plan": {"date": plan_date, "slots": []}}

    targets = [s["target"] for s in slots]

    # Bulk query integration hours
    placeholders = ",".join("?" * len(targets))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT target, ROUND(SUM(exposure_time)/3600.0, 2) as int_hours "
            f"FROM light_files WHERE target IN ({placeholders}) AND exclude=0 GROUP BY target",
            targets,
        ).fetchall()
    int_hours_map = {r[0]: r[1] for r in rows}

    enriched = []
    for slot in slots:
        target = slot["target"]
        start = slot.get("start_hhmm")
        end = slot.get("end_hhmm")

        # planned_h from hhmm strings
        planned_h = None
        if start and end:
            sh, sm = int(start[:2]), int(start[3:])
            eh, em = int(end[:2]), int(end[3:])
            s_frac = sh + sm / 60.0
            e_frac = eh + em / 60.0
            if e_frac < s_frac:
                e_frac += 24
            planned_h = round(e_frac - s_frac, 2)

        # rec_h from folio
        folio = load_folio(target)
        rec_h = None
        if folio:
            rec_h = (folio.get("s50_achievability") or {}).get("recommended_integration_hours")

        int_h = int_hours_map.get(target, 0.0) or 0.0
        enriched.append({
            "target": target,
            "start_hhmm": start,
            "end_hhmm": end,
            "planned_h": planned_h,
            "int_hours": int_h,
            "rec_h": rec_h,
        })

    return {"plan": {"date": plan_date, "slots": enriched}}


@app.post("/settings/horizon")
async def save_horizon(body: dict):
    from nas_server.config import save_setting
    horizon = body.get("horizon", [])
    if not isinstance(horizon, list):
        raise HTTPException(status_code=400, detail="horizon must be a list")
    save_setting("observer_horizon", [[float(p[0]), float(p[1])] for p in horizon])
    return {"ok": True, "count": len(horizon)}


# ── Suggestions ───────────────────────────────────────────────────────────────

@app.get("/suggestions", response_class=HTMLResponse)
def suggestions_page_route():
    from nas_server.web import suggestions_page
    return suggestions_page()


@app.get("/calibration", response_class=HTMLResponse)
def calibration_page_route():
    from nas_server.web import calibration_page
    return calibration_page()


@app.post("/suggestions/{suggestion_id}/resolve")
def resolve_suggestion(suggestion_id: int):
    from nas_server.database import resolve_agent_suggestion
    resolve_agent_suggestion(suggestion_id)
    return {"ok": True}


@app.post("/suggestions/add")
async def add_suggestion_route(body: dict):
    from nas_server.database import add_agent_suggestion
    desc = (body.get("description") or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="description required")
    file_hint = (body.get("file_hint") or "").strip() or None
    sid = add_agent_suggestion(desc, file_hint=file_hint or "", source="user")
    return {"ok": True, "id": sid}


# ── Crop Analysis (Phase 6) ───────────────────────────────────────────────────

@app.get("/crops/{target}/{filename:path}", response_class=HTMLResponse)
def crop_analysis_page(target: str, filename: str):
    """Crop region editor and analysis page for any processed image."""
    from nas_server.crop_analysis import crop_analysis_page as _page
    from nas_server.database import get_image_crops
    # Validate the image exists
    proc_dir = Path(settings["seestar_library_path"]) / target / "_processed"
    img_path = proc_dir / filename
    if not img_path.exists():
        raise HTTPException(404, detail=f"Image not found: {filename}")
    crops = get_image_crops(target, filename)
    return _page(target, filename, crops)


@app.post("/crops/{target}/{filename:path}/save")
async def save_crop_region(target: str, filename: str, request: Request):
    """Save a named crop region and generate its preview JPEG."""
    from nas_server.crop_analysis import generate_crop_jpeg, measure_crop_physics
    from nas_server.database import save_image_crop
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Region name is required"}
    x = float(body.get("x", 0))
    y = float(body.get("y", 0))
    w = float(body.get("w", 0))
    h = float(body.get("h", 0))
    natural_w = int(body.get("natural_w", 0))
    natural_h = int(body.get("natural_h", 0))

    proc_dir = Path(settings["seestar_library_path"]) / target / "_processed"
    source = proc_dir / filename
    if not source.exists():
        return {"ok": False, "error": "Source image not found"}

    crop_id = save_image_crop(target, filename, name, x, y, w, h)
    try:
        generate_crop_jpeg(source, crop_id, x, y, w, h, natural_w, natural_h,
                           display_w=0, display_h=0)
    except Exception as e:
        log.warning(f"[crop] failed to generate crop JPEG for #{crop_id}: {e}")

    return {"ok": True, "crop_id": crop_id}


@app.get("/crops/preview/{crop_id}")
def crop_preview(crop_id: int):
    """Serve a saved crop region's JPEG preview."""
    from nas_server.crop_analysis import crop_preview_path
    path = crop_preview_path(crop_id)
    if not path.exists():
        raise HTTPException(404, detail="Crop preview not found")
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=86400"})


@app.delete("/crops/{crop_id}")
def delete_crop_region(crop_id: int):
    """Delete a saved crop region and its preview JPEG."""
    from nas_server.database import delete_image_crop
    from nas_server.crop_analysis import crop_preview_path
    delete_image_crop(crop_id)
    preview = crop_preview_path(crop_id)
    if preview.exists():
        preview.unlink()
    return {"ok": True}


@app.post("/analyze-crop")
async def analyze_crop_endpoint(request: Request):
    """Send a saved crop JPEG to Claude for structured quality scoring."""
    import base64
    from nas_server.crop_analysis import crop_preview_path, format_physics, measure_crop_physics
    from nas_server.claude_client import analyze_crop_structured
    from nas_server.database import save_crop_analysis
    body = await request.json()
    crop_id = int(body.get("crop_id", 0))
    crop_name = body.get("crop_name", "")
    target = body.get("target", "")
    target_type = body.get("target_type", "default")

    path = crop_preview_path(crop_id)
    if not path.exists():
        return {"error": "Crop preview not found — try saving the region again"}

    metrics = measure_crop_physics(path)
    physics_str = format_physics(metrics)

    try:
        image_b64 = base64.b64encode(path.read_bytes()).decode()
        result = analyze_crop_structured(
            image_b64,
            crop_name=crop_name,
            target=target,
            physics=physics_str,
            target_type=target_type,
        )
        if not result:
            return {"error": "Analysis failed — check API key"}

        save_crop_analysis(
            crop_id,
            scores=result["scores"],
            aggregate=result["aggregate"],
            summary=result["summary"],
            concerns=result["concerns"],
            physics=metrics,
        )
        return {**result, "physics": physics_str, "physics_raw": metrics}
    except Exception as e:
        log.exception("[analyze-crop] error")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# NINA integration — polar alignment ready flag + status proxy
# ---------------------------------------------------------------------------

_nina_ready: bool = False
_nina_ready_set_at: str | None = None


@app.get("/nina/ready")
def nina_get_ready():
    return {"ready": _nina_ready, "set_at": _nina_ready_set_at}


@app.post("/nina/set_ready")
def nina_set_ready():
    global _nina_ready, _nina_ready_set_at
    from datetime import datetime, timezone
    _nina_ready = True
    _nina_ready_set_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("[nina] polar alignment confirmed — sequence unblocked")
    try:
        from nas_server import telegram
        telegram.send("✅ <b>NINA unblocked</b> — polar alignment confirmed, sequence proceeding.")
    except Exception:
        pass
    return {"ok": True, "set_at": _nina_ready_set_at}


@app.post("/nina/reset_ready")
def nina_reset_ready():
    global _nina_ready, _nina_ready_set_at
    _nina_ready = False
    _nina_ready_set_at = None
    log.info("[nina] ready flag reset for new night")
    return {"ok": True}


@app.get("/nina/status")
def nina_get_status():
    try:
        from nas_server.nina_client import get_status
        return get_status()
    except Exception as e:
        return {"error": str(e), "reachable": False}


# ---------------------------------------------------------------------------
# Remote worker idle task endpoints
# ---------------------------------------------------------------------------

@app.get("/idle/tasks")
def idle_get_tasks(limit: int = 10):
    """
    Hand out idle enrichment work to remote workers. Priority:
      1. score_frame — unscored light frames (one task per frame).
      2. solve_batch — a leased batch of unsolved subs for one target, when no
         scoring work remains. The batch is marked solve_status='solving' (a
         timestamped lease) so the VM idle worker doesn't grab the same frames;
         the worker plate-solves locally (NAS mounted at the same paths) and POSTs
         the WCS back to /idle/results. The VM stays the single DB writer.
    Workers POST results back to /idle/results.
    """
    from nas_server.database import get_unscored_light_frames
    frames = get_unscored_light_frames(limit=limit)
    if frames:
        return {
            "tasks": [
                {"file_path": f["file_path"], "target": f["target"], "task_type": "score_frame"}
                for f in frames
            ]
        }
    # No scoring work — offer a leased solve batch.
    from nas_server.database import claim_solve_batch
    target, paths = claim_solve_batch(limit=40)
    if target and paths:
        return {
            "tasks": [{
                "task_type": "solve_batch",
                "target": target,
                "file_paths": paths,
            }]
        }
    return {"tasks": []}


@app.post("/idle/results")
async def idle_post_results(body: dict = Body(...)):
    """
    Accept enrichment results from remote workers and write to DB (VM is the single
    writer). Two payloads, either or both may be present:
      - "results": scored frames
          [{"file_path", "fwhm", "eccentricity", "snr", "star_count",
            "sky_level", "gradient_severity"}, ...]
      - "solve_results": plate-solve WCS from a solve_batch lease
          [{"file_path", "solved_ra", "solved_dec", "solved_rot", "solved_scale",
            "solve_status"}, ...]
        After writing, alignment outliers are re-flagged per affected target.
    """
    from nas_server.database import update_light_frame_scores
    results = body.get("results", [])
    saved = 0
    for r in results:
        try:
            update_light_frame_scores(
                r["file_path"],
                fwhm=r.get("fwhm"),
                eccentricity=r.get("eccentricity"),
                snr=r.get("snr"),
                star_count=r.get("star_count"),
                sky_level=r.get("sky_level"),
                gradient_severity=r.get("gradient_severity"),
            )
            saved += 1
        except Exception as e:
            log.warning(f"[idle/results] failed to save {r.get('file_path')}: {e}")

    solve_results = body.get("solve_results", [])
    solved = 0
    affected_targets = set()
    if solve_results:
        from nas_server.database import update_light_frame_solve, get_conn
        for r in solve_results:
            try:
                update_light_frame_solve(
                    r["file_path"],
                    r.get("solved_ra"),
                    r.get("solved_dec"),
                    r.get("solved_rot"),
                    r.get("solved_scale"),
                    solve_status=r.get("solve_status", "failed"),
                )
                solved += 1
            except Exception as e:
                log.warning(f"[idle/results] failed to save solve {r.get('file_path')}: {e}")
        with get_conn() as conn:
            for r in solve_results:
                row = conn.execute(
                    "SELECT target FROM light_files WHERE file_path=?",
                    (r["file_path"],),
                ).fetchone()
                if row and row[0]:
                    affected_targets.add(row[0])
        from nas_server.sub_solver import flag_alignment_outliers
        for t in affected_targets:
            try:
                flag_alignment_outliers(t)
            except Exception as e:
                log.debug(f"[idle/results] outlier flag failed for {t}: {e}")

    if results:
        log.info(f"[idle/results] saved scores for {saved}/{len(results)} frames")
    if solve_results:
        log.info(f"[idle/results] saved {solved}/{len(solve_results)} solves "
                 f"across {len(affected_targets)} target(s)")
    return {"ok": True, "saved": saved, "solved": solved}


@app.post("/narrowband/{target}")
def narrowband_queue(
    target: str,
    ha: str,
    oiii: str,
    sii: str = "",
    palette: str = "foraxx",
    workflow: str = "seestar_narrowband",
    experiment_mode: bool = False,
    dry_run: bool = False,
):
    """
    Queue a narrowband palette composite + full processing job.

    ha / oiii / sii are filenames (relative to the target's folder) or absolute paths.
    palette: "foraxx" (default, natural HOO) or "sho" (Hubble SII-Ha-OIII).
    """
    from nas_server.queue_manager import add_job
    extra: dict = {
        "narrowband": {
            "ha_path":   ha,
            "oiii_path": oiii,
            "palette":   palette,
        }
    }
    if sii:
        extra["narrowband"]["sii_path"] = sii
    item = add_job(
        target,
        workflow=workflow,
        experiment_mode=experiment_mode,
        dry_run=dry_run,
        extra_params=extra,
    )
    log.info(f"[narrowband] queued '{target}' palette={palette} ha={ha} oiii={oiii}")
    return {"message": f"Narrowband '{target}' queued (palette={palette})", **item}


def _find_nbn_branch_sources(target: str) -> dict:
    """
    Locate the post-stretch starless image + stars layer from the target's most
    recent *full* processing run, to seed an NBN branch (Mode 2).

    Prior NBN branch runs (run dirs ending in ``_nbn``) are skipped: a branch run
    starts at scnr and never re-runs star removal, so it has no stars layer —
    branching off one yields a starless result. We always reach back to the last
    full run, which carries both the starless image and the stars layer.

    Preference order per run dir (newest first):
      1. snapshot files written by recent runs:
           nbn_branch_image.fit  +  nbn_branch_stars.fit
      2. glob fallback for older runs that predate the snapshots:
           highest-numbered  *_auto_scnr_a*.fit   (post-stretch starless)
           08_auto_stars.fit                       (stars layer)

    Returns {"ok": True, "image": <abs>, "stars": <abs|"">, "run": <name>}
    or {"ok": False, "error": ...}.
    """
    lib = settings["seestar_library_path"]
    runs_dir = Path(lib) / target / "_processed" / "runs"
    if not runs_dir.is_dir():
        return {"ok": False, "error": f"No runs dir for '{target}': {runs_dir}"}

    run_dirs = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not run_dirs:
        return {"ok": False, "error": f"No runs found for '{target}'"}

    for rd in run_dirs:
        # Never branch off a prior NBN branch run (no stars layer to recombine).
        if rd.name.endswith("_nbn"):
            continue
        # 1. Deterministic snapshots from recent runs
        snap_img = rd / "nbn_branch_image.fit"
        snap_stars = rd / "nbn_branch_stars.fit"
        if snap_img.exists():
            return {
                "ok": True,
                "image": str(snap_img),
                "stars": str(snap_stars) if snap_stars.exists() else "",
                "run": rd.name,
            }
        # 2. Glob fallback for older runs (predate snapshots). These are scnr-step
        #    variant outputs (a0..aN) with no promoted "winner" file; the primary
        #    variant a0 is the consistent choice. Re-running scnr on it is idempotent.
        scnr_imgs = sorted(rd.glob("*_auto_scnr_a*.fit"))
        if scnr_imgs:
            img = scnr_imgs[0]
            stars = rd / "08_auto_stars.fit"
            return {
                "ok": True,
                "image": str(img),
                "stars": str(stars) if stars.exists() else "",
                "run": rd.name,
            }

    return {"ok": False,
            "error": f"No NBN-branchable image found in any run for '{target}' "
                     f"(checked {len(run_dirs)} runs)"}


@app.post("/narrowband_norm/{target}")
def narrowband_norm_branch(
    target: str,
    method: str = "auto",
    workflow: str = "seestar_nebula",
    dry_run: bool = False,
):
    """
    Mode 2 — branch off the target's most recent processed run, pull in the
    already-stretched starless image, inject NarrowbandNormalization, and
    re-run the nonlinear tail (background_neutralize → ... → assess_final).

    The branch starts at the `scnr` step so the forced narrowband_norm and the
    full color/curves tail re-run on top of the NBN result. Output is written
    alongside the original as auto_final_nbn.fit / auto_final_nbn_preview.jpg.

    method: "auto" (o3Boost auto-scaled from Hα dominance) | "soft" (o3Boost 1.3)
            | "strong" (o3Boost 1.7).
    """
    from nas_server.queue_manager import add_job

    src = _find_nbn_branch_sources(target)
    if not src.get("ok"):
        raise HTTPException(status_code=404, detail=src.get("error"))

    # Resolve the OIII boost. "auto" lets auto_process scale o3Boost from the Hα/OIII
    # dominance ratio (o3Boost=clamp(0.50+0.395·ratio, 1.25, 1.7)) — a more Hα-dominant
    # target needs more OIII boost to surface its weak teal core. "soft"/"strong" pin a
    # manual override (1.3 / 1.7) via the ontology variants. The old MaximumStars/Equalize
    # `method` was a silent no-op in PI 1.9.3 (normalizationMode doesn't exist).
    resolved_method = method
    ha_ratio = None
    force_variants: dict = {}
    if method == "soft":
        force_variants["narrowband_norm"] = "nbn_o3_soft"
    elif method == "strong":
        force_variants["narrowband_norm"] = "nbn_o3_strong"
    else:
        resolved_method = "auto"
        from nas_server.image_analyzer import ha_dominance_ratio
        ha_ratio = ha_dominance_ratio(src["image"])
        _o3 = max(1.25, min(1.7, 0.50 + 0.395 * ha_ratio))
        log.info(f"[narrowband_norm] auto mode: Hα/OIII ratio={ha_ratio:.2f} "
                 f"→ o3Boost={_o3:.2f}")

    extra: dict = {
        "force_steps": ["narrowband_norm"],
        "branch": {
            "start_step": "scnr",
            "image": src["image"],
            "stars": src["stars"],
            "suffix": "nbn",
        },
    }
    if force_variants:
        extra["force_variants"] = force_variants
    item = add_job(target, workflow=workflow, dry_run=dry_run, extra_params=extra)
    log.info(f"[narrowband_norm] branch queued '{target}' from run={src['run']} "
             f"image={Path(src['image']).name} stars={Path(src['stars']).name or '∅'} "
             f"method={method}→{resolved_method}"
             + (f" (Hα/OIII={ha_ratio:.2f})" if ha_ratio is not None else ""))
    return {
        "message": f"NBN branch queued for '{target}' (from {src['run']}) — "
                   f"mode={resolved_method}"
                   + (f", Hα/OIII={ha_ratio:.2f}" if ha_ratio is not None else ""),
        "method": resolved_method,
        "ha_oiii_ratio": ha_ratio,
        "source_image": src["image"],
        "source_stars": src["stars"],
        **item,
    }


# ── Processing Videos ─────────────────────────────────────────────────────────

@app.get("/videos", response_class=HTMLResponse)
def videos_page_route():
    """Gallery of compiled pipeline processing videos."""
    from nas_server.web import videos_page
    return videos_page()


@app.get("/video-file/{target}/{filename:path}")
def serve_video_file(target: str, filename: str):
    """
    Serve a compiled pipeline video MP4.
    Starlette FileResponse handles Range requests automatically — seek works.
    """
    from pathlib import Path as _P
    from nas_server.video_logger import _LIBRARY

    # Sanitise: only allow filename portion, no directory traversal
    safe_name = _P(filename).name
    path = _LIBRARY / target / "_video" / safe_name
    if not path.exists() or path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(path), media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes"})


@app.get("/video-thumb/{target}/{session_id}")
def serve_video_thumb(target: str, session_id: str):
    """
    Return the last frame JPG from a video session as a thumbnail.
    The last frame shows the final processed result.
    """
    from nas_server.video_logger import _LIBRARY

    session_dir = _LIBRARY / target / "_video" / session_id
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Session not found")
    jpgs = sorted(session_dir.glob("*.jpg"))
    if not jpgs:
        raise HTTPException(status_code=404, detail="No frames found")
    return FileResponse(str(jpgs[-1]), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})

"""
Remote worker service — runs inside WSL2 Ubuntu on the laptop.

Start with:
    WORKER_SETTINGS=~/seestar_database/settings_worker.json \\
    uvicorn nas_server.laptop_worker:app --host 0.0.0.0 --port 8001

Exposes:
    GET  /health          → availability + disk + PI status
    POST /jobs            → accept and run a job (single job at a time)
    GET  /jobs/{job_id}   → current job status

Processing strategy — local copy:
    Source FITS is copied from NAS to local SSD (/tmp/ap_work/ by default)
    before processing begins.  seestar_library_path is temporarily redirected
    to the local path so auto_process writes ALL intermediates (stretch variants,
    GraXpert outputs, PI scratch, etc.) to the local NVMe rather than across
    the SMB mount.  When done, only the run output directory is rsynced back
    to NAS.  Local work dir is cleaned up on completion or error.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Body

log = logging.getLogger("laptop_worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SETTINGS_FILE = os.environ.get(
    "WORKER_SETTINGS",
    str(Path.home() / "seestar_database/settings_worker.json"),
)

def _load_settings() -> dict:
    p = Path(_SETTINGS_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as e:
            log.warning(f"[worker] could not read settings: {e}")
    return {}

_settings = _load_settings()

# Configure Telegram so auto_process can send step/completion messages
# (main.py does this on the VM; laptop_worker.py must do it here instead)
if _settings.get("telegram_token") and _settings.get("telegram_chat_id"):
    from nas_server import telegram as _tg_init
    _tg_init.configure(_settings["telegram_token"], _settings["telegram_chat_id"])

# Override PI binary path from worker settings before any imports touch it
_pi_bin = _settings.get("pi_binary", "")
if _pi_bin:
    os.environ["PI_BIN"] = _pi_bin

# Force xvfb for all PI calls on the laptop (WSL2 has no real GPU)
os.environ["PI_FORCE_XVFB"] = "1"

# Bring the main config into view so auto_process imports work
# (config.py reads settings.json — point it at the worker settings file)
os.environ.setdefault("SEESTAR_SETTINGS", _SETTINGS_FILE)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="SeeStar Laptop Worker", version="1.0")


@app.on_event("startup")
def _init_schema():
    """Keep the worker's DB schema in parity with the VM. The worker uses its
    own app entrypoint (not main.py), so main.py's lifespan init never runs
    here — without this, new tables (e.g. target_crops) are missing locally."""
    try:
        from nas_server import database
        database.init_database()
    except Exception as e:
        log.warning(f"[worker] init_database failed: {e}")


# Single-job state (the worker processes one job at a time)
_job: dict = {}
_job_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Return worker availability and environment status."""
    # NAS mount check
    _nas_root = Path(_settings.get("seestar_library_path", "/mnt/nas_data/SeeStar"))
    _nas_ok   = _nas_root.exists()

    # NAS free space
    try:
        _nas_du    = shutil.disk_usage(str(_nas_root) if _nas_ok else "/")
        _nas_free  = round(_nas_du.free / 1e9, 1)
    except Exception:
        _nas_free  = -1.0

    # Local SSD free space (where processing work lives)
    _local_root = Path(_settings.get("local_workdir", "/tmp/ap_work"))
    _local_check = _local_root if _local_root.exists() else Path("/tmp")
    try:
        _ldu       = shutil.disk_usage(str(_local_check))
        _local_free = round(_ldu.free / 1e9, 1)
    except Exception:
        _local_free = -1.0

    # PI availability
    _pi_path = Path(_settings.get("pi_binary", "/opt/PixInsight/bin/PixInsight"))
    _pi_ok   = _pi_path.exists()

    with _job_lock:
        _busy   = bool(_job.get("running"))
        _job_id = _job.get("id")
        _prog   = _job.get("progress", "")

    return {
        "status":       "busy" if _busy else "idle",
        "job_id":       _job_id,
        "progress":     _prog,
        "nas_mounted":  _nas_ok,
        "nas_free_gb":  _nas_free,
        "disk_free_gb": _local_free,   # local SSD — used by VM dispatch guard
        "local_free_gb": _local_free,
        "pi_available": _pi_ok,
        "worker_name":  _settings.get("worker_name", "laptop"),
    }


# ---------------------------------------------------------------------------
# Job dispatch endpoint
# ---------------------------------------------------------------------------

@app.post("/jobs")
def run_job(body: dict = Body(...)):
    """
    Accept a job spec and start processing in a background thread.

    Expected body keys:
        id          (str)  — job identifier (used for status polling)
        target      (str)  — target name
        workflow    (str)  — workflow name (or "auto")
        source_file (str, optional) — specific stack FITS filename (relative to
                                      _processed/)
        callback_url (str, optional) — VM URL to POST completion to
    """
    with _job_lock:
        if _job.get("running"):
            return {"error": "busy", "job_id": _job.get("id")}

        # Guard: local SSD must have at least 20 GB free for intermediates
        _local_root = Path(_settings.get("local_workdir", "/tmp/ap_work"))
        _local_check = _local_root if _local_root.exists() else Path("/tmp")
        try:
            _ldu = shutil.disk_usage(str(_local_check))
            if _ldu.free / 1e9 < 20:
                return {"error": "low_local_disk",
                        "free_gb": round(_ldu.free / 1e9, 1)}
        except Exception:
            pass

        _job.clear()
        _job.update({
            "id":       str(body.get("id", "")),
            "target":   body.get("target", ""),
            "running":  True,
            "started":  time.time(),
            "progress": "starting",
        })

    threading.Thread(
        target=_execute_job,
        args=(dict(body),),
        daemon=True,
        name=f"worker-{body.get('id', 'job')}",
    ).start()
    return {"queued": _job["id"]}


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    """Return current job status."""
    with _job_lock:
        if _job.get("id") != job_id:
            return {"error": "not_found"}
        snap = dict(_job)
    if snap.get("started"):
        snap["elapsed_s"] = round(time.time() - snap["started"], 1)
    return snap


@app.post("/jobs/{job_id}/abort")
def abort_job(job_id: str):
    """Request cooperative abort of the running job. The pipeline bails at the
    next step boundary; the job then completes normally with aborted=True."""
    with _job_lock:
        cur_id = _job.get("id")
        target = _job.get("target", "")
        running = _job.get("running")
    if not running or cur_id != job_id:
        return {"ok": False, "error": "not_running", "job_id": cur_id}
    try:
        from nas_server.auto_process import request_abort
        request_abort(target)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    log.info(f"[worker] abort requested for job {job_id} ({target})")
    return {"ok": True, "job_id": job_id, "target": target}


@app.post("/admin/pull")
def admin_pull():
    """Git pull + reload settings (only safe when idle). Use to deploy updates without SSH."""
    with _job_lock:
        if _job.get("running"):
            return {"error": "busy — wait for job to finish before pulling"}
    import subprocess, sys, os
    repo = Path(__file__).parent.parent
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "pull", "--ff-only"],
            stderr=subprocess.STDOUT, timeout=60
        ).decode().strip()
        return {"ok": True, "git_output": out,
                "note": "Restart worker process to load new code: kill this process and relaunch"}
    except subprocess.CalledProcessError as e:
        return {"error": e.output.decode().strip()}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _execute_job(job: dict) -> None:
    """Run in a daemon thread. Updates _job dict throughout."""
    target = job.get("target", "")
    cb_url = job.get("callback_url")

    try:
        # Apply PI binary override (env var already set at module load; do it
        # imperatively too in case pixinsight was imported before env was set)
        _pi_bin_path = _settings.get("pi_binary", "")
        if _pi_bin_path:
            try:
                import nas_server.pixinsight as _pi_mod
                _pi_mod.PI_BIN = _pi_bin_path
                _pi_dir = str(Path(_pi_bin_path).parent)
                _pi_lib = str(Path(_pi_bin_path).parent / "lib")
                _pi_mod.PI_ENV_OVERLAY["LD_LIBRARY_PATH"] = _pi_lib
                _pi_mod.PI_ENV_OVERLAY["QT_PLUGIN_PATH"] = str(Path(_pi_lib) / "qt-plugins")
                _pi_mod.PI_ENV_OVERLAY["QT_QPA_PLATFORM_PLUGIN_PATH"] = \
                    str(Path(_pi_lib) / "qt-plugins" / "platforms")
            except Exception as _e:
                log.warning(f"[worker] could not override PI_BIN: {_e}")

        result = _run_with_local_copy(job)

        with _job_lock:
            _job.update({
                "running":   False,
                "done":      True,
                "result":    result,
                "elapsed_s": round(time.time() - _job["started"], 1),
            })
        log.info(f"[worker] completed: {target} ok={result.get('ok')}")

    except Exception as exc:
        log.error(f"[worker] {target} failed: {exc}", exc_info=True)
        with _job_lock:
            _job.update({
                "running":   False,
                "done":      True,
                "error":     str(exc),
                "elapsed_s": round(time.time() - _job.get("started", time.time()), 1),
            })

    finally:
        if cb_url:
            try:
                import requests
                with _job_lock:
                    payload = dict(_job)
                requests.post(cb_url, json=payload, timeout=10)
                log.info(f"[worker] callback posted to {cb_url}")
            except Exception as _e:
                log.warning(f"[worker] callback to {cb_url} failed: {_e}")


# ---------------------------------------------------------------------------
# Local-copy processing
# ---------------------------------------------------------------------------

def _run_with_local_copy(job: dict) -> dict:
    """
    Copy source FITS from NAS to local SSD, run auto_process entirely on the
    local disk, then rsync the run output back to NAS.

    Why: SMB I/O for hundreds of sequential FITS read/write ops (stretch
    variants, GraXpert, PI scratch) is slow.  Local NVMe is 5-10× faster.
    Source FITS are typically 100–500 MB; copy-in + copy-out overhead is
    negligible compared to processing time saved.
    """
    from nas_server import config as _cfg

    target      = job["target"]
    nas_lib     = Path(_settings.get("seestar_library_path", "/mnt/nas_data/SeeStar"))
    local_lib   = Path(_settings.get("local_workdir", "/tmp/ap_work"))
    source_file = job.get("source_file")   # may be None

    nas_proc   = nas_lib   / target / "_processed"
    local_proc = local_lib / target / "_processed"

    # ------------------------------------------------------------------ #
    # Step 1 — copy source FITS to local SSD                              #
    # ------------------------------------------------------------------ #
    _update_progress(f"copying source FITS for {target}")
    local_proc.mkdir(parents=True, exist_ok=True)

    if source_file:
        # Specific file provided by the VM's queue manager
        src = nas_proc / source_file
        if not src.exists():
            raise FileNotFoundError(f"Source FITS not found on NAS: {src}")
        dst = local_proc / source_file
        shutil.copy2(src, dst)
        log.info(f"[worker] copied {source_file} ({src.stat().st_size / 1e6:.0f} MB)")
    else:
        # No specific file — copy all FITS sitting directly in _processed/
        # (source stacks only; ignore runs/ subdirectory to avoid pulling
        # gigabytes of old processing history from NAS)
        if not nas_proc.exists():
            raise FileNotFoundError(f"_processed/ not found for {target} at {nas_proc}")

        copied = []
        for f in sorted(nas_proc.glob("*.fit*"), key=lambda x: x.stat().st_mtime):
            if f.is_file():
                dst = local_proc / f.name
                shutil.copy2(f, dst)
                copied.append(f.name)
                log.info(f"[worker] copied {f.name} ({f.stat().st_size / 1e6:.0f} MB)")
        if not copied:
            raise FileNotFoundError(
                f"No FITS found in {nas_proc} — nothing to process"
            )

    # ------------------------------------------------------------------ #
    # Step 2 — redirect seestar_library_path to local SSD                 #
    # ------------------------------------------------------------------ #
    original_lib      = _cfg.settings.get("seestar_library_path")
    original_gaia_path = _cfg.settings.get("gaia_db_path")
    _cfg.settings["seestar_library_path"] = str(local_lib)
    # Inject GAIA DB path from worker settings so SPCC can find the catalog on the NAS
    _worker_gaia = _settings.get("gaia_db_path")
    if _worker_gaia:
        _cfg.settings["gaia_db_path"] = _worker_gaia
        log.info(f"[worker] gaia_db_path → {_worker_gaia}")
    log.info(f"[worker] seestar_library_path → {local_lib}  (was {original_lib})")

    try:
        # ------------------------------------------------------------------ #
        # Step 3 — run auto_process entirely on local disk                   #
        # ------------------------------------------------------------------ #
        _update_progress(f"running auto_process for {target}")
        log.info(f"[worker] starting auto_process: {target} "
                 f"workflow={job.get('workflow', 'auto')}")

        from nas_server.auto_process import auto_process
        result = auto_process(
            target=target,
            workflow=job.get("workflow", "auto"),
            source_file=source_file,
            extra_params=job.get("extra_params") or None,
        )

        # ------------------------------------------------------------------ #
        # Step 4 — rsync run output from local SSD back to NAS               #
        # ------------------------------------------------------------------ #
        _update_progress(f"syncing results to NAS for {target}")

        local_runs = local_proc / "runs"
        nas_runs   = nas_proc   / "runs"

        if local_runs.exists():
            nas_runs.mkdir(parents=True, exist_ok=True)
            cp = subprocess.run(
                ["rsync", "-a", "--info=stats1",
                 str(local_runs) + "/", str(nas_runs) + "/"],
                capture_output=True, text=True,
            )
            if cp.returncode != 0:
                log.warning(f"[worker] rsync failed (rc={cp.returncode}): "
                            f"{cp.stderr.strip()}")
            else:
                # Log rsync transfer summary (last 3 lines of stderr/stdout)
                _summary = (cp.stdout + cp.stderr).strip().splitlines()
                for _ln in _summary[-3:]:
                    if _ln.strip():
                        log.info(f"[worker] rsync: {_ln.strip()}")
        else:
            log.warning(f"[worker] no runs/ dir found locally after processing")

        # Mirror the auto_final* root files to NAS. auto_process writes these to the
        # _processed root (auto_final.fit / auto_final_preview.jpg + any suffixed
        # branch outputs); on the VM that root IS the NAS, but here it's local SSD,
        # so without this copy the target's folder-root preview stays stale on NAS
        # (the website's target page reads the root, not the run dir).
        copied_finals = 0
        for f in sorted(local_proc.glob("auto_final*")):
            if f.is_file():
                try:
                    shutil.copy2(str(f), str(nas_proc / f.name))
                    copied_finals += 1
                except Exception as _ce:
                    log.warning(f"[worker] failed to copy {f.name} to NAS: {_ce}")
        if copied_finals:
            log.info(f"[worker] synced {copied_finals} auto_final* file(s) to NAS root")

        # Rewrite output_path in result from local → NAS path so the VM's
        # callback handler and DB writes use the correct NAS location
        if result.get("output_path"):
            _p = Path(result["output_path"])
            try:
                result["output_path"] = str(nas_lib / _p.relative_to(local_lib))
            except ValueError:
                pass  # Path wasn't under local_lib — leave it as-is

        return result

    finally:
        # Restore original library path and GAIA path (important if job is retried)
        if original_lib is not None:
            _cfg.settings["seestar_library_path"] = original_lib
        if original_gaia_path is not None:
            _cfg.settings["gaia_db_path"] = original_gaia_path
        elif "gaia_db_path" in _cfg.settings and not original_gaia_path:
            _cfg.settings.pop("gaia_db_path", None)

        # Clean up entire local target work dir
        local_target = local_lib / target
        if local_target.exists():
            try:
                shutil.rmtree(local_target)
                log.info(f"[worker] cleaned up local work dir: {local_target}")
            except Exception as _e:
                log.warning(f"[worker] cleanup failed for {local_target}: {_e}")


# ---------------------------------------------------------------------------
# Idle background scoring loop
# ---------------------------------------------------------------------------

def _idle_loop() -> None:
    """
    Background daemon thread.  When no job is running, fetch unscored light
    frames from the VM, score them locally (NAS is mounted at the same paths),
    and POST the results back.  Yields immediately when a job arrives.

    Throttle: one batch (up to 5 frames) per 30 s idle, matching the VM's own
    idle_worker cadence so we don't flood the analysis pipeline.
    """
    import requests as _req

    vm_url = _settings.get("vm_url", "http://127.0.0.1:8000").rstrip("/")
    tasks_url   = f"{vm_url}/idle/tasks"
    results_url = f"{vm_url}/idle/results"

    log.info("[idle] background scoring loop started")

    while True:
        # Don't compete with an active processing job
        with _job_lock:
            busy = bool(_job.get("running"))

        if busy:
            time.sleep(10)
            continue

        try:
            resp = _req.get(tasks_url, params={"limit": 5}, timeout=5)
            if not resp.ok:
                time.sleep(30)
                continue
            tasks = resp.json().get("tasks", [])
            if not tasks:
                time.sleep(30)
                continue

            # A solve_batch task is handed out alone when no scoring work remains.
            solve_task = next((t for t in tasks if t.get("task_type") == "solve_batch"), None)
            if solve_task:
                paths = solve_task.get("file_paths", [])
                target = solve_task.get("target")
                existing = [p for p in paths if Path(p).exists()]
                solve_results: list[dict] = []
                # Missing files: report failed so the VM stops offering them.
                for p in paths:
                    if not Path(p).exists():
                        solve_results.append({"file_path": p,
                                              "solved_ra": None, "solved_dec": None,
                                              "solved_rot": None, "solved_scale": None,
                                              "solve_status": "failed"})
                if existing:
                    try:
                        from nas_server.sub_solver import solve_batch_collect
                        wcs = solve_batch_collect(existing)
                        for fp, rec in wcs.items():
                            solve_results.append({"file_path": fp, **rec})
                        ok = sum(1 for r in solve_results if r.get("solve_status") == "ok")
                        log.info(f"[idle] solved {ok}/{len(existing)} subs for {target}")
                    except Exception as _se:
                        log.debug(f"[idle] solve_batch failed for {target}: {_se}")
                if solve_results:
                    try:
                        _req.post(results_url, json={"solve_results": solve_results}, timeout=30)
                        log.info(f"[idle] posted {len(solve_results)} solves to VM")
                    except Exception as _pe:
                        log.debug(f"[idle] could not post solves: {_pe}")
                time.sleep(5)
                continue

            from nas_server.image_analyzer import analyze as _analyze
            scored: list[dict] = []
            for task in tasks:
                fpath = task["file_path"]
                p = Path(fpath)
                if not p.exists():
                    # Tell the VM it's missing so it stops offering it
                    scored.append({"file_path": fpath,
                                   "fwhm": None, "eccentricity": None,
                                   "snr": None, "star_count": None})
                    continue
                try:
                    stats    = _analyze(fpath)
                    psf      = stats.get("psf",   {})
                    noise    = stats.get("noise", {})
                    bg       = stats.get("background", {})
                    scored.append({
                        "file_path":        fpath,
                        "fwhm":             psf.get("fwhm_median"),
                        "eccentricity":     psf.get("eccentricity"),
                        "snr":              noise.get("snr"),
                        "star_count":       psf.get("star_count"),
                        "sky_level":        bg.get("sky_mean"),
                        "gradient_severity": bg.get("gradient_severity"),
                    })
                    log.debug(f"[idle] scored {p.name}: "
                              f"fwhm={psf.get('fwhm_median'):.2f} "
                              f"snr={noise.get('snr'):.1f}")
                except Exception as _fe:
                    log.debug(f"[idle] could not score {p.name}: {_fe}")

            if scored:
                try:
                    _req.post(results_url, json={"results": scored}, timeout=10)
                    log.info(f"[idle] posted {len(scored)} frame scores to VM")
                except Exception as _pe:
                    log.debug(f"[idle] could not post results: {_pe}")

        except Exception as _e:
            log.debug(f"[idle] loop error: {_e}")

        time.sleep(30)


# Start idle loop as a daemon thread
threading.Thread(target=_idle_loop, daemon=True, name="idle-scoring").start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_progress(msg: str) -> None:
    with _job_lock:
        _job["progress"] = msg


# ---------------------------------------------------------------------------
# Entry point (python -m nas_server.laptop_worker)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(_settings.get("worker_port", 8001))
    log.info(f"[worker] starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

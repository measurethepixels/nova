"""
Remote worker service — runs inside a disposable RunPod CPU pod.

Start with:
    WORKER_SETTINGS=~/seestar_database/settings_cpu_pod.json \\
    uvicorn nas_server.cpu_pod_worker:app --host 0.0.0.0 --port 8002

Exposes the SAME HTTP contract as nas_server/laptop_worker.py, so
nas_server/worker_client.py's ping()/dispatch()/poll()/abort() (unmodified)
work against either:
    GET  /health          → availability + capability status
    POST /jobs             → accept and run a job (single job at a time)
    GET  /jobs/{job_id}   → current job status
    POST /jobs/{job_id}/abort → cooperative abort

Candidate 2 of the 2026-08-23 RunPod CPU-pod planning session. Deliberately
NOT wired into queue_manager.py's automatic worker rotation
(_find_available_worker() still requires health["nas_mounted"], which this
worker correctly never reports -- it has no NAS mount) -- this is an opt-in
worker a caller dispatches to directly via worker_client.py, not the
default path. See docs/RUNPOD_CPU_WORKER.md.

Processing strategy — shared RunPod workspace, not NAS mount:
    A laptop worker reads source FITS directly off an SMB-mounted NAS and
    rsyncs results back the same way -- neither is available on a RunPod
    CPU pod. Instead, the caller (the VM) stages the source stack into a
    Candidate 1 RemoteWorkspace (nas_server/runpod_workspace.py) before
    dispatch and passes the workspace's run_id + the staged key in the job
    body; this worker rehydrates a RemoteWorkspace pointed at that same
    run_id, downloads the input from the shared RunPod S3-compatible
    volume, runs auto_process() entirely on local pod disk (exactly like
    the laptop worker's local-SSD strategy), then publishes every output
    file back into the SAME workspace instead of rsyncing to NAS. The
    worker never calls workspace.cleanup() -- only the workspace's own
    creator (the VM) does that, once it has pulled what it needs.

No PixInsight assumption beyond capability reporting (non-goal per the
Candidate 2 spec) -- pi_available is checked the same way laptop_worker
checks it and will honestly report False on the base image; PixInsight
licensing for ephemeral cloud instances is a separate, unresolved
question (issue #390 / Candidate 5).

Deliberately does NOT expose an /admin/pull endpoint like laptop_worker --
git-pulling inside a running pod would defeat the deterministic
image_version this worker reports in /health. Deploy an updated image
instead; see docs/RUNPOD_CPU_WORKER.md.

Authentication -- every endpoint requires a shared bearer token
(Authorization: Bearer <worker_auth_token>), fail-closed: an unconfigured
token means every request is rejected, never that auth is skipped. This is
unlike laptop_worker.py, which has no auth at all -- reasonable for a
worker that only ever lives on a trusted LAN/WSL box, but not for a worker
docs/RUNPOD_CPU_WORKER.md explicitly documents deploying with its port
exposed on a disposable cloud pod. Without this, anyone reaching the port
could occupy the single paid worker, abort a job by guessing/observing its
id, or (before this was removed) supply an arbitrary callback_url as an
SSRF primitive -- see the callback_url removal below.

No callback_url support (unlike laptop_worker.py) -- polling via
GET /jobs/{id} already covers the only way anything in this repo currently
consumes this worker (nothing dispatches to it with a callback yet, since
it isn't wired into queue_manager.py's automatic rotation). A
caller-supplied callback URL that this worker's own process then POSTs to
is an SSRF primitive from inside the pod; removing the feature entirely
is a stronger guarantee than trying to validate/allowlist URLs would be.
"""
import hmac
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Body, Header, HTTPException

log = logging.getLogger("cpu_pod_worker")
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
    str(Path.home() / "seestar_database/settings_cpu_pod.json"),
)

_ENV_SETTINGS = {
    "NOVA_WORKER_AUTH_TOKEN": "worker_auth_token",
    "NOVA_RUNPOD_S3_ACCESS_KEY_ID": "runpod_s3_access_key_id",
    "NOVA_RUNPOD_S3_SECRET_ACCESS_KEY": "runpod_s3_secret_access_key",
    "NOVA_RUNPOD_VOLUME_ID": "runpod_rcastro_volume_id",
    "NOVA_RUNPOD_REGION": "runpod_rcastro_region",
    "NOVA_RUNPOD_API_KEY": "runpod_api_key",
    "NOVA_RUNPOD_RCASTRO_ENDPOINT_ID": "runpod_rcastro_endpoint_id",
}


def _load_settings() -> dict:
    p = Path(_SETTINGS_FILE)
    result = {}
    if p.exists():
        try:
            result = json.loads(p.read_text())
        except Exception as e:
            log.warning(f"[cpu_pod_worker] could not read settings: {e}")
    if not isinstance(result, dict):
        result = {}
    for env_name, setting_name in _ENV_SETTINGS.items():
        value = os.environ.get(env_name, "")
        if value:
            result[setting_name] = value
    return result


_settings = _load_settings()


def _configure_application_settings() -> Path:
    """Give shared pipeline modules a valid, pod-local config file.

    A disposable pod normally receives its worker credentials exclusively
    through environment variables, so ``WORKER_SETTINGS`` does not exist.
    Shared modules such as :mod:`nas_server.database` still import the main
    config loader, which deliberately exits when ``SEESTAR_SETTINGS`` is
    missing. Create a minimal non-secret settings overlay for those modules
    rather than requiring a bootstrap/SSH step on every fresh pod.

    An explicitly provisioned ``WORKER_SETTINGS`` file remains authoritative.
    """
    configured = Path(_SETTINGS_FILE)
    if configured.exists():
        os.environ["SEESTAR_SETTINGS"] = str(configured)
        return configured

    state_dir = Path(os.environ.get("NOVA_CPU_WORKER_STATE_DIR", "/tmp/nova_cpu_pod"))
    state_dir.mkdir(parents=True, exist_ok=True)
    app_settings = state_dir / "settings.json"
    payload = {
        "db_path": str(state_dir / "worker.db"),
        "local_workdir": str(state_dir / "work"),
        "worker_name": _settings.get("worker_name", "cpu-pod"),
        "worker_port": int(_settings.get("worker_port", 8002)),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".json", dir=state_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, app_settings)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    os.environ["SEESTAR_SETTINGS"] = str(app_settings)
    return app_settings


_APPLICATION_SETTINGS_FILE = _configure_application_settings()

if _settings.get("telegram_token") and _settings.get("telegram_chat_id"):
    from nas_server import telegram as _tg_init
    _tg_init.configure(_settings["telegram_token"], _settings["telegram_chat_id"])

# ``_configure_application_settings`` above always establishes this before a
# shared pipeline module can import nas_server.config.

# Unconditional, unlike the SEESTAR_SETTINGS redirect above: this worker is
# ALWAYS headless (no crop-review UI/blocking-event registry exist here),
# and setting it doesn't affect any other module's importability the way
# SEESTAR_SETTINGS does -- see target_crop.crop_review_allowed_here(), the
# fail-fast auto_process.py checks immediately before it would otherwise
# open a phantom review with nowhere to run.
os.environ["SEESTAR_HEADLESS_WORKER"] = "1"

# Deterministic image/version identity (Candidate 2 scope requirement) --
# baked at build time via the Dockerfile, not derived from a live git pull.
_IMAGE_VERSION = os.environ.get("CPU_WORKER_IMAGE_VERSION", "unknown")

# ---------------------------------------------------------------------------
# Auth -- shared bearer token, fail-closed
# ---------------------------------------------------------------------------

def _authorized(authorization: str | None) -> bool:
    """Whether `authorization` matches "Bearer <worker_auth_token>". Fails
    closed: an empty/unconfigured token means every request is rejected,
    never that auth is silently skipped -- see the module docstring for why
    that matters specifically for this worker. Uses hmac.compare_digest for
    a constant-time comparison rather than `==`."""
    token = _settings.get("worker_auth_token", "")
    if not token:
        return False
    if authorization is None:
        return False
    return hmac.compare_digest(authorization, f"Bearer {token}")


def _require_auth(authorization: str | None) -> None:
    if not _authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="SeeStar CPU Pod Worker", version="1.0")
JOBS_API_CONTRACT = "cpu-pod-jobs-v1"
DISPATCH_PROBE_BODY = {"dispatch_probe": JOBS_API_CONTRACT}


@app.on_event("startup")
def _init_schema():
    try:
        from nas_server import database
        database.init_database()
    except Exception as e:
        log.warning(f"[cpu_pod_worker] init_database failed: {e}")


# Single-job state (the worker processes one job at a time)
_job: dict = {}
_job_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def _pi_available() -> bool:
    pi_path = Path(_settings.get("pi_binary", "/opt/PixInsight/bin/PixInsight"))
    return pi_path.exists()


def _runpod_volume_configured() -> bool:
    """Whether this worker has enough settings to reach the shared RunPod
    S3-compatible volume -- distinct from "mounted", since the CPU pod
    reaches the volume over the S3 API (like the GPU workers do), not a
    filesystem mount."""
    return bool(
        _settings.get("runpod_s3_access_key_id")
        and _settings.get("runpod_s3_secret_access_key")
        and _settings.get("runpod_rcastro_volume_id")
        and _settings.get("runpod_rcastro_region")
    )


@app.get("/health")
def health(authorization: str | None = Header(default=None)):
    """Return worker availability and environment status. Deliberately
    reports CPU-pod-appropriate capabilities, not laptop-specific ones --
    no nas_mounted key at all (there is no NAS mount here), so
    queue_manager.py's _find_available_worker() (which requires
    health["nas_mounted"]) correctly never selects this worker for the
    automatic rotation. See the module docstring for why that's deliberate.

    Requires auth like every other endpoint here -- an unauthenticated
    /health would still let anyone probe whether a pod is up/busy/idle."""
    _require_auth(authorization)
    local_root = Path(_settings.get("local_workdir", "/tmp/cpu_pod_work"))
    local_check = local_root if local_root.exists() else Path("/tmp")
    try:
        du = shutil.disk_usage(str(local_check))
        disk_free_gb = round(du.free / 1e9, 1)
    except Exception:
        disk_free_gb = -1.0

    with _job_lock:
        busy = bool(_job.get("running"))
        job_id = _job.get("id")
        prog = _job.get("progress", "")

    return {
        "status": "busy" if busy else "idle",
        "job_id": job_id,
        "progress": prog,
        "backend": "runpod-cpu-pod",
        "image_version": _IMAGE_VERSION,
        "disk_free_gb": disk_free_gb,
        "runpod_volume_configured": _runpod_volume_configured(),
        "pi_available": _pi_available(),
        "worker_name": _settings.get("worker_name", "cpu-pod"),
    }


@app.get("/jobs")
def dispatch_ready(authorization: str | None = Header(default=None)):
    """Prove the authenticated job API is routed without creating a job.

    RunPod's public proxy can briefly answer one path while another still
    returns 404. Candidate 6 therefore probes this exact ``/jobs`` path with
    GET, not ``/health`` alone, before it marks a paid Pod READY. The mutating
    dispatch remains POST on the same path. This handler deliberately does not
    accept a body, mutate ``_job``, or touch a workspace.
    """
    _require_auth(authorization)
    return _dispatch_readiness()


def _dispatch_readiness() -> dict:
    """Return one side-effect-free readiness snapshot for either probe method."""
    with _job_lock:
        busy = bool(_job.get("running"))
    volume_configured = _runpod_volume_configured()
    return {
        "ready": not busy and volume_configured,
        "backend": "runpod-cpu-pod",
        "image_version": _IMAGE_VERSION,
        "jobs_api_contract": JOBS_API_CONTRACT,
        "runpod_volume_configured": volume_configured,
    }


# ---------------------------------------------------------------------------
# Job dispatch endpoint
# ---------------------------------------------------------------------------

@app.post("/jobs")
def run_job(body: dict = Body(...), authorization: str | None = Header(default=None)):
    """
    Accept a job spec and start processing in a background thread.

    Expected body keys:
        id            (str)  — job identifier (used for status polling)
        target        (str)  — target name
        workflow      (str)  — workflow name (or "auto")
        workspace_run_id (str) — run_id of a RemoteWorkspace the caller
                                  already staged the source stack into
        input_key     (str)  — the staged input's key inside that workspace
        extra_params  (dict, optional)

    No callback_url support -- see the module docstring's Authentication
    section for why. Poll GET /jobs/{id} instead.
    """
    _require_auth(authorization)
    # Exact-body sentinel: this exercises RunPod's POST routing on the real
    # dispatch path without accepting a job. Requiring exact equality keeps a
    # real job that happens to contain a similarly named field from being
    # mistaken for a probe.
    if body == DISPATCH_PROBE_BODY:
        return _dispatch_readiness()
    with _job_lock:
        if _job.get("running"):
            return {"error": "busy", "job_id": _job.get("id")}

        if not body.get("workspace_run_id") or not body.get("input_key"):
            return {"error": "workspace_run_id and input_key are required"}

        local_root = Path(_settings.get("local_workdir", "/tmp/cpu_pod_work"))
        local_check = local_root if local_root.exists() else Path("/tmp")
        try:
            du = shutil.disk_usage(str(local_check))
            if du.free / 1e9 < 20:
                return {"error": "low_local_disk", "free_gb": round(du.free / 1e9, 1)}
        except Exception:
            pass

        if not _runpod_volume_configured():
            return {"error": "runpod_volume_not_configured"}

        _job.clear()
        _job.update({
            "id": str(body.get("id", "")),
            "target": body.get("target", ""),
            "running": True,
            "started": time.time(),
            "progress": "starting",
        })

    threading.Thread(
        target=_execute_job,
        args=(dict(body),),
        daemon=True,
        name=f"cpu-pod-worker-{body.get('id', 'job')}",
    ).start()
    return {"queued": _job["id"]}


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}")
def job_status(job_id: str, authorization: str | None = Header(default=None)):
    """Return current job status. Same shape as laptop_worker.py's --
    status/job_id/running/done/error/result/elapsed_s -- so
    worker_client.py/queue_manager.py's poll handling needs no branching
    between worker backends."""
    _require_auth(authorization)
    with _job_lock:
        if _job.get("id") != job_id:
            return {"error": "not_found"}
        snap = dict(_job)
    if snap.get("started"):
        snap["elapsed_s"] = round(time.time() - snap["started"], 1)
    return snap


@app.post("/jobs/{job_id}/abort")
def abort_job(job_id: str, authorization: str | None = Header(default=None)):
    """Request cooperative abort of the running job. Identical semantics to
    laptop_worker.py's abort endpoint -- the pipeline bails at the next
    step boundary; the job then completes normally with aborted=True."""
    _require_auth(authorization)
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
    log.info(f"[cpu_pod_worker] abort requested for job {job_id} ({target})")
    return {"ok": True, "job_id": job_id, "target": target}


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _execute_job(job: dict) -> None:
    """Run in a daemon thread. Updates _job dict throughout. No callback
    POST -- see the module docstring's Authentication section for why a
    caller-supplied callback URL was removed rather than kept; callers poll
    GET /jobs/{id} instead."""
    target = job.get("target", "")

    try:
        from nas_server import rcastro_gpu

        rcastro_gpu.reset_gpu_spend_events()
        result = _run_with_workspace(job)
        spend = rcastro_gpu.gpu_spend_events()

        with _job_lock:
            _job.update({
                "running": False,
                "done": True,
                "result": result,
                "runpod_gpu_spend": spend,
                "elapsed_s": round(time.time() - _job["started"], 1),
            })
        log.info(f"[cpu_pod_worker] completed: {target} ok={result.get('ok')}")

    except Exception as exc:
        log.error(f"[cpu_pod_worker] {target} failed: {exc}", exc_info=True)
        try:
            from nas_server import rcastro_gpu

            spend = rcastro_gpu.gpu_spend_events()
        except Exception:
            spend = []
        with _job_lock:
            _job.update({
                "running": False,
                "done": True,
                "error": str(exc),
                "runpod_gpu_spend": spend,
                "elapsed_s": round(time.time() - _job.get("started", time.time()), 1),
            })


# ---------------------------------------------------------------------------
# Workspace-backed processing
# ---------------------------------------------------------------------------

def _run_with_workspace(job: dict) -> dict:
    """
    Download the source stack from the shared RunPod workspace to local pod
    disk, run auto_process() entirely on local disk (same rationale as the
    laptop worker's local-SSD strategy -- fast sequential I/O for hundreds
    of intermediate read/writes), then publish every output file back into
    the SAME workspace instead of rsyncing to NAS.

    Never calls workspace.cleanup() -- only the workspace's own creator
    (the VM, which staged the input before dispatch) does that, once it has
    pulled what it needs. See runpod_workspace.RemoteWorkspace's own
    docstring for why a stage must never delete a shared prefix.
    """
    from nas_server import config as _cfg
    from nas_server.runpod_workspace import WorkspaceRef, create_workspace

    target = job["target"]
    run_id = job["workspace_run_id"]
    input_key = job["input_key"]
    local_lib = Path(_settings.get("local_workdir", "/tmp/cpu_pod_work"))
    local_proc = local_lib / target / "_processed"

    workspace = create_workspace(
        bucket=_settings.get("runpod_rcastro_volume_id"), run_id=run_id,
    )
    input_ref = WorkspaceRef(input_key)
    if not workspace.contains(input_ref):
        # A key claiming to belong to this run_id but living outside its
        # prefix -- a stale ref or a caller mistake. Refuse rather than
        # download and process the wrong workspace's data.
        raise ValueError(
            f"input_key {input_key!r} does not belong to workspace {run_id!r}"
        )

    # ------------------------------------------------------------------ #
    # Step 1 — download the source stack from the shared workspace        #
    # ------------------------------------------------------------------ #
    _update_progress(f"downloading input for {target}")
    local_proc.mkdir(parents=True, exist_ok=True)
    input_filename = input_key.rsplit("/", 1)[-1]
    local_input = local_proc / input_filename
    workspace.download(input_ref, local_input)
    log.info(f"[cpu_pod_worker] downloaded {input_key} "
             f"({local_input.stat().st_size / 1e6:.0f} MB)")

    # ------------------------------------------------------------------ #
    # Step 2 — redirect seestar_library_path to local pod disk            #
    # ------------------------------------------------------------------ #
    original_lib = _cfg.settings.get("seestar_library_path")
    original_gaia_path = _cfg.settings.get("gaia_db_path")
    _cfg.settings["seestar_library_path"] = str(local_lib)
    worker_gaia = _settings.get("gaia_db_path")
    if worker_gaia:
        _cfg.settings["gaia_db_path"] = worker_gaia
    log.info(f"[cpu_pod_worker] seestar_library_path → {local_lib} (was {original_lib})")

    try:
        # ------------------------------------------------------------------ #
        # Step 2b — apply the crop snapshot dispatch() attached, if any        #
        # ------------------------------------------------------------------ #
        # This pod's own local target_crops table is otherwise empty --
        # without this, auto_process() would independently re-decide "no
        # saved crop" and open a phantom interactive review that has
        # nowhere to run, even though the VM already confirmed a saved
        # crop exists before ever dispatching this job. REPLACE semantics
        # (see upsert_target_crop_row()): this snapshot always wins over
        # whatever (if anything) this pod's local DB already had.
        crop_snapshot = job.get("crop_snapshot")
        if crop_snapshot:
            from nas_server.target_crop import upsert_target_crop_row
            upsert_target_crop_row(crop_snapshot)
            log.info(f"[cpu_pod_worker] applied crop snapshot for {target}")

        # ------------------------------------------------------------------ #
        # Step 3 — run auto_process entirely on local pod disk                #
        # ------------------------------------------------------------------ #
        _update_progress(f"running auto_process for {target}")
        log.info(f"[cpu_pod_worker] starting auto_process: {target} "
                 f"workflow={job.get('workflow', 'auto')}")

        from nas_server.auto_process import auto_process
        result = auto_process(
            target=target,
            workflow=job.get("workflow", "auto"),
            source_file=input_filename,
            extra_params=job.get("extra_params") or None,
        )

        # ------------------------------------------------------------------ #
        # Step 4 — publish outputs back into the shared workspace             #
        # ------------------------------------------------------------------ #
        _update_progress(f"publishing results for {target}")

        output_refs: dict[str, str] = {}
        job_id = job.get("id", "job")

        local_runs = local_proc / "runs"
        if local_runs.exists():
            for f in sorted(local_runs.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(local_runs)
                    # stage_input() appends local_path.suffix to the name
                    # itself (see its own docstring/tests) -- strip the
                    # suffix here via with_suffix("") so it isn't doubled
                    # into "...output.fit.fit".
                    flat = str(rel.with_suffix("")).replace("/", "_")
                    stage_name = f"{job_id}_runs_{flat}"
                    # stage_input() is a generic "upload this local file into
                    # the workspace under a reserved name" primitive -- Candidate
                    # 1 built it for staging INPUT, but nothing about it is
                    # input-specific; reusing it here avoids adding a
                    # near-duplicate output-publishing method to
                    # RemoteWorkspace for what is the same operation.
                    ref = workspace.stage_input(f, name=stage_name)
                    output_refs[str(rel)] = ref.key
        else:
            log.warning(f"[cpu_pod_worker] no runs/ dir found locally after processing")

        for f in sorted(local_proc.glob("auto_final*")):
            if f.is_file():
                stage_name = f"{job_id}_final_{f.stem}"  # same suffix-doubling avoidance
                ref = workspace.stage_input(f, name=stage_name)
                output_refs[f.name] = ref.key

        if output_refs:
            log.info(f"[cpu_pod_worker] published {len(output_refs)} output file(s) "
                     f"to workspace {run_id}")

        result["workspace_run_id"] = run_id
        result["output_workspace_refs"] = output_refs
        # output_path here is still a LOCAL pod path -- unlike the laptop
        # worker (whose local path is meaningless off-machine but whose NAS
        # path is real once rsynced), there is no NAS path for the caller to
        # use yet. output_workspace_refs is the real handoff; leave
        # output_path as informational only.
        return result

    finally:
        if original_lib is not None:
            _cfg.settings["seestar_library_path"] = original_lib
        if original_gaia_path is not None:
            _cfg.settings["gaia_db_path"] = original_gaia_path
        elif "gaia_db_path" in _cfg.settings and not original_gaia_path:
            _cfg.settings.pop("gaia_db_path", None)

        local_target = local_lib / target
        if local_target.exists():
            try:
                shutil.rmtree(local_target)
                log.info(f"[cpu_pod_worker] cleaned up local work dir: {local_target}")
            except Exception as _e:
                log.warning(f"[cpu_pod_worker] cleanup failed for {local_target}: {_e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_progress(msg: str) -> None:
    with _job_lock:
        _job["progress"] = msg


# ---------------------------------------------------------------------------
# Entry point (python -m nas_server.cpu_pod_worker)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(_settings.get("worker_port", 8002))
    log.info(f"[cpu_pod_worker] starting on port {port} (image_version={_IMAGE_VERSION})")
    uvicorn.run(app, host="0.0.0.0", port=port)

"""
HTTP client for dispatching jobs to remote worker nodes (e.g. laptop).

Used by queue_manager.py on the VM side.
All functions are fail-safe: return None on any network/HTTP error.

Crop review is a VM-only routing invariant, enforced here rather than in
each worker (issue #397): a workflow that hasn't cleared the crop/review
checkpoint may never be dispatched remotely, full stop -- only the
post-crop remainder is eligible for a worker. dispatch() is the one
choke point every remote-dispatch caller already goes through (this
module's queue_manager.py usage, scripts/benchmark_runpod_cpu_pod.py, and
any future segment router), so the check lives here once instead of
being duplicated into nas_server/laptop_worker.py and
nas_server/cpu_pod_worker.py, which stay entirely crop-oblivious --
they simply never receive work that hasn't cleared the checkpoint.

Telemetry is recorded the same way, for the same reason: dispatch()/poll()
log every dispatched operation to nas_server.database's telemetry_events
table automatically, regardless of caller -- so a raw benchmark-script
dispatch shows up in the VM's own "what's running right now" view exactly
like a queue-originated one, and real per-operation timing data
accumulates for Candidate 4's routing-policy activation without every
caller needing its own bookkeeping. Telemetry recording is best-effort
and must never break a dispatch/poll call on its own account -- see
_record_dispatch_start()/_record_dispatch_done()'s own docstrings for why
they catch SystemExit too, not just Exception.
"""
import logging

import requests as _req

log = logging.getLogger(__name__)

_PING_TIMEOUT    = 3.0   # seconds
_DISPATCH_TIMEOUT = 10.0
_POLL_TIMEOUT    = 5.0
_DISPATCH_PROBE_BODY = {"dispatch_probe": "cpu-pod-jobs-v1"}


def _record_dispatch_start(job_id: str, target: str, backend: str, job: dict) -> None:
    """Best-effort telemetry write -- never allowed to break a real
    dispatch. Catches SystemExit too, not just Exception: nas_server.database
    needs nas_server.config's settings to already be validly loaded (its
    module-level load calls sys.exit(1) if the configured settings file is
    missing), which is a real, known failure mode for this module
    specifically -- worker_client.py otherwise has zero dependency on
    config/database (pure requests), so a telemetry-recording attempt must
    not turn "settings not loaded yet" into a dispatch failure. A bare
    `except Exception` would let that SystemExit propagate uncaught."""
    try:
        from nas_server.database import record_telemetry_start  # noqa: PLC0415
        record_telemetry_start(job_id, target, backend,
                               extra={"workflow": job.get("workflow")} if job.get("workflow") else None)
    except (Exception, SystemExit) as exc:
        log.debug(f"[worker_client] telemetry start recording failed: {exc}")


def _record_dispatch_done(job_id: str, backend: str, status_body: dict) -> None:
    """Best-effort telemetry write, called the first time poll() observes
    a job done. Idempotent on the DB side (record_telemetry_complete()
    only updates a still-'running' row), so calling this on every
    subsequent poll of an already-completed job is harmless -- this
    function itself doesn't need to track "have I already recorded this."
    Same SystemExit-catching rationale as _record_dispatch_start().

    `backend` is required and passed through to record_telemetry_complete()
    -- job_id alone isn't a unique identity across backends (see that
    function's own docstring): without it, completing one worker's job_id
    could silently mark a DIFFERENT worker's still-running same-id job
    done too."""
    try:
        from nas_server.database import record_telemetry_complete  # noqa: PLC0415
        result = status_body.get("result") or {}
        status = "error" if (status_body.get("error") or not result.get("ok", True)) else "ok"
        record_telemetry_complete(
            job_id, backend, status,
            elapsed_s=status_body.get("elapsed_s"),
            error=status_body.get("error") or result.get("error"),
        )
    except (Exception, SystemExit) as exc:
        log.debug(f"[worker_client] telemetry completion recording failed: {exc}")


def _auth_headers(auth_token: str | None) -> dict | None:
    """Bearer-token header, or None. Optional and defaults to None so
    callers targeting laptop_worker.py (no auth) are unaffected -- only
    nas_server/cpu_pod_worker.py currently requires this, fail-closed on
    its side if omitted."""
    return {"Authorization": f"Bearer {auth_token}"} if auth_token else None


def ping(url: str, timeout: float = _PING_TIMEOUT, auth_token: str | None = None) -> dict | None:
    """Return health dict from the worker, or None if unreachable."""
    try:
        r = _req.get(f"{url}/health", timeout=timeout, headers=_auth_headers(auth_token))
        if r.ok:
            return r.json()
    except Exception as exc:
        log.debug(f"[worker_client] ping {url} failed: {exc}")
    return None


def dispatch_ready(url: str, timeout: float = _PING_TIMEOUT,
                   auth_token: str | None = None) -> dict | None:
    """Return readiness only after GET and exact-route POST both succeed.

    Kept separate from :func:`ping`: ordinary laptop workers do not expose
    this Candidate 6 endpoint, and a successful generic health response must
    not be mistaken for proof that RunPod's proxy routes ``POST /jobs``. The
    reserved POST body is handled without job mutation by cpu_pod_worker.
    """
    try:
        headers = _auth_headers(auth_token)
        get_response = _req.get(f"{url}/jobs", timeout=timeout, headers=headers)
        if not get_response.ok:
            return None
        get_contract = get_response.json()
        post_response = _req.post(
            f"{url}/jobs",
            json=_DISPATCH_PROBE_BODY,
            timeout=timeout,
            headers=headers,
        )
        if not post_response.ok:
            return None
        post_contract = post_response.json()
        identity_fields = ("backend", "image_version", "jobs_api_contract")
        if any(
            get_contract.get(key) != post_contract.get(key)
            for key in identity_fields
        ):
            return None
        return post_contract
    except Exception as exc:
        log.debug(f"[worker_client] readiness probe {url} failed: {exc}")
    return None


def dispatch(url: str, job: dict, callback_url: str | None = None,
             auth_token: str | None = None, backend: str | None = None) -> str | None:
    """
    POST a job spec to the remote worker.
    Returns the remote job_id string on success, None on failure.

    Refuses (returns None, same fail-safe contract as any other rejection
    here) if `job`'s target hasn't cleared the crop/review checkpoint --
    see the module docstring. Lazy import: target_crop.py needs numpy just
    to load, and this module otherwise doesn't, so importing it eagerly
    would make every caller of this file pay that cost even when never
    dispatching a crop-relevant job -- including an experiment_mode/
    dry_run job, which never reviews regardless of the DB, so that path
    stays numpy-free too (checked here before the import, same reasoning
    as queue_manager.py's own _needs_crop_review_on_vm()).

    `backend` is a free-form label for telemetry (e.g. "laptop",
    "runpod-cpu-pod") -- defaults to `url` when the caller doesn't have a
    friendlier name handy, so telemetry is never silently skipped for
    lack of one.

    Also attaches a "crop_snapshot" of the target's saved crop row (a VM
    DB read, taken at this exact dispatch moment) to the payload whenever
    the job is crop-relevant -- a worker's own local target_crops table is
    otherwise empty or stale, which previously let auto_process() running
    on the worker independently re-decide "no saved crop, open a review"
    even after this function already confirmed a saved crop exists and
    let the job through. See target_crop.upsert_target_crop_row()/
    crop_review_allowed_here() for the worker-side half of this fix."""
    target = job.get("target", "")
    backend = backend or url
    crop_snapshot = None
    if not (job.get("experiment_mode") or job.get("dry_run")):
        from nas_server.target_crop import get_target_crop, needs_crop_review  # noqa: PLC0415
        if needs_crop_review(target, extra_params=job.get("extra_params")):
            log.warning(f"[worker_client] refusing to dispatch {target!r} to {url}: "
                        "needs crop review, which is VM-only (issue #397)")
            return None
        try:
            # Best-effort: a failure here must not block a dispatch the
            # crop-review gate already confirmed is safe. Worst case with
            # no snapshot, the worker's own crop_review_allowed_here()
            # fail-fast still turns a missing local crop into a clean
            # error instead of a hang -- degraded, not unsafe. Catches
            # SystemExit too, same reasoning as _record_dispatch_start()'s
            # own docstring: nas_server.database needs nas_server.config's
            # settings already validly loaded, a real known failure mode
            # here specifically.
            crop_snapshot = get_target_crop(target)
        except (Exception, SystemExit) as exc:
            log.warning(f"[worker_client] could not fetch crop snapshot for "
                        f"{target!r}: {exc}")
    try:
        payload = {**job}
        if crop_snapshot is not None:
            payload["crop_snapshot"] = crop_snapshot
        if callback_url:
            payload["callback_url"] = callback_url
        r = _req.post(f"{url}/jobs", json=payload, timeout=_DISPATCH_TIMEOUT,
                      headers=_auth_headers(auth_token))
        if r.ok:
            data = r.json()
            if data.get("error"):
                log.warning(f"[worker_client] dispatch rejected by {url}: {data['error']}")
                return None
            job_id = data.get("queued") or data.get("job_id")
            if job_id:
                job_id = str(job_id)
                _record_dispatch_start(job_id, target, backend, job)
                return job_id
            log.warning(f"[worker_client] dispatch to {url} OK but no job_id: {data}")
        else:
            log.warning(f"[worker_client] dispatch to {url} HTTP {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        log.warning(f"[worker_client] dispatch to {url} failed: {exc}")
    return None


def abort(url: str, job_id: str, timeout: float = _DISPATCH_TIMEOUT,
         auth_token: str | None = None) -> dict | None:
    """Request cooperative abort of a running remote job. Returns response dict or None."""
    try:
        r = _req.post(f"{url}/jobs/{job_id}/abort", timeout=timeout,
                      headers=_auth_headers(auth_token))
        if r.ok:
            return r.json()
        log.warning(f"[worker_client] abort {url}/jobs/{job_id} → HTTP {r.status_code}")
    except Exception as exc:
        log.warning(f"[worker_client] abort {url} failed: {exc}")
    return None


def poll(url: str, job_id: str, timeout: float = _POLL_TIMEOUT,
        auth_token: str | None = None, backend: str | None = None) -> dict | None:
    """
    Poll job status from the worker.
    Returns status dict on success, None if unreachable.

    Records telemetry completion the first time `done` is observed true --
    see _record_dispatch_done()'s own docstring for why this is safe to
    call on every subsequent poll of an already-completed job, and for why
    `backend` (defaults to `url`, same convention as dispatch()) matters:
    job_id alone isn't unique across backends.
    """
    backend = backend or url
    try:
        r = _req.get(f"{url}/jobs/{job_id}", timeout=timeout, headers=_auth_headers(auth_token))
        if r.ok:
            data = r.json()
            if data.get("done"):
                _record_dispatch_done(job_id, backend, data)
            return data
        log.debug(f"[worker_client] poll {url}/jobs/{job_id} → HTTP {r.status_code}")
    except Exception as exc:
        log.debug(f"[worker_client] poll {url} failed: {exc}")
    return None

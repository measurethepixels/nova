"""
HTTP client for dispatching jobs to remote worker nodes (e.g. laptop).

Used by queue_manager.py on the VM side.
All functions are fail-safe: return None on any network/HTTP error.
"""
import logging

import requests as _req

log = logging.getLogger(__name__)

_PING_TIMEOUT    = 3.0   # seconds
_DISPATCH_TIMEOUT = 10.0
_POLL_TIMEOUT    = 5.0


def ping(url: str, timeout: float = _PING_TIMEOUT) -> dict | None:
    """Return health dict from the worker, or None if unreachable."""
    try:
        r = _req.get(f"{url}/health", timeout=timeout)
        if r.ok:
            return r.json()
    except Exception as exc:
        log.debug(f"[worker_client] ping {url} failed: {exc}")
    return None


def dispatch(url: str, job: dict,
             callback_url: str | None = None) -> str | None:
    """
    POST a job spec to the remote worker.
    Returns the remote job_id string on success, None on failure.
    """
    try:
        payload = {**job}
        if callback_url:
            payload["callback_url"] = callback_url
        r = _req.post(f"{url}/jobs", json=payload, timeout=_DISPATCH_TIMEOUT)
        if r.ok:
            data = r.json()
            if data.get("error"):
                log.warning(f"[worker_client] dispatch rejected by {url}: {data['error']}")
                return None
            job_id = data.get("queued") or data.get("job_id")
            if job_id:
                return str(job_id)
            log.warning(f"[worker_client] dispatch to {url} OK but no job_id: {data}")
        else:
            log.warning(f"[worker_client] dispatch to {url} HTTP {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        log.warning(f"[worker_client] dispatch to {url} failed: {exc}")
    return None


def abort(url: str, job_id: str, timeout: float = _DISPATCH_TIMEOUT) -> dict | None:
    """Request cooperative abort of a running remote job. Returns response dict or None."""
    try:
        r = _req.post(f"{url}/jobs/{job_id}/abort", timeout=timeout)
        if r.ok:
            return r.json()
        log.warning(f"[worker_client] abort {url}/jobs/{job_id} → HTTP {r.status_code}")
    except Exception as exc:
        log.warning(f"[worker_client] abort {url} failed: {exc}")
    return None


def poll(url: str, job_id: str, timeout: float = _POLL_TIMEOUT) -> dict | None:
    """
    Poll job status from the worker.
    Returns status dict on success, None if unreachable.
    """
    try:
        r = _req.get(f"{url}/jobs/{job_id}", timeout=timeout)
        if r.ok:
            return r.json()
        log.debug(f"[worker_client] poll {url}/jobs/{job_id} → HTTP {r.status_code}")
    except Exception as exc:
        log.debug(f"[worker_client] poll {url} failed: {exc}")
    return None

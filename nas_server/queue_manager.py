"""
Processing queue — runs autoprocess jobs one at a time in submission order.
Queue is persisted to the DB so it survives service restarts.

Usage:
    from nas_server.queue_manager import add_job, get_queue, remove_job, clear_queue
    start_worker()  # called once at server startup
"""
import logging
import shutil
import threading
import time
from pathlib import Path

import subprocess

log = logging.getLogger(__name__)

_queue: list[dict] = []
_queue_lock = threading.Lock()
_worker_started = False
_paused: bool = False
_restart_pending: bool = False
_parked_for_review: set[str] = set()  # targets blocked indefinitely waiting for manual review
_park_lock = threading.Lock()
_current_job_target: str | None = None   # target the queue worker is currently waiting on
_current_park_event: threading.Event | None = None  # per-job event for that wait
_remote_inflight: dict[str, dict] = {}   # target → {worker_url, worker_name, remote_id}
_remote_inflight_lock = threading.Lock()
_remote_done: dict[str, dict] = {}       # str(db_id) → {target, error, ts}; set by completion callback
_remote_done_lock = threading.Lock()


def mark_remote_done(job_id, target: str = "", error: str | None = None) -> None:
    """Record that a remote worker reported a dispatched job finished.

    Called by the /jobs/<db_id>/complete callback. The dispatch monitor consults
    this so a job the worker has confirmed finished is resolved as done and is
    NEVER re-queued — even if polling later can't find it (worker moved on).
    """
    with _remote_done_lock:
        _remote_done[str(job_id)] = {"target": target, "error": error, "ts": time.time()}
    log.info(f"[queue] remote done recorded: job={job_id} target='{target}'"
             + (f" error={error}" if error else ""))


def park_for_review(target: str) -> None:
    """Park this target (releases queue worker if it's waiting on this exact job)."""
    with _park_lock:
        _parked_for_review.add(target)
        if target == _current_job_target and _current_park_event is not None:
            _current_park_event.set()


def unpark_from_review(target: str) -> None:
    """Called when a parked pipeline thread resumes after user submits a review."""
    _parked_for_review.discard(target)


# --- Pause / Resume / Graceful Restart ---

def pause_queue(reason: str = "user request") -> None:
    global _paused
    _paused = True
    log.info(f"[queue] paused ({reason})")


def resume_queue() -> None:
    global _paused, _restart_pending
    _paused = False
    _restart_pending = False
    log.info("[queue] resumed")


def is_paused() -> bool:
    return _paused


def is_restart_pending() -> bool:
    return _restart_pending


def request_graceful_restart() -> dict:
    """Pause queue, wait for active job to finish, then restart the service."""
    global _restart_pending
    if _restart_pending:
        return {"status": "already_pending"}
    pause_queue("graceful restart requested")
    _restart_pending = True
    active = _any_active()
    threading.Thread(target=_do_graceful_restart, daemon=True,
                     name="graceful-restart").start()
    return {"status": "waiting" if active else "immediate"}


def _do_graceful_restart() -> None:
    global _restart_pending
    from nas_server import telegram as _tg
    deadline = time.time() + 4 * 3600
    while _restart_pending and (_any_active() or time.time() < deadline):
        if not _any_active():
            break
        time.sleep(10)
    if not _restart_pending:
        log.info("[queue] graceful restart cancelled (queue resumed)")
        return
    log.info("[queue] graceful restart: no active jobs — restarting service now")
    try:
        _tg.send("🔄 <b>Service restarting</b> (graceful restart requested from web UI)")
    except Exception:
        pass
    time.sleep(1)
    subprocess.run(["sudo", "systemctl", "restart", "seestar"], capture_output=True)


# --- DB helpers (best-effort; in-memory queue still works if DB is unavailable) ---

def _db_insert(item: dict) -> int:
    try:
        from nas_server.database import queue_insert
        return queue_insert(item)
    except Exception as e:
        log.warning(f"[queue] DB insert failed (in-memory only): {e}")
        return -1


def _db_delete(row_id: int):
    if row_id < 0:
        return
    try:
        from nas_server.database import queue_delete
        queue_delete(row_id)
    except Exception as e:
        log.warning(f"[queue] DB delete failed: {e}")


def _db_clear():
    try:
        from nas_server.database import queue_clear_all
        queue_clear_all()
    except Exception as e:
        log.warning(f"[queue] DB clear failed: {e}")


# --- Public API ---

def add_job(target: str, workflow: str = "seestar_broadband",
            experiment_mode: bool = False, dry_run: bool = False,
            source_file: str | None = None,
            manual_review: bool = False,
            extra_params: dict | None = None) -> dict:
    item = {
        "job_type": "process",
        "target": target,
        "workflow": workflow,
        "experiment_mode": experiment_mode,
        "dry_run": dry_run,
        "source_file": source_file,
        "manual_review": manual_review,
        "extra_params": extra_params or {},
    }
    item["_db_id"] = _db_insert(item)
    with _queue_lock:
        _queue.append(item)
        pos = len(_queue)
    log.info(f"[queue] added '{target}' (workflow={workflow}) — position {pos}")
    return {"position": pos, **_public(item)}


def add_stack_job(target: str, engine: str = "siril", cull: bool = True,
                  bottom_pct: float = 0.10, min_stars: int = 20,
                  fast: bool = False, framing: str = "min", hero: bool = False,
                  drizzle: bool = False, exptime: int | None = None,
                  eq_only: bool = True,
                  ecc_threshold: float = 0.6,
                  sky_level_factor: float = 3.0,
                  gradient_threshold: float = 0.5,
                  post_autoprocess_workflow: str | None = None,
                  post_autoprocess_experiment: bool = False) -> dict:
    item = {
        "job_type": "stack",
        "target": target,
        "engine": engine,
        "cull": cull,
        "bottom_pct": bottom_pct,
        "min_stars": min_stars,
        "fast": fast,
        "framing": "max" if framing == "max" else "min",
        "hero": hero,
        "drizzle": drizzle,
        "exptime": exptime,
        "eq_only": eq_only,
        "ecc_threshold": ecc_threshold,
        "sky_level_factor": sky_level_factor,
        "gradient_threshold": gradient_threshold,
        # workflow/experiment_mode reused to carry post-stack autoprocess config
        "workflow": post_autoprocess_workflow,
        "experiment_mode": post_autoprocess_experiment if post_autoprocess_workflow else False,
    }
    item["_db_id"] = _db_insert(item)
    with _queue_lock:
        _queue.append(item)
        pos = len(_queue)
    suffix = f" +autoprocess({post_autoprocess_workflow})" if post_autoprocess_workflow else ""
    log.info(f"[queue] added stack '{target}' (engine={engine}){suffix} — position {pos}")
    return {"position": pos, **_public(item)}


def get_queue() -> list[dict]:
    with _queue_lock:
        return [{"position": i + 1, **_public(item)} for i, item in enumerate(_queue)]


def remove_job(index: int) -> bool:
    """Remove by 1-based position. Returns True if removed."""
    removed = None
    with _queue_lock:
        idx = index - 1
        if 0 <= idx < len(_queue):
            removed = _queue.pop(idx)
            log.info(f"[queue] removed '{removed['target']}' from position {index}")
    if removed is not None:
        _db_delete(removed.get("_db_id", -1))
        return True
    return False


def clear_queue() -> int:
    with _queue_lock:
        count = len(_queue)
        _queue.clear()
    _db_clear()
    log.info(f"[queue] cleared {count} pending jobs")
    return count


def _pop_next() -> dict | None:
    with _queue_lock:
        item = _queue.pop(0) if _queue else None
    if item is not None:
        _db_delete(item.get("_db_id", -1))
    return item


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _any_active() -> bool:
    """True if any autoprocess OR stack job is currently running (excludes review-parked targets)."""
    with _remote_inflight_lock:
        if _remote_inflight:
            return True
    try:
        from nas_server.auto_process import get_all_autoprocess_statuses
        if any(
            s.get("phase") not in ("done", "error", "aborted", None)
            and s.get("target") not in _parked_for_review
            for s in get_all_autoprocess_statuses()
        ):
            return True
    except Exception:
        pass
    try:
        from nas_server.stacker import get_all_stack_statuses
        if any(s.get("running") for s in get_all_stack_statuses()):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Remote worker helpers
# ---------------------------------------------------------------------------

def _resolve_workflow(target: str, workflow: str) -> str:
    """Resolve 'auto' workflow to a concrete name. Shared by _run_job and proactive dispatch."""
    if workflow != "auto":
        return workflow
    try:
        from nas_server.auto_process import (_object_type_from_name,
                                             _object_type_from_db, _load_folio)
        folio = _load_folio(target)
        # Priority: hand-curated folio > DB catalog type > name heuristic. The DB type
        # is placed above the name heuristic because the latter collides on substrings
        # (e.g. 'm 10' ⊂ 'm 109' mis-routed the barred spiral M 109 to globular).
        obj_type = ((folio or {}).get("type") or (folio or {}).get("object_type")
                    or _object_type_from_db(target)
                    or _object_type_from_name(target))
        if obj_type in ("globular_cluster", "open_cluster"):
            return "seestar_globular"
        if obj_type == "galaxy":
            return "seestar_galaxy"
        if obj_type in ("emission_nebula", "reflection_nebula", "planetary_nebula"):
            return "seestar_nebula"
        return "seestar_broadband"
    except Exception:
        return "seestar_broadband"


def _find_available_worker() -> dict | None:
    """Return first healthy idle remote worker for process jobs, or None."""
    try:
        from nas_server.config import settings
        from nas_server.worker_client import ping as _wping
    except Exception:
        return None
    try:
        from nas_server.database import get_worker_enabled as _wenabled
    except Exception:
        _wenabled = None
    for w in settings.get("remote_workers", []):
        if not w.get("enabled", True):
            continue
        if _wenabled is not None and not _wenabled(w.get("name", "")):
            continue
        if "process" not in w.get("dispatch_types", ["process"]):
            continue
        health = _wping(w["url"])
        if health and health.get("status") == "idle" and health.get("nas_mounted"):
            return w
    return None


def _needs_crop_review_on_vm(item: dict) -> bool:
    """True if this job would open a manual crop review, which must run on the VM.

    The review fires on a target's first process (no saved crop) or a forced
    re-crop. Experiment and dry-run jobs skip the crop branch entirely in
    auto_process, so they never review and can dispatch remotely.
    """
    if item.get("experiment_mode") or item.get("dry_run"):
        return False
    if (item.get("extra_params") or {}).get("re_crop"):
        return True
    try:
        from nas_server.target_crop import get_target_crop
        return get_target_crop(item.get("target", "")) is None
    except Exception:
        return False


def _proactive_remote_dispatch(current_target: str | None = None) -> None:
    """
    If a remote worker is idle, scan ahead in the queue for a process job that can
    run immediately — i.e. its target is not currently being stacked and has no
    pending stack job ahead of it in the queue.  Plucks that job out and dispatches it.
    """
    worker = _find_available_worker()
    if not worker:
        return

    # Targets that cannot be dispatched right now
    blocked: set[str] = set()
    if current_target:
        blocked.add(current_target)
    # Never dispatch a target already running on a remote worker — prevents
    # duplicate concurrent dispatches of the same target (the churn that
    # produced multiple in-flight copies of one target).
    with _remote_inflight_lock:
        blocked |= set(_remote_inflight)

    with _queue_lock:
        candidate_idx: int | None = None
        for i, item in enumerate(_queue):
            if item.get("job_type") != "process":
                # Stack (or other) jobs ahead of a process job block that target
                blocked.add(item["target"])
                continue
            if item.get("dry_run") or item.get("manual_review"):
                continue
            if item["target"] in blocked:
                continue
            if (item.get("extra_params") or {}).get("branch"):
                # Branch jobs resume from a prior run's post-stretch snapshot that
                # lives in runs/<old>/ — the worker's local-copy staging only pulls
                # FITS sitting directly in _processed/, so the snapshot won't be
                # present remotely. Keep branch jobs on the VM.
                continue
            if _needs_crop_review_on_vm(item):
                # First-time (or forced) crop review can't surface from a remote
                # worker: the review DB record, blocking event, candidate previews,
                # and web UI all live on the VM, not the worker. Keep the review run
                # on the VM. Once a crop is saved it's reused without review, so
                # later runs for this target dispatch freely.
                continue
            candidate_idx = i
            break

        if candidate_idx is None:
            return

        candidate = _queue.pop(candidate_idx)

    # Claim the job: delete its persistent row now (mirrors _pop_next). On a
    # successful remote run the row stays gone; if dispatch fails, the requeue
    # path re-inserts a fresh row. Without this, a proactively-dispatched job that
    # completes remotely leaves an orphaned queue_jobs row that reloads and re-runs
    # already-finished work on the next restart.
    _db_delete(candidate.get("_db_id", -1))

    target = candidate["target"]
    workflow = _resolve_workflow(target, candidate.get("workflow", "auto"))
    candidate_with_wf = {**candidate, "workflow": workflow}

    with _remote_inflight_lock:
        if target in _remote_inflight:
            # Lost the race with another dispatcher — this candidate is a duplicate
            # of a target already in flight. Drop it instead of double-dispatching
            # (DB row already removed above).
            log.info(f"[queue] proactive dispatch skipped duplicate '{target}' "
                     f"— already in flight")
            return
        _remote_inflight[target] = {
            "worker_url": worker["url"],
            "worker_name": worker.get("name", worker["url"]),
            "remote_id": None,
        }
    threading.Thread(
        target=_dispatch_and_monitor,
        args=(candidate_with_wf, worker),
        daemon=True,
        name=f"remote-{target}",
    ).start()
    log.info(
        f"[queue] proactive dispatch: '{target}' → {worker.get('name', worker['url'])} "
        f"(looked ahead, workflow={workflow})"
    )


def _requeue_at_front(item: dict) -> None:
    """Re-insert item at front of queue (used when remote dispatch fails).

    Dedups: never insert a second copy of a target that is already queued — this
    was the churn that accumulated multiple identical rows for one target.
    """
    target = item.get("target")
    job_type = item.get("job_type", "process")
    with _queue_lock:
        if any(q.get("target") == target and q.get("job_type", "process") == job_type
               for q in _queue):
            log.info(f"[queue] re-queue skipped for '{target}' — already in queue")
            return
    new_item = {k: v for k, v in item.items() if not k.startswith("_")}
    new_item["_db_id"] = _db_insert(new_item)
    with _queue_lock:
        _queue.insert(0, new_item)
    log.info(f"[queue] re-queued '{new_item['target']}' at front for local execution")


def _target_has_recent_output(target: str, max_age_hours: float = 4,
                              since_ts: float | None = None) -> bool:
    """True if target has an auto_final.fit on NAS written recently.

    since_ts (dispatch time) is the reliable test: the final must be NEWER than
    when THIS job was dispatched. Without it the check matched a PREVIOUS run's
    auto_final.fit (any within max_age_hours) and marked a fresh dispatch "done"
    against stale output — a same-name collision that silently ate a queued run
    (IC 1805 / NGC 7000 sessions, 2026-07). max_age_hours stays as the fallback
    for callers that don't know the dispatch time (clear_stuck_inflight).
    """
    try:
        from nas_server.config import settings
        lib = settings.get("library_path", "/mnt/nas_data/SeeStar")
        final = Path(lib) / target / "_processed" / "auto_final.fit"
        if not final.exists():
            return False
        mtime = final.stat().st_mtime
        if since_ts is not None:
            return mtime >= since_ts
        return (time.time() - mtime) / 3600 <= max_age_hours
    except Exception:
        return False


def _target_has_active_run(target: str, max_idle_s: float = 210) -> bool:
    """True if an auto_process run dir for target was written within max_idle_s.

    A run in progress continuously writes previews + run.log into its run dir, so a
    fresh mtime means the job is still alive — distinguishes 'worker lost the job'
    from 'job genuinely died' before re-queuing.
    """
    try:
        from nas_server.config import settings
        lib = settings.get("seestar_library_path") or settings.get(
            "library_path", "/mnt/nas_data/SeeStar")
        runs = Path(lib) / target / "_processed" / "runs"
        if not runs.is_dir():
            return False
        dirs = [d for d in runs.iterdir() if d.is_dir()]
        if not dirs:
            return False
        newest = max(dirs, key=lambda d: d.stat().st_mtime)
        mtimes = [newest.stat().st_mtime]
        for f in newest.iterdir():
            try:
                mtimes.append(f.stat().st_mtime)
            except Exception:
                pass
        return (time.time() - max(mtimes)) <= max_idle_s
    except Exception:
        return False


def is_remote_inflight(target: str) -> bool:
    """True if the target is currently dispatched to a remote worker."""
    with _remote_inflight_lock:
        return target in _remote_inflight


def abort_remote(target: str) -> dict:
    """Forward a cooperative abort to the worker running this target.
    Returns {"ok": bool, ...}. The monitor will mark the job done once the
    worker reports completion (aborted jobs resolve as done, not error)."""
    with _remote_inflight_lock:
        info = dict(_remote_inflight.get(target) or {})
    if not info:
        return {"ok": False, "error": "not_inflight"}
    worker_url = info.get("worker_url")
    remote_id = info.get("remote_id")
    if not worker_url or not remote_id:
        return {"ok": False, "error": "no_remote_id_yet"}
    from nas_server.worker_client import abort as _wabort
    resp = _wabort(worker_url, str(remote_id))
    if resp and resp.get("ok"):
        log.info(f"[queue] abort forwarded to {info.get('worker_name')} for '{target}'")
        return {"ok": True, "remote": True, "worker": info.get("worker_name")}
    return {"ok": False, "error": (resp or {}).get("error", "worker_abort_failed")}


def _dispatch_and_monitor(item: dict, worker: dict) -> None:
    """
    Dispatch item to a remote worker and poll until completion.
    Runs as a daemon thread — _worker() continues picking up local jobs in parallel.
    On failure / worker offline >5 min, re-queues the item locally.
    Always removes target from _remote_inflight on exit.
    """
    target_name = item.get("target", "")
    try:
        _dispatch_and_monitor_inner(item, worker)
    finally:
        with _remote_inflight_lock:
            _remote_inflight.pop(target_name, None)


def _dispatch_and_monitor_inner(item: dict, worker: dict) -> None:
    """Inner implementation — called by _dispatch_and_monitor wrapper."""
    from nas_server import telegram as _tg
    from nas_server.config import settings
    from nas_server.worker_client import dispatch as _wdispatch, poll as _wpoll
    from nas_server.auto_process import _set_status as _ap_set_status

    target      = item["target"]
    worker_url  = worker["url"]
    worker_name = worker.get("name", worker_url)
    db_id       = str(item.get("_db_id", ""))

    vm_url = settings.get("vm_url", "")
    callback_url = (f"{vm_url}/jobs/{item.get('_db_id', 'x')}/complete"
                    if vm_url else None)

    remote_id = _wdispatch(
        worker_url,
        {
            "id":          str(item.get("_db_id", target)),
            "target":      target,
            "workflow":    item.get("workflow", "auto"),
            "source_file": item.get("source_file"),
            "extra_params": item.get("extra_params") or {},
        },
        callback_url=callback_url,
    )

    if not remote_id:
        log.warning(f"[queue] dispatch to {worker_name} failed — re-queuing '{target}'")
        with _remote_inflight_lock:
            _remote_inflight.pop(target, None)
        _requeue_at_front(item)
        return

    with _remote_inflight_lock:
        if target in _remote_inflight:
            _remote_inflight[target]["remote_id"] = remote_id

    try:
        from nas_server.database import set_worker_job
        set_worker_job(worker_name, remote_id)
    except Exception:
        pass

    log.info(f"[queue] '{target}' dispatched to {worker_name} (remote_id={remote_id})")

    # Inject stub into VM's autoprocess status so queue UI shows the job as active
    _ap_set_status(target, phase=f"🖥 {worker_name}", workflow=item.get("workflow", "auto"),
                   worker=worker_name, started_at=time.time())

    deadline     = time.time() + 4 * 3600   # 4 hr max
    dispatch_ts  = time.time()   # only a final NEWER than this counts as THIS job's output
    offline_since: float | None = None
    not_found_since: float | None = None   # track how long job has been unknown to worker

    while time.time() < deadline:
        time.sleep(30)

        # Authoritative completion: the worker POSTed /jobs/<db_id>/complete.
        # Trust it unconditionally — resolve done/error and never re-queue,
        # even if a later poll can't find the job (worker already moved on).
        with _remote_done_lock:
            done_rec = _remote_done.pop(db_id, None) if db_id else None
        if done_rec is not None:
            try:
                from nas_server.database import set_worker_job
                set_worker_job(worker_name, None)
            except Exception:
                pass
            if done_rec.get("error"):
                log.error(f"[queue] remote '{target}' on {worker_name} reported failed "
                          f"via callback: {done_rec['error']}")
                _ap_set_status(target, phase="error", error=done_rec["error"])
                _tg.send(
                    f"❌ <b>Remote job failed</b> ({worker_name}): "
                    f"<code>{target}</code>\n{done_rec['error']}"
                )
            else:
                log.info(f"[queue] remote '{target}' on {worker_name} completed "
                         f"(callback confirmed) — not re-queuing")
                _ap_set_status(target, phase="done")
            return

        status = _wpoll(worker_url, remote_id)

        if status is None:
            if offline_since is None:
                offline_since = time.time()
                log.warning(f"[queue] {worker_name} unreachable — waiting up to 5 min")
            elif time.time() - offline_since > 300:
                log.error(f"[queue] {worker_name} offline >5 min — re-queuing '{target}'")
                try:
                    from nas_server.database import set_worker_job
                    set_worker_job(worker_name, None)
                except Exception:
                    pass
                _ap_set_status(target, phase="done")
                _tg.send(
                    f"⚠️ <b>Worker offline</b> — <code>{target}</code> re-queued locally\n"
                    f"Worker {worker_name} did not respond for >5 min."
                )
                _requeue_at_front(item)
                return
            continue

        # Worker responded — reset offline timer
        offline_since = None
        try:
            from nas_server.database import update_worker_heartbeat
            update_worker_heartbeat(worker_name)
        except Exception:
            pass

        # Worker replied but doesn't know this job (completed+replaced by new job,
        # or worker restarted mid-run).  Give it 90 s grace in case of a race
        # between completion callback and the next job starting.
        if not status.get("running") and not status.get("done"):
            if not_found_since is None:
                not_found_since = time.time()
                log.warning(f"[queue] {worker_name}: job {remote_id} not in worker "
                            f"state — waiting 90 s to confirm")
                continue
            if time.time() - not_found_since < 90:
                continue  # still in grace window
            # Grace expired — decide based on NAS output
            log.warning(f"[queue] {worker_name}: job {remote_id} still not found "
                        f"after 90 s — checking NAS for '{target}'")
            try:
                from nas_server.database import set_worker_job
                set_worker_job(worker_name, None)
            except Exception:
                pass
            if _target_has_recent_output(target, since_ts=dispatch_ts):
                log.info(f"[queue] remote '{target}' — fresh output on NAS "
                         f"(newer than dispatch), marking done (worker moved on)")
                _ap_set_status(target, phase="done")
                return
            # Guard 1: worker is now busy with this SAME target under a different
            # job_id (a newer dispatch after a worker restart). The target is already
            # being handled — abandon this stale monitor, do NOT re-queue a duplicate.
            try:
                from nas_server.worker_client import ping as _wping
                _h = _wping(worker_url)
            except Exception:
                _h = None
            if (_h and _h.get("status") == "busy"
                    and target and target in str(_h.get("progress", ""))):
                log.info(f"[queue] remote '{target}' — worker now busy with same "
                         f"target (job {_h.get('job_id')}); abandoning stale monitor, "
                         f"not re-queuing")
                _ap_set_status(target, phase=f"🖥 {worker_name}")
                return
            # Guard 2: a run dir for this target is still being actively written on
            # the NAS (run in progress) — extend grace instead of re-queuing.
            if _target_has_active_run(target):
                log.info(f"[queue] remote '{target}' — active run dir on NAS still "
                         f"being written; extending grace, not re-queuing")
                not_found_since = None
                continue
            # Final guard: a completion callback may have landed during the grace
            # window. If so, the job finished — resolve done, do NOT re-queue.
            with _remote_done_lock:
                done_rec = _remote_done.pop(db_id, None) if db_id else None
            if done_rec is not None:
                if done_rec.get("error"):
                    _ap_set_status(target, phase="error", error=done_rec["error"])
                else:
                    log.info(f"[queue] remote '{target}' — callback confirmed done "
                             f"during grace; not re-queuing")
                    _ap_set_status(target, phase="done")
                return
            log.warning(f"[queue] remote '{target}' — no NAS output, worker idle, "
                        f"no active run; re-queuing")
            _tg.send(
                f"⚠️ <b>Worker job lost</b> ({worker_name}): "
                f"<code>{target}</code> — re-queued locally"
            )
            _requeue_at_front(item)
            return
        not_found_since = None  # reset if worker knows the job again

        if status.get("done"):
            try:
                from nas_server.database import set_worker_job
                set_worker_job(worker_name, None)
            except Exception:
                pass
            if status.get("error"):
                log.error(f"[queue] remote '{target}' on {worker_name} errored: "
                          f"{status['error']}")
                _ap_set_status(target, phase="error", error=status["error"])
                _tg.send(
                    f"❌ <b>Remote job failed</b> ({worker_name}): "
                    f"<code>{target}</code>\n{status['error']}"
                )
            else:
                log.info(f"[queue] remote '{target}' on {worker_name} completed ok")
                _ap_set_status(target, phase="done")
            return

    # Deadline exceeded
    log.error(f"[queue] remote '{target}' on {worker_name} timed out after 4 h")
    try:
        from nas_server.database import set_worker_job
        set_worker_job(worker_name, None)
    except Exception:
        pass
    if _target_has_active_run(target):
        # Same guard as the lost-job path above: a run dir is actively being
        # written for this target (e.g. the job was aborted remotely and re-run
        # locally, leaving this monitor stale). Re-queuing would start a
        # duplicate run behind the pipeline lock (IC 1805, 2026-06-12).
        log.warning(f"[queue] remote '{target}' deadline hit, but an active run "
                    f"dir is still being written on NAS — resolving without re-queue")
        return
    _ap_set_status(target, phase="done")
    _tg.send(
        f"⚠️ <b>Remote job timed out</b> ({worker_name}): "
        f"<code>{target}</code> — re-queued locally"
    )
    _requeue_at_front(item)


def is_running() -> bool:
    """Public: True if any stack or autoprocess job is currently active."""
    return _any_active()


def clear_stuck_inflight() -> dict:
    """
    Scan _remote_inflight for targets whose jobs are no longer running on any worker.
    For each stuck target:
      - If auto_final.fit exists on NAS (recent) → mark done
      - Otherwise → re-queue at front

    Returns {"cleared": [...], "requeued": [...]}
    """
    from nas_server.worker_client import ping as _wping
    from nas_server.config import settings
    from nas_server.auto_process import _set_status as _ap_set_status

    workers = {w["name"]: w for w in settings.get("remote_workers", [])}
    cleared, requeued = [], []

    with _remote_inflight_lock:
        stuck = set(_remote_inflight)  # snapshot

    for target in stuck:
        # Ask every worker if they are running this target
        running_somewhere = False
        for wname, worker in workers.items():
            health = _wping(worker["url"])
            if health and health.get("status") == "busy":
                # Check if the worker's current job progress mentions this target
                prog = health.get("progress", "")
                if target.lower() in prog.lower():
                    running_somewhere = True
                    break

        if running_somewhere:
            continue  # genuinely still running

        # Not running anywhere — resolve
        if _target_has_recent_output(target, max_age_hours=4):
            log.info(f"[queue] clear_stuck_inflight: '{target}' output on NAS — marking done")
            _ap_set_status(target, phase="done")
            with _remote_inflight_lock:
                _remote_inflight.pop(target, None)
            cleared.append(target)
        else:
            log.warning(f"[queue] clear_stuck_inflight: '{target}' no output — re-queuing")
            # Build a minimal item for requeue; workflow will be re-resolved
            _item = {
                "job_type": "process", "target": target,
                "workflow": "auto", "experiment_mode": False,
                "dry_run": False, "source_file": None,
                "manual_review": False, "extra_params": {},
                "_db_id": -1,
            }
            _requeue_at_front(_item)
            with _remote_inflight_lock:
                _remote_inflight.pop(target, None)
            requeued.append(target)

    return {"cleared": cleared, "requeued": requeued}


def _progress_monitor(target: str, job_type: str, eta_min: int,
                       stop_event: threading.Event):
    """Fire a Telegram ping every 15 min while a job runs. Skipped for short jobs."""
    if eta_min <= 15:
        return
    from nas_server import telegram as _tg
    interval = 15 * 60
    elapsed_min = 0
    while not stop_event.wait(interval):
        elapsed_min += 15
        try:
            if job_type == "autoprocess":
                from nas_server.auto_process import get_autoprocess_status
                s = get_autoprocess_status(target) or {}
                phase = s.get("phase", "running")
            else:
                from nas_server.stacker import get_stack_status
                s = get_stack_status(target) or {}
                phase = s.get("phase", "running")
            _tg.send(
                f"⏱ <b>Progress</b>: <code>{target}</code>\n"
                f"Step: {phase} | {elapsed_min}min elapsed / ~{eta_min}min est."
            )
        except Exception:
            pass


def _run_job(item: dict) -> None:
    """Execute one queue item. Runs in a sub-thread spawned by _worker()."""
    target = item["target"]
    job_type = item.get("job_type", "process")
    from nas_server import telegram as tg

    if job_type == "stack":
        engine = item.get("engine", "siril")
        log.info(f"[queue] starting stack '{target}' (engine={engine})")
        stop_event = threading.Event()
        try:
            from nas_server.config import settings
            from nas_server.stacker import stack_target, _estimate_stack_minutes
            try:
                import sqlite3 as _sq
                with _sq.connect(settings.get("db_path", "")) as _c:
                    fc = _c.execute(
                        "SELECT COUNT(*) FROM light_files WHERE target=? AND exclude=0",
                        (target,)
                    ).fetchone()[0] or 60
            except Exception:
                fc = 60
            eta_min = _estimate_stack_minutes(target, fc, engine)
            monitor = threading.Thread(
                target=_progress_monitor,
                args=(target, "stack", eta_min, stop_event),
                daemon=True, name=f"progress-{target}"
            )
            monitor.start()
            result = stack_target(
                target,
                library_path=settings["seestar_library_path"],
                db_path=settings.get("db_path"),
                engine=engine,
                cull=item.get("cull", True),
                bottom_pct=item.get("bottom_pct", 0.10),
                min_stars=item.get("min_stars", 20),
                fast=item.get("fast", False),
                framing=item.get("framing", "min"),
                hero=item.get("hero", False),
                drizzle=item.get("drizzle", False),
                exptime=item.get("exptime"),
                eq_only=item.get("eq_only", True),
                ecc_threshold=item.get("ecc_threshold", 0.6),
                sky_level_factor=item.get("sky_level_factor", 3.0),
                gradient_threshold=item.get("gradient_threshold", 0.5),
            )
            stop_event.set()
            if not result.get("success"):
                raise RuntimeError(result.get("error", "stack failed"))
            post_workflow = item.get("workflow")
            if post_workflow and result.get("processed_fit"):
                from pathlib import Path as _Path
                source_file = _Path(result["processed_fit"]).name
                follow = add_job(
                    target, workflow=post_workflow,
                    experiment_mode=bool(item.get("experiment_mode")),
                    source_file=source_file,
                )
                log.info(f"[queue] auto-queued autoprocess '{target}' "
                         f"({post_workflow}, src={source_file}) — position {follow['position']}")
        except Exception as e:
            stop_event.set()
            log.error(f"[queue] stack '{target}' failed: {e}")
            tg.send(f"❌ <b>Stack failed</b>: <code>{target}</code>\n{e}")
    else:
        workflow = item["workflow"]

        # ── Auto workflow resolution ──────────────────────────────────────────
        if workflow == "auto":
            workflow = _resolve_workflow(target, "auto")
            log.info(f"[queue] '{target}': auto workflow → {workflow}")

        # ── Remote worker dispatch (if one is available and job is eligible) ──
        # dry_run and manual_review must stay local; a first-time/forced crop
        # review must stay on the VM (the review record + blocking event +
        # previews + web UI all live here, not on the worker); everything else
        # can go remote.
        if (not item.get("dry_run") and not item.get("manual_review")
                and not _needs_crop_review_on_vm(item)):
            _remote_worker = _find_available_worker()
            if _remote_worker:
                _remote_item = {**item, "workflow": workflow}
                with _remote_inflight_lock:
                    if target in _remote_inflight:
                        log.info(f"[queue] '{target}' already in flight remotely — "
                                 f"skipping duplicate dispatch")
                        return
                    _remote_inflight[target] = {
                        "worker_url": _remote_worker["url"],
                        "worker_name": _remote_worker.get("name", _remote_worker["url"]),
                        "remote_id": None,
                    }
                threading.Thread(
                    target=_dispatch_and_monitor,
                    args=(_remote_item, _remote_worker),
                    daemon=True,
                    name=f"remote-{target}",
                ).start()
                log.info(f"[queue] '{target}' → {_remote_worker.get('name', _remote_worker['url'])} "
                         f"(remote dispatch, workflow={workflow})")
                return  # _worker() loop continues immediately; monitor thread handles the rest

        # ── Local execution ───────────────────────────────────────────────────
        log.info(f"[queue] starting '{target}' (workflow={workflow})")
        stop_event = threading.Event()
        try:
            from nas_server.auto_process import (auto_process, _estimate_autoprocess_minutes,
                                                  PIPELINE_LOCK, _pipeline_active_target)
            eta_min = _estimate_autoprocess_minutes(target, workflow)
            monitor = threading.Thread(
                target=_progress_monitor,
                args=(target, "autoprocess", eta_min, stop_event),
                daemon=True, name=f"progress-{target}"
            )
            monitor.start()
            import nas_server.auto_process as _ap_mod
            with PIPELINE_LOCK:
                _ap_mod._pipeline_active_target = target
                _ap_mod.mark_pipeline_lock_held()
                try:
                    auto_process(
                        target,
                        workflow=workflow,
                        dry_run=item["dry_run"],
                        experiment_mode=item["experiment_mode"],
                        source_file=item.get("source_file"),
                        manual_review=bool(item.get("manual_review")),
                        extra_params=item.get("extra_params") or {},
                    )
                finally:
                    _ap_mod._pipeline_active_target = None
                    _ap_mod.clear_pipeline_lock_held()
                    # Safety net: clear any physics-only switch the run set, so a
                    # later cron (planner/suggestions/story) isn't starved of the API.
                    try:
                        from nas_server import claude_client as _cc
                        _cc.set_physics_only(False)
                    except Exception:
                        pass
            stop_event.set()
        except Exception as e:
            stop_event.set()
            log.error(f"[queue] '{target}' failed: {e}")
            tg.send(f"❌ <b>Queue job failed</b>: <code>{target}</code>\n{e}")
            try:
                from nas_server.auto_process import _set_status
                _set_status(target, phase="error", error=str(e))
            except Exception:
                pass

    with _queue_lock:
        remaining = len(_queue)
    log.info(f"[queue] '{target}' done — "
             + (f"{remaining} job(s) remaining" if remaining else "queue empty"))


def _worker():
    global _current_job_target, _current_park_event
    log.info("[queue] worker started")
    while True:
        # Only block on LOCAL active jobs. Remote-worker jobs run in their own
        # monitor threads and the queue should continue dispatching stacking or
        # local process jobs in parallel while a remote job is in flight.
        # (_any_active() still counts remote inflight for graceful-restart/disk-guard.)
        _local_active = False
        try:
            from nas_server.auto_process import get_all_autoprocess_statuses
            _local_active = any(
                s.get("phase") not in ("done", "error", "aborted", None)
                and s.get("target") not in _parked_for_review
                and not s.get("worker")          # worker=None → local job
                for s in get_all_autoprocess_statuses()
            )
        except Exception:
            pass
        if not _local_active:
            try:
                from nas_server.stacker import get_all_stack_statuses
                _local_active = any(s.get("running") for s in get_all_stack_statuses())
            except Exception:
                pass
        if _local_active:
            time.sleep(5)
            continue

        if _paused:
            time.sleep(3)
            continue

        # Guard: pause queue if VM disk > 85% full
        _disk = shutil.disk_usage('/')
        if _disk.used / _disk.total > 0.85:
            from nas_server import telegram as _tg
            _tg.send(
                f"⚠️ <b>Queue paused — disk {_disk.used / _disk.total:.0%} full</b>\n"
                f"VM disk has only {_disk.free // (1 << 30)} GB free. Clear space to resume."
            )
            log.warning(f"[queue] disk guard: {_disk.used / _disk.total:.0%} used, "
                        f"{_disk.free // (1 << 30)} GB free — sleeping 60s")
            time.sleep(60)
            continue

        item = _pop_next()
        if item is None:
            time.sleep(3)
            continue

        target = item["target"]
        park_ev = threading.Event()
        with _park_lock:
            _current_job_target = target
            _current_park_event = park_ev

        job_thread = threading.Thread(
            target=_run_job, args=(item,), daemon=True, name=f"job-{target}"
        )
        job_thread.start()

        # If a remote worker is free, immediately look ahead for a process job
        # it can run in parallel (e.g. M 51 while SH2-101 stack is running).
        _proactive_remote_dispatch(current_target=target)

        # Wait for job to complete normally OR park on review.
        # Re-check for proactive dispatch every 15s in case jobs were added
        # to the queue after the current job started (common: user queues
        # process jobs while a stack is already running).
        _last_proactive = time.time()
        while job_thread.is_alive() and not park_ev.is_set():
            job_thread.join(timeout=1.0)
            if time.time() - _last_proactive >= 15:
                _proactive_remote_dispatch(current_target=target)
                _last_proactive = time.time()

        with _park_lock:
            _current_job_target = None
            _current_park_event = None

        if park_ev.is_set() and job_thread.is_alive():
            log.info(f"[queue] '{target}' parked waiting for review — continuing queue")


def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    try:
        from nas_server.database import queue_load_pending
        pending = queue_load_pending()
        if pending:
            with _queue_lock:
                _queue.extend(pending)
            log.info(f"[queue] reloaded {len(pending)} pending job(s) from DB")
    except Exception as e:
        log.warning(f"[queue] could not reload pending queue from DB: {e}")
    t = threading.Thread(target=_worker, daemon=True, name="queue-worker")
    t.start()

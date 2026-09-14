"""RunPod CPU-pod lifecycle state machine (Candidate 6, issue #412).

Scope of this module, deliberately narrow -- matching the same "smallest
reviewable slice" discipline as nas_server/runpod_spend_policy.py: pure state
transitions against a real DB table, with NO real RunPod API calls. This
module does not create, start, poll, or terminate an actual pod -- it tracks
the STATE a caller reports, and enforces the invariants issue #412 requires
(duplicate-launch prevention, a startup health deadline, dispatch only after
ready, current-job association). The real RunPod API client, queue_manager.py
integration, orphan-detection sweep, and UI/status reporting are Candidate
6's larger remaining scope, intentionally deferred.

States and legal transitions:

    PROVISIONING --> STARTING --> READY <--> BUSY
                          |          |          |
                          v          v          v
                       FAILED ----------------> TERMINATING --> TERMINATED
                          ^                          |               ^
                          +---(also reachable---------)              |
                          |    directly from any                     |
                          |    non-TERMINATED state)                 |
                          +-------------------------------------------+
                     (TERMINATING/TERMINATED reachable from any state
                      that isn't already TERMINATED, INCLUDING FAILED)

- PROVISIONING: a work_key has been claimed (duplicate-launch prevention
  active) but RunPod hasn't handed back a real pod_id yet.
- STARTING: pod_id known, waiting for a health check to confirm readiness,
  bounded by `ready_deadline` (the "startup health deadline" issue #412
  requires) -- exceeding it is a FAILED transition, not an indefinite wait.
- READY: healthy, idle, available for dispatch (a claim_for_job() target).
- BUSY: has an active job (current-job association) -- won't be selected
  for a second job while busy.
- FAILED: startup or infra failure. **FAILED is NOT "gone"** (ChatGPT
  catch, PR #417) -- a failed health check or startup crash tells us
  nothing about whether the underlying RunPod instance is actually down;
  a FAILED record still occupies its work_key (blocks a replacement
  provision) and must be walked through TERMINATING -> TERMINATED like
  any other live pod before that work_key frees up. See
  nas_server.runpod_spend_policy's own record_pod_failure()/
  is_work_blocked() for the separate one-retry-then-block policy this
  module intentionally does not duplicate; callers combine both modules.
- TERMINATING / TERMINATED: shutdown requested / confirmed gone. Only
  TERMINATED counts as "gone" for duplicate-launch prevention -- see
  CONFIRMED_GONE_STATES.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

PROVISIONING = "provisioning"
STARTING = "starting"
READY = "ready"
BUSY = "busy"
TERMINATING = "terminating"
TERMINATED = "terminated"
FAILED = "failed"

# Only TERMINATED means "confirmed gone" -- this is what frees a work_key
# for a new provision() and is what the DB's own partial UNIQUE index
# (idx_pod_lifecycle_one_active_per_work_key) enforces atomically. FAILED
# is deliberately NOT here: a failure doesn't prove the pod is actually
# down, so it must still block a replacement and remain reachable through
# TERMINATING -> TERMINATED like any other live record (ChatGPT catch,
# PR #417 -- the original TERMINAL_STATES = (TERMINATED, FAILED) let a
# FAILED-but-still-billable pod get silently replaced AND made
# unreachable from mark_terminating(), a real "paid compute with no
# owner" hole).
CONFIRMED_GONE_STATES = (TERMINATED,)

STARTUP_DEADLINE_MINUTES_DEFAULT = 5.0


class LifecycleError(ValueError):
    """A caller tried an illegal transition or a duplicate provision."""


@dataclass
class PodRecord:
    id: int
    work_key: str
    pod_id: str | None
    state: str
    created_at: str
    ready_deadline: str | None
    ready_at: str | None
    last_seen_at: str | None
    current_job_id: str | None
    terminated_at: str | None
    failure_reason: str | None

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> "PodRecord":
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _get(record_id: int) -> PodRecord:
    from nas_server.database import get_pod_lifecycle_row

    row = get_pod_lifecycle_row(record_id)
    if row is None:
        raise LifecycleError(f"no pod lifecycle record with id={record_id}")
    return PodRecord._from_row(row)


def get_active_pod_for_work(work_key: str) -> PodRecord | None:
    """The record duplicate-launch prevention checks against -- a caller
    should call this BEFORE provision() to decide whether one is already
    in flight, rather than relying solely on provision() rejecting a
    duplicate (which it also does, defensively, and atomically -- see
    provision()'s own docstring)."""
    from nas_server.database import get_active_pod_lifecycle_rows

    rows = get_active_pod_lifecycle_rows(work_key, terminal_states=CONFIRMED_GONE_STATES)
    if not rows:
        return None
    return PodRecord._from_row(rows[0])


def provision(work_key: str, *, startup_deadline_minutes: float = STARTUP_DEADLINE_MINUTES_DEFAULT,
             now: datetime | None = None) -> PodRecord:
    """Claim `work_key` and start a new PROVISIONING record.

    Duplicate-launch prevention is enforced ATOMICALLY at the DB layer
    (the schema's partial UNIQUE index on work_key, active-rows-only) --
    not by this function's own pre-check, which exists only to raise a
    friendlier error message in the common (non-race) case. Two
    concurrent callers racing this function will not both succeed: the
    loser's INSERT hits the UNIQUE constraint and raises
    sqlite3.IntegrityError here, which is caught and re-raised as
    LifecycleError (ChatGPT catch, PR #417 -- the original version only
    had the non-atomic pre-check, a real TOCTOU race)."""
    from nas_server.database import insert_pod_lifecycle_row

    existing = get_active_pod_for_work(work_key)
    if existing is not None:
        raise LifecycleError(
            f"work_key {work_key!r} already has an active pod (id={existing.id}, "
            f"state={existing.state}) -- refusing a duplicate provision"
        )

    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=startup_deadline_minutes)
    try:
        record_id = insert_pod_lifecycle_row(
            work_key=work_key, state=PROVISIONING, ready_deadline=_iso(deadline),
        )
    except sqlite3.IntegrityError as exc:
        raise LifecycleError(
            f"work_key {work_key!r} already has an active pod (lost a concurrent "
            f"provision race) -- refusing a duplicate provision"
        ) from exc
    return _get(record_id)


def mark_pod_id(record_id: int, pod_id: str) -> PodRecord:
    """PROVISIONING -> STARTING, once RunPod hands back a real pod_id."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state != PROVISIONING:
        raise LifecycleError(f"cannot assign pod_id from state {record.state!r} (expected {PROVISIONING!r})")
    update_pod_lifecycle_row(record_id, pod_id=pod_id, state=STARTING)
    return _get(record_id)


def mark_ready(record_id: int, *, now: datetime | None = None) -> PodRecord:
    """STARTING -> READY, but only within the startup health deadline.
    Exceeding it is a FAILED transition instead -- an indefinitely
    "starting" pod is exactly the kind of orphaned spend issue #412 exists
    to prevent, not something to wait on forever."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state != STARTING:
        raise LifecycleError(f"cannot mark ready from state {record.state!r} (expected {STARTING!r})")

    now = now or datetime.now(timezone.utc)
    if record.ready_deadline is not None:
        deadline = datetime.fromisoformat(record.ready_deadline).replace(tzinfo=timezone.utc)
        if now > deadline:
            update_pod_lifecycle_row(
                record_id, state=FAILED,
                failure_reason=f"startup health deadline exceeded (deadline {record.ready_deadline})",
            )
            return _get(record_id)

    update_pod_lifecycle_row(record_id, state=READY, ready_at=_iso(now), last_seen_at=_iso(now))
    return _get(record_id)


def claim_for_job(record_id: int, job_id: str, *, now: datetime | None = None) -> PodRecord:
    """READY -> BUSY, associating the pod with exactly one current job.
    Dispatch only after ready: this is the only path into BUSY, and it
    requires READY -- a caller cannot claim a still-STARTING or already-BUSY
    pod for a second job."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state != READY:
        raise LifecycleError(f"cannot claim from state {record.state!r} (expected {READY!r})")

    now = now or datetime.now(timezone.utc)
    update_pod_lifecycle_row(record_id, state=BUSY, current_job_id=job_id, last_seen_at=_iso(now))
    return _get(record_id)


def release_after_job(record_id: int, *, now: datetime | None = None) -> PodRecord:
    """BUSY -> READY, once the current job finishes (success or failure --
    job-level outcome is telemetry_events'/runpod_spend_policy's concern,
    not this module's; a pod that did a failed job is still a healthy pod
    available for the next one)."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state != BUSY:
        raise LifecycleError(f"cannot release from state {record.state!r} (expected {BUSY!r})")

    now = now or datetime.now(timezone.utc)
    update_pod_lifecycle_row(record_id, state=READY, current_job_id=None, last_seen_at=_iso(now))
    return _get(record_id)


def touch(record_id: int, *, now: datetime | None = None) -> PodRecord:
    """Update last_seen_at without changing state -- a heartbeat for
    is_idle()-style checks, callable from any not-confirmed-gone state
    (including FAILED -- a failed-but-unterminated pod may still need
    heartbeat/reconciliation tracking)."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state in CONFIRMED_GONE_STATES:
        raise LifecycleError(f"cannot touch a confirmed-gone record (state={record.state!r})")

    now = now or datetime.now(timezone.utc)
    update_pod_lifecycle_row(record_id, last_seen_at=_iso(now))
    return _get(record_id)


def mark_terminating(record_id: int, reason: str | None = None) -> PodRecord:
    """Any not-already-TERMINATED state -> TERMINATING. Deliberately
    permissive about the FROM state -- a pod may need to be terminated
    from PROVISIONING (abort before it ever came up), STARTING, READY,
    BUSY (abort a running job), or **FAILED** alike; there's no state
    where "stop this" should be refused. FAILED is explicitly included
    (ChatGPT catch, PR #417) -- a failed pod is exactly the case that most
    needs a real path to confirmed termination, not the one case where
    this function used to refuse to even try."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state in CONFIRMED_GONE_STATES:
        raise LifecycleError(f"already confirmed gone (state={record.state!r})")

    update_pod_lifecycle_row(record_id, state=TERMINATING, failure_reason=reason)
    return _get(record_id)


def mark_terminated(record_id: int, *, now: datetime | None = None) -> PodRecord:
    """TERMINATING -> TERMINATED, once shutdown is actually confirmed --
    never assumed. A caller that requested termination but never confirmed
    it happened must not treat the pod as gone (issue #412's own stop
    rule: any lifecycle path capable of leaving paid compute running with
    no owner/expiry is a stop condition)."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state != TERMINATING:
        raise LifecycleError(f"cannot confirm terminated from state {record.state!r} (expected {TERMINATING!r})")

    now = now or datetime.now(timezone.utc)
    update_pod_lifecycle_row(record_id, state=TERMINATED, terminated_at=_iso(now))
    return _get(record_id)


def mark_failed(record_id: int, reason: str) -> PodRecord:
    """Any not-already-TERMINATED state -> FAILED, for an infra failure
    that isn't a normal shutdown (startup crash, RunPod-side error, etc.).
    A FAILED record still blocks a replacement provision() and remains
    reachable from mark_terminating() -- it is NOT treated as gone (see
    CONFIRMED_GONE_STATES). Callers combine this with
    runpod_spend_policy.record_pod_failure(work_key, ...) for the
    one-retry-then-block policy -- that's separate, intentionally not
    duplicated here."""
    from nas_server.database import update_pod_lifecycle_row

    record = _get(record_id)
    if record.state in CONFIRMED_GONE_STATES:
        raise LifecycleError(f"already confirmed gone (state={record.state!r})")

    update_pod_lifecycle_row(record_id, state=FAILED, failure_reason=reason)
    return _get(record_id)

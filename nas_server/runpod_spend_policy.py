"""RunPod CPU/GPU spend policy (Candidate 6, issue #412) -- decided by Henry
2026-08-24 at a /briefing, recorded verbatim as issue #412's own top comment.
That comment is the authoritative policy record; this module implements it.

Scope of this module, deliberately narrow: admission control, cost
estimation, retry tracking, and idle detection as pure, independently
testable logic against the real `runpod_spend_events`/`runpod_retry_state`
tables and the existing `telemetry_events` table. It does NOT touch
`queue_manager.py`'s actual dispatch flow, provision a pod, or do anything
live -- that integration is Candidate 6's larger remaining scope (state
machine, callback/poll wiring, orphan detection, UI/status reporting),
intentionally deferred to keep this slice reviewable. Nothing here is wired
into a live code path yet.

Caps are hard enforcement, not warnings, per Henry's explicit words: "$2/session
and $5/day hard enforcement limits, not merely Telegram warnings." The budget
check belongs before dispatch (admission control on the NEXT operation's
projected cost), with actual spend reconciled afterward via
record_runpod_spend() -- never a post-hoc-only check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SESSION_CAP_USD = 2.00
DAILY_CAP_USD = 5.00
MONTHLY_CAP_USD = 25.00  # Henry: explicitly a rolling policy to revisit from
                          # real accounting data after 2-4 weeks, not a
                          # permanent constant. Revisit the NUMBER, not the
                          # enforcement mechanism.

# "Daily" and "monthly" are rolling windows from now, not calendar
# day/month boundaries -- avoids a midnight-UTC edge case (a job at 23:59
# and another at 00:01 shouldn't get separate fresh daily budgets 2 minutes
# apart), and matches the "rolling" framing Henry used for the monthly cap.
DAILY_WINDOW = timedelta(hours=24)
MONTHLY_WINDOW = timedelta(days=30)

IDLE_MINUTES_DEFAULT = 10.0  # Henry: idle means no useful work in progress,
                              # not "no connection" -- see is_idle()'s own
                              # docstring for what "useful work" means here.

CAP_NAMES = ("session", "daily", "monthly")


@dataclass
class AdmissionResult:
    allowed: bool
    violated_cap: str | None  # "session" | "daily" | "monthly" | None
    projected_totals: dict[str, float] = field(default_factory=dict)
    current_totals: dict[str, float] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0


@dataclass
class RetryDecision:
    action: str  # "retry" | "blocked"
    failure_count: int
    last_reason: str | None


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def spend_totals(session_start: datetime, *, now: datetime | None = None) -> dict[str, dict[str, float]]:
    """Current spend for all three windows, keyed by cap name.

    `session_start` is caller-defined -- this module doesn't invent what a
    "session" boundary means (a single queued job's lifecycle, one CLI
    benchmark run, etc.); the caller supplies the timestamp that window
    started. Daily and monthly are always rolling from `now`.
    """
    from nas_server.database import runpod_spend_since

    now = now or datetime.now(timezone.utc)
    return {
        "session": runpod_spend_since(_iso(session_start)),
        "daily": runpod_spend_since(_iso(now - DAILY_WINDOW)),
        "monthly": runpod_spend_since(_iso(now - MONTHLY_WINDOW)),
    }


def estimate_cost(*, operation: str, backend: str, history_limit: int = 10) -> float | None:
    """Estimate the next operation's likely cost from real historical runs
    of the same (operation, backend) pair -- median of the most recent
    `history_limit` real costs. Returns None when there's no history yet
    (a genuinely new operation/backend combination) -- the caller decides
    the bootstrap behavior via check_admission()'s `fallback_estimate_usd`,
    this function never invents a number it has no evidence for.
    """
    from nas_server.database import runpod_spend_history

    costs = runpod_spend_history(operation=operation, backend=backend, limit=history_limit)
    if not costs:
        return None
    costs.sort()
    mid = len(costs) // 2
    if len(costs) % 2 == 0:
        return (costs[mid - 1] + costs[mid]) / 2
    return costs[mid]


def check_admission(
    *,
    session_start: datetime,
    estimated_cost_usd: float | None = None,
    operation: str | None = None,
    backend: str | None = None,
    fallback_estimate_usd: float | None = None,
    now: datetime | None = None,
) -> AdmissionResult:
    """Projected-cost admission control: would dispatching the next operation
    push any cap over its limit? Checked BEFORE dispatch, not after.

    Pass `estimated_cost_usd` directly if the caller already knows it (e.g.
    a fixed-price operation); otherwise pass `operation`/`backend` and this
    calls estimate_cost() itself, falling back to `fallback_estimate_usd`
    when there's no history yet. **Fails closed, not open, when no cost
    information exists at all** (no direct estimate, no history, and no
    explicit fallback) -- returns `allowed=False` with
    `violated_cap="no_estimate"` rather than silently admitting unmeasured
    work as if it cost nothing. An earlier version of this function
    defaulted the fallback to 0.0, which is exactly the hole hard
    enforcement exists to close (Codex catch, PR #415) -- a caller that
    forgets to think about the bootstrap cost must never get free
    admission by omission.
    """
    if estimated_cost_usd is None:
        if operation is None or backend is None:
            raise ValueError(
                "must pass either estimated_cost_usd, or both operation and backend "
                "so a real cost can be estimated"
            )
        estimated_cost_usd = estimate_cost(operation=operation, backend=backend)
        if estimated_cost_usd is None:
            if fallback_estimate_usd is None:
                return AdmissionResult(
                    allowed=False,
                    violated_cap="no_estimate",
                    projected_totals={},
                    current_totals={},
                    estimated_cost_usd=0.0,
                )
            estimated_cost_usd = fallback_estimate_usd

    # This is the hard spend boundary -- validate whatever cost figure made
    # it here (direct, from real history, or a caller's fallback) before
    # trusting it in the projection math. A negative value REDUCES the
    # projected total, which can mask an already-over-cap situation; NaN
    # makes every `>` comparison below silently False (IEEE 754), which
    # would let ANY spend through unblocked. Fail closed rather than let
    # either slip past by relying on the arithmetic to happen to catch it
    # (Codex catch, PR #415 second round).
    if not math.isfinite(estimated_cost_usd) or estimated_cost_usd < 0:
        return AdmissionResult(
            allowed=False,
            violated_cap="invalid_estimate",
            projected_totals={},
            current_totals={},
            estimated_cost_usd=estimated_cost_usd,
        )

    current = spend_totals(session_start, now=now)
    caps = {"session": SESSION_CAP_USD, "daily": DAILY_CAP_USD, "monthly": MONTHLY_CAP_USD}
    projected = {
        name: current[name]["total_cost"] + estimated_cost_usd for name in CAP_NAMES
    }

    for name in CAP_NAMES:
        if projected[name] > caps[name]:
            return AdmissionResult(
                allowed=False,
                violated_cap=name,
                projected_totals=projected,
                current_totals={name: current[name]["total_cost"] for name in CAP_NAMES},
                estimated_cost_usd=estimated_cost_usd,
            )

    return AdmissionResult(
        allowed=True,
        violated_cap=None,
        projected_totals=projected,
        current_totals={name: current[name]["total_cost"] for name in CAP_NAMES},
        estimated_cost_usd=estimated_cost_usd,
    )


def record_pod_failure(work_key: str, reason: str) -> RetryDecision:
    """Apply Henry's decided retry policy: exactly one automatic retry.

    First failure for `work_key` -> "retry" (a transient/startup failure
    shouldn't require Henry). Second consecutive failure -> "blocked"
    (evidence, not bad luck -- terminate, preserve diagnostics, mark the
    queue item review-required, surface it in the next briefing; never
    repeat-retry the same failure a third time)."""
    from nas_server.database import record_runpod_pod_failure as _record

    row = _record(work_key, reason)
    action = "blocked" if row["blocked"] else "retry"
    return RetryDecision(action=action, failure_count=row["failure_count"], last_reason=row["last_reason"])


def is_work_blocked(work_key: str) -> bool:
    from nas_server.database import get_runpod_retry_state

    state = get_runpod_retry_state(work_key)
    return bool(state and state["blocked"])


def clear_retry_state(work_key: str) -> None:
    """Call on eventual success -- an old failure must not permanently
    punish a later, unrelated attempt at the same logical work."""
    from nas_server.database import clear_runpod_retry_state as _clear

    _clear(work_key)


def is_idle(backend: str, *, idle_minutes: float = IDLE_MINUTES_DEFAULT,
           now: datetime | None = None) -> bool:
    """Whether `backend` currently has no useful work in progress.

    Henry's explicit distinction: idle means no work happening, not "no
    connection" -- a pod actively processing for 45 minutes is not idle
    even with nobody watching; a pod sitting at a shell waiting on Henry IS
    idle even if technically healthy and reachable. This checks the real
    telemetry_events table (already tracks per-job running state) for any
    row still 'running' against this backend -- if one exists, there is
    useful work in progress right now, full stop, regardless of how long
    it's been running. If none is running, idle is measured from the most
    recent row's completion (or dispatch, if it never completed) against
    `idle_minutes` -- a backend with NO telemetry history at all is treated
    as idle immediately (nothing has ever proven it's doing anything)."""
    from nas_server.database import get_inflight_telemetry, get_recent_telemetry

    if any(row["backend"] == backend for row in get_inflight_telemetry()):
        return False

    now = now or datetime.now(timezone.utc)
    for row in get_recent_telemetry(limit=200):
        if row["backend"] != backend:
            continue
        reference = row.get("completed_at") or row.get("started_at")
        if not reference:
            continue
        try:
            reference_dt = datetime.fromisoformat(reference).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        elapsed_minutes = (now - reference_dt).total_seconds() / 60.0
        return elapsed_minutes >= idle_minutes

    return True

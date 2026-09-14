"""Persisted, fail-closed governance for recommendation/default maturity.

This module records eligibility and authority.  It deliberately does not alter
any processing setting or choose a treatment from aggregate winner rates.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


STATES = (
    "research_only",
    "provisional",
    "production_default",
    "suspended",
    "demoted",
    "retired",
)

GATE_NAMES = (
    "treatment_identity",
    "denominator_completeness",
    "intended_effect_and_preservation",
    "independent_replication",
    "evaluator_human_validity",
    "operational_reliability",
    "drift_compatibility",
    "risk_reversibility",
    "rollback_plan_and_monitoring",
)

_ADVANCING = {
    ("research_only", "provisional"),
    ("provisional", "production_default"),
}
_FAIL_SAFE = {
    ("provisional", "suspended"),
    ("production_default", "suspended"),
    ("production_default", "demoted"),
}
_HUMAN_ONLY = {
    ("suspended", "provisional"),
    ("demoted", "provisional"),
    ("research_only", "retired"),
    ("provisional", "retired"),
    ("suspended", "retired"),
    ("demoted", "retired"),
    ("production_default", "retired"),
}
_FAIL_SAFE_SIGNALS = {"operational_reliability_regression", "drift_compatibility_regression"}
_COMPARATORS = {
    "lt": lambda observed, threshold: observed < threshold,
    "lte": lambda observed, threshold: observed <= threshold,
    "gt": lambda observed, threshold: observed > threshold,
    "gte": lambda observed, threshold: observed >= threshold,
}
_FORBIDDEN_AGGREGATE_KEYS = {"win_rate", "winner_rate", "aggregate_winner_rate"}


class TransitionBlocked(ValueError):
    """A requested transition did not satisfy its evidence/authority contract."""


def migrate(conn) -> None:
    """Create the persisted current-state and append-only audit tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recommendation_states (
            treatment_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN (
                'research_only', 'provisional', 'production_default',
                'suspended', 'demoted', 'retired'
            )),
            treatment_identity_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recommendation_transition_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            treatment_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            revision INTEGER NOT NULL,
            gate_evidence_json TEXT,
            human_authorization_json TEXT,
            fail_safe_trigger_json TEXT,
            requested_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (treatment_id) REFERENCES recommendation_states(treatment_id),
            UNIQUE (treatment_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_recommendation_transitions_treatment
            ON recommendation_transition_events(treatment_id, revision);

        CREATE TABLE IF NOT EXISTS recommendation_fail_safe_policies (
            policy_ref TEXT PRIMARY KEY,
            signal TEXT NOT NULL CHECK (signal IN (
                'operational_reliability_regression',
                'drift_compatibility_regression'
            )),
            comparator TEXT NOT NULL CHECK (comparator IN ('lt', 'lte', 'gt', 'gte')),
            threshold REAL NOT NULL,
            authority TEXT NOT NULL CHECK (authority = 'henry'),
            authorized_at TEXT NOT NULL,
            decision_ref TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contains_forbidden_aggregate(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _FORBIDDEN_AGGREGATE_KEYS
            or _contains_forbidden_aggregate(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_aggregate(item) for item in value)
    return False


def _validate_gates(gates: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(gates, Mapping):
        raise TransitionBlocked("all nine gate inputs are required")
    missing = [name for name in GATE_NAMES if name not in gates]
    if missing:
        raise TransitionBlocked("missing gate inputs: " + ", ".join(missing))
    unexpected = sorted(set(gates) - set(GATE_NAMES))
    if unexpected:
        raise TransitionBlocked("unexpected gate inputs: " + ", ".join(unexpected))
    normalized: dict[str, Any] = {}
    for name in GATE_NAMES:
        gate = gates[name]
        if not isinstance(gate, Mapping):
            raise TransitionBlocked(f"gate {name} must be a structured evidence record")
        if gate.get("passed") is not True:
            raise TransitionBlocked(f"gate {name} did not pass")
        refs = gate.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
            raise TransitionBlocked(f"gate {name} requires at least one evidence reference")
        normalized[name] = dict(gate)
    if _contains_forbidden_aggregate(normalized):
        raise TransitionBlocked("raw aggregate winner rates are not promotion gate inputs")
    return normalized


def _validate_henry_authorization(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise TransitionBlocked("explicit Henry authorization is required")
    required = ("authority", "authorized_at", "decision_ref")
    if record.get("authority") != "henry" or any(not record.get(field) for field in required):
        raise TransitionBlocked("a complete explicit Henry authorization record is required")
    return dict(record)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TransitionBlocked(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise TransitionBlocked(f"{field} must be a finite number")
    return normalized


def _validate_fail_safe(conn, trigger: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trigger, Mapping):
        raise TransitionBlocked("a pre-authorized deterministic fail-safe trigger is required")
    expected_fields = {"policy_ref", "observed_value", "evidence_ref"}
    unexpected = sorted(set(trigger) - expected_fields)
    if unexpected:
        raise TransitionBlocked(
            "fail-safe trigger contains caller-supplied policy fields: " + ", ".join(unexpected)
        )
    for field in expected_fields:
        if field not in trigger or trigger[field] is None or trigger[field] == "":
            raise TransitionBlocked(f"fail-safe trigger requires {field}")
    if _contains_forbidden_aggregate(trigger):
        raise TransitionBlocked("raw aggregate winner rates cannot trigger a state change")
    policy = conn.execute(
        "SELECT * FROM recommendation_fail_safe_policies WHERE policy_ref=?",
        (trigger["policy_ref"],),
    ).fetchone()
    if not policy:
        raise TransitionBlocked("fail-safe policy is not registered and pre-authorized")
    observed = _finite_number(trigger["observed_value"], "observed_value")
    threshold = _finite_number(policy["threshold"], "persisted policy threshold")
    if not _COMPARATORS[policy["comparator"]](observed, threshold):
        raise TransitionBlocked("fail-safe observation did not cross the persisted policy threshold")
    return {
        "policy_ref": policy["policy_ref"],
        "signal": policy["signal"],
        "comparator": policy["comparator"],
        "threshold": threshold,
        "observed_value": observed,
        "evidence_ref": trigger["evidence_ref"],
        "authorization": {
            "authority": policy["authority"],
            "authorized_at": policy["authorized_at"],
            "decision_ref": policy["decision_ref"],
        },
    }


def register_fail_safe_policy(
    policy_ref: str,
    *,
    signal: str,
    comparator: str,
    threshold: float,
    human_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist an immutable deterministic fail-safe policy authorized by Henry."""
    if not policy_ref:
        raise ValueError("policy_ref is required")
    if signal not in _FAIL_SAFE_SIGNALS:
        raise TransitionBlocked("fail-safe signal must be a reliability or drift regression")
    if comparator not in _COMPARATORS:
        raise TransitionBlocked("fail-safe comparator must be lt, lte, gt, or gte")
    normalized_threshold = _finite_number(threshold, "threshold")
    authorization = _validate_henry_authorization(human_authorization)
    from nas_server.database import get_conn

    created_at = _now()
    definition = (
        policy_ref,
        signal,
        comparator,
        normalized_threshold,
        authorization["authority"],
        authorization["authorized_at"],
        authorization["decision_ref"],
    )
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM recommendation_fail_safe_policies WHERE policy_ref=?", (policy_ref,)
        ).fetchone()
        if existing:
            persisted = tuple(existing[field] for field in (
                "policy_ref", "signal", "comparator", "threshold", "authority",
                "authorized_at", "decision_ref",
            ))
            if persisted != definition:
                raise TransitionBlocked("fail-safe policy contradicts persisted definition")
            return dict(existing)
        conn.execute(
            """INSERT INTO recommendation_fail_safe_policies
               (policy_ref,signal,comparator,threshold,authority,authorized_at,decision_ref,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (*definition, created_at),
        )
        row = conn.execute(
            "SELECT * FROM recommendation_fail_safe_policies WHERE policy_ref=?", (policy_ref,)
        ).fetchone()
    return dict(row)


def register_treatment(
    treatment_id: str,
    treatment_identity: Mapping[str, Any],
    *,
    requested_by: str,
    reason: str,
) -> dict[str, Any]:
    """Persist a treatment in the explicit ``research_only`` state."""
    if not treatment_id or not requested_by or not reason:
        raise ValueError("treatment_id, requested_by, and reason are required")
    if not isinstance(treatment_identity, Mapping) or not treatment_identity:
        raise ValueError("a structured treatment identity is required")
    if _contains_forbidden_aggregate(treatment_identity):
        raise ValueError("treatment identity cannot contain aggregate winner rates")
    from nas_server.database import get_conn

    created_at = _now()
    identity_json = _canonical(dict(treatment_identity))
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM recommendation_states WHERE treatment_id=?", (treatment_id,)
        ).fetchone()
        if existing:
            if existing["treatment_identity_json"] != identity_json:
                raise ValueError("treatment identity contradicts persisted identity")
            return _render_state(existing)
        conn.execute(
            "INSERT INTO recommendation_states VALUES (?,?,?,?,?)",
            (treatment_id, "research_only", identity_json, 0, created_at),
        )
        conn.execute(
            """INSERT INTO recommendation_transition_events
               (treatment_id,from_state,to_state,revision,requested_by,reason,created_at)
               VALUES (?,NULL,'research_only',0,?,?,?)""",
            (treatment_id, requested_by, reason, created_at),
        )
        row = conn.execute(
            "SELECT * FROM recommendation_states WHERE treatment_id=?", (treatment_id,)
        ).fetchone()
    return _render_state(row)


def transition(
    treatment_id: str,
    to_state: str,
    *,
    requested_by: str,
    reason: str,
    gates: Mapping[str, Any] | None = None,
    human_authorization: Mapping[str, Any] | None = None,
    fail_safe_trigger: Mapping[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Apply one audited transition, failing closed on missing evidence or authority."""
    if to_state not in STATES:
        raise TransitionBlocked(f"unknown recommendation state: {to_state}")
    if not requested_by or not reason:
        raise ValueError("requested_by and reason are required")
    if _contains_forbidden_aggregate((gates, human_authorization, fail_safe_trigger)):
        raise TransitionBlocked("raw aggregate winner rates cannot directly mutate defaults")

    from nas_server.database import get_conn

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM recommendation_states WHERE treatment_id=?", (treatment_id,)
        ).fetchone()
        if not current:
            raise TransitionBlocked("unknown treatment; register it before transitioning")
        if expected_revision is not None and current["revision"] != expected_revision:
            raise TransitionBlocked("stale recommendation revision")
        edge = (current["state"], to_state)
        if edge not in _ADVANCING | _FAIL_SAFE | _HUMAN_ONLY:
            raise TransitionBlocked(f"transition {edge[0]} -> {edge[1]} is not allowed")

        gate_record = None
        authorization_record = None
        trigger_record = None
        if edge in _ADVANCING:
            gate_record = _validate_gates(gates)
        if edge == ("provisional", "production_default") or edge in _HUMAN_ONLY:
            authorization_record = _validate_henry_authorization(human_authorization)
        if edge in _FAIL_SAFE:
            trigger_record = _validate_fail_safe(conn, fail_safe_trigger)

        revision = current["revision"] + 1
        changed_at = _now()
        conn.execute(
            """UPDATE recommendation_states SET state=?,revision=?,updated_at=?
               WHERE treatment_id=? AND revision=?""",
            (to_state, revision, changed_at, treatment_id, current["revision"]),
        )
        conn.execute(
            """INSERT INTO recommendation_transition_events
               (treatment_id,from_state,to_state,revision,gate_evidence_json,
                human_authorization_json,fail_safe_trigger_json,requested_by,reason,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                treatment_id,
                current["state"],
                to_state,
                revision,
                _canonical(gate_record) if gate_record is not None else None,
                _canonical(authorization_record) if authorization_record is not None else None,
                _canonical(trigger_record) if trigger_record is not None else None,
                requested_by,
                reason,
                changed_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM recommendation_states WHERE treatment_id=?", (treatment_id,)
        ).fetchone()
    return _render_state(row)


def get_state(treatment_id: str) -> dict[str, Any] | None:
    from nas_server.database import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM recommendation_states WHERE treatment_id=?", (treatment_id,)
        ).fetchone()
    return _render_state(row) if row else None


def get_history(treatment_id: str) -> list[dict[str, Any]]:
    from nas_server.database import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM recommendation_transition_events
               WHERE treatment_id=? ORDER BY revision""",
            (treatment_id,),
        ).fetchall()
    rendered = []
    for row in rows:
        item = dict(row)
        for field in ("gate_evidence_json", "human_authorization_json", "fail_safe_trigger_json"):
            item[field.removesuffix("_json")] = json.loads(item.pop(field)) if item[field] else None
        rendered.append(item)
    return rendered


def _render_state(row) -> dict[str, Any]:
    item = dict(row)
    item["treatment_identity"] = json.loads(item.pop("treatment_identity_json"))
    return item

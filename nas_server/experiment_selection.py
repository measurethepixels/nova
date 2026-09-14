"""Explicit, fail-closed Experiment Mode winner-selection policy."""
from __future__ import annotations

import json
import uuid

POLICY_REVISION = "winner-selection/1.0.0"
FACT_TYPES = frozenset({
    "evaluator_preference", "human_preference", "comparative_outcome",
    "operational_selection", "fallback",
})


def record_eligibility(experiment_run_id: str, variant_id: str, *, eligible: bool,
                       reasons: list[str]) -> None:
    """Persist a candidate's hard-gate result independently of preferences."""
    if eligible and reasons:
        raise ValueError("eligible candidates cannot have rejection reasons")
    if not eligible and not reasons:
        raise ValueError("ineligible candidates require a rejection reason")
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiment_candidate_eligibility VALUES (?,?,?,?,?,datetime('now'))
               ON CONFLICT(experiment_run_id,declared_variant_id) DO UPDATE SET
               eligible=excluded.eligible,reasons_json=excluded.reasons_json,
               policy_revision=excluded.policy_revision,recorded_at=datetime('now')""",
            (experiment_run_id, variant_id, int(eligible),
             json.dumps(reasons, sort_keys=True), POLICY_REVISION),
        )


def record_fact(experiment_run_id: str, fact_type: str, *, variant_id: str | None,
                reason: str, policy_id: str | None = None,
                payload: dict | None = None) -> None:
    """Persist one independently queryable decision fact; contradictory replay fails."""
    if fact_type not in FACT_TYPES:
        raise ValueError("unknown selection fact type")
    body = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    from nas_server.database import get_conn
    with get_conn() as conn:
        existing = conn.execute(
            """SELECT selected_variant_id,policy_id,reason,payload_json
               FROM experiment_selection_facts WHERE experiment_run_id=? AND fact_type=?""",
            (experiment_run_id, fact_type),
        ).fetchone()
        intended = (variant_id, policy_id, reason, body)
        if existing:
            if tuple(existing) != intended:
                raise ValueError(f"contradictory {fact_type} fact")
            return
        conn.execute(
            """INSERT INTO experiment_selection_facts VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            ("selection_" + uuid.uuid4().hex, experiment_run_id, fact_type,
             variant_id, policy_id, POLICY_REVISION, reason, body),
        )


def choose_operational_selection(eligible_ids: list[str], *,
                                 evaluator_preference: str | None = None,
                                 human_preference: str | None = None,
                                 fallback_policy: dict | None = None) -> dict:
    """Return explicit comparative and operational outcomes without list-order choice."""
    eligible = frozenset(eligible_ids)
    if not eligible:
        return {"ok": False, "comparative_outcome": "no_valid_survivor",
                "reason": "no eligible candidates"}
    if evaluator_preference is not None and evaluator_preference not in eligible:
        raise ValueError("evaluator preference is not eligible")
    if human_preference is not None and human_preference not in eligible:
        raise ValueError("human preference is not eligible")
    if len(eligible) == 1:
        variant = next(iter(eligible))
        return {"ok": True, "variant_id": variant,
                "comparative_outcome": "single_survivor", "source": "survival",
                "reason": "only one candidate passed hard validity gates"}
    if human_preference:
        return {"ok": True, "variant_id": human_preference,
                "comparative_outcome": "human_preference", "source": "human",
                "reason": "blind human preference among eligible candidates"}
    if evaluator_preference:
        return {"ok": True, "variant_id": evaluator_preference,
                "comparative_outcome": "evaluator_preference", "source": "evaluator",
                "reason": "evaluator preference among eligible candidates"}
    if not fallback_policy:
        return {"ok": False, "comparative_outcome": "no_comparative_winner",
                "reason": "evaluator unavailable and no operational fallback is configured"}
    if fallback_policy.get("type") != "fixed_eligible_candidate":
        return {"ok": False, "comparative_outcome": "no_comparative_winner",
                "reason": "operational fallback policy is invalid"}
    variant = fallback_policy.get("variant_id")
    policy_id = fallback_policy.get("policy_id")
    revision = fallback_policy.get("revision")
    if not all(isinstance(v, str) and v for v in (variant, policy_id, revision)):
        return {"ok": False, "comparative_outcome": "no_comparative_winner",
                "reason": "operational fallback identity/revision/candidate is incomplete"}
    if variant not in eligible:
        return {"ok": False, "comparative_outcome": "no_comparative_winner",
                "reason": "operational fallback candidate is not eligible"}
    return {"ok": True, "variant_id": variant,
            "comparative_outcome": "no_comparative_winner", "source": "fallback",
            "policy_id": policy_id, "policy_revision": revision,
            "reason": "evaluator unavailable; authorized operational fallback used"}

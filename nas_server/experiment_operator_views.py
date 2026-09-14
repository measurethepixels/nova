"""Read-only operator projections over the experiment evidence backbone.

The projections in this module do not register decisions or change recommendation or
validation state.  The aggregate projection delegates to Group 8's existing canonical
derivation (including its epoch-membership materialization).  These views keep the
authoritative Group 5/6/8/9/11/12 records visible without inventing a third, collapsed
interpretation of them.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
import json
from typing import Any

from nas_server import experiment_statistics
from nas_server.experiment_aggregates import aggregate_evidence
from nas_server.experiment_compatibility import PROFILE_VERSION, PROFILES, compatibility
from nas_server.handbook_contract import ValidationEvent
from nas_server.promotion_governance import get_history, get_state
from nas_server.validation_ledger import SEED_EVENTS, derive_stamp


_STATISTICS = {
    "within_parent_paired": experiment_statistics.within_parent_paired_effects,
    "same_data_repeatability": experiment_statistics.same_data_repeatability,
    "independent_captures": experiment_statistics.independent_capture_summary,
    "independent_targets": experiment_statistics.independent_target_replication,
    "changed_condition": experiment_statistics.changed_condition_reproducibility,
    "human_ai_reliability": experiment_statistics.human_ai_reliability,
    "effect_size_distribution": experiment_statistics.effect_size_distribution,
    "hierarchical_repeated_measures": experiment_statistics.hierarchical_repeated_measures,
}


@dataclass(frozen=True)
class PromotionStatusView:
    """The recommendation-governance axis; never a validation assertion."""

    section: str
    treatment_id: str
    state: str | None
    revision: int | None
    provenance: dict[str, Any]
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ValidationStatusView:
    """The claim-validation axis; never a promotion recommendation."""

    section: str
    claim_id: str
    stamp: str
    reason: str
    checks_complete: bool
    provenance: dict[str, Any]


def _json_field(row: dict[str, Any], field: str) -> None:
    raw = row.pop(field)
    row[field.removesuffix("_json")] = json.loads(raw) if raw is not None else None


def presentation_decision_view(presentation_id: str) -> dict[str, Any] | None:
    """Return AI attempts and append-only human decisions side by side.

    Disagreement is derived only when both sides name a variant.  Missing, failed,
    or unparseable decisions remain visibly not comparable rather than becoming
    agreement by omission.
    """
    from nas_server.database import get_conn

    with get_conn() as conn:
        presentation_row = conn.execute(
            "SELECT * FROM comparison_presentations WHERE presentation_id=?",
            (presentation_id,),
        ).fetchone()
        if not presentation_row:
            return None
        attempt_rows = conn.execute(
            "SELECT * FROM evaluator_attempts WHERE presentation_id=? ORDER BY attempt_ordinal",
            (presentation_id,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM human_review_events WHERE presentation_id=? ORDER BY event_sequence",
            (presentation_id,),
        ).fetchall()

    presentation = dict(presentation_row)
    _json_field(presentation, "renderer_settings_json")
    attempts = []
    for source in attempt_rows:
        item = dict(source)
        _json_field(item, "evaluator_settings_json")
        _json_field(item, "parsed_result_json")
        attempts.append(item)
    events = []
    for source in event_rows:
        item = dict(source)
        _json_field(item, "payload_json")
        events.append(item)

    successful = [a for a in attempts if a["status"] == "succeeded"]
    ai_result = successful[-1]["parsed_result"] if successful else None
    ai_choice = None
    if isinstance(ai_result, dict):
        for key in ("selected_variant_id", "winner", "chosen_variant_id"):
            if ai_result.get(key) is not None:
                ai_choice = ai_result[key]
                break
    human_decisions = [e for e in events if e["selected_variant_id"] is not None]
    human_choice = human_decisions[-1]["selected_variant_id"] if human_decisions else None
    disagree = ai_choice != human_choice if ai_choice is not None and human_choice is not None else None

    return {
        "presentation": presentation,
        "evaluator_attempts": attempts,
        "human_review_events": events,
        "decision_comparison": {
            "ai_selected_variant_id": ai_choice,
            "human_selected_variant_id": human_choice,
            "disagreement": disagree,
            "comparison_status": (
                "disagree" if disagree is True else "agree" if disagree is False else "not_comparable"
            ),
        },
    }


def compatibility_stratification_view(
    run_ids: Sequence[str], claim_kinds: Sequence[str] | None = None
) -> dict[str, Any]:
    """Show persisted epoch membership and canonical pairwise split causes."""
    ordered = list(dict.fromkeys(run_ids))
    kinds = list(claim_kinds or PROFILES)
    unknown = sorted(set(kinds) - set(PROFILES))
    if unknown:
        raise ValueError("unknown claim kind: " + ", ".join(unknown))
    from nas_server.database import get_conn

    memberships: list[dict[str, Any]] = []
    with get_conn() as conn:
        for claim_kind in kinds:
            for run_id in ordered:
                row = conn.execute(
                    """SELECT p.profile_id,p.profile_version,em.evidence_epoch,
                              em.split_causes_json
                       FROM evidence_compatibility_descriptors d
                       LEFT JOIN compatibility_profiles p
                         ON p.claim_kind=? AND p.profile_version=?
                       LEFT JOIN evidence_epoch_members em
                         ON em.descriptor_id=d.descriptor_id AND em.profile_id=p.profile_id
                       WHERE d.experiment_run_id=?
                       LIMIT 1""",
                    (claim_kind, PROFILE_VERSION, run_id),
                ).fetchone()
                has_epoch = bool(row and row["evidence_epoch"])
                split_causes = (
                    json.loads(row["split_causes_json"])
                    if has_epoch else []
                )
                memberships.append({
                    "experiment_run_id": run_id,
                    "claim_kind": claim_kind,
                    "compatibility_profile_id": row["profile_id"] if row else None,
                    "compatibility_profile_version": row["profile_version"] if row else None,
                    "evidence_epoch": row["evidence_epoch"] if row else None,
                    "membership_status": "assigned" if has_epoch else "unassigned",
                    "split_causes": split_causes,
                    "poolable": bool(has_epoch and not split_causes),
                })

    pairs = []
    for claim_kind in kinds:
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                result = compatibility(left, right, claim_kind)
                pairs.append({"left_run_id": left, "right_run_id": right, **result})
    return {"memberships": memberships, "pairwise_compatibility": pairs}


def aggregate_operator_view(run_ids: Sequence[str], claim_kind: str) -> dict[str, Any]:
    """Expose Group 8 denominators with an explicit profile label on every epoch."""
    result = aggregate_evidence(list(run_ids), claim_kind)
    from nas_server.database import get_conn

    with get_conn() as conn:
        profile = conn.execute(
            """SELECT profile_id,profile_version FROM compatibility_profiles
               WHERE claim_kind=? AND profile_version=?""",
            (claim_kind, PROFILE_VERSION),
        ).fetchone()
    if not profile:
        raise ValueError("compatibility profile was not produced")
    for stratum in result["compatibility_strata"]:
        stratum["compatibility_profile_id"] = profile["profile_id"]
        stratum["compatibility_profile_version"] = profile["profile_version"]
    return result


def repeated_evidence_view(
    kind: str,
    rows: Sequence[dict[str, Any]],
    *,
    profile_id: str,
    evidence_epoch: str,
    on_invalid: str = "exclude",
) -> dict[str, Any]:
    """Dispatch one of Group 8's eight guarded, epoch-scoped summaries."""
    try:
        summary = _STATISTICS[kind](
            rows, profile_id=profile_id, evidence_epoch=evidence_epoch, on_invalid=on_invalid
        )
    except KeyError as exc:
        raise ValueError("unknown repeated-evidence summary") from exc
    return {
        "compatibility_profile_id": profile_id,
        "evidence_epoch": evidence_epoch,
        "summary": summary,
    }


def recommendation_validation_view(
    treatment_id: str,
    claim_id: str,
    *,
    events: Iterable[ValidationEvent] = SEED_EVENTS,
    current_claim_hash: str | None = None,
) -> dict[str, Any]:
    """Render recommendation and validation as independent, typed sections."""
    state = get_state(treatment_id)
    history = tuple(get_history(treatment_id)) if state else ()
    promotion = PromotionStatusView(
        section="Promotion status",
        treatment_id=treatment_id,
        state=state["state"] if state else None,
        revision=state["revision"] if state else None,
        provenance={
            "source": "promotion_governance",
            "history_ref": f"promotion_governance:treatment:{treatment_id}",
            "state_updated_at": state["updated_at"] if state else None,
            "history_available": bool(history),
        },
        history=history,
    )
    event_values = tuple(events)
    stamp = derive_stamp(claim_id, event_values, current_claim_hash=current_claim_hash)
    latest = stamp.latest_event
    matching_event_ids = tuple(
        event.event_id for event in event_values if event.claim_id == claim_id
    )
    validation = ValidationStatusView(
        section="Validation status",
        claim_id=claim_id,
        stamp=stamp.status,
        reason=stamp.reason,
        checks_complete=stamp.checks_complete,
        provenance={
            "source": "validation_ledger",
            "history_ref": f"validation_ledger:claim:{claim_id}",
            "history_event_ids": matching_event_ids,
            "latest_event_id": latest.event_id if latest else None,
            "validated_on": latest.validated_on.isoformat() if latest else None,
            "validator": latest.validator if latest else None,
        },
    )
    return {
        "promotion_status": asdict(promotion),
        "validation_status": asdict(validation),
        "summary": f"Promotion: {promotion.state or 'unregistered'}. Validation: {validation.stamp}.",
    }

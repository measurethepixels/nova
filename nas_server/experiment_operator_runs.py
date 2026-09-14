"""Read-only operator projections for experiment run and artifact lifecycles."""
from __future__ import annotations

import json
from typing import Any


def _decode(row: Any, *fields: str) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        raw = result.pop(field, None)
        result[field.removesuffix("_json")] = json.loads(raw) if raw is not None else None
    return result


def list_experiment_runs() -> list[dict[str, Any]]:
    """Return registered runs with independent runtime and evidence counts."""
    from nas_server.database import get_conn

    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT r.experiment_run_id,r.process_family,r.question,r.intent,
                      r.created_at,rt.state AS runtime_state,
                      COUNT(DISTINCT rr.candidate_definition_id) AS roster_count,
                      COUNT(DISTINCT ca.candidate_attempt_id) AS attempt_count,
                      COUNT(DISTINCT a.run_artifact_id) AS artifact_count
               FROM experiment_runs r
               LEFT JOIN experiment_run_runtime rt USING (experiment_run_id)
               LEFT JOIN experiment_run_roster rr USING (experiment_run_id)
               LEFT JOIN candidate_attempts ca USING (experiment_run_id)
               LEFT JOIN experiment_run_artifacts a USING (experiment_run_id)
               GROUP BY r.experiment_run_id
               ORDER BY r.created_at DESC,r.experiment_run_id DESC"""
        )]


def experiment_run_view(experiment_run_id: str) -> dict[str, Any] | None:
    """Return a complete, non-collapsed operator view for one registered run."""
    from nas_server.database import get_conn
    from nas_server.experiment_artifacts import discover_orphans
    from nas_server.legacy_experiment_evidence import legacy_evidence_report

    with get_conn() as conn:
        source = conn.execute(
            """SELECT r.*,ar.content_digest AS parent_content_digest,
                      ar.locator AS parent_locator,rt.state AS runtime_state,
                      rt.run_root,rt.selected_artifact_id,rt.winner_projection_path,
                      rt.updated_at AS runtime_updated_at,l.owner_id AS lease_owner,
                      l.lease_expires_at
               FROM experiment_runs r
               JOIN artifact_revisions ar
                 ON ar.artifact_revision_id=r.parent_artifact_revision_id
               LEFT JOIN experiment_run_runtime rt USING (experiment_run_id)
               LEFT JOIN experiment_run_leases l USING (experiment_run_id)
               WHERE r.experiment_run_id=?""",
            (experiment_run_id,),
        ).fetchone()
        if not source:
            return None
        roster_rows = conn.execute(
            """SELECT rr.*,d.declared_variant_id,d.definition_revision,d.definition_json
               FROM experiment_run_roster rr JOIN candidate_definitions d
                 USING (candidate_definition_id)
               WHERE rr.experiment_run_id=? ORDER BY rr.declared_ordinal""",
            (experiment_run_id,),
        ).fetchall()
        attempt_rows = conn.execute(
            """SELECT ca.*,d.declared_variant_id,t.resolution_status,
                      t.no_op_semantics,t.treatment_json
               FROM candidate_attempts ca JOIN candidate_definitions d
                 USING (candidate_definition_id)
               LEFT JOIN effective_treatments t USING (effective_treatment_id)
               WHERE ca.experiment_run_id=?
               ORDER BY d.declared_variant_id,ca.attempt_ordinal""",
            (experiment_run_id,),
        ).fetchall()
        artifact_rows = conn.execute(
            """SELECT * FROM experiment_run_artifacts WHERE experiment_run_id=?
               ORDER BY created_at,run_artifact_id""", (experiment_run_id,),
        ).fetchall()
        journal_rows = conn.execute(
            """SELECT * FROM experiment_run_journal WHERE experiment_run_id=?
               ORDER BY event_sequence""", (experiment_run_id,),
        ).fetchall()
        measurement_rows = conn.execute(
            """SELECT a.run_artifact_id,a.declared_variant_id,p.measurement_plan_id,
                      p.plan_version,o.measurand,o.value_json,o.status,
                      o.units,o.applicability_reason,o.uncertainty_json
               FROM experiment_run_artifacts a
               LEFT JOIN measurement_observations o USING (run_artifact_id)
               LEFT JOIN measurement_plans p USING (measurement_plan_id)
               WHERE a.experiment_run_id=? ORDER BY a.run_artifact_id,o.measurand""",
            (experiment_run_id,),
        ).fetchall()
        gate_rows = conn.execute(
            """SELECT g.*,a.declared_variant_id FROM measurement_gate_results g
               JOIN experiment_run_artifacts a USING (run_artifact_id)
               WHERE a.experiment_run_id=? ORDER BY g.created_at""",
            (experiment_run_id,),
        ).fetchall()
        eligibility_rows = conn.execute(
            """SELECT * FROM experiment_candidate_eligibility
               WHERE experiment_run_id=? ORDER BY declared_variant_id""",
            (experiment_run_id,),
        ).fetchall()
        selection_rows = conn.execute(
            """SELECT * FROM experiment_selection_facts
               WHERE experiment_run_id=? ORDER BY created_at,selection_fact_id""",
            (experiment_run_id,),
        ).fetchall()

    attempts = [_decode(row, "transition_log_json", "treatment_json") for row in attempt_rows]
    artifacts = [dict(row) for row in artifact_rows]
    measurements = [_decode(row, "value_json", "uncertainty_json") for row in measurement_rows]
    missing_measurements = [
        {"run_artifact_id": row["run_artifact_id"],
         "declared_variant_id": row["declared_variant_id"],
         "reason": "artifact has no measurement observation"}
        for row in measurements if row["measurement_plan_id"] is None
    ]
    artifact_variants = {row["declared_variant_id"] for row in artifacts
                         if row["artifact_role"] == "candidate_output"}
    latest_attempt = {}
    for row in attempts:
        latest_attempt[row["declared_variant_id"]] = row
    for roster in roster_rows:
        variant = roster["declared_variant_id"]
        if variant in artifact_variants:
            continue
        attempt = latest_attempt.get(variant)
        reason = "candidate has no attempt or candidate artifact"
        if attempt:
            detail = attempt.get("error_message") or attempt["artifact_status"]
            reason = f"candidate artifact absent: {attempt['execution_status']} ({detail})"
        missing_measurements.append({
            "run_artifact_id": None, "declared_variant_id": variant, "reason": reason,
        })
    failures = [
        {"candidate_attempt_id": row["candidate_attempt_id"],
         "declared_variant_id": row["declared_variant_id"],
         "execution_status": row["execution_status"],
         "assessment_status": row["assessment_status"],
         "error_kind": row["error_kind"], "error_message": row["error_message"]}
        for row in attempts
        if row["execution_status"] in {"failed", "timed_out", "not_attempted"}
        or row["assessment_status"] in {"rejected", "failed", "timed_out"}
    ]
    legacy = legacy_evidence_report()
    legacy_groups = [g for g in legacy["groups"] if g["experiment_run_id"] == experiment_run_id]
    try:
        orphans = discover_orphans(experiment_run_id) if source["run_root"] else []
        orphan_error = None
    except OSError as exc:
        orphans, orphan_error = [], str(exc)
    return {
        "run": dict(source),
        "timeline": [_decode(row, "payload_json") for row in journal_rows],
        "roster": [_decode(row, "definition_json") for row in roster_rows],
        "attempts": attempts,
        "effective_treatments": [
            {key: row[key] for key in ("candidate_attempt_id", "declared_variant_id",
             "effective_treatment_id", "resolution_status", "no_op_semantics", "treatment")}
            for row in attempts
        ],
        "failures_and_rejections": failures,
        "control_status": [
            {"declared_variant_id": row["declared_variant_id"],
             "control_role": row["control_role"], "control_role_version": row["control_role_version"]}
            for row in [_decode(item, "definition_json") for item in roster_rows]
        ],
        "artifacts": artifacts,
        "artifact_edges": [
            {"source_artifact_id": row["source_artifact_id"], "run_artifact_id": row["run_artifact_id"]}
            for row in artifacts if row["source_artifact_id"]
        ],
        "measurements": measurements,
        "measurement_gates": [dict(row) for row in gate_rows],
        "eligibility": [_decode(row, "reasons_json") for row in eligibility_rows],
        "selection_facts": [_decode(row, "payload_json") for row in selection_rows],
        "missing_measurements": missing_measurements,
        "legacy_evidence": legacy_groups or [{"experiment_run_id": experiment_run_id,
                                                "reconstruction_level": "unavailable", "rows": []}],
        "orphans": orphans,
        "orphan_scan_error": orphan_error,
    }

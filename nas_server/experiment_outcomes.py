"""Canonical run-outcome and denominator derivation from durable evidence."""
from __future__ import annotations

import json

CONTRACT_VERSION = "run-outcome/1.0.0"


def derive_run_outcome(experiment_run_id: str, *, persist: bool = True,
                       integrity_status: str = "active") -> dict:
    if integrity_status not in ("active", "invalidated", "superseded"):
        raise ValueError("invalid integrity status")
    from nas_server.database import get_conn
    with get_conn() as conn:
        run = conn.execute("""SELECT r.*,rt.state,rt.selected_artifact_id
            FROM experiment_runs r LEFT JOIN experiment_run_runtime rt USING(experiment_run_id)
            WHERE r.experiment_run_id=?""", (experiment_run_id,)).fetchone()
        if not run:
            raise ValueError("unknown experiment run")
        rows = conn.execute("""SELECT a.*,r.candidate_definition_id AS roster_candidate_definition_id,
            r.control_role,t.no_op_semantics
            FROM experiment_run_roster r LEFT JOIN candidate_attempts a
              ON a.experiment_run_id=r.experiment_run_id AND a.candidate_definition_id=r.candidate_definition_id
            LEFT JOIN effective_treatments t ON t.effective_treatment_id=a.effective_treatment_id
            WHERE r.experiment_run_id=? ORDER BY r.declared_ordinal,a.attempt_ordinal DESC""", (experiment_run_id,)).fetchall()
        latest = {}
        for row in rows:
            latest.setdefault(row["roster_candidate_definition_id"], row)
        attempts = list(latest.values())
        selected = next((a for a in attempts if a["comparison_status"] == "selected"), None)
        survivors = [a for a in attempts if a["execution_status"] == "succeeded" and a["assessment_status"] != "rejected"]
        controls = [a for a in attempts if a["control_role"] in ("no_treatment", "incumbent", "reference")]
        control_available = not controls or any(a["execution_status"] == "succeeded" for a in controls)
        evaluator = conn.execute("""SELECT e.status FROM comparison_presentations p
            LEFT JOIN evaluator_attempts e USING(presentation_id)
            WHERE p.experiment_run_id=? AND e.status IS NOT NULL
            ORDER BY p.created_at DESC,e.attempt_ordinal DESC LIMIT 1""", (experiment_run_id,)).fetchone()
        if integrity_status != "active": kind = integrity_status
        elif run["state"] == "interrupted": kind = "aborted_or_retry_requested"
        elif not attempts or all(a["execution_status"] in (None,"failed","timed_out","not_attempted") for a in attempts): kind = "all_execution_failed"
        elif attempts and all(a["assessment_status"] == "rejected" for a in attempts if a["execution_status"] == "succeeded"): kind = "all_analytically_rejected"
        elif not survivors: kind = "no_valid_survivor"
        elif len(survivors) == 1: kind = "single_survivor"
        elif evaluator and evaluator["status"] in ("failed", "timed_out", "comparison_not_performed"): kind = "evaluator_unavailable"
        elif not control_available: kind = "control_unavailable"
        elif selected and (selected["control_role"] == "no_treatment" or selected["no_op_semantics"] in ("declared_no_treatment","runtime_no_op")): kind = "no_treatment_selected"
        elif selected and selected["control_role"] == "incumbent": kind = "incumbent_selected"
        elif selected: kind = "valid_comparative_selection"
        else: kind = "inconclusive"
        comparative = kind in ("valid_comparative_selection","no_treatment_selected","incumbent_selected") and control_available
        result = {"experiment_run_id": experiment_run_id, "outcome_kind": kind,
                  "declared_attempts": len(attempts), "valid_survivors": len(survivors),
                  "control_available": control_available, "comparative_evidence": comparative,
                  "denominator_eligible": comparative,
                  "claim_scope": "comparative" if comparative else ("operational_only" if selected else "none"),
                  "operational_selection_artifact_id": run["selected_artifact_id"]}
        if persist:
            conn.execute("""INSERT INTO experiment_run_outcomes VALUES (?,?,?,?,?,?,?, ?,datetime('now'))
                ON CONFLICT(experiment_run_id) DO UPDATE SET outcome_kind=excluded.outcome_kind,
                operational_selection_artifact_id=excluded.operational_selection_artifact_id,
                comparative_evidence=excluded.comparative_evidence,denominator_eligible=excluded.denominator_eligible,
                claim_scope=excluded.claim_scope,outcome_json=excluded.outcome_json,
                outcome_contract_version=excluded.outcome_contract_version,derived_at=datetime('now')""",
                (experiment_run_id,kind,run["selected_artifact_id"],int(comparative),int(comparative),result["claim_scope"],json.dumps(result,sort_keys=True),CONTRACT_VERSION))
        return result


def denominator_summary(run_ids: list[str]) -> dict:
    outcomes = [derive_run_outcome(run_id) for run_id in run_ids]
    return {"declared_runs": len(outcomes), "eligible_comparisons": sum(o["denominator_eligible"] for o in outcomes),
            "outcomes": outcomes}

"""Versioned analytic measurement plans and evidence-weaker gate semantics."""
from __future__ import annotations

import hashlib
import json
import uuid

from nas_server.experiment_evidence import RegistrationConflict

PLAN_VERSION = "measurement-plan/1.0.0"
METHOD_VERSION = "step-assessor/1.1.0"
RULE_VERSION = "analytic-rejection/1.1.0"
_COLOR_PROCESSES = frozenset({
    "color_calibration", "color_saturation", "scnr", "sky_green_rebalance",
})
_UNITS = {
    "fwhm_before": "pixel", "fwhm_after": "pixel", "fwhm_delta_pct": "percent",
    "clip_lo_pct": "fraction", "clip_hi_pct": "fraction", "ssim": "unitless",
    "snr_before": "ratio", "snr_after": "ratio", "entropy_before": "bit",
    "entropy_after": "bit",
    "ringing_score": "sigma",
}


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _plan(process_family: str) -> dict:
    return {
        "process_family": process_family,
        "intended_effect_measurands": "step-assessor process-specific metrics",
        "preservation_obligations": ["no destructive clipping", "structure retention"],
        "artifact_checks": ["finalized", "digest-bound"],
        "method": "nas_server.step_assessor.assess_step",
        "method_version": METHOD_VERSION,
        "valid_state": "input/output process state pair",
        "support": "same registered artifact population",
        "roi": "full-frame mono projection unless metric declares otherwise",
        "population": "pixels and detected stars in the registered artifact",
        "decision_rule": {"id": "existing-analytic-rejection",
                          "version": RULE_VERSION},
    }


def record_measurement_bundle(
    *, run_artifact_id: str, process_family: str, metrics: dict,
    state_context: str = "process-output",
    support_context: str = "full-frame-same-support",
    roi_context: str = "full-frame",
    population_context: str = "mono-collapsed-pixels",
    support_compatible: bool = True,
    uncertainty: dict | None = None,
    method: str | None = None,
    method_version: str | None = None,
    decision_rule: dict | None = None,
) -> tuple[str, str]:
    """Persist metrics and a gate without strengthening current selection authority."""
    plan = _plan(process_family)
    if method is not None:
        plan["method"] = method
    recorded_method_version = method_version or METHOD_VERSION
    plan["method_version"] = recorded_method_version
    if decision_rule is not None:
        plan["decision_rule"] = decision_rule
    plan_json = _canonical(plan)
    fingerprint = "sha256:" + hashlib.sha256(plan_json.encode()).hexdigest()
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        artifact = conn.execute(
            "SELECT finalization_status FROM experiment_run_artifacts "
            "WHERE run_artifact_id=?", (run_artifact_id,),
        ).fetchone()
        if not artifact or artifact["finalization_status"] != "finalized":
            raise RegistrationConflict("measurements require finalized artifact identity")
        row = conn.execute(
            "SELECT measurement_plan_id FROM measurement_plans WHERE plan_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row:
            plan_id = str(row["measurement_plan_id"])
        else:
            plan_id = _id("measure_")
            conn.execute(
                """INSERT INTO measurement_plans
                   (measurement_plan_id,plan_fingerprint,process_family,plan_version,plan_json)
                   VALUES (?,?,?,?,?)""",
                (plan_id, fingerprint, process_family, PLAN_VERSION, plan_json),
            )
        observed = {key: value for key, value in metrics.items()
                    if key != "analytically_failed"}
        valid_count = 0
        for measurand, value in observed.items():
            reason = None
            if value is None:
                status = "unavailable"
                reason = "assessor returned no value"
            elif not support_compatible:
                status = "invalid"
                reason = "input/output support is incompatible for comparative use"
            elif process_family in _COLOR_PROCESSES:
                status = "invalid"
                reason = "mono-collapsed generic metric cannot establish color validity"
            else:
                status = "valid"
                valid_count += 1
            payload = (
                _id("observation_"), plan_id, run_artifact_id, measurand,
                None if value is None else _canonical(value), status, recorded_method_version,
                _UNITS.get(measurand, "unitless"), state_context, support_context,
                roi_context, population_context,
                _canonical(uncertainty) if uncertainty else None, reason,
            )
            existing = conn.execute(
                """SELECT value_json,status FROM measurement_observations
                   WHERE measurement_plan_id=? AND run_artifact_id=? AND measurand=?""",
                (plan_id, run_artifact_id, measurand),
            ).fetchone()
            if existing:
                if (existing["value_json"], existing["status"]) != (payload[4], status):
                    raise RegistrationConflict("measurement replay contradicts prior evidence")
                continue
            conn.execute(
                """INSERT INTO measurement_observations
                   (measurement_observation_id,measurement_plan_id,run_artifact_id,
                    measurand,value_json,status,method_version,units,state_context,
                    support_context,roi_context,population_context,uncertainty_json,
                    applicability_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
        if not observed:
            gate, rationale = "not_applicable", "measurement plan produced no measurands"
        elif valid_count == 0:
            gate, rationale = "indeterminate", "no valid applicable measurements"
        elif metrics.get("analytically_failed") is True:
            gate, rationale = "fail", "existing analytic rejection rule triggered"
        else:
            gate, rationale = "pass", "valid measurements did not trigger rejection"
        existing_gate = conn.execute(
            """SELECT measurement_gate_result_id,gate_result FROM measurement_gate_results
               WHERE measurement_plan_id=? AND run_artifact_id=?""",
            (plan_id, run_artifact_id),
        ).fetchone()
        if existing_gate:
            if existing_gate["gate_result"] != gate:
                raise RegistrationConflict("measurement gate replay contradicts prior result")
            return plan_id, str(existing_gate["measurement_gate_result_id"])
        gate_id = _id("gate_")
        conn.execute(
            """INSERT INTO measurement_gate_results
               (measurement_gate_result_id,measurement_plan_id,run_artifact_id,
                decision_rule_id,decision_rule_version,gate_result,rationale)
               VALUES (?,?,?,?,?,?,?)""",
            (gate_id, plan_id, run_artifact_id,
             plan["decision_rule"]["id"], plan["decision_rule"]["version"],
             gate, rationale),
        )
        return plan_id, gate_id

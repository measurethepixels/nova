"""Versioned, evidence-only quality synthesis with no production authority.

The optional production caller writes shadow evidence only.  Nothing in this
module reads or changes ``final_scores``, notifications, or pipeline decisions.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from nas_server.grading_evidence import SYNTHESIS_CONTRACT_VERSION, _canonical


@dataclass(frozen=True)
class Ceiling:
    name: str
    metric: str
    statuses: tuple[str, ...]
    maximum: float


@dataclass(frozen=True)
class SynthesisPolicy:
    version: str
    required_dimensions: tuple[str, ...]
    weights: tuple[tuple[str, float], ...]
    ceilings: tuple[Ceiling, ...]
    dimension_residual_bound: float = 0.5
    overall_residual_bound: float = 0.25


POLICY = SynthesisPolicy(
    version="quality-synthesis/1.0.0-shadow",
    required_dimensions=("noise", "gradient", "stars", "stretch", "color",
                         "dynamic_range", "detail_preservation", "composition"),
    weights=(("noise", .75), ("gradient", .75), ("stars", 1.0),
             ("stretch", 1.0), ("color", .75), ("dynamic_range", 1.0),
             ("detail_preservation", 1.0), ("composition", .5)),
    ceilings=(
        Ceiling("severe_extended_clipping", "highlight_clip_fraction", ("severe", "fail"), 5),
        Ceiling("lost_or_cropped_primary_target", "primary_target_integrity", ("lost", "cropped", "fail"), 3),
        Ceiling("severe_global_star_trailing", "star_trailing", ("severe", "fail"), 5),
        Ceiling("severe_detail_destruction", "detail_preservation", ("severe", "fail"), 5),
    ),
)


def _dimension(row: dict) -> str:
    context = row.get("context") or {}
    method = str(row.get("method", "")).split("/", 1)[0]
    method_dimensions = {"star-roundness": "stars", "dynamic-range": "dynamic_range",
                         "detail": "detail_preservation"}
    return str(context.get("dimension") or method_dimensions.get(method, method)
               or row.get("metric"))


def _derived_residuals(observation: dict) -> tuple[dict[str, float], float]:
    """Convert allowed categorical observations to bounded residual proposals."""
    structured = observation.get("observation") or {}
    dimensions: dict[str, float] = {}
    severity_value = {"slight": -.1, "moderate": -.25, "severe": -.5}
    artifact_dimension = {"trailing": "stars", "ringing": "detail_preservation",
                          "gradient": "gradient", "color_cast": "color",
                          "clipping": "dynamic_range"}
    for artifact in structured.get("artifacts", []):
        dimension = artifact_dimension.get(artifact.get("type"))
        if dimension:
            value = severity_value.get(artifact.get("severity"), 0) * float(
                artifact.get("confidence", 0))
            dimensions[dimension] = min(dimensions.get(dimension, 0), value)
    texture = structured.get("texture", {})
    if texture.get("status") in {"over_smoothed", "over_sharpened"}:
        dimensions["detail_preservation"] = min(
            dimensions.get("detail_preservation", 0), -.5 * float(texture.get("confidence", 0)))
    color = structured.get("color_plausibility", {})
    if color.get("status") in {"blue_cast", "red_cast"}:
        dimensions["color"] = min(dimensions.get("color", 0),
                                  -.5 * float(color.get("confidence", 0)))
    composition = structured.get("composition", {})
    overall = 0.0
    if composition.get("status") in {"unbalanced", "cropped"}:
        dimensions["composition"] = -.5 * float(composition.get("confidence", 0))
        overall = -.25 * float(composition.get("confidence", 0))
    return dimensions, overall


def synthesize(measurements: list[dict], *, observations: list[dict] | None = None,
               context: dict | None = None, policy: SynthesisPolicy = POLICY) -> dict:
    """Return deterministic score evidence from already-calibrated inputs."""
    context = dict(context or {})
    allowed_context = {key: context[key] for key in
                       ("object_type", "morphology", "data_kind", "filter", "frame_fill")
                       if context.get(key) is not None}
    context_conflicts = []
    for key in ("object_type", "morphology", "data_kind", "filter", "frame_fill"):
        values = {row.get("context", {}).get(key) for row in measurements
                  if row.get("context", {}).get(key) is not None}
        if key in allowed_context:
            values.add(allowed_context[key])
        if len(values) > 1:
            context_conflicts.append(key)
    by_dimension: dict[str, tuple[dict, dict]] = {}
    unknown = set(policy.required_dimensions)
    for measurement in sorted(measurements, key=lambda r: r["measurement_id"]):
        if measurement.get("integrity") != "valid":
            continue
        valid = [c for c in measurement.get("calibrations", [])
                 if c.get("grade") is not None and c.get("applicability") != "not_applicable"]
        if not valid:
            continue
        calibration = sorted(valid, key=lambda c: (c["calibration_version"], c["calibration_id"]))[-1]
        dimension = _dimension(measurement)
        if dimension in dict(policy.weights):
            by_dimension[dimension] = (measurement, calibration)
            unknown.discard(dimension)

    explanation = {"active_ceilings": [], "unknown_dimensions": sorted(unknown),
                   "context_conflicts": context_conflicts,
                   "calibration_versions": [], "vision_residuals": []}
    measurement_ids = sorted(r["measurement_id"] for r, _ in by_dimension.values())
    calibration_ids = sorted(c["calibration_id"] for _, c in by_dimension.values())
    observation_ids: list[str] = []
    if not by_dimension or context_conflicts:
        return {"status": ("indeterminate" if context_conflicts else "insufficient_evidence"),
                "display_score": None,
                "interval": None, "confidence": 0.0, "context": allowed_context,
                "measurement_ids": [], "calibration_ids": [], "observation_ids": [],
                "explanation": explanation}

    weights = dict(policy.weights)
    total = sum(weights[d] for d in by_dimension)
    confidence = sum(float(c["confidence"]) * weights[d]
                     for d, (_, c) in by_dimension.items()) / total
    explanation["calibration_versions"] = sorted({c["calibration_version"]
                                                   for _, c in by_dimension.values()})

    dimension_residuals = {dimension: 0.0 for dimension in by_dimension}
    overall_residual = 0.0
    for observation in sorted(observations or [], key=lambda r: r["observation_id"]):
        observation_ids.append(observation["observation_id"])
        contradicted = any(c.get("vision_weight") == 0 for c in observation.get("contradictions", []))
        derived_dimensions, derived_overall = _derived_residuals(observation)
        residual = float(observation.get("overall_residual", derived_overall))
        applied = 0.0 if contradicted else max(-policy.overall_residual_bound,
                                               min(policy.overall_residual_bound, residual))
        overall_residual += applied
        detail = {"observation_id": observation["observation_id"],
                  "proposed_overall": residual, "applied_overall": applied,
                  "dimensions": {}}
        proposals = observation.get("dimension_residuals", derived_dimensions)
        for dimension, proposed in sorted(proposals.items()):
            if dimension not in dimension_residuals:
                continue
            bounded = (0.0 if contradicted else
                       max(-policy.dimension_residual_bound,
                           min(policy.dimension_residual_bound, float(proposed))))
            dimension_residuals[dimension] += bounded
            detail["dimensions"][dimension] = {"proposed": proposed, "applied": bounded}
        explanation["vision_residuals"].append(detail)
    adjusted_base = sum(
        (float(calibration["grade"]) + max(-policy.dimension_residual_bound,
                                            min(policy.dimension_residual_bound,
                                                dimension_residuals[dimension])))
        * weights[dimension]
        for dimension, (_, calibration) in by_dimension.items()) / total
    score = max(1.0, min(10.0, adjusted_base + max(-policy.overall_residual_bound,
                                         min(policy.overall_residual_bound, overall_residual))))
    active_maximum = 10.0
    for ceiling in policy.ceilings:
        for measurement in measurements:
            if (measurement.get("integrity") != "valid"
                    or measurement.get("metric") != ceiling.metric):
                continue
            triggers = [c for c in measurement.get("calibrations", [])
                        if c.get("status") in ceiling.statuses
                        and c.get("applicability") != "not_applicable"]
            if triggers:
                trigger = sorted(triggers, key=lambda c: c["calibration_id"])[-1]
                if measurement["measurement_id"] not in measurement_ids:
                    measurement_ids.append(measurement["measurement_id"])
                if trigger["calibration_id"] not in calibration_ids:
                    calibration_ids.append(trigger["calibration_id"])
                score = min(score, ceiling.maximum)
                active_maximum = min(active_maximum, ceiling.maximum)
                explanation["active_ceilings"].append({
                    "class": ceiling.name, "maximum": ceiling.maximum,
                    "measurement_id": measurement["measurement_id"],
                    "calibration_id": trigger["calibration_id"]})
    missing_fraction = len(unknown) / len(policy.required_dimensions)
    half_width = min(4.5, .25 + 2.0 * missing_fraction + (1.0 - confidence))
    status = "computed" if not unknown else "insufficient_evidence"
    return {"status": status, "display_score": round(score, 4),
            "interval": [round(max(1, score-half_width), 4),
                         round(min(active_maximum, score+half_width), 4)],
            "confidence": round(confidence * (1-missing_fraction), 4),
            "context": allowed_context, "measurement_ids": sorted(measurement_ids),
            "calibration_ids": sorted(calibration_ids), "observation_ids": observation_ids,
            "explanation": explanation}


def record_synthesis(*, operation_key: str, artifact_sha256: str, result: dict,
                     policy: SynthesisPolicy = POLICY) -> str:
    """Append an idempotent result bound to its exact policy and evidence set."""
    evidence = {key: result[key] for key in
                ("measurement_ids", "calibration_ids", "observation_ids")}
    evidence_hash = hashlib.sha256(_canonical(evidence).encode()).hexdigest()
    fields = {"artifact_sha256": artifact_sha256, "synthesis_version": policy.version,
              "status": result["status"], "display_score": result["display_score"],
              "interval_json": None if result["interval"] is None else _canonical(result["interval"]),
              "confidence": result["confidence"], "context_json": _canonical(result["context"]),
              "measurement_ids_json": _canonical(result["measurement_ids"]),
              "calibration_ids_json": _canonical(result["calibration_ids"]),
              "observation_ids_json": _canonical(result["observation_ids"]),
              "evidence_hash": evidence_hash, "explanation_json": _canonical(result["explanation"]),
              "contract_version": SYNTHESIS_CONTRACT_VERSION}
    from nas_server.database import get_conn
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM quality_synthesis_results WHERE operation_key=?",
                                (operation_key,)).fetchone()
        if existing:
            if any(existing[key] != value for key, value in fields.items()):
                raise ValueError("synthesis operation contradicts prior evidence")
            return str(existing["synthesis_id"])
        synthesis_id = "quality_synth_" + uuid.uuid4().hex
        columns = ",".join(("synthesis_id", "operation_key", *fields))
        conn.execute(f"INSERT INTO quality_synthesis_results ({columns}) VALUES "
                     f"({','.join('?' for _ in range(len(fields)+2))})",
                     (synthesis_id, operation_key, *fields.values()))
        return synthesis_id


def record_synthesis_shadow_evidence(final_fits: str | Path, *, context: dict | None = None) -> str:
    """Best-effort caller target: hash a durable artifact and record shadow-only synthesis."""
    path = Path(final_fits)
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    from nas_server.grading_evidence import grading_evidence
    from nas_server.vision_contradictions import vision_evidence
    result = synthesize(grading_evidence(artifact_sha256=digest),
                        observations=vision_evidence(artifact_sha256=digest), context=context)
    return record_synthesis(operation_key=f"{digest}:{POLICY.version}",
                            artifact_sha256=digest, result=result)

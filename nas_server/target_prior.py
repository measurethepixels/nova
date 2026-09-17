"""Conservative, same-target parameter priors for the processing pipeline."""

from __future__ import annotations

import json
import math
from pathlib import Path

from nas_server.workflow_version import workflow_version

_ALIASES = {"bxt_stars": "stellar_amount", "bxt_nonstellar": "nonstellar_amount"}
_MAX_DELTA = 0.20
_ADAPTATION_POLICY_VERSION = "target-prior-application/1.0.0"


def get_experiment_evidence_for_target(*args, **kwargs):
    """Lazy boundary keeps this policy module settings-free on import."""
    from nas_server.database import get_experiment_evidence_for_target as retrieve
    return retrieve(*args, **kwargs)


def _read_environment_components():
    """Lazy boundary keeps this policy module settings-free on import."""
    components = {}
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            for row in conn.execute(
                "SELECT name,installed,status,stale FROM environment_components "
                "WHERE name IN ('NOVA','RC-Astro CLI','BlurXTerminator')"
            ):
                components[row["name"]] = (
                    row["installed"]
                    if row["status"] in ("CURRENT", "UPDATE_AVAILABLE", "APPROVED_UPDATE")
                    and not row["stale"] else None)
    except Exception:
        pass
    return components


def build_current_context(target: str, object_type: str, input_fits: str | Path) -> dict:
    """Build the transient comparison side from observed runtime facts only."""
    ontology_version = None
    try:
        ontology = json.loads(
            (Path(__file__).with_name("processing_ontology.json")).read_text())
        ontology_version = ontology.get("version")
    except (OSError, ValueError):
        pass
    image_scale = None
    try:
        from astropy.io import fits
        from astropy.wcs import WCS
        header = fits.getheader(str(input_fits))
        matrix = WCS(header).celestial.pixel_scale_matrix
        image_scale = float(math.sqrt(abs(float(__import__("numpy").linalg.det(matrix)))) * 3600.0)
    except Exception:
        pass
    components = _read_environment_components()
    return {
        "target": target, "data_kind": "fits", "morphology": object_type,
        "state": "linear", "code_version": components.get("NOVA") or workflow_version(),
        "ontology_version": ontology_version,
        "tool_version": components.get("RC-Astro CLI"),
        "model_version": components.get("BlurXTerminator"),
        "adaptation_policy_version": _ADAPTATION_POLICY_VERSION,
        "image_scale": image_scale,
    }


def derive_param_prior(target, step, object_type, ontology_defaults,
                       current_context: dict | None = None) -> dict:
    """Derive one bounded prior from the newest compatible same-target experiment."""
    baseline = {
        key: spec.get("default") if isinstance(spec, dict) else spec
        for key, spec in ontology_defaults.items()
    }
    empty = {"baseline": baseline, "prior": {}, "applied": dict(baseline)}
    evidence = get_experiment_evidence_for_target(target, step, object_type)
    provenance = {"experiment_run_id": None, "run_ids": [],
                  "tier": evidence.get("tier"), "classification_strength": 0.0}
    experiments = evidence.get("experiments", []) if evidence.get("tier") == "same_target" else []
    provenance["run_ids"] = [e.get("experiment_run_id") for e in experiments]
    if not experiments:
        return {"applied": False, "action": "none",
                "reason": "No same-target experimental evidence meeting the minimum evidence bar.",
                "params": empty, "evidence": provenance}

    eligible = [
        experiment for experiment in experiments
        if experiment.get("winner")
        and len(experiment.get("variants", [])) >= 2
    ]
    if not eligible:
        return {"applied": False, "action": "none",
                "reason": "No same-target experimental evidence meeting the minimum evidence bar.",
                "params": empty, "evidence": provenance}
    from nas_server.experiment_compatibility import compatibility_with_descriptor
    compatible = []
    compatibility_failures = []
    for experiment in eligible:
        result = compatibility_with_descriptor(
            experiment.get("experiment_run_id"), current_context or {},
            "target_prior_application")
        if result["compatible"]:
            compatible.append(experiment)
        else:
            compatibility_failures.extend(result["split_causes"])
    if not compatible:
        return {"applied": False, "action": "reject",
                "reason": "Eligible same-target evidence is incompatible with the current runtime context: "
                          + ", ".join(sorted(set(compatibility_failures))),
                "params": empty, "evidence": provenance}

    parent = compatible[0]
    provenance.update(experiment_run_id=parent.get("experiment_run_id"),
                      classification_strength=parent.get("classification_strength") or 0.0)
    prior = {_ALIASES.get(k, k): v for k, v in parent["winner"].get("params", {}).items()}
    prior = {k: v for k, v in prior.items() if k in baseline and isinstance(v, (int, float))}
    if not prior:
        return {"applied": False, "action": "reject", "reason": "Winner has no valid mapped parameters.",
                "params": empty, "evidence": provenance}
    for rejected in parent.get("rejected", []):
        if rejected.get("reason") != "analytically_failed":
            continue
        rejected_params = {_ALIASES.get(k, k): v for k, v in rejected.get("params", {}).items()}
        if prior and all(k in rejected_params
                         and abs(float(rejected_params[k]) - float(v)) <= 1e-9
                         for k, v in prior.items()):
            return {"applied": False, "action": "reject",
                    "reason": "Selected mapped value matches discrete analytically rejected evidence.",
                    "params": {**empty, "prior": prior}, "evidence": provenance}
    applied = dict(baseline)
    for key, value in prior.items():
        spec = ontology_defaults.get(key, {})
        lo = spec.get("min", value) if isinstance(spec, dict) else value
        hi = spec.get("max", value) if isinstance(spec, dict) else value
        applied[key] = max(lo, min(hi, max(baseline[key] - _MAX_DELTA,
                                           min(baseline[key] + _MAX_DELTA, value))))
    action = "inherit" if applied == baseline else "adapt"
    return {"applied": action == "adapt", "action": action,
            "reason": f"Selected newest compatible experiment {parent.get('experiment_run_id')}.",
            "params": {"baseline": baseline, "prior": prior, "applied": applied},
            "evidence": provenance}

"""Observe production stretch choices in the experiment evidence graph.

This module has no selection authority.  It records candidates which production
already generated and the choice which ``_physics_pick_stretch`` already made.
"""
from __future__ import annotations

from pathlib import Path

CONTRACT_REVISION = "stretch-shadow/1.0.0"


def record_stretch_shadow_evidence(
    *, input_path: str | Path, variants: list[dict], winner_name: str,
    scored: list[dict], operation_key: str, pipeline_run_id: str | None = None,
    selection_source: str = "physics_pick",
) -> str:
    """Persist an observe-only stretch run and return its evidence run id."""
    from nas_server.auto_process import (
        _dark_sky_channel_meds, _ghs_pixel_distance, _variant_saturation,
    )
    from nas_server.experiment_artifacts import register_existing_candidate_artifact
    from nas_server.experiment_evidence import (
        record_attempt_transition, register_experiment_run, start_candidate_attempt,
    )
    from nas_server.experiment_measurements import record_measurement_bundle
    from nas_server.experiment_selection import record_eligibility, record_fact

    roster = []
    paths: dict[str, Path] = {}
    for variant in variants:
        name = variant["name"]
        path = Path(variant.get("fits") or Path(input_path).parent / f"auto_stretch_{name}.fit")
        if path.exists():
            paths[name] = path
            roster.append({"id": name, "command": variant.get("command", name)})
    if winner_name not in paths:
        raise ValueError("operational stretch winner is absent from generated candidates")

    run_id = register_experiment_run(
        input_path=input_path, process_family="stretch", candidates=roster,
        ontology_revision=CONTRACT_REVISION, operation_key=operation_key,
        question="Observe the incumbent production physics stretch selection",
        intent="shadow_observation", pipeline_run_id=pipeline_run_id,
    )
    score_by_name = {item["name"]: item for item in scored}
    ghs_names = [name for name in paths if name in {"ghs", "ghs_soft", "ghs_strong"}]
    collapsed: dict[str, list[str]] = {name: [] for name in ghs_names}
    for index, name in enumerate(ghs_names):
        for other in ghs_names[index + 1:]:
            distance = _ghs_pixel_distance(paths[name], paths[other])
            if distance is not None and distance[0] <= 0.005:
                collapsed[name].append(other)
                collapsed[other].append(name)

    for name, path in paths.items():
        attempt_id = start_candidate_attempt(
            experiment_run_id=run_id, declared_variant_id=name,
            operation_key=f"{operation_key}:attempt:{name}",
        )
        artifact_id = register_existing_candidate_artifact(
            experiment_run_id=run_id, candidate_attempt_id=attempt_id,
            declared_variant_id=name, artifact_path=path,
        )
        score = score_by_name.get(name, {})
        meds = _dark_sky_channel_meds(path)
        black_clipped = bool(meds is not None and min(meds) < 0.01 and max(meds) > 0.04)
        metrics = {key: value for key, value in score.items()
                   if key not in {"name", "winner", "decision_basis", "dropped"}}
        metrics.update({
            "variant_saturation": _variant_saturation(path),
            "black_channel_clipped": black_clipped,
            "ghs_near_duplicate_of": collapsed.get(name, []),
            "analytically_failed": bool(black_clipped or score.get("dropped") == "sky_clip"),
        })
        record_measurement_bundle(
            run_artifact_id=artifact_id, process_family="stretch", metrics=metrics,
            state_context="nonlinear-stretch-output",
            population_context="candidate FITS pixels",
            method="nas_server.auto_process stretch physics measurements",
            method_version=CONTRACT_REVISION,
            decision_rule={"id": "stretch-objective-defect-gates",
                           "version": CONTRACT_REVISION},
        )
        rejection = score.get("dropped")
        reasons = ([str(rejection)] if rejection else [])
        if black_clipped:
            reasons.append("black_channel_clipped")
        record_eligibility(run_id, name, eligible=not reasons, reasons=reasons)
        record_attempt_transition(
            attempt_id=attempt_id, operation_key=f"{operation_key}:observed:{name}",
            updates={
                "execution_status": "succeeded", "artifact_status": "produced",
                "assessment_status": "succeeded",
                "comparison_status": "selected" if name == winner_name else "not_selected",
                "persistence_status": "succeeded",
            },
        )

    winner_score = score_by_name.get(winner_name, {})
    reason = (f"{selection_source} chose {winner_name}; "
              f"basis={winner_score.get('decision_basis', 'unscored-fallback')}; "
              f"rank={winner_score.get('decision_rank', 'not-ranked')}")
    record_fact(
        run_id, "operational_selection", variant_id=winner_name,
        policy_id=selection_source, reason=reason,
        payload={"source": selection_source, "score": winner_score,
                 "candidate_scores": scored},
    )
    return run_id

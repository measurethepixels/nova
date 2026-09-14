"""Descriptive experiment aggregates with explicit, compatible denominators."""
from __future__ import annotations
from collections import Counter
import json

from nas_server.experiment_compatibility import assign_epoch
from nas_server.experiment_outcomes import derive_run_outcome


def aggregate_evidence(run_ids: list[str], claim_kind: str) -> dict:
    """Return per-epoch counts; never pool across compatibility epochs."""
    ordered = list(dict.fromkeys(run_ids))
    strata: dict[str, dict] = {}
    from nas_server.database import get_conn
    for run_id in ordered:
        epoch = assign_epoch(run_id, claim_kind)
        outcome = derive_run_outcome(run_id)
        with get_conn() as conn:
            run = conn.execute("SELECT parent_artifact_revision_id,pipeline_run_id FROM experiment_runs WHERE experiment_run_id=?", (run_id,)).fetchone()
            descriptor = conn.execute("SELECT descriptor_json FROM evidence_compatibility_descriptors WHERE experiment_run_id=?", (run_id,)).fetchone()
            attempts = conn.execute("""SELECT execution_status,assessment_status,comparison_status
                FROM candidate_attempts WHERE experiment_run_id=?""", (run_id,)).fetchall()
        data = json.loads(descriptor[0])
        bucket = strata.setdefault(epoch["evidence_epoch"], {
            "evidence_epoch": epoch["evidence_epoch"], "split_causes": epoch["split_causes"],
            "attempted_runs": 0, "valid_comparable_runs": 0, "parent_datasets": set(),
            "capture_units": set(), "targets": set(), "candidate_exposures": 0,
            "execution_failures": 0, "analytic_rejections": 0,
            "comparison_participations": 0, "valid_comparison_selections": 0,
            "outcomes": Counter(),
        })
        bucket["attempted_runs"] += 1
        bucket["valid_comparable_runs"] += int(outcome["denominator_eligible"])
        bucket["parent_datasets"].add(run["parent_artifact_revision_id"])
        bucket["capture_units"].add(run["pipeline_run_id"] or run["parent_artifact_revision_id"])
        bucket["targets"].add(data.get("target"))
        bucket["candidate_exposures"] += len(attempts)
        bucket["execution_failures"] += sum(a[0] in ("failed", "timed_out") for a in attempts)
        bucket["analytic_rejections"] += sum(a[1] == "rejected" for a in attempts)
        bucket["comparison_participations"] += sum(a[2] in ("selected", "not_selected") for a in attempts)
        if outcome["denominator_eligible"]:
            bucket["valid_comparison_selections"] += sum(a[2] == "selected" for a in attempts)
        bucket["outcomes"][outcome["outcome_kind"]] += 1
    rendered = []
    for bucket in strata.values():
        bucket["unique_parent_datasets"] = len(bucket.pop("parent_datasets"))
        bucket["unique_capture_units"] = len(bucket.pop("capture_units"))
        bucket["unique_targets"] = len({v for v in bucket.pop("targets") if v is not None})
        bucket["outcomes"] = dict(bucket["outcomes"])
        bucket["repeatability_runs"] = bucket["attempted_runs"] - bucket["unique_parent_datasets"]
        bucket["reproducibility_capture_units"] = bucket["unique_capture_units"]
        bucket["independent_target_stratum"] = bucket["unique_targets"]
        bucket["descriptive_effect_sizes"] = None
        bucket["effect_size_status"] = "unavailable:no_supported_paired_design"
        rendered.append(bucket)
    return {"claim_kind": claim_kind, "attempted_runs": len(ordered),
            "compatibility_strata": sorted(rendered, key=lambda x: x["evidence_epoch"]),
            "legacy_winner_fraction_label": "descriptive_legacy_winner_fraction_not_confidence"}

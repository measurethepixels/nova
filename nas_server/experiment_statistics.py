"""Guarded repeated-evidence summaries; never accepts an unstratified pool."""
from __future__ import annotations
from collections import Counter, defaultdict
import math
import statistics


def _canonical_rejection(experiment_run_id, profile_id, evidence_epoch):
    """Return a rejection reason from canonical Groups 6/8/9 evidence."""
    if not experiment_run_id:
        return "missing_experiment_run_id"
    from nas_server.database import get_conn
    with get_conn() as conn:
        membership = conn.execute("""SELECT em.evidence_epoch
            FROM evidence_epoch_members em
            JOIN evidence_compatibility_descriptors d USING(descriptor_id)
            JOIN compatibility_profiles p USING(profile_id)
            WHERE d.experiment_run_id=? AND p.profile_id=?""",
            (experiment_run_id, profile_id)).fetchone()
    if not membership or membership["evidence_epoch"] != evidence_epoch:
        return "incompatible_epoch"
    from nas_server.experiment_outcomes import derive_run_outcome
    if not derive_run_outcome(experiment_run_id, persist=False)["denominator_eligible"]:
        return "ineligible_outcome"
    return None


def _validated(rows, profile_id, evidence_epoch, on_invalid):
    if not profile_id or not evidence_epoch:
        raise ValueError("compatibility profile and evidence epoch are required")
    if on_invalid not in ("raise", "exclude"):
        raise ValueError("on_invalid must be raise or exclude")
    valid, excluded = [], []
    for row in rows:
        reason = _canonical_rejection(row.get("experiment_run_id"), profile_id,
                                      evidence_epoch)
        if row.get("legacy_reconstruction_level") not in (None, "complete"):
            reason = "legacy_provenance_incomplete"
        if reason:
            if on_invalid == "raise":
                raise ValueError(reason)
            excluded.append({"row": row, "reason": reason})
        else:
            valid.append(row)
    return valid, excluded


def _distribution(values):
    values = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not values:
        return {"n": 0, "mean": None, "median": None, "sample_sd": None, "standard_error": None}
    sd = statistics.stdev(values) if len(values) >= 2 else None
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "sample_sd": sd, "standard_error": sd / math.sqrt(len(values)) if sd is not None else None}


def _group_effects(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        if row.get(key) is not None and isinstance(row.get("effect"), (int, float)):
            grouped[row[key]].append(row["effect"])
    return {name: statistics.mean(values) for name, values in grouped.items()}


def within_parent_paired_effects(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    effects = _group_effects(valid, "parent_dataset_id")
    return {"kind": "within_parent_paired", "effects": effects,
            "summary": _distribution(effects.values()), "excluded": excluded}


def same_data_repeatability(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    groups = defaultdict(list)
    for row in valid:
        groups[row.get("parent_dataset_id")].append(row.get("effect"))
    eligible = {k: [v for v in vals if isinstance(v, (int, float))]
                for k, vals in groups.items() if k is not None and len(vals) >= 2}
    return {"kind": "same_data_repeatability", "parents": {k: _distribution(v) for k, v in eligible.items()},
            "excluded": excluded}


def independent_capture_summary(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    effects = _group_effects(valid, "capture_unit_id")
    return {"kind": "independent_captures", "summary": _distribution(effects.values()),
            "independent_units": len(effects), "excluded": excluded}


def independent_target_replication(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    effects = _group_effects(valid, "target_id")
    return {"kind": "independent_targets", "summary": _distribution(effects.values()),
            "independent_targets": len(effects), "excluded": excluded}


def changed_condition_reproducibility(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    effects = _group_effects(valid, "changed_condition")
    return {"kind": "changed_condition", "conditions": {k: _distribution([v]) for k, v in effects.items()},
            "excluded": excluded}


def human_ai_reliability(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    pairs = [(r.get("human_choice"), r.get("ai_choice")) for r in valid
             if r.get("human_choice") is not None and r.get("ai_choice") is not None]
    agreement = sum(a == b for a, b in pairs) / len(pairs) if pairs else None
    labels = set(v for pair in pairs for v in pair)
    kappa = None
    if len(pairs) >= 5 and len(labels) >= 2:
        human, ai = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
        chance = sum(human[l] * ai[l] for l in labels) / len(pairs) ** 2
        kappa = (agreement - chance) / (1 - chance) if chance < 1 else None
    return {"kind": "human_ai_reliability", "n": len(pairs), "agreement_rate": agreement,
            "cohens_kappa": kappa, "reliability_status": "estimated" if kappa is not None else "insufficient_structure",
            "excluded": excluded}


def effect_size_distribution(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    return {"kind": "effect_size_distribution", "summary": _distribution(r.get("effect") for r in valid),
            "excluded": excluded}


def hierarchical_repeated_measures(rows, *, profile_id, evidence_epoch, on_invalid="raise"):
    valid, excluded = _validated(rows, profile_id, evidence_epoch, on_invalid)
    groups = defaultdict(list)
    for row in valid:
        if row.get("target_id") is not None and isinstance(row.get("effect"), (int, float)):
            groups[row["target_id"]].append(row["effect"])
    if len(groups) < 3 or any(len(values) < 2 for values in groups.values()):
        raise ValueError("insufficient hierarchical sample structure")
    target_means = [statistics.mean(values) for values in groups.values()]
    return {"kind": "hierarchical_repeated_measures", "targets": len(groups),
            "target_mean_distribution": _distribution(target_means), "excluded": excluded}

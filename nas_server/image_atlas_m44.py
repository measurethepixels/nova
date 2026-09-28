"""Side-effect-free implementation of the frozen M4.4 reachability comparator.

The comparator consumes already-loaded decision ledgers.  It never opens image
data and it has no production/validation mode: the eight-ledger binding is the
responsibility of the deliberately one-shot runner.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

import numpy as np

from nas_server.image_atlas_ledger import DecisionLedger, load_decision_ledger
from nas_server.image_atlas_matched import (
    _field, _finite_number, _midranks, _region, epistemic_stratum,
)


ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "docs/analysis/image_atlas"
POLICY_PATH = ANALYSIS_DIR / "milestone_4_4_policy.json"
INHERITED_POLICY_PATH = ANALYSIS_DIR / "milestone_4_3_policy.json"
PROPOSITIONS = ("artifact_inhibition", "stellar_protection")
STRATA = ("positive_detection", "no_positive_detection", "conflict",
          "unavailable_or_insufficient")


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    values_array, weights_array = np.asarray(values, float), np.asarray(weights, float)
    total = float(np.sum(weights_array))
    if not total or len(values_array) != len(weights_array) or np.any(weights_array < 0):
        raise ValueError("weights must be nonnegative, aligned, and have positive total")
    return float(np.sum(values_array * weights_array) / total)


def weighted_population_variance(values: Sequence[float], weights: Sequence[float]) -> float:
    mean = weighted_mean(values, weights)
    return weighted_mean([(float(value) - mean) ** 2 for value in values], weights)


def weighted_smd(high: Sequence[float], high_weights: Sequence[float],
                 low: Sequence[float], low_weights: Sequence[float]) -> float:
    difference = weighted_mean(high, high_weights) - weighted_mean(low, low_weights)
    pooled = (weighted_population_variance(high, high_weights)
              + weighted_population_variance(low, low_weights)) / 2
    if pooled == 0:
        return 0.0 if difference == 0 else math.inf
    return difference / math.sqrt(pooled)


def kish_ess(weights: Sequence[float]) -> float:
    array = np.asarray(weights, float)
    denominator = float(np.sum(array * array))
    return float(np.sum(array) ** 2 / denominator) if denominator else 0.0


def distribution(values: Sequence[Any]) -> dict[str, Any]:
    finite = sorted(value for raw in values if (value := _finite_number(raw)) is not None)
    result: dict[str, Any] = {"count": len(values), "missing_count": len(values) - len(finite)}
    if not finite:
        result.update(dict.fromkeys(("min", "q1", "median", "q3", "max")))
    else:
        quantiles = np.percentile(finite, [0, 25, 50, 75, 100], method="linear")
        result.update(zip(("min", "q1", "median", "q3", "max"), map(float, quantiles)))
    return result


def _population(records: Sequence[Mapping[str, Any]], proposition: str,
                spec: Mapping[str, Any]) -> tuple[list[dict], list[str]]:
    population, schema_reasons = [], []
    for record in records:
        if record.get("outcomes", {}).get("valid") is not True:
            continue
        if record.get("m4", {}).get("product_validity", {}).get(proposition) != spec["required_product_validity"]:
            continue
        stratum = epistemic_stratum(record.get("m3", {}))
        exposure = _finite_number(_field(record, spec["exposure"]))
        if stratum is None:
            schema_reasons.append("schema_error_unmapped_epistemic_state")
            continue
        if exposure is None:
            schema_reasons.append("missing_exposure")
            continue
        population.append({"tile": tuple(record["tile"]), "stratum": stratum,
                           "exposure": exposure, "record": record})
    population.sort(key=lambda item: item["tile"])
    exposure_ranks = _midranks([item["exposure"] for item in population])
    for item, rank in zip(population, exposure_ranks):
        item["exposure_rank"] = float(rank)
    paths = spec["common_content_covariates"]
    raw = [[_finite_number(_field(item["record"], path)) for path in paths]
           for item in population]
    for column in range(len(paths)):
        valid = [index for index, row in enumerate(raw) if row[column] is not None]
        ranks = _midranks([raw[index][column] for index in valid])
        for index, rank in zip(valid, ranks):
            population[index].setdefault("partial_ranks", {})[column] = float(rank)
    for item, row in zip(population, raw):
        item["covariates"] = dict(zip(paths, row))
        if all(value is not None for value in row):
            item["ranks"] = np.asarray([item["partial_ranks"][column]
                                        for column in range(len(paths))])
    return population, sorted(set(schema_reasons))


def match_with_replacement(high: Sequence[dict], low: Sequence[dict],
                           caliper: float = 0.2) -> list[tuple[dict, dict]]:
    """Match every high tile independently; controls remain available for reuse."""
    pool = sorted(low, key=lambda item: item["tile"])
    pairs = []
    for source in sorted(high, key=lambda item: item["tile"]):
        candidates = []
        for target in pool:
            distances = np.abs(source["ranks"] - target["ranks"])
            if np.all(distances <= caliper):
                candidates.append((float(np.sum(distances)), target["tile"], target))
        if candidates:
            pairs.append((source, min(candidates, key=lambda item: (item[0], item[1]))[2]))
    return pairs


def _harm(record: Mapping[str, Any]) -> tuple[float | None, float | None]:
    products = record["outcomes"]["products"]
    clipping = _finite_number(products.get("clipping_increase"))
    noise = _finite_number(products.get("noise_amplification"))
    primary = clipping + max(noise, 0.0) if clipping is not None and noise is not None else None
    halo = _finite_number(products.get("halo_distortion"))
    secondary = (max(halo, 0.0) if record["outcomes"].get("halo_valid") is True
                 and halo is not None else None)
    return primary, secondary


def _unit(population: Sequence[dict], stratum: str, paths: Sequence[str],
          gates: Mapping[str, Any], schema_reasons: Sequence[str]) -> dict:
    members = [item for item in population if item["stratum"] == stratum]
    all_high = [item for item in members if item["exposure_rank"] >= 2 / 3]
    low = [item for item in members if item["exposure_rank"] <= 1 / 3 and "ranks" in item]
    matchable_high = [item for item in all_high if "ranks" in item]
    pairs = match_with_replacement(matchable_high, low)
    matched_tiles = {high["tile"] for high, _ in pairs}
    unmatched = [item for item in all_high if item["tile"] not in matched_tiles]
    control_counts = Counter(low_item["tile"] for _, low_item in pairs)
    distinct_controls = {item["tile"]: item for _, item in pairs}
    high_weights = [1] * len(pairs)
    control_items = [distinct_controls[tile] for tile in sorted(distinct_controls)]
    control_weights = [control_counts[item["tile"]] for item in control_items]
    high_region_weights = Counter(_region(item["tile"]) for item, _ in pairs)
    low_region_weights = Counter()
    for item, weight in zip(control_items, control_weights):
        low_region_weights[_region(item["tile"])] += weight
    balance = {}
    for column, path in enumerate(paths):
        value = (weighted_smd([item["ranks"][column] for item, _ in pairs], high_weights,
                              [item["ranks"][column] for item in control_items], control_weights)
                 if pairs else math.inf)
        balance[path] = "+inf" if not math.isfinite(value) else float(value)
    control_ess = kish_ess(control_weights)
    high_region_ess = kish_ess(list(high_region_weights.values()))
    low_region_ess = kish_ess(list(low_region_weights.values()))
    reasons = set(schema_reasons)
    if len(pairs) < gates["minimum_matched_high_risk_tiles"]:
        reasons.add("matched_high_risk_tiles_below_support")
    if control_ess < gates["minimum_control_tile_kish_ess"]:
        reasons.add("control_tile_kish_ess_below_support")
    if len(high_region_weights) < gates["minimum_distinct_positive_weight_regions_per_arm"]:
        reasons.add("high_region_below_support")
    if len(low_region_weights) < gates["minimum_distinct_positive_weight_regions_per_arm"]:
        reasons.add("low_region_below_support")
    if any(value == "+inf" or abs(value) > gates["maximum_absolute_weighted_smd_per_covariate"]
           for value in balance.values()):
        reasons.add("covariate_imbalance")
    matches = []
    if not reasons:
        for high, low_item in pairs:
            high_primary, high_secondary = _harm(high["record"])
            low_primary, low_secondary = _harm(low_item["record"])
            if high_primary is None or low_primary is None:
                reasons.add("missing_primary_harm")
                break
            matches.append({"high_tile": list(high["tile"]), "low_tile": list(low_item["tile"]),
                            "high_region": list(_region(high["tile"])),
                            "primary_difference": high_primary - low_primary,
                            "secondary_halo_difference": (high_secondary - low_secondary
                                if high_secondary is not None and low_secondary is not None else None)})
    if reasons:
        matches = []
    fields = {"exposure": distribution([item["exposure"] for item in unmatched])}
    fields.update({path: distribution([item["covariates"][path] for item in unmatched])
                   for path in paths})
    diagnostics = {
        "all_high_risk_tiles": len(all_high), "matched_high_risk_tiles": len(pairs),
        "unmatched_high_risk_tiles": len(unmatched),
        "unmatched_high_fraction": len(unmatched) / len(all_high) if all_high else 0.0,
        "unmatched_high_distributions": fields,
        "distinct_matched_controls": len(control_items), "control_use_counts": [
            {"tile": list(item["tile"]), "weight": control_counts[item["tile"]]}
            for item in control_items],
        "control_tile_kish_ess": control_ess,
        "distinct_positive_weight_regions": {"high": len(high_region_weights),
                                              "low": len(low_region_weights)},
        "region_kish_ess": {"high": high_region_ess, "low": low_region_ess},
        "unit_region_effective_support": min(high_region_ess, low_region_ess),
        "weighted_covariate_smd": balance,
        "cross_region_pair_fraction": (sum(_region(high["tile"]) != _region(low_item["tile"])
                                           for high, low_item in pairs) / len(pairs) if pairs else 0.0),
    }
    return {"status": "abstain" if reasons else "identified", "reasons": sorted(reasons),
            "diagnostics": diagnostics,
            "estimate": float(median(item["primary_difference"] for item in matches)) if matches else None,
            "secondary_halo_estimate": (float(median(secondary)) if
                (secondary := [item["secondary_halo_difference"] for item in matches
                               if item["secondary_halo_difference"] is not None]) else None),
            "matches": matches}


def _bootstrap(units: Mapping[str, dict], rng: np.random.Generator,
               replicates: int) -> list[float]:
    grouped_units = []
    for checkpoint in sorted(units):
        grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
        for match in units[checkpoint]["matches"]:
            grouped[tuple(match["high_region"])].append(match["primary_difference"])
        grouped_units.append([grouped[key] for key in sorted(grouped)])
    draws = []
    for _ in range(replicates):
        checkpoint_estimates = []
        for regions in grouped_units:
            selection = rng.integers(0, len(regions), size=len(regions))
            checkpoint_estimates.append(float(np.median(
                [value for index in selection for value in regions[int(index)]])))
        draws.append(float(np.mean(checkpoint_estimates)))
    return [float(value) for value in np.percentile(draws, [2.5, 97.5], method="linear")]


def compare_ledger_data(ledgers: Sequence[tuple[str, DecisionLedger]]) -> dict:
    policy_bytes = POLICY_PATH.read_bytes()
    policy_hash = hashlib.sha256(policy_bytes).hexdigest()
    policy = json.loads(policy_bytes)
    inherited = json.loads(INHERITED_POLICY_PATH.read_text())
    hashes = [digest for digest, _ in ledgers]
    if any(len(digest) != 64 or digest.lower() != digest
           or any(char not in "0123456789abcdef" for char in digest) for digest in hashes):
        raise ValueError("input ledger hashes must be lowercase SHA-256")
    checkpoints = {}
    for _, ledger in ledgers:
        checkpoint = ledger.header["checkpoint_id"]
        if checkpoint in checkpoints:
            raise ValueError("duplicate checkpoint ledger")
        if not ledger.header.get("has_outcomes"):
            raise ValueError("comparison requires outcome-bearing ledgers")
        checkpoints[checkpoint] = ledger
    seed_text = "\n".join([policy_hash, *sorted(hashes)])
    seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
    result = {"schema": "image-atlas-m44-reachability/v1", "policy_sha256": policy_hash,
              "input_ledger_hashes": sorted(hashes), "bootstrap_seed": seed,
              "retired_propositions": {"tonal_bound_protection":
                  "not_identifiable_under_current_measurement"}, "aggregates": {}}
    by_group: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    checkpoint_output = {}
    for checkpoint, ledger in sorted(checkpoints.items()):
        checkpoint_output[checkpoint] = {}
        for proposition in PROPOSITIONS:
            spec = inherited["propositions"][proposition]
            population, schema_reasons = _population(ledger.records, proposition, spec)
            checkpoint_output[checkpoint][proposition] = {}
            for stratum in STRATA:
                unit = _unit(population, stratum, spec["common_content_covariates"],
                             policy["unit_hard_gates"], schema_reasons)
                checkpoint_output[checkpoint][proposition][stratum] = unit
                if unit["status"] == "identified":
                    by_group[(proposition, stratum)][checkpoint] = unit
    result["checkpoints"] = checkpoint_output
    aggregate_gates = policy["aggregation"]
    replicates = policy["uncertainty"]["replicates"]
    # Unit uncertainty is required even when no aggregate reaches admission.
    # Each statistic starts from the one frozen serialized seed so its result
    # is independent of dictionary traversal and of which sibling units pass.
    for checkpoint in checkpoint_output.values():
        for proposition in checkpoint.values():
            for unit in proposition.values():
                unit["cluster_interval_95"] = (
                    _bootstrap({"unit": unit}, np.random.Generator(np.random.PCG64(seed)),
                               replicates)
                    if unit["status"] == "identified" else None)
    for proposition in PROPOSITIONS:
        result["aggregates"][proposition] = {}
        for stratum in STRATA:
            units = by_group[(proposition, stratum)]
            supports = {name: unit["diagnostics"]["unit_region_effective_support"]
                        for name, unit in units.items()}
            total_support = sum(supports.values())
            fractions = {name: support / total_support for name, support in supports.items()} if total_support else {}
            reasons = []
            if len(units) < aggregate_gates["minimum_contributing_checkpoints"]:
                reasons.append("contributing_checkpoints_below_minimum")
            if fractions and max(fractions.values()) > aggregate_gates["maximum_single_checkpoint_support_fraction"]:
                reasons.append("checkpoint_support_dominance_exceeded")
            identified = not reasons
            estimate = float(np.mean([unit["estimate"] for unit in units.values()])) if identified else None
            rng = np.random.Generator(np.random.PCG64(seed))
            result["aggregates"][proposition][stratum] = {
                "status": "identified" if identified else "abstain", "reasons": reasons,
                "contributing_checkpoints": sorted(units),
                "checkpoint_support_fractions": fractions, "estimate": estimate,
                "cluster_interval_95": _bootstrap(units, rng, replicates) if identified else None,
            }
    result["admission_verdict"] = any(
        aggregate["status"] == "identified"
        for propositions in result["aggregates"].values() for aggregate in propositions.values())
    return result


def compare_ledger_paths(paths: Sequence[str | Path]) -> dict:
    entries = []
    for raw_path in paths:
        path = Path(raw_path)
        data = path.read_bytes()
        entries.append((hashlib.sha256(data).hexdigest(), load_decision_ledger(path)))
    return compare_ledger_data(entries)

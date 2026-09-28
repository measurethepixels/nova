"""Observe-only M4.3.1 matched harm association from immutable M4 ledgers.

No image data is opened here.  In particular, the selected M4.3 holdout is
refused until a separately authorized validation path is designed.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

import numpy as np

from nas_server.image_atlas_ledger import DecisionLedger, load_decision_ledger


POLICY_PATH = (Path(__file__).resolve().parents[1] / "docs/analysis/image_atlas"
               / "milestone_4_3_policy.json")
HOLDOUT_MANIFEST_PATH = POLICY_PATH.with_name("milestone_4_3_holdout_manifest.json")
DEVELOPMENT_HASHES = frozenset({
    "bf4b5acc2524928867fafd46a26fd5ee2521c8b8dee441c07031ad1078778da6",
    "c043946425222f9dc81d0dd867fccc2a9674e5fd592225e9aba7ef001fa36185",
    "2ecc0279ff5cd3290cb7d0956d650c19dad77fc9f893b5b646a0b789a80087e7",
    "8fbb2a2351443729721b42bc31361f792b7625709d4a3cef559ba6b2020563ed",
    "a8059f51b3b7993a6f43ae6afa9e026e9357a111077f7b480b7039aa2647907c",
})


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def epistemic_stratum(m3: Mapping[str, Any]) -> str | None:
    """Apply the frozen four-state precedence, without altering risk or harm."""
    if m3.get("state") == "conflict" or m3.get("contradiction_present") is True:
        return "conflict"
    if m3.get("sufficient") is False or m3.get("rule_id") == "insufficient_required_evidence":
        return "unavailable_or_insufficient"
    if m3.get("rule_id") == "no_positive_detection" and m3.get("sufficient") is True:
        return "no_positive_detection"
    if m3.get("detection_present") is True and m3.get("sufficient") is True:
        return "positive_detection"
    return None


def _field(record: Mapping[str, Any], path: str) -> Any:
    if path.startswith("m3.evidence.tonal_context."):
        name = path.removeprefix("m3.evidence.tonal_context.")
        groups = [item for item in record["m3"]["evidence"]
                  if item.get("group") == "tonal_context"]
        if len(groups) != 1:
            return None
        fields, values = groups[0].get("fields", ()), groups[0].get("values", ())
        return values[fields.index(name)] if name in fields and len(values) == len(fields) else None
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _midranks(values: Sequence[float]) -> np.ndarray:
    """Value-only average fractional ranks; all ties receive 0.5."""
    n = len(values)
    if n < 2:
        return np.full(n, 0.5, dtype=float)
    _, inverse, counts = np.unique(np.asarray(values, dtype=float),
                                    return_inverse=True, return_counts=True)
    starts = np.cumsum(counts) - counts + 1
    return (starts[inverse] + (counts[inverse] - 1) / 2 - 1) / (n - 1)


def _balance(high: np.ndarray, low: np.ndarray) -> float:
    numerator = float(np.mean(high) - np.mean(low))
    variance = float((np.var(high) + np.var(low)) / 2)
    if variance == 0:
        return 0.0 if numerator == 0 else math.inf
    return numerator / math.sqrt(variance)


def _region(tile: tuple[int, int]) -> tuple[int, int]:
    return tile[0] // 4, tile[1] // 4


def _checkpoint_gate_reasons(diagnostic: Mapping[str, Any], gates: Mapping[str, Any]) -> set[str]:
    reasons = set()
    if min(diagnostic["arm_counts"]["low"], diagnostic["arm_counts"]["high"]) < gates["minimum_usable_tiles_per_arm_per_checkpoint"]:
        reasons.add("exposure_arm_below_support")
    if any(diagnostic["arm_region_counts"][name] < gates["minimum_independent_regions_per_arm_per_checkpoint"]
           for name in ("low", "high")):
        reasons.add("arm_region_below_support")
    if diagnostic["matched_pairs"] < gates["minimum_matched_pairs_per_checkpoint"]:
        reasons.add("matched_pairs_below_support")
        if diagnostic["missing_covariate"]:
            reasons.add("missing_covariate")
    if diagnostic["unmatched_fraction"] > gates["common_support"]["maximum_unmatched_fraction_of_smaller_arm"]:
        reasons.add("unmatched_fraction_exceeded")
    if any(value == "+inf" or abs(value) > gates["common_support"]["maximum_covariate_standardized_mean_difference"]
           for value in diagnostic["covariate_balance"].values()):
        reasons.add("covariate_imbalance")
    return reasons


def _experiment_gate_reasons(supporting: int, pair_counts: Sequence[int],
                             gates: Mapping[str, Any]) -> set[str]:
    reasons = set()
    if supporting < gates["minimum_supporting_checkpoints"]:
        reasons.add("supporting_checkpoints_below_minimum")
    total = sum(pair_counts)
    if total and max(pair_counts) / total > gates["target_balance"]["maximum_fraction_of_matched_pairs_from_one_checkpoint"]:
        reasons.add("target_concentration_exceeded")
    return reasons


def _match(high: list[dict], low: list[dict], caliper: float) -> list[tuple[dict, dict]]:
    """Global deterministic nearest-pair greedy order, without replacement."""
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for item in low:
        by_stratum[item["stratum"]].append(item)
    for items in by_stratum.values():
        items.sort(key=lambda item: item["tile"])
    queues: list[tuple[list[dict], np.ndarray, np.ndarray] | None] = []
    heap: list[tuple[float, tuple[int, int], tuple[int, int], int, int]] = []
    for source in sorted(high, key=lambda item: item["tile"]):
        pool = by_stratum.get(source["stratum"], [])
        if not pool:
            continue
        distances = np.abs(np.asarray([item["ranks"] for item in pool]) - source["ranks"])
        eligible = np.flatnonzero(np.all(distances <= caliper, axis=1))
        totals = np.sum(distances[eligible], axis=1)
        order = np.argsort(totals, kind="stable")  # pool is coordinate-sorted
        indices = eligible[order]
        scores = totals[order]
        if len(indices):
            queue_id = len(queues)
            queues.append((pool, indices, scores))
            target = pool[int(indices[0])]
            heapq.heappush(heap, (float(scores[0]), source["tile"],
                                  target["tile"], queue_id, 0))
    high_by_tile = {item["tile"]: item for item in high}
    used_high: set[tuple[int, int]] = set()
    used_low: set[tuple[int, int]] = set()
    matches = []
    while heap:
        _, high_tile, low_tile, queue_id, position = heapq.heappop(heap)
        if high_tile in used_high:
            continue
        if low_tile not in used_low:
            used_high.add(high_tile)
            used_low.add(low_tile)
            matches.append((high_by_tile[high_tile], queues[queue_id][0][int(queues[queue_id][1][position])]))
            continue
        pool, indices, scores = queues[queue_id]
        position += 1
        while position < len(indices) and pool[int(indices[position])]["tile"] in used_low:
            position += 1
        if position < len(indices):
            next_tile = pool[int(indices[position])]["tile"]
            heapq.heappush(heap, (float(scores[position]), high_tile, next_tile,
                                  queue_id, position))
    return matches


def _checkpoint(records: Sequence[Mapping[str, Any]], proposition: str,
                spec: Mapping[str, Any], policy: Mapping[str, Any]) -> dict:
    gates = policy["comparison_protocol"]
    reasons: set[str] = set()
    population = []
    for record in records:
        if record.get("outcomes", {}).get("valid") is not True:
            continue
        if record.get("m4", {}).get("product_validity", {}).get(proposition) != spec["required_product_validity"]:
            continue
        stratum = epistemic_stratum(record.get("m3", {}))
        if stratum is None:
            reasons.add("schema_error_unmapped_epistemic_state")
            continue
        exposure = _finite_number(_field(record, spec["exposure"]))
        if exposure is None:
            reasons.add("missing_exposure")
            continue
        tile = tuple(record["tile"])
        population.append({"tile": tile, "stratum": stratum, "exposure": exposure,
                           "record": record})
    population.sort(key=lambda item: item["tile"])
    diagnostic: dict[str, Any] = {"eligible_tiles": len(population), "missing_covariate": 0}
    if len(population) < 2 or len({item["exposure"] for item in population}) < 2:
        reasons.add("exposure_not_separable")
    exposure_ranks = _midranks([item["exposure"] for item in population])
    for item, rank in zip(population, exposure_ranks):
        item["exposure_rank"] = float(rank)
    # Covariate ranks are computed once across the full eligible cell, including
    # all strata and middle-third tiles, before any arm/match selection.
    paths = spec["common_content_covariates"]
    values = [[_finite_number(_field(item["record"], path)) for path in paths]
              for item in population]
    for column in range(len(paths)):
        valid_indices = [index for index, row in enumerate(values) if row[column] is not None]
        ranks = _midranks([values[index][column] for index in valid_indices])
        for index, rank in zip(valid_indices, ranks):
            population[index].setdefault("partial_ranks", {})[column] = float(rank)
    for item, row in zip(population, values):
        if any(value is None for value in row):
            diagnostic["missing_covariate"] += 1
            continue
        item["ranks"] = np.asarray([item["partial_ranks"][column] for column in range(4)])
    arms = {
        "low": [item for item in population if item["exposure_rank"] <= 1 / 3],
        "high": [item for item in population if item["exposure_rank"] >= 2 / 3],
        "middle": [item for item in population if 1 / 3 < item["exposure_rank"] < 2 / 3],
    }
    diagnostic["arm_counts"] = {name: len(items) for name, items in arms.items()}
    diagnostic["arm_region_counts"] = {
        name: len({_region(item["tile"]) for item in arms[name]})
        for name in ("low", "high")
    }
    high = [item for item in arms["high"] if "ranks" in item]
    low = [item for item in arms["low"] if "ranks" in item]
    pairs = _match(high, low, gates["common_support"]["covariate_rank_caliper"])
    diagnostic["matched_pairs"] = len(pairs)
    smaller = min(len(arms["low"]), len(arms["high"]))
    diagnostic["unmatched_fraction"] = (1 - len(pairs) / smaller) if smaller else 1.0
    diagnostic["cross_region_pair_fraction"] = (
        sum(_region(h["tile"]) != _region(l["tile"]) for h, l in pairs) / len(pairs)
        if pairs else 0.0)
    balance = {}
    for column, path in enumerate(paths):
        smd = (_balance(np.asarray([h["ranks"][column] for h, _ in pairs]),
                        np.asarray([l["ranks"][column] for _, l in pairs]))
               if pairs else math.inf)
        balance[path] = "+inf" if not math.isfinite(smd) else smd
    diagnostic["covariate_balance"] = balance
    reasons.update(_checkpoint_gate_reasons(diagnostic, gates))
    # The pairing and all support diagnostics above are frozen before opening
    # any outcome product values.
    matches = []
    if not reasons:
        for h, l in pairs:
            products_h = h["record"]["outcomes"]["products"]
            products_l = l["record"]["outcomes"]["products"]
            def harm(products: Mapping[str, Any]) -> float | None:
                clipping = _finite_number(products.get("clipping_increase"))
                noise = _finite_number(products.get("noise_amplification"))
                return clipping + max(noise, 0.0) if clipping is not None and noise is not None else None
            h_harm, l_harm = harm(products_h), harm(products_l)
            if h_harm is None or l_harm is None:
                reasons.add("missing_primary_harm")
                break
            halo_h = _finite_number(products_h.get("halo_distortion"))
            halo_l = _finite_number(products_l.get("halo_distortion"))
            secondary = (max(halo_h, 0.0) - max(halo_l, 0.0)
                         if h["record"]["outcomes"].get("halo_valid") is True
                         and l["record"]["outcomes"].get("halo_valid") is True
                         and halo_h is not None and halo_l is not None else None)
            matches.append({"id": f"{proposition}:{h['stratum']}:{h['tile'][0]},{h['tile'][1]}"
                                  f">{l['tile'][0]},{l['tile'][1]}",
                            "stratum": h["stratum"], "high_tile": list(h["tile"]),
                            "low_tile": list(l["tile"]), "region": list(_region(h["tile"])),
                            "difference": h_harm - l_harm, "secondary_halo_difference": secondary})
    if reasons:
        matches = []
    return {"status": "abstain" if reasons else "identified",
            "reasons": sorted(reasons), "diagnostics": diagnostic,
            "estimate": float(median(item["difference"] for item in matches)) if matches else None,
            "secondary_halo_estimate": (float(median(values)) if (values := [item["secondary_halo_difference"]
                for item in matches if item["secondary_halo_difference"] is not None]) else None),
            "matches": matches}


def _bootstrap(checkpoints: Mapping[str, dict], policy_hash: str,
               input_hashes: Sequence[str], policy: Mapping[str, Any]) -> list[float]:
    spec = policy["estimand"]["cluster_uncertainty"]
    seed_input = "\n".join([policy_hash, *sorted(input_hashes)]).encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(seed_input).digest()[:8], "big")
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = []
    regions_by_checkpoint = []
    for checkpoint in sorted(checkpoints):
        grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
        for match in checkpoints[checkpoint]["matches"]:
            grouped[tuple(match["region"])].append(match["difference"])
        regions_by_checkpoint.append([grouped[key] for key in sorted(grouped)])
    for _ in range(spec["replicates"]):
        estimates = []
        for regions in regions_by_checkpoint:
            selected = rng.integers(0, len(regions), size=len(regions))
            estimates.append(float(np.median([value for index in selected for value in regions[int(index)]])))
        draws.append(float(np.mean(estimates)))
    return [float(value) for value in np.percentile(draws, [2.5, 97.5], method="linear")]


def compare_ledger_data(ledgers: Sequence[tuple[str, DecisionLedger]], *,
                        mode: str = "synthetic", authorization: str | None = None) -> dict:
    """Pure comparison. Hashes identify immutable input bytes supplied by caller."""
    if mode not in {"synthetic", "development_calibration_not_validation", "heldout_validation"}:
        raise ValueError("unsupported comparator mode; held-out evaluation is not authorized")
    if mode == "heldout_validation":
        if not isinstance(authorization, str) or not authorization.strip():
            raise ValueError("heldout_validation requires non-empty explicit authorization")
    elif authorization is not None:
        raise ValueError("authorization is accepted only in heldout_validation mode")
    policy_bytes = POLICY_PATH.read_bytes()
    policy_hash = hashlib.sha256(policy_bytes).hexdigest()
    policy = json.loads(policy_bytes)
    if policy["version"] != "m4.3.1":
        raise ValueError("expected frozen M4.3.1 policy")
    manifest = json.loads(HOLDOUT_MANIFEST_PATH.read_text())
    if manifest["policy"]["sha256"] != policy_hash:
        raise ValueError("policy differs from bound holdout manifest")
    bound = {item["target"]: item["sha256"] for item in manifest["eligible_pool"]
             if item["selected"] is True}
    forbidden = {item["target"] for item in manifest["roster"]}
    if set(bound) != forbidden or len(manifest["roster"]) != len(forbidden):
        raise ValueError("held-out manifest roster is inconsistent")
    hashes = [digest for digest, _ in ledgers]
    if any(len(digest) != 64 or digest != digest.lower()
           or any(char not in "0123456789abcdef" for char in digest) for digest in hashes):
        raise ValueError("input ledger hashes must be lowercase SHA-256")
    if mode == "development_calibration_not_validation":
        if len(hashes) != 5 or set(hashes) != DEVELOPMENT_HASHES:
            raise ValueError("development mode requires exactly the five immutable spent ledgers")
    elif mode == "synthetic" and any(digest in DEVELOPMENT_HASHES for digest in hashes):
        raise ValueError("spent M4.2 ledgers require development_calibration_not_validation mode")
    by_checkpoint = {}
    for _, ledger in ledgers:
        checkpoint = ledger.header["checkpoint_id"]
        if mode != "heldout_validation" and checkpoint in forbidden:
            raise ValueError("M4.3 held-out checkpoint requires separate Henry authorization")
        if checkpoint in by_checkpoint:
            raise ValueError("duplicate checkpoint ledger")
        if not ledger.header.get("has_outcomes"):
            raise ValueError("comparison requires an outcome-bearing M4 ledger")
        by_checkpoint[checkpoint] = ledger
    if mode == "heldout_validation" and (
        set(by_checkpoint) != forbidden or len(ledgers) != len(forbidden)
        or any(by_checkpoint[target].header.get("checkpoint_hash") != bound[target]
               for target in forbidden)
    ):
        raise ValueError("held-out ledger checkpoint roster or SHA-256 does not match manifest")
    result = {"schema": "image-atlas-matched-comparison/m4.3.1", "mode": mode,
              "policy_hash": policy_hash, "input_ledger_hashes": sorted(hashes),
              "propositions": {}}
    if mode == "heldout_validation":
        result["authorization"] = authorization
    for proposition, spec in policy["propositions"].items():
        contributions = {checkpoint: _checkpoint(ledger.records, proposition, spec, policy)
                         for checkpoint, ledger in sorted(by_checkpoint.items())}
        identified = {key: value for key, value in contributions.items()
                      if value["status"] == "identified"}
        gates = policy["comparison_protocol"]
        total_pairs = sum(item["diagnostics"]["matched_pairs"] for item in identified.values())
        max_fraction = (max((item["diagnostics"]["matched_pairs"] / total_pairs
                             for item in identified.values()), default=0.0))
        reasons = sorted(_experiment_gate_reasons(
            len(identified), [item["diagnostics"]["matched_pairs"] for item in identified.values()], gates))
        estimate = float(np.mean([item["estimate"] for item in identified.values()])) if identified and not reasons else None
        result["propositions"][proposition] = {
            "status": "abstain" if reasons else "identified", "reasons": reasons,
            "diagnostics": {"supporting_checkpoints": len(identified),
                            "matched_pairs": total_pairs, "largest_checkpoint_pair_fraction": max_fraction},
            "estimate": estimate,
            "cluster_interval_95": (_bootstrap(identified, policy_hash, hashes, policy)
                                     if estimate is not None else None),
            "checkpoints": contributions}
    return result


def compare_ledger_paths(paths: Sequence[str | Path], *, mode: str = "synthetic",
                         authorization: str | None = None) -> dict:
    """Read immutable decision ledgers; never open or generate source images."""
    entries = []
    for path in paths:
        path = Path(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((digest, load_decision_ledger(path)))
    return compare_ledger_data(entries, mode=mode, authorization=authorization)


def write_result_atomic(path: str | Path, result: Mapping[str, Any]) -> str:
    """Write deterministic JSON without exposing a partial result artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(result, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("utf-8")
    partial = path.with_name(path.name + ".partial")
    try:
        with partial.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()

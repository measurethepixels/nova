"""Bounded, reconstructable per-tile decision evidence for Image Atlas M3/M4."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from nas_server.image_atlas_evidence import RealitySupportResult, policy_hash as m3_policy_hash
from nas_server.image_atlas_measurements import AtlasMeasurementResult
from nas_server.image_atlas_opportunity import LocalContrastOutcomes, LocalContrastPrediction

SCHEMA = "image-atlas-decision-ledger/v1"
MAX_EVIDENCE_GROUPS = 16
MAX_FIELDS_PER_GROUP = 32
CONTRIBUTION_NAMES = (
    "tonal_capacity", "headroom", "star_protection", "bound_protection",
    "artifact_ratio", "source_available",
    "bright_core", "halo_risk", "highlight_bound", "near_black_bound",
    "gradient_artifact", "broad_residual_artifact", "seam_artifact",
)
STRATA_NAMES = (
    "intended", "protected", "unknown_conflict", "neutral_reference", "diagnostic",
)


def _number(value: Any) -> float | int | bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    result = float(value)
    return result if math.isfinite(result) else None


def _line(value: Mapping[str, Any]) -> bytes:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (payload + "\n").encode()


@dataclass(frozen=True)
class DecisionLedger:
    header: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]

    def record_at(self, row: int, column: int) -> Mapping[str, Any]:
        width = int(self.header["grid_shape"][1])
        return self.records[row * width + column]


def _records(reality: RealitySupportResult, prediction: LocalContrastPrediction,
             outcomes: LocalContrastOutcomes | None) -> Iterator[dict[str, Any]]:
    arrays = {
        "opportunity": prediction.opportunity, "protection": prediction.protection,
        "inhibition": prediction.inhibition, "candidate_weight": prediction.candidate_weight,
    }
    strata = {name: getattr(prediction, name) for name in STRATA_NAMES}
    outcome_arrays = () if outcomes is None else (
        ("contrast_gain", outcomes.contrast_gain), ("boundary_gain", outcomes.boundary_gain),
        ("clipping_increase", outcomes.clipping_increase),
        ("noise_amplification", outcomes.noise_amplification),
        ("halo_distortion", outcomes.halo_distortion),
    )
    for support in reality.records:
        tile = support.tile
        evidence = []
        for item in support.evidence:
            if len(item.field_ids) > MAX_FIELDS_PER_GROUP:
                raise ValueError("M3 evidence group exceeds ledger bound")
            evidence.append({
                "group": item.dependence_group, "role": item.role.value,
                "applicability": item.applicability.value, "fields": list(item.field_ids),
                "values": [_number(value) for value in item.values],
                "conclusion": item.conclusion,
            })
        if len(evidence) > MAX_EVIDENCE_GROUPS:
            raise ValueError("M3 evidence exceeds ledger bound")
        record: dict[str, Any] = {
            "tile": list(tile),
            "m3": {
                "state": support.state.value, "rule_id": support.rule_id,
                "detection_present": support.detection_present,
                "contradiction_present": support.contradiction_present,
                "sufficient": support.sufficient, "evidence": evidence,
            },
            "m4": {
                "validity": str(prediction.validity[tile]),
                "product_validity": {
                    name: str(values[tile])
                    for name, values in prediction.product_validity.items()
                },
                "requirements": {
                    proposition: {
                        family: str(values[tile]) for family, values in families.items()
                    }
                    for proposition, families in prediction.requirement_state.items()
                },
                "products": {name: _number(array[tile]) for name, array in arrays.items()},
                "contributions": {
                    name: _number(prediction.contributions[name][tile])
                    for name in CONTRIBUTION_NAMES
                },
                "strata": {name: bool(array[tile]) for name, array in strata.items()},
            },
        }
        if outcomes is not None:
            record["outcomes"] = {
                "valid": bool(outcomes.valid[tile]), "halo_valid": bool(outcomes.halo_valid[tile]),
                "products": {name: _number(array[tile]) for name, array in outcome_arrays},
            }
        yield record


def write_decision_ledger_atomic(path: str | Path, measurement: AtlasMeasurementResult,
                                 reality: RealitySupportResult,
                                 prediction: LocalContrastPrediction, *, scale: int,
                                 outcomes: LocalContrastOutcomes | None = None) -> str:
    """Write deterministic gzip JSONL and return its SHA-256 digest."""
    path = Path(path)
    shape = tuple(int(value) for value in reality.states.shape)
    expected = shape[0] * shape[1]
    arrays = [prediction.opportunity, prediction.protection, prediction.inhibition,
              prediction.candidate_weight, prediction.validity]
    arrays += [getattr(prediction, name) for name in STRATA_NAMES]
    arrays += [prediction.contributions.get(name) for name in CONTRIBUTION_NAMES]
    arrays += list(prediction.product_validity.values())
    arrays += [values for families in prediction.requirement_state.values()
               for values in families.values()]
    if len(reality.records) != expected or any(value is None or value.shape != shape for value in arrays):
        raise ValueError("ledger inputs must contain one value and one M3 record per tile")
    if outcomes is not None and any(getattr(outcomes, name).shape != shape for name in (
            "contrast_gain", "boundary_gain", "clipping_increase", "noise_amplification",
            "halo_distortion", "valid", "halo_valid")):
        raise ValueError("outcome fields must share the ledger tile grid")
    header = {
        "schema": SCHEMA, "checkpoint_id": measurement.snapshot.checkpoint_id,
        "checkpoint_hash": measurement.snapshot.checkpoint_hash,
        "grid_shape": list(shape), "tile_count": expected, "scale": scale,
        "m3_policy_hash": m3_policy_hash(), "m4_policy_hash": prediction.policy_hash,
        "candidate": prediction.candidate, "has_outcomes": outcomes is not None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    digest = hashlib.sha256()
    try:
        with partial.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                compressed.write(_line(header))
                for value in _records(reality, prediction, outcomes):
                    compressed.write(_line(value))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_decision_ledger(path: str | Path, *, checkpoint_hash: str | None = None,
                         m4_policy_hash: str | None = None) -> DecisionLedger:
    """Load and structurally validate a ledger without needing source arrays."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            values = [json.loads(line) for line in stream if line.strip()]
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid or truncated decision ledger") from exc
    if not values or values[0].get("schema") != SCHEMA:
        raise ValueError("unsupported decision ledger schema")
    header, records = values[0], tuple(values[1:])
    shape = tuple(header.get("grid_shape", ()))
    if len(shape) != 2 or any(type(value) is not int or value <= 0 for value in shape):
        raise ValueError("invalid ledger grid shape")
    if header.get("tile_count") != shape[0] * shape[1] or len(records) != header["tile_count"]:
        raise ValueError("incomplete decision ledger")
    if checkpoint_hash is not None and header.get("checkpoint_hash") != checkpoint_hash:
        raise ValueError("decision ledger checkpoint identity mismatch")
    if m4_policy_hash is not None and header.get("m4_policy_hash") != m4_policy_hash:
        raise ValueError("decision ledger policy identity mismatch")
    expected_tiles = [[row, column] for row in range(shape[0]) for column in range(shape[1])]
    if [record.get("tile") for record in records] != expected_tiles:
        raise ValueError("decision ledger tile roster is incomplete or out of order")
    for record in records:
        m3, m4 = record.get("m3", {}), record.get("m4", {})
        evidence = m3.get("evidence", ())
        if len(evidence) > MAX_EVIDENCE_GROUPS or any(
                len(item.get("fields", ())) > MAX_FIELDS_PER_GROUP for item in evidence):
            raise ValueError("decision ledger evidence exceeds schema bounds")
        if set(m4.get("contributions", {})) != set(CONTRIBUTION_NAMES):
            raise ValueError("decision ledger contribution roster mismatch")
        if set(m4.get("strata", {})) != set(STRATA_NAMES):
            raise ValueError("decision ledger stratum roster mismatch")
        if set(m4.get("product_validity", {})) != set(
                record["m4"]["requirements"]):
            raise ValueError("decision ledger requirement roster mismatch")
    return DecisionLedger(header=header, records=records)

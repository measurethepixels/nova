"""Explainable, observe-only Image Atlas evidence fusion (Milestone 3).

This module turns Milestone-2 measurements into an ordinal proposition about
real structure at a declared scale.  It deliberately does not produce a mask,
probability, processing permission, or pixel change.  Measurements derived
from the same checkpoint remain one correlated evidence source, so they can
earn at most ``tentative`` without future independent-capture evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import numpy as np

from nas_server.image_atlas_measurements import (
    ANGULAR_SIGMAS_ARCSEC,
    AtlasMeasurementResult,
)


class RealitySupport(str, Enum):
    SUPPORTED = "supported"
    TENTATIVE = "tentative"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class EvidenceRole(str, Enum):
    DETECTION = "detection"
    ALTERNATIVE_EXPLANATION = "alternative_explanation"
    CONTEXT = "context"
    VALIDITY = "validity"
    CORROBORATION = "corroboration"


class Applicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EvidenceItem:
    dependence_group: str
    role: EvidenceRole
    applicability: Applicability
    field_ids: tuple[str, ...]
    values: tuple[float, ...]
    conclusion: str


@dataclass(frozen=True)
class TileSupportRecord:
    tile: tuple[int, int]
    scale: int
    scale_sigma_arcsec: float
    proposition: str
    state: RealitySupport
    rule_id: str
    detection_present: bool
    contradiction_present: bool
    sufficient: bool
    evidence: tuple[EvidenceItem, ...]
    observe_only: bool = True


@dataclass(frozen=True)
class RealitySupportResult:
    states: np.ndarray
    records: tuple[TileSupportRecord, ...]
    state_codes: Mapping[int, str]

    def record_at(self, row: int, column: int) -> TileSupportRecord:
        width = self.states.shape[1]
        return self.records[row * width + column]


# Predeclared, checkpoint-independent MVP policy.  These are measurement-domain
# thresholds, not fitted probabilities or target-specific tuning.
MIN_VALID_FRACTION = 0.80
MIN_USABLE_PIXELS = 16
MIN_EDGE_DISTANCE_PX = 2.0
DETECTION_SCORE = 3.0
MIN_ADJACENT_COHERENCE = 0.10
GRADIENT_TO_NOISE = 0.50
SEAM_TO_NOISE = 0.20
BROAD_RESIDUAL_TO_NOISE = 25.0

M3_POLICY_VERSION = "m3.1-frozen"


def policy_hash() -> str:
    """Return the identity of the frozen M3 rules recorded by decision ledgers."""
    policy = {
        "version": M3_POLICY_VERSION,
        "thresholds": {
            "min_valid_fraction": MIN_VALID_FRACTION,
            "min_usable_pixels": MIN_USABLE_PIXELS,
            "min_edge_distance_px": MIN_EDGE_DISTANCE_PX,
            "detection_score": DETECTION_SCORE,
            "min_adjacent_coherence": MIN_ADJACENT_COHERENCE,
            "gradient_to_noise": GRADIENT_TO_NOISE,
            "seam_to_noise": SEAM_TO_NOISE,
            "broad_residual_to_noise": BROAD_RESIDUAL_TO_NOISE,
        },
        "rules": (
            "insufficient_required_evidence", "detection_with_material_alternative",
            "same_checkpoint_detection_only", "no_positive_detection",
        ),
        "authority": "observe_only_no_pixel_control",
    }
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()

_STATE_CODE = {
    RealitySupport.UNKNOWN: 0,
    RealitySupport.TENTATIVE: 1,
    RealitySupport.CONFLICT: 2,
    RealitySupport.SUPPORTED: 3,
}


def _value(arrays: Mapping[str, np.ndarray], name: str, tile: tuple[int, int]) -> float:
    return float(arrays[name][tile])


def _available(values: tuple[float, ...]) -> bool:
    return all(np.isfinite(value) for value in values)


def assess_real_structure(
    measurement: AtlasMeasurementResult,
    *,
    scale: int,
    proposition: str = "real_structure",
) -> RealitySupportResult:
    """Assess scale-matched structure without treating correlated fields as votes.

    ``supported`` is intentionally unreachable in Milestone 3: all available
    inputs are transforms of one checkpoint.  The enum reserves the public
    ordinal state for a later, genuinely independent-capture evidence group.
    """
    if scale not in range(len(ANGULAR_SIGMAS_ARCSEC)):
        raise ValueError(f"scale must be in 0..{len(ANGULAR_SIGMAS_ARCSEC) - 1}")
    if proposition != "real_structure":
        raise ValueError("Milestone 3 supports only the real_structure proposition")

    arrays = measurement.arrays
    required = (
        "support.usable_pixels", "support.valid_fraction", "support.edge_distance_px",
        f"noise.residual_sigma_s{scale}", f"detectability.score_s{scale}",
        f"structure.band_energy_s{scale}", "structure.adjacent_scale_coherence",
        "risk.broad_gradient", "risk.broad_model_residual",
        "risk.seam_discontinuity", "provenance.post_processing",
    )
    missing = [name for name in required if name not in arrays]
    if missing:
        raise ValueError(f"measurement is missing required fields: {missing}")

    shape = arrays["support.usable_pixels"].shape
    if any(arrays[name].shape != shape for name in required):
        raise ValueError("required evidence fields do not share one tile grid")

    states = np.zeros(shape, dtype=np.uint8)
    records: list[TileSupportRecord] = []
    for row in range(shape[0]):
        for column in range(shape[1]):
            tile = (row, column)
            support_fields = (
                "support.usable_pixels", "support.valid_fraction", "support.edge_distance_px")
            support_values = tuple(_value(arrays, name, tile) for name in support_fields)
            sufficient = (
                _available(support_values)
                and support_values[0] >= MIN_USABLE_PIXELS
                and support_values[1] >= MIN_VALID_FRACTION
                and support_values[2] >= MIN_EDGE_DISTANCE_PX
            )
            support = EvidenceItem(
                "support_geometry", EvidenceRole.VALIDITY,
                Applicability.APPLICABLE if _available(support_values) else Applicability.UNAVAILABLE,
                support_fields, support_values, "sufficient" if sufficient else "insufficient")

            detection_fields = (
                f"noise.residual_sigma_s{scale}", f"detectability.score_s{scale}",
                f"structure.band_energy_s{scale}", "structure.adjacent_scale_coherence")
            detection_values = tuple(_value(arrays, name, tile) for name in detection_fields)
            detection_available = _available(detection_values)
            detection_present = (
                detection_available
                and detection_values[0] > 0.0
                and detection_values[1] >= DETECTION_SCORE
                and detection_values[2] > 0.0
                and detection_values[3] >= MIN_ADJACENT_COHERENCE
            )
            detection = EvidenceItem(
                "multiscale_residual", EvidenceRole.DETECTION,
                Applicability.APPLICABLE if detection_available else Applicability.UNAVAILABLE,
                detection_fields, detection_values,
                "positive" if detection_present else "not_positive")

            alternative_fields = (
                "risk.broad_gradient", "risk.broad_model_residual",
                "risk.seam_discontinuity", "provenance.post_processing")
            alternative_values = tuple(_value(arrays, name, tile) for name in alternative_fields)
            alternative_available = _available(alternative_values) and detection_available
            noise = max(detection_values[0], 1e-12) if detection_available else np.nan
            # post_processing is frame-level provenance, not a per-tile artifact
            # measurement.  Record it with the evidence but do not let it create
            # a contradiction independently of the three spatial risk signals.
            contradiction_present = bool(
                alternative_available and (
                    alternative_values[0] / noise >= GRADIENT_TO_NOISE
                    or alternative_values[1] / noise >= BROAD_RESIDUAL_TO_NOISE
                    or alternative_values[2] / noise >= SEAM_TO_NOISE
                ))
            alternative = EvidenceItem(
                "artifact_alternative", EvidenceRole.ALTERNATIVE_EXPLANATION,
                Applicability.APPLICABLE if alternative_available else Applicability.UNAVAILABLE,
                alternative_fields, alternative_values,
                "material" if contradiction_present else "not_material")

            # Tonal values describe applicability/context only.  They never add a
            # positive vote.  Compact-source evidence is not applicable to this
            # generic morphology-neutral proposition.
            tonal_fields = tuple(name for name in ("intensity.center", "intensity.tonal_range")
                                 if name in arrays)
            tonal_values = tuple(_value(arrays, name, tile) for name in tonal_fields)
            tonal = EvidenceItem(
                "tonal_context", EvidenceRole.CONTEXT,
                Applicability.APPLICABLE if tonal_fields and _available(tonal_values)
                else Applicability.UNAVAILABLE,
                tonal_fields, tonal_values, "context_only")
            compact = EvidenceItem(
                "compact_source", EvidenceRole.CONTEXT, Applicability.NOT_APPLICABLE,
                (), (), "generic_real_structure_is_morphology_neutral")
            corroboration = EvidenceItem(
                "independent_capture", EvidenceRole.CORROBORATION,
                Applicability.UNAVAILABLE, (), (), "not_available_in_milestone_3")

            sufficient = sufficient and detection_available and alternative_available
            if not sufficient:
                state, rule = RealitySupport.UNKNOWN, "insufficient_required_evidence"
            elif detection_present and contradiction_present:
                state, rule = RealitySupport.CONFLICT, "detection_with_material_alternative"
            elif detection_present:
                state, rule = RealitySupport.TENTATIVE, "same_checkpoint_detection_only"
            else:
                state, rule = RealitySupport.UNKNOWN, "no_positive_detection"

            states[tile] = _STATE_CODE[state]
            records.append(TileSupportRecord(
                tile=tile, scale=scale, scale_sigma_arcsec=ANGULAR_SIGMAS_ARCSEC[scale],
                proposition=proposition, state=state, rule_id=rule,
                detection_present=detection_present,
                contradiction_present=contradiction_present, sufficient=sufficient,
                evidence=(support, detection, alternative, tonal, compact, corroboration),
            ))
    return RealitySupportResult(
        states=states, records=tuple(records),
        state_codes={code: state.value for state, code in _STATE_CODE.items()},
    )

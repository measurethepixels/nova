"""Observe-only Image Atlas local-contrast predictions and outcome evidence.

Milestone 4 derives three separate products from pre-operation Atlas evidence.
The candidate weight is a prediction for evaluation, never a mask or processing
permission.  Candidate output is consumed only by :func:`measure_outcomes`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from nas_server.image_atlas_evidence import RealitySupport, RealitySupportResult
from nas_server.image_atlas_measurements import AtlasMeasurementResult

POLICY_VERSION = "m4.2-frozen"
PRIMARY_CANDIDATE = {
    "process_family": "clahe", "variant_id": "clahe_mild",
    "engine": "seti_astro", "fn": "clahe",
    "params": {"clip_limit": 1.5, "tile_size": 8},
}
POLICY = {
    "version": POLICY_VERSION,
    "candidate": PRIMARY_CANDIDATE,
    "thresholds": {
        "tonal_range_full": 0.12,
        "highlight_headroom_full": 0.20,
        "shadow_headroom_full": 0.05,
        "highlight_headroom_protect": 0.08,
        "near_black_protect": 0.10,
        "halo_wing_risk_full": 12.0,
        "artifact_ratio_full": 1.0,
        "gradient_to_noise_full": 0.50,
        "broad_residual_to_noise_full": 25.0,
        "seam_to_noise_full": 0.20,
        "neutral_detectability_max": 2.0,
        "neutral_tonal_range_max": 0.04,
    },
    "evidence_requirements": {
        "diffuse_opportunity": {
            "tonal_context": "required", "real_structure": "required",
            "compact_source": "optional-protective", "tonal_bounds": "not_applicable",
            "artifact_risk": "not_applicable",
        },
        "stellar_protection": {
            "tonal_context": "not_applicable", "real_structure": "not_applicable",
            "compact_source": "required", "tonal_bounds": "not_applicable",
            "artifact_risk": "not_applicable",
        },
        "tonal_bound_protection": {
            "tonal_context": "not_applicable", "real_structure": "not_applicable",
            "compact_source": "not_applicable", "tonal_bounds": "required",
            "artifact_risk": "not_applicable",
        },
        "artifact_inhibition": {
            "tonal_context": "not_applicable", "real_structure": "required",
            "compact_source": "not_applicable", "tonal_bounds": "not_applicable",
            "artifact_risk": "required",
        },
    },
    "comparison_protocol": {
        "minimum_usable_tiles_per_stratum": 64,
        "required_strata": [
            "intended", "other_valid", "protected_or_inhibited", "other_safe",
        ],
        "insufficient_support_result": "abstain",
    },
    "authority": "observe_only_no_pixel_control",
}


def policy_hash() -> str:
    payload = json.dumps(POLICY, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def run_primary_candidate(input_fits: str | Path, output_fits: str | Path) -> dict:
    """Run the frozen ontology candidate through NOVA's existing executor."""
    ontology_path = Path(__file__).with_name("processing_ontology.json")
    ontology = json.loads(ontology_path.read_text())
    variants = ontology["processing_steps"]["clahe"]["experiment_variants"]
    variant = next(item for item in variants if item["id"] == "clahe_mild")
    declared = {"process_family": "clahe", "variant_id": variant["id"],
                "engine": variant.get("engine", "seti_astro"), "fn": variant["fn"],
                "params": variant["params"]}
    if declared != PRIMARY_CANDIDATE:
        raise RuntimeError("frozen M4 candidate no longer matches the ontology")
    from nas_server.experiments import _run_variant
    return _run_variant(variant, Path(input_fits), Path(output_fits))


class ProductValidity(str, Enum):
    VALID = "valid"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LocalContrastPrediction:
    opportunity: np.ndarray
    protection: np.ndarray
    inhibition: np.ndarray
    candidate_weight: np.ndarray
    validity: np.ndarray
    intended: np.ndarray
    protected: np.ndarray
    unknown_conflict: np.ndarray
    neutral_reference: np.ndarray
    diagnostic: np.ndarray
    contributions: Mapping[str, np.ndarray]
    product_validity: Mapping[str, np.ndarray]
    requirement_state: Mapping[str, Mapping[str, np.ndarray]]
    policy_hash: str
    candidate: Mapping[str, object]
    observe_only: bool = True


@dataclass(frozen=True)
class LocalContrastOutcomes:
    contrast_gain: np.ndarray
    boundary_gain: np.ndarray
    clipping_increase: np.ndarray
    noise_amplification: np.ndarray
    halo_distortion: np.ndarray
    valid: np.ndarray
    halo_valid: np.ndarray


@dataclass(frozen=True)
class BoundaryFidelityRecord:
    target: str
    description: str
    tiles: tuple[tuple[int, int], ...]
    opportunity: tuple[float, ...]
    protection: tuple[float, ...]
    changes_across_boundary: bool
    limitation: str


@dataclass(frozen=True)
class ComparisonSupport:
    result: str
    counts: Mapping[str, int]
    minimum_per_stratum: int
    insufficient_strata: tuple[str, ...]


def _finite(*values: np.ndarray) -> np.ndarray:
    result = np.ones(values[0].shape, dtype=bool)
    for value in values:
        result &= np.isfinite(value)
    return result


def _clip01(value: np.ndarray) -> np.ndarray:
    return np.clip(value, 0.0, 1.0).astype(np.float32)


def _resolve_requirements(family_available: Mapping[str, np.ndarray]):
    """Resolve the versioned requirement matrix generically for every tile."""
    shape = next(iter(family_available.values())).shape
    validity = {}
    states = {}
    for proposition, requirements in POLICY["evidence_requirements"].items():
        required_missing = np.zeros(shape, dtype=bool)
        optional_missing = np.zeros(shape, dtype=bool)
        resolved = {}
        for family, requirement in requirements.items():
            available = family_available[family]
            if requirement == "not_applicable":
                resolved[family] = np.full(shape, "not_applicable", dtype="<U24")
            else:
                resolved[family] = np.where(available, "available", "unavailable")
                if requirement == "required":
                    required_missing |= ~available
                elif requirement == "optional-protective":
                    optional_missing |= ~available
                else:
                    raise ValueError(f"unknown evidence requirement: {requirement}")
        validity[proposition] = np.where(
            required_missing, ProductValidity.UNKNOWN.value,
            np.where(optional_missing, ProductValidity.PARTIAL.value,
                     ProductValidity.VALID.value))
        states[proposition] = resolved
    return validity, states


def derive_local_contrast_prediction(
    measurement: AtlasMeasurementResult,
    reality: RealitySupportResult,
    *,
    scale: int,
) -> LocalContrastPrediction:
    """Derive a frozen, non-authoritative CLAHE prediction from pre-op evidence."""
    arrays = measurement.arrays
    names = (
        "intensity.q05", "intensity.tonal_range", "intensity.highlight_headroom",
        "intensity.near_black_fraction", "stars.bright_core_count",
        "stars.source_count", "stars.halo_wing_risk", f"noise.residual_sigma_s{scale}",
        f"detectability.score_s{scale}", "risk.broad_gradient",
        "risk.broad_model_residual", "risk.seam_discontinuity",
    )
    missing = [name for name in names if name not in arrays]
    if missing:
        raise ValueError(f"measurement is missing required M4 fields: {missing}")
    shape = arrays["intensity.tonal_range"].shape
    if reality.states.shape != shape or any(arrays[name].shape != shape for name in names):
        raise ValueError("M3 evidence and M4 fields must share one tile grid")

    t = POLICY["thresholds"]
    tonal = arrays["intensity.tonal_range"]
    highlight = arrays["intensity.highlight_headroom"]
    shadow = arrays["intensity.q05"]
    near_black = arrays["intensity.near_black_fraction"]
    bright = arrays["stars.bright_core_count"]
    source_count = arrays["stars.source_count"]
    halo_raw = arrays["stars.halo_wing_risk"]
    # M2 deliberately stores shape metrics as NaN when a supported source
    # extraction found zero sources in a tile.  That is known absence, not an
    # unavailable worker result; source_count disambiguates the two cases.
    halo = np.where(source_count == 0, 0.0, halo_raw)
    noise = arrays[f"noise.residual_sigma_s{scale}"]
    detectability = arrays[f"detectability.score_s{scale}"]
    gradient = arrays["risk.broad_gradient"]
    residual = arrays["risk.broad_model_residual"]
    seam = arrays["risk.seam_discontinuity"]

    tonal_available = _finite(tonal, highlight, shadow, detectability)
    bounds_available = _finite(highlight, near_black)
    compact_available = _finite(bright, source_count) & (
        (source_count == 0) | np.isfinite(halo_raw))
    artifact_available = _finite(noise, gradient, residual, seam)
    tentative = np.array([
        record.state is RealitySupport.TENTATIVE for record in reality.records
    ], dtype=bool).reshape(shape)
    conflict_or_unknown = np.array([
        record.state in {RealitySupport.CONFLICT, RealitySupport.UNKNOWN}
        or not record.sufficient for record in reality.records
    ], dtype=bool).reshape(shape)
    structure_available = np.array([
        record.sufficient for record in reality.records
    ], dtype=bool).reshape(shape)
    product_validity, requirement_state = _resolve_requirements({
        "tonal_context": tonal_available,
        "real_structure": structure_available,
        "compact_source": compact_available,
        "tonal_bounds": bounds_available,
        "artifact_risk": artifact_available,
    })
    opportunity_available = (
        product_validity["diffuse_opportunity"] != ProductValidity.UNKNOWN.value)

    tonal_capacity = _clip01(1.0 - tonal / float(t["tonal_range_full"]))
    headroom = np.minimum(
        _clip01(highlight / float(t["highlight_headroom_full"])),
        _clip01(shadow / float(t["shadow_headroom_full"])))
    opportunity = _clip01(tonal_capacity * headroom)
    opportunity[~tentative | ~opportunity_available] = 0.0

    bright_core = (bright > 0).astype(np.float32)
    halo_risk = _clip01(halo / float(t["halo_wing_risk_full"]))
    highlight_bound = _clip01(
        (float(t["highlight_headroom_protect"]) - highlight)
        / float(t["highlight_headroom_protect"]))
    near_black_bound = _clip01(near_black / float(t["near_black_protect"]))
    star_protection = np.maximum(bright_core, halo_risk)
    star_protection[~compact_available] = 0.0
    bound_protection = np.maximum(highlight_bound, near_black_bound)
    bound_protection[~bounds_available] = 0.0
    protection = _clip01(np.maximum(star_protection, bound_protection))

    safe_noise = np.maximum(noise, 1e-12)
    gradient_artifact = gradient / (safe_noise * float(t["gradient_to_noise_full"]))
    residual_artifact = residual / (
        safe_noise * float(t["broad_residual_to_noise_full"]))
    seam_artifact = seam / (safe_noise * float(t["seam_to_noise_full"]))
    artifact = np.maximum.reduce((gradient_artifact, residual_artifact, seam_artifact))
    inhibition = _clip01(artifact / float(t["artifact_ratio_full"]))
    inhibition[conflict_or_unknown] = 1.0
    inhibition[~artifact_available | ~structure_available] = 1.0

    intended = opportunity_available & tentative & (opportunity > 0.0)
    protected = ((compact_available & (star_protection > 0.0))
                 | (bounds_available & (bound_protection > 0.0)))
    unknown_conflict = ~tonal_available | ~artifact_available | conflict_or_unknown
    diagnostic = artifact_available & (artifact >= 1.0)
    neutral = (tonal_available & ~tentative
               & (detectability <= float(t["neutral_detectability_max"]))
               & (tonal <= float(t["neutral_tonal_range_max"])))
    weight = _clip01(opportunity * (1.0 - protection) * (1.0 - inhibition))
    weight[unknown_conflict] = 0.0
    validity = np.where(
        ~tonal_available | ~artifact_available | ~structure_available,
        ProductValidity.UNKNOWN.value,
        np.where(compact_available, ProductValidity.VALID.value,
                 ProductValidity.PARTIAL.value))
    return LocalContrastPrediction(
        opportunity, protection, inhibition, weight, validity,
        intended, protected, unknown_conflict, neutral, diagnostic,
        {
            "tonal_capacity": tonal_capacity,
            "headroom": headroom,
            "star_protection": star_protection,
            "bound_protection": bound_protection,
            "artifact_ratio": artifact,
            "source_available": compact_available,
            "bright_core": bright_core,
            "halo_risk": halo_risk,
            "highlight_bound": highlight_bound,
            "near_black_bound": near_black_bound,
            "gradient_artifact": gradient_artifact,
            "broad_residual_artifact": residual_artifact,
            "seam_artifact": seam_artifact,
        },
        product_validity, requirement_state,
        policy_hash(), dict(PRIMARY_CANDIDATE))


def measure_outcomes(before: AtlasMeasurementResult, after: AtlasMeasurementResult,
                     *, scale: int) -> LocalContrastOutcomes:
    """Measure post-candidate effects independently of precomputed role labels."""
    b, a = before.arrays, after.arrays
    shape = b["intensity.tonal_range"].shape
    required = (
        "intensity.tonal_range", "intensity.highlight_headroom",
        "intensity.near_black_fraction", f"structure.band_energy_s{scale}",
        f"noise.residual_sigma_s{scale}",
    )
    if any(name not in a or name not in b for name in required):
        raise ValueError("before/after measurement is missing an outcome field")
    if any(b[name].shape != shape or a[name].shape != shape for name in required):
        raise ValueError("before/after outcome fields must share one tile grid")
    star_names = ("stars.source_count", "stars.halo_wing_risk")
    if any(name not in a or name not in b for name in star_names):
        raise ValueError("before/after measurement is missing star outcome fields")
    b_halo = np.where(b["stars.source_count"] == 0, 0.0, b["stars.halo_wing_risk"])
    a_halo = np.where(a["stars.source_count"] == 0, 0.0, a["stars.halo_wing_risk"])
    valid = _finite(*(b[name] for name in required), *(a[name] for name in required))
    halo_valid = _finite(b["stars.source_count"], a["stars.source_count"], b_halo, a_halo)
    contrast = a["intensity.tonal_range"] - b["intensity.tonal_range"]
    boundary = a[f"structure.band_energy_s{scale}"] - b[f"structure.band_energy_s{scale}"]
    clipping = np.maximum(
        b["intensity.highlight_headroom"] - a["intensity.highlight_headroom"], 0.0)
    clipping += np.maximum(
        a["intensity.near_black_fraction"] - b["intensity.near_black_fraction"], 0.0)
    noise_amp = (a[f"noise.residual_sigma_s{scale}"]
                 / np.maximum(b[f"noise.residual_sigma_s{scale}"], 1e-12) - 1.0)
    halo = a_halo - b_halo
    outputs = [contrast, boundary, clipping, noise_amp, halo]
    for output in outputs[:-1]:
        output[~valid] = np.nan
    halo[~halo_valid] = np.nan
    return LocalContrastOutcomes(*(value.astype(np.float32) for value in outputs),
                                 valid, halo_valid)


def assess_comparison_support(prediction: LocalContrastPrediction,
                              outcomes: LocalContrastOutcomes) -> ComparisonSupport:
    """Apply the frozen experiment-level identifiability rule."""
    unsafe = prediction.protected | prediction.unknown_conflict
    masks = {
        "intended": outcomes.valid & prediction.intended,
        "other_valid": outcomes.valid & ~prediction.intended,
        "protected_or_inhibited": outcomes.valid & unsafe,
        "other_safe": outcomes.valid & ~unsafe,
    }
    counts = {name: int(np.count_nonzero(mask)) for name, mask in masks.items()}
    minimum = int(POLICY["comparison_protocol"]["minimum_usable_tiles_per_stratum"])
    required = POLICY["comparison_protocol"]["required_strata"]
    insufficient = tuple(name for name in required if counts[name] < minimum)
    return ComparisonSupport(
        result="abstain" if insufficient else "identified",
        counts=counts, minimum_per_stratum=minimum,
        insufficient_strata=insufficient,
    )


def inspect_boundary_fidelity(target: str, description: str,
                              prediction: LocalContrastPrediction,
                              tiles: Sequence[tuple[int, int]]) -> BoundaryFidelityRecord:
    """Record whether fixed-grid fields vary across a predeclared real boundary."""
    if len(tiles) < 2:
        raise ValueError("a boundary inspection requires at least two predeclared tiles")
    opportunity = tuple(float(prediction.opportunity[tile]) for tile in tiles)
    protection = tuple(float(prediction.protection[tile]) for tile in tiles)
    change = ((max(opportunity) - min(opportunity) >= 0.15)
              or (max(protection) - min(protection) >= 0.15))
    limitation = ("fixed tiles distinguish the sampled boundary"
                  if change else
                  "fixed tiles mix the sampled boundary; evaluate a finer/adaptive representation")
    return BoundaryFidelityRecord(target, description, tuple(tiles), opportunity,
                                  protection, change, limitation)

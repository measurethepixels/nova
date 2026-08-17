"""Minimal launch contract for NOVA Handbook and validation content.

This module is the compatibility seam requested by issue #284.  It gives the
launch-scoped content, renderer, and validation-ledger work one small versioned
contract without attempting to implement the canonical registry planned in
issues #262/#263.  That later registry may replace these dataclasses; consumers
should therefore depend on the public names and ``SCHEMA_VERSION``, not on a
particular storage format or dataclass implementation.

Editorial state deliberately lives here (or in content built against this
contract), never in a processing run's ``run.log``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


SCHEMA_VERSION = 1


class ProcessFamily(StrEnum):
    """The launch families exercised by the M66 evidence set."""

    PEDESTAL_REMOVAL = "pedestal_removal"
    COSMETIC_CORRECTION = "cosmetic_correction"
    BACKGROUND_EXTRACTION = "background_extraction"
    COLOR_CALIBRATION = "color_calibration"
    DECONVOLUTION = "deconvolution"
    DENOISE = "denoise"
    STAR_CORRECTION = "star_correction"
    STRETCH = "stretch"


class ProvenanceLabel(StrEnum):
    """Evidence labels adopted verbatim from the handbook strategy."""

    NOVA_EXECUTION_RECORD = "NOVA execution record"
    NOVA_SOURCE_CONFIRMED = "NOVA source-confirmed"
    ARTIFACT_CONFIRMED = "Artifact-confirmed"
    VENDOR_DOCUMENTED = "Vendor-documented"
    PRIMARY_RESEARCH_SUPPORTED = "Primary-research-supported"
    REASONED_TRANSLATION = "Reasoned translation"
    TOOL_TESTED = "Tool-tested"
    HENRY_VALIDATED = "Jeff validated"
    RESULT_COMPARED = "Result-compared"
    VISUAL_JUDGMENT = "Visual judgment"
    UNKNOWN_UNVERIFIED = "Unknown/unverified"


class EquivalenceClass(StrEnum):
    """Strength of a cross-tool or cross-host reproduction claim."""

    EXACT_REPLAY = "Exact replay"
    SAME_ENGINE_ADAPTED_HOST = "Same engine, adapted host"
    ALGORITHMICALLY_EQUIVALENT = "Algorithmically equivalent"
    FUNCTIONAL_ALTERNATIVE = "Functional alternative"
    CONCEPTUAL_SUBSTITUTE = "Conceptual substitute"
    NO_DIRECT_EQUIVALENT = "No direct equivalent"


class ValidationLevel(StrEnum):
    """Independent dimensions of validation; a generic PASS is insufficient."""

    PROCEDURE = "Procedure"
    PARAMETERS = "Parameters"
    STATE = "State"
    ARTIFACT = "Artifact"
    BEHAVIOR = "Behavior"
    EQUIVALENCE = "Equivalence"
    ACCEPTANCE = "Acceptance"


class ValidationOutcome(StrEnum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _require_text_items(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    for value in values:
        _require_text(value, field_name)


def _require_schema_version(value: int) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {SCHEMA_VERSION}, got {value!r}"
        )


@dataclass(frozen=True)
class EvidenceReference:
    """A traceable source or project artifact supporting one or more claims."""

    reference_id: str
    title: str
    locator: str
    provenance: ProvenanceLabel

    def __post_init__(self) -> None:
        _require_text(self.reference_id, "reference_id")
        _require_text(self.title, "title")
        _require_text(self.locator, "locator")
        if not isinstance(self.provenance, ProvenanceLabel):
            raise ValueError("provenance must be a ProvenanceLabel")


@dataclass(frozen=True)
class ToolGuidance:
    """One tool-specific path within a process-family article."""

    tool_id: str
    tool_version: str
    host: str
    instructions: tuple[str, ...]
    controls_and_starting_ranges: tuple[tuple[str, str], ...]
    expected_result: str
    failure_modes: tuple[str, ...]
    recovery: tuple[str, ...]
    mask_support: str
    equivalence: EquivalenceClass
    provenance: tuple[ProvenanceLabel, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.tool_id, "tool_id")
        _require_text(self.tool_version, "tool_version")
        _require_text(self.host, "host")
        _require_text_items(self.instructions, "instructions")
        if not self.instructions:
            raise ValueError("instructions must contain at least one step")
        if not isinstance(self.controls_and_starting_ranges, tuple):
            raise ValueError("controls_and_starting_ranges must be a tuple")
        for name, value in self.controls_and_starting_ranges:
            _require_text(name, "control name")
            _require_text(value, "control starting range")
        _require_text(self.expected_result, "expected_result")
        _require_text_items(self.failure_modes, "failure_modes")
        _require_text_items(self.recovery, "recovery")
        _require_text(self.mask_support, "mask_support")
        if not isinstance(self.equivalence, EquivalenceClass):
            raise ValueError("equivalence must be an EquivalenceClass")
        if not self.provenance:
            raise ValueError("provenance must contain at least one label")
        if any(not isinstance(value, ProvenanceLabel) for value in self.provenance):
            raise ValueError("provenance entries must be ProvenanceLabel values")
        _require_text_items(self.source_ids, "source_ids")
        if (
            not self.source_ids
            and ProvenanceLabel.UNKNOWN_UNVERIFIED not in self.provenance
        ):
            raise ValueError(
                "tool guidance without source_ids must be labeled Unknown/unverified"
            )


@dataclass(frozen=True)
class HandbookArticle:
    """Process-first guidance; never a target- or run-specific recipe."""

    article_id: str
    schema_version: int
    revision: int
    process_family: ProcessFamily
    purpose: str
    observable_symptoms: tuple[str, ...]
    intended_output: str
    limits: tuple[str, ...]
    required_input_state: tuple[str, ...]
    nova_action: str
    nova_evidence_ids: tuple[str, ...]
    use_when: tuple[str, ...]
    skip_when: tuple[str, ...]
    scientific_and_aesthetic_notes: tuple[str, ...]
    tool_guidance: tuple[ToolGuidance, ...]
    measurements: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    sources: tuple[EvidenceReference, ...]
    validation_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.article_id, "article_id")
        if not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if not isinstance(self.process_family, ProcessFamily):
            raise ValueError("process_family must be a ProcessFamily")
        _require_text(self.purpose, "purpose")
        _require_text_items(self.observable_symptoms, "observable_symptoms")
        _require_text(self.intended_output, "intended_output")
        for field_name in (
            "limits",
            "required_input_state",
            "nova_evidence_ids",
            "use_when",
            "skip_when",
            "scientific_and_aesthetic_notes",
            "measurements",
            "acceptance_criteria",
            "validation_event_ids",
        ):
            _require_text_items(getattr(self, field_name), field_name)
        _require_text(self.nova_action, "nova_action")
        if not self.tool_guidance:
            raise ValueError("tool_guidance must contain at least one path")
        if any(not isinstance(item, ToolGuidance) for item in self.tool_guidance):
            raise ValueError("tool_guidance entries must be ToolGuidance values")
        if not self.sources:
            raise ValueError("sources must contain at least one reference")
        if any(not isinstance(item, EvidenceReference) for item in self.sources):
            raise ValueError("sources entries must be EvidenceReference values")
        source_ids = {source.reference_id for source in self.sources}
        missing = {
            source_id
            for guidance in self.tool_guidance
            for source_id in guidance.source_ids
            if source_id not in source_ids
        }
        if missing:
            raise ValueError(f"tool_guidance references unknown sources: {sorted(missing)}")


@dataclass(frozen=True)
class ToolIdentity:
    """Version identity needed to interpret or reproduce a validation event."""

    tool: str
    tool_version: str
    host: str
    host_version: str
    operating_system: str
    module_or_cli: str
    model_version: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "tool",
            "tool_version",
            "host",
            "host_version",
            "operating_system",
            "module_or_cli",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.model_version is not None:
            _require_text(self.model_version, "model_version")


@dataclass(frozen=True)
class ValidationEvent:
    """Append-only evidence tied to one claim revision and tool identity."""

    event_id: str
    schema_version: int
    validator: str
    validated_on: date
    claim_id: str
    claim_revision_hash: str
    process_family: ProcessFamily
    tool: ToolIdentity
    input_state: tuple[str, ...]
    input_artifact_id: str
    parameters: tuple[tuple[str, Any], ...]
    masks: tuple[str, ...]
    output_artifact_id: str
    comparison_evidence_ids: tuple[str, ...]
    comparison_strength: EquivalenceClass | None
    deviations: tuple[str, ...]
    corrections: tuple[str, ...]
    outcome: ValidationOutcome
    validation_levels: frozenset[ValidationLevel]
    notes: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.event_id, "event_id")
        _require_text(self.validator, "validator")
        if not isinstance(self.validated_on, date):
            raise ValueError("validated_on must be a date")
        _require_text(self.claim_id, "claim_id")
        if (
            not isinstance(self.claim_revision_hash, str)
            or len(self.claim_revision_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.claim_revision_hash)
        ):
            raise ValueError("claim_revision_hash must be 64 lowercase hexadecimal characters")
        if not isinstance(self.process_family, ProcessFamily):
            raise ValueError("process_family must be a ProcessFamily")
        if not isinstance(self.tool, ToolIdentity):
            raise ValueError("tool must be a ToolIdentity")
        _require_text_items(self.input_state, "input_state")
        _require_text(self.input_artifact_id, "input_artifact_id")
        if not isinstance(self.parameters, tuple):
            raise ValueError("parameters must be a tuple")
        for name, _value in self.parameters:
            _require_text(name, "parameter name")
        _require_text_items(self.masks, "masks")
        _require_text(self.output_artifact_id, "output_artifact_id")
        _require_text_items(self.comparison_evidence_ids, "comparison_evidence_ids")
        if self.comparison_strength is not None and not isinstance(
            self.comparison_strength, EquivalenceClass
        ):
            raise ValueError("comparison_strength must be an EquivalenceClass or None")
        _require_text_items(self.deviations, "deviations")
        _require_text_items(self.corrections, "corrections")
        if not isinstance(self.outcome, ValidationOutcome):
            raise ValueError("outcome must be a ValidationOutcome")
        if not isinstance(self.validation_levels, frozenset) or not self.validation_levels:
            raise ValueError("validation_levels must be a non-empty frozenset")
        if any(not isinstance(level, ValidationLevel) for level in self.validation_levels):
            raise ValueError("validation_levels entries must be ValidationLevel values")
        if (
            ValidationLevel.EQUIVALENCE in self.validation_levels
            and self.comparison_strength is None
        ):
            raise ValueError(
                "comparison_strength is required when Equivalence is validated"
            )
        _require_text(self.notes, "notes")


def process_family_ids() -> tuple[str, ...]:
    """Return the stable launch taxonomy in declaration order."""

    return tuple(family.value for family in ProcessFamily)

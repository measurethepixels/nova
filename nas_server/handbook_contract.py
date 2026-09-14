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
from typing import Any, Literal


SCHEMA_VERSION = 1


class ProcessFamily(StrEnum):
    """Published process families in pipeline order."""

    PEDESTAL_REMOVAL = "pedestal_removal"
    SUBFRAME_INSPECTION = "subframe_inspection"
    REGISTRATION_ALIGNMENT = "registration_alignment"
    STACKING_INTEGRATION = "stacking_integration"
    CROP_FRAMING = "crop_framing"
    COSMETIC_CORRECTION = "cosmetic_correction"
    BACKGROUND_EXTRACTION = "background_extraction"
    COLOR_CALIBRATION = "color_calibration"
    DECONVOLUTION = "deconvolution"
    DENOISE = "denoise"
    STAR_CORRECTION = "star_correction"
    LINEAR_STAR_SPLIT = "linear_star_split"
    STRETCH = "stretch"
    STARLESS_FINISHING = "starless_finishing"
    BACKGROUND_NEUTRALIZATION = "background_neutralization"
    SKY_GREEN_REBALANCE = "sky_green_rebalance"
    CURVES = "curves"
    SATURATION = "saturation"
    LOCAL_CONTRAST = "local_contrast"
    HDR_COMPRESSION = "hdr_compression"
    HDR_CORE_BLEND = "hdr_core_blend"
    POST_STRETCH_DENOISE = "post_stretch_denoise"
    DARK_STRUCTURE_ENHANCEMENT = "dark_structure_enhancement"
    HALO_SUPPRESSION = "halo_suppression"
    NARROWBAND_DUAL_BAND_STRATEGY = "narrowband_dual_band_strategy"


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


class ValidationCheckStatus(StrEnum):
    """Outcome for one validation dimension, including explicit applicability."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


class EvidenceOrigin(StrEnum):
    """How evidence for a concept claim was obtained, not its maturity."""

    SOURCE_CONFIRMED = "source-confirmed"
    VENDOR_DOCUMENTED = "vendor-documented"
    EXECUTION_CONFIRMED = "execution-confirmed"
    ARTIFACT_COMPARISON = "artifact-comparison"
    NOT_REJECTED = "not-rejected"
    RUN_LOCAL_WINNER = "run-local-winner"
    HISTORICAL_WIN_FRACTION = "historical-win-fraction"
    REPEATED_EVIDENCE = "repeated-evidence"


class SourceKind(StrEnum):
    ONTOLOGY_OPERATION = "ontology-operation"
    PROCESS_FAMILY = "process-family"
    CONCEPT_PAGE = "concept-page"
    EXPERIMENT_VARIANT = "experiment-variant"
    RUNTIME_BEHAVIOR = "runtime-behavior"


class SourceClassification(StrEnum):
    PUBLIC_CONCEPT_ROOT = "public-concept-root"
    PUBLIC_SUBCONCEPT = "public-subconcept"
    ALIAS_OR_SUBENGINE = "alias-or-subengine"
    PARAMETER_NAMESPACE = "parameter-namespace"
    EXPERIMENT_VARIANT = "experiment-variant"
    CONTROL_BASELINE = "control-baseline"
    FALLBACK = "fallback"
    OBJECT_SPECIFIC_STRATEGY = "object-specific-strategy"
    ADAPTIVE_POLICY = "adaptive-policy"
    METRIC_OR_EVALUATOR = "metric-or-evaluator"
    STATE_TRANSITION = "state-transition"
    IMPLEMENTATION_DETAIL = "implementation-detail"
    RESEARCH_FUTURE_SURFACE = "research-future-surface"
    INTENTIONAL_HANDBOOK_DEBT = "intentional-handbook-debt"
    DEPRECATED_HISTORICAL = "deprecated-historical"


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
class ConceptSection:
    section_id: str
    title: str
    body: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.section_id, "section_id")
        _require_text(self.title, "title")
        _require_text_items(self.body, "body")
        if not self.body:
            raise ValueError("body must contain at least one paragraph")


@dataclass(frozen=True)
class Claim:
    """One auditable assertion with orthogonal evidence dimensions."""

    claim_id: str
    text: str
    origin: EvidenceOrigin
    source_ids: tuple[str, ...]
    validation_event_ids: tuple[str, ...] = ()
    human_validated: bool = False
    recommended: bool = False
    applicability_bound: str | None = None
    contradiction_status: Literal["none", "contradictory", "unresolved"] = "none"

    def __post_init__(self) -> None:
        _require_text(self.claim_id, "claim_id")
        _require_text(self.text, "text")
        if not isinstance(self.origin, EvidenceOrigin):
            raise ValueError("origin must be an EvidenceOrigin")
        _require_text_items(self.source_ids, "source_ids")
        if not self.source_ids:
            raise ValueError("source_ids must contain at least one evidence reference")
        _require_text_items(self.validation_event_ids, "validation_event_ids")
        if self.human_validated and not self.validation_event_ids:
            raise ValueError("human_validated claims require validation_event_ids")
        non_validating_origins = {
            EvidenceOrigin.NOT_REJECTED,
            EvidenceOrigin.RUN_LOCAL_WINNER,
            EvidenceOrigin.HISTORICAL_WIN_FRACTION,
        }
        if self.human_validated and self.origin in non_validating_origins:
            raise ValueError(f"{self.origin.value} evidence cannot be human-validated")
        if self.applicability_bound is not None:
            _require_text(self.applicability_bound, "applicability_bound")
        if self.contradiction_status not in {"none", "contradictory", "unresolved"}:
            raise ValueError("invalid contradiction_status")


@dataclass(frozen=True)
class ConceptArticle:
    """Methodology or vocabulary guidance outside the process taxonomy."""

    concept_id: str
    schema_version: int
    revision: int
    title: str
    subtitle: str
    summary: str
    sections: tuple[ConceptSection, ...]
    claims: tuple[Claim, ...]
    unresolved_gates: tuple[str, ...]
    related_process_families: tuple[ProcessFamily, ...]
    sources: tuple[EvidenceReference, ...]
    validation_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        for name in ("concept_id", "title", "subtitle", "summary"):
            _require_text(getattr(self, name), name)
        if not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if not self.sections or any(not isinstance(x, ConceptSection) for x in self.sections):
            raise ValueError("sections must contain ConceptSection values")
        if not self.claims or any(not isinstance(x, Claim) for x in self.claims):
            raise ValueError("claims must contain Claim values")
        _require_text_items(self.unresolved_gates, "unresolved_gates")
        if not isinstance(self.related_process_families, tuple) or any(
            not isinstance(x, ProcessFamily) for x in self.related_process_families
        ):
            raise ValueError("related_process_families must contain ProcessFamily values")
        if not self.sources or any(not isinstance(x, EvidenceReference) for x in self.sources):
            raise ValueError("sources must contain EvidenceReference values")
        _require_text_items(self.validation_event_ids, "validation_event_ids")
        source_ids = {source.reference_id for source in self.sources}
        missing = {source_id for claim in self.claims for source_id in claim.source_ids if source_id not in source_ids}
        if missing:
            raise ValueError(f"claims reference unknown sources: {sorted(missing)}")
        validation_event_ids = set(self.validation_event_ids)
        missing_validation_events = {
            event_id
            for claim in self.claims
            for event_id in claim.validation_event_ids
            if event_id not in validation_event_ids
        }
        if missing_validation_events:
            raise ValueError(
                "claims reference unknown validation events: "
                f"{sorted(missing_validation_events)}"
            )


@dataclass(frozen=True)
class CanonicalConcept:
    """Accounting owner for a public concept; content coverage is separate."""

    concept_id: str
    title: str
    owner: str | None

    def __post_init__(self) -> None:
        _require_text(self.concept_id, "concept_id")
        _require_text(self.title, "title")
        if self.owner is not None:
            _require_text(self.owner, "owner")


@dataclass(frozen=True)
class CoverageDebt:
    reason: str
    owner: str
    introduced_by: str
    review_by: date
    exit_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("reason", "owner", "introduced_by"):
            _require_text(getattr(self, name), name)
        if not isinstance(self.review_by, date):
            raise ValueError("review_by must be a date")
        _require_text_items(self.exit_criteria, "exit_criteria")
        if not self.exit_criteria:
            raise ValueError("exit_criteria must not be empty")


@dataclass(frozen=True)
class SourceSurfaceMapping:
    """Classification of one ontology/runtime surface and its content locations."""

    source_id: str
    source_kind: SourceKind
    locator: str
    classification: SourceClassification
    canonical_concept: str | None
    content_locations: tuple[str, ...]
    rationale: str
    debt: CoverageDebt | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "locator", "rationale"):
            _require_text(getattr(self, name), name)
        if not isinstance(self.source_kind, SourceKind):
            raise ValueError("source_kind must be a SourceKind")
        if not isinstance(self.classification, SourceClassification):
            raise ValueError("classification must be a SourceClassification")
        _require_text_items(self.content_locations, "content_locations")
        if self.canonical_concept is not None:
            _require_text(self.canonical_concept, "canonical_concept")
        if self.classification is SourceClassification.INTENTIONAL_HANDBOOK_DEBT:
            if self.debt is None:
                raise ValueError("intentional debt requires debt metadata")
        elif self.debt is not None:
            raise ValueError("debt metadata is only valid for intentional debt")


@dataclass(frozen=True)
class CoverageReport:
    accounted_source_ids: tuple[str, ...]
    unaccounted_source_ids: tuple[str, ...]
    content_covered_source_ids: tuple[str, ...]
    debt_source_ids: tuple[str, ...]
    ownerless_concept_ids: tuple[str, ...]
    unknown_concept_source_ids: tuple[str, ...]


def assess_concept_coverage(
    concepts: tuple[CanonicalConcept, ...],
    mappings: tuple[SourceSurfaceMapping, ...],
    required_source_ids: tuple[str, ...],
) -> CoverageReport:
    """Report accounting, content coverage, and debt as separate dimensions."""

    concept_ids = {concept.concept_id for concept in concepts}
    mapping_ids = [mapping.source_id for mapping in mappings]
    if len(mapping_ids) != len(set(mapping_ids)):
        raise ValueError("source mappings must have unique source_id values")
    required = set(required_source_ids)
    by_id = {mapping.source_id: mapping for mapping in mappings}
    accounted = tuple(sorted(required & by_id.keys()))
    debt = tuple(sorted(source_id for source_id in accounted if by_id[source_id].debt))
    covered = tuple(sorted(
        source_id for source_id in accounted
        if by_id[source_id].content_locations and source_id not in debt
    ))
    return CoverageReport(
        accounted_source_ids=accounted,
        unaccounted_source_ids=tuple(sorted(required - by_id.keys())),
        content_covered_source_ids=covered,
        debt_source_ids=debt,
        ownerless_concept_ids=tuple(sorted(c.concept_id for c in concepts if c.owner is None)),
        unknown_concept_source_ids=tuple(sorted(
            m.source_id for m in mappings
            if m.canonical_concept is not None and m.canonical_concept not in concept_ids
        )),
    )


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
class ValidationCheck:
    """Claim-revision-scoped result for one independent validation dimension."""

    level: ValidationLevel
    status: ValidationCheckStatus
    evidence_links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.level, ValidationLevel):
            raise ValueError("level must be a ValidationLevel")
        if not isinstance(self.status, ValidationCheckStatus):
            raise ValueError("status must be a ValidationCheckStatus")
        _require_text_items(self.evidence_links, "evidence_links")
        if self.status != ValidationCheckStatus.NOT_APPLICABLE and not self.evidence_links:
            raise ValueError("applicable validation checks require evidence_links")


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
    checks: tuple[ValidationCheck, ...] = ()

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
        if not isinstance(self.checks, tuple) or any(
            not isinstance(check, ValidationCheck) for check in self.checks
        ):
            raise ValueError("checks must be a tuple of ValidationCheck values")
        check_levels = [check.level for check in self.checks]
        if len(check_levels) != len(set(check_levels)):
            raise ValueError("checks must contain at most one result per validation level")
        checked_levels = {
            check.level
            for check in self.checks
            if check.status != ValidationCheckStatus.NOT_APPLICABLE
        }
        if self.checks and checked_levels != set(self.validation_levels):
            raise ValueError("validation_levels must match applicable checks")


def process_family_ids() -> tuple[str, ...]:
    """Return the stable launch taxonomy in declaration order."""

    return tuple(family.value for family in ProcessFamily)

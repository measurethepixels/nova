"""Append-only validation ledger built against :mod:`handbook_contract` (issue #287).

The ledger is seeded from ``M66_Manual_Recipe_Verification_Feedback.md``'s "# Step
reviews" section -- Henry's own PixInsight-track, hands-on verification of the M66
recipe. Only what that section actually establishes is recorded here; where the note
leaves a value, date, or version unrecorded, this module says so explicitly rather
than inferring one (per issue #287's stop rule).

Step 1 (Crop) is reviewed in that section too, but "crop" is not one of the 8 launch
process families #284 defined (``ProcessFamily`` deliberately covers pedestal removal
through stretch, not every M66 recipe step -- see #284's Desired outcome). Extending
that enum is #284's scope, not this issue's, so Step 1 is left unseeded rather than
guessed into a family it does not belong to.

Each event's ``claim_revision_hash`` is a SHA-256 digest of the transcribed claim text
next to it below (``_CLAIM_TEXT``), not of any live recipe/handbook copy -- there is no
canonical claim registry yet (#262/#263 remain deferred, per #284's compatibility
seam). :func:`derive_stamp` compares an event's stored hash against a *supplied*
current claim hash so the export path can detect a material rewrite. Without one
supplied, applicability fails closed rather than treating append-only recency as
proof that the event still addresses the current claim.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .handbook_contract import (
    SCHEMA_VERSION,
    ProcessFamily,
    ToolIdentity,
    ValidationEvent,
    ValidationLevel,
    ValidationOutcome,
    ValidationCheckStatus,
)

SOURCE_NOTE = "M66_Manual_Recipe_Verification_Feedback.md"

# Henry reported completing this verification pass on 2026-07-30 (Steps 5-7 section,
# "# Step reviews"); no other per-step date is recorded in that section, so every event
# below uses this same date rather than inventing per-step precision the note lacks.
_VERIFICATION_DATE = date(2026, 7, 30)

_NOT_RECORDED = "not recorded in evidence"


def _hash_claim(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tool(module_or_cli: str) -> ToolIdentity:
    return ToolIdentity(
        tool="pixinsight",
        tool_version=_NOT_RECORDED,
        host="PixInsight",
        host_version=_NOT_RECORDED,
        operating_system=_NOT_RECORDED,
        module_or_cli=module_or_cli,
    )


def _cite(heading: str) -> str:
    return f"Source: {SOURCE_NOTE}, section '{heading}'."


# ---------------------------------------------------------------------------
# Claim text -- the transcribed instruction each event validates. Kept next to the
# event so a reviewer can compare it directly against the source note, and hashed
# below to give claim_revision_hash real (if currently self-referential) content.
# ---------------------------------------------------------------------------

_CLAIM_TEXT: dict[str, str] = {
    "m66-pixinsight-remove_pedestal": (
        "In PixelMath, subtract the confirmed pedestal with $T - min($T), rescaling "
        "disabled, after correcting the earlier unclear execution."
    ),
    "m66-pixinsight-cosmetic_correction": (
        "Run CosmeticCorrection as a batch process with hot/cold sigma controls "
        "configured on the target file."
    ),
    "m66-pixinsight-background_extraction": (
        "Open GraXpert from PixInsight, use Subtraction for the additive gradient, and "
        "use smoothing 0.5 for this M66 run; generate both the corrected image and "
        "background model."
    ),
    "m66-pixinsight-color_calibration": (
        "Complete SPCC color calibration on the M66 linear image (Henry-reported "
        "hands-on pass; no separate recording)."
    ),
    "m66-pixinsight-deconvolution": (
        "Complete BlurXTerminator deconvolution on the M66 linear image (Henry-reported "
        "hands-on pass; no separate recording)."
    ),
    "m66-pixinsight-denoise_linear": (
        "Complete NoiseXTerminator linear denoise on the M66 image (Henry-reported "
        "hands-on pass; no separate recording)."
    ),
    "m66-pixinsight-star_sharpen": (
        "Run BlurXTerminator with Correct Only enabled and Sharpen Stars, Adjust Star "
        "Halos, and Sharpen Nonstellar all at 0, repairing star shape/elongation "
        "without sharpening."
    ),
    "m66-pixinsight-stretch": (
        "Stretch the full linear starless image with MultiscaleAdaptiveStretch using "
        "NOVA's exact default preset: Target Background 0.150, Aggressiveness 0.70, "
        "Dynamic range compression 0.40, no Background Reference, Contrast Recovery "
        "enabled at 1024px/intensity 1.00, Color Saturation enabled at Amount "
        "0.75/Boost 0.50, Lightness mask enabled."
    ),
}


def claim_text(claim_id: str) -> str:
    """Return the transcribed claim text an event's hash was computed from."""

    return _CLAIM_TEXT[claim_id]


def current_claim_hashes() -> dict[str, str]:
    """Return revision hashes for the current public validation claims.

    The transcribed claim registry above is the source whose revisions the seeded
    validation events address.  Keeping this derivation separate from event data is
    important: copying hashes from the events would make a stale event look current.
    """

    return {claim_id: _hash_claim(text) for claim_id, text in _CLAIM_TEXT.items()}


# ---------------------------------------------------------------------------
# Seed events -- Steps 2-4, 5-7, 8, 10 of the "# Step reviews" section only, per
# issue #287's own Evidence list (Step 1/Crop excluded; see module docstring).
# Later steps (11-16, the separate Siril/SASpro passes) are out of this issue's
# scope and are not seeded here.
# ---------------------------------------------------------------------------

STEP_2_REMOVE_PEDESTAL = ValidationEvent(
    event_id="m66-pixinsight-step2-remove-pedestal-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-remove_pedestal",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-remove_pedestal"]),
    process_family=ProcessFamily.PEDESTAL_REMOVAL,
    tool=_tool("PixelMath"),
    input_state=("Post-Step-1 linear integration with a positive global minimum.",),
    input_artifact_id="02_remove_pedestal_pixinsight_NEEDS_CORRECTION.mp4",
    parameters=(),
    masks=(),
    output_artifact_id="02_remove_pedestal_pixinsight_PASS_with_statistics.mp4",
    comparison_evidence_ids=(
        "M66 Recipe pixinsight Steps 1 and 2.mp4",
        "02_remove_pedestal_pixinsight_NEEDS_CORRECTION.mp4",
        "02_remove_pedestal_pixinsight_PASS_with_statistics.mp4",
    ),
    comparison_strength=None,
    deviations=(
        "Initial recording showed a failed/unclear PixelMath attempt before the "
        "corrected recording.",
        "PASS validates operation of the demonstrated PixelMath procedure only; it "
        "does not establish that the calibrated M66 stack actually contained a "
        "removable ADC pedestal (2026-07-31 scientific qualification).",
    ),
    corrections=(
        "PixelMath expression corrected to $T - min($T) with rescaling disabled, "
        "applied consistently across channels.",
    ),
    outcome=ValidationOutcome.PARTIAL,
    validation_levels=frozenset({ValidationLevel.PROCEDURE, ValidationLevel.STATE}),
    notes=(
        "PASS after correcting the PixelMath execution; Statistics confirmed channel "
        "minima brought to zero. Does not by itself prove a genuine electronic "
        "pedestal was present, which is why NOVA's own remove_pedestal defaults to "
        "skip. " + _cite("## Step 2 — Remove Pedestal")
    ),
)

STEP_3_COSMETIC_CORRECTION = ValidationEvent(
    event_id="m66-pixinsight-step3-cosmetic-correction-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-cosmetic_correction",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-cosmetic_correction"]),
    process_family=ProcessFamily.COSMETIC_CORRECTION,
    tool=_tool("CosmeticCorrection"),
    input_state=("Post-Step-2 (pedestal-corrected) linear integration, not a calibrated pre-registration subframe.",),
    input_artifact_id="03_cosmetic_correction_pixinsight_PASS_with_statistics.mp4 (pre-correction frame)",
    parameters=(),
    masks=(),
    output_artifact_id="03_cosmetic_correction_pixinsight_PASS_with_statistics.mp4 (post-correction frame)",
    comparison_evidence_ids=("03_cosmetic_correction_pixinsight_PASS_with_statistics.mp4",),
    comparison_strength=None,
    deviations=(
        "CosmeticCorrection was demonstrated on a post-stack Step 2 file, not on "
        "calibrated pre-registration subframes, so this does not by itself prove the "
        "recipe's post-integration placement is the right stage for the operation.",
    ),
    corrections=(),
    outcome=ValidationOutcome.PARTIAL,
    validation_levels=frozenset({ValidationLevel.PROCEDURE, ValidationLevel.PARAMETERS}),
    notes=(
        "The PixInsight tool and hot/cold-sigma controls were successfully "
        "demonstrated as a batch process, but the recipe-stage sequencing question "
        "(pre-integration vs. post-stack residual cleanup) was left an open decision "
        "in the note, not resolved by this event. Recorded as tool PASS / "
        "recipe-stage NEEDS CORRECTION. " + _cite("## Step 3 — Cosmetic Correction")
    ),
)

STEP_4_BACKGROUND_EXTRACTION = ValidationEvent(
    event_id="m66-pixinsight-step4-background-extraction-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-background_extraction",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-background_extraction"]),
    process_family=ProcessFamily.BACKGROUND_EXTRACTION,
    tool=_tool("GraXpert (launched from PixInsight)"),
    input_state=("Post-Step-3 linear integration prior to background extraction.",),
    input_artifact_id="04_background_extraction_pixinsight_NEEDS_CORRECTION_with_statistics.mp4 (pre-extraction frame)",
    parameters=(("correction_mode", "Subtraction"), ("smoothing", "0.5")),
    masks=(),
    output_artifact_id="04_background_extraction_pixinsight_NEEDS_CORRECTION_with_statistics.mp4 (extracted image + background model)",
    comparison_evidence_ids=("04_background_extraction_pixinsight_NEEDS_CORRECTION_with_statistics.mp4",),
    comparison_strength=None,
    deviations=(
        "Recorded evidence used GraXpert, not the previously documented "
        "GradientCorrection/DBE processes.",
    ),
    corrections=(
        "Documented PixInsight instructions replaced GradientCorrection/DBE with "
        "GraXpert (Subtraction, smoothing 0.5) as the primary reproduced path, "
        "inspecting the generated background model for target imprint before "
        "accepting the result.",
    ),
    outcome=ValidationOutcome.PARTIAL,
    validation_levels=frozenset({ValidationLevel.PROCEDURE, ValidationLevel.PARAMETERS}),
    notes=(
        "NEEDS CORRECTION verdict in the note: operational PASS for the demonstrated "
        "GraXpert path, but the previously documented PixInsight instructions named "
        "the wrong tool. " + _cite("## Step 4 — Background Extraction")
    ),
)

STEP_5_COLOR_CALIBRATION = ValidationEvent(
    event_id="m66-pixinsight-step5-color-calibration-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-color_calibration",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-color_calibration"]),
    process_family=ProcessFamily.COLOR_CALIBRATION,
    tool=_tool("SpectrophotometricColorCalibration (SPCC)"),
    input_state=("Post-background-extraction linear image; no separate recording retained.",),
    input_artifact_id="no recording retained; hands-on report only",
    parameters=(),
    masks=(),
    output_artifact_id="no recording retained; hands-on report only",
    comparison_evidence_ids=(),
    comparison_strength=None,
    deviations=(
        "No screenshot or recording exists for this step; the exported verification "
        "session (menu paths, settings, observations) was still pending at the time "
        "of this event.",
    ),
    corrections=(),
    outcome=ValidationOutcome.PASS,
    validation_levels=frozenset({ValidationLevel.PROCEDURE}),
    notes=(
        "PASS by hands-on manual verification reported 2026-07-30; detailed exported "
        "record pending. Reconcile a future export with SPCC's astrometric-solution, "
        "sensor/filter selection, linear-input, and residual-fit requirements. "
        + _cite("### Step 5 — Color Calibration")
    ),
)

STEP_6_DECONVOLUTION = ValidationEvent(
    event_id="m66-pixinsight-step6-deconvolution-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-deconvolution",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-deconvolution"]),
    process_family=ProcessFamily.DECONVOLUTION,
    tool=_tool("BlurXTerminator"),
    input_state=("Post-color-calibration linear image; no separate recording retained.",),
    input_artifact_id="no recording retained; hands-on report only",
    parameters=(),
    masks=(),
    output_artifact_id="no recording retained; hands-on report only",
    comparison_evidence_ids=(),
    comparison_strength=None,
    deviations=(
        "No screenshot or recording exists for this step; the exported verification "
        "session was still pending at the time of this event.",
    ),
    corrections=(),
    outcome=ValidationOutcome.PASS,
    validation_levels=frozenset({ValidationLevel.PROCEDURE}),
    notes=(
        "PASS by hands-on manual verification reported 2026-07-30; detailed exported "
        "record pending. Reconcile a future export with the actual BlurXTerminator "
        "settings and confirm the result was inspected for ringing, halos, damaged "
        "stars, and lost faint structure. " + _cite("### Step 6 — Deconvolution")
    ),
)

STEP_7_DENOISE_LINEAR = ValidationEvent(
    event_id="m66-pixinsight-step7-denoise-linear-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-denoise_linear",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-denoise_linear"]),
    process_family=ProcessFamily.DENOISE,
    tool=_tool("NoiseXTerminator"),
    input_state=("Post-deconvolution linear image; no separate recording retained.",),
    input_artifact_id="no recording retained; hands-on report only",
    parameters=(),
    masks=(),
    output_artifact_id="no recording retained; hands-on report only",
    comparison_evidence_ids=(),
    comparison_strength=None,
    deviations=(
        "No screenshot or recording exists for this step; the exported verification "
        "session was still pending at the time of this event.",
    ),
    corrections=(),
    outcome=ValidationOutcome.PASS,
    validation_levels=frozenset({ValidationLevel.PROCEDURE}),
    notes=(
        "PASS by hands-on manual verification reported 2026-07-30; detailed exported "
        "record pending. Reconcile a future export with the actual NoiseXTerminator "
        "settings and confirm noise reduction did not materially soften stars or "
        "remove faint galaxy detail. " + _cite("### Step 7 — Denoise Linear")
    ),
)

STEP_8_STAR_CORRECTION = ValidationEvent(
    event_id="m66-pixinsight-step8-star-correction-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-star_sharpen",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-star_sharpen"]),
    process_family=ProcessFamily.STAR_CORRECTION,
    tool=_tool("BlurXTerminator (Correct Only)"),
    input_state=("Linear image retaining the window name GraXpert_background_extraction.",),
    input_artifact_id="08_star_sharpen_pixinsight_PASS_with_statistics.mp4 (before FWHM/eccentricity/statistics)",
    parameters=(
        ("correct_only", "enabled"),
        ("sharpen_stars", "0"),
        ("sharpen_nonstellar", "0"),
    ),
    masks=(),
    output_artifact_id="08_star_sharpen_pixinsight_PASS_with_statistics.mp4 (after FWHM/eccentricity/statistics)",
    comparison_evidence_ids=("08_star_sharpen_pixinsight_PASS_with_statistics.mp4",),
    comparison_strength=None,
    deviations=(
        "The visible source image retained the window name "
        "GraXpert_background_extraction, which proves the BXT operation ran but does "
        "not independently prove it was the prescribed post-denoise Step 7 output.",
        "Before/after luminance copies used differing star-support populations, so "
        "the measured FWHM/eccentricity deltas are indicative rather than a precise "
        "identical-star comparison.",
    ),
    corrections=(
        "Step renamed from 'Star Sharpen' to 'Star Correction — BlurXTerminator "
        "Correct Only', since sharpening amounts were zero and the operation repairs "
        "star shape rather than sharpening.",
    ),
    outcome=ValidationOutcome.PASS,
    validation_levels=frozenset(
        {ValidationLevel.PROCEDURE, ValidationLevel.PARAMETERS, ValidationLevel.STATE}
    ),
    notes=(
        "PASS for the PixInsight procedure, with documentation and measurement "
        "clarifications. Correct Only enabled with all sharpen amounts at 0; "
        "FWHMEccentricity, Statistics, and Scaled Noise Evaluation were measured "
        "before/after. " + _cite("## Step 8 — Star Correction")
    ),
)

STEP_10_STRETCH = ValidationEvent(
    event_id="m66-pixinsight-step10-stretch-v1",
    schema_version=SCHEMA_VERSION,
    validator="Henry",
    validated_on=_VERIFICATION_DATE,
    claim_id="m66-pixinsight-stretch",
    claim_revision_hash=_hash_claim(_CLAIM_TEXT["m66-pixinsight-stretch"]),
    process_family=ProcessFamily.STRETCH,
    tool=_tool("MultiscaleAdaptiveStretch"),
    input_state=("Full linear starless image (not a partial preview).",),
    input_artifact_id="10_stretch_NEEDS_CORRECTION.png (pre-stretch starless image)",
    parameters=(
        ("target_background", "0.095"),
        ("aggressiveness", "0.80"),
        ("dynamic_range_compression", "0.55"),
        ("contrast_recovery_intensity", "1.000"),
        ("boost", "0.70"),
    ),
    masks=(),
    output_artifact_id="10_stretch_NEEDS_CORRECTION.png (post-stretch result, R/G/B medians ~0.095)",
    comparison_evidence_ids=("10_stretch_NEEDS_CORRECTION.png",),
    comparison_strength=None,
    deviations=(
        "Henry's demonstrated PixInsight run used manually chosen MAS values (target "
        "background 0.095, aggressiveness 0.80, compression 0.55, boost 0.70), which "
        "are not NOVA's actual recorded/default parameters (Target Background 0.150, "
        "Aggressiveness 0.70, Dynamic range compression 0.40, Boost 0.50).",
        "The prior PixInsight instructions omitted MultiscaleAdaptiveStretch entirely, "
        "listing only GHS, HistogramTransformation, MaskedStretch, and ArcsinhStretch.",
    ),
    corrections=(
        "Documented PixInsight stretch instructions expanded to include "
        "MultiscaleAdaptiveStretch as the tool that reproduces NOVA's actual stretch, "
        "with NOVA's exact default-preset values recovered via module-default/code "
        "audit rather than Henry's manually chosen demonstration values.",
    ),
    outcome=ValidationOutcome.PARTIAL,
    validation_levels=frozenset({ValidationLevel.PROCEDURE}),
    notes=(
        "NEEDS CORRECTION in the note: MultiscaleAdaptiveStretch operates and produces "
        "plausible full-image medians near the operator's chosen target, but the "
        "demonstrated values do not match NOVA's actual default preset, so this event "
        "validates tool operation only, not an exact NOVA reproduction. "
        + _cite("## Step 10 — Stretch")
    ),
)

SEED_EVENTS: tuple[ValidationEvent, ...] = (
    STEP_2_REMOVE_PEDESTAL,
    STEP_3_COSMETIC_CORRECTION,
    STEP_4_BACKGROUND_EXTRACTION,
    STEP_5_COLOR_CALIBRATION,
    STEP_6_DECONVOLUTION,
    STEP_7_DENOISE_LINEAR,
    STEP_8_STAR_CORRECTION,
    STEP_10_STRETCH,
)

EVENT_BY_ID = {event.event_id: event for event in SEED_EVENTS}


# ---------------------------------------------------------------------------
# Stamp derivation -- "Henry validated" is a derived record, never a stored boolean.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationStamp:
    """The current validation state for one claim, derived from its event history."""

    claim_id: str
    status: str  # "unvalidated" | "failed" | "partial" | "validated" | "stale"
    latest_event: ValidationEvent | None
    reason: str
    checks_complete: bool = False


def derive_stamp(
    claim_id: str,
    events: Iterable[ValidationEvent],
    *,
    current_claim_hash: str | None = None,
) -> ValidationStamp:
    """Derive a claim's current stamp from its append-only event history.

    Never a boolean: the result names the specific event and outcome the stamp
    rests on. If ``current_claim_hash`` is supplied and differs from the latest
    matching event's ``claim_revision_hash``, the stamp is "stale" regardless of
    that event's own outcome, per issue #287's "marked stale on material rewrite"
    requirement.
    """

    matching = sorted(
        (event for event in events if event.claim_id == claim_id),
        key=lambda event: event.validated_on,
    )
    if not matching:
        return ValidationStamp(
            claim_id, "unvalidated", None, "No validation event recorded for this claim."
        )
    latest = matching[-1]
    if current_claim_hash is None:
        return ValidationStamp(
            claim_id,
            "unvalidated",
            latest,
            "The current claim revision was not supplied, so applicability cannot be confirmed.",
        )
    if current_claim_hash != latest.claim_revision_hash:
        return ValidationStamp(
            claim_id,
            "stale",
            latest,
            "The claim text has changed since the most recent validation event; "
            "re-validation is needed before this stamp can be trusted again.",
        )
    checks_by_level = {check.level: check for check in latest.checks}
    checks_complete = set(checks_by_level) == set(ValidationLevel)
    if not checks_complete:
        if latest.outcome == ValidationOutcome.FAIL:
            status = "failed"
        elif latest.outcome == ValidationOutcome.INCONCLUSIVE:
            status = "unvalidated"
        else:
            status = "partial"
        return ValidationStamp(
            claim_id,
            status,
            latest,
            "Applicability is not recorded for every validation dimension; "
            "the event cannot support a universal validation assertion.",
        )
    applicable = [
        check for check in latest.checks
        if check.status != ValidationCheckStatus.NOT_APPLICABLE
    ]
    if not applicable:
        return ValidationStamp(
            claim_id,
            "unvalidated",
            latest,
            "No applicable validation dimension has supporting evidence.",
            True,
        )
    if any(check.status == ValidationCheckStatus.FAIL for check in applicable):
        status = "failed"
    elif latest.outcome == ValidationOutcome.FAIL:
        status = "failed"
    elif latest.outcome == ValidationOutcome.INCONCLUSIVE:
        status = "unvalidated"
    elif any(check.status == ValidationCheckStatus.INCONCLUSIVE for check in applicable):
        status = "partial" if any(
            check.status == ValidationCheckStatus.PASS for check in applicable
        ) else "unvalidated"
    elif all(check.status == ValidationCheckStatus.PASS for check in applicable):
        status = (
            "validated"
            if latest.outcome == ValidationOutcome.PASS
            else "partial"
        )
    else:  # Defensive fail-closed branch for a future enum value.
        status = "unvalidated"
    return ValidationStamp(
        claim_id,
        status,
        latest,
        f"Most recent event: {latest.outcome.value} on {latest.validated_on.isoformat()}, "
        f"by {latest.validator}; applicability recorded for every dimension.",
        True,
    )


def stamps_by_claim(
    events: Iterable[ValidationEvent] = SEED_EVENTS,
    *,
    current_claim_hashes: dict[str, str] | None = None,
) -> dict[str, ValidationStamp]:
    """Derive one current stamp per distinct claim_id present in ``events``."""

    values = tuple(events)
    claim_ids = sorted({event.claim_id for event in values})
    hashes = current_claim_hashes or {}
    return {
        claim_id: derive_stamp(
            claim_id, values, current_claim_hash=hashes.get(claim_id)
        )
        for claim_id in claim_ids
    }

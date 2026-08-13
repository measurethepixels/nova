"""Launch handbook content built against :mod:`handbook_contract`.

Issue #285 lands the eight M66 process families in independently reviewable
batches.  This module starts with the calibration-gates batch only: pedestal
removal and cosmetic correction.  Content is process-first, not a replacement
for a run-specific recipe.  Tool paths deliberately distinguish documented,
tested, source-confirmed, and unverified claims.
"""

from __future__ import annotations

from .handbook_contract import (
    SCHEMA_VERSION,
    EquivalenceClass,
    EvidenceReference,
    HandbookArticle,
    ProcessFamily,
    ProvenanceLabel,
    ToolGuidance,
)


M66_VERIFICATION = EvidenceReference(
    reference_id="m66-manual-verification",
    title="M66 manual recipe verification feedback",
    locator="M66_Manual_Recipe_Verification_Feedback.md",
    provenance=ProvenanceLabel.HENRY_VALIDATED,
)

NOVA_PEDESTAL_SOURCE = EvidenceReference(
    reference_id="nova-pedestal-source",
    title="NOVA pedestal implementation and conditional gate",
    locator="nas_server/seti_astro.py:3096; nas_server/auto_process.py:655",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_M66_RUN_1247 = EvidenceReference(
    reference_id="nova-m66-run-1.24.7",
    title="M66 workflow 1.24.7 execution record",
    locator="critiques/20260802_045251_seestar_galaxy.json",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

NOVA_COSMETIC_SOURCE = EvidenceReference(
    reference_id="nova-cosmetic-source",
    title="NOVA isolated-defect correction implementation and ontology defaults",
    locator="nas_server/seti_astro.py:3133; nas_server/processing_ontology.json:325",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SIRIL_CALIBRATION_144 = EvidenceReference(
    reference_id="siril-calibration-1.4.4",
    title="Siril 1.4.4 calibration documentation",
    locator="https://siril.readthedocs.io/en/stable/preprocessing/calibration.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_COSMETIC_144 = EvidenceReference(
    reference_id="siril-cosmetic-1.4.4",
    title="Siril 1.4.4 cosmetic correction documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/cc.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)


PEDESTAL_REMOVAL = HandbookArticle(
    article_id="pedestal-removal",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.PEDESTAL_REMOVAL,
    purpose=(
        "Remove a known residual constant electronic offset without confusing "
        "legitimate sky signal, noise, interpolation, or an isolated dark pixel "
        "with that offset."
    ),
    observable_symptoms=(
        "Calibration records identify a residual constant offset that was not removed.",
        "Calibrated subframes show left-edge clipping that requires a documented output "
        "pedestal during recalibration, rather than post-stack minimum subtraction.",
    ),
    intended_output=(
        "A still-linear image with only the confirmed uniform offset removed, no new "
        "shadow clipping, and recorded before/after channel statistics."
    ),
    limits=(
        "A positive global minimum does not identify an electronic pedestal.",
        "Post-stack subtraction cannot restore values clipped during calibration.",
        "Subtracting separate channel minima can alter color balance.",
        "An output pedestal added during calibration to prevent clipping is not evidence "
        "that an arbitrary post-stack floor should be removed.",
    ),
    required_input_state=(
        "Linear data with known calibration provenance.",
        "A documented offset value or calibration diagnosis independent of the stack's "
        "single lowest pixel.",
        "Before-operation channel minima, medians, and clipped-pixel counts recorded.",
    ),
    nova_action=(
        "NOVA 1.24.7 skips remove_pedestal by default because a global minimum is "
        "insufficient evidence. If an operator explicitly forces the current function, "
        "it subtracts one global minimum uniformly from every channel and clips only as "
        "a numerical safety guard."
    ),
    nova_evidence_ids=("nova-pedestal-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Capture or calibration provenance establishes a remaining constant offset.",
        "A controlled calibration diagnosis supplies the amount and the subtraction can "
        "be repeated on the original linear data.",
    ),
    skip_when=(
        "The only evidence is a positive minimum, median, or visually raised sky floor.",
        "The image is an ordinary calibrated stack with no documented residual offset.",
        "The proposed amount is a sky statistic rather than a calibration offset.",
        "The data are nonlinear or the original calibration products are unavailable.",
    ),
    scientific_and_aesthetic_notes=(
        "Sky background is real signal plus noise; forcing its darkest sample to zero is "
        "not a calibration measurement.",
        "Judge this analytically with statistics and provenance, not a before/after beauty "
        "slider.",
        "If calibration clipped negative values, reprocess the subframes with an "
        "appropriate calibration-time output pedestal; later addition or subtraction "
        "cannot recover clipped structure.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Inspect capture and calibration provenance for an explicitly documented "
                "remaining constant offset.",
                "Leave the step skipped when that evidence is absent; this is the default.",
                "If force_apply is authorized from real provenance, record the input, "
                "uniform amount, and before/after channel statistics.",
            ),
            controls_and_starting_ranges=(
                ("default action", "skip"),
                ("force_apply", "false; true only with documented calibration evidence"),
                ("subtraction amount", "the confirmed offset; never infer from global minimum alone"),
            ),
            expected_result="Only the documented uniform offset moves; channel distributions retain their shape.",
            failure_modes=(
                "A single cold pixel determines the amount.",
                "Legitimate sky background is forced to zero.",
                "Shadow clipping increases or channel balance changes.",
            ),
            recovery=(
                "Revert to the unchanged linear input.",
                "Recalibrate from subframes when the failure originated during calibration.",
            ),
            mask_support="No mask: a true constant offset is a full-image calibration property.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(
                ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
                ProvenanceLabel.NOVA_EXECUTION_RECORD,
            ),
            source_ids=("nova-pedestal-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="M66 validation environment; exact version not recorded",
            host="PixInsight PixelMath",
            instructions=(
                "Confirm a residual uniform offset from calibration provenance; otherwise skip.",
                "In PixelMath subtract that measured scalar from $T with rescaling disabled.",
                "Apply consistently to the intended channels and inspect Statistics before "
                "and after for minima, medians, and clipping.",
            ),
            controls_and_starting_ranges=(
                ("expression", "$T - confirmed_offset"),
                ("rescale result", "disabled"),
                ("offset", "measured calibration value; no generic starting number"),
            ),
            expected_result="A uniform shift by the confirmed amount without distribution-shape or color damage.",
            failure_modes=(
                "Using min($T) as proof instead of merely as arithmetic.",
                "Using a sky median as the offset.",
                "Per-channel subtraction changes color balance.",
            ),
            recovery=("Undo and return to the original linear image; recalibrate if clipping occurred earlier.",),
            mask_support="No mask for a confirmed full-frame constant offset.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.2 manual test; 1.4.4 calibration reference",
            host="Siril Pixel Math and calibration",
            instructions=(
                "Prefer correct bias/dark/flat calibration and inspect the calibration "
                "statistics before considering a later subtraction.",
                "Skip post-stack subtraction unless provenance confirms a residual offset.",
                "For the demonstrated manual arithmetic, subtract one confirmed scalar in "
                "Pixel Math and compare channel statistics before and after.",
            ),
            controls_and_starting_ranges=(
                ("post-stack scalar", "confirmed offset only; no generic starting number"),
                ("display normalization", "disabled while comparing numeric statistics"),
            ),
            expected_result="Means and medians shift by the scalar while sigma/MAD and distribution shape remain stable.",
            failure_modes=(
                "Treating the lowest channel minimum as an identified pedestal.",
                "Clamping a large part of the real sky background to zero.",
            ),
            recovery=("Undo; return to the calibrated linear input and diagnose the calibration chain.",),
            mask_support="No mask for calibration-wide offset handling.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(
                ProvenanceLabel.VENDOR_DOCUMENTED,
                ProvenanceLabel.TOOL_TESTED,
                ProvenanceLabel.HENRY_VALIDATED,
            ),
            source_ids=("siril-calibration-1.4.4", "m66-manual-verification"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="not established for this operation",
            host="Seti Astro Suite Pro",
            instructions=(
                "Do not invent a SASpro post-stack pedestal-removal equivalent.",
                "Handle the calibration diagnosis in a calibration-capable tool and import "
                "the resulting clean linear stack.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A calibrated linear stack enters SASpro without an undocumented black-level shift.",
            failure_modes=("Assuming a general black-point or PixelMath operation proves calibration equivalence.",),
            recovery=("Return to the calibration-capable source tool and preserve the original stack.",),
            mask_support="Not applicable.",
            equivalence=EquivalenceClass.NO_DIRECT_EQUIVALENT,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
    ),
    measurements=(
        "Calibration provenance and the exact asserted offset value.",
        "Per-channel minimum, median, MAD/sigma, and clipped-pixel fraction before and after.",
        "Difference image confirming a spatially uniform subtraction.",
    ),
    acceptance_criteria=(
        "The step remains skipped when no independent pedestal evidence exists.",
        "When applied, the difference image is spatially constant at the documented amount.",
        "No material increase in low-clipped pixels occurs.",
        "Channel-distribution shape and intended color relationships remain intact.",
    ),
    sources=(
        M66_VERIFICATION,
        NOVA_PEDESTAL_SOURCE,
        NOVA_M66_RUN_1247,
        SIRIL_CALIBRATION_144,
    ),
)


COSMETIC_CORRECTION = HandbookArticle(
    article_id="cosmetic-correction",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.COSMETIC_CORRECTION,
    purpose=(
        "Identify and replace isolated defective pixels or very small residual defects "
        "without treating stars, compact structure, or ordinary noise as defects."
    ),
    observable_symptoms=(
        "Stable hot or cold pixels recur at sensor coordinates in calibrated subframes.",
        "Sparse one-pixel colored specks survive integration and become conspicuous after stretch.",
        "Impulse defects would bias later denoise or sharpening operations.",
    ),
    intended_output=(
        "Calibrated subframes corrected from a master-dark or bad-pixel map when possible; "
        "otherwise a documented sparse residual cleanup whose corrected-pixel count and "
        "star preservation have been checked."
    ),
    limits=(
        "Ordinary cosmetic correction does not repair gradients, broad artifacts, trails, or bad calibration.",
        "Post-integration cleanup is not equivalent to correcting every calibrated subframe before registration and stacking.",
        "Aggressive thresholds can replace star cores and real compact structure.",
    ),
    required_input_state=(
        "Prefer calibrated, pre-registration subframes plus a matching master-dark or bad-pixel map.",
        "For residual post-stack cleanup, use a linear integrated image and a sparse-defect hypothesis.",
        "Record CFA/debayer state because CFA-aware detection differs from RGB-image correction.",
    ),
    nova_action=(
        "NOVA's post-stack residual cleanup operates per channel with a 5x5 local median, "
        "flags absolute residuals above 5 times the median absolute residual, keeps only "
        "connected components of at most three pixels, and replaces them with the local median."
    ),
    nova_evidence_ids=("nova-cosmetic-source",),
    use_when=(
        "A master-dark or bad-pixel map identifies repeatable sensor defects before integration.",
        "A linear stack contains sparse, isolated residual hot/cold pixels that survived rejection.",
    ),
    skip_when=(
        "The candidate defects form stars, compact target structure, trails, or extended regions.",
        "The correction count or fraction is unexpectedly large.",
        "Blink/difference inspection shows real signal changing.",
    ),
    scientific_and_aesthetic_notes=(
        "Pre-integration correction preserves the stacking engine's opportunity to reject and weight data correctly.",
        "A successful tool execution proves procedure, not that the chosen stage or threshold was appropriate.",
        "Judge sparse defects at native scale and inspect a difference image; a full-frame beauty slider can hide one-pixel damage.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Run on the linear stack only when sparse residual defects remain after integration.",
                "Use the standing code and ontology defaults for sigma, odd median-kernel size, and maximum connected-defect size.",
                "Review pixels_fixed, pct_fixed, a native-scale blink, and a difference image before accepting.",
            ),
            controls_and_starting_ranges=(
                ("sigma", "5.0 standing ontology and implementation default"),
                ("kernel_size", "5 pixels, odd; standing ontology and implementation default"),
                ("max_defect_size", "3 connected pixels; standing implementation default"),
            ),
            expected_result="Only isolated components of at most three flagged pixels are replaced by local medians.",
            failure_modes=(
                "Threshold is low enough to flag star cores.",
                "Connected real structure fragments into small flagged islands.",
                "A large correction fraction is accepted without investigation.",
            ),
            recovery=("Revert to the unchanged linear input and raise the threshold or correct subframes before integration.",),
            mask_support="No user mask; the connectivity filter is the implementation's structural safeguard.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-cosmetic-source",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="M66 validation environment; exact version not recorded",
            host="PixInsight CosmeticCorrection",
            instructions=(
                "Prefer applying CosmeticCorrection to calibrated subframes before registration and integration.",
                "Use a master-dark defect list when available; otherwise configure conservative hot/cold detection.",
                "Inspect the corrected subframes or rejection evidence at native scale before continuing.",
            ),
            controls_and_starting_ranges=(
                ("hot/cold sigma", "approximately 3-5 is M66 guidance, not a universal preset"),
                ("target", "calibrated subframes; post-stack M66 execution validated tool operation only"),
            ),
            expected_result="Repeatable isolated defects disappear from subframes without changing stellar profiles.",
            failure_modes=(
                "Using the batch process successfully on an integrated stack and calling the recipe stage validated.",
                "Thresholds remove faint-star cores or compact detail.",
            ),
            recovery=("Undo and move the correction to calibrated subframes or use a more conservative defect map.",),
            mask_support="Tool-specific defect lists/auto-detection; no handbook claim of ordinary image-mask behavior.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4 reference; M66 standalone test recorded as 1.4.2",
            host="Siril calibration or Cosmetic Correction",
            instructions=(
                "During calibration, prefer master-dark sigma detection or a bad-pixel map; enable CFA handling for Bayer data.",
                "Estimate the corrected count before batch processing; Siril flags estimates above one percent for review.",
                "For an already debayered residual stack, use the standalone Cosmetic Correction filter with CFA disabled and inspect the reported count.",
            ),
            controls_and_starting_ranges=(
                ("cold sigma", "5.00 in the M66 standalone test"),
                ("hot sigma", "5.00 in the M66 standalone test"),
                ("amount", "1.00 in the M66 standalone test"),
                ("CFA", "on only for CFA/Bayer input; off for debayered RGB"),
                ("review threshold", "investigate when the calibration estimate exceeds 1%"),
            ),
            expected_result="Sparse hot/cold pixels are replaced; M66 reported 13 corrected pixels with no obvious broad change.",
            failure_modes=(
                "CFA state is set incorrectly.",
                "A high corrected count is accepted without revisiting thresholds or calibration.",
                "Standalone post-stack cleanup is represented as equivalent to pre-integration correction.",
            ),
            recovery=("Undo, correct the CFA setting, raise sigma, or return to sequence calibration with a master-dark/bad-pixel map.",),
            mask_support="Siril documents cosmetic correction as full-image sensor-defect processing, not a mask-limited operation.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(
                ProvenanceLabel.VENDOR_DOCUMENTED,
                ProvenanceLabel.TOOL_TESTED,
                ProvenanceLabel.HENRY_VALIDATED,
            ),
            source_ids=("siril-calibration-1.4.4", "siril-cosmetic-1.4.4", "m66-manual-verification"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="not established for this operation",
            host="Seti Astro Suite Pro",
            instructions=(
                "Do not claim a native SASpro equivalent without current source or tool evidence.",
                "Perform sensor-defect correction during calibration in Siril or PixInsight before importing the stack.",
            ),
            controls_and_starting_ranges=(),
            expected_result="SASpro receives a calibrated stack whose sensor defects were already handled upstream.",
            failure_modes=("Using a generic blemish or denoise operation as an undocumented cosmetic-correction substitute.",),
            recovery=("Return to the calibrated subframes and a documented calibration-capable tool.",),
            mask_support="Not applicable.",
            equivalence=EquivalenceClass.NO_DIRECT_EQUIVALENT,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
    ),
    measurements=(
        "Corrected-pixel count and fraction, separated by hot/cold class where available.",
        "Native-scale before/after blink and a difference image.",
        "Star-core and compact-structure measurements in representative regions.",
        "Input stage, CFA/debayer state, defect-map provenance, and thresholds.",
    ),
    acceptance_criteria=(
        "Correction occurs at the pre-integration stage when source subframes and calibration evidence are available.",
        "Any post-stack path is labeled residual cleanup, not ordinary calibration equivalence.",
        "The corrected fraction is sparse and explainable; unexpectedly high counts trigger review.",
        "No stellar core or real compact structure appears in the difference image.",
    ),
    sources=(
        M66_VERIFICATION,
        NOVA_COSMETIC_SOURCE,
        NOVA_M66_RUN_1247,
        SIRIL_CALIBRATION_144,
        SIRIL_COSMETIC_144,
    ),
)


CALIBRATION_GATE_ARTICLES: tuple[HandbookArticle, ...] = (
    PEDESTAL_REMOVAL,
    COSMETIC_CORRECTION,
)

ARTICLE_BY_ID = {article.article_id: article for article in CALIBRATION_GATE_ARTICLES}

"""Launch handbook content built against :mod:`handbook_contract`.

Issue #285 lands the eight M66 process families in independently reviewable
batches.  This module now contains all eight: calibration-gates,
background/color, restoration (deconvolution, denoise, star correction), and
stretch -- the deliberately separate final batch, since it is the most
contested and mathematically varied family (six real engines, one
M66-validated). Content is process-first, not a replacement for a
run-specific recipe.  Tool paths deliberately distinguish documented,
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

NOVA_BACKGROUND_SOURCE = EvidenceReference(
    reference_id="nova-background-source",
    title="NOVA background-extraction implementations (GraXpert, ADBE, native PixInsight DBE/GradientCorrection) and ontology",
    locator=(
        "nas_server/seti_astro.py:934 (GraXpert background_extract), "
        "nas_server/seti_astro.py:457 (ADBE); "
        "nas_server/pixinsight.py:188-190 (dbe/dbe_correction/gradient_correction params); "
        "nas_server/pi_postprocess.js:361-395 (DynamicBackgroundExtraction and "
        "GradientCorrection PJSR calls); "
        "nas_server/processing_ontology.json:354 (experiment_variants: graxpert_sub, "
        "graxpert_div, pi_gc, adbe_default, adbe_cubic, adbe_poly_only, none)"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_COLOR_SOURCE = EvidenceReference(
    reference_id="nova-color-source",
    title="NOVA SPCC/SSSC selection, fallback logic, and ontology",
    locator="nas_server/seti_astro.py:4150; nas_server/processing_ontology.json:492",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SIRIL_BACKGROUND_144 = EvidenceReference(
    reference_id="siril-background-1.4.4",
    title="Siril 1.4.4 background extraction documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/background.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_GRAXPERT_144 = EvidenceReference(
    reference_id="siril-graxpert-1.4.4",
    title="Siril 1.4.4 GraXpert interface documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/graxpert.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_SPCC_144 = EvidenceReference(
    reference_id="siril-spcc-1.4.4",
    title="Siril 1.4.4 spectrophotometric color calibration documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/color-calibration/spcc.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SASPRO_SOURCE_118 = EvidenceReference(
    reference_id="saspro-source-1.18.0",
    title="Installed Seti Astro Suite Pro 1.18.0 processing source",
    locator=(
        "saspro/pedestal.py; saspro/numba_utils.py; saspro/stacking_suite.py; "
        "saspro/abe.py; saspro/sssc.py"
    ),
    provenance=ProvenanceLabel.ARTIFACT_CONFIRMED,
)

SASPRO_SYNTHETIC_20260814 = EvidenceReference(
    reference_id="saspro-synthetic-2026-08-14",
    title="SASpro 1.18.0 synthetic-array operation checks",
    locator="2026-08-14 local validation: pedestal, cosmetic correction, and ADBE",
    provenance=ProvenanceLabel.TOOL_TESTED,
)

NOVA_DECONVOLUTION_SOURCE = EvidenceReference(
    reference_id="nova-deconvolution-source",
    title="NOVA BlurXTerminator deconvolution implementation, fallback, and ontology",
    locator="nas_server/seti_astro.py:2667; nas_server/processing_ontology.json:791",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_DENOISE_SOURCE = EvidenceReference(
    reference_id="nova-denoise-source",
    title="NOVA Cosmic Clarity / NoiseXTerminator denoise implementations, variants, and ontology",
    locator=(
        "nas_server/seti_astro.py:1559; nas_server/pixinsight.py:217-219; "
        "nas_server/experiments.py:239-243 (denoise_nxt variant dispatch -- reads "
        "nxt_denoise/nxt_iterations only, not the ontology's nxt_detail field); "
        "nas_server/processing_ontology.json:791"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_STAR_CORRECTION_SOURCE = EvidenceReference(
    reference_id="nova-star-correction-source",
    title="NOVA BlurXTerminator correct-only star-shape implementation and ontology",
    locator="nas_server/seti_astro.py:2623; nas_server/processing_ontology.json:915",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SIRIL_NATIVE_STRETCH_MANUAL = EvidenceReference(
    reference_id="siril-native-stretch-manual",
    title="Project-authored Siril native-stretch guidance (SIRIL_MANUAL['stretch'])",
    locator="nas_server/recipe_page.py:116",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

JEFF_SIRIL_VERALUX_NOTE = EvidenceReference(
    reference_id="jeff-siril-veralux-note",
    title="Jeff: Siril has a separately installed Veralux Python script (2026-08-15)",
    locator="Jeff, direct confirmation, 2026-08-15 conversation",
    provenance=ProvenanceLabel.HENRY_VALIDATED,
)

NOVA_STRETCH_SOURCE = EvidenceReference(
    reference_id="nova-stretch-source",
    title=(
        "NOVA stretch engines: stat_stretch/ghs_stretch (real SASpro calls), "
        "stf_stretch/veralux_stretch/smart_stretch (NOVA-original, no external tool), "
        "PixInsight MultiscaleAdaptiveStretch, and ontology variants"
    ),
    locator=(
        "nas_server/seti_astro.py:273 (stf_stretch, pure NumPy, no setiastro import), "
        "nas_server/seti_astro.py:376 (stat_stretch, calls "
        "setiastro.saspro.imageops.stretch), "
        "nas_server/seti_astro.py:421 (ghs_stretch, calls setiastro.saspro.ghs_preset), "
        "nas_server/seti_astro.py:1136 (veralux_stretch, pure NumPy, NOVA-original, no "
        "setiastro import), nas_server/seti_astro.py:1319 (smart_stretch, adaptive "
        "orchestration over the above); nas_server/pixinsight.py:223 (mas flag); "
        "nas_server/pi_postprocess.js:813-833 (MultiscaleAdaptiveStretch PJSR call, "
        "no parameter override in M66's run); nas_server/tool_params.py:250-274 "
        "(compute_ghs: only alpha and pivot are data-driven, beta/gamma/lp/hp stay at "
        "function defaults); nas_server/processing_ontology.json:956 (13 "
        "experiment_variants across 6 engines)"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
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
            tool_version="exact version not recorded; procedure tool-tested",
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
            tool_version="1.4.4 documentation; procedure tool-tested in 1.4.2",
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
            tool_version="1.18.0 source-inspected and synthetic-array tested",
            host="Seti Astro Suite Pro Pedestal Removal",
            instructions=(
                "Treat target class as irrelevant: decide from calibration provenance and linear-image statistics.",
                "Skip unless each channel's residual offset is independently established; a positive minimum alone is not evidence.",
                "If applied, record each channel minimum and compare distributions, clipping, and color before and after.",
            ),
            controls_and_starting_ranges=(
                ("operation", "per-channel minimum subtraction; no adjustable amount"),
                ("default decision", "skip without independent per-channel calibration evidence"),
            ),
            expected_result="Each channel minimum moves to zero while spatial structure is translated by that channel's own amount.",
            failure_modes=(
                "A cold pixel or legitimate sky sample defines a channel's subtraction.",
                "Different channel minima alter the established color balance.",
                "Clipping or earlier calibration damage is mistaken for a removable pedestal.",
            ),
            recovery=("Undo immediately and return to the unchanged linear image or recalibrate the source frames.",),
            mask_support="The active full image or preview is processed; this is not a calibration mask operation.",
            equivalence=EquivalenceClass.CONCEPTUAL_SUBSTITUTE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED),
            source_ids=("saspro-source-1.18.0", "saspro-synthetic-2026-08-14"),
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
        SASPRO_SOURCE_118,
        SASPRO_SYNTHETIC_20260814,
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
            tool_version="exact version not recorded; procedure tool-tested",
            host="PixInsight CosmeticCorrection",
            instructions=(
                "Prefer applying CosmeticCorrection to calibrated subframes before registration and integration.",
                "Use a master-dark defect list when available; otherwise configure conservative hot/cold detection.",
                "Inspect the corrected subframes or rejection evidence at native scale before continuing.",
            ),
            controls_and_starting_ranges=(
                ("hot/cold sigma", "3-5 is a tested range; tune from defect and star-preservation evidence"),
                ("input", "calibrated subframes; post-stack use is residual cleanup only"),
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
            tool_version="1.4.4 documentation; standalone procedure tool-tested in 1.4.2",
            host="Siril calibration or Cosmetic Correction",
            instructions=(
                "During calibration, prefer master-dark sigma detection or a bad-pixel map; enable CFA handling for Bayer data.",
                "Estimate the corrected count before batch processing; Siril flags estimates above one percent for review.",
                "For an already debayered residual stack, use the standalone Cosmetic Correction filter with CFA disabled and inspect the reported count.",
            ),
            controls_and_starting_ranges=(
                ("cold sigma", "5.00 tested starting point; tune from the corrected-pixel evidence"),
                ("hot sigma", "5.00 tested starting point; tune from the corrected-pixel evidence"),
                ("amount", "1.00 tested starting point"),
                ("CFA", "on only for CFA/Bayer input; off for debayered RGB"),
                ("review threshold", "investigate when the calibration estimate exceeds 1%"),
            ),
            expected_result="Sparse hot/cold pixels are replaced without an obvious broad change or measurable compact-signal damage.",
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
            tool_version="1.18.0 source-inspected and synthetic-array tested",
            host="Seti Astro Suite Pro Stacking Suite Cosmetic Correction",
            instructions=(
                "Enable Cosmetic Correction while calibrating light frames in the Stacking Suite, before registration and integration.",
                "Use Bayer-aware correction for undebayered CFA data and ordinary mono/color correction only for the matching input state.",
                "Start conservatively, then inspect corrected frames and a difference image for repaired defects and altered star cores.",
            ),
            controls_and_starting_ranges=(
                ("recommended hot sigma", "5.0 in the installed Stacking Suite"),
                ("recommended cold sigma", "5.0 in the installed Stacking Suite"),
                ("input mode", "Bayer pattern for CFA data; debayered/mono path otherwise"),
            ),
            expected_result="Sparse isolated hot and cold pixels are replaced during light-frame calibration without changing real compact signal.",
            failure_modes=(
                "The wrong Bayer pattern or debayer state mixes unlike color samples.",
                "Thresholds classify a sharp stellar core as a hot pixel; the synthetic check showed this is possible and requires inspection.",
                "Interactive Blemish Blaster is mistaken for repeatable calibration-stage cosmetic correction.",
            ),
            recovery=("Disable or raise the relevant threshold and recalibrate from the unchanged light frames.",),
            mask_support="The Stacking Suite selects a Bayer-aware or debayered/mono full-frame algorithm; defect handling is not an ordinary image mask.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED),
            source_ids=("saspro-source-1.18.0", "saspro-synthetic-2026-08-14"),
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
        SASPRO_SOURCE_118,
        SASPRO_SYNTHETIC_20260814,
    ),
)


BACKGROUND_EXTRACTION = HandbookArticle(
    article_id="background-extraction",
    schema_version=SCHEMA_VERSION,
    revision=2,
    process_family=ProcessFamily.BACKGROUND_EXTRACTION,
    purpose=(
        "Model and remove unwanted large-scale background variation while preserving "
        "faint astronomical signal that occupies the same image."
    ),
    observable_symptoms=(
        "Representative empty-sky regions differ systematically across the linear image.",
        "Light pollution or moonlight produces an additive slope, dome, or color-dependent wash.",
        "Residual vignetting produces a multiplicative center-to-edge brightness pattern after calibration.",
    ),
    intended_output=(
        "A still-linear image with a more spatially uniform sky and a saved or inspected "
        "background model that contains the unwanted gradient but not the target."
    ),
    limits=(
        "Background extraction cannot replace correct flat-field calibration; Siril specifically recommends a master flat for vignetting.",
        "A flatter background metric does not prove that faint galaxy halo or nebulosity survived.",
        "Subtraction is appropriate for additive gradients; division is reserved for genuinely multiplicative effects.",
        "Background neutralization is a later channel-balance decision, not another name for spatial gradient removal.",
        "Smoothing scales are not comparable across engines: GraXpert's smoothing runs 0.0 (aggressive) to 1.0 "
        "(gentle); PixInsight's native DynamicBackgroundExtraction smoothing is a different, larger-range control "
        "(NOVA's own DBE call uses 5.0). Copying a GraXpert number into DBE's field is not a translation.",
    ),
    required_input_state=(
        "Linear, unstretched data with registration borders and low-coverage edges cropped away.",
        "Representative sky regions identified away from stars, target structure, and stacking artifacts.",
        "Before-operation corner/region statistics and the intended correction mode recorded.",
    ),
    nova_action=(
        "NOVA's ontology exposes seven selectable background-extraction variants; M66's recorded run used "
        "GraXpert AI subtraction (smoothing 0.50). The other six are real, code-confirmed alternatives NOVA "
        "can select but did not use for M66: GraXpert division (vignetting), PixInsight's native "
        "GradientCorrection, and three SASpro ADBE presets (default, cubic, polynomial-only). A 'none' baseline "
        "is also selectable. Do not present the M66-selected path as the only one NOVA runs."
    ),
    nova_evidence_ids=("nova-background-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Empty-sky measurements show a coherent large-scale spatial trend.",
        "The modeled background can be separated from the target without absorbing real extended signal.",
    ),
    skip_when=(
        "The apparent gradient is actually uncropped stacking overlap, calibration failure, or target structure filling the field.",
        "No representative sky region or defensible model can be established.",
        "A prior extraction already produced uniform sky and a clean background model.",
    ),
    scientific_and_aesthetic_notes=(
        "The evidence artifact is the model as well as the corrected image: a plausible-looking result can still be overfit.",
        "Large nebulae and galaxy halos are the adversarial case because real signal can resemble a smooth background trend.",
        "M66 validates one GraXpert subtraction configuration, not a universal smoothing value or model choice.",
        "The project's own history is a warning here: an earlier version of this guidance pointed at PixInsight "
        "DynamicBackgroundExtraction/GradientCorrection as the reference path before Jeff's hands-on M66 "
        "verification established GraXpert as what NOVA actually runs. DBE and GradientCorrection are real, "
        "code-confirmed NOVA alternatives, not fabricated -- they were simply never M66's selected path, and an "
        "earlier draft implied they were without checking.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Crop invalid borders before evaluating the gradient.",
                "Select subtraction for an additive gradient and division only with evidence of a multiplicative residual.",
                "Save or inspect the modeled background and compare representative sky statistics before accepting.",
            ),
            controls_and_starting_ranges=(
                ("M66 recorded path", "graxpert_sub -- GraXpert AI subtraction, smoothing 0.50, model 1.0.1"),
                ("other selectable variants", "graxpert_div, pi_gc (PixInsight GradientCorrection), adbe_default, adbe_cubic, adbe_poly_only, none"),
            ),
            expected_result="The large-scale sky trend decreases while target structure is absent from the background model.",
            failure_modes=(
                "The model reproduces the galaxy halo, nebula, or dense stellar structure.",
                "Uncropped borders bias the fit.",
                "A division correction is used to hide missing or incorrect flats.",
                "Presenting the M66-recorded variant as NOVA's only background-extraction behavior.",
            ),
            recovery=(
                "Revert to the unchanged linear input, correct the crop or calibration, and rerun with a simpler or better-protected model.",
            ),
            mask_support="Model protection is engine-specific; acceptance still requires model inspection and target-region comparison.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-background-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="M66 validation environment; exact version not recorded",
            host="GraXpert AI (Subtraction) via PixInsight",
            instructions=(
                "Use the verified GraXpert route on the cropped linear image.",
                "Choose Subtraction for an additive gradient (light pollution, moonlight) and inspect the generated model before accepting.",
                "Record the installed GraXpert process/model version, smoothing, correction mode, and before/after statistics.",
            ),
            controls_and_starting_ranges=(
                ("M66 correction", "Subtraction"),
                ("M66 smoothing", "0.50 (GraXpert's own 0.0-1.0 scale)"),
                ("generic range", "none established beyond M66; tune from the image and model"),
            ),
            expected_result="The M66-style additive gradient is reduced without the target appearing in the model.",
            failure_modes=(
                "Treating the M66 smoothing value as universal.",
                "Accepting a clean-looking image without inspecting the extracted background.",
            ),
            recovery=("Undo, simplify or protect the model, and repeat from the original linear image.",),
            mask_support="Use host/process structure protection where available; exact mask behavior was not established by the M66 recording.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="not independently confirmed in this repo",
            host="GraXpert AI (Division) via PixInsight",
            instructions=(
                "Use only when a residual center-to-edge brightness pattern is genuinely multiplicative "
                "(vignetting), not an additive light-pollution slope.",
                "Same route as the Subtraction variant, with Division selected instead.",
                "Prefer fixing the flat-field calibration first; Division here is a correction, not a substitute for a missing or wrong master flat.",
            ),
            controls_and_starting_ranges=(("correction", "Division; smoothing 0.5 is NOVA's code default, not M66-recorded"),),
            expected_result="A multiplicative vignetting pattern flattens without a smooth subtraction-style residual.",
            failure_modes=("Using Division to mask a missing or incorrect master flat instead of fixing calibration.",),
            recovery=("Revert and re-derive/apply a correct master flat before repeating.",),
            mask_support="Same as the Subtraction variant; not independently characterized.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-background-source",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="not independently confirmed in this repo",
            host="Native DynamicBackgroundExtraction (DBE)",
            instructions=(
                "A real, selectable NOVA alternative to GraXpert, not the M66-recorded path -- run it directly "
                "in PixInsight on the linear image if you specifically want this route.",
                "Set the correction mode (Subtraction or Division) to match the gradient type.",
                "Compare against a GraXpert result on the same image before treating DBE as equivalent.",
            ),
            controls_and_starting_ranges=(
                ("smoothing", "5.0 (NOVA's code default) -- DBE's own scale, not comparable to GraXpert's 0.0-1.0"),
                ("useRollingPenaltyTerm", "enabled"),
                ("correction", "subtraction or division, matching the gradient type"),
            ),
            expected_result="A modeled background comparable in intent to GraXpert's, produced by PixInsight's own classical algorithm rather than GraXpert's AI model.",
            failure_modes=("Assuming DBE and GraXpert produce the same model because both are called 'background extraction'.",),
            recovery=("Undo and fall back to the M66-verified GraXpert route.",),
            mask_support="DBE supports manual sample-point placement in PixInsight's UI; not exercised through NOVA's automated call.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-background-source",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="not independently confirmed in this repo",
            host="Native GradientCorrection",
            instructions=(
                "A real, selectable NOVA alternative to GraXpert, not the M66-recorded path.",
                "Run PixInsight's GradientCorrection process on the linear image with its own default settings -- "
                "NOVA's call exposes no tunable parameters for this process.",
                "Compare against a GraXpert result on the same image before treating GradientCorrection as equivalent.",
            ),
            controls_and_starting_ranges=(("parameters", "none exposed by NOVA; PixInsight's own process defaults apply"),),
            expected_result="A modeled/corrected background using PixInsight's modern classical gradient-removal process.",
            failure_modes=("Assuming this and GraXpert are interchangeable because both target the same symptom.",),
            recovery=("Undo and fall back to the M66-verified GraXpert route.",),
            mask_support="Not exposed through NOVA's automated call.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-background-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="Siril interface v2.1.0, GraXpert model 1.0.1, verified in Siril 1.4.4",
            host="GraXpert AI via Siril's Python integration",
            instructions=(
                "Open Scripts > Python Scripts > Processing > GraXpert AI on the cropped linear image.",
                "Use Subtraction for the demonstrated additive gradient; leave Keep Background off.",
                "Record the GraXpert interface/model version shown, since it is not bundled with stock Siril.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified", "Background Extraction operation, model 1.0.1, smoothing 0.50, Subtraction, Keep Background off, batch size 4, GPU acceleration enabled when available"),
            ),
            expected_result="Matches the PixInsight/NOVA GraXpert result closely; same engine, different host.",
            failure_modes=("Treating this as a stock Siril feature -- it is a separate Python integration.",),
            recovery=("Undo and re-run with adjusted smoothing, or fall back to native Background Extraction.",),
            mask_support="Model-driven, same as the PixInsight GraXpert route; not independently characterized beyond that.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4",
            host="Native Background Extraction (RBF/polynomial)",
            instructions=(
                "Open Image Processing > Background Extraction on the linear image.",
                "Set samples per line to about 20 and raise Tolerance so samples land off nebulosity and stars.",
                "Use RBF for busy, irregular gradients; use a low-degree polynomial (degree 4 is a ceiling, not a starting point) only for a simple, smooth trend.",
                "Choose Subtraction for additive gradients; inspect the generated model before accepting.",
            ),
            controls_and_starting_ranges=(
                ("RBF smoothing", "0.50 vendor starting point; adjust from the model"),
                ("polynomial degree", "low degree for a simple trend; 4 is a documented maximum, not a default"),
                ("correction", "Subtraction for light pollution; Division reserved for genuine vignetting"),
            ),
            expected_result="Representative sky regions converge and the background model contains no target imprint.",
            failure_modes=(
                "Samples land on nebulosity or the galaxy halo.",
                "A high polynomial degree overcorrects the image.",
                "Division is used for vignetting that should have been corrected by a master flat.",
            ),
            recovery=("Restore the original image, revise samples/model complexity, or repair calibration before retrying.",),
            mask_support="Manual sample placement can exclude target regions.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("siril-background-1.4.4",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected; ADBE execution tested on a synthetic linear array",
            host="ADBE default (polynomial degree 2 + RBF)",
            instructions=(
                "Run ADBE on the cropped linear image with the default preset.",
                "Inspect the background model and compare representative sky regions and target structure.",
                "Record which preset was used because this is a functional alternative, not a replay of GraXpert AI.",
            ),
            controls_and_starting_ranges=(
                ("degree", "2 (quadratic) -- typical for ordinary SeeStar gradients"),
                ("num_samples", "100 auto-placed background sample points"),
                ("use_rbf", "true -- RBF refinement after the polynomial stage"),
                ("rbf_smooth", "0.1 (0.01 very tight to 1.0 very smooth)"),
            ),
            expected_result="The spatial gradient decreases without subtracting extended target signal. Fastest of the three ADBE presets.",
            failure_modes=("Assuming ADBE and GraXpert produce equivalent models because both flatten backgrounds.",),
            recovery=("Revert to the original linear input and try adbe_poly_only for a gentler pass, or use the verified GraXpert path.",),
            mask_support="Tool-specific protection was not independently characterized; rely on model and difference inspection.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED),
            source_ids=("saspro-source-1.18.0", "saspro-synthetic-2026-08-14"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected",
            host="ADBE cubic (polynomial degree 3 + RBF)",
            instructions=(
                "Use in place of the default preset only when the gradient is visibly more complex than a "
                "simple quadratic trend -- a busier or multi-directional gradient.",
                "Inspect the background model for overfitting; a higher-degree polynomial can start absorbing real structure.",
                "Compare against the default preset's model on the same image before preferring this one.",
            ),
            controls_and_starting_ranges=(
                ("degree", "3 (cubic) -- more flexible for complex gradients"),
                ("num_samples", "120"),
                ("use_rbf", "true"),
                ("rbf_smooth", "0.15"),
            ),
            expected_result="A more flexible background model that still excludes target structure; more prone to overfitting than the default preset.",
            failure_modes=("The higher-degree polynomial fits real extended structure as if it were background.",),
            recovery=("Revert to the linear input and use the default or poly-only preset instead.",),
            mask_support="Not independently characterized.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED,),
            source_ids=("saspro-source-1.18.0",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected",
            host="ADBE polynomial-only (degree 2, no RBF)",
            instructions=(
                "Use for a faster, gentler pass when the gradient is simple and RBF refinement risks pulling in "
                "faint extended signal.",
                "Inspect the background model the same way as the other presets.",
            ),
            controls_and_starting_ranges=(
                ("degree", "2"),
                ("num_samples", "100"),
                ("use_rbf", "false -- no RBF refinement stage"),
                ("rbf_smooth", "0.1 (unused with RBF disabled)"),
            ),
            expected_result="A gentler, faster correction than the RBF-enabled presets; less able to follow irregular gradients.",
            failure_modes=("Using this preset on a genuinely irregular gradient that needs RBF's local flexibility.",),
            recovery=("Revert and switch to the default or cubic preset for a more flexible model.",),
            mask_support="Not independently characterized.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED,),
            source_ids=("saspro-source-1.18.0",),
        ),
    ),
    measurements=(
        "Tool, host, software/model version, correction mode, smoothing/model settings, and input linear state.",
        "The saved or displayed background model.",
        "Matched representative empty-sky medians and channel ratios before and after.",
        "Target-halo or nebulosity measurements plus a difference image to detect signal loss.",
    ),
    acceptance_criteria=(
        "Invalid borders and calibration defects are resolved before fitting.",
        "The modeled background contains the unwanted large-scale trend and no recognizable target structure.",
        "Representative sky variation decreases without material target-signal loss.",
        "Correction type and settings are recorded; the result remains linear for color calibration.",
    ),
    sources=(
        M66_VERIFICATION,
        NOVA_BACKGROUND_SOURCE,
        NOVA_M66_RUN_1247,
        SIRIL_BACKGROUND_144,
        SIRIL_GRAXPERT_144,
        SASPRO_SOURCE_118,
        SASPRO_SYNTHETIC_20260814,
    ),
)


COLOR_CALIBRATION = HandbookArticle(
    article_id="color-calibration",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.COLOR_CALIBRATION,
    purpose=(
        "Derive reproducible channel scaling from catalog spectra, the imaging system response, "
        "and an explicit white reference instead of balancing color by eye."
    ),
    observable_symptoms=(
        "The linear image retains the camera/filter response rather than an intentional color reference.",
        "Star colors or background channel ratios show a broad systematic cast after gradient removal.",
    ),
    intended_output=(
        "A linear, astrometrically valid image with recorded calibration inputs, catalog matches, "
        "fit evidence, and channel coefficients that downstream linked operations preserve."
    ),
    limits=(
        "A process-complete message does not prove that the WCS, sensor, filter, white reference, or fit was valid.",
        "Broadband SPCC and narrowband/dual-band calibration are not interchangeable prescriptions.",
        "Physically calibrated narrowband intensities do not automatically produce an aesthetic SHO/Hubble palette.",
        "Background neutralization is not a substitute for a valid spectrophotometric solve.",
    ),
    required_input_state=(
        "Linear, unstretched color data after accepted background extraction.",
        "A correct plate solution whose field center, scale, and orientation match the image.",
        "Known OSC/mono state, sensor response, filter or passband information, and chosen white reference.",
    ),
    nova_action=(
        "NOVA uses PixInsight SPCC as the broadband default when its prerequisites are valid, "
        "but treats light-pollution/dual-band data separately: SSSC is the intended physical "
        "route where configured, while failed SPCC can fall back to generic color calibration. "
        "The step is therefore conditional, not universally forced."
    ),
    nova_evidence_ids=("nova-color-source", "nova-m66-run-1.24.7"),
    use_when=(
        "The linear image has trustworthy astrometry and documented sensor/filter response.",
        "The chosen calibration mode matches broadband or narrowband acquisition.",
    ),
    skip_when=(
        "The image is stretched, monochrome without an intended color composition, or lacks valid astrometry.",
        "Required sensor/filter/passband information is unknown.",
        "An aesthetic false-color palette is the goal and physical line intensity is not the intended balance.",
    ),
    scientific_and_aesthetic_notes=(
        "Siril SPCC compares measured image flux ratios with Gaia DR3 spectra folded through the selected sensor and filters.",
        "White reference is an explicit rendering choice inside a physically grounded calibration; Average Spiral Galaxy is Siril's broad default, not a universal truth.",
        "A linked downstream stretch preserves established channel relationships; unlinked stretching can overwrite them.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Verify or repair WCS and record acquisition/filter state before selecting the calibration route.",
                "Use broadband SPCC only for a compatible broadband path; use configured SSSC handling for LP/dual-band data.",
                "Record route, catalog/reference, matched-star or fit evidence, coefficients, and any retry or fallback.",
            ),
            controls_and_starting_ranges=(
                ("broadband route", "PixInsight SPCC with SeeStar S50 sensor/filter profile"),
                ("LP/dual-band route", "SSSC when configured and adequately matched; otherwise explicit fallback"),
                ("SPCC attempts", "2 in current implementation before generic ColorCalibration fallback"),
            ),
            expected_result="The selected route completes with valid fit evidence and produces channel balance appropriate to the acquisition mode.",
            failure_modes=(
                "Stale or malformed WCS allows a misleading run or forces fallback.",
                "Broadband SPCC is applied to LP/dual-band data with an inappropriate response model.",
                "A fallback result is labeled as successful SPCC.",
            ),
            recovery=("Restore the linear input, repair astrometry/configuration, and rerun while preserving the failed-route record.",),
            mask_support="Calibration is global; background/white reference selection and catalog-star rejection are engine-specific safeguards.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-color-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="M66 validation environment; exact version not recorded",
            host="PixInsight SPCC",
            instructions=(
                "Confirm the image is linear and its astrometric solution is correct.",
                "Select the SeeStar S50 sensor, actual filter/LP mode, and intended white reference.",
                "Apply SPCC, then record catalog, matched-star/fit evidence, coefficients, and whether any fallback occurred.",
            ),
            controls_and_starting_ranges=(
                ("white reference", "Average Spiral Galaxy for the verified M66 broadband run"),
                ("filter mode", "match acquisition; do not infer broadband from image appearance"),
                ("M66 LP mode", "disabled"),
            ),
            expected_result="The fit is defensible and star colors show a plausible range without a global cast.",
            failure_modes=("Wrong WCS or response profile produces invalid matching or misleading balance.", "Fallback is not disclosed."),
            recovery=("Undo, correct WCS/sensor/filter/reference inputs, and repeat from the linear post-background image.",),
            mask_support="Global calibration; use the process's source-detection and fit-rejection controls rather than a decorative image mask.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4",
            host="Siril SPCC",
            instructions=(
                "Run only on linear data and plate-solve with correct center, scale, and orientation first.",
                "Select OSC mode, the closest valid SeeStar sensor/filter response, broadband or narrowband mode, and an explicit white reference.",
                "Inspect and record the R/G and B/G fit plots, matched-star count, and resulting coefficients.",
            ),
            controls_and_starting_ranges=(
                ("catalog", "Gaia DR3; local catalog supported in Siril 1.4+"),
                ("M66 white reference", "Average Spiral Galaxy"),
                ("M66 result", "33 stars; R 0.837, G 0.759, B 1.000"),
            ),
            expected_result="Robust catalog-to-image fits support the applied coefficients and downstream linked operations preserve them.",
            failure_modes=(
                "The remote Gaia service is unavailable and no local catalog is configured.",
                "Existing but inaccurate WCS is trusted without checking image scale and field.",
                "SHO data are expected to become an aesthetic Hubble palette through physical SPCC alone.",
            ),
            recovery=("Use the local Gaia catalog or repair astrometry/configuration; for aesthetic SHO balance, use a documented manual palette workflow.",),
            mask_support="SPCC uses detected/catalog-matched stars and robust fitting; it is not ordinarily applied through an image mask.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("siril-spcc-1.4.4", "m66-manual-verification"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected",
            host="Seti Astro Suite Pro SSSC",
            instructions=(
                "Use SSSC on a linear, plate-solved color image when the field supplies enough spectrum-bearing calibration stars.",
                "Provide valid astrometry and record Gaia-XP matches, solved response, coefficients, and fallback behavior.",
                "Label SSSC as a related physical calibration approach, not the identically implemented SPCC process.",
            ),
            controls_and_starting_ranges=(
                ("catalog", "Gaia-XP spectra"),
                ("NOVA integration minimum", "20 enriched spectrum-bearing stars; otherwise explicit fallback"),
                ("SASpro solution stages", "scalar below 50 stars; color model at 50+; full response at 200+ with adequate color span"),
            ),
            expected_result="A documented system-response solution calibrates the linear image without being mislabeled as SPCC.",
            failure_modes=("Too few matched spectra produce fallback or an unstable solution.", "SSSC and SPCC are described as exact equivalents."),
            recovery=("Repair astrometry, use a star-richer field where appropriate, or fall back explicitly to a validated SPCC route.",),
            mask_support="Global calibration based on matched stellar spectra; no ordinary image-mask claim is established.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-source-1.18.0", "nova-color-source"),
        ),
    ),
    measurements=(
        "Linear-state confirmation and astrometric center, scale, orientation, and solver source.",
        "Sensor, filter/passband mode, white reference, catalog source, and software version.",
        "Matched-star count, fit plots/residuals where available, and resulting channel coefficients.",
        "Explicit success, retry, fallback, or skip state plus downstream linked/unlinked behavior.",
    ),
    acceptance_criteria=(
        "The image is demonstrably linear and correctly plate-solved before calibration.",
        "The response model and calibration route match the acquisition mode.",
        "Fit/match evidence and coefficients are recorded rather than inferring validity from completion alone.",
        "Any fallback is named accurately, and downstream processing preserves or deliberately revises the calibrated balance.",
    ),
    sources=(M66_VERIFICATION, NOVA_COLOR_SOURCE, NOVA_M66_RUN_1247, SIRIL_SPCC_144, SASPRO_SOURCE_118),
)


DECONVOLUTION = HandbookArticle(
    article_id="deconvolution",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.DECONVOLUTION,
    purpose=(
        "Recover spatial detail blurred by seeing and optics on linear data, before "
        "stretch makes the blur visually obvious and harder to separate from noise."
    ),
    observable_symptoms=(
        "Star cores and fine target detail look soft rather than sharp at native scale.",
        "Resolution is limited by the point-spread function, not by tracking error or focus drift.",
    ),
    intended_output=(
        "A still-linear image with tighter stars and recovered fine structure, with no new "
        "ringing halos and no loss of faint extended signal."
    ),
    limits=(
        "Deconvolution is not a fix for elongated or trailed stars; that is a tracking/guiding "
        "problem, not a PSF-blur problem.",
        "AI deconvolution can ring stars into halos that only become obvious after stretch and "
        "star removal -- NOVA's own fallback path documents exactly this failure on NGC 6914.",
        "A stable, estimable PSF is required; deconvolving an unstable or unknown PSF amplifies "
        "noise and artifacts instead of recovering detail.",
    ),
    required_input_state=(
        "Linear, unstretched data, after background extraction and color calibration.",
        "A usable point-spread function -- either auto-detected from stars or a supplied estimate.",
    ),
    nova_action=(
        "NOVA's standard-mode default is bxt_deconvolve: full PixInsight BlurXTerminator with "
        "automatic PSF detection, stellar amount 0.5 and nonstellar amount 0.3. When PixInsight/BXT "
        "is unavailable or fails, it falls back to SASpro's Cosmic Clarity Sharpen at the same "
        "amounts -- a different engine with a different risk profile, not a silent equivalent."
    ),
    nova_evidence_ids=("nova-deconvolution-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Stars and fine detail are measurably softer than the PSF should allow on otherwise "
        "well-tracked, well-focused linear data.",
    ),
    skip_when=(
        "Stars are elongated or trailed from tracking/guiding error rather than seeing blur.",
        "The image is already noisy or oversharpened; deconvolution amplifies both.",
        "No stable PSF can be estimated.",
    ),
    scientific_and_aesthetic_notes=(
        "AI deconvolution (BXT, Cosmic Clarity Sharpen) is not classical Richardson-Lucy; both "
        "exist in NOVA's codebase but only the AI path is the default -- classical RL "
        "(deconvolve_rl) is an explicit experiment-mode comparison, not the recipe path.",
        "The same amounts (0.5 stellar / 0.3 nonstellar) mean different things on BXT and Cosmic "
        "Clarity Sharpen; do not treat identical numbers as proof of identical output.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="workflow 1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Run on linear data after background extraction and color calibration, before denoise.",
                "Use automatic PSF detection unless a specific estimate is already validated.",
                "Inspect stars and faint structure at 1:1 before accepting; a plausible preview is "
                "not sufficient evidence against ringing that only appears after later steps.",
            ),
            controls_and_starting_ranges=(
                ("engine default", "bxt_deconvolve (PixInsight BlurXTerminator)"),
                ("M66 recorded amounts", "stellar_amount 0.5, nonstellar_amount 0.3"),
                ("fallback", "cc_sharpen_inprocess (SASpro Cosmic Clarity Sharpen) when PI/BXT unavailable"),
            ),
            expected_result="Stars tighten and fine detail sharpens without new ringing or halos.",
            failure_modes=(
                "Cosmic Clarity Sharpen looks sharper on the linear preview but rings stars into "
                "halos that become catastrophic after stretch and star removal.",
                "Deconvolving noisy or poorly tracked data amplifies the defect instead of detail.",
            ),
            recovery=("Revert to the pre-deconvolution linear image and re-run with the primary BXT path or a lower amount.",),
            mask_support="No explicit mask; protection comes from amount and PSF choice, not a painted mask.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-deconvolution-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="Core 1.9.3 Lockhart host; BlurXTerminator version not independently confirmed in this repo",
            host="PixInsight BlurXTerminator",
            instructions=(
                "Run BlurXTerminator directly on the linear image with automatic PSF detection.",
                "Use NOVA's recorded amounts (stellar 0.5, nonstellar 0.3) as a starting point, not a universal preset.",
                "Compare star profiles and faint structure before and after at native scale.",
            ),
            controls_and_starting_ranges=(
                ("M66 recorded amounts", "stellar_amount 0.5, nonstellar_amount 0.3"),
                ("PSF", "automatic detection (auto_psf)"),
            ),
            expected_result="Sharper stars and detail with no visible ringing at native scale.",
            failure_modes=("Accepting a sharpened linear preview without checking for ringing after stretch and star removal.",),
            recovery=("Undo and re-run with a lower amount or a manually estimated PSF.",),
            mask_support="Not applicable; BXT operates on the full image without a painted mask.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-deconvolution-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="RC Astro BlurXTerminator wrapper UI 1.0.5, model Latest v4, verified in Siril 1.4.4",
            host="Siril via RC-Astro > BlurXTerminator.py (licensed external integration)",
            instructions=(
                "Open Scripts > Python Scripts > RC-Astro > BlurXTerminator.py on the linear image.",
                "Enable Automatic PSF; leave Correct Only off so deblurring/sharpening is applied.",
                "Record the wrapper and model version shown, since they are not bundled with stock Siril.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified settings", "Automatic PSF on; Sharpen Stars 0.50; Adjust Star Halos 0.00; Sharpen Nonstellar 0.30; Correct Only off; Tile Overlap 0.20"),
            ),
            expected_result="Matches the PixInsight/NOVA BXT result closely; same engine, different host.",
            failure_modes=(
                "Treating this as a stock Siril feature -- it requires a separately licensed RC Astro integration.",
                "Siril has no native AI deconvolution; classical Richardson-Lucy PSF-from-stars is the only no-extra-cost native path and is not equivalent.",
            ),
            recovery=("Revert to the pre-deconvolution image and re-run with adjusted amounts or the native RL path.",),
            mask_support="Not demonstrated; treat as full-image like the PixInsight host.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected; Cosmic Clarity Sharpen demonstrated at version 1.19.11",
            host="Seti Astro Suite Pro Cosmic Clarity Sharpen",
            instructions=(
                "Run Cosmic Clarity Sharpen in Both mode (stellar and non-stellar) on the linear image.",
                "This is the same engine NOVA's bxt_deconvolve falls back to -- treat its ringing risk "
                "as real, not hypothetical, and inspect star profiles closely.",
                "Do not present the demonstrated amounts as a recovered NOVA value; they are a tested starting point.",
            ),
            controls_and_starting_ranges=(
                ("M66 demonstrated", "automatic PSF detection; mode Both; stellar amount 0.50; non-stellar amount 0.50; non-stellar PSF 3.0"),
                ("NOVA fallback amounts", "stellar_amount 0.5, nonstellar_amount 0.3 (not the same non-stellar value as the demonstrated run)"),
            ),
            expected_result="Sharper stars and detail; compare galaxy detail, star cores, dark rings, and halos 1:1 before accepting.",
            failure_modes=(
                "The same halo/ringing failure NOVA's own code documents for this engine, appearing after stretch and star removal.",
                "Treating the RC Astro CLI integration explored in the same session as evidence for the SASpro-native Cosmic Clarity result -- it is a separate paid engine path.",
            ),
            recovery=("Revert to the linear input, reduce the amount, or use the primary BXT path instead.",),
            mask_support="Not demonstrated; full-image operation.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("saspro-source-1.18.0", "m66-manual-verification"),
        ),
    ),
    measurements=(
        "Star FWHM and eccentricity before and after, from matched luminance extractions.",
        "1:1 inspection of star cores, dark rings, and halos.",
        "Faint extended structure (galaxy arms, dust lanes, nebulosity) preserved, not eroded.",
    ),
    acceptance_criteria=(
        "Stars tighten without new ringing, halos, or damaged profiles.",
        "Faint extended structure survives at the same or better visibility.",
        "The result still looks correct after later steps (stretch, star removal) -- not just on the linear preview.",
    ),
    sources=(M66_VERIFICATION, NOVA_DECONVOLUTION_SOURCE, NOVA_M66_RUN_1247, SASPRO_SOURCE_118),
)


DENOISE = HandbookArticle(
    article_id="denoise",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.DENOISE,
    purpose=(
        "Reduce photon and read noise on linear data before stretch amplifies it, without "
        "erasing the faint real structure that lives in the same tonal range as the noise."
    ),
    observable_symptoms=(
        "Visible luminance and chromatic grain, worse in shadow regions.",
        "Noise that will become much more visible once the image is stretched.",
    ),
    intended_output=(
        "A still-linear image with measurably lower noise and faint structure -- dust lanes, "
        "faint arms, extended nebulosity -- still visible."
    ),
    limits=(
        "Denoise is not free: it can inflate star FWHM slightly, which is exactly why NOVA runs "
        "star correction immediately after it on dense fields.",
        "Aggressive denoise on faint, noise-limited signal erases real structure rather than noise; "
        "there is no setting that distinguishes them perfectly.",
        "A learned/AI denoiser (Cosmic Clarity, NoiseXTerminator) is not a classical wavelet or "
        "NL-Bayes denoiser; they fail differently and are not interchangeable by amount alone.",
    ),
    required_input_state=(
        "Linear, unstretched data, after deconvolution.",
    ),
    nova_action=(
        "NOVA's ontology default is Cosmic Clarity denoise (CLI or in-process) on luma/color "
        "amounts. Individual workflows can select the 'nxt' variant instead -- PixInsight "
        "NoiseXTerminator via the pipeline's run_postprocess(nxt=True) path -- and M66's "
        "seestar_galaxy workflow did exactly that. The exact denoise_linear parameters were not "
        "serialized in M66's own run record; the ontology's code defaults are the traceable source."
    ),
    nova_evidence_ids=("nova-denoise-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Linear noise is visibly present before stretch, and a representative faint-structure "
        "region can be checked for over-smoothing.",
    ),
    skip_when=(
        "The signal is already integration-limited and denoise would erase faint structure rather than noise.",
        "A separate post-stretch denoise pass is already planned; running both risks double-smoothing.",
    ),
    scientific_and_aesthetic_notes=(
        "Which engine NOVA uses (Cosmic Clarity vs NoiseXTerminator) is a workflow choice, not a "
        "universal default -- do not present one as 'the' NOVA denoise without naming the workflow.",
        "Denoise-induced star FWHM inflation is the documented reason star correction exists as its "
        "own downstream family; judge denoise partly by what star correction has to fix afterward.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="workflow 1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Run on linear data after deconvolution.",
                "Confirm which variant the active workflow selects (Cosmic Clarity default, or the "
                "'nxt' PixInsight NoiseXTerminator variant M66's seestar_galaxy workflow used).",
                "Check a representative faint-structure region before and after, not only the sky.",
            ),
            controls_and_starting_ranges=(
                ("Cosmic Clarity default", "denoise_luma 0.5, denoise_color 0.7 (ontology default)"),
                ("nxt variant (M66's workflow)", "nxt_denoise 0.7, nxt_iterations 2 -- code-recovered default, not serialized in this run"),
            ),
            expected_result="Measurably lower noise with faint structure and star cores intact.",
            failure_modes=(
                "Faint arms or dust lanes soften or disappear along with the noise.",
                "Star FWHM inflates more than star correction can cleanly reverse.",
            ),
            recovery=("Revert to the pre-denoise linear image and reduce the amount or switch engine variant.",),
            mask_support="No explicit mask; protection is amount-driven.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-denoise-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="Core 1.9.3 Lockhart host; NoiseXTerminator version not independently confirmed in this repo",
            host="PixInsight NoiseXTerminator",
            instructions=(
                "Run NoiseXTerminator directly on the linear image.",
                "Start from the pipeline's own recorded default rather than the tool's own dialog default.",
                "Compare a representative faint-structure region before and after at native scale.",
            ),
            controls_and_starting_ranges=(
                ("NOVA's nxt default", "nxt_denoise 0.70, nxt_iterations 2"),
            ),
            expected_result="Lower noise with faint structure and star cores preserved.",
            failure_modes=("Accepting the tool's own higher default dialog value without checking against NOVA's recorded default.",),
            recovery=("Undo and re-run at a lower denoise amount or fewer iterations.",),
            mask_support="Not applicable; full-image operation.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-denoise-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="RC Astro NoiseXTerminator wrapper model Latest v3, verified in Siril 1.4.4",
            host="Siril via RC-Astro > NoiseXTerminator.py (licensed external integration)",
            instructions=(
                "Open Scripts > Python Scripts > RC-Astro > NoiseXTerminator.py on the linear image.",
                "Do not accept the wrapper's initial dialog value without checking it against a "
                "representative faint-structure region -- the demonstrated M66 run changed it before applying.",
                "For a no-extra-cost native alternative, use Siril's own Denoise (Anscombe VST + NL-Bayes) "
                "instead; it is a functional alternative, not the same engine.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified (applied, not the initial dialog value)", "Denoise 0.70; 2 iterations; Intensity/Color Separation off; Frequency Separation off; Tile Overlap 0.20"),
                ("native Siril Denoise", "Anscombe VST + NL-Bayes, modulation 1.0, cosmetic correction off"),
            ),
            expected_result="Matches the PixInsight/NOVA NXT result closely when the RC Astro wrapper is used; the native path is a different algorithm.",
            failure_modes=("Applying the wrapper's initial higher default instead of a checked value.",),
            recovery=("Revert to the pre-denoise image and re-run at a lower amount or with the native path.",),
            mask_support="Not demonstrated; full-image operation.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected; Cosmic Clarity Denoise demonstrated at version 1.19.11",
            host="Seti Astro Suite Pro Cosmic Clarity Denoise",
            instructions=(
                "Run Cosmic Clarity Denoise in full mode on the linear image.",
                "Confirm an output was actually produced -- the demonstrated run required a GPU-device-loss retry before completing.",
                "Compare a representative faint-structure region 1:1 before and after.",
            ),
            controls_and_starting_ranges=(
                ("M66 demonstrated", "luminance 0.50, color 0.50, mode full, model Standard, temporary linear-assist stretch 0.25, chunk size 256, overlap 64"),
            ),
            expected_result="Smoother chromatic and luminance noise without smeared dust lanes, erased faint arms, or waxy galaxy structure.",
            failure_modes=(
                "A silent GPU failure that produces no output -- confirm the file was written.",
                "Over-smoothing that gives a waxy, plastic look to galaxy structure.",
            ),
            recovery=("Revert to the linear input and reduce luminance/color strength.",),
            mask_support="Not demonstrated; full-image operation.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("saspro-source-1.18.0", "m66-manual-verification"),
        ),
    ),
    measurements=(
        "Representative faint-structure region (dust lane, faint arm) before and after -- smoothed, not erased.",
        "Sky-region noise statistic before and after.",
        "Star FWHM delta, as input evidence for the following star-correction step.",
    ),
    acceptance_criteria=(
        "Noise measurably decreases in a representative sky region.",
        "Faint extended structure remains visible at 1:1, not smeared or erased.",
        "Star FWHM inflation, if any, is small enough for star correction to address.",
    ),
    sources=(M66_VERIFICATION, NOVA_DENOISE_SOURCE, NOVA_M66_RUN_1247, SASPRO_SOURCE_118),
)


STAR_CORRECTION = HandbookArticle(
    article_id="star-correction",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.STAR_CORRECTION,
    purpose=(
        "Correct star shape -- roundness and elongation, including the FWHM inflation denoise "
        "introduces -- without sharpening or deblurring the background or nebulosity."
    ),
    observable_symptoms=(
        "Stars are slightly elongated or irregular after denoise, especially on dense fields "
        "(globular clusters, open clusters).",
        "Star FWHM measurably increased relative to the pre-denoise image.",
    ),
    intended_output=(
        "Rounder, tighter stars with the background and nebulosity visually unchanged from before "
        "this step."
    ),
    limits=(
        "This is pure geometric correction, not sharpening or denoising -- 'Star Sharpen' is a "
        "misleading name for a step whose sharpening amounts are deliberately zero.",
        "This step happens before the star/starless split (remove_stars_linear); it is not the "
        "same operation and does not create or use a separate star layer.",
        "It cannot fix elongation caused by tracking or guiding error -- that is not a denoise "
        "side effect and this step will not correct it.",
    ),
    required_input_state=(
        "Linear, unstretched data, after denoise -- this step exists specifically to correct what "
        "denoise did to star shapes.",
    ),
    nova_action=(
        "NOVA runs bxt_star_correct: PixInsight BlurXTerminator in Correct Only mode, automatic "
        "PSF, with sharpen_stars, sharpen_nonstellar, and adjust_halos all forced to zero -- pure "
        "star-shape correction, most consequential on dense star fields where denoise inflates FWHM."
    ),
    nova_evidence_ids=("nova-star-correction-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Denoise measurably inflated star FWHM or introduced visible elongation on a dense star field.",
    ),
    skip_when=(
        "Stars are already round and FWHM-stable after denoise; there is nothing to correct.",
        "Elongation is from tracking/guiding error, not denoise -- Correct Only will not fix trailing stars.",
    ),
    scientific_and_aesthetic_notes=(
        "Jeff's own manual verification renamed this step from 'Star Sharpen' to 'Star Correction "
        "-- BlurXTerminator Correct Only' precisely because the old name implied sharpening that "
        "does not happen here; the taxonomy's family name reflects that correction.",
        "Do not equate BlurXTerminator Correct Only with Cosmic Clarity stellar-only sharpening -- "
        "both improve star shape but are different engines and need their own before/after check.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="workflow 1.24.7",
            host="NOVA Python pipeline",
            instructions=(
                "Run immediately after denoise, on the active linear image -- the star/starless "
                "split has not happened yet at this point in the pipeline.",
                "Use automatic PSF detection; leave all sharpen/halo amounts at zero.",
                "Compare matched luminance extractions (identical FWHM/eccentricity settings) before and after.",
            ),
            controls_and_starting_ranges=(
                ("mode", "Correct Only"),
                ("PSF", "automatic detection"),
                ("sharpen_stars / sharpen_nonstellar / adjust_halos", "0.0 / 0.0 / 0.0 -- forced, not tunable"),
            ),
            expected_result="Lower eccentricity, stable-or-improved FWHM, comparable star support, no change to background/nebulosity.",
            failure_modes=("Confusing this step's output with a separate star layer -- none exists yet at this point.",),
            recovery=("Revert to the pre-correction linear image and re-check the denoise step that preceded it.",),
            mask_support="Not applicable; BXT Correct Only operates on the full image.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-star-correction-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="Core 1.9.3 Lockhart host; BlurXTerminator version not independently confirmed in this repo",
            host="PixInsight BlurXTerminator, Correct Only",
            instructions=(
                "Run BlurXTerminator on the active linear image with Correct Only enabled and Sharpen "
                "Stars, Adjust Star Halos, and Sharpen Nonstellar all at 0.",
                "Extract identical before/after luminance copies with the same FWHMEccentricity "
                "sensitivity, upper limit, and PSF model.",
                "Use noise evaluation as a non-regression check only; Correct Only is not a denoiser.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified", "Correct Only on; Sharpen Stars 0; Adjust Star Halos 0; Sharpen Nonstellar 0; automatic PSF"),
            ),
            expected_result="Lower eccentricity without material FWHM growth, halos, ringing, lost detail, or noise regression.",
            failure_modes=(
                "Measuring before/after populations with differing star-support counts and treating the delta as precise.",
                "Retaining an older window/file name after later operations and mistaking it for a different input stage.",
            ),
            recovery=("Undo and re-run with matched extraction settings for a valid comparison.",),
            mask_support="Not applicable; full-image operation.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="RC Astro BlurXTerminator wrapper model v4, verified in Siril 1.4.4",
            host="Siril via RC-Astro > BlurXTerminator.py, Correct Only (licensed external integration)",
            instructions=(
                "Open Scripts > Python Scripts > RC-Astro > BlurXTerminator.py with Correct Only enabled.",
                "Leave Sharpen Stars, Adjust Star Halos, and Sharpen Nonstellar all at 0.",
                "Automatic PSF is disabled/irrelevant in this mode; do not try to tune it.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified", "Correct Only on; Sharpen Stars 0.00; Adjust Star Halos 0.00; Sharpen Nonstellar 0.00; Tile Overlap 0.20"),
            ),
            expected_result="Matches the PixInsight/NOVA result closely; same engine, different host.",
            failure_modes=("Siril has no native equivalent -- attempting a native star-shape-only correction without this integration.",),
            recovery=("Revert to the pre-correction linear image.",),
            mask_support="Not demonstrated; full-image operation.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected; procedure discussed but not independently tested at this exact stage",
            host="Seti Astro Suite Pro Cosmic Clarity Sharpen, stellar-only",
            instructions=(
                "Apply Cosmic Clarity Sharpen in Stellar Only mode to the active linear image -- not "
                "'the star layer', which does not exist yet at this point in the pipeline.",
                "Keep non-stellar sharpening disabled so this stays a star-shape-only pass.",
                "Do not treat this as the same operation as BlurXTerminator Correct Only; compare "
                "before/after star profiles independently, since they are different engines.",
            ),
            controls_and_starting_ranges=(("mode", "Stellar Only; non-stellar sharpening disabled"),),
            expected_result="Improved star roundness with the background and nebulosity unaffected.",
            failure_modes=("Assuming stellar-only Cosmic Clarity Sharpen and BXT Correct Only are interchangeable without their own comparison.",),
            recovery=("Revert to the pre-correction linear image, or defer star-shape correction to PixInsight BXT Correct Only.",),
            mask_support="Not demonstrated; full-image operation.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.TOOL_TESTED),
            source_ids=("saspro-source-1.18.0", "m66-manual-verification"),
        ),
    ),
    measurements=(
        "Star FWHM and eccentricity from matched before/after luminance extractions.",
        "Star support population (count of stars measured), noted when it differs between before/after.",
        "Noise evaluation as a non-regression check, not a success metric.",
    ),
    acceptance_criteria=(
        "Eccentricity decreases without material FWHM growth.",
        "Star support population is comparable before and after.",
        "No new halos, ringing, lost detail, or meaningful noise regression.",
        "Background and nebulosity are visibly unchanged from the input.",
    ),
    sources=(M66_VERIFICATION, NOVA_STAR_CORRECTION_SOURCE, NOVA_M66_RUN_1247, SASPRO_SOURCE_118),
)


STRETCH = HandbookArticle(
    article_id="stretch",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.STRETCH,
    purpose=(
        "Convert linear data -- where almost all real signal sits near zero and is "
        "invisible on a normal display -- into a non-linear image a human can actually "
        "see, without crushing the sky, clipping stars, or hiding the shape of the "
        "stretch curve's own tradeoffs."
    ),
    observable_symptoms=(
        "The linear image displays as almost entirely black except star cores; faint "
        "structure is present in the data but not visible without a tone curve.",
        "A naive linear stretch either leaves the image too dark to judge or blows out "
        "the brightest cores before faint structure becomes visible.",
    ),
    intended_output=(
        "A non-linear image with a dark, low sky background, visible faint structure "
        "(galaxy arms, dust lanes, nebulosity), and star cores that are not hard-clipped."
    ),
    limits=(
        "This is the single most contested, aesthetically-loaded step in the whole "
        "pipeline -- 'correct' output depends on target type, sky darkness, and taste, "
        "not just physics. Treat any single stretch as a judgment call, not a ground truth.",
        "M66's recorded run validates exactly one engine (PixInsight MultiscaleAdaptiveStretch). "
        "NOVA has six real, code-confirmed stretch engines total; the other five are real "
        "alternatives it can select for other targets, not M66-validated.",
        "Different engines are mathematically unrelated -- statistical (percentile-based), "
        "generalized hyperbolic, arcsinh, and multiscale-adaptive curves. A similar-sounding "
        "parameter value on two engines (e.g. a 'target' near 0.08-0.15) does not imply a "
        "similar result; only the engine and its own documented math define the curve.",
        "stf_stretch, veralux_stretch, and the adaptive selector smart_stretch are NOVA-original "
        "code with no `setiastro` import and no PixInsight or SASpro GUI equivalent. veralux_stretch "
        "independently reimplements a real, separately installed Siril Python script named Veralux -- "
        "prefer that real script (Siril tab) as the reference path; Siril's native Asinh Stretch "
        "remains a fallback approximation when the actual script isn't installed. stf_stretch and "
        "smart_stretch still have no manual path in any tool.",
        "Irreversible: clipped highlights and crushed shadows from an aggressive stretch "
        "cannot be recovered from the output alone; the input's actual dynamic range sets "
        "the ceiling on what any engine can safely show.",
    ),
    required_input_state=(
        "Linear, unstretched data, after deconvolution and denoise (and star correction, "
        "on workflows that run it) -- late in the pipeline, immediately before "
        "background-neutralize/color-boost operate on the now-visible tonal range.",
    ),
    nova_action=(
        "NOVA's ontology exposes six real stretch engines behind 13 selectable parameter "
        "presets: PixInsight MultiscaleAdaptiveStretch (pi_mas -- M66's recorded path), "
        "SASpro Statistical Stretch (stat_default/stat_bright/stat_globular), SASpro GHS "
        "Stretch (ghs_default/ghs_galaxy), and three NOVA-original engines with no external "
        "tool behind them: stf_stretch (a from-scratch reimplementation of the STF tone-curve "
        "concept, presets stf_galaxy/stf_nebula), veralux_stretch (an original arcsinh-based "
        "design with auto-detected symmetry point, presets veralux_default/colorful/globular), "
        "and smart_stretch, which profiles the image's dynamic range and star fraction and "
        "adaptively selects among STF/veralux/GHS/stat rather than running one fixed engine."
    ),
    nova_evidence_ids=("nova-stretch-source", "nova-m66-run-1.24.7"),
    use_when=(
        "Always, once linear processing (calibration, background, color, deconvolution, "
        "denoise) is complete -- this is the step that makes the image viewable at all.",
    ),
    skip_when=(
        "The input is already non-linear (re-stretching a stretched image compounds tone "
        "curves and is a distinct, deliberate operation, not this step).",
    ),
    scientific_and_aesthetic_notes=(
        "Jeff's own stated preference is galaxies darker and higher-contrast than a "
        "generic 'bright and colorful' default -- a stretch that looks technically correct "
        "can still be the wrong aesthetic call for a given target.",
        "smart_stretch's adaptive engine selection is itself a real NOVA design decision, not "
        "a documentation gap: for some targets the 'right' engine genuinely depends on "
        "measured dynamic range and star density, not a fixed per-target-type default.",
        "The historical MAS parameters recorded from M66 are the module's own installed "
        "defaults (version 1.1.1.0), not values NOVA computed or tuned for this image -- the "
        "workflow created the process and ran it with zero overrides.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="workflow 1.23.0 (M66 run); ontology current",
            host="NOVA Python pipeline",
            instructions=(
                "Confirm which of the six engines the active workflow selects before assuming "
                "MAS (M66's engine) is the general default -- it is one of six, not the only one.",
                "For stf_stretch or smart_stretch, do not look for a manual reproduction path "
                "in PixInsight/Siril/SASpro; none exists for these NOVA-original engines. Judge "
                "the output on its own measurements instead.",
                "For veralux_stretch, prefer the real Veralux Siril script (see the Siril tab) -- "
                "the same named algorithm, independently reimplemented -- over the native Asinh "
                "Stretch fallback if the actual script is installed.",
                "For stat_stretch/ghs_stretch/mas, the tool-specific tabs below give the real "
                "manual reproduction path.",
            ),
            controls_and_starting_ranges=(
                ("M66 recorded engine", "pi_mas -- PixInsight MultiscaleAdaptiveStretch, module defaults, no override"),
                ("other selectable engines", "stat_stretch, ghs_stretch, stf_stretch, veralux_stretch, smart_stretch"),
                ("NOVA-original, no manual equivalent anywhere", "stf_stretch, smart_stretch"),
                ("NOVA-original, real Siril script counterpart", "veralux_stretch -- Siril's own Veralux script, algorithmically equivalent; native Asinh Stretch is only the fallback"),
            ),
            expected_result="A non-linear image with sky near-black, visible faint structure, and unclipped star cores.",
            failure_modes=(
                "Presenting M66's MAS result as NOVA's one and only stretch behavior.",
                "Assuming stf_stretch or smart_stretch has a PixInsight/Siril/SASpro equivalent.",
                "Reaching for Siril's native Asinh Stretch fallback when the real Veralux script is actually installed and available.",
                "Presenting NOVA's veralux_stretch and the Siril Veralux script as byte-identical just because they share a name and algorithm -- independent implementations, algorithmically equivalent, not exact replay.",
            ),
            recovery=("Revert to the linear input and select a different engine/preset for the target's actual dynamic range.",),
            mask_support="Engine-specific; MAS has an internal lightness mask on its color-saturation stage (see the PixInsight tab).",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-stretch-source", "nova-m66-run-1.24.7"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="MultiscaleAdaptiveStretch module 1.1.1.0",
            host="PixInsight MultiscaleAdaptiveStretch (MAS)",
            instructions=(
                "Create a MultiscaleAdaptiveStretch instance and run it on the full linear "
                "starless image -- not a partial preview; Contrast Recovery does not permit "
                "partial-preview execution.",
                "M66's run applied every module default with zero parameter overrides; only "
                "override noiseThreshold/clippingFraction if you have a specific reason to.",
                "Inspect the whole-image statistics after; they describe the whole array, not "
                "a claim that the empty-sky region specifically landed at the target background.",
            ),
            controls_and_starting_ranges=(
                ("Target Background", "0.150 (module default)"),
                ("Aggressiveness", "0.70 (module default)"),
                ("Dynamic range compression", "0.40 (module default)"),
                ("Background Reference", "disabled (module default)"),
                ("Contrast Recovery", "enabled, scale separation 1024px, intensity 1.00 (module defaults)"),
                ("Color Saturation", "enabled, Amount 0.75, Boost 0.50, Lightness mask enabled (module defaults)"),
            ),
            expected_result="Full-array median ~0.085, p99 ~0.473, max ~0.936 on M66's linear starless input -- deterministic (byte-identical output across repeated runs on the same input).",
            failure_modes=(
                "Substituting manually-tuned values (e.g. Target Background 0.095, Aggressiveness 0.80) and calling the result an exact NOVA reproduction -- those are Jeff's own experimental settings, not what the pipeline ran.",
                "Running Contrast Recovery on a partial preview, which the module does not support.",
            ),
            recovery=("Reset to module defaults and re-run on the full starless image.",),
            mask_support="MAS's own internal Lightness mask restricts its color-saturation stage by luminance; this is not an externally-applied PixInsight mask.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("m66-manual-verification", "nova-stretch-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="Seti Astro Statistical Stretch integration v3.1.2, verified in Siril 1.4.4",
            host="Seti Astro Statistical Stretch via Siril's Python integration",
            instructions=(
                "Open the licensed Seti Astro Statistical Stretch script on the linear image.",
                "Set the target median and black-point sigma, enable Linked Stretch and Normalize.",
                "Apply the curves-boost pass at the recorded strength; check the reported clip percentage before accepting.",
            ),
            controls_and_starting_ranges=(
                ("M66 verified", "Target median 0.15; Black point sigma 5.00; No black clipping, Linked Stretch, Normalize all enabled; HDR Highlight Compress disabled; Stretch luminance only disabled; Apply curves boost enabled at strength 0.50"),
                ("M66 result", "preview clip report 1 of 1,352,337 pixels (0.0001%)"),
            ),
            expected_result="A statistically-targeted stretch with the sky near the target median and negligible hard clipping.",
            failure_modes=("Treating this as a stock Siril feature -- it is a separately licensed external integration, not bundled with Siril.",),
            recovery=("Undo and re-run with a lower target median or higher black-point sigma if the sky lifts too far.",),
            mask_support="Not demonstrated; operates on the full image.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.TOOL_TESTED, ProvenanceLabel.HENRY_VALIDATED),
            source_ids=("m66-manual-verification",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="separately installed Python script; exact version not independently confirmed in this repo",
            host="Veralux via Siril's Python integration",
            instructions=(
                "Open the Veralux script from Siril's Python scripts on the linear image -- a separately "
                "installed script, not a stock Siril feature, the same pattern as the Seti Astro "
                "Statistical Stretch integration above.",
                "This is the real, named script NOVA's veralux_stretch independently reimplements in "
                "Python -- prefer this over the native Asinh Stretch fallback below when it's installed, "
                "since it shares the actual algorithm rather than only the general arcsinh math.",
                "Record the exact menu path, script version, and every setting used; none of that is "
                "independently confirmed in this repository yet.",
            ),
            controls_and_starting_ranges=(
                ("availability", "Jeff-confirmed installed and available; exact menu path, version, and default parameters not yet independently verified in this repo"),
            ),
            expected_result="An arcsinh-based stretch from the same named script family NOVA's veralux_stretch reimplements, closer to NOVA's engine than the native Asinh Stretch fallback.",
            failure_modes=(
                "Treating this as a stock Siril feature rather than a separately installed script.",
                "Assuming NOVA's independently-coded veralux_stretch reproduces this script byte-for-byte just because they share a name and algorithm family -- same-named algorithm, independent implementations.",
            ),
            recovery=("Fall back to native Asinh Stretch (below) if the script isn't installed.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.ALGORITHMICALLY_EQUIVALENT,
            provenance=(ProvenanceLabel.HENRY_VALIDATED,),
            source_ids=("jeff-siril-veralux-note",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4",
            host="Native Generalized Hyperbolic Stretch / Histogram Transformation",
            instructions=(
                "Use as a no-extra-cost native alternative when the Seti Astro integration isn't installed.",
                "For GHS: set the Symmetry point just above the background, raise D (stretch intensity) and b (local intensity) to lift signal without blowing the core.",
                "Autostretch (the eyeball icon) is a fast preview, not a final result.",
            ),
            controls_and_starting_ranges=(("GHS starting point", "Symmetry point near background level; no fixed D/b range established"),),
            expected_result="A workable stretch using Siril's own native tools; not a reproduction of any specific NOVA engine's exact curve.",
            failure_modes=("Presenting a native GHS/HT result as equivalent to NOVA's MAS, Statistical Stretch, or any NOVA-original engine.",),
            recovery=("Undo and adjust the symmetry point or stretch intensity.",),
            mask_support="Not demonstrated.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("siril-native-stretch-manual",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4",
            host="Native Asinh Stretch (fallback if the Veralux script isn't installed)",
            instructions=(
                "Use only as a no-extra-cost fallback when the actual Veralux script (above) isn't "
                "installed -- both are arcsinh-based, but this is Siril's own separate native tool, a "
                "conceptual approximation rather than the same algorithm.",
                "Open Image Processing > Asinh Stretch on the linear image; its two controls are stretch "
                "factor and black point.",
                "Sample the empty sky first and set the black point from that measurement, mirroring "
                "Veralux's own auto-detected symmetry point, before raising the stretch factor.",
                "Increase the stretch factor gradually and check star cores and color saturation at each step; Siril's default RGB blend clipping mode does its own luminance-based color blending during the stretch, but it has no adjustable equivalent to Veralux's color_grip parameter or its shadow-convergence damping.",
            ),
            controls_and_starting_ranges=(
                ("black point", "set from a measured empty-sky sample, not left at a default; no fixed value established"),
                ("stretch factor", "no fixed value established; raise gradually and inspect"),
                ("not reproduced here", "Veralux's adjustable color_grip parameter and shadow-convergence damping have no Asinh Stretch equivalent; Siril's own default RGB blend clipping mode blends luminance and color differently and isn't a substitute"),
            ),
            expected_result="A dark-sky, arcsinh-shaped stretch broadly similar in character to Veralux, without matching its exact curve, color handling, or automatic background detection.",
            failure_modes=(
                "Leaving the black point at Siril's default instead of sampling the actual sky level.",
                "Presenting this result as a reproduction of Veralux rather than an approximation of the same general math.",
            ),
            recovery=("Undo and resample the black point, or reduce the stretch factor if color saturates unevenly.",),
            mask_support="Not demonstrated.",
            equivalence=EquivalenceClass.CONCEPTUAL_SUBSTITUTE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("siril-native-stretch-manual",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected",
            host="Statistical Stretch",
            instructions=(
                "Run Statistical Stretch on the linear image with Linked channels enabled.",
                "Choose a preset by target: default for an ordinary galaxy/broadband target, brighter for a dim target or very dark sky, globular for a dense star cluster.",
                "This is the same underlying algorithm Siril's licensed Seti Astro integration exposes -- both trace to the same statistical-stretch design, hosted differently.",
            ),
            controls_and_starting_ranges=(
                ("stat_default", "target_median 0.08, linked, blackpoint_sigma 4.0, curves_boost 0.05 -- sky should land ~0.06-0.08"),
                ("stat_bright", "target_median 0.13, linked, blackpoint_sigma 4.0, curves_boost 0.05 -- dim targets or very dark skies"),
                ("stat_globular", "target_median 0.11, linked, blackpoint_sigma 5.0, curves_boost 0.05 -- Jeff-confirmed good on C 80 (8.2/10 vs a pipeline stf_nebula run's 6.2/10); lowered from 0.15 after a globular over-stretched with core clipping"),
            ),
            expected_result="Sky lands near the chosen target median with linked-channel color preserved and a mild curves-boost S-curve applied.",
            failure_modes=("Using the galaxy/broadband preset on a dense globular, which needs the extra shadow headroom the globular preset provides.",),
            recovery=("Revert to the linear input and choose the preset matching the actual target density.",),
            mask_support="Not applicable; full-image linked stretch.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-source-1.18.0", "nova-stretch-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 source-inspected",
            host="GHS Stretch",
            instructions=(
                "Run GHS Stretch on the linear image with the pivot set near the sky background, not a generic 0.25.",
                "In this pipeline's automatic parameter selection, only alpha (stretch strength) and pivot are "
                "data-driven from the image's dynamic range; beta, gamma, lp, and hp stay at their function "
                "defaults regardless of target -- do not assume every GHS control is being tuned per-image.",
                "Choose the galaxy preset for a stronger core-to-arm range; the default preset otherwise.",
            ),
            controls_and_starting_ranges=(
                ("ghs_default", "alpha 5.0, beta 0.0, gamma 3.0, pivot 0.02, channel K"),
                ("ghs_galaxy", "alpha 8.0, beta -0.2, gamma 4.0, pivot 0.02, channel K -- stronger core-to-arm range"),
                ("pipeline auto-selection", "alpha and pivot only; beta/gamma/lp/hp are not varied by this pipeline's own parameter logic"),
            ),
            expected_result="A hyperbolic-curve stretch with the pivot anchored near the sky level.",
            failure_modes=("Setting the pivot well above the sky background, which is a documented way to lift real signal into false brightness rather than stretch from the true floor.",),
            recovery=("Revert and re-anchor the pivot to a measured background level.",),
            mask_support="Not applicable; full-image operation.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.ARTIFACT_CONFIRMED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-source-1.18.0", "nova-stretch-source"),
        ),
    ),
    measurements=(
        "Representative empty-sky median/percentile before and after, against the engine's own target.",
        "High-percentile (p99/p99.9) and maximum values, to check core headroom rather than assuming a correct median implies no clipping.",
        "Low- and high-clipped pixel fraction, explicitly measured rather than inferred from a clean-looking preview.",
        "Faint extended structure (galaxy arms, dust lanes, nebulosity) visibility check.",
    ),
    acceptance_criteria=(
        "Sky background lands near the engine's own stated target, measured in a representative region, not assumed from the whole-image median.",
        "Star cores and highlights retain headroom; hard clipping is explicitly measured, not just visually judged absent.",
        "Faint extended structure is visible without being flattened into noise.",
        "The specific engine, preset, and version used are recorded -- not just 'stretch applied'.",
    ),
    sources=(
        M66_VERIFICATION,
        NOVA_STRETCH_SOURCE,
        NOVA_M66_RUN_1247,
        SIRIL_NATIVE_STRETCH_MANUAL,
        JEFF_SIRIL_VERALUX_NOTE,
        SASPRO_SOURCE_118,
    ),
)


HANDBOOK_ARTICLES: tuple[HandbookArticle, ...] = (
    PEDESTAL_REMOVAL,
    COSMETIC_CORRECTION,
    BACKGROUND_EXTRACTION,
    COLOR_CALIBRATION,
    DECONVOLUTION,
    DENOISE,
    STAR_CORRECTION,
    STRETCH,
)

# Compatibility name retained for the first-batch exporter and downstream imports.
CALIBRATION_GATE_ARTICLES = HANDBOOK_ARTICLES

ARTICLE_BY_ID = {article.article_id: article for article in HANDBOOK_ARTICLES}

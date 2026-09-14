"""Handbook Phase 3, Group E1: Sky-Selective Green Rebalance.

Synthesized from Phase-2 research packet P23 (2026-08-28, SeeStar-db
`3a54b06f3951a15f3cd3fd2a5ab33b7db06a3023`). Re-verified against current
source: no material drift found -- `seti_astro.sky_green_rebalance()`'s
defaults, self-gate, max(R,B) excess field, and signal-green extension all
match the packet's description exactly.
"""

from __future__ import annotations

from nas_server.handbook_contract import (
    SCHEMA_VERSION,
    EquivalenceClass,
    EvidenceReference,
    HandbookArticle,
    ProcessFamily,
    ProvenanceLabel,
    ToolGuidance,
)

P23_PACKET = EvidenceReference(
    reference_id="packet-p23",
    title="Phase-2 research packet P23, 2026-08-28",
    locator="packet:P23:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_SKY_GREEN_REBALANCE_SOURCE = EvidenceReference(
    reference_id="nova-sky-green-rebalance-source",
    title="NOVA sky-selective green rebalance implementation and Experiment Mode candidates",
    locator=(
        "nas_server/seti_astro.py:sky_green_rebalance; "
        "nas_server/processing_ontology.json sky_green_rebalance; "
        "nas_server/experiments.py _run_variant"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

M31_M42_EVIDENCE = EvidenceReference(
    reference_id="nova-m31-m42-sky-green-rebalance-history",
    title="M31/M42 real-run sky G/R reduction while preserving object/star color",
    locator="nas_server/seti_astro.py:sky_green_rebalance (code comment, real run values)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

SCIPY_GAUSSIAN_FILTER = EvidenceReference(
    reference_id="scipy-gaussian-filter-p23",
    title="SciPy ndimage.gaussian_filter reference",
    locator="https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_SCNR_DOCS = EvidenceReference(
    reference_id="siril-scnr-p23",
    title="Siril SCNR (green removal) documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/color-calibration.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

PIXINSIGHT_RESOURCES_P23 = EvidenceReference(
    reference_id="pixinsight-resources-p23",
    title="PixInsight official resources",
    locator="https://www.pixinsight.com/resources/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SKY_GREEN_REBALANCE = HandbookArticle(
    article_id="sky-green-rebalance",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.SKY_GREEN_REBALANCE,
    purpose=(
        "Reduce a residual, spatially systematic green excess in the sky/"
        "background of an otherwise successfully calibrated and stretched "
        "image, without recoloring legitimate green, cyan, or teal target "
        "structure -- the SPCC-safe alternative to running broad SCNR "
        "everywhere."
    ),
    observable_symptoms=(
        "A modest green sky cast remains visible after successful color "
        "calibration and a linked stretch that would otherwise be damaged by "
        "running global SCNR (which can crush an already-correct near-black "
        "sky pedestal or break the linked white balance).",
        "The green is concentrated in background/sky regions, not spread "
        "uniformly across genuine emission, reflection, or palette-mapped "
        "structure.",
    ),
    intended_output=(
        "A sky/background region with the identified systematic green excess "
        "reduced toward a bounded neutral reference, with target and stellar "
        "colors in protected regions substantially preserved and no new "
        "magenta cast, channel crush, or visible mask boundary introduced. "
        "When no qualifying sky excess exists, the step passes the image "
        "through unchanged rather than forcing a correction."
    ),
    limits=(
        "This is masked chromatic correction, not calibration: it does not "
        "solve camera response or derive a physical white reference. It "
        "estimates which pixels are likely background from luminance "
        "statistics alone -- not WCS/catalog geometry or trained semantic "
        "segmentation -- so faint real nebulosity, IFN, diffuse galaxy halos, "
        "and large mosaic structure can all be classified as sky because they "
        "are faint, not because they are actually empty.",
        "The default self-gate (sky G/R <= 1.03 passes through unchanged) is "
        "a 3% engineering trigger, not a confidence interval -- 1.029 is not "
        "meaningfully different from 1.031.",
        "The correction field is a Gaussian-smoothed excess "
        "(max(G_smooth - max(R_smooth, B_smooth), 0)), deliberately not a "
        "pointwise green clip, so systematic tint is subtracted rather than "
        "one-sidedly clamping green noise -- the wrapper's own comment "
        "records that an earlier pointwise version dragged M31's sky median "
        "below neutral (1.06 -> 0.93).",
        "Experiment Mode's two candidates (sgr_soft, sgr_standard) pass only "
        "`amount` and default `allow_signal_green` to false; no P23-specific "
        "adaptation function exists in experiments.py. Production autoprocess "
        "can separately opt into an additional signal-region green cap gated "
        "by the target's folio color prior. These are not the same treatment "
        "-- Experiment Mode evidence for sgr_soft/sgr_standard validates only "
        "the sky-only parameterization, not the production folio-gated "
        "signal extension, and the two should not be pooled.",
        "No explicit no-op control exists among the current Experiment Mode "
        "candidates -- without an unchanged baseline in that comparison set, "
        "an assessor can select the least-damaging active treatment without "
        "ever demonstrating that a correction was needed at all.",
        "The optional signal-region correction (opt-in, p99 green-lead "
        "heuristic above signal_trigger=0.06) is an explicitly heuristic "
        "override, not a spectroscopic validation of true target color -- "
        "target green cannot be labeled wrong merely because it is locally "
        "the strongest channel.",
    ),
    required_input_state=(
        "Nonlinear/post-stretch RGB, with upstream gradient and color work "
        "already performed, containing enough low-luminance area for the "
        "algorithm to identify a sky core. For claims about preserving "
        "calibrated target color, the upstream SPCC success/fallback state "
        "should be known and recorded.",
    ),
    nova_action=(
        "seti_astro.sky_green_rebalance() builds a luminance-derived mask: it "
        "sigma-clips the luminance distribution to estimate a sky level and "
        "noise, ramps from that sky level (lo_sigma=1.5) to a signal "
        "transition (hi_sigma=5.0), applies a smoothstep shape, and Gaussian-"
        "feathers the boundary (feather=3.0) to get a sky weight W. A strict "
        "sky core is W > 0.9; with no such region, the function passes "
        "through unchanged (skipped=no-sky). Within that core it self-gates "
        "on measured sky G/R against min_gr=1.03, passing through unchanged "
        "if already at or below that (skipped=no-excess). Otherwise it "
        "Gaussian-smooths the channels (smooth_sigma=6.0), computes the "
        "positive excess max(G_smooth-max(R_smooth,B_smooth),0), and "
        "subtracts amount (default 0.85) times that excess weighted by sky "
        "weight -- the max(R,B) cap means green can be neutralized but never "
        "inverted into magenta, unlike an (R+B)/2-style cap. An opt-in "
        "signal-region extension (allow_signal_green, default false) applies "
        "the same smoothed-excess math inside the signal mask when a p99 "
        "green-lead heuristic exceeds signal_trigger=0.06, at signal_amount="
        "0.6 -- gated in production by the target's folio color prior so "
        "genuinely teal/OIII-dominant targets are not capped. Runtime "
        "diagnostics (amount, gr_before, gr_after, signal_lead, signal_cap, "
        "sky_coverage) are returned and should be persisted with any evidence "
        "record; candidate ID alone does not capture effective treatment."
    ),
    nova_evidence_ids=("nova-sky-green-rebalance-source", "nova-m31-m42-sky-green-rebalance-history"),
    use_when=(
        "The image is already nonlinear and otherwise well calibrated/"
        "processed.",
        "Residual green is concentrated in sky/background rather than "
        "obviously astrophysical target structure.",
        "Successful upstream SPCC makes global SCNR undesirable, but a "
        "measurable modest sky cast remains.",
        "A usable low-luminance sky region exists.",
    ),
    skip_when=(
        "The inferred \"sky\" is actually frame-filling faint nebulosity, "
        "IFN, galaxy halo, unresolved crowded stars, or mosaic structure.",
        "No meaningful sky core exists -- a no-sky skip is the correct "
        "behavior, not a failure to work around.",
        "Measured sky G/R is already within the configured tolerance.",
        "The image is linear and still awaiting physical/catalog color "
        "calibration.",
        "The target/palette legitimately contains dominant green/teal "
        "signal and signal correction has not been independently justified "
        "by the target's own color prior.",
        "A gradient, pedestal, or calibration failure is the actual cause -- "
        "this step should not be used to hide an upstream defect.",
    ),
    scientific_and_aesthetic_notes=(
        "A defensible comparison against global SCNR or between amount "
        "settings must use the same parent FITS at the same pipeline state, "
        "block by target/object morphology, broadband vs. LP/dual-band/"
        "palette state, SPCC success vs. fallback state, stretch engine and "
        "parameters, background-neutralization state, and starless vs. "
        "recombined state -- pooling across those states treats different "
        "problems as the same experiment.",
        "gr_before/gr_after alone is incomplete evidence: a favorable number "
        "can arise from a wrong mask. Always pair it with sky coverage and a "
        "mask-plausibility review, and check target-region and matched-star "
        "color ratios separately.",
        "A neutral-looking sky in this context is a rendering goal, not "
        "proof of physical photometry -- it can be affected by airglow, "
        "residual gradients, calibration state, and palette-mapping choices.",
        "Historical M31/M42 examples reduced reported sky G/R while "
        "preserving object/star color, but are condition-specific artifact "
        "evidence, not a controlled repeated validation series.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="ontology current; NOVA-original NumPy/SciPy implementation, not a SASpro or PixInsight process",
            host="NOVA Python pipeline",
            instructions=(
                "Record gr_before, gr_after, signal_lead, signal_cap, and "
                "sky_coverage for every run used as evidence -- candidate ID "
                "alone is not sufficient provenance.",
                "Do not generalize Experiment Mode sgr_soft/sgr_standard "
                "results to production's folio-gated signal-extension "
                "behavior; they are different treatments.",
                "Inspect the effective sky mask, not only the final image, "
                "before accepting a result on a frame-filling or extended "
                "target.",
            ),
            controls_and_starting_ranges=(
                ("sgr_soft", "amount=0.60; gentler alternative"),
                ("sgr_standard", "amount=0.85; current reference parameterization"),
                ("min_gr", "1.03 self-gate; skip if sky already at or below this"),
                ("allow_signal_green", "false by default; production gates opt-in via folio color prior"),
            ),
            expected_result=(
                "Reduced sky G/R with protected target/star regions "
                "materially unchanged, or an unchanged pass-through when no "
                "qualifying excess or sky core exists."
            ),
            failure_modes=(
                "Faint real target structure misclassified as sky and "
                "desaturated.",
                "Magenta/red overshoot from an inappropriate reference or "
                "excessive correction.",
                "Legitimate OIII/teal signal suppressed by an inappropriately "
                "enabled signal cap.",
            ),
            recovery=("Revert to the parent input; disable the signal cap; tighten or inspect the mask; accept a no-sky/no-excess pass-through as a valid outcome.",),
            mask_support="Built-in luminance-derived sky/signal mask with smoothstep shaping and Gaussian feathering.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-sky-green-rebalance-source", "nova-m31-m42-sky-green-rebalance-history"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="SCNR (global selective-color neutralization); not this step's algorithm",
            host="PixInsight-family SCNR",
            instructions=(
                "Treat global SCNR as a conceptually related but not "
                "equivalent alternative -- it corrects wherever green exceeds "
                "a neutral reference across the whole frame, not only in an "
                "estimated sky region.",
                "Record the exact effective mode/amount/preserve-lightness "
                "settings if using SCNR as a comparison point; adapted "
                "settings under upstream calibration failure can collapse "
                "nominally different presets into the same effective "
                "treatment.",
            ),
            controls_and_starting_ranges=(
                ("mode", "Average-Neutral or Maximum-Neutral; whole-frame, not sky-masked"),
            ),
            expected_result="Whole-frame green neutralization toward the chosen reference.",
            failure_modes=("Crushing an already-correct near-black sky pedestal or breaking a linked white balance after successful SPCC.",),
            recovery=("Prefer the sky-selective step or a no-op after successful calibration.",),
            mask_support="No sky-specific mask -- whole-frame by design.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("pixinsight-resources-p23",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="SCNR (green removal); exact version not independently confirmed in this repo",
            host="Siril SCNR",
            instructions=(
                "Treat Siril's SCNR as the same conceptual family as global "
                "SCNR elsewhere -- whole-frame green suppression, not a "
                "sky-masked correction.",
                "Record the exact effective settings used if comparing "
                "against the sky-selective step.",
            ),
            controls_and_starting_ranges=(
                ("green removal", "whole-frame, not sky-masked"),
            ),
            expected_result="Whole-frame green suppression toward the chosen reference.",
            failure_modes=("Crushing an already-correct sky pedestal or recoloring legitimate green/teal target structure after successful calibration.",),
            recovery=("Prefer the sky-selective step or a no-op after successful calibration.",),
            mask_support="No sky-specific mask -- whole-frame by design.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("siril-scnr-p23",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="SCNR; exact effective mode/amount semantics belong to the separate SCNR article's own source audit, not re-derived here",
            host="Seti Astro Suite Pro SCNR",
            instructions=(
                "Treat SASpro's SCNR as the same whole-frame conceptual "
                "family, not this step's spatially selective algorithm.",
                "Record the effective mode and amount if used as a "
                "comparison point; this step's smoothed-excess masked math "
                "is a different, NOVA-original algorithm, not a SASpro "
                "wrapper.",
            ),
            controls_and_starting_ranges=(
                ("mode", "whole-frame selective-green neutralization, not sky-masked"),
            ),
            expected_result="Whole-frame green neutralization toward the chosen reference.",
            failure_modes=("Crushing an already-correct near-black sky pedestal or breaking a linked white balance after successful SPCC.",),
            recovery=("Prefer the sky-selective step or a no-op after successful calibration.",),
            mask_support="No sky-specific mask -- whole-frame by design.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-sky-green-rebalance-source",),
        ),
    ),
    measurements=(
        "Robust G/R and B/R medians in the exact sky mask used for "
        "evaluation, not whole-frame medians.",
        "gr_before/gr_after paired with sky_coverage from the runtime "
        "diagnostics.",
        "Per-channel sky median shifts, to detect neutralization achieved by "
        "crushing one channel rather than correcting a tint.",
        "Target-region channel ratios on independently defined target masks, "
        "before and after.",
        "Matched unsaturated star color changes.",
        "Local spatial maps of channel-ratio residuals, not only one "
        "aggregate number.",
        "A no-op paired comparison on the exact same parent input.",
    ),
    acceptance_criteria=(
        "A valid evaluation sky region exists and is not obviously dominated "
        "by real target signal.",
        "Sky G/R moves toward the declared target/tolerance without "
        "overshooting into a red/magenta bias, and without channel collapse.",
        "Protected target regions and matched unsaturated stars show "
        "materially smaller color change than the sky correction region.",
        "No visible mask boundary, chromatic halo, clipping, or banding is "
        "introduced.",
        "Effective parameters, mask coverage, upstream calibration state, and "
        "signal-cap state are recorded -- meeting these criteria is single-"
        "run artifact evidence, not universal validation.",
    ),
    sources=(
        P23_PACKET,
        NOVA_SKY_GREEN_REBALANCE_SOURCE,
        M31_M42_EVIDENCE,
        SCIPY_GAUSSIAN_FILTER,
        SIRIL_SCNR_DOCS,
        PIXINSIGHT_RESOURCES_P23,
    ),
)

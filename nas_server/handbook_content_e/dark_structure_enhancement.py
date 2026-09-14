"""Handbook Phase 3, Group F: Dark-Structure Enhancement.

Synthesized from Phase-2 research packet P30 Part A (2026-08-28, SeeStar-db
`e6f359d3689f331ed195a574fafe1f99845739ab`). P30 explicitly recommends
publishing dark-structure enhancement and halo suppression as two separate
Handbook concepts -- this article covers only the former; see
`halo_suppression.py` for the latter. P30's completion comment on #457 is
authoritative per that issue's completion-evidence rule.

Re-verified and calibrated against retained M51 evidence on 2026-09-10 for
issue #722. The ontology now shares the wrapper/adapter's 5.0 standard, its
four DSE candidates remain distinct after adaptation on that fixture, and the
`pi_lhe_dark` candidate routes to PixInsight's existing `lhe` branch using
only the demonstrably consumed `lhe_amount` control.
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

P30_PACKET = EvidenceReference(
    reference_id="packet-p30",
    title="Phase-2 research packet P30 (Part A: Dark-Structure Enhancement), 2026-08-28",
    locator="packet:P30:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_DSE_SOURCE = EvidenceReference(
    reference_id="nova-dse-source",
    title="NOVA dark_enhance() implementation and calibrated ontology candidates",
    locator=(
        "nas_server/seti_astro.py:dark_enhance; nas_server/tool_params.py:compute_dark_enhance; "
        "nas_server/processing_ontology.json dark_enhance; nas_server/experiments.py _STEP_CFG"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

PI_LHE_DARK_DISPATCH_GAP = EvidenceReference(
    reference_id="nova-pi-lhe-dark-dispatch-gap",
    title="Issue #722 pi_lhe_dark dispatch correction and M51 DSE calibration",
    locator="nas_server/experiments.py:_run_variant (fn_name == \"lhe\" branch); nas_server/processing_ontology.json dark_enhance pi_lhe_dark",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SASPRO_WAVESCALE_DSE_UPSTREAM = EvidenceReference(
    reference_id="saspro-wavescale-dse-upstream",
    title="Seti Astro Suite Pro current upstream WaveScale DSE source (version-bound, not the pinned install)",
    locator="setiastro/setiastrosuitepro src/setiastro/saspro/wavescalede.py, ref c2c941b88335c82cc42ced4a2016c43261610918",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

DARK_STRUCTURE_ENHANCEMENT = HandbookArticle(
    article_id="dark-structure-enhancement",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.DARK_STRUCTURE_ENHANCEMENT,
    purpose=(
        "Selectively increase the visibility/contrast of dark structures "
        "embedded in brighter surroundings -- galaxy dust lanes, dark "
        "filaments, faint outer structure -- via multiscale local-contrast "
        "redistribution. This is contrast redistribution over existing "
        "recorded structure, not recovery of missing signal, not "
        "background extraction, deconvolution, denoise, or HDR compression, "
        "and not the same operation as PixInsight LHE, CLAHE, or curves, "
        "even though all pursue related-sounding perceptual goals."
    ),
    observable_symptoms=(
        "A specific dark structure (dust lane, dark filament, faint outer "
        "structure) is visibly or quantitatively present in the stretched "
        "image but lacks local separation from its immediate surroundings.",
    ),
    intended_output=(
        "Dark structures gain local contrast/depth against their immediate "
        "surroundings, with midtone/highlight regions outside the intended "
        "mask preserved and no new ringing, noise/mottle amplification, "
        "unnatural black channels, or seam/background-extraction-residue "
        "exaggeration."
    ),
    limits=(
        "The current upstream SASpro implementation is the clearest "
        "algorithmic description available: `compute_wavescale_dse()` "
        "performs an a-trous multiscale decomposition, builds a darkness "
        "mask from the negative portions of wavelet planes on selected "
        "mid-scales, and increases the magnitude of those negative "
        "coefficients over two iterations with an exponentially decaying "
        "scale weight; on RGB data it works in Lab luminance only, then "
        "converts back. This is contrast redistribution -- it does not "
        "estimate a physical sky/background model and does not infer "
        "missing detail from a PSF. If weak structure is below the useful "
        "SNR of the parent data, making a pattern more visible can equally "
        "make correlated noise or processing residue more visible.",
        "NOVA's `_load_fits()` globally min/max-normalizes the loaded array "
        "before the transform, so normalization is part of the effective "
        "treatment -- a nominally identical boost/scale/gamma applied to "
        "differently-ranged parent files is not necessarily equivalent, and "
        "a comparison against PixInsight LHE must account for this.",
        "NOVA adds an external shadow luminance mask after the SASpro "
        "transform in Experiment Mode, even though SASpro's DSE already "
        "computes its own internal darkness mask from wavelet coefficients. "
        "The real effective treatment is therefore NOVA global "
        "normalization -> SASpro internal wavelet darkness selection -> "
        "DSE transform -> NOVA external luminance-mask blend, not simply "
        "\"WaveScale DSE at boost X.\" That composite chain should be part "
        "of any cited experiment provenance.",
        "Issue #722 traced the historical treatment collapse to adaptive "
        "scaling: with an M51 baseline of 2.1, old declarations 1.0, 1.3, "
        "and 1.8 all hit the 1.0 clamp. A direct retained-frame sweep then "
        "supported declarations 3.0, 3.5, 5.0, and 7.5, which adapt to "
        "distinct 1.26, 1.47, 2.10, and 3.15 treatments on that fixture. "
        "This proves treatment identity and spacing there, not a universal "
        "physical optimum; historical winner counts retain their original "
        "effective-treatment meaning and are not reinterpreted.",
        "The `pi_lhe_dark` candidate now routes through engine `pixinsight` "
        "and function `lhe`, exposing only `lhe_amount`. The former declared "
        "kernel-radius and slope-limit keys were removed because the executed "
        "wrapper does not consume them. This makes LHE executable as a "
        "strategy alternative, but does not make its amount numerically "
        "commensurate with DSE boost or establish comparative superiority.",
        "The calibrated ontology range is 3.0-10.0 with default 5.0. The "
        "adapter still permits values through 12.0, but that wider ceiling "
        "was not selected by the retained-M51 calibration and must not be "
        "presented as validated guidance.",
        "n_scales is a pixel count, not an angular unit -- a native SeeStar "
        "stack, a drizzled stack, and a differently cropped/resampled "
        "mosaic receive materially different angular/spatial treatment at "
        "the same nominal scale value.",
    ),
    required_input_state=(
        "A nonlinear, already-stretched image whose upstream background, "
        "color, denoise, and dynamic-range decisions are already stable, so "
        "this step is not compensating for an earlier defect. Current "
        "ontology places DSE in the nonlinear stage and recommends it after "
        "HDR compression.",
    ),
    nova_action=(
        "`seti_astro.dark_enhance(n_scales=6, boost_factor=5.0, "
        "mask_gamma=1.0)` loads through the normalizing shared loader and "
        "calls SASpro's `compute_wavescale_dse()`. `tool_params."
        "compute_dark_enhance()` derives a heuristic `shadow_snr` from the "
        "whole-image SNR-like proxy scaled by the clamped ratio of the "
        "global stretched median to 0.20, computes boost as roughly "
        "`1.5 + shadow_snr/4` (increased ~25% for galaxies, reduced for "
        "clusters), and derives scales/gamma from other image-statistic "
        "proxies -- these are engineering heuristics keyed to a global "
        "stretched median and generic SNR estimate, not a measurement of "
        "any specific dark structure. In Experiment Mode, "
        "`_adapt_nonlinear_variants()` additionally computes and blends an "
        "external shadow luminance mask (lower bound ~median*0.5 clamped "
        "0.03-0.10, upper bound ~median*2.0 clamped 0.20-0.45) after the "
        "SASpro transform runs, on top of SASpro's own internal darkness "
        "mask."
    ),
    nova_evidence_ids=("nova-dse-source", "nova-pi-lhe-dark-dispatch-gap"),
    use_when=(
        "The parent image is already correctly background-modeled and "
        "stretched, a specific dark structure is visibly/quantitatively "
        "present but lacks local separation, denoise is stable enough that "
        "enhancement is not mostly revealing grain, and the structure's "
        "spatial scale fits the chosen wavelet depth. Galaxy dust lanes and "
        "selected nebular dark features are the strongest current-use "
        "candidates.",
    ),
    skip_when=(
        "The parent already resolves the dark structure naturally, the "
        "target is star-dominated with little meaningful dark extended "
        "structure, background noise or mottling is close to the scale/"
        "contrast of the desired feature, the apparent feature changes "
        "strongly across independent stacks or aligns with gradients, tile "
        "seams, drizzle artifacts, ringing, or denoise residuals, or "
        "additional local contrast would make the image more dramatic but "
        "less natural.",
    ),
    scientific_and_aesthetic_notes=(
        "A rigorous DSE comparison needs the same exact nonlinear parent "
        "FITS for every candidate, a true unchanged `none` arm, effective "
        "post-adaptation parameters recorded for each arm (not just "
        "candidate ID, given the _STEP_CFG mismatch), normalization held "
        "constant or explicitly factored in, the same outer luminance mask "
        "when comparing strength probes, fixed downstream processing, and "
        "target/background ROIs defined before seeing candidate labels "
        "when possible. Compare DSE to LHE only as a strategy comparison, "
        "never a single-knob one -- DSE's wavelet scales/boost/masks and "
        "LHE's kernel radius/slope/amount are not numerically commensurate, "
        "and the corrected route alone is not evidence of a fair comparison.",
        "Entropy, high-frequency power, generic sharpness index, NOVA's "
        "whole-frame SNR-like score, a \"detail level\" score, perceptual "
        "preference, and historical win rate can all rise when noise, "
        "ringing, seams, or artificial texture are amplified. A run-local "
        "preferred DSE result is evidence of preference for that finishing "
        "treatment, not evidence that new astrophysical detail was "
        "recovered.",
        "Clusters are already downweighted in NOVA's adaptive heuristic -- "
        "a sensible direction, but a heuristic policy rather than a "
        "validated universal rule.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="SASpro WaveScale DSE wrapper + external luminance-mask blend; shadow-SNR-heuristic adaptation",
            host="NOVA Python pipeline",
            instructions=(
                "Record effective post-adaptation boost/scales/gamma and "
                "whether the external luminance mask actually applied -- "
                "candidate ID alone is not sufficient; preserve the "
                "post-adaptation treatment record.",
                "Treat `pi_lhe_dark` as an executable strategy alternative, "
                "not as proof that DSE and LHE were fairly compared.",
                "Do not silently treat the adapter's wider ceiling (up to "
                "12.0) as validated guidance; issue #722 calibrated 3-10.",
            ),
            controls_and_starting_ranges=(
                ("dse_whisper / dse_gentle / dse_mild", "declared boost 3.0 / 3.5 / 5.0 -- calibrated parameter probes"),
                ("dse_standard", "declared boost 7.5 -- stronger calibrated probe"),
                ("pi_lhe_dark", "PixInsight LHE alternative; lhe_amount 0.4"),
                ("none", "true control"),
            ),
            expected_result=(
                "Increased local contrast/depth of the intended dark "
                "structure with midtone/highlight regions outside the "
                "intended mask, and background continuity, preserved."
            ),
            failure_modes=(
                "Noise/mottle amplified as apparent structure.",
                "Dark ringing around stars or high-contrast edges.",
                "Duplicated enhancement from the internal SASpro darkness "
                "mask plus the external NOVA mask stacking.",
                "Different appearance at native vs. drizzled sampling "
                "despite identical nominal `n_scales`.",
            ),
            recovery=("Revert to the parent/no-op; lower boost; reduce affected scales; change or soften the mask; fix the actual upstream defect rather than masking it with DSE.",),
            mask_support="Native SASpro internal darkness mask plus an external NOVA-computed luminance-mask blend layered on top -- both are part of the effective treatment.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-dse-source", "nova-pi-lhe-dark-dispatch-gap"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="LocalHistogramEqualization (LHE) as an executable strategy alternative",
            host="PixInsight LocalHistogramEqualization",
            instructions=(
                "LHE can pursue a similar perceptual goal through a "
                "different local-histogram mechanism, but P24 (Local "
                "Contrast) owns LHE's own semantics -- do not duplicate "
                "that research here.",
                "Do not reinterpret historical failed `pi_lhe_dark` attempts "
                "as DSE-vs-LHE evidence after the routing correction.",
            ),
            controls_and_starting_ranges=(
                ("ontology value", "lhe_amount 0.4 -- the only control consumed by the existing wrapper"),
            ),
            expected_result="An LHE output produced through the existing PixInsight dispatch branch.",
            failure_modes=("Treating the corrected route as proof of a fair DSE-vs-LHE comparison.",),
            recovery=("Use P24's own LHE tool guidance for a real PixInsight local-contrast comparison instead.",),
            mask_support="The existing PixInsight LHE path uses its background anchor; no unconsumed kernel/slope controls are declared.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-pi-lhe-dark-dispatch-gap",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="WaveScale DSE (compute_wavescale_dse); project pin 1.18.0",
            host="Seti Astro Suite Pro WaveScale DSE",
            instructions=(
                "Current upstream source performs an a-trous multiscale "
                "decomposition with a darkness mask from negative "
                "wavelet-plane coefficients on mid-scales, two iterations "
                "with exponentially decaying scale weight, Lab-luminance-"
                "only processing on RGB -- treat as version-bound context, "
                "not confirmed proof of the pinned 1.18.0 install's exact "
                "numeric path.",
                "Remember NOVA's shared FITS loader performs a global min/"
                "max normalization to [0,1] before this transform runs.",
            ),
            controls_and_starting_ranges=(
                ("n_scales", "3-8, ontology default 6 -- pixel count, not an angular unit"),
                ("boost_factor", "calibrated ontology 3.0-10.0, default 5.0"),
                ("mask_gamma", "NOVA adaptive ~1.1 + SNR/80, clamped 1.0-1.6"),
            ),
            expected_result="Multiscale negative-detail enhancement on the Lab luminance channel, converted back to RGB.",
            failure_modes=("Presenting the adapter's wider computed ceiling (up to 12.0) as validated public guidance instead of the documented 1.0-5.0 range.",),
            recovery=("Use the conservative documented range; verify pinned 1.18.0 source before presenting implementation details beyond the wrapper as production-runtime fact.",),
            mask_support="Internal darkness mask built from wavelet-plane negative coefficients; NOVA layers its own external luminance mask on top.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-wavescale-dse-upstream", "nova-dse-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="no dedicated multiscale dark-structure/DSE-equivalent tool independently confirmed in this repo",
            host="Siril (context only for this family)",
            instructions=(
                "No packaged Siril tool matching WaveScale DSE's specific "
                "multiscale darkness-mask mechanism was confirmed in this "
                "repo's research -- Siril's general local-contrast/curve "
                "tools pursue a related perceptual goal by a different "
                "mechanism.",
            ),
            controls_and_starting_ranges=(
                ("availability", "not currently NOVA's dispatch path for this step"),
            ),
            expected_result="Not applicable to NOVA's current dark-structure-enhancement dispatch; documented for completeness only.",
            failure_modes=("Assuming a general Siril local-contrast adjustment is evidence about NOVA's DSE candidates.",),
            recovery=("N/A -- not NOVA's current dispatch path for this family.",),
            mask_support="Not applicable to NOVA's current dispatch for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-dse-source",),
        ),
    ),
    measurements=(
        "Local contrast across known dust-lane/adjacent-signal profiles, "
        "and percentile/profile separation between the dark feature and its "
        "immediate surroundings, in matched ROIs on the same parent.",
        "Background RMS/MAD in matched empty-sky ROIs, and a difference "
        "image showing where the transform actually acted.",
        "Edge/ring/overshoot measurements near sharp boundaries, and shadow "
        "clipping fraction / fraction pinned to zero.",
        "Preservation of bright/midtone regions outside the intended mask, "
        "and color differences/chroma drift on RGB parents.",
        "Effective post-adaptation boost/scales/gamma, distinct from "
        "candidate ID, preserving calibrated treatment identity.",
    ),
    acceptance_criteria=(
        "The intended dark structure gains local contrast/depth against its "
        "immediate surroundings without noise/mottle amplification, dark "
        "ringing, unnatural black channels, or seam/gradient exaggeration, "
        "checked against a real unchanged control.",
        "Effective post-adaptation parameters are recorded, not just "
        "candidate ID; every retained DSE arm must remain a distinct effective "
        "treatment or be explicitly declared a control.",
        "No claim of a fair DSE-vs-PixInsight-LHE comparison is made merely "
        "because the corrected `pi_lhe_dark` route executes.",
        "Entropy, high-frequency power, generic sharpness, whole-frame "
        "SNR-like score, or historical win rate are never presented as "
        "standalone proof of recovered real structure.",
    ),
    sources=(
        P30_PACKET,
        NOVA_DSE_SOURCE,
        PI_LHE_DARK_DISPATCH_GAP,
        SASPRO_WAVESCALE_DSE_UPSTREAM,
    ),
)

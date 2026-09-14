"""Handbook Phase 3, Group E3: HDR and Dynamic-Range Compression.

Synthesized from Phase-2 research packet P25 (2026-08-28, SeeStar-db
`3a54b06f3951a15f3cd3fd2a5ab33b7db06a3023`). Re-verified against current
source 2026-09-08: `seti_astro.hdr_compression()`'s normalizing preprocessing,
`tool_params.compute_hdr_compression()`/`compute_ihdr()`/`compute_hdrmt()`'s
adaptive formulas, and the `hdr_core_blend` P25/P26 boundary all match the
packet's description exactly -- no material drift found.

One packet claim is corrected here, consistent with the `local-contrast` and
`curves`/`saturation` articles: the ontology's declared `none` control for
`hdr_compression` does not currently dispatch as a real copy-through
(issue #669).
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

P25_PACKET = EvidenceReference(
    reference_id="packet-p25",
    title="Phase-2 research packet P25, 2026-08-28",
    locator="packet:P25:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_HDR_SOURCE = EvidenceReference(
    reference_id="nova-hdr-source",
    title="NOVA WaveScale HDR/HDRMT/iHDR implementation, ontology candidates, and adaptation",
    locator=(
        "nas_server/seti_astro.py:hdr_compression; nas_server/tool_params.py:"
        "compute_hdr_compression,compute_ihdr,compute_hdrmt; "
        "nas_server/pi_postprocess.js; nas_server/pi_ihdr.js; "
        "nas_server/processing_ontology.json hdr_compression; "
        "nas_server/experiments.py _adapt_nonlinear_variants"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

PIXINSIGHT_HDRMT_REFERENCE = EvidenceReference(
    reference_id="pixinsight-hdrmt-reference-p25",
    title="PixInsight-authored HDRMultiscaleTransform reference: multiscale dynamic-range control via wavelet/multiscale-median decomposition",
    locator="https://pixinsight.com/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

IHDR_UPSTREAM_REFERENCE = EvidenceReference(
    reference_id="ihdr-upstream-p25",
    title="Uri Darom iHDR distribution (exact installed version unresolved in this repo)",
    locator="https://uridarom.com/pixinsight/scripts/iHDR/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NONE_CONTROL_DISPATCH_GAP = EvidenceReference(
    reference_id="none-control-dispatch-gap-hdr",
    title="Ontology 'none' control candidates fail dispatch instead of running a true copy-through (issue #669)",
    locator="nas_server/experiments.py:_run_variant; SeeStar-db#669",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

HDR_COMPRESSION = HandbookArticle(
    article_id="hdr-dynamic-range-compression",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.HDR_COMPRESSION,
    purpose=(
        "Redistribute already-captured nonlinear brightness relationships so "
        "bright structures occupy less display range and lower-contrast "
        "structure becomes simultaneously visible. This is a tone-"
        "relationship operation, not a way to create missing highlight "
        "information -- once a region is genuinely clipped in the "
        "underlying data or destroyed by an earlier stretch, no HDR "
        "operator can reconstruct that lost signal by compressing tone. "
        "This article covers the global/multiscale family: NOVA/SASpro "
        "WaveScale HDR, PixInsight HDRMultiscaleTransform (HDRMT), and Uri "
        "Darom's iHDR. Bright-core selective HDR (`hdr_core_blend`) is a "
        "separate strategy that changes spatial treatment scope and is "
        "covered by its own article."
    ),
    observable_symptoms=(
        "The stretched image contains real, captured structure spanning "
        "substantially different brightness regimes -- a bright galaxy "
        "nucleus alongside faint arms/dust lanes, or a bright nebular core "
        "alongside surrounding shells/filaments -- and a global display "
        "mapping makes one region readable at the expense of the other.",
    ),
    intended_output=(
        "Bright structures occupy a more manageable tonal range without "
        "materially damaging faint outer signal, background smoothness/"
        "continuity, star cores/halos/colors, local morphology, large-scale "
        "tonal relationships, color balance, or headroom in areas not "
        "meant to be compressed. A copied-input no-op is a legitimate "
        "outcome."
    ),
    limits=(
        "A decrease in p99 or white-pixel pile-up after processing proves "
        "the output was remapped downward -- it does not prove lost "
        "highlight detail was recovered. Entropy, local contrast, "
        "sharpness, and perceptual preference cannot be promoted into proof "
        "of restored astrophysical detail.",
        "NOVA's WaveScale HDR wrapper loads through the shared "
        "`_load_fits()`, which performs a global min/max normalization to "
        "[0,1] before the algorithm runs. That preprocessing is part of the "
        "effective treatment and confounds a direct comparison against "
        "PixInsight HDRMT/iHDR unless explicitly controlled.",
        "PixInsight HDRMT is materially richer than a single \"layers=N\" "
        "candidate row: current NOVA source fixes `medianTransform=true`, "
        "`toLightness=true`, and `luminanceMask=true`, and the wrapper can "
        "apply a post-HDR background re-anchor (a corrective "
        "HistogramTransformation) when positive background drift exceeds "
        "tolerance. Those are treatment-defining choices, not visible in "
        "the candidate row, and the background re-anchor is a cross-engine "
        "confounder the SASpro path does not share.",
        "Uri Darom's iHDR is invoked as an external PixInsight script by "
        "installed path. Its exact installed version is not determinable "
        "from the repository, so precise semantics and valid ranges remain "
        "version-bound to that external script -- NOVA's own "
        "`compute_ihdr()` comments (iterations=strength, higher preservation "
        "=gentler) are source-confirmed NOVA interpretation, not confirmed "
        "against official iHDR documentation.",
        "The three `hdr_whisper/mild/standard` candidates are parameter "
        "probes within one engine (WaveScale HDR); `pi_ihdr_standard` and "
        "`pi_hdrmt_standard` are separate method alternatives. A single "
        "winner table therefore spans both a parameter question and a "
        "method question and must not be read as one clean ranking.",
        "The ontology does declare a `none` control alongside these five, "
        "but it does not currently dispatch as a real copy-through -- "
        "`_run_variant()` only recognizes a no-op via an explicit "
        "`engine == \"none\"` key or `fn is None`, and the candidate sets "
        "`fn: \"none\"` as a string, which falls through to a nonexistent "
        "`seti_astro.none` and returns `ok: False` (issue #669).",
        "`compute_hdrmt()`'s layer/iteration mapping and "
        "`compute_hdr_compression()`'s compression-factor mapping are both "
        "driven by NOVA's own histogram-derived `dynamic_range`/`p99` "
        "statistics, not an independently calibrated physical detector "
        "dynamic-range measurement -- present them as engineering "
        "heuristics, not validated thresholds.",
    ),
    required_input_state=(
        "A nonlinear/post-stretch image with registration/crop geometry "
        "stable, major calibration/gradient/color-calibration problems "
        "already addressed, major linear denoise/deconvolution already "
        "complete, star/starless state known, and current background/"
        "highlight headroom measurable. Any prior CLAHE/LHE/Curves/"
        "sharpening state should be known because it can materially change "
        "what HDR appears to accomplish.",
    ),
    nova_action=(
        "`seti_astro.hdr_compression(n_scales=5, compression_factor=1.5, "
        "mask_gamma=1.0)` loads through the normalizing shared loader and "
        "calls SASpro's `compute_wavescale_hdr(...)`. "
        "`tool_params.compute_hdr_compression()` derives `compression_factor` "
        "from measured dynamic range and the bright-pixel fraction above "
        "p99=0.70 (clamped 1.1-2.8, boosted ~15% for nebulae, capped at 1.4 "
        "for clusters). PixInsight HDRMT (`pi_postprocess.js`) currently "
        "defaults to 6 layers / 3 iterations / 0.0 overdrive with "
        "`medianTransform`, `toLightness`, and `luminanceMask` fixed true, "
        "plus optional background re-anchoring. iHDR (`pi_ihdr.js`) wraps "
        "an external PixInsight script with `compute_ihdr()`-derived "
        "iterations/preservation/mask-strength, adjusted by NOVA image "
        "statistics and object type. `hdr_core_blend` computes the same "
        "WaveScale HDR layer but blends it only under a feathered bright-"
        "core mask -- that is a distinct strategy, covered separately."
    ),
    nova_evidence_ids=("nova-hdr-source", "none-control-dispatch-gap-hdr"),
    use_when=(
        "The image contains real captured structure spanning substantially "
        "different brightness regimes and bright regions dominate the "
        "display -- bright galaxy nuclei plus arms/dust lanes, bright "
        "nebular cores plus surrounding shells/filaments, or compact bright "
        "planetary-nebula structure.",
    ),
    skip_when=(
        "There is no meaningful highlight-compression problem, the bright "
        "region is actually clipped in the underlying data (the goal "
        "\"recover clipped data\" cannot be met by this step), the target "
        "is predominantly smooth low-SNR faint emission and HDR mainly "
        "increases texture/noise, stars dominate the field, local contrast/"
        "Curves already produced a compressed appearance, mosaic seams "
        "become enhanced, or only a tiny bright core needs treatment -- "
        "that is `hdr_core_blend`'s problem, where global HDR is often the "
        "wrong strategy.",
    ),
    scientific_and_aesthetic_notes=(
        "A rigorous experiment must state whether it tests parameter "
        "strength (hold engine/mask/normalization fixed, vary compression "
        "factor), method (cross-engine, controlling preprocessing, mask, "
        "background anchoring, and effective post-adaptation values), or "
        "strategy (global vs. selective-core, or HDR vs. a better upstream "
        "stretch).",
        "A better initial stretch is a real, frequently omitted "
        "counterfactual: whether HDR is needed at all can depend on the "
        "upstream stretch choice, not only on HDR engine/parameter choice.",
        "Do not aggregate blindly across galaxy/nebula/cluster, broadband/"
        "dual-band, native/drizzled scale, starless/stars-present state, or "
        "different PixInsight/SASpro/iHDR versions -- block these as "
        "nuisance factors.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="SASpro WaveScale HDR wrapper + PixInsight HDRMT/iHDR dispatch; image-statistic-driven adaptation",
            host="NOVA Python pipeline",
            instructions=(
                "Record engine/version, effective layers/scales/iterations/"
                "strength, mask state, preprocessing/normalization, and any "
                "background re-anchor event -- candidate ID alone does not "
                "establish treatment identity here.",
                "Do not compare a `hdr_core_blend` result directly against "
                "these global-HDR candidates as though they answer the same "
                "question; they are different strategies with different "
                "spatial scope.",
                "Do not rely on the ontology's declared `none` control as a "
                "working baseline until issue #669 is fixed -- use a "
                "manual copied-input comparison in the meantime.",
            ),
            controls_and_starting_ranges=(
                ("hdr_whisper / hdr_mild / hdr_standard", "WaveScale HDR compression 1.1 / 1.3 / 1.6, scales 5 -- parameter probes within one engine"),
                ("pi_ihdr_standard", "iHDR iterations 5, preservation 5, mask strength 1.25 -- method alternative"),
                ("pi_hdrmt_standard", "HDRMT layers 6, iterations 3, overdrive 0.0 -- method alternative"),
                ("none", "declared control, currently non-dispatching -- see limits"),
            ),
            expected_result=(
                "Improved readability of bright structure while faint "
                "signal, background smoothness, stars, color, and natural "
                "large-scale tone are preserved."
            ),
            failure_modes=(
                "Flattened/plastic bright regions from too much compression, "
                "too many iterations/layers, or an overbroad mask.",
                "Rings/halos around bright structures from multiscale "
                "transition artifacts.",
                "Loss of faint outer signal from global remapping "
                "compressing more of the frame than intended.",
                "Background-anchor side effects attributed entirely to "
                "HDRMT itself when a corrective re-anchor pass also ran.",
            ),
            recovery=("Reduce strength/iterations; narrow spatial treatment or switch to hdr_core_blend; improve mask feathering; compare residual/difference maps against a real baseline.",),
            mask_support="Luminance masks are injected for hdr_compression through the generic nonlinear adaptation path; HDRMT additionally fixes an internal luminance mask in current NOVA source regardless of the candidate row.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-hdr-source", "none-control-dispatch-gap-hdr"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="HDRMultiscaleTransform (HDRMT) + iHDR (Uri Darom, external script); project runtime pin 1.9.3 Lockhart",
            host="PixInsight HDRMultiscaleTransform / iHDR",
            instructions=(
                "Layers/scales is a spatial-scale control, not a generic "
                "strength slider -- too few layers overemphasizes small "
                "structures and stars, too many can flatten larger "
                "morphology.",
                "Check whether a post-HDR background re-anchor ran; if it "
                "did, the effective treatment is HDRMT plus re-anchor, not "
                "HDRMT alone.",
                "Treat iHDR's exact numeric semantics as version-bound to "
                "the installed external script until that version is "
                "confirmed.",
            ),
            controls_and_starting_ranges=(
                ("hdrmt_layers", "6 (current NOVA default) -- spatial-scale control"),
                ("hdrmt_iterations", "3 (current NOVA default) -- repeated application increases strength and ringing/flattening risk"),
                ("ihdr iterations / preservation / mask strength", "5 / 5 / 1.25 (current NOVA default), adjusted per image statistics and object type"),
            ),
            expected_result="Multiscale-decomposed dynamic-range compression via HDRMT or iHDR, with the fixed lightness/median-transform/luminance-mask treatment HDRMT always applies.",
            failure_modes=("Attributing all output difference to layers/iterations when a background re-anchor or the fixed lightness/mask treatment also contributed.",),
            recovery=("Persist the re-anchor event and fixed-treatment flags as part of provenance; re-run with re-anchor disabled to isolate the transform's own effect if needed.",),
            mask_support="HDRMT fixes an internal luminance mask (toLightness+luminanceMask) regardless of candidate row; iHDR exposes an explicit mask-strength control.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("pixinsight-hdrmt-reference-p25", "ihdr-upstream-p25", "nova-hdr-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="WaveScale HDR (compute_wavescale_hdr); project pin 1.18.0",
            host="Seti Astro Suite Pro WaveScale HDR",
            instructions=(
                "Remember NOVA's shared FITS loader performs a global min/"
                "max normalization to [0,1] before this algorithm runs -- "
                "that preprocessing is part of the effective treatment and "
                "a cross-engine confounder unless controlled.",
            ),
            controls_and_starting_ranges=(
                ("n_scales", "3-8, current probes use 5 -- decomposition depth / characteristic structure sizes affected, should track actual feature size and pixel scale rather than image dimensions alone"),
                ("compression_factor", ">1 compresses highlights, <1 boosts; current probes 1.1/1.3/1.6"),
                ("mask_gamma", "current candidates leave this at 1.0, so present experiments do not independently identify gamma sensitivity"),
            ),
            expected_result="A multiscale-decomposed highlight-compressed result on the min/max-normalized input.",
            failure_modes=("Comparing raw before/after histograms across engines without accounting for this wrapper's own normalization step.",),
            recovery=("Control or explicitly record the normalization path before treating a cross-engine histogram comparison as fair.",),
            mask_support="Supported via mask_gamma and the generic nonlinear adaptation luminance mask.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-hdr-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="no dedicated multiscale HDR/dynamic-range-compression tool independently confirmed in this repo",
            host="Siril (context only for this family)",
            instructions=(
                "Siril's own highlight/tone tools (e.g. its stretch and "
                "curve options) can address some dynamic-range display "
                "problems, but a dedicated multiscale HDR transform "
                "comparable to HDRMT/WaveScale HDR/iHDR was not confirmed "
                "in this repo's research -- treat Siril as a different "
                "strategy (better upstream stretch/highlight protection) "
                "rather than a functional-alternative engine for this "
                "specific family.",
            ),
            controls_and_starting_ranges=(
                ("availability", "not currently NOVA's dispatch path for this step"),
            ),
            expected_result="Not applicable to NOVA's current dynamic-range-compression dispatch; documented for completeness only.",
            failure_modes=("Assuming a Siril stretch/tone result is directly comparable to a multiscale HDR candidate.",),
            recovery=("N/A -- not NOVA's current dispatch path for this family.",),
            mask_support="Not applicable to NOVA's current dispatch for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-hdr-source",),
        ),
    ),
    measurements=(
        "Bright-core/highlight retention: p99/p99.9/p99.99 movement, local "
        "variance inside known bright structure, radial/structural profiles "
        "through the core, and edge/ring residuals around compressed "
        "regions.",
        "Faint-structure preservation: matched outer-arm/outer-nebula ROI "
        "median/contrast and difference/residual maps showing whether "
        "broad faint signal was globally dimmed.",
        "Background preservation: robust background median and MAD/RMS in "
        "representative sky ROIs, multi-scale gradient/seam continuity.",
        "Star preservation: unsaturated-star radial profiles/FWHM, core/"
        "halo ratio, and channel-ratio/hue changes for matched stars.",
        "Color preservation: matched-ROI channel ratios/hue/chroma and "
        "bright-core saturation loss.",
    ),
    acceptance_criteria=(
        "Bright structure becomes more readable while faint outer signal, "
        "background smoothness, stars, color, and natural large-scale tone "
        "are preserved, checked against a real copied-input baseline -- "
        "currently a manual copy, since the ontology's own declared `none` "
        "candidate does not yet dispatch (issue #669).",
        "Reduced clipping/p99 is never presented as recovered information "
        "-- only as compression.",
        "Effective engine/version, layers/scales/iterations/strength, mask, "
        "and any background re-anchor event are recorded, not just "
        "candidate ID.",
        "A global-HDR result is never compared directly against "
        "`hdr_core_blend` as though they answer the same strategy "
        "question.",
    ),
    sources=(
        P25_PACKET,
        NOVA_HDR_SOURCE,
        PIXINSIGHT_HDRMT_REFERENCE,
        IHDR_UPSTREAM_REFERENCE,
        NONE_CONTROL_DISPATCH_GAP,
    ),
)

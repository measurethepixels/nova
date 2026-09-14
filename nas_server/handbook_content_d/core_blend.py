"""Handbook Phase 3, Group E3: Bright-Core Selective HDR / Core Blending.

Synthesized from Phase-2 research packet P26 (2026-08-28, SeeStar-db
`3bfe5b9ed2a9bbb0f2de4c38bfce274db5c5ec91`). Re-verified against current
source 2026-09-08: `seti_astro.hdr_core_blend()`'s mask formula, thresholds,
luminance-ratio blend, clipped-hue refill, and the cluster/gate exclusions in
`auto_process.py` all match the packet's description exactly -- no material
drift found.

One packet claim is corrected here: P26 said no no-op/control candidate
existed for this family. The ontology does declare one (`{"id": "none",
"fn": "none"}`), but it does not currently dispatch as a real copy-through
(issue #669) -- so the corrected finding is a declared-but-non-functional
control, not an absent one. The ontology's own ProcessSteps note text also
uses the word "Recovers blown-core detail," which this article does not
repeat -- P26's Independent Critic correctly flagged that phrasing as too
strong; the corrected wording ("reveal/separate structure that remains
encoded") is used throughout instead.
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

P26_PACKET = EvidenceReference(
    reference_id="packet-p26",
    title="Phase-2 research packet P26, 2026-08-28",
    locator="packet:P26:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_CORE_BLEND_SOURCE = EvidenceReference(
    reference_id="nova-core-blend-source",
    title="NOVA hdr_core_blend implementation, run/skip gating, and candidate ladder",
    locator=(
        "nas_server/seti_astro.py:hdr_core_blend; nas_server/auto_process.py "
        "(cluster skip, upstream highlight-occupancy gate, object-type threshold override); "
        "nas_server/processing_ontology.json hdr_core_blend"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

M31_M42_EVIDENCE = EvidenceReference(
    reference_id="nova-m31-m42-core-blend-evidence",
    title="M31/M42 prototype and workflow 1.8.0 execution evidence: motivation, hue-blend fix, and the Trapezium clipping limit",
    locator="critiques/WORKFLOW_CHANGELOG.md (workflow 1.8.0); critiques/20260611_075554_seestar_nebula.md",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

NIST_BLOCKING_P26 = EvidenceReference(
    reference_id="nist-blocking-p26",
    title="NIST/SEMATECH Engineering Statistics Handbook: randomized block designs (nuisance-factor blocking)",
    locator="https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NONE_CONTROL_DISPATCH_GAP = EvidenceReference(
    reference_id="none-control-dispatch-gap-core-blend",
    title="Ontology 'none' control candidates fail dispatch instead of running a true copy-through (issue #669)",
    locator="nas_server/experiments.py:_run_variant; SeeStar-db#669",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

HDR_CORE_BLEND = HandbookArticle(
    article_id="bright-core-selective-hdr",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.HDR_CORE_BLEND,
    purpose=(
        "When only a small bright region needs dynamic-range compression, "
        "process that region selectively instead of applying HDR to the "
        "whole frame. `hdr_core_blend` is not a fourth HDR engine -- it "
        "computes a WaveScale HDR layer (the same engine `hdr_compression` "
        "uses) and blends it into the frame only under a feathered bright-"
        "core luminance mask, typically 0.3-2.7% of the frame. It is a "
        "spatial treatment strategy built on an HDR engine, not an "
        "independent algorithm."
    ),
    observable_symptoms=(
        "A bright nucleus or nebular core occupies a small fraction of the "
        "frame while faint arms, shells, dust, or nebulosity occupy much "
        "more, and whole-frame HDR would fix the core at the cost of "
        "flattening or dimming the faint majority of the image.",
    ),
    intended_output=(
        "Improved readability of still-present bright-core structure while "
        "the untreated frame remains as close as practical to its original "
        "appearance and content -- faint outer signal, background level/"
        "noise texture, star profiles outside the core, core-to-surrounding "
        "transition smoothness, color relationships outside the core, and "
        "large-scale morphology are not materially degraded."
    ),
    limits=(
        "Selective HDR can reveal or separate structure that remains "
        "encoded in near-clipped data. It cannot reconstruct signal that "
        "was numerically clipped or never captured -- the M42 workflow "
        "1.8.0 execution evidence itself shows this limit: even after a "
        "successful core-blend pass, the Trapezium remained grey-white "
        "without clean separation where the data were genuinely clipped. "
        "\"Recovers blown-core detail\" is too strong as general language "
        "and is not used in this article, even though the ontology's own "
        "processing-step note currently uses that phrase.",
        "NOVA's `_load_fits()` performs a global min/max normalization "
        "before processing, so `hdr_core_blend` is not literally \"copy "
        "original pixels everywhere outside the mask, modify only the "
        "core\" -- pixels outside the spatial mask are not guaranteed to "
        "be bit-identical to the original FITS values unless the original "
        "already spanned the same normalized range.",
        "The production upstream \"core clipping\" gate is a relative "
        "highlight-occupancy heuristic (fraction of frame-relative-maximum-"
        "normalized mean-channel luminance at/above 0.90 and 0.98), not a "
        "scientific clipping/saturation measurement. A bright but well-"
        "recorded nonlinear core can exceed these relative thresholds; a "
        "genuinely clipped region can be affected by prior scaling/stretch "
        "state.",
        "The upstream gate and the actual blend mask use different "
        "brightness definitions and normalization: the gate uses mean-"
        "channel luminance divided by its maximum (no minimum subtraction), "
        "while the mask uses Rec.709-style luminance after the wrapper's "
        "min-max normalization, smoothed and feathered. For strongly "
        "colored targets or images with a nontrivial pedestal/background "
        "offset, these can disagree -- a run can pass the upstream gate but "
        "still self-skip inside `hdr_core_blend` (`skipped=\"no-core\"`), "
        "or the gate-inferred extent can differ from the actual mask.",
        "The strategy contains two brightness-selection mechanisms: "
        "SASpro's own internal luminance-dependent WaveScale HDR behavior, "
        "and NOVA's separate external bright-core spatial blend mask. "
        "Describing this as simply \"masked HDR\" hides part of the "
        "effective treatment.",
        "The near-white clipped-hue refill (blending in chroma sampled from "
        "a broader well-exposed surrounding region, active roughly above "
        "L=0.78, using a wide Gaussian sigma-100 convolution) is a "
        "reconstruction heuristic that borrows chromatic context from "
        "nearby pixels. It is not photometric recovery of the original "
        "clipped core hue and must not be presented as such, even though "
        "it produced a more plausible warm continuity in M31 project "
        "review.",
        "Current production explicitly skips both global HDR and "
        "`hdr_core_blend` for cluster object types (point-source-dominated "
        "fields, no intended extended bright core). Any globular-cluster "
        "use of this strategy is a future research question, not current "
        "production behavior -- code outranks a generic target-list "
        "description here.",
        "`core_sigma=6`, `feather=20`, and the hue-refill sigma of 100 are "
        "pixel-space constants, so native vs. drizzled images receive "
        "materially different angular treatment widths for the identical "
        "settings (e.g. at ~2.39\"/px native vs. ~1.19\"/px drizzled S50 "
        "scales, core_sigma spans roughly 14\" vs. 7\"). Image scale is a "
        "required blocking/provenance factor, not an implementation detail "
        "to ignore.",
        "The three ontology candidates (`hdr_core_soft`, "
        "`hdr_core_standard`, `hdr_core_strong`) move threshold and "
        "compression together -- as the candidate gets \"stronger\", "
        "compression increases AND threshold decreases (expanding mask "
        "coverage). A winner therefore cannot isolate whether the benefit "
        "came from stronger compression, larger spatial coverage, or their "
        "interaction; this is a strategy/intensity ladder, not a clean "
        "single-factor parameter experiment. The ontology does declare a "
        "`none` control alongside them, but it does not currently dispatch "
        "as a real copy-through (issue #669) -- `_run_variant()` only "
        "recognizes a no-op via `engine == \"none\"` or `fn is None`, and "
        "this candidate sets `fn: \"none\"` as a string, which falls "
        "through to a nonexistent `seti_astro.none` and returns `ok: "
        "False`.",
        "Unlike `hdr_compression`, `hdr_core_blend` is not currently listed "
        "in Experiment Mode's generic nonlinear-adaptation set -- its "
        "candidates are closer to static strategy probes using wrapper "
        "defaults than image-adapted values, though production is still "
        "conditional in the broader sense (upstream gate, object-type "
        "threshold override, starless availability).",
        "Whole-frame metrics (SNR-like score, sharpness, entropy, dynamic-"
        "range score, perceptual \"more detail\") are structurally "
        "unsuitable here -- a sub-percent to few-percent mask can be "
        "invisible to global statistics. Production correctly exempts "
        "`hdr_core_blend` from the ordinary whole-frame objective veto, but "
        "that exemption means the metric is unsuitable, not that the step "
        "is automatically validated whenever it runs.",
    ),
    required_input_state=(
        "A stretched/nonlinear image with major linear calibration, "
        "background, color, denoise, and restoration operations already "
        "complete; current highlight occupancy measurable; a starless "
        "layer available when the workflow has one (production "
        "preferentially measures it for the upstream gate, since bright "
        "stars can pin the top of the range); image scale and resampling/"
        "drizzle state known; and prior curves/local-contrast/HDR state "
        "known, since these change core brightness and mask coverage.",
    ),
    nova_action=(
        "`hdr_core_blend(threshold=0.72, n_scales=5, compression_factor=1.5, "
        "mask_gamma=1.0, core_sigma=6.0, feather=20.0)` forms Rec.709-style "
        "luminance from the normalized input, smooths it with "
        "`core_sigma`, forms a mask onset `clip((smoothed_L - threshold) / "
        "(0.97 - threshold), 0, 1)`, feathers it with `feather`, and "
        "self-skips (`skipped=\"no-core\"`) when the feathered mask's max "
        "is below 0.05. When it runs, it computes the same "
        "`compute_wavescale_hdr()` layer `hdr_compression` uses, derives a "
        "bounded luminance ratio between the HDR layer and the original, "
        "and blends the new luminance through the mask -- preserving RGB "
        "ratios except where the clipped-hue refill engages above roughly "
        "L=0.78. Production overrides `threshold` by object type (0.80 "
        "galaxy / 0.72 other eligible types, project-validated in the "
        "2026-06-10 M31/M42 prototype work) and skips the step entirely for "
        "cluster object types. Reported `core_coverage` is the fraction of "
        "pixels where the feathered mask exceeds 0.5 -- a descriptive mask "
        "statistic, not a clipped-pixel or physical-unit measurement."
    ),
    nova_evidence_ids=("nova-core-blend-source", "nova-m31-m42-core-blend-evidence", "none-control-dispatch-gap-core-blend"),
    use_when=(
        "A bright nucleus or compact nebular core is much brighter than "
        "the surrounding target, and whole-frame HDR would flatten or dim "
        "the majority of the frame that does not need treatment. Current "
        "production can apply this for galaxies and nebulae/planetary "
        "nebulae when the highlight gate fires.",
    ),
    skip_when=(
        "The object type is a globular or open cluster -- current NOVA "
        "production skips this step as a class for clusters, since a dense "
        "star field is not an extended bright core to tone-map.",
        "The underlying data are genuinely clipped/saturated at the target "
        "region -- selective HDR can only tone-map what remains, not "
        "reconstruct what was lost.",
        "A large fraction of the target needs compression rather than a "
        "small core -- whole-frame `hdr_compression` may be the more "
        "coherent strategy in that case.",
    ),
    scientific_and_aesthetic_notes=(
        "This is a strategy-level tradeoff, not evidence that global HDR "
        "is intrinsically inferior. NOVA's workflow history records the "
        "motivating case directly: global HDR made M42/M31 core structure "
        "more readable but reduced faint-signal presentation enough that "
        "whole-frame assessment rejected the step, which is why "
        "`hdr_core_blend` exists to decouple the local benefit from the "
        "global penalty. If a large fraction of the target genuinely needs "
        "compression, whole-frame HDR may still be the more coherent "
        "operation.",
        "A defensible experiment should independently vary threshold and "
        "compression (rather than moving them together as the current "
        "three-candidate ladder does), hold `n_scales`/`mask_gamma`/"
        "`core_sigma`/`feather`/color-reconstruction fixed unless one is the "
        "factor under study, block by image scale/drizzle state, and "
        "record both the upstream gate metrics and the function's returned "
        "`core_coverage`/`skipped` state -- not just candidate ID.",
        "The 2026-06-10/11 M31 and M42 prototype/run evidence supports the "
        "current strategy for those specific data and goals. It does not "
        "validate universal thresholds, pixel-space mask sizes, or color "
        "reconstruction fidelity across other targets/data types.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="hdr_core_blend built on SASpro WaveScale HDR; object-type threshold override; cluster/gate exclusions",
            host="NOVA Python pipeline",
            instructions=(
                "Record both the upstream gate metrics AND the function's "
                "own `core_coverage`/`skipped` return -- they can disagree, "
                "and that disagreement is itself evidence, not an "
                "unexplained outcome.",
                "Never describe a result as \"recovering\" blown-core "
                "detail; describe it as revealing/separating structure "
                "still encoded in near-clipped data.",
                "Do not rely on the ontology's declared `none` control as a "
                "working baseline until issue #669 is fixed -- use a "
                "manual copied-input comparison in the meantime.",
                "Do not treat threshold and compression as independently "
                "tested by the current three-candidate ladder -- they move "
                "together.",
            ),
            controls_and_starting_ranges=(
                ("hdr_core_soft", "threshold 0.80, compression 1.3 -- gentle, concentrated"),
                ("hdr_core_standard", "threshold 0.72, compression 1.5 -- current nominal"),
                ("hdr_core_strong", "threshold 0.68, compression 1.8 -- wider coverage, stronger compression, confounded with soft/standard by design"),
                ("none", "declared control, currently non-dispatching -- see limits"),
            ),
            expected_result=(
                "Improved readability of still-present bright-core "
                "structure, with faint outer signal, background, star "
                "profiles outside the core, and color relationships "
                "outside the core left materially unaffected."
            ),
            failure_modes=(
                "Flat or \"punched-in\" core from too much compression.",
                "Halo or tonal shelf at the mask boundary from too-tight or "
                "poorly feathered masking.",
                "Star-trigger contamination from a very bright nearby star "
                "influencing the luminance-selected mask despite "
                "pre-smoothing.",
                "Color-reconstruction error from the clipped-hue refill "
                "importing implausible surrounding color, especially risky "
                "for compact emission-line regions or strong color "
                "gradients.",
                "Scale-dependent mask behavior when comparing native and "
                "drizzled products under identical pixel-space settings.",
            ),
            recovery=("Raise threshold or reduce compression for a flat core; adjust mask onset/smoothing/feather for boundary artifacts; use a starless science layer for star-trigger contamination; reduce or disable the color refill and compare to a luminance-only treatment for reconstruction errors.",),
            mask_support="Native and mandatory to the strategy -- a feathered Gaussian-smoothed bright-core luminance mask is the entire mechanism, layered on top of SASpro's own internal luminance-dependent WaveScale HDR weighting.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-core-blend-source", "nova-m31-m42-core-blend-evidence", "none-control-dispatch-gap-core-blend"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="no dedicated selective bright-core HDR mask/blend strategy independently confirmed as an equivalent packaged tool",
            host="PixInsight (context only for this family)",
            instructions=(
                "A comparable manual result is achievable in PixInsight via "
                "HDRMultiscaleTransform or a masked/blended HDR pass with a "
                "hand-built or generated luminance mask, but no single "
                "packaged PixInsight tool matching NOVA's specific mask "
                "formula, threshold/feather behavior, and clipped-hue "
                "refill was confirmed in this repo's research.",
                "Treat any manual PixInsight replication as a functional "
                "alternative built from PixInsight's own masking/HDR "
                "primitives, not a direct equivalence claim.",
            ),
            controls_and_starting_ranges=(
                ("manual mask + HDRMT", "build a bright-core luminance mask (e.g. via PixelMath/range selection) and apply HDRMT or a curve only through it"),
            ),
            expected_result="A manually assembled selective-core dynamic-range treatment, not a one-to-one NOVA equivalent.",
            failure_modes=("Assuming a manual PixInsight selective-HDR result and NOVA's hdr_core_blend are directly comparable without controlling mask geometry, threshold semantics, and color-reconstruction behavior.",),
            recovery=("Document the manual mask/threshold/blend approach explicitly rather than presenting it as an equivalent engine.",),
            mask_support="Fully supported through PixInsight's native masking tools when manually constructed; not a packaged one-call equivalent.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-core-blend-source",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="WaveScale HDR (compute_wavescale_hdr) is the underlying engine; the bright-core mask/blend/refill are NOVA additions, not vendor SASpro behavior",
            host="Seti Astro Suite Pro WaveScale HDR (as the underlying layer)",
            instructions=(
                "Current upstream SASpro `wavescale_hdr.py` confirms the "
                "broad multiscale/luminance-weighted model (Lab-luminance "
                "processing, a-trous decomposition, luminance-dependent "
                "weighting, reconstruction/median alignment, a highlight-"
                "dimming curve) -- this is version-bound theory/interface "
                "context for the underlying layer, not proof of NOVA's "
                "external mask/blend/refill logic, which SASpro does not "
                "provide.",
            ),
            controls_and_starting_ranges=(
                ("n_scales / compression_factor / mask_gamma", "same WaveScale HDR parameters as hdr_compression; here they shape the layer that gets locally blended, not the whole frame"),
            ),
            expected_result="A WaveScale HDR-compressed layer, subsequently blended only under NOVA's external bright-core mask.",
            failure_modes=("Treating SASpro's own internal luminance weighting as the only brightness-selection mechanism in play, ignoring NOVA's separate external mask.",),
            recovery=("Document both selection mechanisms (SASpro internal weighting + NOVA external mask) when describing the effective treatment.",),
            mask_support="SASpro's own internal luminance-dependent weighting applies within the WaveScale HDR call itself, separate from NOVA's external spatial mask.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("nova-core-blend-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="no dedicated selective bright-core HDR tool independently confirmed in this repo",
            host="Siril (context only for this family)",
            instructions=(
                "No packaged Siril tool matching this specific selective-"
                "core strategy was confirmed in this repo's research -- "
                "treat as an out-of-scope comparison for this family rather "
                "than assuming equivalence with Siril's general tone tools.",
            ),
            controls_and_starting_ranges=(
                ("availability", "not currently NOVA's dispatch path for this step"),
            ),
            expected_result="Not applicable to NOVA's current dispatch for this family; documented for completeness only.",
            failure_modes=("Assuming a general Siril tone/stretch adjustment is evidence about NOVA's selective-core strategy.",),
            recovery=("N/A -- not NOVA's current dispatch path for this family.",),
            mask_support="Not applicable to NOVA's current dispatch for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-core-blend-source",),
        ),
    ),
    measurements=(
        "Core-local tonal occupancy in a fixed core ROI: p90/p95/p99, "
        "fraction near the display ceiling, local luminance headroom.",
        "Core-local structure preservation/readability: matched-ROI edge/"
        "feature contrast, radial or profile cuts through core features, "
        "difference images against a real unchanged control.",
        "Outside-mask preservation: faint-arm/nebulosity surface-brightness "
        "proxies, background median/noise texture, star FWHM/profile/color "
        "outside the core.",
        "Mask transition quality: inspection of the feather boundary for "
        "halos, tonal shelves, gradient discontinuity, or color seams.",
        "Color continuity: brightness-stratified and radial channel ratios "
        "around the core, understood as evidence of coherent reconstruction "
        "-- not proof of original core color accuracy.",
        "Both the upstream gate's relative-occupancy metrics and the "
        "function's own `core_coverage`/`skipped` state, recorded together.",
    ),
    acceptance_criteria=(
        "Still-present bright-core structure becomes easier to read while "
        "faint outer signal, background, star profiles outside the core, "
        "and color relationships outside the core remain materially "
        "unaffected, checked against a real copied-input baseline -- "
        "currently a manual copy, since the ontology's own declared `none` "
        "candidate does not yet dispatch (issue #669).",
        "No claim states or implies that genuinely clipped source "
        "information was reconstructed -- only that near-clipped structure "
        "was made easier to see.",
        "The clipped-hue refill's output color is described as a "
        "reconstruction from surrounding context, never as recovered "
        "original core chroma.",
        "Cluster-object results are never claimed as current production "
        "behavior -- clusters are skipped as a class.",
        "Both upstream-gate and function-level (`core_coverage`/`skipped`) "
        "provenance are recorded, especially when they disagree.",
        "Threshold-and-compression ladder results are labeled a strategy/"
        "intensity comparison, never a clean single-factor result.",
    ),
    sources=(
        P26_PACKET,
        NOVA_CORE_BLEND_SOURCE,
        M31_M42_EVIDENCE,
        NIST_BLOCKING_P26,
        NONE_CONTROL_DISPATCH_GAP,
    ),
)

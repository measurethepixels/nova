"""Handbook Phase 3, Group E2: Saturation and Hue-Selective Color Enhancement.

Synthesized from Phase-2 research packet P28 (2026-08-28, SeeStar-db
`83a59b783c0ec3125aa86c9563044d80b17aa094`). Re-verified against current
source: the `_NL_STEPS` set literally contains `color_saturation`, not the
ontology's own `color_sat` key, confirming the packet's step-name-mismatch
finding is still accurate -- no material drift found.

Corrected 2026-09-08 during Group E3 cross-checking: the `none` control this
article originally described as a real no-op baseline does not currently
dispatch (issue #669) -- `_run_variant()` only recognizes a copy-through via
an explicit `engine == "none"` key or `fn is None`, and the ontology's `none`
candidates set `fn: "none"` as a string, which falls through to a
nonexistent `seti_astro.none`. This affects every ontology family with a
declared `none` candidate, `color_sat` included.
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

P28_PACKET = EvidenceReference(
    reference_id="packet-p28",
    title="Phase-2 research packet P28, 2026-08-28",
    locator="packet:P28:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_SATURATION_SOURCE = EvidenceReference(
    reference_id="nova-saturation-source",
    title="NOVA ColorSaturation implementation, HS presets, and the color_sat/color_saturation step-name mismatch",
    locator=(
        "nas_server/pi_postprocess.js; nas_server/pixinsight.py:run_postprocess; "
        "nas_server/tool_params.py:compute_color_sat; nas_server/experiments.py _NL_STEPS"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NGC4565_NGC6334_EVIDENCE = EvidenceReference(
    reference_id="nova-ngc4565-ngc6334-saturation-evidence",
    title="NGC 4565 and NGC 6334 real runs: masked/hue-selective enhancement recommended over global boost",
    locator="critiques/20260722_073443_seestar_galaxy.md; critiques/20260722_074547_seestar_nebula.md",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

PIXINSIGHT_SATURATION_INTRO = EvidenceReference(
    reference_id="pixinsight-saturation-intro-p28",
    title="PixInsight introductory material: saturation transfer and selective hue examples",
    locator="https://pixinsight.com/astrophotocl/outreach/pixinsight_eccai_2006.pdf",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NIST_DOE_P28 = EvidenceReference(
    reference_id="nist-doe-p28",
    title="NIST/SEMATECH Engineering Statistics Handbook: design of experiments",
    locator="https://www.itl.nist.gov/div898/handbook/pri/pri.htm",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NONE_CONTROL_DISPATCH_GAP = EvidenceReference(
    reference_id="none-control-dispatch-gap",
    title="Ontology 'none' control candidates fail dispatch instead of running a true copy-through (issue #669)",
    locator="nas_server/experiments.py:_run_variant; SeeStar-db#669",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SATURATION = HandbookArticle(
    article_id="saturation",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.SATURATION,
    purpose=(
        "Controlled nonlinear chroma enhancement over color relationships "
        "that have already been established by calibration -- a rendering "
        "transformation applied to calibrated relationships, not additional "
        "calibration and not creation of new spectral information. Covers "
        "three related but non-equivalent strategies: global saturation "
        "enhancement, hue-selective enhancement, and masked enhancement."
    ),
    observable_symptoms=(
        "Color relationships are already established (post-SPCC or another "
        "calibration/palette path) but chromatic separation is subtle and "
        "hard to see -- target color, galaxy arm/bulge distinction, or "
        "narrowband palette separation reads as muted.",
    ),
    intended_output=(
        "Existing chromatic separation becomes easier to see, intended hue "
        "relationships are preserved unless a hue-selective strategy "
        "deliberately changes emphasis, background chroma noise does not "
        "become visible colored grain, and bright star cores/high-chroma "
        "regions are not driven into clipped/flat color."
    ),
    limits=(
        "Saturation increases channel separation/chroma while attempting to "
        "preserve hue family; it does not create new spectral information. "
        "If the original data, filter, calibration, stretch, or clipping "
        "already destroyed color contrast, saturation can only remap the "
        "color information still present -- it cannot recover it.",
        "Near-white/clipped star cores cannot regain color by saturation: "
        "if all channels are already clipped/equalized at a pixel, the "
        "channel-ratio information needed to reconstruct color is already "
        "gone at that pixel. Saturation may only color the surrounding "
        "unclipped profile/halo.",
        "The current `color_sat` Experiment Mode family is a single-method "
        "strength experiment (sat_feather/gentle/moderate/strong, all "
        "uniform, plus a declared `none` control) -- not a method experiment "
        "and not a complete strategy experiment. The declared `none` "
        "candidate does not currently dispatch as a real copy-through "
        "(issue #669: `fn: \"none\"` falls through to a nonexistent "
        "`seti_astro.none` rather than the dispatcher's `engine == \"none\"` "
        "copy-through path), so today's comparisons lack even that baseline "
        "until it is fixed. The runtime also contains "
        "materially different hue-selective strategies (galaxy preset, "
        "nebula preset, post-NarrowbandNormalization HOO restoration) that "
        "the normal `sat_*` variants do not test: `_run_variant()` forwards "
        "`color_sat_boost` but not `sat_preset`, so all four active `sat_*` "
        "variants execute the wrapper's uniform curve regardless of the "
        "dormant galaxy/nebula presets existing in code. A historical "
        "`sat_moderate` win cannot be cited as evidence for the galaxy "
        "preset, nebula preset, or HOO restore -- they are different "
        "strategies with different confounders.",
        "`_NL_STEPS` (the generic nonlinear adaptation/masking step set) "
        "contains the string `color_saturation`, while the ontology's real "
        "step key is `color_sat`. A normal Experiment Mode call using the "
        "actual ontology key therefore does not enter the generic nonlinear "
        "adaptation path at all -- no generic luminance-mask injection, and "
        "`tool_params.compute_color_sat()`'s measured boost is not used to "
        "scale the `sat_*` variants around a measured baseline. The "
        "ontology's fixed amounts (0.08/0.15/0.25/0.38) are the effective "
        "amounts today, not adapted values.",
        "`compute_color_sat()`'s boost estimate is a whole-frame-median "
        "channel-spread proxy, not per-pixel chroma in target ROIs and not "
        "a calibrated color-science saturation measurement. It can be "
        "dominated by sky background, a large emission nebula, a bright "
        "galaxy bulge, filter response, or residual cast -- useful as an "
        "engineering heuristic for \"the image may look globally muted,\" "
        "not as a direct saturation-quality measurement.",
    ),
    required_input_state=(
        "Nonlinear/stretched data, already through the intended broad "
        "color-calibration path (SPCC, other calibration, or an explicitly "
        "non-calibrated/palette path), free of major unresolved background "
        "color casts that saturation would simply amplify, in a stable "
        "tonal state since saturation interacts strongly with stretch and "
        "curves, with enough floating-point channel headroom that "
        "enhancement does not pile channels against output limits. Note: "
        "the ontology describes color_sat as applied after curves, but the "
        "actual runtime call order in `pixinsight.run_postprocess()` is "
        "`... HDRMT -> LHE -> ColorSat -> Curves` for a specific production "
        "run -- the higher-level autoprocess call sequence is authoritative "
        "for what actually happened, not the ontology sentence.",
    ),
    nova_action=(
        "The ontology exposes one uniform parameter, color_sat_boost "
        "(nominal range 0.10-0.60, default 0.25), dispatched through "
        "`run_postprocess(color_sat=True, color_sat_boost=...)` to "
        "PixInsight ColorSaturation. pi_postprocess.js supports three "
        "hue-vs-saturation-delta curve presets: uniform (same boost at all "
        "hue control points), galaxy (modest red, negative yellow/orange, "
        "stronger cyan/blue/violet), and nebula (strong Ha-red, OIII/cyan, "
        "magenta blend-zone boost) -- but `sat_preset` defaults to None and "
        "no production caller currently supplies \"galaxy\" or \"nebula\", "
        "so the JS falls through to uniform regardless of object type. "
        "Separately, after a successful PixInsight NarrowbandNormalization "
        "with nbn_hoo_boost > 0, NOVA applies its own hue-selective "
        "ColorSaturation curve (strong red/Ha boost, substantial OIII/cyan "
        "boost, low boost near yellow-green/blue star regions) specifically "
        "to restore HOO visual separation NBN can leave muted -- this is a "
        "strategy tied to narrowband normalization, not evidence for "
        "broadband OSC saturation choices."
    ),
    nova_evidence_ids=("nova-saturation-source", "nova-ngc4565-ngc6334-saturation-evidence", "none-control-dispatch-gap"),
    use_when=(
        "Color relationships are already calibrated/established and "
        "existing chromatic separation is genuinely muted, not merely a "
        "consequence of an upstream problem that should be fixed earlier.",
    ),
    skip_when=(
        "The data is monochrome or not yet mapped into a color composite/"
        "palette -- compute_color_sat() correctly returns disabled for "
        "non-color images.",
        "An unresolved background color cast exists upstream -- saturation "
        "would amplify the cast rather than fix it.",
        "The desired outcome is actually color recovery at clipped/near-"
        "clipped pixels -- that information is already gone and cannot be "
        "restored by this step.",
    ),
    scientific_and_aesthetic_notes=(
        "A fair comparison starts every candidate from the same parent FITS "
        "and exact upstream state (color-calibration/NBN state, stretch, "
        "curves, star-removal/recombination state, masking) and uses a real "
        "copied-input control -- currently a manual copy, since the "
        "ontology's own declared `none` candidate does not yet dispatch "
        "(issue #669). Hue-selective-vs-uniform comparisons must be labeled "
        "a strategy experiment, not a strength experiment, and the exact "
        "HS control points/preset should be recorded, not just a variant "
        "ID.",
        "More saturation is not more color accuracy -- a transform can make "
        "an incorrectly calibrated image more vividly incorrect. A low-"
        "saturation output is not automatically deficient either; some "
        "targets/data paths are genuinely dominated by one emission "
        "component or have weak recorded color contrast.",
        "Background chroma can masquerade as target improvement: if a "
        "global boost increases both nebula red and red sky noise, a "
        "preview may look more dramatic while objective target-vs-"
        "background separation actually gets worse.",
        "Real condition-specific evidence (NGC 4565, NGC 6334) recommends "
        "masked/hue-selective enhancement over a strong global boost when "
        "sky chroma noise is visible or the target is a large fraction of "
        "the frame -- single-condition execution evidence, not universal "
        "validation.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="uniform ColorSaturation dispatch; galaxy/nebula HS presets and compute_color_sat() dormant/unwired for the normal color_sat experiment",
            host="NOVA Python pipeline",
            instructions=(
                "Record the effective HS curve/preset (even though it is "
                "currently always uniform for the sat_* family), the "
                "applied amount, mask state, and upstream color-calibration/"
                "NBN state -- candidate ID alone does not capture treatment "
                "identity here.",
                "Do not generalize a sat_* strength-ladder result to the "
                "dormant galaxy/nebula hue-selective presets or the HOO "
                "restore path; none of them are exercised by the normal "
                "experiment today.",
                "Do not treat compute_color_sat()'s whole-frame-median "
                "proxy as a saturation-quality measurement.",
            ),
            controls_and_starting_ranges=(
                ("sat_feather / sat_gentle / sat_moderate / sat_strong", "uniform boost 0.08 / 0.15 / 0.25 / 0.38"),
                ("none", "declared control, currently non-dispatching -- see limits"),
                ("galaxy / nebula HS presets, NBN HOO restore", "source-confirmed capability, not currently an Experiment Mode candidate"),
            ),
            expected_result=(
                "Existing chromatic separation becomes easier to see with "
                "hue relationships preserved and no new colored background "
                "grain or clipped/flat star cores."
            ),
            failure_modes=(
                "Global color/noise exaggeration -- vivid target but "
                "colored/grainy sky.",
                "Garish or posterized stars from too much boost on clipped/"
                "near-clipped stellar profiles.",
                "Monochromatic emission staying monochromatic -- stronger "
                "global saturation mostly amplifies one already-dominant "
                "channel rather than producing real separation.",
            ),
            recovery=("Reduce amount; use a luminance/range/object mask; compare against the no-op control; diagnose the underlying channel content before forcing separation.",),
            mask_support="JS implementation supports an optional luminance mask via lum_masks.color_sat, but the color_sat/color_saturation step-name mismatch currently prevents the standard experiment from receiving the generic adapted mask.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-saturation-source", "nova-ngc4565-ngc6334-saturation-evidence"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="ColorSaturation (HS curve); project runtime pin 1.9.3 Lockhart",
            host="PixInsight ColorSaturation",
            instructions=(
                "ColorSaturation.HS is a saturation-delta-vs-hue-position "
                "curve, matching PixInsight's own documented saturation-"
                "transfer model.",
                "Distinguish CurvesTransformation's own saturation-curve "
                "capability from ColorSaturation -- both can manipulate "
                "saturation as a function of hue but are functionally "
                "similar, not automatically numerically equivalent.",
            ),
            controls_and_starting_ranges=(
                ("uniform / galaxy / nebula HS curve", "control-point presets; galaxy and nebula are NOVA-authored, not generic vendor presets"),
            ),
            expected_result="A hue-conditioned saturation delta applied via the HS control-point curve.",
            failure_modes=("Assuming display appearance alone (an exported preview) proves color-managed correctness rather than checking floating-point channel extrema.",),
            recovery=("Reduce boost; protect highlights; inspect the floating-point export rather than only a preview JPEG.",),
            mask_support="Supported for masked enhancement; not exercised by the current standard color_sat experiment due to the step-name mismatch.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("pixinsight-saturation-intro-p28", "nova-saturation-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="not the primary engine for this step; P28 does not materially depend on SASpro for the saturation transform itself",
            host="Seti Astro Suite Pro (context only for this family)",
            instructions=(
                "This process family's actual transform is PixInsight "
                "ColorSaturation, not a SASpro wrapper -- treat any SASpro "
                "saturation tooling as a separate, functionally similar "
                "alternative rather than the engine NOVA currently uses "
                "here.",
            ),
            controls_and_starting_ranges=(
                ("availability", "not currently NOVA's dispatch path for this step"),
            ),
            expected_result="Not applicable to NOVA's current color_sat dispatch; documented for completeness only.",
            failure_modes=("Assuming SASpro saturation tooling and NOVA's PixInsight-based color_sat produce equivalent output without a controlled comparison.",),
            recovery=("N/A -- not NOVA's current dispatch path.",),
            mask_support="Not applicable to NOVA's current dispatch for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-saturation-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native saturation/color tools; exact version not independently confirmed in this repo",
            host="Siril saturation/color tools",
            instructions=(
                "Treat as the same conceptual goal -- chroma enhancement "
                "over already-established color -- with different "
                "implementation than NOVA's PixInsight-based path.",
                "Apply the same before/after chroma/hue/headroom measurement "
                "discipline used for NOVA's own candidates.",
            ),
            controls_and_starting_ranges=(
                ("saturation tools", "typically applied post-stretch/post-calibration"),
            ),
            expected_result="Enhanced chroma via Siril's own saturation tooling.",
            failure_modes=("Treating a Siril saturation result as evidence about NOVA's PixInsight ColorSaturation candidates.",),
            recovery=("Prefer the no-op control if the result damages target or star color.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-saturation-source",),
        ),
    ),
    measurements=(
        "Per-pixel or ROI chroma/saturation distributions before/after, "
        "not whole-frame channel medians alone.",
        "Hue shift in selected target and stellar ROIs.",
        "Channel headroom/clipping fractions by channel, especially in "
        "bright stars and bright nebular cores.",
        "Brightness-stratified star-color behavior -- unsaturated faint/"
        "moderate stars checked separately from clipped/near-clipped bright "
        "stars.",
        "Background chroma/noise in carefully selected sky ROIs.",
        "Target-vs-background chroma separation.",
        "A no-op control difference image identifying where color changes "
        "actually occurred.",
    ),
    acceptance_criteria=(
        "Existing chromatic separation is easier to see with minimal "
        "unintended hue rotation, checked in matched ROIs against a real "
        "copied-input baseline -- currently a manual copy, not the "
        "ontology's own declared `none` candidate, which does not yet "
        "dispatch (issue #669).",
        "Background chroma/noise does not become visibly dominant colored "
        "grain.",
        "Bright/clipped star cores are not claimed to have regained color "
        "-- only their unclipped surrounding profile can legitimately show "
        "a saturation effect.",
        "Hue-selective or masked results are never generalized from a "
        "uniform-strength experiment result, and vice versa.",
        "Effective HS curve/preset, applied amount, and mask state are "
        "recorded -- not just a variant ID.",
    ),
    sources=(
        P28_PACKET,
        NOVA_SATURATION_SOURCE,
        NGC4565_NGC6334_EVIDENCE,
        PIXINSIGHT_SATURATION_INTRO,
        NIST_DOE_P28,
        NONE_CONTROL_DISPATCH_GAP,
    ),
)

"""Handbook Phase 3, Group E2: Curves and Tone Shaping.

Synthesized from Phase-2 research packet P27 (2026-08-28, SeeStar-db
`3bfe5b9ed2a9bbb0f2de4c38bfce274db5c5ec91`). Re-verified against current
source: the ontology candidate set, the `curves`/`color_saturation` step-name
split in `_NL_STEPS`, and the feather/s_mild clamp collapse all match the
packet's description exactly -- no material drift found.

Corrected 2026-09-08 during Group E3 cross-checking: the `none` control this
article originally described as a real no-op baseline does not currently
dispatch (issue #669) -- `_run_variant()` only recognizes a copy-through via
an explicit `engine == "none"` key or `fn is None`, and the ontology's `none`
candidates set `fn: "none"` as a string, which falls through to a
nonexistent `seti_astro.none`. This affects every ontology family with a
declared `none` candidate, `curves` included.
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

P27_PACKET = EvidenceReference(
    reference_id="packet-p27",
    title="Phase-2 research packet P27, 2026-08-28",
    locator="packet:P27:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_CURVES_SOURCE = EvidenceReference(
    reference_id="nova-curves-source",
    title="NOVA curves implementation, ontology candidates, and adaptation gap",
    locator=(
        "nas_server/seti_astro.py:curves; nas_server/tool_params.py:compute_curves; "
        "nas_server/processing_ontology.json curves; nas_server/experiments.py _adapt_nonlinear_variants"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NONE_CONTROL_DISPATCH_GAP = EvidenceReference(
    reference_id="none-control-dispatch-gap",
    title="Ontology 'none' control candidates fail dispatch instead of running a true copy-through (issue #669)",
    locator="nas_server/experiments.py:_run_variant; SeeStar-db#669",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

M12_M3_GLOBULAR_EVIDENCE = EvidenceReference(
    reference_id="nova-m12-m3-globular-curve-evidence",
    title="M12/M3 real runs: globular core-rolloff curve crushed halo tones or sky without core benefit",
    locator="critiques (M12 2026-07-15 workflow 1.22.1; M3 2026-07-15 workflow 1.23.0)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

PIXINSIGHT_LINEAR_NONLINEAR = EvidenceReference(
    reference_id="pixinsight-linear-nonlinear-p27",
    title="PixInsight staff: what is a linear versus non-linear image",
    locator="https://pixinsight.com/forum/index.php?threads/what-is-a-linear-verus-non-linear-image.428/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NIST_BLOCKING_P27 = EvidenceReference(
    reference_id="nist-blocking-p27",
    title="NIST/SEMATECH Engineering Statistics Handbook: randomized block designs",
    locator="https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

CURVES = HandbookArticle(
    article_id="curves",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.CURVES,
    purpose=(
        "Refine where already-visible tonal zones sit and how strongly they "
        "separate -- shadow/midtone/highlight placement -- after the main "
        "stretch has already made the faint signal visible. Curves are final "
        "or near-final tonal placement, not a substitute for the main stretch "
        "and not a way to recover clipped or never-captured signal."
    ),
    observable_symptoms=(
        "The broad stretch is already acceptable, but the sky/background "
        "sits slightly too high or too low, midrange structure needs modest "
        "additional separation, or a bright core needs a smoother shoulder.",
        "Near-white highlights lack headroom, or faint outer structure needs "
        "protection from an overly strong shadow pull.",
    ),
    intended_output=(
        "Intended tonal zones move in the intended direction while faint "
        "astrophysical structure that should not be sacrificed is preserved, "
        "with no new black/white clipping, no unintended non-monotonic tonal "
        "behavior, and color relationships preserved when the curve is "
        "intended as a linked/luminance-like operation."
    ),
    limits=(
        "A tone curve is a pointwise nonlinear mapping (output = f(input)); "
        "it can redistribute recorded levels but cannot recover signal that "
        "was clipped, saturated, quantized away, or never captured. A lower "
        "p99 after a highlight rolloff demonstrates compression, not "
        "recovered highlight information.",
        "NOVA's own data-driven strategy (tool_params.compute_curves(), "
        "deriving bespoke monotonic control points from measured sky level "
        "and percentiles) is computed inside Experiment Mode's adaptation "
        "path but its custom points are not currently propagated into the "
        "curve variants -- the generic adaptation loop only looks for a "
        "primary parameter called `amount`, and compute_curves() returns no "
        "`amount`. The current Experiment Mode curve run should not be "
        "described as testing NOVA's bespoke data-driven curve; that curve "
        "is computed but effectively discarded by this adaptation path.",
        "Because compute_curves() supplies no `amount`, adaptation clamps "
        "every candidate's effective amount to [0.2, 0.8] using the nominal "
        "0.5 default and each ontology ratio. This collapses `feather` "
        "(nominal 0.12) and `s_mild` (nominal 0.20) into the identical "
        "effective amount 0.20 with the same shape -- they are the same "
        "effective treatment and must not be counted as two independent "
        "candidates or two pieces of evidence.",
        "The current SASpro-vs-PixInsight comparison is confounded by mask "
        "asymmetry: the luminance-mask map uses the key `curves_pi`, while "
        "the ontology's PixInsight candidate function name is `pi_curves`. "
        "Mask injection checks the exact function-name key, so SASpro "
        "`curves` candidates receive a computed midtone mask while "
        "`pi_curves` candidates do not, through the current default "
        "adaptation path. The current comparison is closer to masked-SASpro-"
        "strategy vs. unmasked-PixInsight-strategy than a clean engine "
        "comparison.",
        "SASpro preprocessing is part of the effective treatment: NOVA's "
        "shared `_load_fits()` performs a global min/max normalization to "
        "[0,1] before the LUT is built, so a cross-engine comparison assuming "
        "bit-identical absolute sample values between engines is unsafe.",
        "The `L` (luminance) channel mode explicitly quantizes through 8-bit "
        "OpenCV Lab before returning to float -- it is a distinct "
        "implementation strategy, not a high-precision floating-point "
        "luminance-curve equivalent, and should not be presented as one.",
        "The two globular-specific PixInsight presets (`globular_balanced`, "
        "`globular_core_rolloff`) carry real, contradictory execution "
        "history: positive calibration comments from C80/Omega Centauri and "
        "M13, but later real runs (M12, M3) where the core-rolloff curve "
        "crushed halo/outer tones or sky background without the intended "
        "core benefit -- in the M12 case, an inversion guard correctly "
        "rejected the curve before it shipped. Two examples are not "
        "statistical validation, but they mean measured input state should "
        "outrank the object-class label alone for these presets.",
        "The ontology's declared `none` candidate is not currently a working "
        "no-op control: the dispatcher only recognizes a copy-through when "
        "`engine` is explicitly `\"none\"` or `fn` is Python `None`, but the "
        "candidate sets `fn: \"none\"` as a literal string, so it falls "
        "through to `seti_astro.none`, which does not exist, and the run "
        "returns `ok: False`. Any comparison presented as validated against "
        "a real unchanged baseline is currently unsupported until this "
        "dispatch gap is fixed (tracked separately, not a content change).",
    ),
    required_input_state=(
        "Nonlinear/stretched data with the upstream stretch method and "
        "effective parameters known, the relevant branch state known "
        "(current ontology expects the starless stretched branch before star "
        "recombination for the ordinary deep-sky path), and background/"
        "gradient/color-calibration problems already resolved rather than "
        "being disguised with tone shaping. Clipping/headroom and histogram "
        "percentiles should be measured before curves, and any luminance "
        "mask used should be recorded. A fair comparison requires the exact "
        "same parent nonlinear FITS for every candidate.",
    ),
    nova_action=(
        "seti_astro.curves() has two materially different execution paths. "
        "The SASpro preset/LUT path imports SASpro's curves_preset LUT "
        "machinery, exposing shape (linear/s_mild/s_med/s_strong/"
        "lift_shadows/crush_shadows/fade_blacks/rolloff_highlights), amount, "
        "and channel (all/R/G/B/L); it builds normalized control points into "
        "a 65,536-entry LUT applied to the (already globally min/max-"
        "normalized) data. The PixInsight path routes pi_curves/curves_pi to "
        "CurvesTransformation.K from named built-in shapes (including the "
        "two NOVA-authored globular strategies) or explicit custom control "
        "points. Separately, tool_params.compute_curves() derives measured "
        "control points from sigma-clipped sky background, percentiles "
        "(p80/p90/p95/p99/p99.5/p99.9-like), and object type, with "
        "object-specific sky-anchor priors and shadow-pull gentleness "
        "(gentler for galaxies than clusters) -- but as noted in limits, "
        "this computed strategy is not currently propagated into Experiment "
        "Mode's generic curve variants."
    ),
    nova_evidence_ids=("nova-curves-source", "nova-m12-m3-globular-curve-evidence", "none-control-dispatch-gap"),
    use_when=(
        "The broad stretch is already acceptable but one or more tonal "
        "zones need refinement -- sky level, faint-structure protection, "
        "midrange separation, highlight headroom, or object-specific core/"
        "halo balance.",
    ),
    skip_when=(
        "The image already has acceptable tonal placement.",
        "The actual problem is a residual gradient, incorrect color "
        "calibration, noise, star halos, clipped acquisition data, or a "
        "fundamentally wrong initial stretch -- curves cannot fix any of "
        "these and should not be used to disguise them.",
    ),
    scientific_and_aesthetic_notes=(
        "There is no single physically correct finished histogram for an "
        "astrophotograph. Curves have measurable constraints and failure "
        "conditions, but much of the final tonal preference remains "
        "aesthetic -- label perceptual judgments (flat vs. harsh, preferred "
        "sky darkness, visual emphasis) as rendering preference, not "
        "physical validation.",
        "A rigorous comparison should separate distinct questions -- engine "
        "(match transfer function and mask as closely as possible), "
        "strength (same engine/shape/mask/parent, vary amount only), shape "
        "(same engine and strength/mask, vary shape only), mask strategy "
        "(same transfer function, full-frame vs. fixed mask), object-"
        "specific strategy (deliberately different curves/masks, classified "
        "as a strategy comparison), and adaptive-vs-fixed (the measured "
        "custom strategy against fixed presets and a no-op, once it is "
        "actually executed) -- rather than mixing all of them into one "
        "run's evidence.",
        "Linked RGB curves (channel=\"all\") apply the same function to R, "
        "G, and B, but f(R)/f(G) != R/G in general for nonlinear f -- linked "
        "curves can still change saturation/chroma relationships as a "
        "function of brightness and should not be described as exact color "
        "preservation merely because the same function was used per channel.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="SASpro LUT path + PixInsight CurvesTransformation dispatch; measured compute_curves() strategy not yet propagated into Experiment Mode",
            host="NOVA Python pipeline",
            instructions=(
                "Record the effective shape/control points, effective "
                "amount, channel mode, and whether a luminance mask actually "
                "applied -- candidate ID alone is not sufficient provenance, "
                "especially since feather/s_mild currently collapse to the "
                "same effective treatment.",
                "Do not compare SASpro and PixInsight curve candidates as a "
                "clean engine test until the curves_pi/pi_curves mask-key "
                "mismatch is accounted for -- today one side is masked and "
                "the other is not.",
                "Do not cite a historical curve-family win as validating "
                "NOVA's measured/adaptive strategy specifically; that "
                "strategy is not what the generic Experiment Mode variants "
                "currently execute.",
            ),
            controls_and_starting_ranges=(
                ("s_mild / s_med / s_strong", "SASpro LUT shapes, nominal amount 0.20/0.28/0.38"),
                ("lift_shadows / rolloff_highlights", "alternative tonal strategies, nominal amount 0.18/0.15"),
                ("pi_s_mild_globular / pi_globular_core_rolloff", "NOVA-authored PixInsight object-specific strategies; core_rolloff has contradictory real-run evidence"),
                ("none", "declared control, currently non-dispatching -- see limits"),
            ),
            expected_result=(
                "Intended tonal zones move as intended with no new clipping, "
                "no unintended inversion, and protected regions stable."
            ),
            failure_modes=(
                "Crushed shadows / faint-signal loss from an over-aggressive "
                "shadow pull.",
                "Highlight inversion where a nominal rolloff brightens the "
                "core while lower tones darken (real M12 evidence).",
                "Sky crushed without core benefit (real M3 evidence).",
            ),
            recovery=("Revert to the parent input; derive the shoulder from actual measured p95/p99; use a mask restricted to the intended region; weaken strength.",),
            mask_support="SASpro path supports a computed luminance midtone mask via the generic nonlinear adaptation; PixInsight path does not currently receive it due to the curves_pi/pi_curves key mismatch.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-curves-source", "nova-m12-m3-globular-curve-evidence"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="CurvesTransformation.K; project runtime pin 1.9.3 Lockhart",
            host="PixInsight CurvesTransformation",
            instructions=(
                "A non-straight CurvesTransformation makes a previously "
                "linear image nonlinear -- apply after the primary stretch, "
                "not among physically linear operations.",
                "Treat the two NOVA-authored globular presets as project "
                "strategies executed by PixInsight, not vendor-endorsed "
                "recommendations, and check measured input state before "
                "trusting the object-class label alone given the real "
                "contradictory M12/M3 evidence.",
            ),
            controls_and_starting_ranges=(
                ("named shapes", "mild/medium/strong S curves; highlight rolloff; shadow lift; globular_balanced; globular_core_rolloff"),
            ),
            expected_result="A monotonic tonal remapping via CurvesTransformation matching the requested shape or custom control points.",
            failure_modes=("Inversion/wrong-knee behavior from a preset calibrated on a different input histogram.",),
            recovery=("Revert; use monotonic measured custom points instead of a fixed object-class preset.",),
            mask_support="Supported natively; current default Experiment Mode dispatch does not inject the generic adapted mask for pi_curves due to the key-name mismatch.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("pixinsight-linear-nonlinear-p27", "nova-curves-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="curves_preset LUT machinery; project pin 1.18.0",
            host="Seti Astro Suite Pro curves LUT",
            instructions=(
                "Remember NOVA's shared FITS loader performs a global min/"
                "max normalization to [0,1] before this LUT is built -- that "
                "preprocessing is part of the effective treatment.",
                "Treat channel=\"L\" as a distinct, lower-precision strategy "
                "(8-bit OpenCV Lab quantization), not a high-bit-depth "
                "luminance-curve equivalent.",
            ),
            controls_and_starting_ranges=(
                ("shape", "linear / s_mild / s_med / s_strong / lift_shadows / crush_shadows / fade_blacks / rolloff_highlights"),
                ("channel", "all (linked) / R / G / B / L (8-bit Lab quantized)"),
            ),
            expected_result="A 65,536-entry LUT tonal remapping applied to the normalized data.",
            failure_modes=("Treating channel=\"L\" results as equivalent to a high-precision floating-point luminance curve.",),
            recovery=("Use linked (\"all\") or a real luminance-preserving strategy where 8-bit quantization loss matters.",),
            mask_support="Supported through the generic nonlinear adaptation mask when the function name matches the mask-map key.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-curves-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native curves/histogram transformation; exact version not independently confirmed in this repo",
            host="Siril curves/histogram tools",
            instructions=(
                "Treat as the same conceptual family -- a pointwise "
                "brightness remapping -- with different interpolation/"
                "implementation than NOVA's SASpro or PixInsight paths.",
                "Apply the same before/after measurement discipline "
                "(percentiles, clipping fraction, protected-ROI checks) "
                "used for NOVA's own candidates.",
            ),
            controls_and_starting_ranges=(
                ("curve editor", "manual control-point placement, typically post-stretch"),
            ),
            expected_result="A monotonic tonal remapping via Siril's own curve/histogram tools.",
            failure_modes=("Treating a Siril curves result as evidence about NOVA's SASpro/PixInsight candidates.",),
            recovery=("Prefer the no-op control if the result damages the target.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-curves-source",),
        ),
    ),
    measurements=(
        "Input/output control points or sampled transfer function, and its "
        "monotonicity.",
        "Background/sky median movement in fixed sky ROIs.",
        "Percentile movement (p05/p50/p80/p90/p95/p99/p99.9) on the exact "
        "same parent.",
        "Fraction at/near numerical 0 and 1.",
        "Local target-to-background contrast and radial/line profiles "
        "through a core, halo, arm, or filament, in fixed ROIs.",
        "Channel-specific clipping and color-ratio shifts.",
        "Difference images and protected-region residuals.",
        "Effective mask coverage and transition behavior, when a mask is "
        "used.",
    ),
    acceptance_criteria=(
        "Intended tonal zones move in the intended direction without new "
        "black/white clipping or unintended non-monotonic behavior.",
        "Faint astrophysical structure not meant to be sacrificed is "
        "preserved, checked against a copied-input baseline on the same "
        "parent -- currently a manual copy rather than the ontology's own "
        "declared `none` candidate, which does not yet dispatch.",
        "Effective shape/control points, effective amount, channel mode, "
        "and mask state are recorded -- candidate ID alone is not "
        "sufficient, given the feather/s_mild collapse and the SASpro/"
        "PixInsight mask asymmetry.",
        "A lower highlight percentile is never presented as recovered "
        "information -- only as compression.",
        "Object-specific presets (especially the globular strategies) are "
        "checked against measured input state, not applied on class label "
        "alone, given the real contradictory M12/M3/C80 evidence.",
    ),
    sources=(
        P27_PACKET,
        NOVA_CURVES_SOURCE,
        M12_M3_GLOBULAR_EVIDENCE,
        PIXINSIGHT_LINEAR_NONLINEAR,
        NIST_BLOCKING_P27,
        NONE_CONTROL_DISPATCH_GAP,
    ),
)

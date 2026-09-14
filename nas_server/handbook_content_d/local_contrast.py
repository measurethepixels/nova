"""Handbook Phase 3, Group E3: Local Contrast Enhancement (CLAHE / LHE).

Synthesized from Phase-2 research packet P24 (2026-08-28, SeeStar-db
`3a54b06f3951a15f3cd3fd2a5ab33b7db06a3023`). Re-verified against current
source 2026-09-08: `seti_astro.clahe()`'s docstring/grid-semantics drift,
`tool_params.compute_clahe()`'s tile_size selection, and the PixInsight LHE
generic-dispatch radius/slope gap in `experiments.py` all match the packet's
description exactly -- no material drift found.

One packet claim was corrected during this re-verification: P24 said no
no-op/control candidate existed for the `clahe` family. The ontology does
declare one (`{"id": "none", "fn": "none"}`), but it does not currently
dispatch as a real copy-through (issue #669) -- so the corrected finding is
that a control is declared but non-functional, not that none exists.
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

P24_PACKET = EvidenceReference(
    reference_id="packet-p24",
    title="Phase-2 research packet P24, 2026-08-28",
    locator="packet:P24:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_CLAHE_SOURCE = EvidenceReference(
    reference_id="nova-clahe-source",
    title="NOVA CLAHE/LHE implementation, ontology candidates, and the LHE dispatch gap",
    locator=(
        "nas_server/seti_astro.py:clahe; nas_server/tool_params.py:compute_clahe,compute_lhe; "
        "nas_server/processing_ontology.json clahe; nas_server/experiments.py _run_variant,_adapt_nonlinear_variants"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SASPRO_CLAHE_UPSTREAM = EvidenceReference(
    reference_id="saspro-clahe-upstream-p24",
    title="Seti Astro Suite Pro current upstream CLAHE source (version-bound, not the pinned 1.18.0 install)",
    locator="setiastro/setiastrosuitepro src/setiastro/saspro/clahe.py, ref c2c941b88335c82cc42ced4a2016c43261610918",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

ZUIDERVELD_CLAHE = EvidenceReference(
    reference_id="zuiderveld-clahe",
    title="Karel Zuiderveld, Contrast Limited Adaptive Histogram Equalization, Graphics Gems IV (1994)",
    locator="Graphics Gems IV, 1994",
    provenance=ProvenanceLabel.PRIMARY_RESEARCH_SUPPORTED,
)

NONE_CONTROL_DISPATCH_GAP = EvidenceReference(
    reference_id="none-control-dispatch-gap-clahe",
    title="Ontology 'none' control candidates fail dispatch instead of running a true copy-through (issue #669)",
    locator="nas_server/experiments.py:_run_variant; SeeStar-db#669",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

LOCAL_CONTRAST = HandbookArticle(
    article_id="local-contrast",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.LOCAL_CONTRAST,
    purpose=(
        "Increase tonal separation between nearby structures so real "
        "intermediate-scale detail that survived the main stretch, but "
        "remains visually compressed against its immediate surroundings, "
        "becomes easier to see. This is a finishing operation over already-"
        "captured tonal relationships, not a way to restore blur (deconvolution/"
        "sharpening) or recover clipped/never-captured signal."
    ),
    observable_symptoms=(
        "Galaxy arms/dust lanes, nebular filaments/shells, or compact "
        "planetary-nebula structure survived the stretch but read as flat "
        "or muted relative to their immediate neighborhood.",
    ),
    intended_output=(
        "Improved visibility of real local-scale structure while preserving "
        "background continuity, faint diffuse signal, star profiles/halos, "
        "highlight headroom, and color relationships. A no-op is a valid "
        "outcome -- local contrast is enhancement, not required calibration."
    ),
    limits=(
        "More microcontrast is not recovered spatial resolution. Entropy, "
        "high-frequency power, generic sharpness, global variance, or a "
        "visually crunchier result can all improve while noise, ringing, "
        "seams, or interpolation artifacts simultaneously worsen -- none of "
        "these is a standalone success metric.",
        "NOVA's `seti_astro.clahe()` docstring says `tile_size` is a tile "
        "size in pixels and that smaller values mean finer adaptation, but "
        "the wrapper passes `(tile_size, tile_size)` straight through as "
        "SASpro/OpenCV `tileGridSize` -- which is the number of tiles in the "
        "grid, not the pixel width of a tile. For a fixed image, (8,8) means "
        "more/smaller tiles and (4,4) means fewer/larger tiles, so "
        "`tool_params.compute_clahe()` choosing `tile_size=4` for supposedly "
        "\"finer\" galaxy detail is directionally opposite to current "
        "upstream OpenCV grid semantics. This is recorded as likely comment/"
        "adaptation semantic drift, version-bound until the exact pinned "
        "SASpro 1.18.0 source (not just current upstream) is checked.",
        "`_load_fits()` performs a global min/max normalization to [0,1] "
        "before CLAHE runs, so a NOVA CLAHE result is not simply the "
        "original amplitude scale plus local equalization -- that "
        "preprocessing is part of the effective treatment and confounds a "
        "direct CLAHE-vs-PixInsight-LHE comparison unless controlled.",
        "In the reviewed generic PixInsight dispatch (`experiments.py`, "
        "`_run_variant()`'s `fn_name == \"lhe\"` branch), only `lhe_amount` "
        "is forwarded into the PI call -- `lhe_kernel_r` and "
        "`lhe_slope_limit` are not. Separately, `_adapt_nonlinear_variants()` "
        "does compute and store adapted `lhe_kernel_r`/`lhe_slope_limit` into "
        "the candidate's own params dict. So the adapted radius/slope values "
        "are calculated but never actually reach the PixInsight call -- only "
        "the amount does. An LHE experiment that appears to vary radius or "
        "slope may not be exercising those settings at all.",
        "Confirmed CLAHE candidates (`clahe_whisper`, `clahe_mild`, "
        "`clahe_standard`, `clahe_fine`) are parameter/scale probes within "
        "one CLAHE engine, not four independent algorithms. The ontology "
        "does declare a `none` control alongside them, but it does not "
        "currently dispatch as a real copy-through -- `_run_variant()` only "
        "recognizes a no-op via an explicit `engine == \"none\"` key or "
        "`fn is None`, and the ontology's candidate sets `fn: \"none\"` as a "
        "string, which falls through to a nonexistent `seti_astro.none` and "
        "returns `ok: False` (issue #669). Comparisons cannot currently rely "
        "on this declared baseline until that dispatch gap is fixed.",
        "Current upstream SASpro CLAHE source converts through 8-bit CIELAB "
        "and applies OpenCV CLAHE to the L channel before converting back -- "
        "an 8-bit internal precision path, not a high-bit-depth luminance-"
        "curve equivalent. This is version-bound context from current "
        "upstream source, not confirmed against the exact pinned 1.18.0 "
        "install.",
    ),
    required_input_state=(
        "A geometrically stable, globally balanced nonlinear image with "
        "major gradients/color problems already handled, noise sufficiently "
        "controlled that the operation will not primarily enhance the noise "
        "floor, and star/starless state, masks, and prior sharpening known. "
        "`seti_astro.clahe()` can technically run on linear or stretched "
        "data, but current NOVA treatment places CLAHE among nonlinear "
        "adaptive finishing steps -- implementation capability should not be "
        "read as a workflow recommendation for linear use.",
    ),
    nova_action=(
        "`seti_astro.clahe(clip_limit=2.0, tile_size=8)` loads the FITS "
        "through the shared normalizing loader, calls SASpro's "
        "`apply_clahe(..., tile_grid_size=(tile_size, tile_size))`, clips to "
        "[0,1], and saves. `tool_params.compute_clahe()` derives `clip_limit` "
        "from a P02 SNR-like proxy (clamped 1.0-5.0, capped lower for "
        "nebulae and boosted slightly for galaxies) and picks `tile_size=4` "
        "for sharp galaxies, else 8 -- these are engineering heuristics, not "
        "calibrated physical laws. PixInsight LocalHistogramEqualization "
        "(LHE) is the method alternative: `tool_params.compute_lhe()` picks "
        "kernel radius from roughly 1/12 of the shorter image dimension "
        "(clamped 32-128px), and slope limit by object type (1.5 nebula / "
        "2.5 galaxy / 2.0 other), with amount fixed at 0.5. The PI path can "
        "also sample pre-LHE background for a post-operation anchor "
        "correction."
    ),
    nova_evidence_ids=("nova-clahe-source", "none-control-dispatch-gap-clahe"),
    use_when=(
        "Meaningful structure survived the stretch but remains visually "
        "compressed relative to its immediate surroundings -- galaxy arms/"
        "dust lanes, nebular filaments/shells, compact planetary-nebula "
        "structure, or high-SNR starless detail.",
    ),
    skip_when=(
        "The actual problem is a residual gradient, the data are very "
        "noisy, stars dominate the field, smooth faint signal matters more "
        "than microcontrast, a previous sharpening/local-contrast pass "
        "already produced crunchy texture, mosaic seams/resampling "
        "artifacts are present, or bright-core dynamic range -- not local "
        "tonal separation -- is the actual problem.",
    ),
    scientific_and_aesthetic_notes=(
        "A method comparison (CLAHE vs. PixInsight LHE) must predefine "
        "approximate feature scale and effective strength, verify actual "
        "grid semantics rather than trusting current comments, and evaluate "
        "on untouched test ROIs. If each engine is independently auto-tuned "
        "to its own best-looking result, that is a strategy comparison, not "
        "a controlled engine comparison.",
        "A within-CLAHE clip-limit probe should hold parent FITS, "
        "normalization, grid semantics, mask, object metadata, and preview "
        "transform fixed and persist the effective post-adaptation clip "
        "limit and grid rather than just the candidate ID.",
        "Human/AI perceptual assessment can legitimately judge whether "
        "structure reads as easier to see or looks artificial. It cannot "
        "prove recovered physical resolution or the astrophysical reality "
        "of newly visible faint texture.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="SASpro CLAHE wrapper + PixInsight LHE dispatch; adaptive tile/clip/radius/slope heuristics",
            host="NOVA Python pipeline",
            instructions=(
                "Record effective clip limit, effective grid (both the "
                "tile-count tuple and the resulting approximate tile pixel "
                "size once grid semantics are verified), mask state, and "
                "input normalization -- candidate ID alone is not sufficient "
                "provenance.",
                "Do not present an LHE radius/slope experiment as testing "
                "those parameters until the generic-dispatch forwarding gap "
                "is fixed or the effective PI call arguments are directly "
                "confirmed.",
                "Do not rely on the ontology's declared `none` CLAHE control "
                "as a working baseline until issue #669 is fixed -- use a "
                "manual copied-input comparison in the meantime.",
            ),
            controls_and_starting_ranges=(
                ("clahe_whisper / clahe_mild / clahe_standard", "clip 1.0 / 1.5 / 2.0, tile grid (8,8) -- parameter probes"),
                ("clahe_fine", "clip 1.5, tile grid (4,4) -- fewer/larger tiles per current OpenCV grid semantics, despite the wrapper's \"finer\" comment"),
                ("none", "declared control, currently non-dispatching -- see limits"),
            ),
            expected_result=(
                "Improved visibility of real intermediate-scale structure "
                "with background continuity, star profiles, and highlight "
                "headroom preserved."
            ),
            failure_modes=(
                "Noise crunch / false texture from excessive clip limit or "
                "an inappropriately fine grid scale.",
                "Star halos or rings from local treatment near point "
                "sources.",
                "Artificial filamentation that looks convincing but is not "
                "confirmed against a difference image or noise-only ROI.",
            ),
            recovery=("Reduce clip limit; use a broader tile scale; mask low-SNR regions; denoise first; or skip.",),
            mask_support="Not currently masked through the generic nonlinear adaptation path in the reviewed source; PI LHE can use a luminance mask when explicitly configured.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-clahe-source", "none-control-dispatch-gap-clahe"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="LocalHistogramEqualization (LHE); project runtime pin 1.9.3 Lockhart",
            host="PixInsight LocalHistogramEqualization",
            instructions=(
                "LHE is a functional alternative to CLAHE, not an exact "
                "algorithmic equivalent -- different runtime model (moving/"
                "circular neighborhood vs. tiled grid) and different "
                "control semantics (radius, slope limit, amount).",
                "Verify the effective radius/slope actually used before "
                "citing an LHE experiment result -- current generic "
                "dispatch may only be forwarding amount.",
            ),
            controls_and_starting_ranges=(
                ("radius", "~1/12 of shorter image dimension, clamped 32-128px -- a feature-scale parameter, not a fixed universal default"),
                ("slope limit", "1.5 nebula / 2.5 galaxy / 2.0 other -- NOVA heuristics, plausible direction, not validation thresholds"),
                ("amount", "0.5 default"),
            ),
            expected_result="A local histogram remapping via a moving/circular neighborhood, matching the effective radius/slope/amount actually forwarded.",
            failure_modes=("Assuming a candidate's declared radius/slope was the value actually executed.",),
            recovery=("Confirm effective PI call arguments directly (e.g. via logged JS parameters) before drawing conclusions; otherwise treat only amount as controlled.",),
            mask_support="Supported natively; NOVA's PI path can also sample pre-LHE background for a corrective post-anchor.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("nova-clahe-source",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="CLAHE via OpenCV tileGridSize; project pin 1.18.0",
            host="Seti Astro Suite Pro CLAHE",
            instructions=(
                "Current upstream source converts through 8-bit CIELAB and "
                "applies OpenCV CLAHE to the L channel, retaining chroma -- "
                "treat this as version-bound context, not confirmed proof "
                "of the pinned 1.18.0 install's exact numeric path.",
                "`tileGridSize` is a tile COUNT, not a pixel size -- verify "
                "before describing a smaller `tile_size` argument as \"finer\".",
            ),
            controls_and_starting_ranges=(
                ("clip_limit", "roughly 1.0 weak probe, 1.5-2.5 sensible moderate range in NOVA's current parameterization; cross-library numeric equality is not guaranteed"),
                ("tileGridSize", "grid tile COUNT, e.g. (8,8) or (4,4) -- fewer tiles is coarser, more tiles is finer"),
            ),
            expected_result="Local histogram-equalized luminance with clipped amplification, retained chroma, converted back to RGB.",
            failure_modes=("Precision/posterization risk from the 8-bit internal CLAHE path (version-bound, unverified against the pinned runtime).",),
            recovery=("Verify pinned 1.18.0 source before presenting precision/grid details as production-runtime fact.",),
            mask_support="Not exercised by the current default seti_astro.clahe() wrapper call.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("saspro-clahe-upstream-p24", "nova-clahe-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native local-contrast/AHE-family tools; exact version not independently confirmed in this repo",
            host="Siril local-contrast tools",
            instructions=(
                "Treat as the same conceptual family -- local tonal "
                "remapping -- with different implementation than NOVA's "
                "SASpro CLAHE or PixInsight LHE paths.",
                "Apply the same target-ROI contrast + noise + star-profile "
                "measurement discipline used for NOVA's own candidates.",
            ),
            controls_and_starting_ranges=(
                ("local contrast / AHE tools", "typically applied post-stretch"),
            ),
            expected_result="A local tonal remapping via Siril's own local-contrast tooling.",
            failure_modes=("Treating a Siril result as evidence about NOVA's SASpro/PixInsight candidates.",),
            recovery=("Prefer a manually copied unchanged baseline if the result damages the target.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-clahe-source",),
        ),
    ),
    measurements=(
        "Matched target-ROI local contrast on known structures (arm/inter-"
        "arm, filament/adjacent nebula, shell edge/interior).",
        "Robust noise change in matched low-signal background ROIs.",
        "Background/seam continuity at multiple spatial scales.",
        "Unsaturated-star radial profiles, halo/core ratio, and headroom.",
        "Channel-wise near-black/near-white pile-up and high percentiles.",
        "Before/after difference images or multiscale residuals showing "
        "where the change actually occurred.",
    ),
    acceptance_criteria=(
        "Structure that survived the stretch becomes easier to read "
        "without introducing noise crunch, halos, false filamentation, or "
        "seam amplification.",
        "Effective clip limit and grid (not just candidate ID) are "
        "recorded, with grid semantics verified rather than assumed.",
        "Entropy, high-frequency power, generic sharpness, or global "
        "standard deviation are never presented as standalone proof of "
        "benefit.",
        "A CLAHE-vs-LHE result is labeled a strategy comparison unless "
        "normalization, feature scale, and mask state were actually "
        "controlled.",
        "No claim relies on the ontology's declared `none` control until "
        "issue #669 confirms it dispatches as a real copy-through.",
    ),
    sources=(
        P24_PACKET,
        NOVA_CLAHE_SOURCE,
        SASPRO_CLAHE_UPSTREAM,
        ZUIDERVELD_CLAHE,
        NONE_CONTROL_DISPATCH_GAP,
    ),
)

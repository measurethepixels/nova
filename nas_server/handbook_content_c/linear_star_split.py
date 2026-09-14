"""Handbook Phase 3, Group D2b: Linear Star Split, Star-Layer Stretch, and
Recombination.

Synthesized from Phase-2 research packet P18 (2026-08-28, SeeStar-db
`3a54b06f...`) for the split stage. The star-stretch and recombination
stages were originally documented (2026-09-03, PR #630) using only direct
ontology/runtime inspection, since no dedicated P19/P20 research existed --
tracked as issue #631.

Revised 2026-09-09: #631's research gap is closed. A real P19/P20-depth
research pass (posted directly to #631, 2026-09-08) covers SetiAstro Star
Stretch v2.6's published transfer function, NOVA's deliberate data-driven
deviation from it, screen-recombination math and its invariants, a 10-case
failure gallery, and fair-comparison design. This revision folds that
research in and re-verifies both stages against current source: the
`star_stretch()` fallback formula bug the research surfaced (#658) is now
fixed (PR #661, merged 2026-09-08) and matches the vendor formula exactly;
`combine_stars_screen()` also has a `_floor_star_layer()` inter-star
black-point mitigation not previously documented here, which is real,
current, source-confirmed behavior addressing the research's own
"residual background in stars layer" failure-gallery item -- not something
this revision is proposing, but something already live that the prior
version of this article omitted.
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

P18_PACKET = EvidenceReference(
    reference_id="packet-p18",
    title="Phase-2 research packet P18, 2026-08-28",
    locator="packet:P18:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_STAR_SPLIT_SOURCE = EvidenceReference(
    reference_id="nova-star-split-source",
    title="NOVA linear star split, star-stretch, and recombination implementation",
    locator=(
        "nas_server/seti_astro.py:sxt_star_split,remove_stars_split,"
        "star_stretch,combine_stars_screen; nas_server/auto_process.py "
        "step_type=='star_split' branch; nas_server/processing_ontology.json "
        "remove_stars_linear/stretch_stars/combine_stars_screen"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

RC_ASTRO_SXT_USAGE_NOTES = EvidenceReference(
    reference_id="rc-astro-sxt-usage-notes",
    title="RC-Astro StarXTerminator Usage Notes",
    locator="https://www.rc-astro.com/starxterminator-usage-notes/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

RC_ASTRO_SXT_MASK_HANDLING = EvidenceReference(
    reference_id="rc-astro-sxt-mask-handling",
    title="RC-Astro StarXTerminator 2.2.0 — Proper Mask Handling",
    locator="https://www.rc-astro.com/starxterminator-2-2-0-proper-mask-handling/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SASPRO_DARKSTAR_SOURCE = EvidenceReference(
    reference_id="saspro-darkstar-source",
    title="Seti Astro Suite Pro DarkStar / remove_stars.py",
    locator="https://www.setiastro.com/cosmic-clarity",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

P19_P20_RESEARCH = EvidenceReference(
    reference_id="p19-p20-research",
    title="P19/P20-depth research pass: star-layer stretch (SetiAstro Star Stretch v2.6 transfer function) and screen recombination math, invariants, failure gallery, fair-comparison design",
    locator="SeeStar-db#631 (comment, 2026-09-08)",
    # The #631 packet combines vendor material, NOVA source/runtime
    # inspection, historical critique evidence, math/inference, and
    # failure-gallery synthesis -- it is not itself a vendor document, so it
    # cannot inherit VENDOR_DOCUMENTED from one of its constituent sources
    # (SETIASTRO_STAR_STRETCH_SOURCE below carries that label on its own).
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

SETIASTRO_STAR_STRETCH_SOURCE = EvidenceReference(
    reference_id="setiastro-star-stretch-source",
    title="SetiAstro Star Stretch v2.6 published PJSR script/template",
    locator="https://www.setiastro.com/pjsr-scripts",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NOVA_STAR_STRETCH_FALLBACK_FIX = EvidenceReference(
    reference_id="nova-star-stretch-fallback-fix",
    title="star_stretch() local fallback formula corrected to match the published v2.6 transfer function",
    locator="nas_server/seti_astro.py:_star_stretch_transfer; SeeStar-db#658, PR #661 (merged 2026-09-08)",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_STAR_FLOOR_SOURCE = EvidenceReference(
    reference_id="nova-star-floor-source",
    title="combine_stars_screen()'s _floor_star_layer() inter-star black-point mitigation",
    locator="nas_server/seti_astro.py:_floor_star_layer,combine_stars_screen",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

IC5146_STAR_FLOOR_EVIDENCE = EvidenceReference(
    reference_id="nova-ic5146-star-floor-evidence",
    title="IC 5146 (Cocoon Nebula) real run: star layer ~94% of combined background variance; star-floor k=2.0 moved combined sky std only 0.0749->0.0744",
    locator="critiques/20260606_162829_seestar_nebula.md (run 20260606_162829_seestar_nebula, workflow seestar_nebula v1.0.0)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

LINEAR_STAR_SPLIT = HandbookArticle(
    article_id="linear-star-split",
    schema_version=SCHEMA_VERSION,
    revision=2,
    process_family=ProcessFamily.LINEAR_STAR_SPLIT,
    purpose=(
        "Separate stars from extended target structure while the image is still "
        "linear, so each can receive a stretch and finishing treatment tailored to "
        "its own tonal needs, then recombine them into one nonlinear image -- "
        "rather than forcing one stretch curve to serve both a star's high-contrast "
        "compact profile and a nebula's or galaxy's faint diffuse structure."
    ),
    observable_symptoms=(
        "A single whole-image stretch that develops faint extended structure well "
        "either clips or over-enlarges stars, or one tuned for good star color and "
        "core retention leaves the faint target underdeveloped.",
        "Star color, halo size, or core saturation compete with nebula/galaxy "
        "visibility for the same stretch curve's limited range.",
    ),
    intended_output=(
        "A recombined nonlinear image where the starless layer and the stars layer "
        "were each stretched appropriately for their own content, with no visible "
        "seam, dark ring, halo mismatch, or color discontinuity at the recombination "
        "boundary."
    ),
    limits=(
        "This is a paired-layer state transition, not a single tool call: a "
        "successful split produces a starless image (which becomes the main "
        "pipeline path) and a stars-only sidecar that must retain shared "
        "provenance -- matching dimensions/WCS and a known extraction/"
        "recombination convention -- until they are recombined.",
        "NOVA's current split path is StarXTerminator first, DarkStar fallback -- "
        "not two independently ranked engines. DarkStar only runs after SXT fails, "
        "so production history is selection-biased: DarkStar sees a different case "
        "mix than SXT. A historical success/win ratio between the two paths is not "
        "a valid comparative performance statistic for that reason.",
        "The two current backends do not derive the stars sidecar the same way. "
        "SXT requests StarXTerminator's native `--stars` sidecar directly. NOVA's "
        "DarkStar fallback instead derives stars as the inverse of screen blending "
        "(`stars = 1 - (1-original)/(1-starless)`, mode `unscreen` by default). "
        "RC-Astro's own StarXTerminator usage guidance recommends simple "
        "subtraction, not Unscreen, when generating a stars image from linear "
        "data specifically because Unscreen is intended for already-stretched, "
        "nonlinear images. This means SXT and DarkStar can differ in the "
        "definition of the stars layer itself, not merely in neural-model "
        "quality -- a raw comparison between the two engines' stars layers, or "
        "between recombined results, is confounded by this until reconciled.",
        "A successful split is not proven by the existence of both output files. "
        "File existence is a structural execution gate; it says nothing about "
        "whether nonstellar compact structure (galaxy nuclei, HII knots, "
        "planetary-nebula cores, cometary features) leaked into the stars layer, "
        "or whether residual star cores/halos/tile seams remain in the starless "
        "layer. RC-Astro's own documentation names compact nonstellar structure "
        "as a class of feature that can require a protective mask.",
        "Star-layer stretch is not \"just another stretch.\" Its job is to map a "
        "sparse, high-dynamic-range stars-only layer into a nonlinear layer that "
        "preserves star color/profile while leaving enough highlight headroom for "
        "the later screen blend. NOVA's SetiAstro Star Stretch v2.6 fallback "
        "transfer function is `y = f*x / ((f-1)*x + 1)`, `f = 3^stretch_factor` -- "
        "the published vendor default is `stretch_factor=5` with an explicit "
        "\"above 5 with caution\" warning, but NOVA deliberately overrides that "
        "default at runtime with a data-driven auto-selection targeting the star "
        "layer's own measured p90 brightness to land near 0.25 post-stretch "
        "(typically factor 1.0-3.0) -- substantially subtler than the vendor "
        "default, by design, not by accident. The ontology's stated default of "
        "2.0 only applies if that auto-detection fails.",
        "A global whole-frame p90 target is a field-density-dependent control "
        "variable: on a sparse galaxy field, p90 of the stars-only layer can be "
        "essentially inter-star background; on a dense Milky Way field, p90 can "
        "represent genuine faint stars or residual halos. The same p90 target "
        "does not imply the same stellar appearance across targets -- treat it "
        "as a useful starting control, not a quality proof.",
        "NOVA's local fallback formula for `star_stretch()` previously did not "
        "algebraically match SetiAstro's published v2.6 transfer function "
        "(`f*x/(f*x+1)` instead of the correct `f*x/((f-1)*x+1)` -- notably the "
        "correct form maps x=1 exactly to 1, the incorrect one did not). This was "
        "only a latent defect if the real SASpro import failed and the fallback "
        "engaged, but it is now fixed and matches the vendor formula exactly "
        "(#658, PR #661, merged 2026-09-08) -- current behavior, not a caveat "
        "about a live bug.",
        "SetiAstro's own published Star Stretch script makes green-cast removal "
        "(SCNR) optional and defaults it OFF. NOVA's `star_stretch()` defaults "
        "`do_scnr=True` at `scnr_amount=0.9`, injected at runtime from the "
        "starless-stage SCNR experiment winner rather than an independent "
        "choice for the stars layer specifically. SCNR is a color-changing "
        "operation; do not attribute a color improvement or regression to "
        "\"stretch\" alone when SCNR and saturation changed at the same time.",
        "Screen recombination is `combine(B,S) = 1 - (1-B)(1-S) = B+S-BS` for "
        "bounded layers B (starless), S (stars) in [0,1]. Useful invariants: "
        "`combine(B,0)=B` and `combine(0,S)=S`; the result is >= each input and "
        "<=1; if either input is already 1 at a pixel, the output is 1 -- "
        "clipping/saturation already present in either layer cannot be undone "
        "by the blend. The exact inverse (`S=(O-B)/(1-B)`) becomes "
        "ill-conditioned as B approaches 1, which matters for any future "
        "recomposition-residual check near bright regions.",
        "`combine_stars_screen()` applies `_floor_star_layer()` before "
        "screening: a per-channel median + `star_floor_k`*MAD (default 2.0) "
        "black point on the stars layer, rescaled so bright star cores well "
        "above the floor are preserved. This exists specifically because "
        "screen-combining an unfloored stretched star layer injects its "
        "amplified inter-star sky grain directly into the final background "
        "(screen behaves approximately additively for small values). This is "
        "real, current, already-live behavior -- not proposed by this revision "
        "-- and it is not exposed as an ontology-tunable parameter, so "
        "Experiment Mode currently cannot vary or disable it per candidate.",
        "A material background *drop* across a pure screen combine is not "
        "explained by screen math -- `combine(B,S) >= B` always. NOVA's own "
        "critique history includes at least one such case; treat a real "
        "post-combine background decrease as a pipeline/normalization/"
        "measurement anomaly to investigate, not a normal property of "
        "screening. Conversely, a background-region variance *increase* after "
        "recombination is not automatically a defect either -- one NOVA "
        "critique found the stars layer accounted for ~94% of background-"
        "region variance on a dense faint-star field, so screening it back "
        "legitimately raised std/background; the star-floor mechanism above "
        "changed that field's combined-sky std only ~0.0749->0.0744, meaning "
        "most of that variance was real faint stars, not noise the floor could "
        "safely suppress. Real run: IC 5146 (Cocoon Nebula), "
        "`20260606_162829_seestar_nebula` -- the star layer measured ~94% of "
        "combined background variance, and the tested star-floor at the safe "
        "k=2.0 moved combined sky std only 0.0749->0.0744 (star cores stayed "
        "intact, p99.9 0.999->0.998); reaching a materially lower std needed a "
        "p95+ floor that erased most faint stars, so the floor is kept as a "
        "no-regret guard for genuine read-noise cases, not a general fix.",
        "The ontology currently defines exactly one `stretch_stars` experiment "
        "variant (`star_stretch_default`) -- there is no weaker/stronger "
        "parameter ladder or vendor-reference-factor candidate, and no no-op "
        "control, the way CLAHE or HDR compression have. A historical "
        "production run of this step is evidence that the step executed, not "
        "evidence that its specific factor was compared against real "
        "alternatives.",
        "This is a distinct operation from nonlinear star removal/starless "
        "finishing (a separate article) -- that operation runs on an already-"
        "nonlinear/stretched parent as an optional post-stretch strategy for "
        "targets that were not split before stretching; the two are not "
        "interchangeable and do not share extraction math.",
        "No NOVA execution record isolates this three-step sequence's own "
        "before/after quality on a specific validated run in the way Stretch "
        "and Star Correction have a Henry-validated worked example. The "
        "P19/P20 research explicitly does not claim NOVA's current parameter "
        "choices (the p90->0.25 controller, SCNR default, star-floor k=2.0) "
        "are empirically optimal -- the remaining gap is controlled real-image "
        "validation across representative target types (sparse galaxy field, "
        "emission nebula with bright stars, dense Milky Way field, "
        "high-dynamic-range bright-star case), not missing theory.",
    ),
    required_input_state=(
        "Fully linear, unstretched data, after color calibration, deconvolution, "
        "denoise, and star correction -- immediately before the main stretch. "
        "Severe optical aberrations or oversampled stars should ideally be "
        "corrected first; RC-Astro documents that both can reduce StarXTerminator's "
        "split effectiveness.",
    ),
    nova_action=(
        "NOVA's `star_split` step (`step_type == \"star_split\"` in "
        "auto_process.py) first calls `seti_astro.sxt_star_split()`, which "
        "dispatches RC-Astro's StarXTerminator through the same headless RC-Astro "
        "CLI/RunPod GPU path used elsewhere in the pipeline -- not PixInsight, "
        "despite older documentation sometimes implying a PI dependency for this "
        "step. If both the starless and stars files exist afterward, the split is "
        "accepted and the pipeline's active path switches to the starless output "
        "while the stars file is retained. If SXT fails, NOVA logs a warning and "
        "falls back to `seti_astro.remove_stars_split()`, SASpro's in-process "
        "DarkStar wrapper (default mode `unscreen`), which derives the stars "
        "sidecar via the inverse-screen formula above rather than SXT's native "
        "sidecar. Later, `stretch_stars` runs SASpro Star Stretch on the stars-"
        "only file specifically -- not the main starless path -- and at runtime "
        "its `stretch_factor` is overridden by a data-driven auto-selection based "
        "on the star layer's own measured p90 brightness (target: roughly 0.25 "
        "post-stretch, deliberately subtle relative to the nebula/galaxy layer); "
        "the ontology's stated default of 2.0 only applies if that auto-detection "
        "fails. `star_stretch()` prefers SASpro's real `applyPixelMath_numba`; "
        "if that import fails, it falls back to a local implementation of the "
        "same published SetiAstro v2.6 transfer `y = f*x/((f-1)*x+1)`, "
        "`f=3^stretch_factor` -- corrected to match the vendor formula exactly "
        "as of 2026-09-08 (#658, PR #661). Finally, `combine_stars_screen` "
        "black-points the stars layer's inter-star background via "
        "`_floor_star_layer()` (median + `star_floor_k`*MAD, default 2.0) "
        "before performing the screen-blend recombination "
        "`1-(1-starless)(1-stars)` of the stretched starless and stretched "
        "stars layers, and by its own ontology note must follow the split, "
        "main stretch, and star stretch in that exact order."
    ),
    nova_evidence_ids=(
        "nova-star-split-source",
        "nova-star-stretch-fallback-fix",
        "nova-star-floor-source",
        "nova-ic5146-star-floor-evidence",
    ),
    use_when=(
        "The workflow is configured to stretch the starless and stellar layers "
        "separately rather than stretching the whole linear image with stars "
        "still merged into it.",
        "Stars are dominating local contrast, color, or tone decisions that would "
        "otherwise constrain how the extended target structure can be developed.",
        "The parent linear image gives recognizably clean, reasonably well-"
        "corrected stellar profiles for the split model to work from.",
    ),
    skip_when=(
        "Stars are the primary subject (dense star fields, globular or open "
        "clusters) -- removing the pipeline's defining signal to isolate a "
        "background layer can invert the actual goal, and both split engines "
        "carry elevated risk of misreading nonstellar compact structure as "
        "stellar on crowded fields.",
        "Both the SXT and DarkStar paths fail; the pipeline should preserve the "
        "unsplit linear parent rather than accept a failed or copied result as a "
        "successful split.",
        "The target contains many compact nonstellar features (bright galaxy "
        "nuclei, planetary-nebula cores, cometary structure, dense filament "
        "intersections) without an available protective mask -- RC-Astro "
        "explicitly documents these as feature classes a split can mistakenly "
        "remove.",
    ),
    scientific_and_aesthetic_notes=(
        "A clean-looking starless preview is not proof of a good split -- it "
        "proves only that the model produced a smooth-looking result, which can "
        "happen by erasing real compact structure just as easily as by correctly "
        "isolating stars.",
        "A fair split-engine comparison needs the exact same linear parent and "
        "prior-processing history for both engines, identical crop/WCS/precision, "
        "recorded engine/model/mode identity, and -- ideally -- an immediate "
        "recomposition-residual check (recombining starless + stars using the "
        "method's own intended inverse/forward math and comparing against the "
        "original parent) before any unrelated downstream processing is layered "
        "on top and confounds the result.",
        "Because DarkStar in this pipeline is a failure-triggered fallback, not a "
        "peer alternative selected by preference, its production frequency is not "
        "an unbiased reliability measure for the engine itself.",
        "Star population counts before/after a split must use the same detection "
        "threshold, crop, and plate scale -- a lower star count can mean stricter "
        "detection rather than a worse split, and the reverse comparison error is "
        "just as easy to make.",
        "A fair star-stretch-strength comparison should freeze one exact "
        "stars-only sidecar from one exact split, hold saturation=1 and "
        "SCNR=off while testing strength alone (then test saturation/SCNR "
        "separately on the winning stretch, to avoid confounding causes), and "
        "measure on star-mask-conditioned statistics -- fraction of detected "
        "star-core pixels at/above 0.999 and 0.99, star-mask p95/p99/p99.9, "
        "matched-detection FWHM/half-light radius, per-star color for "
        "unsaturated stars, inter-star background median/MAD on the "
        "stars-only layer, and halo/ring energy in an annulus outside the "
        "core -- not primarily whole-frame statistics.",
        "A fair recombination comparison should freeze one stretched starless "
        "base and one stretched stars layer, only compare screen against an "
        "alternative blend if that alternative is mathematically compatible "
        "with the extraction convention actually used (never compare additive "
        "vs. screen as if they were cosmetic, extraction-independent choices), "
        "and measure star-core clipping, star color, halo/ring residuals, "
        "target-region luminance/chroma away from stars, and background "
        "median/MAD on a star-excluded mask separately.",
        "Whole-frame p99.9 and background standard deviation are especially "
        "easy to misread once stars are restored: recombination can "
        "legitimately consume most or all remaining highlight headroom (one "
        "NOVA critique recorded curves p99.9=0.805 pre-recombination, "
        "p99.9/p99.99=1.0 after) without that being a defect, and a "
        "background-variance rise can mean real faint stars returned rather "
        "than noise. Prefer star-conditioned and star-excluded metrics side "
        "by side over either whole-frame statistic alone.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="ontology current; RC-Astro CLI/RunPod GPU dispatch, SASpro in-process for the DarkStar fallback",
            host="NOVA Python pipeline",
            instructions=(
                "Confirm which backend actually ran (SXT or the DarkStar fallback) "
                "before assuming SXT's native-sidecar semantics -- the fallback "
                "changes the stars-layer extraction math, not just the model.",
                "Do not assume this step requires PixInsight -- SXT here is "
                "dispatched headlessly through the RC-Astro CLI/RunPod GPU path.",
                "Verify `combine_stars_screen` ran after, not before, the main "
                "stretch and `stretch_stars` -- the ontology's own note states "
                "this ordering is required, not merely conventional.",
                "Record whether `star_stretch()` used the real SASpro import or "
                "its local fallback -- both now implement the same correct "
                "vendor v2.6 formula (#658/PR #661), but which one ran is still "
                "worth recording for reproducibility.",
                "Record the effective `star_floor_k` used by "
                "`combine_stars_screen()` (default 2.0) -- it is not currently "
                "an ontology-exposed parameter, so it will not appear in a "
                "candidate's declared params even though it materially affects "
                "the final background.",
            ),
            controls_and_starting_ranges=(
                ("split mode (SXT primary)", "native `--stars` sidecar; RunPod GPU endpoint first, local RC-Astro CLI/CPU fallback second"),
                ("split mode (DarkStar fallback, on SXT failure)", "unscreen (default) or additive; GPU on by default"),
                ("stretch_stars stretch_factor", "auto-selected from the star layer's own p90 brightness (target ~0.25 post-stretch, typically 1.0-3.0); ontology default of 2.0 applies only if auto-detection fails. Vendor published default is 5, with caution above 5 -- NOVA is deliberately subtler."),
                ("stretch_stars do_scnr / scnr_amount", "NOVA default True/0.9, injected from the starless SCNR winner; vendor script default is SCNR off"),
                ("combine_stars_screen star_floor_k", "default 2.0 (median + k*MAD black point on the stars layer before screening); not currently ontology-exposed"),
            ),
            expected_result=(
                "A starless image carried forward as the main pipeline path, a "
                "matching stars-only sidecar, and -- after the main stretch, star "
                "stretch, and recombination -- one nonlinear image with no visible "
                "seam or halo mismatch at former star positions, and no unexplained "
                "background level change from the recombination step itself."
            ),
            failure_modes=(
                "Treating a DarkStar-fallback split as evidence about SXT's own "
                "quality, or vice versa, given the fallback's selection bias.",
                "Running `combine_stars_screen` before both layers have actually "
                "been stretched.",
                "Assuming the ontology's stretch_factor default of 2.0 describes "
                "what actually ran, when the real value is data-driven per run.",
                "Attributing a color change to stretch strength when SCNR/"
                "saturation also changed on the same candidate.",
                "Reading a post-recombination background increase as noise "
                "amplification without checking whether it is real faint-star "
                "signal the star-floor mechanism correctly chose not to remove.",
            ),
            recovery=("Preserve the unsplit linear parent and continue or stop per pipeline policy rather than accepting a failed or partial split as successful.",),
            mask_support="Not exposed by NOVA's current wrapper for either split backend; RC-Astro's own protective masks (see vendor sources) are not currently wired into this dispatch path.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=(
                "nova-star-split-source",
                "nova-star-stretch-fallback-fix",
                "nova-star-floor-source",
                "p19-p20-research",
                "nova-ic5146-star-floor-evidence",
            ),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="StarXTerminator, stand-alone RC-Astro CLI path; exact model revision not independently confirmed in this repo",
            host="RC-Astro StarXTerminator (stand-alone, headless -- no PixInsight host process)",
            instructions=(
                "Use early, on linear data, per RC-Astro's own usage guidance -- "
                "it internally MTF-stretches for inference and reverses that "
                "transform, so working on linear data is the documented intended use.",
                "For a linear stars image, RC-Astro recommends simple subtraction "
                "over Unscreen, since Unscreen is documented for nonlinear/"
                "stretched extraction specifically.",
                "Apply a protective mask for known compact nonstellar structures "
                "(galaxy nuclei, HII knots, comet cores) if the workflow supports "
                "it -- RC-Astro documents this as sometimes necessary, not optional insurance.",
            ),
            controls_and_starting_ranges=(
                ("recommended use point", "early/linear, per vendor guidance"),
                ("linear stars extraction", "subtraction preferred over Unscreen, per vendor guidance"),
                ("sampling", "RC-Astro reports best operation below roughly 8px stellar FWHM, with 3-4px described as adequately sampled"),
            ),
            expected_result="A starless image and a matched stars sidecar with minimal residual star cores and minimal nonstellar-structure loss.",
            failure_modes=(
                "Applying StarXTerminator to a severely aberrated or oversampled "
                "parent and expecting split quality unaffected -- RC-Astro documents "
                "both as degrading factors.",
                "Using Unscreen to derive a linear stars image against the vendor's own guidance.",
            ),
            recovery=("Correct upstream optical/profile issues where valid, or fall back to another split strategy for this target.",),
            mask_support="RC-Astro documents mask support for protecting non-stellar compact structure; not currently wired into NOVA's headless dispatch of this step.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("rc-astro-sxt-usage-notes", "rc-astro-sxt-mask-handling"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="DarkStar/Cosmic Clarity (split fallback) + Star Stretch v2.6 (stars-layer stretch); exact NOVA-deployed model version not independently confirmed in this repo",
            host="Seti Astro Suite Pro DarkStar / Star Stretch",
            instructions=(
                "Run DarkStar only as the documented fallback when the primary "
                "StarXTerminator split fails, not as an interchangeable first choice.",
                "Confirm which mode ran (unscreen vs. additive) before interpreting "
                "the derived stars layer -- this changes the extraction math, not just the strength.",
                "Treat the resulting stars sidecar as DarkStar's own inverse-screen "
                "derivation, not as directly comparable to a StarXTerminator native sidecar.",
                "Star Stretch's published v2.6 transfer function is "
                "`y = (3^a*x) / ((3^a-1)*x + 1)`, default `a=5`, UI range 0-8, "
                "with an explicit \"above 5 with caution\" warning -- NOVA's "
                "runtime `a` (stretch_factor) is normally the data-driven "
                "auto-selected value, not this vendor default.",
                "The vendor script's SCNR (green removal) defaults OFF; NOVA's "
                "wrapper defaults it ON at 0.9 -- do not assume vendor-default "
                "behavior when reasoning about NOVA's star-layer color handling.",
            ),
            controls_and_starting_ranges=(
                ("mode", "unscreen (default) or additive"),
                ("use_gpu", "true by default"),
                ("chunk_size", "512 (implementation/performance control, not an image-quality setting)"),
                ("Star Stretch amount (published UI)", "0-8, vendor default 5, caution above 5"),
            ),
            expected_result="A starless image and a derived stars sidecar via inverse-screen math (DarkStar), or a stretched stars-only layer matching the vendor transfer function (Star Stretch).",
            failure_modes=("Pooling DarkStar-fallback outcomes with SXT outcomes as if they were peer, unbiased alternatives.",),
            recovery=("Preserve the unsplit linear parent if both split engines fail for this target.",),
            mask_support="Not currently exposed by NOVA's wrapper for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=(
                "saspro-darkstar-source",
                "nova-star-split-source",
                "setiastro-star-stretch-source",
                "nova-star-stretch-fallback-fix",
            ),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native StarNet integration; exact version not independently confirmed in this repo",
            host="Siril native StarNet star/starless split",
            instructions=(
                "Siril's built-in `starnet` command produces a starless image and "
                "a stars-only companion using StarNet++, a different neural model "
                "family from both StarXTerminator and DarkStar -- treat it as a "
                "conceptual alternative, not a matched reproduction of NOVA's split.",
                "Siril's own documentation does not draw the same linear-vs-"
                "nonlinear subtraction/Unscreen distinction RC-Astro's guidance "
                "makes for star-layer extraction; do not assume Siril's derived "
                "stars layer follows either convention without checking.",
                "Record the exact Siril version and command options used; none of "
                "this integration's split-quality behavior has been independently "
                "verified against NOVA's pipeline in this repo.",
            ),
            controls_and_starting_ranges=(
                ("availability", "native `starnet` command; no linear/nonlinear mode selection equivalent to NOVA's split confirmed"),
            ),
            expected_result="A starless image and a stars-only companion via a different star-removal model family than either of NOVA's current engines.",
            failure_modes=(
                "Treating a Siril StarNet split as evidence about StarXTerminator "
                "or DarkStar quality -- it is a third, unrelated model family.",
                "Assuming the same extraction/recombination math as either of NOVA's two current backends.",
            ),
            recovery=("Fall back to the linear parent and select another split path if StarNet's output is unsatisfactory for this target.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.CONCEPTUAL_SUBSTITUTE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-star-split-source",),
        ),
    ),
    measurements=(
        "Dimensions, WCS, and finite numeric data preserved in both the starless "
        "and stars output layers.",
        "Recomposition residual: recombining starless + stars using the active "
        "method's own inverse/forward math and comparing against the original "
        "linear parent, before any unrelated downstream processing.",
        "Flux/profile measurements on representative unsaturated stars, checked "
        "for consistency across split-then-recombine within an explicitly chosen tolerance.",
        "Compact nonstellar regions of interest checked for retained morphology/flux "
        "in the starless layer, not leaked into the stars layer.",
        "Residual maps around bright stars checked for dark holes, bright rings, "
        "seams, or duplicated halo texture.",
        "Star-mask-conditioned clipping fraction (>=0.999, >=0.99), matched-"
        "detection FWHM/half-light radius, and per-star color for unsaturated "
        "stars, before and after the star-layer stretch.",
        "Inter-star background median/MAD on the stars-only layer, and the "
        "effective `star_floor_k` value actually used by "
        "`combine_stars_screen()`, since it is not currently ontology-exposed.",
        "Background median/MAD in a star-excluded mask, and background-region "
        "variance, measured before and after recombination separately from "
        "whole-frame statistics.",
    ),
    acceptance_criteria=(
        "Both output layers preserve dimensions, WCS, and finite data.",
        "An immediate recomposition residual (where checked) closely reconstructs "
        "the original linear parent, not merely 'looks similar'.",
        "Known compact nonstellar structures retain their morphology and flux in "
        "the starless layer rather than disappearing into the stars layer.",
        "No systematic dark holes, bright rings, seams, or duplicated texture "
        "around former star positions after recombination.",
        "Which backend actually executed (SXT vs. the DarkStar fallback), its "
        "mode, and whether a fallback was triggered are all recorded -- not just "
        "'star split applied'.",
        "A lower detected star count or a smoother-looking starless background is "
        "never treated as validation by itself; it can equally mean over-removal "
        "or destroyed compact signal.",
        "The effective star-stretch factor (not just whether auto-detection "
        "engaged), SCNR/saturation settings, and the effective `star_floor_k` "
        "are all recorded as part of the treatment identity -- not just "
        "'star stretch applied' or 'recombined'.",
        "A post-recombination background level change is checked against "
        "screen math's own invariants (`combine(B,S) >= B` always) before "
        "being called a defect or dismissed as noise.",
    ),
    sources=(
        P18_PACKET,
        NOVA_STAR_SPLIT_SOURCE,
        RC_ASTRO_SXT_USAGE_NOTES,
        RC_ASTRO_SXT_MASK_HANDLING,
        SASPRO_DARKSTAR_SOURCE,
        P19_P20_RESEARCH,
        SETIASTRO_STAR_STRETCH_SOURCE,
        NOVA_STAR_STRETCH_FALLBACK_FIX,
        NOVA_STAR_FLOOR_SOURCE,
        IC5146_STAR_FLOOR_EVIDENCE,
    ),
)

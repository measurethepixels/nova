"""Handbook Phase 3, Group F: Post-Stretch Denoise.

Synthesized from Phase-2 research packet P29 (2026-08-28, SeeStar-db
`83a59b783c0ec3125aa86c9563044d80b17aa094`). P29's own completion comment on
#457 is authoritative per that issue's completion-evidence rule; the issue
body's checkboxes are stale (truncation risk prevented rewriting them) and
must not be read as "research incomplete."

Re-verified against current source 2026-09-09 -- real, material drift found
since the Aug-28 snapshot:

- `noise_reduction`'s production default engine changed from
  `cc_denoise_inprocess` to `denoise_nxt` the day after the research snapshot
  (2026-08-29, workflow 1.25.0, commit 1c63d87) -- Henry explicitly chose a
  second conservative NXT pass over Cosmic Clarity after a real M31 benchmark
  where a full local Cosmic Clarity run took ~27.4 minutes and the objective
  gate rejected the result anyway. This is current production truth the
  packet could not have known; the packet's roughly-symmetric three-engine
  framing is corrected to reflect NXT as the deliberate default with CC and
  Prism Mini as explicit Experiment Mode alternatives, not equals.
- The ontology's `cc_denoise_gentle`/`cc_denoise_moderate` candidates declare
  `fn: "cc_denoise"`, which does not match any real `seti_astro` function
  (the real one is `cc_denoise_inprocess`) -- a real, previously-undiscovered
  dispatch bug that predates both the NXT-default switch and the P29
  research itself. Filed separately as #679 (owner:codex); this article
  describes both CC candidates as currently non-dispatching rather than as
  working alternatives.
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

P29_PACKET = EvidenceReference(
    reference_id="packet-p29",
    title="Phase-2 research packet P29, 2026-08-28",
    locator="packet:P29:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_DENOISE_SOURCE = EvidenceReference(
    reference_id="nova-post-stretch-denoise-source",
    title="NOVA post-stretch denoise implementation: denoise_nxt, cc_denoise_inprocess, syqon_prism_denoise, denoise_bakeoff.py",
    locator=(
        "nas_server/seti_astro.py:denoise_nxt,cc_denoise_inprocess,syqon_prism_denoise; "
        "nas_server/processing_ontology.json noise_reduction; scripts/denoise_bakeoff.py"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NXT_DEFAULT_DECISION = EvidenceReference(
    reference_id="nova-nxt-default-decision",
    title="Workflow 1.25.0: NXT chosen as the standard post-stretch engine over Cosmic Clarity, following a real M31 benchmark",
    locator="critiques/WORKFLOW_CHANGELOG.md (workflow 1.25.0, 2026-08-29); commit 1c63d87",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

CC_DISPATCH_BUG = EvidenceReference(
    reference_id="nova-cc-denoise-dispatch-bug",
    title="cc_denoise_gentle/moderate candidates reference a nonexistent seti_astro.cc_denoise function (issue #679)",
    locator="nas_server/processing_ontology.json noise_reduction; SeeStar-db#679",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

RC_ASTRO_NXT_MANUAL = EvidenceReference(
    reference_id="rc-astro-nxt-manual",
    title="RC Astro NoiseXTerminator 2/AI3 User Manual (PixInsight)",
    locator="https://www.rc-astro.com/noisexterminator-2-ai3-user-manual-pixinsight/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SETIASTRO_COSMIC_CLARITY = EvidenceReference(
    reference_id="setiastro-cosmic-clarity",
    title="Seti Astro Cosmic Clarity",
    locator="https://www.setiastro.com/cosmic-clarity",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

POST_STRETCH_DENOISE = HandbookArticle(
    article_id="post-stretch-denoise",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.POST_STRETCH_DENOISE,
    purpose=(
        "Suppress visually objectionable residual noise -- stochastic grain, "
        "chroma speckle, correlated resampling texture -- after the main "
        "stretch, while preserving real low-contrast structure and compact "
        "features. A nonlinear stretch changes the apparent amplitude and "
        "spatial distribution of noise that linear-stage denoise (a separate "
        "article) already addressed once; this is an optional second pass on "
        "a different signal/noise regime, not a repeat of the same step."
    ),
    observable_symptoms=(
        "Dark background variation, faint-detail grain, or chroma speckle "
        "became visually prominent after the main stretch, even though "
        "color calibration and linear-stage denoise are correct.",
    ),
    intended_output=(
        "Reduced objectionable stochastic grain/chroma speckle in matched "
        "background and faint-signal regions, with low-contrast filaments, "
        "dust, galaxy outskirts, small-scale target texture, and star "
        "profiles/cores preserved -- no plastic, waxy, patchy, tiled, or "
        "smeared texture, and no unjustified change to background level, "
        "gradients, hue, or dynamic-range mapping."
    ),
    limits=(
        "Denoising does not recover information that was never recorded. A "
        "smoother result is not automatically a more truthful one -- learned "
        "denoisers can suppress real faint texture as noise, and strong "
        "settings can create plasticky/smeared regions or edge "
        "discontinuities. RC Astro's own NXT documentation makes this "
        "explicit: 0 means no reduction, 1 means attempting full reduction, "
        "and 100% removal can look unnaturally smooth.",
        "NoiseXTerminator is NOVA's current production default for this "
        "step, not one of three roughly-equal engines. Workflow 1.25.0 "
        "(2026-08-29) made `denoise_nxt` the ontology's `seti_astro_fn`, "
        "replacing the prior default `cc_denoise_inprocess`, following a "
        "real M31 broadband benchmark where a full local Cosmic Clarity run "
        "took approximately 27.4 minutes and the objective gate rejected "
        "the resulting candidate anyway. Cosmic Clarity and Prism Mini "
        "remain real Experiment Mode alternatives, not the production "
        "default -- describing them as three symmetric production choices "
        "overstates their current standing.",
        "The ontology's `cc_denoise_gentle` and `cc_denoise_moderate` "
        "candidates declare `fn: \"cc_denoise\"`, which does not match any "
        "real `seti_astro` function (the real function is "
        "`cc_denoise_inprocess`). Both candidates currently fail every time "
        "they are selected, returning `ok: False, error: \"seti_astro."
        "cc_denoise not found\"` instead of running Cosmic Clarity (issue "
        "#679). Until that ontology fix lands, Cosmic Clarity is not a "
        "working Experiment Mode alternative in practice, regardless of "
        "what the candidate list appears to offer.",
        "Cross-engine numeric strengths are not commensurate: Cosmic "
        "Clarity's luma/color values, Prism Mini's `strength`, and NXT's "
        "denoise/iterations represent different controls on different "
        "models. Comparing one preset from each is a complete-strategy "
        "comparison, not an isolated test of engine superiority -- do not "
        "translate a strength value from one engine onto another.",
        "Prism Mini's tile/overlap/pad geometry (fixed at 512/64/64 in "
        "current ontology candidates) is part of treatment identity, not an "
        "implementation detail -- fixed pixel-space tile geometry maps to "
        "different angular scales at different drizzle factors, and seams "
        "at tile boundaries are a real failure mode worth an explicit "
        "acceptance check, especially on large or mosaic frames.",
        "A prior Handbook/source audit recorded an NXT treatment-identity "
        "risk: the ontology includes an `nxt_detail` field, but the "
        "candidate's own description now states outright that \"the "
        "RC-Astro CLI does not currently expose an equivalent control\" -- "
        "the field is recorded for preset parity, not necessarily consumed "
        "by the runtime. Effective executed parameters, not ontology "
        "labels, are the real treatment identity.",
        "`scripts/denoise_bakeoff.py` is a real, valuable execution/"
        "comparison scaffold -- it runs a true unchanged baseline, Cosmic "
        "Clarity, Prism Mini, and NXT from the same post-stretch starless "
        "parent and records `snr`, background RMS, median, and a "
        "`grain = background_rms / median` ratio. That proves the "
        "comparison workflow is designed and executable; it does not by "
        "itself establish a validated engine ranking, and its metrics "
        "inherit the same caveats as the rest of this family (see "
        "measurement traps below).",
    ),
    required_input_state=(
        "A nonlinear, already-stretched image, past the main linear "
        "restoration chain (deconvolution, linear denoise, star correction "
        "as applicable), with residual noise actually assessed as "
        "objectionable rather than denoised reflexively. NOVA workflows "
        "place this step after stretch and other nonlinear cleanup/contrast "
        "operations, so the real parent can be a post-background-neutralize "
        "or post-CLAHE product depending on the active workflow path -- "
        "CLAHE/local contrast can amplify noise, making a subsequent pass "
        "here qualitatively different from denoise applied immediately "
        "after stretch alone.",
    ),
    nova_action=(
        "NOVA's production default is `denoise_nxt()`: a second, "
        "conservative NoiseXTerminator pass at `nxt_denoise=0.65`, "
        "`nxt_iterations=2`, dispatched through the remote RC-Astro GPU "
        "when enabled and the local RC-Astro CLI otherwise -- never "
        "silently substituting Cosmic Clarity. This is distinct from the "
        "pre-stretch linear NXT pass, which remains unchanged. Cosmic "
        "Clarity (`cc_denoise_inprocess()`, separate luminance/color "
        "strengths) and Prism Mini (`syqon_prism_denoise()`, a pure "
        "in-process NumPy path with explicit tile/overlap/pad controls) "
        "remain available as explicit Experiment Mode candidates, though "
        "the Cosmic Clarity candidates currently fail to dispatch (#679). "
        "`scripts/denoise_bakeoff.py` provides a real four-arm comparison "
        "scaffold (unchanged baseline, Cosmic Clarity, Prism Mini, NXT) "
        "from a shared post-stretch parent for evaluation outside the "
        "standard pipeline path."
    ),
    nova_evidence_ids=(
        "nova-post-stretch-denoise-source",
        "nova-nxt-default-decision",
        "nova-cc-denoise-dispatch-bug",
    ),
    use_when=(
        "The stretched image shows objectionable stochastic grain or chroma "
        "speckle after the main stretch, and denoise strength can be "
        "checked against real preservation of faint structure rather than "
        "applied automatically.",
    ),
    skip_when=(
        "The image is already adequately denoised from the linear stage and "
        "shows no new objectionable noise after stretch -- a second pass "
        "risks compounding detail loss for no real gain.",
        "The workflow's own object-type routing omits this step for the "
        "target class (the galaxy workflow currently omits the nonlinear "
        "denoise step entirely, per workflow 1.25.0's own changelog note).",
        "The parent state includes strong prior local-contrast enhancement "
        "(CLAHE, DSE) that has already amplified noise texture in a way "
        "this step cannot cleanly separate from real structure.",
    ),
    scientific_and_aesthetic_notes=(
        "A rigorous experiment should start from the same parent FITS, hold "
        "stretch/curves/CLAHE/HDR/color/star-state/cropping fixed, include "
        "a true unchanged baseline, record engine/model/tool version and "
        "actual executed parameters (not just candidate ID), record masks/"
        "tiling/normalization behavior and whether the image scale is "
        "native/drizzled/mosaic, compare multiple target/background "
        "structures rather than whole-frame statistics alone, and inspect "
        "full-resolution outputs and difference images rather than JPEG "
        "previews. If claiming a cross-engine comparison, tune engines "
        "fairly rather than comparing arbitrary preset numbers as if the "
        "scales were equivalent.",
        "A within-engine strength result (e.g. NXT gentle vs. standard vs. "
        "strong) and a cross-engine method result (NXT vs. Cosmic Clarity "
        "vs. Prism Mini) are two different levels of interpretation. "
        "Pooling both into one historical \"noise_reduction winner "
        "percentage\" erases treatment-identity differences that matter.",
        "Post-stretch denoise is highly parent-state dependent: the same "
        "candidate run after stronger CLAHE/curves or a different stretch "
        "has a different apparent need and different artifact behavior. "
        "Historical winner rates across unlike parent states are weak "
        "evidence unless blocked by stretch, local-contrast history, "
        "target class, image scale, and prior denoise state.",
        "\"Plasticity\" -- waxy smooth regions, suppressed faint granular "
        "structure, local texture inconsistency -- is not captured by one "
        "scalar. Entropy or high-frequency power can detect that something "
        "changed but cannot identify whether the removed high-frequency "
        "content was noise or real signal. Human/perceptual inspection "
        "remains necessary, paired with matched structural ROIs and "
        "difference images, not treated as unstructured taste alone.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="denoise_nxt() production default (workflow 1.25.0); cc_denoise_inprocess()/syqon_prism_denoise() Experiment Mode alternatives; denoise_bakeoff.py comparison scaffold",
            host="NOVA Python pipeline",
            instructions=(
                "Treat NXT as the deliberate production default, not one of "
                "three symmetric choices -- the 2026-08-29 switch away from "
                "Cosmic Clarity was a real, evidenced decision (M31 "
                "benchmark: ~27.4min runtime, objective-gate rejection), "
                "not an arbitrary preference.",
                "Do not select cc_denoise_gentle/moderate expecting a "
                "working Cosmic Clarity comparison until #679 is fixed -- "
                "both currently fail dispatch.",
                "Record actual executed parameters, not just candidate ID "
                "-- the ontology's nxt_detail field may not be consumed by "
                "the runtime path even when declared on a candidate.",
                "For Prism Mini, record tile/overlap/pad alongside "
                "strength -- geometry is part of treatment identity, "
                "especially across native vs. drizzled image scale.",
            ),
            controls_and_starting_ranges=(
                ("nxt_gentle / nxt_standard / nxt_strong", "NXT denoise 0.45 / 0.65 / 0.80 -- production default runs at 0.65, 2 iterations"),
                ("cc_denoise_gentle / cc_denoise_moderate", "Cosmic Clarity luma/color 0.50/0.35 and 0.70/0.50 -- currently non-dispatching, see #679"),
                ("prism_mini_gentle / prism_mini_standard", "Prism Mini strength 0.65 / 0.85, tile 512 / overlap 64 / pad 64"),
                ("none", "true control"),
            ),
            expected_result=(
                "Reduced objectionable grain/chroma speckle in matched "
                "background and faint-signal regions with low-contrast "
                "structure, star profiles, and background level preserved."
            ),
            failure_modes=(
                "Waxy/plastic faint regions or loss of filaments/dust from "
                "strength set too high or a second denoise pass compounding "
                "detail loss.",
                "Chroma smearing from color-denoise strength set too high.",
                "Tile seams or local discontinuities on large/drizzled/"
                "mosaic frames from Prism Mini's fixed pixel-space tile "
                "geometry.",
                "Treating a Cosmic Clarity candidate selection as a real "
                "comparison arm when it is currently failing to dispatch.",
            ),
            recovery=("Revert to the parent/unchanged control; reduce strength; separate luminance and chroma treatment; increase tile overlap/padding or switch engine for seam artifacts.",),
            mask_support="Not currently exposed by NOVA's wrapper for any of the three engines in this family.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-post-stretch-denoise-source", "nova-nxt-default-decision", "nova-cc-denoise-dispatch-bug"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="RC-Astro NoiseXTerminator via headless CLI/RunPod GPU dispatch, not a PixInsight host process",
            host="RC-Astro NoiseXTerminator 2/AI3",
            instructions=(
                "Vendor semantics: `denoise` amount 0 = no reduction, 1 = "
                "attempt full reduction; 100% removal can look unnaturally "
                "smooth. `iterations` describes successive-approximation "
                "steps -- more can retain detail better in noisy regions, "
                "but high counts can create artifacts.",
                "NOVA's simpler candidate family does not expose NXT's full "
                "documented parameter space (e.g. intensity/color and high-/"
                "low-frequency separation modes in the full tool) -- a NOVA "
                "result is evidence about NOVA's selected configuration, "
                "not NXT as an abstract universal method.",
            ),
            controls_and_starting_ranges=(
                ("denoise", "0-1, NOVA production default 0.65"),
                ("iterations", "successive-approximation steps, NOVA production default 2"),
            ),
            expected_result="Suppressed residual noise via NXT's learned model at the configured denoise/iteration settings.",
            failure_modes=("Assuming NOVA exercises NXT's full documented parameter space when only denoise/iterations are exposed.",),
            recovery=("Reduce denoise amount or iterations; compare against the unchanged baseline.",),
            mask_support="Not exposed by NOVA's current wrapper.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("rc-astro-nxt-manual", "nova-post-stretch-denoise-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="Cosmic Clarity (in-process, currently non-dispatching for this family's ontology candidates -- #679) + Prism Mini",
            host="Seti Astro Suite Pro Cosmic Clarity / Prism Mini",
            instructions=(
                "Cosmic Clarity exposes separate luminance and color denoise "
                "strengths -- meaningful because chroma speckle can be "
                "objectionable even when luminance structure should be "
                "preserved, though strong chroma reduction can smear "
                "legitimate weak color variation.",
                "The installed SASpro package/model version is part of "
                "treatment identity for an AI denoiser embedded through "
                "SASpro -- treat exact model identity as version-bound "
                "unless captured by run provenance.",
                "Do not expect cc_denoise_gentle/moderate to actually run "
                "until #679's ontology fix lands.",
            ),
            controls_and_starting_ranges=(
                ("denoise_luma / denoise_color", "0.50/0.35 gentle, 0.70/0.50 moderate in current ontology candidates"),
                ("Prism Mini strength", "0.65 gentle, 0.85 standard"),
                ("Prism Mini tile / overlap / pad", "512 / 64 / 64 -- fixed pixel-space geometry, a reproducibility factor across native/drizzle scale"),
            ),
            expected_result="Learned-model noise suppression via Cosmic Clarity or Prism Mini's in-process NumPy path.",
            failure_modes=("Comparing Cosmic Clarity/Prism Mini strength values directly against NXT's denoise/iterations as if numerically equivalent.",),
            recovery=("Reduce strength; separate luma/color treatment for Cosmic Clarity; adjust tile/overlap for Prism Mini seam artifacts.",),
            mask_support="Not exposed by NOVA's current wrapper for either engine.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("setiastro-cosmic-clarity", "nova-cc-denoise-dispatch-bug", "nova-post-stretch-denoise-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native denoise tools; exact version not independently confirmed in this repo",
            host="Siril native denoise tools",
            instructions=(
                "Treat as the same conceptual goal -- residual noise "
                "suppression on a nonlinear image -- with different "
                "implementation than NOVA's NXT/Cosmic Clarity/Prism Mini "
                "paths.",
                "Apply the same joint noise-reduction-plus-structure-"
                "preservation measurement discipline used for NOVA's own "
                "candidates.",
            ),
            controls_and_starting_ranges=(
                ("native denoise tools", "typically applied post-stretch"),
            ),
            expected_result="Reduced residual noise via Siril's own denoise tooling.",
            failure_modes=("Treating a Siril denoise result as evidence about NOVA's NXT/Cosmic Clarity/Prism Mini candidates.",),
            recovery=("Prefer the unchanged baseline if the result damages faint structure.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-post-stretch-denoise-source",),
        ),
    ),
    measurements=(
        "Robust sky/background RMS or MAD reduction, and chroma-noise "
        "reduction in neutral/background regions, on matched parent/output "
        "states and appropriate ROIs.",
        "Local variance reduction in designated empty-sky ROIs, and "
        "brightness-stratified star-profile change if stars are present.",
        "Structural residual/difference images in faint target ROIs, and "
        "preservation of known filament/dust boundaries across matched "
        "coordinates.",
        "Tile-boundary discontinuity checks for tiled engines (Prism Mini).",
        "Runtime/resource cost, since it materially informed the real "
        "production engine decision (the M31 27.4-minute Cosmic Clarity "
        "benchmark).",
    ),
    acceptance_criteria=(
        "Objectionable residual noise is reduced in matched background/"
        "faint-signal regions while low-contrast filaments, dust, galaxy "
        "outskirts, target texture, and star profiles are preserved, "
        "checked against a real unchanged baseline.",
        "No waxy/plastic texture, tile seams, or chroma smearing introduced.",
        "Effective engine, model/tool version, and actual executed "
        "parameters are recorded -- not just candidate ID, given the "
        "nxt_detail forwarding caveat and the currently-broken Cosmic "
        "Clarity candidates.",
        "Whole-frame NOVA SNR-like values, background_rms/median grain "
        "ratio, entropy, high-frequency power, generic sharpness, SSIM "
        "against the noisy parent, and AI/perceptual preference are never "
        "presented as standalone proof -- lower RMS can reward deleting "
        "faint signal, and a higher SNR-like proxy can reward "
        "over-smoothing.",
        "A cross-engine result is never presented as isolating engine "
        "superiority unless effective strengths were fairly matched or "
        "each engine received its own fair tuning opportunity.",
    ),
    sources=(
        P29_PACKET,
        NOVA_DENOISE_SOURCE,
        NXT_DEFAULT_DECISION,
        CC_DISPATCH_BUG,
        RC_ASTRO_NXT_MANUAL,
        SETIASTRO_COSMIC_CLARITY,
    ),
)

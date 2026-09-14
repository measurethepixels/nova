"""Handbook Phase 3, Group D2b: Nonlinear Star Removal and Starless Finishing.

Synthesized from Phase-2 research packet P31 (2026-08-28, SeeStar-db
`e6f359d3689f331ed195a574fafe1f99845739ab`). Conceptually and mathematically
distinct from Linear Star Split (a separate article): that operation splits an
already-linear image before stretching; this one is an optional strategy
applied to an already-nonlinear/stretched image.
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

P31_PACKET = EvidenceReference(
    reference_id="packet-p31",
    title="Phase-2 research packet P31, 2026-08-28",
    locator="packet:P31:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_STARLESS_FINISHING_SOURCE = EvidenceReference(
    reference_id="nova-starless-finishing-source",
    title="NOVA nonlinear star removal implementation and Experiment Mode candidates",
    locator=(
        "nas_server/seti_astro.py:remove_stars_inprocess,star_removal_starxt; "
        "nas_server/processing_ontology.json star_removal; nas_server/experiments.py _NL_STEPS"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

RC_ASTRO_SXT_USAGE_NOTES = EvidenceReference(
    reference_id="rc-astro-sxt-usage-notes-p31",
    title="RC-Astro StarXTerminator Usage Notes",
    locator="https://www.rc-astro.com/starxterminator-usage-notes/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

RC_ASTRO_SXT_FAQ_FAILURE = EvidenceReference(
    reference_id="rc-astro-sxt-faq-failure",
    title="RC-Astro: Why does StarXTerminator fail to remove stars?",
    locator="https://www.rc-astro.com/faq/why-does-starxterminator-fail-to-remove-stars/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SASPRO_DARKSTAR_SOURCE = EvidenceReference(
    reference_id="saspro-darkstar-source-p31",
    title="Seti Astro Suite Pro DarkStar / Cosmic Clarity",
    locator="https://www.setiastro.com/cosmic-clarity",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

STARLESS_FINISHING = HandbookArticle(
    article_id="starless-finishing",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.STARLESS_FINISHING,
    purpose=(
        "Optionally and temporarily separate stellar from non-stellar content on an "
        "already-nonlinear (post-stretch) image, so extended structure -- nebulosity, "
        "galaxy detail, dust -- can be finished without simultaneously pushing, "
        "recoloring, or clipping stars."
    ),
    observable_symptoms=(
        "Extended-structure finishing operations (local contrast, dark-structure "
        "enhancement, selective saturation) keep interacting with stars because "
        "they are still merged into the same nonlinear image.",
        "A finishing pass that helps nebulosity or galaxy detail simultaneously "
        "enlarges, recolors, or blows out stars, forcing an unwanted compromise.",
    ),
    intended_output=(
        "A nonlinear starless working image suitable for selected extended-object "
        "finishing. This is a model-conditioned reconstruction, not an observation "
        "of the sky with stars physically absent -- a clean-looking filled region "
        "is not recovered hidden astrophysical data, and compact non-stellar "
        "structure can be removed if the model classifies it as star-like."
    ),
    limits=(
        "Star removal is segmentation and inpainting, not physical signal recovery. "
        "This is the central scientific-language boundary for this article: nothing "
        "downstream should describe a starless result as having revealed data that "
        "was actually recorded underneath a star.",
        "NOVA's four current candidates are `darkstar_unscreen` and "
        "`darkstar_additive` (SASpro DarkStar, same engine, different extraction "
        "mode -- strategy probes within one engine family, not independent "
        "algorithms), `pi_starxt` (RC-Astro StarXTerminator dispatched through "
        "NOVA's stand-alone RC-Astro CLI/RunPod path, restoring celestial WCS "
        "afterward since RC-Astro drops it), and `none`, a true unchanged control. "
        "The ontology's `pi_starxt` naming and its 'best-in-class' description are "
        "both stale/unsupported: current execution is the stand-alone RC-Astro "
        "CLI, not a PixInsight host process, and 'best-in-class' is vendor "
        "marketing language, not a repository-established comparative evidence "
        "level -- neither should be repeated here as validated fact.",
        "`star_removal` is not in Experiment Mode's `_NL_STEPS`, so it receives "
        "none of the generic nonlinear luminance-mask injection or parameter "
        "adaptation that several neighboring nonlinear families do. That removes "
        "one common cross-candidate confounder, but real treatment differences "
        "remain: different AI models/training domains, DarkStar's unscreen-vs-"
        "additive semantics, RC-Astro-vs-SASpro preprocessing, tiling/chunking "
        "behavior, GPU/CPU numerical differences, and WCS restoration applying "
        "only to the StarXTerminator path. The absence of generic adaptation is "
        "not the same thing as full engine equivalence.",
        "RC-Astro explicitly documents that StarXTerminator can remove non-"
        "stellar compact features -- comet cores, compact galaxy cores, and "
        "filamentary nebular structure are named vendor examples -- and that "
        "masks are sometimes required to protect them. A starless result "
        "therefore cannot be validated by a lower star count alone.",
        "The `none` control is not a weak candidate. On star-rich targets, "
        "clusters, fields with difficult optical aberrations, or targets "
        "containing compact non-stellar structure, removal can do more harm than "
        "the starless finishing it was meant to enable -- `none` winning is a "
        "scientifically legitimate outcome, not a failed experiment.",
        "This experiment evaluates the starless output alone. It does not "
        "validate a complete matching stars-only layer or a later recombination "
        "-- a good-looking starless result is not proof that a compatible "
        "stars-only layer and clean recombination exist.",
        "This is a distinct operation from Linear Star Split (a separate "
        "article), which splits an already-linear parent before stretching using "
        "different extraction math for a linear image; the two should never be "
        "treated as the same strategy at different pipeline positions.",
    ),
    required_input_state=(
        "Nonlinear, already-stretched data with stable background modeling and "
        "color state, a completed stretch whose stellar profiles are not "
        "pathologically distorted, no gross clipping that makes star cores/halos "
        "impossible to model cleanly, and known scale/resampling state. RC-Astro "
        "documents that removal effectiveness can degrade with severe optical "
        "aberrations, excessive oversampling, and certain aggressive/alternative "
        "stretches such as GHS or arcsinh.",
    ),
    nova_action=(
        "NOVA's DarkStar path (`seti_astro.remove_stars_inprocess`) is a headless "
        "in-process SASpro wrapper accepting `mode` (default `unscreen`), "
        "`use_gpu` (default true), and `chunk_size` (default 512), writing a "
        "starless FITS. NOVA's StarXTerminator path "
        "(`seti_astro.star_removal_starxt`) imports `run_rcastro()` and dispatches "
        "operation `sxt` -- the same stand-alone RC-Astro CLI/RunPod GPU "
        "architecture used elsewhere in the pipeline, not a PixInsight-host "
        "process, despite the ontology candidate id `pi_starxt`; NOVA restores "
        "celestial WCS on the output afterward since RC-Astro's dispatcher drops "
        "it. The `none` candidate copies the input unchanged as a true control. "
        "None of these three receives NOVA's generic nonlinear adaptation/mask "
        "layer, since `star_removal` is absent from `_NL_STEPS`."
    ),
    nova_evidence_ids=("nova-starless-finishing-source",),
    use_when=(
        "Extended target structure is the finishing priority and stars are "
        "currently constraining how far that finishing can go.",
        "The parent stretch gives recognizable, reasonably clean stellar profiles "
        "-- not one of RC-Astro's documented degrading conditions (severe "
        "aberration, oversampling, GHS/arcsinh-shaped extreme stretches).",
        "The target does not contain many compact features easily confused with "
        "stars, or a protective mask is available in the chosen workflow.",
        "A true `none` control is retained in the comparison, not dropped for expedience.",
    ),
    skip_when=(
        "Stars are the subject -- clusters, star-rich fields, or any target where "
        "removing stars would remove the point of the image.",
        "The target contains compact nonstellar structure at meaningful risk of "
        "misclassification (bright compact galaxy nuclei, planetary-nebula cores, "
        "cometary features, filament intersections) without an available "
        "protective mask.",
        "The parent stretch or optical state falls into RC-Astro's documented "
        "degrading conditions for removal effectiveness.",
        "Starless processing offers no specific downstream advantage for this "
        "target -- it is not a default 'better workflow' step.",
    ),
    scientific_and_aesthetic_notes=(
        "The value of starless processing is workflow control and perceptual "
        "separation, not recovery of occluded information -- this framing should "
        "govern how any starless result is described, not just how it is judged.",
        "A fair DarkStar-vs-StarXTerminator comparison needs the exact same "
        "nonlinear parent, the same crop/WCS/scale/resampling state, the same "
        "upstream stretch and star-correction history, recorded engine/model/tool "
        "version, a true unchanged `none` control, target-preservation regions of "
        "interest selected before results are known where practical, and artifact "
        "inspection at full/native resolution rather than preview thumbnails.",
        "`darkstar_unscreen` vs. `pi_starxt` is not guaranteed to isolate 'DarkStar "
        "model vs. StarXTerminator model' in isolation -- their preprocessing and "
        "internal strategies differ, so treat any comparison as a complete-method "
        "comparison unless internal settings are demonstrated equivalent.",
        "A winner measured on one stretch-engine-shaped parent (for example a GHS "
        "stretch) should not be generalized to a differently-shaped parent (for "
        "example Statistical Stretch or STF) without repeated evidence -- RC-Astro "
        "itself documents stretch shape as a factor in removal effectiveness.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="ontology current; RC-Astro CLI/RunPod GPU dispatch for pi_starxt, SASpro in-process for DarkStar",
            host="NOVA Python pipeline",
            instructions=(
                "Confirm the parent is genuinely nonlinear/post-stretch before "
                "running this step -- it is not interchangeable with Linear Star "
                "Split, which requires a linear parent and uses different extraction math.",
                "Do not read the `pi_starxt` candidate id as implying a "
                "PixInsight-host execution; verify it dispatched through the "
                "stand-alone RC-Astro CLI/RunPod path if provenance matters.",
                "Retain the `none` control in any comparison rather than assuming "
                "removal is always preferable.",
            ),
            controls_and_starting_ranges=(
                ("darkstar_unscreen / darkstar_additive", "mode unscreen or additive; GPU on by default"),
                ("pi_starxt", "stand-alone RC-Astro StarXTerminator via run_rcastro('sxt'); WCS restored on output"),
                ("none", "unchanged copy of the input; true control"),
            ),
            expected_result=(
                "A nonlinear starless working image with residual stellar energy "
                "at former star positions minimized without measurable loss of "
                "compact non-stellar target structure, or a `none` result when "
                "removal would cost more than it helps for this target."
            ),
            failure_modes=(
                "Presenting the ontology's 'best-in-class' StarXTerminator "
                "description as an established comparative result rather than "
                "vendor marketing language.",
                "Judging a candidate by star count or a smoother-looking background alone.",
                "Confusing this step's output with Linear Star Split's starless output.",
            ),
            recovery=("Fall back to `none` (unchanged parent) if all removal candidates damage the target or introduce unacceptable artifacts.",),
            mask_support="Not currently wired into NOVA's headless dispatch of this step for either engine.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-starless-finishing-source",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="StarXTerminator, stand-alone RC-Astro CLI path; exact model revision not independently confirmed in this repo",
            host="RC-Astro StarXTerminator (stand-alone, headless -- no PixInsight host process)",
            instructions=(
                "Apply to an already-nonlinear/stretched image, consistent with "
                "RC-Astro's own documented workflow position for this use case.",
                "Use a protective mask for known compact nonstellar structures "
                "where the workflow supports it.",
                "If removal appears to fail or leave residuals, check for RC-"
                "Astro's documented degrading conditions (severe aberration, "
                "oversampling, unusual stretch shape) before assuming a model defect.",
            ),
            controls_and_starting_ranges=(
                ("documented degrading conditions", "severe optical aberration; excessive oversampling; certain aggressive/alternative stretches (GHS, arcsinh)"),
            ),
            expected_result="A starless image with minimized residual star energy and no material loss of known compact non-stellar structure.",
            failure_modes=("Assuming a starless result validates itself without checking target-preservation regions of interest.",),
            recovery=("Correct upstream conditions where possible, or prefer the `none` control for this target.",),
            mask_support="RC-Astro documents mask support for protecting non-stellar compact structure; not currently wired into NOVA's headless dispatch of this step.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("rc-astro-sxt-usage-notes-p31", "rc-astro-sxt-faq-failure"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="DarkStar/Cosmic Clarity; exact NOVA-deployed model version not independently confirmed in this repo",
            host="Seti Astro Suite Pro DarkStar",
            instructions=(
                "Choose unscreen or additive mode deliberately -- these are "
                "different extraction/recombination assumptions, not merely a "
                "strength setting, and should be documented as separate strategies.",
                "Compare against a true `none` control, not only against the other "
                "removal engine.",
            ),
            controls_and_starting_ranges=(
                ("mode", "unscreen or additive"),
                ("use_gpu", "true by default"),
                ("chunk_size", "512 (implementation/performance control)"),
            ),
            expected_result="A starless working image via DarkStar's chosen extraction mode.",
            failure_modes=("Treating unscreen and additive as interchangeable strength settings of the same strategy.",),
            recovery=("Prefer the `none` control if both DarkStar modes damage the target.",),
            mask_support="Not currently exposed by NOVA's wrapper for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-darkstar-source-p31", "nova-starless-finishing-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native StarNet integration; exact version not independently confirmed in this repo",
            host="Siril native StarNet nonlinear star removal",
            instructions=(
                "Siril's built-in `starnet` command is a closer conceptual match "
                "here than for the linear split, since it is typically used on "
                "already-stretched data -- but it is still a different model "
                "family (StarNet++) from both DarkStar and StarXTerminator, so "
                "treat it as a functional alternative, not a matched engine.",
                "Apply the same target-preservation and residual checks used for "
                "NOVA's own candidates before accepting a StarNet result -- no "
                "engine is exempt from the compact-structure-loss risk.",
                "Record the exact Siril version and command options; this "
                "integration's removal quality has not been independently "
                "verified against NOVA's pipeline in this repo.",
            ),
            controls_and_starting_ranges=(
                ("availability", "native `starnet` command, typically applied post-stretch"),
            ),
            expected_result="A starless image and a stars-only companion via a different star-removal model family than either of NOVA's current engines.",
            failure_modes=("Treating a Siril StarNet result as evidence about DarkStar or StarXTerminator quality.",),
            recovery=("Prefer the `none` control if StarNet's output damages the target.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-starless-finishing-source",),
        ),
    ),
    measurements=(
        "Residual stellar energy/profile test at matched star locations before/"
        "after, measuring residual compact flux/rings rather than only counting "
        "detected stars.",
        "Target-preservation regions of interest on known compact non-stellar "
        "structures, checked for local flux/structure loss.",
        "Difference image (input minus starless output), interpreted with "
        "nonlinear compositing math in mind, to reveal removed target structure, "
        "halos, seams, or residual star cores.",
        "Round-trip reconstruction error when a matching stars layer exists, "
        "recombined with the correct additive/screen rule and compared to the parent.",
        "Tile/seam inspection, especially on large or high-resolution/drizzled images.",
        "Background statistics in genuinely unchanged regions, to detect model-"
        "induced texture or noise drift.",
        "Color residual inspection for cyan/magenta/red rings or color holes "
        "around removed stars.",
    ),
    acceptance_criteria=(
        "Residual stellar energy at former star positions is measurably reduced "
        "without new dark holes, bright rings, or duplicated texture.",
        "Known compact non-stellar structures retain their measured local flux "
        "and morphology.",
        "No systematic tile seams or background texture/noise drift in otherwise-"
        "unchanged regions.",
        "The candidate actually executed (engine, mode, and whether `none` won) "
        "is recorded -- a lower star count, smoother background, or more "
        "dramatic-looking target is never accepted alone as validation.",
        "`none` winning is recorded as a legitimate outcome, not reported as a "
        "failed or incomplete experiment.",
    ),
    sources=(
        P31_PACKET,
        NOVA_STARLESS_FINISHING_SOURCE,
        RC_ASTRO_SXT_USAGE_NOTES,
        RC_ASTRO_SXT_FAQ_FAILURE,
        SASPRO_DARKSTAR_SOURCE,
    ),
)

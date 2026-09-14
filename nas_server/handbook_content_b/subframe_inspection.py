"""Public Handbook guidance for subframe inspection and quality culling."""

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


P06_PACKET = EvidenceReference(
    reference_id="packet-p06",
    title="Phase-2 research packet P06, 2026-08-27",
    locator="Handbook P06 — Subframe Inspection, Scoring, and Culling",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

NOVA_CULLING_SOURCE = EvidenceReference(
    reference_id="nova-subframe-culling-source",
    title="NOVA frame-quality gates and corrected percentile ordering",
    locator=(
        "nas_server/frame_quality.py:worst_percentile_keys; "
        "nas_server/database.py:get_frames_for_stack; "
        "nas_server/stack_config.py:STACK_CONFIG_DEFAULTS"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_CULLING_FIX = EvidenceReference(
    reference_id="nova-culling-fix-468",
    title="Issue #468 fix: Reject worst percentile frames",
    locator="commit 524d899 (issue #468)",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SEP_DOCUMENTATION = EvidenceReference(
    reference_id="sep-source-extraction-docs",
    title="SEP source extraction and ellipse-parameter documentation",
    locator="https://sep.readthedocs.io/en/stable/reference.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_DOCUMENTATION = EvidenceReference(
    reference_id="siril-sequence-filter-docs",
    title="Siril sequence filtering and stacking command documentation",
    locator="https://siril.readthedocs.io/en/stable/Commands.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

AAVSO_STACKING = EvidenceReference(
    reference_id="aavso-stacking-guidance",
    title="AAVSO stacking guidance",
    locator="https://www.aavso.org/index.php/stacking-0",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)


SUBFRAME_INSPECTION = HandbookArticle(
    article_id="subframe-inspection",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.SUBFRAME_INSPECTION,
    purpose=(
        "Inspect comparable light frames before integration, exclude clearly damaging "
        "frames, and preserve as much useful integration time as the evidence supports."
    ),
    observable_symptoms=(
        "A subset of subframes has broader or more elongated stars than the rest.",
        "Cloud, obstruction, bright sky, or a strong gradient creates a visibly poor tail.",
        "A proposed cull sharpens the stack but also reduces frame count and depth.",
    ),
    intended_output=(
        "Persisted per-frame measurements and a deterministic retained-frame set for "
        "stacking under a declared policy; culling does not transform the input FITS files."
    ),
    limits=(
        "FWHM, eccentricity, star support, sky level, and gradient severity are indicators, not a universal quality score.",
        "The 10% cull is NOVA's current engineering default, not a proven general optimum; no comparative corpus establishes one best percentage.",
        "The minimum of 20 detected stars is a heuristic guardrail, not proof of clear sky or a universal physical threshold.",
        "The eccentricity 0.66, sky-factor 3.0, and gradient 0.5 defaults are engineering heuristics, not empirically optimal constants.",
        "Whether culling generally improves NOVA final stacks remains unresolved without matched stack-level experiments.",
        "Cached measurements remain bound to the analysis implementation and settings that produced them.",
    ),
    required_input_state=(
        "Individual light frames before integration, with calibration and CFA/debayer state known.",
        "A comparison pool matched enough in field or panel, image scale, filter/capture mode, exposure regime, and measurement version for ranking to be meaningful.",
        "Fallback or unavailable measurements distinguished from observed star measurements.",
    ),
    nova_action=(
        "NOVA first applies hard gates for weak star support, excessive eccentricity, "
        "measured gradient severity, and session-relative sky level. It then ranks the "
        "measured survivors by fwhm × (1 + eccentricity), where lower is better, and "
        "rejects the requested fraction from the high-scoring, worst end. The old "
        "reversed-tail defect reported by P06 is historical: commit 524d899 fixed it, "
        "and the current shared helper explicitly rejects the highest composite scores. "
        "Unscored frames have no measurement basis for percentile rejection. Manual, "
        "registration, exposure, and mount-mode eligibility remain separate filters."
    ),
    nova_evidence_ids=(
        "nova-subframe-culling-source",
        "nova-culling-fix-468",
        "packet-p06",
    ),
    use_when=(
        "A coherent frame pool contains a clearly degraded tail from tracking, focus, seeing, cloud, bright sky, or gradients.",
        "Enough frames remain for rejection and integration to behave reliably.",
        "A matched policy experiment can retain the exact frame manifest and compare final stacks.",
    ),
    skip_when=(
        "Frames are uniformly similar and discarded integration time is likely to cost more than the suspected defect.",
        "The dataset is small, or source-extraction measurements are unreliable for its field, filter, or sampling.",
        "Different panels, filters, scales, exposures, or measurement versions would be rank-compared as one population.",
        "Real extended emission may be mistaken for a sky gradient without review or appropriate grouping.",
    ),
    scientific_and_aesthetic_notes=(
        "Rejecting a damaged subframe can improve star shape or artifact burden, but rejecting usable frames reduces signal averaging; the final stack is the response that matters.",
        "Experiment Mode (experiment-mode) supplies the no-op-control principle: compare no quality culling with declared treatments while keeping the captured pool and stack settings fixed.",
        "How NOVA Knows (how-nova-knows) defines the evidence-strength vocabulary used here; source-confirmed defaults are not validation or recommendation.",
        "A 20% cut retains 80% of the frames and, for otherwise comparable sky-noise-limited data, begins with the integration-depth trade implied by square-root-N scaling.",
        "Sparse fields, filtered data, crowded clusters, nebulae, and mosaics can change what star count, shape, sky, and gradient measurements mean.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="current source",
            host="NOVA stack selection",
            instructions=(
                "Form a comparable candidate pool and preserve its frame identities.",
                "Inspect hard-gate failures separately from percentile-ranked rejection.",
                "Start with no quality culling as the control; treat 5%, 10%, and 20% as policy probes, not promises of improvement.",
                "Build matched final stacks and retain each arm's kept/rejected manifest, frame count, and integration time.",
            ),
            controls_and_starting_ranges=(
                ("bottom_pct", "0% control; 5% mild probe; 10% current default/reference; 20% aggressive research probe"),
                ("min_stars", "20 current heuristic default; retune only with data-class evidence"),
                ("ecc_threshold", "0.66 current heuristic default"),
                ("sky_level_factor", "3.0 × session median current heuristic default"),
                ("gradient_threshold", "0.5 current heuristic default"),
            ),
            expected_result="A reproducible retained-frame set whose final stack can be compared with the no-culling control.",
            failure_modes=(
                "Over-culling reduces depth without a compensating stack-level benefit.",
                "Sparse fields or filtered data fail a fixed star-support gate.",
                "Mixed populations or real extended emission distort ranking and gradient decisions.",
            ),
            recovery=(
                "Reduce or disable the percentile cut and compare matched final stacks.",
                "Stratify the pool and review rejected frames by reason.",
                "Treat unavailable or fallback measurements as unavailable, not observed PSF evidence.",
            ),
            mask_support="No image mask is applied; comparability comes from pool definition and recorded eligibility/rejection reasons.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-subframe-culling-source", "nova-culling-fix-468"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="unverified installed version",
            host="PixInsight SubframeSelector",
            instructions=(
                "Measure one comparable subframe population with one fixed expression and scale configuration.",
                "Review measurement plots and rejected frames instead of adopting an unverified threshold blindly.",
                "Export the retained-frame list and compare a matched integration against a keep-all control.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A documented alternative retained-frame set; exact equivalence to NOVA's formulas is not claimed.",
            failure_modes=("Scale or expression differences make scores incomparable.", "A ranking expression hides the integration-time cost."),
            recovery=("Re-measure with one configuration and inspect both retained and rejected tails.", "Compare final integrations, not scores alone."),
            mask_support="No starting mask is claimed; record any ROI or support restriction because it changes the measurement population.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4 documentation",
            host="Siril sequence filtering",
            instructions=(
                "Register and measure a comparable sequence using consistent settings.",
                "Filter by documented FWHM, roundness, background, star-count, quality, or manual criteria as appropriate.",
                "Record the filter expression or percentage and compare the resulting stack with the same sequence kept eligible.",
            ),
            controls_and_starting_ranges=(("percentage", "Use as a declared probe; NOVA's 5/10/20% values are not transferable scientific constants."),),
            expected_result="A functionally similar, tool-specific selection whose metric definitions are not assumed identical to NOVA's.",
            failure_modes=("Different metric definitions are mistaken for exact NOVA equivalence.", "Filtering is optimized without measuring the final stack."),
            recovery=("Record Siril's actual filter and weighting choices.", "Compare matched output stacks and retained integration time."),
            mask_support="No image mask is claimed; sequence membership and measurement configuration define support.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("siril-sequence-filter-docs",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="1.18.0 research snapshot",
            host="Seti Astro Suite Pro Blink Comparator",
            instructions=(
                "Inspect the comparable frame pool and record the frame-selection criteria used.",
                "Treat NOVA source comments about Blink Comparator lineage as claimed lineage, not proof of the same formula.",
                "Retain the selected-frame manifest and compare the final stack with a keep-all control.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A manual or score-assisted alternative selection; exact mathematical equivalence is unverified.",
            failure_modes=("Visual preference is presented as a reproducible threshold.", "Claimed lineage is presented as exact formula equivalence."),
            recovery=("Record explicit acceptance/rejection criteria and the frame manifest.", "Bound the conclusion to the tested pool and final-stack evidence."),
            mask_support="No image mask is claimed; visually inspected frame content supplies the decision context.",
            equivalence=EquivalenceClass.CONCEPTUAL_SUBSTITUTE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("packet-p06",),
        ),
    ),
    measurements=(
        "Per-frame FWHM and eccentricity distributions under matched image scale and extraction settings.",
        "Star-support, sky-level, and gradient-severity distributions, with unavailable/fallback values identified.",
        "Retained and rejected frame counts, rejection reasons, and total integration time for every policy arm.",
        "Final-stack star shape, background/noise proxy, gradients/artifacts, coverage, and matched visual evidence.",
    ),
    acceptance_criteria=(
        "The worst/highest composite-score tail is rejected; lower-is-better frames are not described as the rejected percentile.",
        "Every arm starts from the same captured pool and records its exact retained/rejected manifest and effective thresholds.",
        "Any claimed benefit is bounded to matched final-stack evidence and reports both quality change and lost integration.",
        "The 10%, min-stars 20, eccentricity 0.66, sky-factor 3.0, and gradient 0.5 values remain labeled current engineering defaults or probes, not optima.",
    ),
    sources=(
        P06_PACKET,
        NOVA_CULLING_SOURCE,
        NOVA_CULLING_FIX,
        SEP_DOCUMENTATION,
        SIRIL_DOCUMENTATION,
        AAVSO_STACKING,
    ),
)

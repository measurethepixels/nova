"""Public Handbook guidance for stacking, weighting, and pixel rejection."""

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


P09_PACKET = EvidenceReference(
    reference_id="packet-p09",
    title="Phase-2 research packet P09, 2026-08-27",
    locator="Handbook P09 — Stacking, Integration Engines, and Rejection Strategy",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

NOVA_STACKING_SOURCE = EvidenceReference(
    reference_id="nova-stacking-source",
    title="NOVA stack dispatch, configuration, and assessment source",
    locator=(
        "nas_server/stacker.py:stack_target; nas_server/stack_config.py; "
        "nas_server/seestar_stack_only.ssf; nas_server/stack_assessor.py"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

SIRIL_STACKING_DOCS = EvidenceReference(
    reference_id="siril-stacking-1.4.4",
    title="Siril 1.4.4 stacking command documentation",
    locator="https://siril.readthedocs.io/en/stable/Commands.html#stack",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SASPRO_IMAGEMM = EvidenceReference(
    reference_id="saspro-imagemm-vendor",
    title="Seti Astro Suite Pro ImageMM stacking description",
    locator="https://www.setiastro.com/seti-astro-suite-pro",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)


STACKING_INTEGRATION = HandbookArticle(
    article_id="stacking-integration-weighting-rejection",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.STACKING_INTEGRATION,
    purpose=(
        "Combine a declared set of registered exposures so persistent astronomical "
        "signal accumulates while normalization, weighting, and pixel-level outlier "
        "rejection limit noise and transient defects."
    ),
    observable_symptoms=(
        "A single exposure is noisy, but the target repeats at the same registered coordinates across frames.",
        "Satellite trails, cosmic rays, or residual defects affect isolated pixel samples rather than every exposure.",
        "Different integration strategies produce different noise, detail, artifact, or coverage tradeoffs from the same frame set.",
    ),
    intended_output=(
        "One still-linear integrated science image with its exact input manifest, frame count, "
        "total exposure, output grid, normalization, weighting, rejection, and executed engine recorded."
    ),
    limits=(
        "Frame-level culling and pixel-level rejection are different decisions: subframe inspection decides whether an entire exposure enters the stack; rejection decides whether an individual aligned pixel sample is an outlier during integration.",
        "Rejection needs enough comparable samples to estimate a distribution; aggressive thresholds can remove real faint or thin structure as well as transients.",
        "The ideal random-noise improvement is approximately proportional to the square root of comparable exposure count, but gradients, seeing, correlated noise, resampling, and unequal frames prevent that from being a guarantee.",
        "An engine comparison that changes frame selection, registration grid, drizzle, framing, normalization, or support is a strategy/bundle comparison, not proof that one pixel-combination algorithm is intrinsically better.",
        "NOVA's stack SNR is a structure-to-background-noise proxy, not conventional astronomical photometric SNR; efficiency derived from it is not a universal truth score.",
        "A readable output proves artifact production, not correctness, preservation, or a class-level engine preference.",
    ),
    required_input_state=(
        "A frozen, provenance-bearing light-frame manifest after frame-level subframe inspection and culling.",
        "Frames registered to a declared common geometry, with calibration, CFA/debayer, filter, exposure, mount mode, panel, and session compatibility known.",
        "A declared output grid, framing, drizzle state, normalization goal, weighting policy, rejection policy, and common measurement support.",
    ),
    nova_action=(
        "NOVA currently exposes four meaningful stack paths. Siril is the reference strategy: "
        "Siril registration followed by a 3/3 Winsorized-rejection mean, additive-scale "
        "normalization, background-noise weighting, and output normalization. ImageMM uses "
        "Siril-registered frames for a multi-frame deconvolution stack, currently 20 iterations, "
        "kappa 2.0, PerChannel. The historically named pixinsight_wbpp path is actually a hybrid: "
        "Siril registration plus PixInsight ImageIntegration with 3/3 Winsorized rejection, "
        "additive scaling, and requested SNR weighting. pixinsight_register changes registration, "
        "grid, and integration together. Requested behavior can fall back or change, so comparisons "
        "must record the executed treatment rather than only the requested engine name."
    ),
    nova_evidence_ids=("nova-stacking-source", "packet-p09"),
    use_when=(
        "Two or more compatible, registered exposures contain repeated target signal and partly independent noise.",
        "A declared rejection and weighting policy is supportable for the available frame count and data class.",
        "Alternative engines are being compared from a frozen manifest with state differences controlled or explicitly labeled.",
    ),
    skip_when=(
        "There is only one usable exposure; no-op is not a comparable stacking control, so use a declared reference integration strategy instead.",
        "Calibration, membership, registration, CFA/channel state, or output geometry is unresolved.",
        "The sample count is too small for the proposed rejection rule to estimate outliers reliably.",
        "The intended comparison cannot separate integration behavior from changed registration, drizzle, framing, or measurement support.",
    ),
    scientific_and_aesthetic_notes=(
        "Subframe Inspection and Culling is the frame-level decision page; this page begins with its retained manifest and does not duplicate its scoring guidance.",
        "Measuring an Astrophoto defines why matched support and a vector of measurements matter; no single sharpness, SNR-proxy, or efficiency value should rank all stack strategies.",
        "ImageMM couples integration with restoration, so smaller FWHM or more high-frequency structure can reflect ringing, noise amplification, or sharpened artifacts rather than more truthful detail.",
        "Drizzle, max-framing mosaics, and canonical grids change sampling or support; compare FWHM in common angular units and keep coverage performance separate from common-support image quality.",
        "A class-level preference requires repeated, version-applicable evidence across independent targets and data conditions, not one attractive result or a historical win fraction.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="current source snapshot described by P09",
            host="NOVA stack dispatch",
            instructions=(
                "Freeze the eligible frame manifest after frame-level culling and registration decisions.",
                "Select a reference or alternative strategy and record requested plus executed engine, versions, framing, drizzle, grid, normalization, weighting, rejection, and fallback behavior.",
                "Inspect common-support measurements, coverage, rejected samples or maps when available, and artifacts before accepting the integrated result.",
            ),
            controls_and_starting_ranges=(
                ("Siril reference", "Winsorized 3/3; additive-scale normalization; background-noise weighting"),
                ("ImageMM current runtime", "20 iterations; kappa 2.0; PerChannel"),
                ("hybrid PI integration", "Winsorized 3/3; additive scaling; requested SNR weighting; avgdev or hero-mode IKSS scale"),
            ),
            expected_result="A reproducible linear integration with manifest, executed treatment, and quality/resource evidence retained.",
            failure_modes=(
                "Frame-level exclusions are mistaken for pixel rejection.",
                "A fallback silently changes the requested strategy.",
                "One proxy or sharpened appearance is treated as an overall engine verdict.",
            ),
            recovery=(
                "Return to the frozen manifest and a conventional Siril reference integration.",
                "Match or explicitly disclose grid, support, drizzle, and framing differences.",
                "Inspect rejection artifacts and preservation evidence before adjusting thresholds.",
            ),
            mask_support="Use one declared common celestial/support region for comparisons; coverage maps and low-coverage edges are assessed separately.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-stacking-source",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="source-recorded 1.9.3 behavior; installed version unverified",
            host="PixInsight ImageIntegration",
            instructions=(
                "Use the same registered frame manifest and output geometry when testing integration alone.",
                "Match normalization, rejection, and weighting goals as closely as the tool permits and record differences.",
                "Inspect rejection maps and common-support output; call a PI-native registration run a strategy comparison, not integration-only evidence.",
            ),
            controls_and_starting_ranges=(
                ("NOVA hybrid reference", "Winsorized 3/3, additive scaling, SNR weight request"),
            ),
            expected_result="A weighted/rejected linear integration whose registration and parameter differences are explicit.",
            failure_modes=("Calling NOVA's hybrid path full WBPP.", "Changing registration or grid while claiming an integration-only comparison."),
            recovery=("Retain Siril registration for the hybrid comparison.", "Relabel PI-native registration plus integration as a bundle comparison."),
            mask_support="Measure a common valid region; retain rejection maps separately from the integrated image.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-stacking-source",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="1.4.4 documentation; NOVA source targets 1.4.x",
            host="Siril sequence stacking",
            instructions=(
                "Stack the frozen registered sequence with the declared combination, normalization, weighting, rejection, and framing policy.",
                "Record frame count and generate rejection maps when inspection is needed.",
                "Compare on common support and do not treat the current 3/3 thresholds as universal optima.",
            ),
            controls_and_starting_ranges=(("NOVA reference", "rej 3 3, implicit Winsorized, addscale, noise weighting, output normalization"),),
            expected_result="A conventional robust weighted integration suitable as NOVA's reference strategy.",
            failure_modes=("Too few samples make clipping unstable.", "Max-framing blank or low-coverage regions dominate whole-image statistics."),
            recovery=("Reduce or disable rejection for a small sample and inspect the result.", "Use a common-support mask and assess union coverage separately."),
            mask_support="Use common valid support for quality measurements; -maximize union coverage is a separate declared treatment.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("siril-stacking-1.4.4", "nova-stacking-source"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="vendor capability documented; installed version unverified",
            host="Seti Astro Suite Pro ImageMM",
            instructions=(
                "Use the same registered manifest and record the actual iteration, kappa, and color-mode settings.",
                "Treat ImageMM as integration plus multi-frame restoration rather than another robust mean.",
                "Inspect stars, faint structure, and high-contrast edges for ringing, false detail, and amplified noise.",
            ),
            controls_and_starting_ranges=(("NOVA current runtime", "20 iterations; kappa 2.0; PerChannel"),),
            expected_result="A restored multi-frame integration assessed for both detail and preservation artifacts.",
            failure_modes=("Sharpness-oriented metrics reward the method by construction.", "The ontology's unexecutable 40-iteration candidate is reported as current behavior."),
            recovery=("Compare against the conventional reference on common support.", "Return to the source-confirmed 20-iteration runtime treatment and record executed parameters."),
            mask_support="Use common support and inspect artifact-prone star edges and faint structures at native scale.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-imagemm-vendor", "nova-stacking-source"),
        ),
    ),
    measurements=(
        "Exact included frame identities, count, total exposure, and spatial coverage.",
        "Robust background-noise proxy on common sky support, with sampling and resampling state recorded.",
        "FWHM and eccentricity from a stable star population in common angular or grid units.",
        "Residual gradients, seams, clipping/pile-up diagnostics, and rejected-sample artifacts.",
        "Fine-structure preservation plus ringing, false-detail, and noise-amplification inspection.",
        "Runtime, memory, storage, I/O, and failures reported separately from image quality.",
    ),
    acceptance_criteria=(
        "The artifact is readable, linear, on the declared grid, and accounts for the expected manifest and exposure.",
        "Frame-level culling is explicitly distinguished from pixel-level rejection throughout the evidence and explanation.",
        "No catastrophic registration, coverage, clipping, rejection-hole, trail, ringing, or preservation failure is accepted.",
        "Comparisons use common support and disclose every unavoidable registration, drizzle, framing, normalization, weighting, and rejection difference.",
        "SNR-proxy and efficiency values remain qualified; no one-dimensional score or single dataset establishes a universal engine preference.",
    ),
    sources=(P09_PACKET, NOVA_STACKING_SOURCE, SIRIL_STACKING_DOCS, SASPRO_IMAGEMM),
)

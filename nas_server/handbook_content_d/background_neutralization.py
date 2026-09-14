"""Handbook Phase 3, Group E1: Background Neutralization.

Synthesized from Phase-2 research packet P22 (2026-08-28, SeeStar-db
`3a54b06f3951a15f3cd3fd2a5ab33b7db06a3023`). Material source drift since that
research ref: `seti_astro.background_neutralize()` gained a signal-preserving
mask (2026-09-04, real M42 post-mortem) that blends the correction back toward
original pixels in signal-bearing regions -- P22's own "whole-frame
application" framing predates this and is corrected here against current
source; P22's other findings (gate/mode-selector measurement mismatch,
the two real offset/scale semantics, reference-selection risk, historical-evidence
reclassification) were re-verified against current source and remain accurate.
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

P22_PACKET = EvidenceReference(
    reference_id="packet-p22",
    title="Phase-2 research packet P22, 2026-08-28",
    locator="packet:P22:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_BACKGROUND_NEUTRALIZE_SOURCE = EvidenceReference(
    reference_id="nova-background-neutralize-source",
    title="NOVA background neutralization wrapper, gate, and signal-preserving mask",
    locator=(
        "nas_server/seti_astro.py:background_neutralize; "
        "nas_server/tool_params.py:compute_background_neutralize; "
        "nas_server/auto_process.py:_physics_should_run/background_neutralize; "
        "nas_server/processing_ontology.json background_neutralize"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

M42_MASK_EVIDENCE = EvidenceReference(
    reference_id="nova-m42-background-neutralize-mask-2026-09-04",
    title="Real M42 run: whole-frame correction dragged the nebula core green before the "
          "signal-preserving mask; masked correction restored it",
    locator="nas_server/seti_astro.py:background_neutralize (2026-09-04 code comment, real run values)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

SASPRO_BACKGROUNDNEUTRAL_SOURCE = EvidenceReference(
    reference_id="saspro-backgroundneutral-source-p22",
    title="Seti Astro Suite Pro backgroundneutral.py (upstream source, commit 6df4a28)",
    locator="https://github.com/setiastro/setiastrosuitepro/blob/6df4a286a1b2e1d79f7d3f4dbfa55539b8ab37e0/src/setiastro/saspro/backgroundneutral.py",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_MANUAL_COLOR_CALIBRATION = EvidenceReference(
    reference_id="siril-manual-color-calibration-p22",
    title="Siril 1.4.4 Manual Color Calibration documentation",
    locator="https://siril.readthedocs.io/en/stable/processing/color-calibration/manual.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

PIXINSIGHT_RESOURCES = EvidenceReference(
    reference_id="pixinsight-resources-p22",
    title="PixInsight official resources",
    locator="https://www.pixinsight.com/resources/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

BACKGROUND_NEUTRALIZATION = HandbookArticle(
    article_id="background-neutralization",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.BACKGROUND_NEUTRALIZATION,
    purpose=(
        "Correct a measured residual sky color cast -- a channel imbalance in a "
        "chosen background reference -- after the main color-calibration and "
        "stretch path, without modeling a spatial gradient and without acting as "
        "a substitute for physical/catalog color calibration."
    ),
    observable_symptoms=(
        "Dark-corner sky patches read a noticeably non-neutral color (green, "
        "blue, or magenta tint) even after SPCC/SSSC and stretch have run.",
        "The rest of the image otherwise looks correctly calibrated -- this is a "
        "residual background offset, not a broken calibration path.",
    ),
    intended_output=(
        "A reduction in the measured dark-corner sky channel imbalance, with "
        "target color, sky level/headroom, stars, and extended structure "
        "preserved closely enough that the result remains a valid continuation "
        "of the same processing path."
    ),
    limits=(
        "This is a channel-balancing transform based on a reference region, not "
        "gradient extraction and not physical color calibration. A neutral "
        "corner after this step is not proof that stars or nebula have "
        "physically correct color, and it does not repair a spatial gradient -- "
        "a flatter-looking corner should never be credited as gradient removal "
        "without an independent, matched P11 comparison.",
        "The run/skip gate uses dark-corner sky ratios specifically because "
        "whole-frame color can be dominated by target signal. Ordinary "
        "processing keeps the established scale correction; Experiment Mode "
        "compares that real scale/pivot branch with SASpro's explicit offset "
        "branch and a no-treatment control.",
        "As of 2026-09-04, NOVA's wrapper reuses Sky-Selective Green Rebalance's "
        "own luminance mask to blend the SASpro-corrected result back toward "
        "the original pixels wherever the mask says signal, not background -- "
        "so the correction is no longer applied uniformly to the whole frame. "
        "A real M42 run recorded in the source: the unmasked correction "
        "neutralized the sky (corner G/R 0.673 -> 0.939) but dragged the bright "
        "nebula core -- whose true color is not neutral, dominant OIII lands in "
        "G -- from G/R 0.914 to a visibly green 1.133; the masked correction "
        "restored the core to G/R 0.918 (matching its pre-correction value) "
        "while leaving the corner correction (0.941) essentially unchanged. If "
        "mask construction itself fails, NOVA skips neutralization entirely "
        "rather than falling back to the unmasked correction (fail-closed).",
        "The auto-selected background reference is a heuristic, not a semantic "
        "\"clean sky\" detector: it searches for the darkest available patch. "
        "Dark does not guarantee uncontaminated -- extended faint nebulosity, "
        "IFN, dust, mosaic seams, vignette remnants, and narrowband structure "
        "can all occupy the darkest region.",
        "Historical run evidence has sometimes credited this step with "
        "dramatically improving \"gradient\" or spatial flatness. That "
        "inference is rejected here: those runs also contained major upstream "
        "palette/stretch/background differences, and a channel-balancing "
        "transform is not a gradient model -- see the linked background "
        "extraction article for gradient-specific evidence.",
    ),
    required_input_state=(
        "Nonlinear/stretched RGB, after the main background-extraction/"
        "gradient step, after the color-calibration path and its success/"
        "failure state are known, preferably after a linked stretch when "
        "preservation of calibrated color is intended, and with a meaningful "
        "region of relatively clean sky/background available to measure. Not "
        "applicable to monochrome data -- the gate explicitly skips it.",
    ),
    nova_action=(
        "NOVA gates on measured dark-corner sky ratios "
        "(image_analyzer._color()'s sky_g_over_r/sky_b_over_r), skipping "
        "when the maximum deviation from 1.0 is <= 0.10 to preserve an "
        "already-correct SPCC balance, and running when it exceeds 0.10. When "
        "it runs, tool_params.compute_background_neutralize() selects the "
        "established scale correction. seti_astro.background_neutralize() then calls SASpro's "
        "auto_rect_50x50() to find a background patch and "
        "background_neutralize_rgb() to correct it, applies a NOVA-specific "
        "dark-corner completeness pass (driving all four frame corners toward "
        "the darkest channel's median, motivated by a case where the single "
        "auto patch read neutral while the corners still carried a cast), and "
        "finally blends the whole result back toward the original pixels in "
        "signal-bearing regions using a reused luminance mask -- failing "
        "closed (skipping the step) if that masking step itself fails. The "
        "objective post-check requires the measured dark-corner cast to drop "
        "by more than 0.02 with the SNR-proxy retained above 0.85, and can "
        "request a stronger retry when residual imbalance stays above 0.15."
    ),
    nova_evidence_ids=("nova-background-neutralize-source", "nova-m42-background-neutralize-mask-2026-09-04"),
    use_when=(
        "A nonlinear RGB image has a measured residual background color cast "
        "after the upstream calibration/stretch state is understood.",
        "Multiple plausible sky regions agree on the direction of the cast.",
        "The image has a trustworthy clean-background reference, or the auto "
        "reference has been inspected/validated.",
        "A fallback color path left a background pedestal/cast and a later "
        "cleanup is explicitly intended, not misrepresented as catalog "
        "calibration.",
    ),
    skip_when=(
        "Monochrome data.",
        "The representative background is already neutral within the "
        "measured 0.10 engineering tolerance -- SPCC-calibrated color should "
        "be preserved, not disturbed, when there is no measured problem.",
        "No clean background reference exists and the darkest areas are "
        "likely real extended signal (frame-filling nebulae, IFN, galaxy "
        "halos).",
        "The apparent issue is a spatial gradient or vignetting rather than a "
        "global channel bias -- that is a background-extraction problem, not "
        "this step's job.",
        "The upstream color or stretch failure has not been diagnosed -- this "
        "step should not be used to mask a problem that belongs earlier in "
        "the pipeline.",
    ),
    scientific_and_aesthetic_notes=(
        "A rigorous comparison must hold constant the exact input FITS, the "
        "color-calibration success/failure path, the linked/unlinked stretch "
        "state, the narrowband/palette state, crop and mosaic coverage state, "
        "and ideally use a fixed validated background reference rectangle if "
        "testing transform semantics rather than reference selection.",
        "Before treating a background region as valid, check that it is "
        "consistently dark across multiple scales, contains no detected stars "
        "or compact objects, does not reveal diffuse nebulosity/IFN/dust under "
        "a deep stretch, is not near a mosaic seam or vignette edge, and "
        "agrees with several other candidate sky regions.",
        "Offset and scale are the two real correction semantics exposed by "
        "NOVA; pedestal removal remains a separate, unexposed axis.",
        "A single historical run reporting a large numeric improvement from "
        "one pivot mode is confounded artifact evidence at best -- not "
        "statistical confidence, and not proof that mode is universally "
        "preferable.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="SASpro-backed wrapper with NOVA completeness pass and signal-preserving mask (2026-09-04)",
            host="NOVA Python pipeline",
            instructions=(
                "Confirm the measured dark-corner cast that triggered this "
                "step, not just whether the frame looks non-neutral overall.",
                "Treat offset and scale as distinct correction semantics, and "
                "surface any candidate artifacts that collapse in a run.",
                "Persist the selected reference rectangle, effective mode, "
                "pre/post patch medians, pre/post dark-corner ratios, and "
                "whether the signal-preserving mask activated -- candidate ID "
                "alone is not sufficient provenance for this step.",
            ),
            controls_and_starting_ranges=(
                ("bg_neutral_offset / bg_neutral_scale", "the two real Experiment Mode correction semantics"),
                ("none", "true control -- essential for proving correction was needed"),
                ("0.10 run/skip gate", "source-confirmed engineering threshold on dark-corner max(|G/R-1|,|B/R-1|); not a scientifically validated optimum"),
            ),
            expected_result=(
                "A measurable reduction in dark-corner sky channel imbalance "
                "with target color, sky headroom, and structure preserved."
            ),
            failure_modes=(
                "Crediting this step with gradient correction because corners "
                "look flatter.",
                "Treating a large historical improvement from one pivot mode "
                "as validated superiority rather than confounded artifact "
                "evidence.",
                "Assuming the darkest available patch is guaranteed clean sky.",
            ),
            recovery=("Revert to the parent input; validate or choose a different background reference; skip if no clean sky exists.",),
            mask_support="Signal-preserving luminance mask built in (2026-09-04); fails closed (skips the step) if mask construction itself fails.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-background-neutralize-source", "nova-m42-background-neutralize-mask-2026-09-04"),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="backgroundneutral.py; production-installed 1.18.0 core semantics not independently re-verified in this repo",
            host="Seti Astro Suite Pro background neutralization",
            instructions=(
                "Select a background rectangle that represents true, "
                "uncontaminated sky -- the upstream source's own auto-finder "
                "searches for darkness, not cleanliness.",
                "Be aware that the inspected upstream core's default "
                "`remove_pedestal=True` subtracts each channel's whole-image "
                "minimum before the reference patch is even measured, which "
                "can alter channel relationships ahead of the correction "
                "itself -- this is unresolved/version-bound for the installed "
                "NOVA deployment.",
            ),
            controls_and_starting_ranges=(
                ("mode", "offset (explicit additive branch) or scale (NOVA's stable name for the pivot-around-1 branch)"),
            ),
            expected_result="A background-referenced channel correction toward neutral in the selected rectangle.",
            failure_modes=("Treating arbitrary non-offset strings as additional correction modes.",),
            recovery=("Fall back to a manually validated reference rectangle, or skip.",),
            mask_support="No signal-preserving mask at the inspected upstream core; NOVA's wrapper adds one on top.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("saspro-backgroundneutral-source-p22",),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="manual color calibration; exact version not independently confirmed in this repo",
            host="Siril manual background-reference color calibration",
            instructions=(
                "Select a background region that represents true background "
                "and avoid crowded or contrasty/stellar/target-dominated "
                "regions, per Siril's own documentation.",
                "Treat this as the same process goal -- background-reference "
                "channel equalization -- with a different selection/transform "
                "implementation than NOVA/SASpro, not a pixel-identical "
                "algorithm.",
            ),
            controls_and_starting_ranges=(
                ("background selection", "manual region selection representing true sky"),
            ),
            expected_result="Channel-equalized background toward neutral gray in the selected region.",
            failure_modes=("Selecting a background region that is dark but not actually clean sky.",),
            recovery=("Choose a different background region; compare against a no-op.",),
            mask_support="Not applicable -- manual region selection is itself the targeting mechanism.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("siril-manual-color-calibration-p22",),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="BackgroundNeutralization process; not currently dispatched by NOVA for this step",
            host="PixInsight BackgroundNeutralization",
            instructions=(
                "Select a background reference region using the same "
                "true-sky discipline as any other background-neutralization "
                "tool -- darkest is not automatically cleanest.",
                "Treat this as the same process goal with a different host "
                "implementation; this packet did not establish a production "
                "NOVA-to-PixInsight dispatch path for this step, so no "
                "direct execution equivalence is claimed.",
            ),
            controls_and_starting_ranges=(
                ("background reference", "manually or ROI-selected region representing true sky"),
            ),
            expected_result="Channel-referenced background correction toward neutral in the selected reference.",
            failure_modes=("Selecting a background reference that is dark but not actually clean sky.",),
            recovery=("Choose a different reference region; compare against a no-op.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("pixinsight-resources-p22",),
        ),
    ),
    measurements=(
        "Defined background ROI channel ratios (G/R, B/R) before and after, "
        "in the exact region used to evaluate the step -- not whole-frame "
        "medians.",
        "Spatial consistency across several independently selected sky ROIs, "
        "not only one corner aggregate.",
        "Change map / residual map by channel, showing where the step altered "
        "the frame.",
        "Sky-level/headroom/clipping change per channel.",
        "Matched unsaturated-star color ratios, to check whether a "
        "background-only correction distorts calibrated stellar colors.",
        "Target-region color ratios, checked separately from background, "
        "especially in emission/narrowband fields.",
        "A no-op paired comparison on the exact same parent input.",
    ),
    acceptance_criteria=(
        "Measured dark-corner sky cast drops without stronger corner "
        "neutrality being achieved by crushing one channel.",
        "Matched unsaturated star colors and target-region color ratios show "
        "no unexplained systematic shift.",
        "No new opposite-sign cast (e.g., a corrected green cast reappearing "
        "as magenta) elsewhere in the frame.",
        "The effective mode, selected reference rectangle, and whether the "
        "signal-preserving mask activated are recorded -- candidate ID alone "
        "is not sufficient evidence.",
        "A flatter-looking corner is never accepted as gradient-correction "
        "evidence without an independent, matched background-extraction "
        "comparison.",
    ),
    sources=(
        P22_PACKET,
        NOVA_BACKGROUND_NEUTRALIZE_SOURCE,
        M42_MASK_EVIDENCE,
        SASPRO_BACKGROUNDNEUTRAL_SOURCE,
        SIRIL_MANUAL_COLOR_CALIBRATION,
        PIXINSIGHT_RESOURCES,
    ),
)

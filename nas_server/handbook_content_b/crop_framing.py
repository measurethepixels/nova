"""Public Handbook guidance for crop and framing.

Synthesized from Phase-2 research packet P08 (2026-08-27). Ships last among
Group B's process families (after B4's registration/coverage-map article and
B5's stacking article) because crop decides which of the registered/stacked
result's pixels to keep, not the other way around.

The packet's most serious finding is carried through explicitly rather than
smoothed over: several current Experiment Mode crop candidate labels do not
execute what their names claim (`crop_artifact` and `crop_canonical` both
currently call the same `crop_multi` invocation; `crop_coverage` and
`crop_intersection` both currently call the legacy image-derived crop, not
NOVA's true registered-frame count-coverage map). Per #507's Group B gate,
that execution-identity defect is described here as current behavior, not
implemented or fixed by this content, and is tracked as a separate issue.
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

P08_PACKET = EvidenceReference(
    reference_id="packet-p08",
    title="Phase-2 research packet P08, 2026-08-27",
    locator="Handbook P08 — Crop and Framing",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

NOVA_CROP_SOURCE = EvidenceReference(
    reference_id="nova-crop-source",
    title="NOVA crop, framing, and canonical-reprojection implementation",
    locator=(
        "nas_server/seti_astro.py (crop, crop_multi, _artifact_trim_bounds); "
        "nas_server/canonical_frame.py (canonical_target_wcs, reproject_interp); "
        "nas_server/crop_review.py; nas_server/target_crop.py; "
        "nas_server/auto_process.py (crop branch, Experiment Mode routing, "
        "objective-gate exemption)"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_M102_CRITIQUE = EvidenceReference(
    reference_id="nova-m102-crop-critique",
    title="M 102 crop-subject-loss critiques, 2026-07-18",
    locator=(
        "critiques/20260718_182327_seestar_galaxy.md; "
        "critiques/20260718_192500_seestar_galaxy.md"
    ),
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

NOVA_REMOTE_REVIEW_INCIDENT = EvidenceReference(
    reference_id="nova-remote-crop-review-incident",
    title="Remote-worker crop-review hang and the VM-only invariant (issues #397, #399)",
    locator="issue #397; issue #399",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

ASTROPY_WCS_DOCUMENTATION = EvidenceReference(
    reference_id="astropy-wcs-docs-crop",
    title="Astropy WCS documentation",
    locator="https://docs.astropy.org/en/stable/wcs/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

REPROJECT_DOCUMENTATION = EvidenceReference(
    reference_id="reproject-docs-crop",
    title="reproject image reprojection documentation",
    locator="https://reproject.readthedocs.io/en/stable/howto/images.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

SIRIL_FRAMING_DOCUMENTATION = EvidenceReference(
    reference_id="siril-framing-docs-crop",
    title="Siril 1.4.4 sequence registration framing (min/max) documentation",
    locator="https://siril.readthedocs.io/en/stable/Commands.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)


CROP_FRAMING = HandbookArticle(
    article_id="crop-and-framing",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.CROP_FRAMING,
    purpose=(
        "Decide which registered pixels are worth keeping and what sky composition "
        "becomes the target's repeatable frame -- a constrained framing decision, not "
        "an area-maximization or generic image-quality problem."
    ),
    observable_symptoms=(
        "Black bands, ragged edges, or high-noise strips survive at the stack's border.",
        "A mosaic's outer wings carry far less registered-frame support than its center.",
        "A small or elongated target sits too tight in frame, or a bright core crowds out a faint extended envelope.",
        "The same target's framing appears to drift, or fails to reproduce, across independently captured sessions.",
    ),
    intended_output=(
        "A linear FITS containing the selected sky region, with valid WCS where a WCS "
        "path was used, enough retained subject and context for downstream processing, "
        "no avoidable no-data wedges from the framing transform, and a persisted "
        "target-level record of how the frame was chosen."
    ),
    limits=(
        "Retained area is a descriptive geometry fact, never a quality score: the largest crop can be wrong and the smallest clean crop can be wrong.",
        "A positive-looking coverage or artifact metric does not establish that the subject and its context survived framing.",
        "Reprojection onto a canonical sky box is coordinate-normalizing interpolation, not a lossless pixel crop and not automatically flux-conserving.",
        "Several current Experiment Mode crop candidate labels do not execute what their names claim (see Experiment Mode section) -- a recorded historical label is not proof of which geometry actually ran.",
        "The project's own S50-resolution finding applies here directly: SeeStar S50 data cannot afford an over-aggressive crop given its native resolution, which is why a native-resolution floor exists to override an automatically preferred candidate that would otherwise be too small.",
    ),
    required_input_state=(
        "A linear, registered/stacked science image in NOVA's normal path.",
        "Knowledge of whether a real registered-frame count-coverage map exists and is aligned with the stack.",
        "Knowledge of whether celestial WCS is present and trustworthy enough to define a repeatable sky box.",
        "Target canonical identity and catalog center/size where canonical framing is being considered.",
        "Knowledge of whether the data are mosaic/max-framing or a single ordinary pointing, and whether a saved target crop already exists.",
    ),
    nova_action=(
        "On a target's first normal run, NOVA generates multiple candidate framings -- "
        "artifact trim, canonical WCS frame, count-coverage, intersection, and "
        "image-derived largest-inscribed-rectangle variants -- and opens a VM-hosted "
        "human review; the operator selects a generated candidate or draws/rotates a "
        "manual crop, and that choice is persisted primarily as a WCS sky box (center "
        "RA/Dec, size, scale, position angle), with fractional pixel bounds as a "
        "fallback. Later normal runs reproject the current stack onto that saved sky "
        "box rather than re-selecting from scratch; a forced recrop returns the target "
        "to the review branch. A native-resolution floor -- the larger of 900 pixels "
        "or 45% of the input frame's short side -- can override the preferred candidate "
        "if it would otherwise be too small. Because reframing changes field of view, "
        "the standard whole-frame objective-quality gate explicitly does not evaluate crop."
    ),
    nova_evidence_ids=(
        "nova-crop-source", "nova-m102-crop-critique",
        "nova-remote-crop-review-incident", "packet-p08",
    ),
    use_when=(
        "Registration or integration has left black, ragged, or high-noise borders that a chosen support criterion should remove.",
        "A mosaic or max-framing result contains weakly supported outer regions worth trimming honestly.",
        "A stable, reproducible frame is required across future sessions on the same target.",
        "Corrected WCS or target metadata makes a previously persisted crop worth revisiting.",
    ),
    skip_when=(
        "A valid saved human crop already matches the current capture geometry.",
        "No candidate can demonstrate that the subject and its required context survive the crop.",
        "WCS or target metadata are too uncertain to trust a canonical sky box, and image-derived heuristics risk confusing real extended signal with a border artifact.",
        "Changing the frame would break a measurement comparison that depends on identical field of view.",
    ),
    scientific_and_aesthetic_notes=(
        "Crop is a constrained optimization -- maximize useful retained subject and context subject to edge/support reliability, required target extent, useful sampling, valid coordinates, and no unacceptable resampling artifacts -- not a single number to maximize or minimize.",
        "\"Coverage\" is overloaded in the current implementation: true registered-frame count coverage (from the stacker's coverage map) and the image-derived blank/noise heuristic used by legacy auto-crop are different evidence classes that happen to share a name.",
        "An all-frame intersection is not automatically the safest choice -- one shifted or rotated frame can shrink the common rectangle far more than the support gain is worth.",
        "A canonical WCS frame trades quality for repeatability: it is stable across sessions when target metadata are correct, but the current implementation discards the reprojection footprint and can zero-fill area beyond the actual supported input.",
        "Manual composition legitimately captures subject-preservation and context judgments that none of NOVA's current automated metrics fully express; treating human review as a workaround rather than a real acceptance dimension misdescribes the design.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova", tool_version="current source", host="NOVA crop and framing",
            instructions=(
                "On first normal run, generate the candidate set and review the proposed frames against subject and context, not only against border cleanliness.",
                "Prefer the true registered-frame count-coverage map over image-derived border heuristics when both are available for a support-based decision.",
                "Persist the accepted frame as a WCS sky box where possible; verify the native-resolution floor did not silently override a canonical selection for a small target.",
                "For Experiment Mode use, confirm which geometry actually executed before treating a candidate label as evidence -- see the Experiment Mode note below.",
            ),
            controls_and_starting_ranges=(
                ("count-coverage threshold", "0.80 minimum-tile-count fraction (engineering threshold, not a physical optimum)"),
                ("intersection threshold", "0.999 minimum-tile-count fraction (effectively all-frame support)"),
                ("image-derived LIR preset", "conservative / balanced / aggressive noise and blank-fraction thresholds"),
                ("native-resolution floor", "max(900 px, 45% of input frame's short side)"),
            ),
            expected_result="A linear FITS on the selected sky region with valid WCS where used, sufficient retained subject/context, and a persisted target-level framing record.",
            failure_modes=(
                "A technically clean crop removes the subject or its required context (historical M 102 evidence: 93% area loss, subject reduced to a core-like blob).",
                "Intersection framing over-crops because of one outlier frame.",
                "A canonical sky box requests more sky than the input actually supports, producing zero-filled borders.",
                "A generated (non-manual) candidate's fractional fallback cannot reproduce an originally off-center crop when the WCS path is unavailable.",
            ),
            recovery=(
                "Return to the pre-crop registered/stacked image; do not attempt to recover lost sky downstream.",
                "Force a recrop with a support- or context-aware strategy, or a manual frame, once the failure mode is identified.",
                "Validate WCS against sky/catalog geometry before trusting a repeated crop's coordinates.",
            ),
            mask_support="No processing mask; crop operates on the full frame or a stored sky-box region, not a weighted selection.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-crop-source", "nova-m102-crop-critique"),
        ),
        ToolGuidance(
            tool_id="pixinsight", tool_version="unverified installed version", host="PixInsight DynamicCrop / mosaic tools",
            instructions=(
                "Define the retained region against the same subject/context and support evidence used elsewhere in this article, not composition alone.",
                "Record the exact bounds, rotation, and interpolation used so the result is reproducible.",
                "Treat this as a manual, one-time pixel selection unless it is deliberately reconciled with NOVA's WCS sky-box semantics.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A documented alternative crop; exact equivalence to NOVA's persistent sky-box framing is not claimed.",
            failure_modes=("A pleasing composition is accepted without checking retained support or coordinate validity.",),
            recovery=("Re-derive the crop from the registered/stacked source with explicit bounds recorded.",),
            mask_support="No mask behavior claimed for this path.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
        ToolGuidance(
            tool_id="siril", tool_version="1.4.4 documentation", host="Siril sequence framing",
            instructions=(
                "Use Siril's own min/max sequence-framing choice at registration time to establish intersection or union geometry (see the Registration article); this is upstream of, and distinct from, NOVA's post-stack candidate selection.",
                "If a further crop is applied after stacking, record its bounds and rotation explicitly.",
            ),
            controls_and_starting_ranges=(("sequence framing", "min (intersection) or max (union), set at registration"),),
            expected_result="A registered/stacked sequence whose extent matches the declared framing choice, suitable as crop input.",
            failure_modes=("Min/max framing at registration time is conflated with NOVA's own post-stack crop candidates.",),
            recovery=("Treat Siril's framing choice as upstream geometry input to crop, not as the crop decision itself.",),
            mask_support="Not applicable at this stage.",
            equivalence=EquivalenceClass.CONCEPTUAL_SUBSTITUTE,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED,),
            source_ids=("siril-framing-docs-crop",),
        ),
        ToolGuidance(
            tool_id="saspro", tool_version="unverified installed version", host="Seti Astro Suite Pro",
            instructions=(
                "Use a documented crop/framing workflow only after confirming it on the installed version.",
                "Record retained bounds, rotation, and support evidence rather than relying on visual composition alone.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A documented alternative crop; exact equivalence to NOVA's path is not claimed.",
            failure_modes=("An unverified menu path is presented as an established equivalent.",),
            recovery=("Verify the installed tool's actual behavior before publication.",),
            mask_support="No mask behavior claimed for this unverified path.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
    ),
    measurements=(
        "Retained sky/area fraction -- descriptive geometry only, never an acceptance criterion by itself.",
        "Minimum and low-percentile registered-frame count coverage inside the crop, when a true coverage map exists.",
        "Subject containment and margin against trusted target world coordinates and, where possible, catalog or segmentation-based extent.",
        "WCS center, scale, and orientation residuals for canonical or saved-box outputs.",
        "Supported-output fraction from the reprojection footprint where available, to catch zero-filled unsupported area.",
        "Cross-session repeatability: reapplying a saved frame to independently captured sessions and comparing sky-coordinate bounds.",
    ),
    acceptance_criteria=(
        "The retained frame meets the selected support/edge-reliability criterion without unexplained black or high-noise borders.",
        "Required target extent and its scientifically or visually important context remain inside the frame with adequate margin.",
        "WCS correctly describes the retained or reprojected sky wherever the output claims WCS.",
        "A persisted target framing reproduces the same intended sky region on a later session, or fails explicitly rather than silently drifting.",
        "No conclusion about which crop strategy is better rests on whole-frame SNR, background, FWHM, eccentricity, gradient severity, entropy, or a generic visual/AI score alone.",
    ),
    sources=(
        P08_PACKET, NOVA_CROP_SOURCE, NOVA_M102_CRITIQUE, NOVA_REMOTE_REVIEW_INCIDENT,
        ASTROPY_WCS_DOCUMENTATION, REPROJECT_DOCUMENTATION, SIRIL_FRAMING_DOCUMENTATION,
    ),
)

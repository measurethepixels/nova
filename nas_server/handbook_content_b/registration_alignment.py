"""Public Handbook guidance for registration and mosaic geometry."""

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


P07_PACKET = EvidenceReference(
    reference_id="packet-p07",
    title="Phase-2 research packet P07, 2026-08-27",
    locator="Handbook P07 — Registration, Alignment, Coverage Maps, and Mosaics",
    provenance=ProvenanceLabel.REASONED_TRANSLATION,
)

NOVA_GEOMETRY_SOURCE = EvidenceReference(
    reference_id="nova-registration-coverage-source",
    title="NOVA registration, max-framing, and coverage-map implementation",
    locator=(
        "nas_server/stacker.py; nas_server/canonical_frame.py; "
        "nas_server/seestar_register.ssf; nas_server/seestar_register_maxframing.ssf; "
        "nas_server/pi_register_and_drizzle.js"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_WCS_AUDIT = EvidenceReference(
    reference_id="nova-wcs-consumer-audit",
    title="NOVA WCS consumer audit and coordinate-lineage incident",
    locator="docs/audit_wcs_consumers.md (2026-07-02 incident)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

SIRIL_DOCUMENTATION = EvidenceReference(
    reference_id="siril-registration-docs-1.4.4",
    title="Siril 1.4.4 registration and sequence-command documentation",
    locator="https://siril.readthedocs.io/en/stable/Commands.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

ASTROPY_WCS_DOCUMENTATION = EvidenceReference(
    reference_id="astropy-wcs-docs",
    title="Astropy WCS documentation",
    locator="https://docs.astropy.org/en/stable/wcs/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

REPROJECT_DOCUMENTATION = EvidenceReference(
    reference_id="reproject-0.21.0-docs",
    title="reproject 0.21.0 celestial reprojection documentation",
    locator="https://reproject.readthedocs.io/en/stable/celestial.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

PIXINSIGHT_DOCUMENTATION = EvidenceReference(
    reference_id="pixinsight-star-alignment-docs",
    title="PixInsight StarAlignment distortion and mosaic tutorial",
    locator="https://www.pixinsight.com/tutorials/sa-distortion/index.html",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)


REGISTRATION_ALIGNMENT = HandbookArticle(
    article_id="registration-alignment-coverage-mosaics",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.REGISTRATION_ALIGNMENT,
    purpose=(
        "Place eligible exposures on a common sky grid while retaining an honest record "
        "of their overlap, so later integration and crop decisions compare like with like."
    ),
    observable_symptoms=(
        "Stars double, trail, or leave structured subtraction residuals after alignment.",
        "A mosaic preserves its full field but its outer wings have fewer contributing frames than its center.",
        "A FITS file has WCS metadata even though stars do not coincide accurately at subpixel scale.",
        "A visible panel seam remains after geometric alignment and may instead reflect background, color, PSF, or sampling differences.",
    ),
    intended_output=(
        "Registered samples on one declared output grid, plus an aligned occupancy/count "
        "map showing how many finite, non-zero registered frames support each pixel."
    ),
    limits=(
        "WCS presence is not proof of registration accuracy; a solution can be stale, offset, mirrored, or otherwise wrong.",
        "NOVA's coverage map counts valid registered samples. It is not exposure-weighted coverage, SNR, rejection confidence, or uncertainty.",
        "The coverage FITS remains pixel-aligned with the stack but is not independently sky-located by a copied science WCS header.",
        "Finite-and-nonzero occupancy is a practical border heuristic, not exact fractional detector exposure.",
        "A seam is not a registration metric by itself; background, color, PSF, sampling, and support can also change across panels.",
        "Simple interpolation reprojection is not automatically flux conserving, especially when pixel scales differ.",
    ),
    required_input_state=(
        "Eligible calibrated light frames whose CFA/debayer, linearity, calibration, and processing-history state is known.",
        "Reliable image scale and either trustworthy plate solutions or sufficient common stars for the selected alignment model.",
        "A declared reference grid and framing goal: common intersection, union/max extent, or another bounded geometry.",
        "Mosaic panel/product identity kept distinct from canonical astronomical target identity.",
    ),
    nova_action=(
        "For ordinary Siril stacks, NOVA plate-solves frames and applies registration on a "
        "common grid. Mosaic-associated or mosaic-flagged targets force max framing so the "
        "union extent is retained; that path preserves WCS-derived cross-panel offsets rather "
        "than overwriting them with ordinary two-pass star matching. PixInsight registration "
        "uses Debayer and StarAlignment, while the synthetic canonical Gaia reference is a "
        "single-pointing strategy and is skipped for mosaics. After stacking, NOVA builds a "
        "count coverage map from registered frames and replays orientation flips so map and "
        "stack remain pixel-aligned. Image matching remains a fallback capability, not the "
        "claimed normal mosaic path."
    ),
    nova_evidence_ids=("nova-registration-coverage-source", "nova-wcs-consumer-audit", "packet-p07"),
    use_when=(
        "Combining dithered, rotated, cross-session, or multi-panel exposures that must describe one output pixel geometry.",
        "Preserving a mosaic union or diagnosing where registered inputs have spatially varying support.",
        "Testing a registration path with transform residuals and output-state evidence before integration conclusions are drawn.",
    ),
    skip_when=(
        "The frames do not share trustworthy sky geometry and neither plate solving nor common-star matching establishes it.",
        "A proposed comparison would give one candidate an extra resampling pass without treating that pass as part of the strategy.",
        "Coverage count alone is being used to infer photometric quality, SNR, or the best crop.",
    ),
    scientific_and_aesthetic_notes=(
        "Transform accuracy and interpolation quality are separate: a correct transform can still soften or ring, and a refined kernel cannot repair a wrong transform.",
        "Registration is a state transition because non-integer shifts, rotation, scale changes, distortion correction, or reprojection resample the data.",
        "Repeated resampling changes the sampled representation. The Measuring an Astrophoto page's when-comparisons-are-invalid guidance governs downstream metric comparability.",
        "Intersection framing favors uniformly supported area; union/max framing preserves field extent and necessarily exposes lower-coverage borders.",
        "A mosaic edge has less integration depth when fewer registered frames cover it, even when its geometry is correct.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova", tool_version="current source", host="NOVA stack registration",
            instructions=(
                "Confirm input state, target/panel identity, and the intended intersection or union geometry.",
                "Use the ordinary plate-solved path for one pointing and max framing for mosaic geometry; preserve the exact frame manifest.",
                "Inspect registered-star residuals and verify that the final stack and coverage map remain pixel-aligned after orientation normalization.",
            ),
            controls_and_starting_ranges=(("framing", "common/intersection for uniform overlap; max/union for mosaic extent"),),
            expected_result="A common output grid with all intended panels represented and an aligned occupancy/count map.",
            failure_modes=("Wrong or stale WCS displaces frames.", "Sparse panel overlap defeats image matching.", "Union borders are mistaken for uniform support."),
            recovery=("Re-solve and inspect coordinate lineage.", "Use an appropriate WCS-preserving mosaic path.", "Review coverage before crop or stack comparison."),
            mask_support="The coverage count map describes registered-sample occupancy; it is not a processing mask or weight map.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED,),
            source_ids=("nova-registration-coverage-source", "nova-wcs-consumer-audit"),
        ),
        ToolGuidance(
            tool_id="pixinsight", tool_version="1.9.3 project snapshot", host="PixInsight StarAlignment",
            instructions=(
                "Choose a defensible reference and register the debayered frames on one output grid.",
                "Enable a more flexible distortion model only when field evidence justifies it.",
                "Inspect matched-star or subtraction residuals and retain the registered-frame manifest.",
            ),
            controls_and_starting_ranges=(("pixel interpolation", "Auto with clamping in NOVA's current script; record the actual setting"),),
            expected_result="Registered frames whose residuals and retained extent are measured under the declared StarAlignment configuration.",
            failure_modes=("An unnecessarily flexible model fits centroid noise.", "Residuals or edge support are judged only from a beauty image."),
            recovery=("Return to a simpler model and compare residuals.", "Inspect difference/residual images and coverage."),
            mask_support="Record any reference support or rejection mask because it changes the stars used to estimate the transform.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.VENDOR_DOCUMENTED),
            source_ids=("nova-registration-coverage-source", "pixinsight-star-alignment-docs"),
        ),
        ToolGuidance(
            tool_id="siril", tool_version="1.4.4 documentation", host="Siril sequence registration",
            instructions=(
                "Plate-solve or match a coherent sequence and estimate its transforms.",
                "Apply registration with min/common or max/union framing according to the declared goal.",
                "Record interpolation, drizzle, reference, framing, and software version before comparing outputs.",
            ),
            controls_and_starting_ranges=(("interpolation", "record the chosen nearest, linear, cubic, Lanczos4, area, or no-interpolation mode"),),
            expected_result="A registered Siril sequence with an explicit extent and reproducible resampling state.",
            failure_modes=("Version-specific behavior is generalized to every Siril 1.4.x release.", "Star matching destroys meaningful cross-panel WCS offsets."),
            recovery=("Record and validate the deployed Siril version.", "Use the WCS-preserving max-framing path for sparse-overlap mosaics."),
            mask_support="Sequence validity and output footprint define support; do not call it SNR coverage.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("siril-registration-docs-1.4.4", "nova-registration-coverage-source"),
        ),
        ToolGuidance(
            tool_id="saspro", tool_version="unverified installed version", host="Seti Astro Suite Pro",
            instructions=(
                "Use a documented registration or mosaic workflow only after confirming it on the installed version.",
                "Record reference, output extent, interpolation, and retained-frame identities.",
                "Compare matched-star residuals and spatial support against the same input pool.",
            ),
            controls_and_starting_ranges=(),
            expected_result="A documented alternative registered set; exact equivalence to NOVA's path is not claimed.",
            failure_modes=("An unverified control or menu path is presented as established.", "A visual seam is treated as registration error without residual evidence."),
            recovery=("Verify the installed tool workflow before publication.", "Separate geometry residuals from background, color, PSF, and coverage diagnostics."),
            mask_support="No mask behavior is claimed for this unverified path.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.UNKNOWN_UNVERIFIED,),
            source_ids=(),
        ),
    ),
    measurements=(
        "Robust matched-star residuals in pixels or arcseconds, with the matching and extraction procedure held constant.",
        "Catalog-to-transformed-star WCS residuals for plate-solved frames.",
        "Coverage count/fraction and area above declared occupancy thresholds, explicitly under NOVA's finite-and-nonzero definition.",
        "Pre/post-registration FWHM and eccentricity only at matched scale, field, star population, and extraction settings.",
        "Seam-region background, color, PSF, and support diagnostics reported separately from registration residuals.",
    ),
    acceptance_criteria=(
        "Registration residuals are bounded for the named path, dataset, software version, transform, and interpolation settings.",
        "All intended mosaic panels appear in the declared max-framing union without claiming uniform edge depth.",
        "Coverage map and final stack remain pixel-aligned after orientation normalization, and coverage is labeled occupancy/count only.",
        "Comparisons do not attribute bundled registration-plus-integration differences to registration alone.",
        "No conclusion relies on WCS presence, retained area, raw star count, or visual seam preference as a standalone quality proof.",
    ),
    sources=(
        P07_PACKET, NOVA_GEOMETRY_SOURCE, NOVA_WCS_AUDIT, SIRIL_DOCUMENTATION,
        ASTROPY_WCS_DOCUMENTATION, REPROJECT_DOCUMENTATION, PIXINSIGHT_DOCUMENTATION,
    ),
)

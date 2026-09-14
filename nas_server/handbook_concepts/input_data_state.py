"""Group B1 concept page: recognizing an image's provenance and state before
choosing a tool or interpreting a metric.

Synthesized from Phase-2 research packet P04 (2026-08-27). Despite being
filed under Handbook Phase 3's "Group B" (data state/calibration/geometry/
pre-stack), this page's own research explicitly recommends the Group A
ConceptArticle shape, not a HandbookArticle: there is no single tool that
produces "input data state" -- it is a precondition every later Group B
process page assumes, the same way Group A's evidence vocabulary is a
precondition every process page cites.
"""

from __future__ import annotations

from nas_server.handbook_contract import (
    SCHEMA_VERSION,
    Claim,
    ConceptArticle,
    ConceptSection,
    EvidenceOrigin,
    EvidenceReference,
    ProcessFamily,
    ProvenanceLabel,
)

_SOURCES = (
    EvidenceReference(
        "packet-p04",
        "Phase-2 research packet P04, 2026-08-27",
        "packet:P04:2026-08-27",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "how-nova-knows-page",
        "Handbook concept page: How NOVA Knows",
        "handbook:how-nova-knows",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "experiment-mode-page",
        "Handbook concept page: Experiment Mode",
        "handbook:experiment-mode",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-is-raw-stack",
        "NOVA raw-stack safety predicate (database.is_raw_stack)",
        "nas_server/database.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "packet-p36",
        "Phase-2 research packet P36, 2026-08-28",
        "packet:P36:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "fits-wcs-standard",
        "FITS World Coordinate System standard",
        "https://fits.gsfc.nasa.gov/fits_wcs.html",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "container-is-not-processing-state",
        "A FITS file describes a container, not a processing state. Knowing "
        "what produced an artifact and what transformations it has already "
        "undergone matters more than its file extension or on-screen "
        "appearance.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04",),
    ),
    Claim(
        "native-and-pipeline-stacks-are-different-provenance-classes",
        "A native SeeStar stack (produced by the telescope/app during "
        "capture) and a NOVA pipeline stack (produced by NOVA's own stacker) "
        "are different provenance classes even when they represent the same "
        "target -- the integration engine, frame selection, registration, "
        "rejection, and normalization can all differ between them.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04",),
    ),
    Claim(
        "mosaic-is-an-attribute-not-a-second-target",
        "Mosaic is a geometry and provenance attribute, not a second "
        "astronomical target and not a fourth product class alongside "
        "subframe, stack, and processed result. Treating a mosaic suffix as "
        "a separate canonical target fragments provenance that belongs "
        "together.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04",),
    ),
    Claim(
        "broadband-and-dual-band-are-different-spectral-inputs",
        "Broadband/UV-IR-cut and Ha/OIII dual-band SeeStar data are "
        "different spectral inputs. Their channel ratios and the downstream "
        "color assumptions built on them are not directly interchangeable, "
        "even when both are nominally the same filter product.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04",),
    ),
    Claim(
        "linear-can-look-bright-without-being-nonlinear",
        "Linear data can be displayed brightly with a screen transfer "
        "function or autostretch without the stored pixels becoming "
        "nonlinear. On-screen appearance alone is not evidence of "
        "processing state.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04",),
    ),
    Claim(
        "wcs-metadata-still-needs-validation-confidence",
        "WCS metadata states how pixels are claimed to map to sky "
        "coordinates -- it does not by itself prove that claim is correct "
        "for this image and field. A WCS-dependent operation still needs "
        "independent confidence that the recorded solution actually belongs "
        "to the data it's attached to.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("fits-wcs-standard", "packet-p04"),
    ),
    Claim(
        "safety-predicate-and-provenance-label-answer-different-questions",
        "A raw-input safety predicate and a user-facing provenance label "
        "answer different questions. The label describes what a product is; "
        "the safety rule decides whether it is allowed to enter a raw "
        "processing pipeline -- NOVA's own `is_raw_stack()` check exists "
        "specifically because a plausible-looking label is not sufficient "
        "grounds to skip that decision.",
        EvidenceOrigin.EXECUTION_CONFIRMED,
        ("nova-is-raw-stack",),
    ),
    Claim(
        "scientific-intermediate-and-presentation-export-are-different-classes",
        "A processed scientific/intermediate artifact retained for further "
        "processing or comparison is a different product class from a "
        "presentation export (JPEG, PNG, or a color-managed TIFF) generated "
        "for viewing -- the two can look similar or even come from the same "
        "result, but clipping, normalization, resizing, gamma/display "
        "transforms, or color-profile conversion applied for presentation "
        "make that export unsafe to substitute as the parent for a later "
        "scientific comparison.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "packet-p04"),
    ),
    Claim(
        "experiment-comparisons-need-shared-scientific-state",
        "An Experiment Mode comparison is interpretable only when its "
        "candidate inputs share the relevant scientific state, or when the "
        "differing state is explicitly the strategy being tested -- the "
        "same same-input-branching fairness principle Experiment Mode "
        "itself is built around, applied specifically to the input data's "
        "own state.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p04", "experiment-mode-page"),
    ),
)

_UNRESOLVED_GATES = (
    "Whether NOVA's NINA-sourced light subframes are fully supported "
    "end to end is not established -- treat that path as source-confirmed "
    "scaffolding, not a validated production path, until real end-to-end "
    "evidence exists.",
    "Whether NOVA supports generic mono/SHO acquisition and stacking is "
    "not established -- narrowband composition code exists, but that is "
    "processing support, not proof of validated mono-channel capture and "
    "stack ingestion.",
    "How stored per-frame metric caches (FWHM, eccentricity, and similar) "
    "get invalidated when the state-detection or extraction logic itself "
    "changes version is not fully specified.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="input-data-state",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Know Your Starting Point",
    subtitle=(
        "Image data, provenance, and processing state -- what NOVA needs "
        "to know before choosing a tool or interpreting a metric"
    ),
    summary=(
        "A file's container, name, and appearance are not enough to decide "
        "how to process it. A valid processing decision depends on "
        "acquisition provenance, spectral response, calibration, geometry, "
        "registration, and whether the data are still linear."
    ),
    sections=(
        ConceptSection(
            "what-file-is-this-is-a-scientific-question",
            "Why 'what file is this?' is a scientific question",
            (
                "Processing assumptions depend on provenance and state, not "
                "on a file's extension or how it looks on screen. The same "
                "FITS container can hold a raw light subframe, a native "
                "telescope stack, a NOVA pipeline stack, a partially "
                "processed intermediate, or a finished result -- and each "
                "one requires different handling. Reading the file is not "
                "the same as knowing what it is.",
            ),
        ),
        ConceptSection(
            "the-product-ladder",
            "The product ladder",
            (
                "Data moves through a real sequence: light subframes are "
                "calibrated, registered, and integrated into a native "
                "SeeStar stack or a NOVA pipeline stack; linear processing "
                "and restoration produce linear intermediates, including a "
                "star/starless split; a stretch produces nonlinear "
                "intermediates; finishing and recombination produce the "
                "processed final. A mosaic is a badge that can span any of "
                "these rows -- it is not a separate fourth branch of the "
                "ladder.",
            ),
        ),
        ConceptSection(
            "the-state-card",
            "The state card",
            (
                "A complete description of an image's state names its "
                "source, product class, filter, calibration provenance, "
                "channel/spectral regime, linearity, WCS and registration "
                "status, mosaic/geometry attributes, stack provenance, and "
                "processing history so far. Product class itself has a "
                "boundary worth naming explicitly: a processed scientific/ "
                "intermediate artifact kept for further processing or "
                "comparison is not the same class as a presentation export "
                "made for viewing, even when both were rendered from the "
                "same result. Any one of these left unknown is itself a "
                "fact worth recording, not something to guess past.",
            ),
        ),
        ConceptSection(
            "broadband-dual-band-and-mono",
            "Broadband, dual-band, and mono",
            (
                "Broadband/UV-IR-cut and Ha/OIII dual-band SeeStar data are "
                "different spectral inputs, and mono/discrete-narrowband "
                "data is different again. Downstream color and metric "
                "assumptions that hold for one spectral regime do not "
                "automatically transfer to another, even when the target "
                "and telescope are the same.",
            ),
        ),
        ConceptSection(
            "linear-is-not-dark",
            "Linear is not 'dark'",
            (
                "A screen transfer function or autostretch can display "
                "linear data brightly for viewing without changing the "
                "stored pixels at all -- appearance is a display choice, "
                "not state evidence. Judging whether data is linear by how "
                "it looks on screen is exactly the mistake this page exists "
                "to prevent.",
            ),
        ),
        ConceptSection(
            "wcs-present-versus-wcs-trusted",
            "WCS present versus WCS trusted",
            (
                "WCS metadata is a claim about how pixels map to sky "
                "coordinates, not proof that the claim is correct for this "
                "specific image and field. A WCS-dependent operation needs "
                "independent confidence that the recorded solution actually "
                "belongs to the data it's attached to before relying on it.",
            ),
        ),
        ConceptSection(
            "native-stack-versus-pipeline-stack",
            "Native stack versus pipeline stack",
            (
                "A native SeeStar stack and a NOVA pipeline stack can "
                "represent the same target while being genuinely different "
                "provenance classes -- the integration engine, frame "
                "selection, registration, rejection, and normalization "
                "history are not guaranteed to match. They are not "
                "interchangeable controls in an experiment; comparing them "
                "directly is a strategy comparison, not a same-input "
                "comparison.",
            ),
        ),
        ConceptSection(
            "experiment-mode-fairness-checklist",
            "Experiment Mode fairness checklist",
            (
                "Before trusting any Experiment Mode winner language, check "
                "that the candidate inputs actually share the relevant "
                "scientific state -- or that the differing state is "
                "explicitly what the experiment is testing. A comparison "
                "that silently mixes native and pipeline stacks, or "
                "broadband and dual-band data, is not measuring what it "
                "claims to measure.",
            ),
        ),
        ConceptSection(
            "when-nova-is-uncertain",
            "When NOVA is uncertain",
            (
                "Provenance and state facts fall into honest categories: "
                "known, source-declared, heuristically inferred, and "
                "unknown. A heuristic like a filename pattern or a pixel "
                "percentile can support an inference, but it is not the "
                "same claim as a recorded, source-confirmed fact -- and "
                "'unknown' is a legitimate, useful answer rather than "
                "something to paper over.",
            ),
        ),
        ConceptSection(
            "when-to-check-and-when-to-stop",
            "When to check, and when to stop rather than infer",
            (
                "A state check matters before choosing a processing "
                "workflow, reprocessing an existing file, starting "
                "Experiment Mode, comparing results quantitatively, or "
                "treating an external or manually supplied artifact as a "
                "raw pipeline input. Stop rather than infer when product "
                "provenance is unknown, when linearity is ambiguous ahead "
                "of a linear-only step, when a calibration source is "
                "unknown for a calibration-sensitive decision, or when the "
                "only evidence for raw status is that a filename happens to "
                "contain the word 'stack.'",
            ),
        ),
    ),
    claims=_CLAIMS,
    unresolved_gates=_UNRESOLVED_GATES,
    related_process_families=(
        ProcessFamily.PEDESTAL_REMOVAL,
        ProcessFamily.COSMETIC_CORRECTION,
        ProcessFamily.BACKGROUND_EXTRACTION,
        ProcessFamily.COLOR_CALIBRATION,
        ProcessFamily.DECONVOLUTION,
        ProcessFamily.DENOISE,
        ProcessFamily.STAR_CORRECTION,
        ProcessFamily.STRETCH,
    ),
    sources=_SOURCES,
    validation_event_ids=(),
)

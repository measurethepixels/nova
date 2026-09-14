"""Group B2 concept page: what calibration removes before stacking, what a
pedestal protects, and what post-processing cannot repair afterwards.

Synthesized from Phase-2 research packet P05 (2026-08-27), with the Pedestal
Removal delta from packet P38 (2026-08-28). Like B1's "Know Your Starting
Point", this page takes the ConceptArticle shape rather than a
HandbookArticle: calibration is not one tool NOVA runs, it is an upstream
state that later process pages depend on. The narrower Pedestal Removal
process article stays where it is and links here; this page does not replace
it and must not be read as a general licence to subtract a floor.
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
        "packet-p05",
        "Phase-2 research packet P05, 2026-08-27",
        "packet:P05:2026-08-27",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "packet-p38",
        "Phase-2 research packet P38, 2026-08-28",
        "packet:P38:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "input-data-state-page",
        "Handbook concept page: Know Your Starting Point",
        "handbook:input-data-state",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "pedestal-removal-article",
        "Handbook process article: Pedestal Removal",
        "handbook:pedestal-removal",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-pedestal-ontology",
        "NOVA processing ontology, remove_pedestal operation",
        "nas_server/processing_ontology.json",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "siril-calibration-docs",
        "Siril calibration documentation",
        "https://siril.readthedocs.io/en/stable/preprocessing/calibration.html",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "pixinsight-dark-calibration",
        "PixInsight dark calibration tutorial",
        "https://pixinsight.com/doc/docs/DC_tutorial/DC_tutorial.pdf",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "pixinsight-output-pedestal-thread",
        "PixInsight forum guidance on output pedestals during calibration",
        "https://pixinsight.com/forum/index.php?threads/flats-not-being-applied.14557/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "pixinsight-cfa-order-thread",
        "PixInsight forum guidance on calibrating CFA data before debayer",
        "https://pixinsight.com/forum/index.php?threads/darks-and-bias-from-a-osc.13582/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "zwo-seestar-dark-calibration",
        "ZWO representative statement that exported SeeStar FITS are dark calibrated",
        "https://bbs.zwoastro.com/d/22423-s50-file-processing-with-asi-studio-asideepstack-biasflatsdarks",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "calibration-and-post-stack-correction-are-different-layers",
        "Calibration and post-stack correction are different layers. "
        "Bias, dark, and flat calibration act upstream on raw sensor frames; "
        "a post-stack residual-offset step is a narrow scalar correction and "
        "is not a substitute for correct calibration.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05", "nova-pedestal-ontology"),
    ),
    Claim(
        "clipping-is-irreversible",
        "Once calibration has clipped low values to zero, adding a pedestal "
        "later cannot reconstruct the lost values or the differences between "
        "them. It only raises zeros that have already lost their original "
        "spacing.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
    Claim(
        "background-is-not-bias",
        "A positive minimum, a positive median, or a raised background is not "
        "enough to identify an electronic offset. Image background is sky, "
        "terrestrial light, detector noise, and processing state combined; "
        "bias is an acquisition property of the detector readout.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05", "pedestal-removal-article"),
    ),
    Claim(
        "flats-are-multiplicative-background-extraction-is-a-model",
        "Flat correction is a multiplicative correction measured from an "
        "optical and sensor reference. Background extraction is a model "
        "inferred from the science image itself. A gradient tool can make "
        "residual vignetting look better without being calibration-equivalent "
        "to a proper flat.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
    Claim(
        "calibrate-cfa-before-debayer",
        "For one-shot-colour data the conceptual order is raw CFA, then "
        "calibration, then debayer, then registration, then integration. "
        "Demosaicing interpolates missing colour samples and changes the "
        "pixel statistics, so calibrating afterwards is no longer acting on "
        "independent raw sensor samples.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("pixinsight-cfa-order-thread", "siril-calibration-docs", "packet-p05"),
        applicability_bound=(
            "Individual engines may fuse commands or save intermediates "
            "differently; the ordering is conceptual, not one fixed command "
            "sequence."
        ),
    ),
    Claim(
        "output-pedestal-protects-against-truncation",
        "An output pedestal added during calibration is bookkeeping that "
        "keeps legitimate low-valued samples representable before truncation. "
        "It is protection against destructive clipping, not added "
        "astrophysical signal.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("pixinsight-output-pedestal-thread", "packet-p05"),
        applicability_bound=(
            "Published numeric examples are tool-, version-, and data-specific "
            "procedural guidance; choose the amount from measured clipping "
            "diagnostics rather than adopting a universal default."
        ),
    ),
    Claim(
        "input-pedestal-and-output-pedestal-are-different-claims",
        "A protective calibration-time output pedestal and a residual "
        "electronic offset in a finished stack are different claims about "
        "different stages. Subtracting a protective pedestal later can "
        "recreate exactly the truncation it was added to prevent.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05", "packet-p38"),
    ),
    Claim(
        "seestar-fits-are-dark-calibrated-not-fully-calibrated",
        "ZWO states that FITS exported by SeeStar are dark calibrated. That "
        "is a useful provenance fact and does not establish that every "
        "exported frame also received independent bias and flat calibration.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("zwo-seestar-dark-calibration", "packet-p05"),
        applicability_bound=(
            "Calibration provenance is version-bound: firmware and capture-app "
            "behaviour can change what a given file actually received."
        ),
    ),
    Claim(
        "seestar-data-cannot-be-assumed-clip-free",
        "A file being produced by SeeStar does not make low-end clipping "
        "impossible. Community incident reports describe clipping and "
        "black-level problems in some firmware-era calibrated files, so "
        "suspicious clipping is worth diagnosing rather than ruling out.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("zwo-seestar-dark-calibration", "packet-p05"),
        applicability_bound=(
            "Incident evidence, not a general specification: it establishes "
            "that version-specific defects can occur, not that any particular "
            "capture is affected."
        ),
    ),
    Claim(
        "nova-starts-from-a-stack",
        "NOVA's post-stack pipeline begins from an already-integrated image, "
        "so CFA-domain calibration and debayer decisions have already been "
        "made upstream by the capture or stacking path. The post-stack "
        "pipeline cannot reconstruct raw-frame calibration from the stack "
        "alone.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05", "input-data-state-page"),
    ),
    Claim(
        "downstream-tools-mitigate-they-do-not-repair",
        "Cosmetic correction can suppress isolated residual defects, "
        "background extraction can model large-scale residual gradients, and "
        "colour calibration can correct channel response -- but none of them "
        "recreate improperly calibrated pixels. The honest description of "
        "downstream cleanup is that it mitigates visible residuals, not that "
        "it repairs calibration.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
    Claim(
        "flatter-background-does-not-prove-better-calibration",
        "A flatter final background does not prove a better flat, lower noise "
        "does not prove better dark calibration, and colour balance is not a "
        "direct flat-quality metric. Each of those can be produced by later "
        "modelling, rejection, or smoothing rather than by calibration.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
    Claim(
        "calibration-comparisons-must-start-from-the-same-frames",
        "A fair calibration comparison starts from identical raw subframes "
        "with an identical inclusion set, matched masters, identical CFA and "
        "debayer ordering, and identical registration and integration "
        "settings afterwards. Comparing finished images from different frame "
        "sets or different downstream settings measures the whole strategy, "
        "not calibration.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
    Claim(
        "calibration-is-validated-on-subframes-not-on-the-final-picture",
        "The strongest acceptance evidence for calibration is measured on the "
        "calibrated subframes -- clipped fraction, structured residuals, CFA "
        "and debayer state -- before any later adaptive processing can hide "
        "the difference. Which final rendering scores higher is not the test.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p05",),
    ),
)

_UNRESOLVED_GATES = (
    "The exact current SeeStar firmware calibration contract is not formally "
    "published; the strongest available evidence is a vendor representative's "
    "statement about dark calibration rather than a full bias/dark/flat "
    "specification.",
    "Archived captures can span multiple firmware versions, and NOVA does not "
    "currently record capture firmware or app version as part of calibration "
    "provenance.",
    "Which calibration steps each stacking path actually performs, bypasses, "
    "or inherits from already-calibrated exported lights is not enumerated "
    "per engine.",
    "Whether a machine-readable output-pedestal keyword and amount survive "
    "consistently across stack and export formats is not established.",
    "Which calibration master paths are automated for mono or externally "
    "captured data is not established; existing narrowband processing support "
    "is not evidence of validated mono calibration ingestion.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="calibration-foundations",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Calibration Foundations",
    subtitle=(
        "Bias, darks, flats, CFA state, and clipping -- what calibration "
        "removes before stacking, what a pedestal protects, and what "
        "post-processing cannot repair"
    ),
    summary=(
        "Calibration is an upstream operation on raw sensor frames, not a "
        "correction applied to a finished stack. Understanding what bias, "
        "dark, and flat calibration each remove -- and what clipping and "
        "demosaicing destroy permanently -- is what makes later processing "
        "decisions interpretable rather than cosmetic."
    ),
    sections=(
        ConceptSection(
            "what-calibration-is-and-is-not",
            "What calibration is, and what it is not",
            (
                "Calibration corrects the detector and the optical path "
                "against measured references before any creative processing "
                "begins. It is not an appearance adjustment, and it is not "
                "something a later step can stand in for. The distinction "
                "matters because several later operations can produce a "
                "superficially similar before-and-after while being "
                "physically different corrections.",
                "One category error is worth naming up front: bias, dark, "
                "and pedestal terms are additive; flats are multiplicative; "
                "debayer is an interpolation and a change of state; cosmetic "
                "correction replaces localised defects; and background "
                "extraction is a model derived from the science image "
                "itself. Treating any of these as interchangeable is how a "
                "calibration problem gets papered over instead of fixed.",
            ),
        ),
        ConceptSection(
            "bias-and-electronic-offset",
            "Bias and electronic offset",
            (
                "A detector readout carries an electronic baseline in "
                "addition to photon-derived signal. A bias or equivalent "
                "short-exposure reference characterises that baseline, "
                "though some CMOS workflows fold it into darks or "
                "flat-darks instead of keeping a separate master bias.",
                "The public distinction that matters most: bias is an "
                "acquisition property of the sensor, while image background "
                "is sky plus terrestrial light plus detector noise plus "
                "whatever processing has already happened. A raised "
                "background is therefore never, by itself, an identification "
                "of an electronic offset.",
            ),
        ),
        ConceptSection(
            "dark-calibration",
            "Dark calibration",
            (
                "Dark frames capture the exposure-, temperature-, and "
                "gain-dependent fixed signal -- dark current and hot-pixel "
                "structure -- along with readout contributions. Whether "
                "subtraction is correct depends on how the masters were "
                "built and whether dark scaling or optimisation is valid for "
                "that particular camera.",
                "For SeeStar data specifically, ZWO has stated that exported "
                "files are dark calibrated. That is a real provenance fact "
                "worth relying on, and it stops short of establishing that "
                "the same files received independent bias and flat "
                "calibration.",
            ),
        ),
        ConceptSection(
            "flat-calibration",
            "Flat calibration",
            (
                "Flats correct multiplicative variation in sensitivity and "
                "illumination: vignetting, pixel-response nonuniformity, "
                "dust shadows, and optical differences across the field. "
                "The algebra depends on which masters already contain the "
                "additive terms, so the roles matter more than any single "
                "universal formula.",
                "Flat correction and background extraction are not "
                "substitutes for one another. A flat is a measured "
                "correction from a reference exposure; background "
                "extraction is a model fitted to the science image. The "
                "second can reduce how residual vignetting looks without "
                "making the data correctly flat-fielded.",
            ),
        ),
        ConceptSection(
            "cfa-state-and-debayer-order",
            "CFA state and debayer order",
            (
                "For one-shot-colour data, calibration should normally "
                "happen while the frames are still in their colour-filter-"
                "array mosaic state. Demosaicing interpolates the missing "
                "colour samples, after which neighbouring pixels no longer "
                "represent independent raw sensor readings in the same way.",
                "The robust teaching order is raw CFA, then calibration, "
                "then debayer, then registration, then integration. Engines "
                "may fuse those into fewer commands or save different "
                "intermediates, but a wrong Bayer pattern or a debayer "
                "performed at the wrong stage is not something later colour "
                "work can undo.",
            ),
        ),
        ConceptSection(
            "clipping-and-output-pedestals",
            "Clipping and output pedestals",
            (
                "Calibration arithmetic can legitimately produce values "
                "below zero. If the software or file representation clamps "
                "those to zero too early, the information is gone: a clipped "
                "zero does not remember how far below zero it would have "
                "been. This is why an output pedestal can be added during "
                "calibration -- a known positive offset that keeps "
                "legitimate low samples representable through truncation.",
                "That pedestal is bookkeeping, not signal, and the "
                "consequence runs one way only. If calibration has already "
                "clipped, adding an offset afterwards raises zeros without "
                "restoring what separated them. Choose the amount from "
                "measured clipping diagnostics for the data and tool at "
                "hand; published numeric examples are procedural guidance "
                "for a specific tool and version, not a constant.",
            ),
        ),
        ConceptSection(
            "input-pedestal-versus-output-pedestal",
            "Input pedestal versus output pedestal",
            (
                "Two different things share the word pedestal. A "
                "calibration-time output pedestal is protection added on "
                "purpose so that low values survive truncation. A residual "
                "electronic offset is an uncorrected additive term left in "
                "the data. They are different claims about different stages "
                "of the same pipeline.",
                "Confusing them has a specific failure mode: subtracting a "
                "protective pedestal later, on the theory that it is a "
                "residual offset, can reintroduce exactly the clipping it "
                "was added to prevent. The narrower Pedestal Removal article "
                "covers when a genuine residual offset may be removed -- and "
                "its default answer remains skip.",
            ),
        ),
        ConceptSection(
            "what-seestar-has-already-done",
            "What SeeStar has already done",
            (
                "Exported SeeStar files arrive with vendor-stated dark "
                "calibration. Treat that as a provenance attribute rather "
                "than a guarantee: it is version-bound, it does not assert "
                "independent bias and flat correction, and community reports "
                "of clipping in some firmware-era files mean that low-end "
                "damage is worth diagnosing rather than assuming away.",
                "A native SeeStar stack is already far downstream of raw "
                "calibration. Redoing a dark or flat calibration on it is "
                "not possible without the original subframes and appropriate "
                "masters.",
            ),
        ),
        ConceptSection(
            "what-nova-can-still-do-after-stacking",
            "What NOVA can still do after stacking",
            (
                "NOVA's processing pipeline starts from a stack, which means "
                "the CFA and calibration decisions have already been made by "
                "the capture or stacking path before the first processing "
                "step runs. What remains available is detection and "
                "mitigation of residual defects, not reconstruction of raw "
                "measurements.",
                "Stated plainly: cosmetic correction can suppress leftover "
                "isolated defects, background extraction can model residual "
                "large-scale gradients, colour calibration can correct "
                "channel response, and a documented residual offset can be "
                "subtracted. None of those recreate correctly calibrated "
                "pixels, and describing them as repairing calibration would "
                "overstate what they do.",
            ),
        ),
        ConceptSection(
            "is-this-a-calibration-problem",
            "Is this a calibration problem, or something else?",
            (
                "Persistent hot or cold pixels in stable sensor positions, "
                "amp glow, and structured dark residuals point upstream to "
                "dark calibration. Vignetting, dust shadows, inverse dust "
                "donuts, and repeated multiplicative field patterns point to "
                "flats. Zipper or checker patterns, wrong colour assignment, "
                "and altered star profiles point to debayer placement or "
                "pattern. A sharp pile-up at zero points to calibration "
                "clipping.",
                "The corresponding recovery in each case is to return to the "
                "original subframes and recalibrate, not to reach for a "
                "downstream tool. Cosmetic correction, background "
                "extraction, and colour calibration each have their own "
                "article and their own legitimate job; standing in for "
                "absent calibration is not it.",
            ),
        ),
        ConceptSection(
            "how-to-validate-calibration",
            "How to validate calibration",
            (
                "Measure on the calibrated subframes, before later "
                "processing can hide the difference: the low-clipped "
                "fraction with known file scaling, per-channel or per-CFA-"
                "plane minima, medians, robust spread and clipping counts "
                "compared at the same processing state, flat-field residual "
                "spatial structure, and persistent hot or cold pixel "
                "structure across frames. A difference image against a "
                "controlled reference shows whether a claimed scalar "
                "operation is actually spatially uniform.",
                "Several tempting metrics cannot carry this weight. A "
                "background-gradient score is confounded by real nebulosity "
                "and by later modelling; a whole-image signal-to-noise proxy "
                "is not photometric signal-to-noise and rewards smoothing "
                "and rejection; star count, half-flux diameter, and "
                "eccentricity are dominated by seeing, registration, and "
                "extraction thresholds. Visual inspection is genuinely "
                "useful for gradients, dust donuts, clipped black texture "
                "and mosaicing artefacts -- as a supplement to subframe "
                "statistics, not as a replacement.",
            ),
        ),
        ConceptSection(
            "comparing-calibration-strategies-fairly",
            "Comparing calibration strategies fairly",
            (
                "A calibration comparison has to happen upstream on the same "
                "raw subframes, not by subtracting different constants from "
                "an image that has already been stacked. Fairness requires "
                "identical frames and inclusion sets, matched masters or an "
                "explicitly stated master-strategy difference, identical CFA "
                "and debayer ordering, identical registration and "
                "integration settings, identical bit depth and output "
                "scaling, and an identical preview stretch used only for "
                "review.",
                "The confounders that most often invalidate such a "
                "comparison are different frame sets, differently built "
                "masters, different demosaic methods, different rejection or "
                "weighting during integration, an unnamed difference in "
                "output pedestal or clipping policy, firmware drift between "
                "captures, and adaptive downstream steps that retune "
                "themselves to the changed statistics. Any result from such "
                "a comparison is a result for that data, those candidates, "
                "those versions, and that evaluation procedure.",
            ),
        ),
        ConceptSection(
            "evidence-and-version-notes",
            "Evidence and version notes",
            (
                "Calibration behaviour is engine- and firmware-dependent, so "
                "the strength of each statement here varies deliberately. "
                "Siril's support for bias, dark, and flat masters with "
                "CFA-aware handling is vendor documented. PixInsight's "
                "calibration model is vendor documented, while the "
                "output-pedestal practice is drawn from vendor community "
                "guidance and its numeric examples are procedural rather "
                "than specification. The SeeStar dark-calibration statement "
                "is direct but narrow vendor evidence.",
                "Two claims are explicitly not supported and should not be "
                "repeated: that SeeStar calibration can never clip, and that "
                "a flatter final background demonstrates improved flat "
                "calibration. Neither follows from the available evidence.",
            ),
        ),
    ),
    claims=_CLAIMS,
    unresolved_gates=_UNRESOLVED_GATES,
    related_process_families=(
        ProcessFamily.PEDESTAL_REMOVAL,
        ProcessFamily.SUBFRAME_INSPECTION,
        ProcessFamily.STACKING_INTEGRATION,
        ProcessFamily.COSMETIC_CORRECTION,
        ProcessFamily.BACKGROUND_EXTRACTION,
        ProcessFamily.COLOR_CALIBRATION,
    ),
    sources=_SOURCES,
    validation_event_ids=(),
)

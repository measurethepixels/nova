"""Group H concept page: output, export, and reproducibility.

Synthesized from Phase-2 research packet P36 (2026-08-28). P36 is explicit
that this is a cross-cutting state/provenance concept, not a processing step
and not an export tutorial -- it teaches two separate questions readers
otherwise conflate: what must be retained to keep a result reproducible, and
what should be exported for presentation. No production code, schema,
renderer, or database change is implied by this page.
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
        "packet-p36",
        "Phase-2 research packet P36, 2026-08-28",
        "packet:P36:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "how-nova-knows",
        "How NOVA Knows: evidence, provenance, validation, and version drift",
        "concept:how-nova-knows",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "input-data-state",
        "Input Data, Provenance, and State (P04)",
        "concept:input-data-state",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "experiment-evidence-aggregation",
        "Experiment Evidence Aggregation / What NOVA Has Learned (P35)",
        "concept:experiment-evidence-aggregation",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "processed-scanner-source",
        "NOVA processed-library scanner: extension classification and metadata extraction",
        "nas_server/processed_scanner.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "stack-filename-source",
        "NOVA standardized stack filename and configuration-token generation",
        "nas_server/stack_config.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "run-tables-source",
        "NOVA run/provenance database tables (processed_files, processing_runs, "
        "processing_history, stacking_runs)",
        "nas_server/database.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "experiment-candidate-source",
        "Experiment Mode candidate output and preview generation",
        "nas_server/experiments.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "fits-primer",
        "NASA/IAU FITS Support Office, FITS Primer",
        "https://fits.gsfc.nasa.gov/fits_primer.html",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "xisf-spec",
        "PixInsight XISF format documentation",
        "https://pixinsight.com/xisf/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "icc-profile-embedding",
        "International Color Consortium, profile embedding",
        "https://www.color.org/profile_embedding/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "container-is-not-state",
        "A file extension or container format (FITS, XISF, TIFF, JPEG/PNG) is not "
        "itself an image's processing state, product class, or reproducibility "
        "guarantee -- the same rule P04 established on the input side applies "
        "symmetrically to output.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "input-data-state"),
    ),
    Claim(
        "eight-artifact-classes",
        "NOVA's real outputs separate into eight artifact classes -- native "
        "acquisition product, pipeline integration product, processed "
        "scientific/intermediate product, derived layer (starless/stars/mask/"
        "narrowband channel/mosaic layer), Experiment candidate or control, "
        "processed final/master result, presentation export, and scratch/"
        "sidecar artifact -- each with a different reproducibility obligation.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36",),
    ),
    Claim(
        "scanner-confirms-class-separation",
        "NOVA's own processed-library scanner enforces part of this separation "
        "in production, by exclusion rather than a positive whitelist: "
        ".jpg/.jpeg/.png are explicitly treated as previews and skipped from "
        "the processed-files index, and solver .ini/.wcs files are explicitly "
        "excluded as scratch sidecars. Any other extension is accepted "
        "unconditionally -- a `_PROCESSABLE_EXTS` constant naming "
        ".fit/.fits/.xisf/.tif/.tiff as the intended processable-science class "
        "exists in source but is never consulted by the accept path, so it is "
        "an unenforced, current-runtime gap rather than an active whitelist.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "processed-scanner-source"),
    ),
    Claim(
        "tiff-metadata-asymmetry",
        "Being accepted into the processable library does not imply equal "
        "provenance introspection: TIFF is currently accepted as a processable "
        "extension, but the scanner does not extract equivalent image/"
        "provenance metadata from TIFF the way it does for FITS/XISF.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "processed-scanner-source"),
    ),
    Claim(
        "filename-is-identity-not-manifest",
        "NOVA's standardized stack filenames (target, date, integration, "
        "temperature, config token, tool, step) are real, useful operational "
        "identity and overwrite protection, but a filename -- including its "
        "configuration hash -- is a compact identity token, not a complete "
        "self-describing parameter manifest.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "stack-filename-source"),
    ),
    Claim(
        "history-parsing-is-heuristic",
        "NOVA's HISTORY-keyword flag extraction is deliberately best-effort and "
        "recognizes only a small set of string patterns; the absence of a "
        "recognized token is not proof that an operation never ran.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "processed-scanner-source"),
    ),
    Claim(
        "provenance-is-fragmented-not-absent",
        "NOVA already records substantial real provenance -- processed_files, "
        "processing_runs, processing_history, and stacking_runs together carry "
        "target, workflow, timestamps, step parameters, scores, engine, and "
        "output paths -- but that evidence is fragmented across tables and file "
        "metadata rather than attached to one authoritative artifact manifest.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "run-tables-source", "how-nova-knows"),
    ),
    Claim(
        "path-is-not-identity",
        "An output path recorded in a run table proves that the run recorded a "
        "path at that time; it does not by itself prove the file still exists, "
        "remains byte-identical to the original result, or carries every "
        "metadata field required to reproduce it -- paths are mutable "
        "identifiers, not checksums.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "run-tables-source"),
    ),
    Claim(
        "experiment-candidates-are-fits-not-jpeg",
        "Experiment Mode's scientific candidate/control outputs are FITS; the "
        "JPEGs generated alongside them are evaluator comparison renderings, "
        "including deliberate display transforms for linear comparisons, and "
        "are not themselves the canonical candidate data.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "experiment-candidate-source", "experiment-evidence-aggregation"),
    ),
    Claim(
        "quantization-can-lose-precision",
        "Converting floating-point intermediate data to integer storage can "
        "irreversibly quantize small differences, especially across large "
        "dynamic range; FITS supports IEEE float32/float64 specifically to "
        "avoid this, but a writer can still choose to scale into integers.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("packet-p36", "fits-primer"),
    ),
    Claim(
        "format-capability-is-not-provenance",
        "Container format capability does not by itself establish provenance: a "
        "FITS or XISF writer can still clip, rescale, strip headers, reorder "
        "dimensions, alter WCS, or save already-nonlinear/processed pixels "
        "despite the format's own capacity to preserve high-precision arrays "
        "and rich metadata.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("packet-p36", "fits-primer", "xisf-spec"),
    ),
    Claim(
        "no-format-is-universal-winner",
        "No single container format is a universal winner: FITS is strongest "
        "for interoperable scientific arrays and astronomy metadata/WCS, XISF "
        "is strongest for PixInsight-native rich metadata and ICC profiles, "
        "TIFF suits high-bit-depth interchange once astronomy-specific "
        "semantics are no longer required, and JPEG/PNG suit presentation and "
        "delivery -- the choice depends on what the next step actually needs.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("packet-p36", "fits-primer", "xisf-spec"),
    ),
    Claim(
        "presentation-export-is-a-rendering",
        "A presentation export (JPEG/PNG, or a color-managed TIFF) is a "
        "rendered, display-referred view of a result -- possibly resized, "
        "gamma/display-transformed, color-profile converted, or lossily "
        "compressed -- and should never silently replace the retained "
        "scientific/intermediate master as the parent for later quantitative "
        "comparison.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36", "icc-profile-embedding"),
    ),
    Claim(
        "visual-similarity-is-not-scientific-equivalence",
        "A visually identical export is not automatically scientifically "
        "identical: different quantization, profile conversion, clipping, "
        "metadata loss, or geometry changes can produce a similar-looking "
        "display while breaking later measurements -- pixel-equivalent, "
        "state-equivalent, and presentation-equivalent are three different, "
        "non-interchangeable claims.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p36",),
    ),
)

_UNRESOLVED_GATES = (
    "Which final/master artifact NOVA should designate as canonical when both "
    "FITS and XISF descendants of the same result exist.",
    "Which operations currently preserve WCS/FITS keywords exactly, which "
    "rewrite them, and which strip them -- this needs a real execution matrix, "
    "not an assumption.",
    "Whether every intended publication/export path embeds an ICC profile, and "
    "what presentation encoding is canonical for the website/YouTube workflow.",
    "Whether TIFF should remain a processable-library artifact without "
    "equivalent metadata extraction, or should carry an explicitly different "
    "provenance requirement.",
    "What artifact-retention policy should apply to Experiment Mode losers, "
    "failed variants, masks, and rendered previews once P35's complete-"
    "denominator evidence model is fully implemented.",
    "Whether code SHA/tool versions should be embedded directly into FITS/"
    "XISF metadata, retained only in a manifest/database, or both.",
    "What checksum/manifest strategy best survives NAS file moves/renames "
    "while remaining practical for very large mosaic/drizzle products.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="output-export-reproducibility",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Saving the Result Without Losing the Experiment",
    subtitle="Output files, image state, and reproducibility",
    summary=(
        "A NOVA result is not reproducible merely because an image file exists. "
        "This page separates what must be retained to preserve scientific/process "
        "reproducibility from what should be exported for presentation -- those "
        "questions often have different answers, and NOVA's own architecture "
        "already treats them differently."
    ),
    sections=(
        ConceptSection(
            "extension-is-not-state",
            "A file extension is not an image state",
            (
                "FITS, XISF, TIFF, and JPEG are containers with very different "
                "strengths. None alone tells a reader whether an artifact is a "
                "native SeeStar stack, a NOVA/Siril integration, a processed "
                "linear intermediate, a nonlinear final, a starless layer, a "
                "mosaic-derived product, an Experiment Mode candidate, or a "
                "presentation preview. P04 already established this rule on "
                "the input side; it applies symmetrically here.",
            ),
        ),
        ConceptSection(
            "eight-artifact-classes",
            "The artifact classes NOVA actually creates",
            (
                "Native acquisition product (a SeeStar-native stack or light "
                "frame); pipeline integration product (a NOVA/Siril/PixInsight-"
                "created raw stack eligible as a processing parent); processed "
                "scientific/intermediate product; derived layer (starless, "
                "stars, mask, reconstructed narrowband channel, mosaic/"
                "coverage-derived layer); Experiment candidate or control (a "
                "treatment-specific child of one declared parent); processed "
                "final/master result; presentation export; and scratch/sidecar "
                "artifact (solver .wcs/.ini, temporary previews, logs).",
                "Container format is a separate field from artifact class. "
                "NOVA's own processed-library scanner draws part of this "
                "boundary in production, by exclusion rather than a positive "
                "whitelist: JPEG/PNG are explicitly skipped as previews and "
                "solver .ini/.wcs files are explicitly excluded as scratch, "
                "but any other extension is accepted unconditionally -- a "
                "constant naming FITS/XISF/TIFF as the intended processable "
                "class exists in source but is never consulted, so it is not "
                "currently an enforced whitelist. Being accepted by the "
                "library does not imply equal provenance introspection -- "
                "TIFF is processable today without the same metadata "
                "extraction FITS/XISF receive.",
            ),
        ),
        ConceptSection(
            "native-vs-pipeline-vs-processed-vs-mosaic",
            "Native stack vs. pipeline stack vs. processed result vs. mosaic",
            (
                "A native SeeStar stack is an acquisition/device-generated "
                "integration product -- not synonymous with a NOVA raw pipeline "
                "stack. A raw pipeline stack is NOVA-created and intended as a "
                "processing parent; its stack engine, configuration, and frame "
                "selection are material provenance. A processed stack or "
                "product is a descendant that may be linear or nonlinear and "
                "may no longer be eligible as raw pipeline input. A mosaic is "
                "an orthogonal geometry/provenance attribute, not a replacement "
                "product class -- a mosaic can itself be native-derived, "
                "pipeline-integrated, or processed. Target identity and product "
                "provenance cannot be inferred solely from filename or folder "
                "naming.",
            ),
        ),
        ConceptSection(
            "master-vs-export",
            "Science/master files versus presentation exports",
            (
                "Two separate questions: what should be retained to preserve "
                "reproducibility, and what should be exported for presentation "
                "or interchange? A presentation export -- JPEG/PNG or a "
                "color-managed TIFF -- is a rendered view: possibly resized, "
                "gamma/display-transformed, color-profile converted, or "
                "lossily compressed. It should never silently substitute for "
                "the retained science/intermediate master in later quantitative "
                "comparison, and the master should be retained before any "
                "export is created, not the other way around.",
            ),
        ),
        ConceptSection(
            "format-tradeoffs",
            "FITS, XISF, TIFF, JPEG/PNG: what each preserves and risks",
            (
                "FITS: best fit for interoperable scientific/intermediate "
                "arrays and astronomy metadata/WCS. Prefer 32-bit floating "
                "point when preserving a naturally floating processing state; "
                "integer FITS with scaling can lose precision, and header "
                "survival through external tools should be verified, not "
                "assumed.",
                "XISF: best fit for high-fidelity PixInsight-centric working/"
                "master files with rich image properties, FITS-keyword "
                "compatibility, structured metadata, and ICC profiles. Its "
                "richness does not automatically make it the most "
                "interoperable choice for every external CLI or service.",
                "TIFF: best fit for high-bit-depth interchange or a "
                "presentation master once astronomy-specific FITS/XISF "
                "semantics are no longer required by the next step. Integer "
                "versus floating TIFF, bit depth, color encoding, ICC profile, "
                "compression, and metadata retention are not implied by the "
                "extension alone.",
                "JPEG/PNG: best fit for previews, web/phone delivery, "
                "evaluator views, and publication assets. JPEG is lossy; both "
                "commonly use bounded integer samples and a display-referred "
                "color encoding. The FITS/XISF/high-bit-depth master should be "
                "preserved separately, not derived backward from the export.",
            ),
        ),
        ConceptSection(
            "precision-and-clipping",
            "Bit depth, floating point, normalization, and clipping",
            (
                "Quantizing floating data into integers can irreversibly lose "
                "precision, especially across large dynamic range. Clipping or "
                "normalization during export can change percentiles, "
                "background levels, star cores, color ratios, and dynamic-"
                "range statistics -- which is also why a JPEG should not be "
                "used as the source for quantitative pipeline metrics when the "
                "original FITS/XISF candidate exists.",
            ),
        ),
        ConceptSection(
            "color-profiles",
            "Color profiles and why web appearance can differ",
            (
                "ICC profiles are the standard mechanism for defining "
                "presentation color interpretation across color-managed "
                "software. TIFF and baseline JPEG can embed ICC profiles, but "
                "profile embedding and interoperability are format- and "
                "reader-dependent, not guaranteed by the container alone -- an "
                "exported image can appear materially different across "
                "browsers, apps, and devices for this reason even when the "
                "underlying pixels are unchanged.",
            ),
        ),
        ConceptSection(
            "what-nova-records-today",
            "What NOVA records today, and where it is incomplete",
            (
                "NOVA already records substantial real provenance: standardized "
                "stack filenames carrying target/date/integration/temperature/"
                "configuration identity, and database tables (processed_files, "
                "processing_runs, processing_history, stacking_runs) carrying "
                "workflow, timestamps, step parameters, scores, engine, and "
                "output paths. This is valuable, but it is fragmented across "
                "tables and file metadata rather than attached to one "
                "authoritative artifact manifest -- a filename hash is a "
                "compact identity token, not a complete parameter manifest, "
                "and a path in a run table proves only that a path was "
                "recorded, not that the file still exists or is unchanged.",
                "HISTORY-keyword flag extraction is deliberately best-effort "
                "and recognizes only a small set of string patterns; the "
                "absence of a recognized token is not proof an operation never "
                "ran.",
            ),
        ),
        ConceptSection(
            "experiment-mode-outputs",
            "How Experiment Mode outputs and previews differ",
            (
                "Experiment Mode's candidate and control outputs are FITS; "
                "JPEGs are generated as evaluator comparison previews, and for "
                "linear-stage comparisons those previews deliberately apply "
                "display transforms so a human or AI evaluator can judge them "
                "at all. A viewer preference over those JPEGs is evidence about "
                "that rendering and evaluator context -- it is not proof that "
                "the JPEG is the scientific output, or that the underlying "
                "candidate was universally superior. The true no-op candidate "
                "copies the FITS input; that makes it a legitimate processing "
                "control, not a new acquisition product.",
            ),
        ),
        ConceptSection(
            "reproducibility-checklist",
            "Reproducibility checklist",
            (
                "A real reproducibility check should be able to answer: can "
                "the exact parent artifact be identified? Is its product class "
                "known? Is linear/nonlinear and star/starless/composite state "
                "known? Are acquisition/filter/calibration and mosaic/geometry/"
                "WCS states known where material? Are engine, parameters, "
                "masks, and ordering recoverable? Are code/workflow/tool/model "
                "versions recorded where they can materially change output? Is "
                "the output numeric representation known? Was any clipping, "
                "rescaling, gamma/display transform, resizing, lossy "
                "compression, or color-space conversion applied? Can the "
                "artifact be matched to its recorded run, ideally by a stable "
                "artifact ID or checksum rather than path/name alone?",
            ),
        ),
        ConceptSection(
            "failure-and-recovery",
            "Failure modes and recovery",
            (
                "Quantization or clipping: low-level differences disappear, "
                "background/percentile metrics shift, highlights pile up. "
                "Recover by returning to the highest-precision retained parent "
                "and re-exporting without clipping -- never try to infer lost "
                "values from the quantized export.",
                "Lost or invalid WCS/metadata: plate-dependent tools fail and "
                "provenance fields disappear. Recover by preferring the "
                "retained parent with valid metadata and re-solving geometry "
                "when appropriate, recording that re-solving is a new "
                "provenance event, not a restoration of the original.",
                "Color/profile mismatch: an exported image looks materially "
                "different across viewers. Recover with an explicit, tested "
                "color-managed presentation export -- never modify the "
                "scientific master merely to compensate for an unmanaged "
                "viewer.",
                "Presentation file reused as a processing parent: unexpectedly "
                "low precision, clipped values, changed dimensions or gamma, "
                "missing astronomy metadata. Recover by returning to the "
                "retained FITS/XISF/high-fidelity parent and treating the "
                "presentation file as terminal, display-only state.",
                "Provenance fragmentation: the image exists but its exact "
                "parent, versions, parameters, or experiment context cannot be "
                "reconstructed. Classify that evidence as incomplete -- never "
                "promote an artifact to reproducibility-confirmed merely "
                "because it looks right.",
            ),
        ),
        ConceptSection(
            "tempting-but-unsafe",
            "Tempting but unsafe statements",
            (
                "'16-bit TIFF is lossless, so it is equivalent to float FITS/"
                "XISF' -- not generally; integer quantization can still lose "
                "small differences, and equivalence depends on source range, "
                "encoding, and downstream needs.",
                "'FITS preserves the science automatically' -- a writer can "
                "still clip, rescale, strip headers, or save already-processed "
                "pixels into a FITS container; format capability is not "
                "provenance.",
                "'XISF is always better than FITS' -- XISF has richer "
                "PixInsight-native facilities, but interoperability and "
                "downstream tooling matter; there is no universal winner.",
                "'JPEG is only bad because it is compressed' -- the more "
                "important boundary is that a JPEG is a rendered, possibly "
                "resized and gamma/profile-transformed view generated from a "
                "particular stretch, not the pipeline's numeric state.",
                "'The filename tells you how the file was made' -- "
                "configuration hashes are opaque without their source "
                "parameters, and filenames do not include every later "
                "processing decision.",
                "'HISTORY proves the full pipeline' -- current flag extraction "
                "is deliberately heuristic and only recognizes selected "
                "strings.",
                "'A path in processing_runs makes a run reproducible' -- paths "
                "are mutable identifiers; without checksums and retained "
                "parameters/versions/lineage, a path can later refer to a "
                "moved, replaced, or missing file.",
                "'A visually identical export is scientifically identical' -- "
                "different quantization, profile conversion, clipping, "
                "metadata loss, or geometry changes can produce a similar-"
                "looking display while breaking later measurements.",
            ),
        ),
    ),
    claims=_CLAIMS,
    unresolved_gates=_UNRESOLVED_GATES,
    related_process_families=(
        ProcessFamily.STACKING_INTEGRATION,
        ProcessFamily.CROP_FRAMING,
    ),
    sources=_SOURCES,
)

"""Group A4 concept page: how NOVA measures images and where measurement ends.

Synthesized from Phase-2 research packet P02 (2026-08-27). Cites the shared
evidence vocabulary established by A2 ("How NOVA Knows", P03) and the
comparison model established by A3 ("Experiment Mode", P01).
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
        "packet-p02",
        "Phase-2 research packet P02, 2026-08-27",
        "packet:P02:2026-08-27",
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
        "nova-step-assessor",
        "NOVA analytic rejection rules (step_assessor.py, current families)",
        "nas_server/step_assessor.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
)

_CLAIMS = (
    Claim(
        "denoise-must-prove-preservation-not-just-reduction",
        "A denoise candidate is not accepted for reducing noise alone -- "
        "NOVA's current rejection rule fails a candidate whose measured "
        "FWHM broadens materially against the input, because broadening is "
        "a proxy for real detail being destroyed along with the noise.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02", "nova-step-assessor"),
    ),
    Claim(
        "deconvolution-guards-against-ringing-not-just-sharpness",
        "A deconvolution candidate that appears sharper is not accepted if "
        "it broadens the measured stellar profile at all, or if its ringing "
        "score crosses NOVA's current threshold -- an apparently crisper "
        "result can still be a ringing artifact rather than resolved "
        "detail.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02", "nova-step-assessor"),
    ),
    Claim(
        "background-extraction-guards-real-signal-not-just-flatness",
        "A flatter measured background is not automatically a better "
        "result -- NOVA's current rejection rule watches a nebulosity-"
        "leakage score specifically because an algorithm can flatten the "
        "sky by subtracting real extended emission along with the "
        "gradient.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02", "nova-step-assessor"),
    ),
    Claim(
        "no-analytic-rejection-is-not-passed-validation",
        "A candidate that triggers no current hard-rejection rule is "
        "described as having no analytic rejection triggered, never as "
        "having 'passed objective validation' -- it may still carry "
        "missing metrics, an assessor failure, or a real defect that no "
        "current guardrail happens to catch.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "similarity-metrics-measure-preservation-not-quality",
        "A structural-similarity score describes how much an output "
        "resembles a reference; it does not describe whether the output is "
        "good, since processing is often deliberately meant to change the "
        "image. Similarity is preservation evidence, not a quality score.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "comparisons-require-matched-conditions",
        "A measured comparison between two outputs is only as valid as "
        "what was held constant between them -- color-ratio comparisons, "
        "for example, break when the filter or data type differs, when "
        "target emission contaminates the sampled sky region, or when a "
        "linked versus unlinked stretch has altered the channel "
        "relationships being compared.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "star-removal-must-guard-target-detail-not-just-star-count",
        "A star-removal or star-reduction candidate is not judged by how "
        "many stellar sources it removed alone -- the framework requires "
        "guarding compact nonstellar structures and genuine target detail "
        "against being stripped out along with the stars, the same "
        "signal-preservation-over-reduction principle denoise uses. NOVA's "
        "current analytic rejection rules do not yet include a "
        "star-removal-specific guardrail for this; it is stated below as "
        "an open limitation, not a passed guardrail.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "hdr-remapping-is-not-recovery-of-clipped-data",
        "HDR remapping and tone compression can only redistribute the "
        "range that survived capture -- they cannot recover source "
        "information that was already clipped at acquisition, so a "
        "well-tone-mapped highlight or shadow is still built from whatever "
        "data existed there beforehand, never data that was never "
        "captured.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "halo-suppression-requires-no-op-control-and-multi-check-guard",
        "Judging a halo-suppression result credibly requires comparing it "
        "against an explicit no-op control alongside ring, color-fringing, "
        "and noise checks, not a single before/after glance -- a "
        "suppressed halo that only looks different is not evidence the "
        "tool improved the image rather than merely altering it. NOVA's "
        "current analytic rejection rules do not yet implement this "
        "guardrail; it is stated below as an open limitation, not a "
        "passed guardrail.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
    Claim(
        "narrowband-palette-comparisons-need-derived-channel-provenance",
        "A narrowband palette comparison (SHO, HOO, or similar) is only "
        "meaningful alongside the provenance of its derived channels -- "
        "which raw filters fed each mapped channel, and what recombination "
        "or mixing ratio produced it -- because comparing two palette "
        "renders without that provenance can mistake a mapping choice for "
        "a genuine signal difference.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p02",),
    ),
)

_UNRESOLVED_GATES = (
    "Which of NOVA's current metrics are direct statistics, model-based "
    "estimates, physically motivated proxies, or heuristic diagnostics has "
    "not been labeled consistently across every existing Handbook article "
    "-- P02 defines the taxonomy but applying it retroactively is separate "
    "work.",
    "Whether NOVA's whole-frame 'SNR' field should eventually be renamed, "
    "given that it is a structure-to-background-noise proxy rather than a "
    "photometric object SNR measurement, or whether labeling its actual "
    "meaning in the Handbook is sufficient.",
    "How finely comparisons should be gated by matched conditions (image "
    "scale, linear/nonlinear state, extraction thresholds, metric "
    "implementation version) before a metric difference is treated as "
    "meaningful rather than an artifact of mismatched measurement.",
    "NOVA's current analytic rejection rules (step_assessor.py) do not "
    "yet include a star-removal guardrail for compact-nonstellar-"
    "structure or target-detail preservation -- this page states the "
    "requirement as a framework principle, not as an enforced guardrail.",
    "NOVA's current analytic rejection rules do not yet include a "
    "halo-suppression guardrail requiring an explicit no-op control plus "
    "ring/color/noise checks -- this page states the requirement as a "
    "framework principle, not as an enforced guardrail.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="measurement-framework",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Measuring an Astrophoto",
    subtitle=(
        "What NOVA can quantify, what it can only estimate, and what "
        "still requires judgment"
    ),
    summary=(
        "How NOVA's measurements fit into a decision: which numbers are "
        "direct statistics, which are proxies, which are heuristics, and "
        "where the boundary sits between an objective measurement and an "
        "aesthetic preference."
    ),
    sections=(
        ConceptSection(
            "why-nova-measures-images",
            "Why NOVA measures images",
            (
                "Measurements constrain decisions; they do not replace "
                "judgment. A number can be perfectly repeatable without "
                "being a universal quality score -- NOVA reads every "
                "metric only within the image state and process context "
                "where its assumptions actually hold, not as a standalone "
                "verdict.",
            ),
        ),
        ConceptSection(
            "six-kinds-of-evidence",
            "Six kinds of evidence",
            (
                "Every metric NOVA presents is one of six kinds, and the "
                "distinction matters for how much weight it can carry: a "
                "direct descriptive statistic computed straight from pixel "
                "values (a median, a percentile, a robust sigma); a "
                "model-based estimate that assumes an extraction procedure "
                "(FWHM from an extracted source ellipse, a background grid "
                "estimate); a physically motivated proxy related to a real "
                "concept without being the conventional scientific "
                "measurement of it (NOVA's whole-frame 'SNR,' which is a "
                "structure-to-background-noise proxy, not a photometric "
                "object measurement); a heuristic diagnostic score built to "
                "detect a known artifact (a gradient-severity or "
                "ringing-score value); a preservation or similarity metric "
                "that measures difference from a reference rather than "
                "quality (SSIM); and a perceptual or aesthetic judgment "
                "that asks which valid result better serves the goal, "
                "which is legitimate evidence but is never a pixel "
                "measurement.",
            ),
        ),
        ConceptSection(
            "background-and-noise",
            "Background and noise",
            (
                "Sky level and robust noise estimates (median, MAD-derived "
                "sigma) describe the current image's own background "
                "region under the current measurement procedure. They are "
                "reproducible, but they remain dependent on which region "
                "was sampled and how -- the same physical sky can measure "
                "differently under a different mask or a different "
                "background-model scale.",
            ),
        ),
        ConceptSection(
            "stars-and-psf",
            "Stars and PSF",
            (
                "FWHM in pixels is only directly comparable at the same "
                "image scale and using the same star-extraction procedure. "
                "A smaller measured FWHM after deconvolution does not mean "
                "the atmosphere suddenly improved -- it means the "
                "measurement changed, and the honest question is whether "
                "that change reflects real resolved detail or an "
                "artifact of the method.",
            ),
        ),
        ConceptSection(
            "histogram-and-clipping",
            "Histogram and clipping",
            (
                "Percentile-based measurements of highlight and shadow "
                "clipping describe pixel distribution, not detector "
                "physics -- a histogram percentile is not the same thing "
                "as a hardware clipping rail, and treating the two as "
                "interchangeable overstates what the number actually "
                "shows.",
            ),
        ),
        ConceptSection(
            "detail-and-structure",
            "Detail and structure",
            (
                "Sharpness and structure metrics such as spatial-frequency "
                "power, entropy, or SSIM are not universal quality scores. "
                "Noise and ringing can inflate a sharpness or entropy "
                "measurement without adding any real detail, which is "
                "exactly why these metrics are read as safeguards inside a "
                "process-specific guardrail rather than as a general "
                "'more detail is better' rule.",
            ),
        ),
        ConceptSection(
            "process-specific-guardrails",
            "Process-specific guardrails",
            (
                "Different processing steps need different safety checks, "
                "because the same statistic can mean opposite things in "
                "different contexts. NOVA's current hard-rejection rules "
                "reflect this: a denoise candidate is rejected for "
                "broadening measured stellar profiles too far, a "
                "deconvolution candidate is rejected for broadening FWHM "
                "at all or crossing a ringing threshold, and a background "
                "extraction candidate is rejected for leaking measured "
                "nebulosity into the modeled background. A candidate that "
                "triggers none of these guardrails has no analytic "
                "rejection triggered -- language the Handbook uses "
                "deliberately, since a current guardrail's silence never "
                "means the candidate 'passed objective validation.'",
            ),
        ),
        ConceptSection(
            "star-removal-hdr-halo-and-narrowband-guardrails",
            "Star removal, HDR, halo suppression, and narrowband guardrails",
            (
                "The same principle applies to four more steps this "
                "page's evidence set does not yet cover with a current "
                "NOVA analytic rejection rule. Star removal must guard "
                "compact nonstellar structures and real target detail, "
                "not just a reduced star count. HDR remapping and tone "
                "compression can only redistribute already-captured "
                "range -- they cannot recover source information that was "
                "clipped before the image existed. Halo suppression needs "
                "an explicit no-op control alongside ring, color, and "
                "noise checks before a difference is credited as "
                "improvement rather than mere alteration. Narrowband "
                "palette comparisons need the derived channels' own "
                "provenance -- which raw filters and recombination "
                "produced each mapped channel -- alongside any "
                "comparison, not instead of it.",
                "Star removal and halo suppression are stated here as "
                "required guardrails without a current NOVA analytic "
                "rejection rule enforcing either one; both are named "
                "again below as open limitations, not as passed "
                "guardrails. HDR's clipped-data framing and narrowband's "
                "provenance requirement are evaluation principles this "
                "Handbook enforces in how it describes those steps, "
                "independent of whether an analytic rule for either is "
                "added later.",
            ),
        ),
        ConceptSection(
            "when-comparisons-are-invalid",
            "When comparisons are invalid",
            (
                "A measured comparison is only as strong as what both "
                "sides held constant. FWHM and eccentricity comparisons "
                "break when image scale or extraction thresholds differ; "
                "noise and SNR-like comparisons break when crop "
                "composition or background modeling changes what feeds the "
                "measurement; color-ratio comparisons break when filter or "
                "data type differs, when target emission contaminates the "
                "sampled sky, or when a linked versus unlinked stretch has "
                "already altered the channel relationships being "
                "measured.",
            ),
        ),
        ConceptSection(
            "objective-evidence-vs-aesthetic-preference",
            "Objective evidence vs aesthetic preference",
            (
                "Some questions are close to purely factual: did measured "
                "background sigma decrease under the same procedure, did "
                "an operation fail to produce output, did geometry change. "
                "Others mix scientific and processing judgment: did "
                "background extraction remove a gradient without removing "
                "faint real signal. And some are primarily aesthetic: "
                "which valid stretch has the most pleasing tonal balance. "
                "The Handbook states plainly which kind of question a "
                "given claim is answering, rather than dressing a taste "
                "judgment up as a physics result.",
            ),
        ),
        ConceptSection(
            "how-experiment-mode-uses-measurements",
            "How Experiment Mode uses measurements",
            (
                "Measurements enter a candidate comparison in order: "
                "confirm the candidate executed at all, apply hard "
                "analytic safety gates, bring in the remaining metrics as "
                "context, then apply visual or human judgment, with "
                "repeated evidence accumulating on top of all of it. This "
                "is the same execution-then-safeguards-then-judgment "
                "sequence Experiment Mode itself follows -- measurement is "
                "the layer beneath comparison, not a replacement for it.",
            ),
        ),
        ConceptSection(
            "provenance-and-uncertainty",
            "Provenance and uncertainty",
            (
                "A measured value and a fallback estimate are not "
                "interchangeable, even when they end up in the same field. "
                "Where a threshold came from, whether a metric's "
                "implementation has since changed version, and whether a "
                "number was actually measured or substituted after a "
                "failure all belong to the claim's provenance -- omitting "
                "them lets an estimate quietly read as a measurement.",
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

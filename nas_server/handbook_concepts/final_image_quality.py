"""Group A5 concept page: final image quality assessment.

Synthesized from Phase-2 research packet P34 (2026-08-28).  This page keeps
scientific constraints, measurement evidence, and aesthetic preference
separate while explaining how NOVA interprets an Experiment Mode winner.
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
        "packet-p34",
        "Phase-2 research packet P34, 2026-08-28",
        "packet:P34:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "how-nova-knows",
        "How NOVA Knows: evidence, provenance, validation, and version drift",
        "concept:how-nova-knows",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "measurement-framework",
        "Measuring an Astrophoto: operation-specific measurement safeguards",
        "concept:measurement-framework",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nist-tn-1297",
        "NIST Technical Note 1297 measurement terminology",
        "https://www.nist.gov/pml/nist-technical-note-1297/"
        "nist-tn-1297-appendix-d1-terminology",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "nist-ai-rmf",
        "NIST AI Risk Management Framework 1.0",
        "https://doi.org/10.6028/NIST.AI.100-1",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "quality-is-constrained-vector",
        "Final image quality is a set of constrained objectives, not one scalar: "
        "an output must first pass technical and preservation checks before "
        "aesthetic preference can decide among acceptable alternatives.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34",),
    ),
    Claim(
        "measurements-have-domains",
        "A metric is evidence only for the image property and measurement "
        "conditions it actually describes; no SNR-like, FWHM, entropy, "
        "sharpness, gradient, clipping, or similarity value is a standalone "
        "measure of final image quality.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34", "nist-tn-1297"),
    ),
    Claim(
        "preference-is-not-fidelity",
        "A more visually compelling candidate may be the legitimate aesthetic "
        "choice, but that preference does not establish recovered signal, "
        "physical color fidelity, or scientific correctness.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34",),
    ),
    Claim(
        "winner-is-run-local",
        "An Experiment Mode winner is the preferred acceptable result among "
        "the candidates that ran for one image under the recorded state, "
        "metrics, preview method, evaluator, and versions -- not proof of a "
        "universally best method or statistical confidence.",
        EvidenceOrigin.RUN_LOCAL_WINNER,
        ("packet-p34", "how-nova-knows"),
        applicability_bound="The recorded run, candidate set, and evaluation conditions",
    ),
    Claim(
        "preview-is-not-artifact",
        "A JPEG or other transformed preview is display-dependent evidence for "
        "visual comparison, not the scientific image artifact; a defensible "
        "comparison discloses the preview transform and evaluator identity.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34",),
    ),
    Claim(
        "ai-is-bounded-comparator",
        "AI visual assessment can compare perceptual tradeoffs after technical "
        "gates, but its choice is conditioned by preview, scale, compression, "
        "prompt, supplied metrics, priors, and model version and is not itself "
        "human validation or physical measurement.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34", "nist-ai-rmf"),
    ),
    Claim(
        "validation-needs-complete-event",
        "A public Jeff-validated claim requires the validation outcome, covered "
        "dimensions, exact claim revision, and current applicability together; "
        "the mere presence of a review event is insufficient.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34", "how-nova-knows"),
    ),
    Claim(
        "halo-assessment-needs-no-op-and-multi-check-guard",
        "A final halo-suppression assessment requires a legitimate no-op "
        "control plus ring, color-fringing, and noise checks; visual change "
        "alone cannot establish improvement.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34", "measurement-framework"),
    ),
    Claim(
        "narrowband-assessment-needs-derived-channel-provenance",
        "A final narrowband palette assessment must preserve derived-channel "
        "provenance, including the raw filters and recombination or mixing "
        "ratios that produced each compared rendering.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p34", "measurement-framework"),
    ),
)

_UNRESOLVED_GATES = (
    "How well AI visual rankings agree with repeated blinded human preferences "
    "across target classes and evaluator model versions.",
    "Whether showing measurements to a visual evaluator improves judgment or "
    "anchors it toward metrics that do not apply to the process under review.",
    "A canonical set of final-stage regions of interest for each morphology and "
    "data type, including how contaminated regions are excluded.",
    "How much repeated, stratified evidence is required before a run-local "
    "preference can become a conditional recommendation.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="final-image-quality-assessment",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Final Image Quality Assessment",
    subtitle="Scientific constraints, measurements, and aesthetic preference",
    summary=(
        "How NOVA judges whether a final image is technically sound, whether "
        "processing preserved its information, and which acceptable rendering "
        "is preferred -- without turning those different questions into one score."
    ),
    sections=(
        ConceptSection(
            "quality-is-not-one-score",
            "Quality is not one score",
            (
                "Astrophotography quality is multi-objective. Lower background "
                "noise can erase faint nebulosity; stronger sharpening can narrow "
                "stellar profiles while adding rings; more saturation can improve "
                "visual separation while moving colors away from calibrated "
                "values. NOVA therefore treats quality as a set of constrained "
                "objectives, not a number to maximize.",
                "The order matters: reject invalid or damaged outputs, measure the "
                "property the operation was intended to change, inspect preserved "
                "signal and artifacts, and only then use perceptual judgment for "
                "the tradeoffs that remain genuinely aesthetic.",
            ),
        ),
        ConceptSection(
            "three-questions",
            "Three questions, three kinds of evidence",
            (
                "First: is the output technically valid? A corrupt file, destructive "
                "clipping, severe ringing, or invalid state transition fails this "
                "gate regardless of appearance. Second: did the operation improve "
                "its intended property while preserving important information? "
                "That needs matched measurements and artifact checks. Third: among "
                "the acceptable results, which image is preferred? That last answer "
                "may properly depend on taste.",
                "These answers never merge into one confidence signal. Aesthetic "
                "preference cannot retroactively prove scientific recovery: a "
                "brighter faint arm is not newly recovered signal merely because "
                "contrast made it more visible, and a more saturated nebula is not "
                "stronger evidence of color calibration.",
            ),
        ),
        ConceptSection(
            "measurement-gates",
            "What measurements can establish",
            (
                "Repeatable measurements include pixel percentiles and clipping, "
                "robust background statistics in valid sky regions, residual "
                "gradients, stellar profiles on a comparable star population, "
                "channel ratios with known calibration state, and difference maps "
                "against the matched parent or control. Every result must name its "
                "image state, region of interest, estimator, units, comparison "
                "direction, and applicability conditions.",
                "Comparisons also require the same parent state, crop, scale, "
                "channel provenance, star or starless state, display transform, "
                "metric implementation, and effective runtime masks and parameters. "
                "A valid unchanged control remains in the set, failures remain in "
                "the denominator, and no hidden downstream step may favor one candidate.",
                "SNR-like values, entropy, sharpness, raw star count, gradient "
                "flatness, SSIM, and automated artifact thresholds are useful "
                "diagnostics only inside their valid domains. A smaller FWHM is not "
                "better if it came with halos; a flatter background is not better "
                "if genuine nebulosity was subtracted; lower noise is not better if "
                "faint structure disappeared.",
                "Process-specific evidence remains mandatory at final assessment. "
                "Halo suppression requires a legitimate no-op control alongside "
                "ring, color-fringing, and noise checks; a changed halo is not by "
                "itself an improved halo. A narrowband palette comparison must "
                "carry the derived channels' provenance, including which raw "
                "filters and recombination or mixing ratios produced each rendering.",
            ),
        ),
        ConceptSection(
            "preservation-and-artifacts",
            "Improvement must include preservation",
            (
                "A candidate is technically acceptable only when the expected "
                "process-specific change appears in a valid diagnostic and important "
                "target structure remains intact. Difference and residual views "
                "should expose leakage, rings, seams, halos, holes, clipped cores, "
                "removed compact structure, and unintended changes outside the "
                "operation's treatment domain.",
                "This is why optimizing an isolated metric can make the final image "
                "worse. Denoise can improve a background statistic by deleting "
                "signal; local contrast can raise entropy by amplifying grain; tonal "
                "compression can create headroom without restoring clipped data; "
                "star suppression can reduce counts while removing compact nebular features.",
            ),
        ),
        ConceptSection(
            "aesthetic-preference",
            "Where aesthetic preference belongs",
            (
                "Once technical constraints are satisfied, preference among natural "
                "or dramatic contrast, star prominence, an intentionally aesthetic "
                "palette, texture, framing, tone curve, saturation, or HDR strength "
                "is meaningful. The Handbook records it as visual judgment, not as "
                "measurement of beauty or proof of physical fidelity.",
                "A technically faithful image need not be Jeff's preferred rendering, "
                "and Jeff's preferred rendering need not be the strongest scientific "
                "artifact. Keeping both statements lets the creator make an artistic "
                "choice without borrowing authority from an unrelated measurement.",
            ),
        ),
        ConceptSection(
            "experiment-mode-comparison",
            "How an Experiment Mode comparison stays fair",
            (
                "Candidate roles remain visible: reference, alternative, parameter "
                "probe, object-specific strategy, fallback, unchanged control, "
                "research candidate, or historical method. A no-op control has not "
                "failed because it changes nothing; it can correctly win when every "
                "active treatment makes the image worse.",
                "A single survivor is selected by survival or operational fallback, "
                "not demonstrated as best. Conflicting measurements produce a stated "
                "tradeoff or an inconclusive result. Failed and rejected candidates "
                "stay in the evidence record instead of disappearing once a winner "
                "is named.",
            ),
        ),
        ConceptSection(
            "visual-comparison",
            "A preview is not the scientific artifact",
            (
                "Visual comparisons often need a preview, and a matched normal plus "
                "deep display can reveal damage hidden at one stretch. But a JPEG or "
                "derived preview is conditioned by its stretch, scale, compression, "
                "crop, and color rendering. The final comparison must disclose those "
                "transforms and identify the human or model evaluator. The FITS data "
                "and full-data diagnostics remain the scientific artifacts.",
                "All candidates must use the same disclosed display treatment. A "
                "prettier preview created by a different stretch is evidence about "
                "that presentation, not evidence that the underlying processing "
                "candidate preserved more signal.",
            ),
        ),
        ConceptSection(
            "bounded-ai-assessment",
            "AI is a bounded perceptual comparator",
            (
                "An AI evaluator can help compare naturalness, texture, halos, and "
                "the balance between subject and stars after technical gates have "
                "passed. Its decision is still conditioned by preview resolution and "
                "compression, the prompt, any supplied metric table, historical "
                "context, and the evaluator model and version. These belong in the "
                "evidence provenance.",
                "Historical win rates are context, not statistical confidence. They "
                "must not be presented as independent evidence for a current visual "
                "comparison, because doing so creates confirmation pressure, unless "
                "the experiment intentionally compares blinded and prior-informed "
                "strategies and labels that design explicitly. AI preference is not "
                "human validation, scientific proof, or a physical measurement.",
            ),
        ),
        ConceptSection(
            "what-a-winner-means",
            "What a winner means",
            (
                "A winner is the preferred acceptable result among the candidates "
                "that actually ran for this image, under this parent state, metric "
                "set, preview method, evaluator, prompt, and software or model "
                "versions. It is run-local evidence. It does not mean scientifically "
                "correct, universally best, statistically confident, or best for a "
                "whole target class unless separate repeated evidence supports that claim.",
                "When every candidate violates a constraint, there is no acceptable "
                "winner. When measurement and perception conflict without a stated "
                "priority, the honest outcome is a documented tradeoff or inconclusive -- "
                "not an opaque composite score that manufactures certainty.",
            ),
        ),
        ConceptSection(
            "human-validation",
            "What Jeff validation must say",
            (
                "The presence of a review event alone never makes evidence "
                "'Jeff validated.' A public statement must carry four facts together: "
                "the outcome (pass, partial, fail, or inconclusive), the validation "
                "dimensions actually checked, the exact claim revision reviewed, and "
                "whether the result currently applies. If any is absent, the event "
                "does not support that public label.",
                "For a final visual comparison, the record also names the scientific "
                "artifact, any transformed preview and its display method, and the "
                "evaluator identity. That disclosure makes clear whether Jeff judged "
                "the full-data artifact, a derived presentation, or both, and keeps "
                "aesthetic acceptance separate from scientific validation.",
            ),
        ),
    ),
    claims=_CLAIMS,
    unresolved_gates=_UNRESOLVED_GATES,
    related_process_families=(
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

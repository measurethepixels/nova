"""Group A3 concept page: Experiment Mode, NOVA's controlled-comparison model.

Synthesized from Phase-2 research packet P01 (2026-08-27). Cites the shared
evidence vocabulary established by A2 ("How NOVA Knows", P03) rather than
re-explaining it.
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
        "packet-p01",
        "Phase-2 research packet P01, 2026-08-27",
        "packet:P01:2026-08-27",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "how-nova-knows-page",
        "Handbook concept page: How NOVA Knows",
        "handbook:how-nova-knows",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
)

_CLAIMS = (
    Claim(
        "no-op-is-a-valid-winner",
        "A no-op or 'do nothing' candidate is a legitimate winner, not a "
        "placeholder inserted to flatter the other candidates. NOVA already "
        "has the execution mechanism for an unchanged candidate, and a "
        "process step that a control beats is one the Handbook can honestly "
        "say should be skipped.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p01",),
    ),
    Claim(
        "measurement-is-a-safeguard-not-the-objective",
        "A single measurement is not the aesthetic answer. A denoise method "
        "can lower background noise while destroying real detail; a "
        "deconvolution method can lower measured FWHM while adding halos. "
        "Experiment Mode uses measurements as safeguards and evidence "
        "before judgment, not as one scalar objective to maximize.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p01",),
    ),
    Claim(
        "execution-failure-is-not-inferiority",
        "A candidate that fails to run in the current environment is "
        "recorded as an operational failure, not as evidence that its "
        "method produces worse images than the candidates that did run.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p01",),
    ),
    Claim(
        "fallback-survivor-is-not-an-ai-choice",
        "When no comparison judgment is available and only one candidate "
        "survives, NOVA's current runtime selects it by default and labels "
        "that outcome an operational fallback -- never presented as an "
        "AI-selected winner, because no comparison actually happened.",
        EvidenceOrigin.EXECUTION_CONFIRMED,
        ("packet-p01",),
    ),
    Claim(
        "one-run-winner-stays-local",
        "A single run's winning candidate is a conclusion under that run's "
        "specific input, candidate set, and software state -- it is local "
        "evidence, not a cross-target recommendation, until repeated "
        "independent evidence supports generalizing it.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p01", "how-nova-knows-page"),
    ),
    Claim(
        "score-margins-are-not-significance",
        "A narrow score difference between two candidates, such as a 9 "
        "against an 8, is not equivalent to a statistically significant "
        "treatment effect. Translating a score margin into a generalized "
        "rule requires repeated trials, not one comparison.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p01",),
    ),
)

_UNRESOLVED_GATES = (
    "Where a no-op/control candidate is scientifically appropriate for "
    "each process family has not been decided for every family -- some "
    "ontology families do not yet include one.",
    "How finely 'object type' should be stratified before pooling "
    "historical experiment evidence across targets -- 'galaxy' currently "
    "treats M31, M51, and a faint edge-on galaxy as the same experimental "
    "unit, which P01 flags as too coarse.",
    "Whether disagreement between Claude's assessment and blind human "
    "review should be resolved, and by what authority, when both are "
    "recorded as valuable evidence rather than one simply overriding the "
    "other.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="experiment-mode",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Experiment Mode",
    subtitle=(
        "How NOVA compares processing choices and learns from them -- "
        "candidates, controls, measurements, review, winners, and why "
        "one win is not a universal rule"
    ),
    summary=(
        "The conceptual model behind every candidate comparison NOVA "
        "runs: what counts as a fair comparison, what a 'winner' actually "
        "means, and why a single run's result stays local evidence rather "
        "than becoming a universal recommendation."
    ),
    sections=(
        ConceptSection(
            "what-experiment-mode-is",
            "What Experiment Mode is",
            (
                "Experiment Mode answers one processing question at a time: "
                "given a specific image state, which of several candidate "
                "approaches produces the best result, and how confident "
                "should that conclusion be. It runs a defined set of "
                "candidates from the same starting point, measures and "
                "compares them, and records what happened -- including when "
                "nothing beat doing nothing at all.",
            ),
        ),
        ConceptSection(
            "what-counts-as-a-candidate",
            "What counts as a candidate",
            (
                "A candidate can be a reference method, an alternative "
                "method, a parameter probe of the same method, an "
                "object-specific strategy, a fallback, a no-op control, or "
                "an explicitly historical/deprecated approach kept for "
                "comparison. Treating these as one undifferentiated list "
                "would blur what a given comparison is actually testing -- "
                "a parameter probe answers a different question than a "
                "method alternative does, even when both appear in the same "
                "candidate set. When a Handbook article shows a candidate "
                "table, it uses this same vocabulary: what the candidate "
                "is, what varies about it, whether it was adapted to the "
                "image, whether it is a control or fallback, and what "
                "evidence currently supports it.",
            ),
        ),
        ConceptSection(
            "fair-comparison-starts-from-same-input",
            "A fair comparison starts from the same input",
            (
                "Every candidate in one experiment begins from the same "
                "exact image state. Candidate outputs do not chain into "
                "each other -- each one is an independent branch from the "
                "same starting point, not a pipeline where one candidate's "
                "output becomes another's input. That is what makes the "
                "comparison fair: any difference between candidates comes "
                "from the method itself, not from an accumulated difference "
                "in what each one started with.",
            ),
        ),
        ConceptSection(
            "adaptive-parameters",
            "Adaptive parameters",
            (
                "A candidate's ontology-declared default parameters are not "
                "always what actually ran. NOVA can adapt parameters to the "
                "current image's own statistics, so the evidence that "
                "matters is the executed runtime value, not just the "
                "declared default. When adaptation itself fails, that "
                "failure is recorded rather than silently treating the "
                "fallback-to-default run as equivalent to a successfully "
                "adapted one.",
            ),
        ),
        ConceptSection(
            "measurements-before-judgment",
            "Measurements before judgment",
            (
                "NOVA measures candidates before any visual or AI judgment "
                "happens, and uses those measurements as multi-dimensional "
                "safeguards and evidence rather than one aggregate score to "
                "maximize. Hard analytic rejection removes candidates that "
                "violate a current process-specific analytic heuristic or "
                "safeguard -- a threshold that is appropriate and "
                "revisitable for that family, not a universal physical "
                "validity rule, and one that is not yet defined evenly "
                "across every process family -- before visual comparison "
                "even starts, so a visual assessor is never asked to pick "
                "a candidate that a quantitative safeguard already ruled "
                "out.",
            ),
        ),
        ConceptSection(
            "how-nova-compares-survivors",
            "How NOVA compares survivors",
            (
                "Candidates that pass the safety checks are compared using "
                "their measurements, a visual/AI assessment, and any "
                "relevant historical context. When only one candidate "
                "survives, it is selected by default rather than by "
                "comparison -- see 'What failure modes mean' below for why "
                "that distinction matters.",
            ),
        ),
        ConceptSection(
            "human-review",
            "Human review",
            (
                "Optional human review uses blinded candidate labels rather "
                "than named methods, so a reviewer's judgment is not "
                "steered by knowing which candidate is which. When a "
                "person's judgment disagrees with NOVA's own assessment, "
                "that disagreement is recorded as evidence in its own "
                "right, not smoothed away in favor of one source "
                "overriding the other.",
            ),
        ),
        ConceptSection(
            "what-winner-means",
            "What 'winner' means",
            (
                "A winner is a local-run winner: the candidate selected "
                "under one specific input, candidate set, and software "
                "state. It is not automatically the universal best choice "
                "for every future image of that kind. Treating a single "
                "run's winner as a permanent rule is exactly the "
                "overgeneralization this framework exists to prevent.",
            ),
        ),
        ConceptSection(
            "controls-and-do-nothing",
            "Controls and 'do nothing'",
            (
                "A no-op control is not a weak candidate added to make the "
                "real candidates look good -- it is the reference needed to "
                "answer whether doing anything helped at all. Without a "
                "control, every candidate can leave the image worse than it "
                "started, and one of them will still be ranked first. A "
                "good control supports three honest conclusions: a "
                "treatment clearly improves on the unchanged input, "
                "treatments are roughly tied with it (suggesting the step "
                "may be unnecessary), or every treatment is worse than the "
                "control (meaning skip the process). 'Skip' is a valid "
                "winner whenever the process itself turns out to be "
                "unnecessary.",
            ),
        ),
        ConceptSection(
            "learning-over-time",
            "Learning over time",
            (
                "Repeated experiments build a historical record: win "
                "rates, adapted parameters, and evidence counts across "
                "runs. This is memory and context accumulating over time, "
                "not the underlying model being retrained -- a historical "
                "win rate describes how often something was selected "
                "across recorded runs, and is read that way rather than as "
                "a growing certainty score.",
                "A win rate is only honest evidence when its denominator is "
                "honest. Failed, rejected, no-op-won, single-survivor, and "
                "inconclusive outcomes belong in that denominator before "
                "any strong aggregate claim is made -- a historical corpus "
                "that only remembers clean wins reads as stronger than the "
                "underlying evidence actually supports. Repeated runs on "
                "the same target are kept separate from independent-target "
                "evidence rather than pooled as if they were equally "
                "informative, and evidence is partitioned whenever the "
                "code, tool, model, evaluator, prompt, metric, or treatment "
                "identity behind it materially changes -- a win recorded "
                "under one of those no longer counts as evidence for a "
                "materially different one. For the same reason, a win "
                "recorded against an older code, tool, or model version is "
                "not carried forward as current evidence for a newer "
                "version without revalidation; historical evidence stays "
                "visible, but it is not silently treated as if it were "
                "measured against what NOVA runs today.",
            ),
        ),
        ConceptSection(
            "evidence-strength-for-experiments",
            "Evidence strength for experiments",
            (
                "Experiment evidence sits alongside, not above, the other "
                "evidence origins described in How NOVA Knows: source-"
                "confirmed code, execution-confirmed runs, and human "
                "validation each answer a different question than a "
                "run-local winner or a historical win fraction does. A "
                "narrow score margin between two candidates is not a "
                "statistically significant result -- generalizing it "
                "requires repeated trials, not a single comparison.",
            ),
        ),
        ConceptSection(
            "failure-modes",
            "What failure modes mean",
            (
                "Different failures mean different things, and Experiment "
                "Mode keeps them distinguishable. A candidate that cannot "
                "execute in the current environment is an operational "
                "failure, not evidence of lower image quality. An "
                "experiment where every candidate fails should fail closed "
                "rather than fabricate a winner. A candidate that violates "
                "an analytic safeguard is excluded from selection, with "
                "the evidence retained rather than discarded. When "
                "adaptation itself fails and a candidate falls back to "
                "ontology defaults, that fallback is recorded so later "
                "learning does not treat it as an equivalent, successfully "
                "adapted trial.",
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

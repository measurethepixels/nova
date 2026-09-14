"""Group A6 concept page: experiment aggregation and corpus integrity.

Synthesized from Phase-2 research packet P35 (2026-08-28). This page applies
How NOVA Knows vocabulary to repeated Experiment Mode evidence without turning
historical winner rows into a leaderboard or statistical confidence claim.
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
        "packet-p35",
        "Phase-2 research packet P35, 2026-08-28",
        "packet:P35:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "how-nova-knows",
        "How NOVA Knows concept page",
        "handbook:how-nova-knows",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nist-experiment-design",
        "NIST/SEMATECH Engineering Statistics Handbook",
        "https://www.itl.nist.gov/div898/handbook/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "complete-denominator-before-aggregate",
        "NOVA does not publish an aggregate selection percentage as strong "
        "evidence until its denominator includes failures, all-failed runs, "
        "no-op selections, single survivors, and inconclusive outcomes; every "
        "reported figure names the matching comparable-run or candidate-"
        "exposure denominator.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p35", "how-nova-knows"),
    ),
    Claim(
        "target-count-is-not-run-count",
        "Repeated runs on one target are dependent reruns, not independent-"
        "target replication. A corpus summary reports both run count and "
        "independent target count rather than letting one stand in for the other.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p35", "nist-experiment-design"),
    ),
    Claim(
        "historical-frequency-is-descriptive",
        "A selection frequency over a complete, comparable stratum is a "
        "descriptive property of that recorded corpus, not statistical "
        "confidence, a posterior probability, or proof that a treatment is best.",
        EvidenceOrigin.HISTORICAL_WIN_FRACTION,
        ("packet-p35", "how-nova-knows"),
    ),
    Claim(
        "material-change-splits-evidence",
        "When code, tool, model, evaluator, prompt, metric, or effective "
        "treatment identity changes materially, NOVA partitions the evidence "
        "into a new stratum unless compatibility is established; it does not "
        "silently fold the old results into current totals.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p35", "how-nova-knows"),
    ),
    Claim(
        "versions-bind-aggregate",
        "Aggregation records the relevant tool and model versions. Evidence "
        "produced by a materially superseded version remains visible as stale "
        "or version-bound history rather than being presented as current.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p35", "how-nova-knows"),
    ),
    Claim(
        "negative-evidence-qualifies-claim",
        "Failures, analytic rejections, no-op wins, preservation failures, and "
        "class-specific reversals are first-class evidence. Contradictions "
        "split or qualify a claim instead of being averaged out of view.",
        EvidenceOrigin.REPEATED_EVIDENCE,
        ("packet-p35", "how-nova-knows"),
        contradiction_status="unresolved",
    ),
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="experiment-evidence-aggregation",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="What NOVA Has Learned: Experiment Aggregation and Corpus Integrity",
    subtitle="How repeated wins, failures, and changed conditions become bounded evidence",
    summary=(
        "How NOVA combines Experiment Mode results without hiding failed runs, "
        "mistaking repeated targets for independent evidence, or pooling across "
        "material version changes."
    ),
    sections=(
        ConceptSection(
            "what-learning-means",
            "What learning means here",
            (
                "NOVA learns by retaining versioned experiment evidence and "
                "finding bounded tendencies in comparable groups of runs. It "
                "does not train a mysterious universal model from winner rows. "
                "The public unit of evidence remains a traceable run: its input "
                "state, candidates, effective treatments, measurements, "
                "evaluator, decision, and terminal outcomes.",
            ),
        ),
        ConceptSection(
            "complete-denominator",
            "Why the denominator matters",
            (
                "A selection count is meaningful only alongside everything that "
                "had a chance to contribute. Before an aggregate win percentage "
                "can be strong evidence, the corpus must represent successful "
                "comparisons, execution failures, all-failed and aborted runs, "
                "analytic rejections, no-op wins, single-survivor outcomes, and "
                "inconclusive comparisons. A single survivor may show fallback "
                "reliability, but it did not comparatively defeat candidates "
                "that never produced a valid result.",
                "Every figure names its denominator: total attempted runs, "
                "comparable concluded runs, candidate exposures, execution "
                "successes, rejections, selections, controls, and inconclusive "
                "outcomes as relevant. Rows stored only for successful variants "
                "are historical records, not proof that every attempted run was "
                "counted.",
            ),
        ),
        ConceptSection(
            "independence-and-comparability",
            "Comparable runs and independent targets",
            (
                "Ten reruns or reprocessing branches of the same target are not "
                "ten independent targets. Summaries report both counts and keep "
                "same-target reruns distinct from evidence spanning independent "
                "targets. Runs are pooled only when their process family, input "
                "state, data class, candidate set, adaptation policy, measurement "
                "system, and relevant image conditions support the same bounded "
                "question.",
                "If a treatment helps one morphology but harms another, the "
                "result becomes class-specific guidance. A global percentage "
                "must not erase that reversal or let an overrepresented class "
                "choose the apparent winner.",
            ),
        ),
        ConceptSection(
            "treatment-and-evaluator-identity",
            "What counts as the same experiment",
            (
                "A candidate name alone is not treatment identity. Effective "
                "parameters, masks, adaptation policy, candidate-set revision, "
                "code and tool versions, metric implementation, evaluator model, "
                "prompt, preview transform, visible candidate identity, and "
                "historical-prior exposure can all change what was tested or how "
                "it was judged.",
                "When any of those identities changes materially, NOVA partitions "
                "the evidence into a new versioned stratum unless compatibility "
                "has been demonstrated. Operational history influenced by earlier "
                "preferences is disclosed, and stronger generalized claims need "
                "some prior-hidden or blind evidence rather than treating a "
                "self-reinforcing sequence as independent replication.",
            ),
        ),
        ConceptSection(
            "version-bound-evidence",
            "Versions travel with the evidence",
            (
                "Tool and model versions are recorded at aggregation time, along "
                "with the other identities that materially define the treatment "
                "and evaluation. If a generating version later changes enough to "
                "break comparability, its entries remain visible but are flagged "
                "stale or version-bound. They are not silently folded into totals "
                "for the current version.",
            ),
        ),
        ConceptSection(
            "responsible-summaries",
            "What NOVA can summarize",
            (
                "With a complete denominator, NOVA can responsibly report counts "
                "of exposures, successes, failures, rejections, selections, no-op "
                "wins, inconclusive runs, independent targets, and review "
                "disagreements. A selection frequency is written as a descriptive "
                "count such as 'selected in 8 of 12 comparable runs across 6 "
                "targets,' never as '80% confidence.'",
                "Where a process-valid measurement exists, paired same-parent "
                "effects and preservation checks usually say more than a winner "
                "percentage. More observations do not turn a weak or gameable "
                "proxy into scientific validation, and formal intervals or tests "
                "wait until the sampling unit, estimand, dependence, and "
                "comparability assumptions are justified.",
            ),
        ),
        ConceptSection(
            "recommendation-maturity",
            "How a recommendation matures",
            (
                "Evidence progresses from research candidate, to execution-"
                "confirmed, experimented, promising in a defined class, preferred "
                "in a defined class, and finally recommended or default. A run "
                "proves execution; one comparison supports only a run-local "
                "observation. Preference requires complete negative evidence, "
                "stable treatment identity, multiple independent targets, valid "
                "measurements, preservation checks, evaluator provenance, and no "
                "unresolved material contradiction in the stated class.",
                "There is no magic run count or winner percentage. Applicability, "
                "target diversity, effect consistency, failure rate, measurement "
                "validity, and dependence determine what the evidence can carry.",
            ),
        ),
        ConceptSection(
            "negative-and-contradictory-evidence",
            "Negative results stay visible",
            (
                "Execution failures, artifact rejections, degraded measurements, "
                "preservation failures, no-op wins, version regressions, and "
                "inconclusive outcomes are part of what NOVA learned. Conflicting "
                "evidence qualifies or splits a claim; it is not averaged away or "
                "overwritten by the newest result.",
            ),
        ),
        ConceptSection(
            "current-limit",
            "The current limit",
            (
                "NOVA's existing experiment history contains useful run-level "
                "ingredients, but it is not automatically a complete experiment "
                "registry. Until attempted candidates and every terminal outcome "
                "are known to be represented, the Handbook does not publish raw "
                "historical winner data as a generalized leaderboard or a "
                "statistical confidence model.",
            ),
        ),
    ),
    claims=_CLAIMS,
    unresolved_gates=(
        "Audit whether historical experiment records contain a complete denominator before publishing aggregate figures.",
        "Define compatibility rules for deciding which code, tool, model, evaluator, prompt, metric, and treatment changes require a new evidence stratum.",
        "Define a formal sampling and dependence model before publishing confidence intervals or significance claims.",
    ),
    related_process_families=tuple(ProcessFamily),
    sources=_SOURCES,
)

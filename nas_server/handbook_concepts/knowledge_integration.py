"""Group A7 concept page: how Handbook, Recipe, Validation, and Experiment
evidence relate without being interchangeable.

Synthesized from Phase-2 research packet P37 (2026-08-28), the last Group A
page -- lands after A2-A6 so it can cross-reference their finished pages
instead of duplicating them. Destination #37 per P40; P40's own publication-
count note flags #33 (A2, "How NOVA Knows" -- the evidence-vocabulary page)
and #37 (this page, the four-surface relationship page) as a possible later
combination point, but P37's own title and scope are distinct from A2's:
A2 defines what evidence terms mean, this page explains how four different
kinds of NOVA content relate to each other without one substituting for
another.
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
        "packet-p37",
        "Phase-2 research packet P37, 2026-08-28",
        "packet:P37:2026-08-28",
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
        "measurement-framework-page",
        "Handbook concept page: Measuring an Astrophoto",
        "handbook:measurement-framework",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "experiment-evidence-aggregation-page",
        "Handbook concept page: Experiment Evidence Aggregation",
        "handbook:experiment-evidence-aggregation",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
)

_CLAIMS = (
    Claim(
        "evidence-is-not-transitive",
        "Used is not validated. A validated procedure is not the same "
        "claim as a best result. A winner is not a universal best. A "
        "historical win fraction is not statistical confidence. Each of "
        "these pairs answers a different question, and NOVA's four content "
        "surfaces are built specifically so that citing one does not "
        "quietly stand in for another.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "how-nova-knows-page"),
    ),
    Claim(
        "cross-links-are-navigation-not-independent-evidence",
        "When the Handbook cites a Recipe and that Recipe's own prose "
        "cites the Handbook back, the two together are still one piece of "
        "evidence, not independent confirmation of each other. "
        "Independent support requires a genuinely distinct source, "
        "execution, artifact, validation, or experiment record -- a "
        "cross-link by itself is a pointer, not new evidence.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37",),
    ),
    Claim(
        "validation-laundering-is-a-named-failure-mode",
        "A single validation event that only checked Procedure is not "
        "summarized as an article-level 'Jeff validated' claim. The public "
        "statement carries exactly the outcome and validation dimensions "
        "that event actually covered, never rolled up beyond its tested "
        "scope.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "how-nova-knows-page"),
    ),
    Claim(
        "winner-laundering-is-a-named-failure-mode",
        "An Experiment Mode winner does not get described in the Handbook "
        "as 'NOVA validated this method.' It is a run-local winner, or at "
        "most a bounded repeated tendency, until real aggregation evidence "
        "supports stronger language -- an experiment producing a winner "
        "never by itself creates a validation stamp.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "experiment-mode-page"),
    ),
    Claim(
        "recipes-preserve-history-rather-than-inheriting-current-guidance",
        "A Recipe records what a specific run actually did -- the "
        "parameters, method, and version in effect at the time -- and does "
        "not silently update to reflect current Handbook defaults when "
        "guidance later changes. A Recipe may carry a dated annotation "
        "noting that current guidance has moved on, but the historical "
        "record itself stays untouched.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37",),
    ),
    Claim(
        "recipes-record-effective-not-declarative-parameters",
        "A Recipe's parameters are the executed runtime values, not the "
        "ontology/config field that was originally requested. When a "
        "wrapper or adaptation step ignores, transforms, clamps, or "
        "overrides a declared parameter before it runs, the Recipe records "
        "the effective treatment that actually executed -- the same "
        "requested-versus-effective distinction Experiment Mode's adaptive "
        "parameters already draw -- not the declarative default that was "
        "asked for.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "experiment-mode-page"),
    ),
    Claim(
        "preference-on-one-target-does-not-prove-equivalence",
        "An Experiment preferring one tool over another on a given target "
        "does not establish that the two tools are algorithmically "
        "equivalent. Cross-tool equivalence is its own claim, evaluated "
        "with its own evidence, independent of which one happened to win "
        "a particular comparison.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37",),
    ),
    Claim(
        "recommendation-maturity-follows-a-defined-ladder",
        "Experiment evidence earns stronger Handbook language only by "
        "climbing a defined ladder -- research candidate, execution-"
        "confirmed, experimented, promising in a defined class, preferred "
        "in a defined class, recommended or default in a defined class -- "
        "and each rung requires the aggregation discipline the evidence-"
        "aggregation page describes, not just an accumulating win count.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "experiment-evidence-aggregation-page"),
    ),
    Claim(
        "metrics-keep-their-measurement-class-on-every-surface",
        "A metric does not become more authoritative by moving from one "
        "surface to another. A number shown on a Recipe page is a run "
        "description, the same number in the Handbook is guidance, in a "
        "Validation event it supports only the specific dimensions that "
        "event tested, and in an Experiment it is comparative evidence -- "
        "the measurement framework's proxy/heuristic/perceptual "
        "distinctions apply identically regardless of which page displays "
        "the number.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p37", "measurement-framework-page"),
    ),
)

_UNRESOLVED_GATES = (
    "Validation claim hashes are not yet bound to a single canonical live "
    "claim registry, so two records that should point at the same claim "
    "can currently drift apart without a mechanical check catching it.",
    "Recipe instructional prose and Handbook guidance can drift "
    "independently today -- there is no enforced link keeping a Recipe's "
    "explanation of a step synchronized with the Handbook's current "
    "explanation of that same step.",
    "Experiment evidence is not yet a complete validation/corpus registry, "
    "so an aggregate claim's denominator (including failed and rejected "
    "runs) is not mechanically guaranteed complete the way the evidence-"
    "aggregation page's own rules require.",
    "Production integration of the four-surface relationship described "
    "here is conceptually sound but not yet fully built -- this page "
    "documents the intended contract, not a claim that every current "
    "Handbook article, Recipe, Validation event, and Experiment record "
    "already honors it.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="how-novas-knowledge-fits-together",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="How NOVA's Knowledge Fits Together",
    subtitle=(
        "Handbook, Recipes, Validation, and Experiments -- general "
        "guidance, one-run records, tested claims, and comparative "
        "learning are connected, but they are not interchangeable"
    ),
    summary=(
        "NOVA keeps four different kinds of content -- general Handbook "
        "guidance, historical Recipe records, claim-specific Validation "
        "events, and comparative Experiment evidence -- clearly related "
        "but never substituted for one another."
    ),
    sections=(
        ConceptSection(
            "four-questions-four-records",
            "Four questions, four records",
            (
                "Four different questions get four different kinds of "
                "answer. What does NOVA generally recommend, and why -- "
                "that is the Handbook. What did one specific run actually "
                "do -- that is a Recipe. Was a specific claim actually "
                "checked, and how -- that is a Validation event. How did "
                "candidate approaches compare on a real image -- that is "
                "Experiment evidence. Each record type stays honest about "
                "which question it answers, and none of them silently "
                "answers for another.",
            ),
        ),
        ConceptSection(
            "one-example-end-to-end",
            "One example end to end",
            (
                "Consider one process step end to end: the Handbook "
                "explains the general method and when to use it; a "
                "specific target's Recipe records the exact settings that "
                "run actually applied -- the effective, executed values "
                "after any wrapper adaptation, clamping, or override, not "
                "just the declarative field a config or ontology entry "
                "originally requested; one Validation event shows what a "
                "person actually checked about that claim, and how; an "
                "Experiment run shows how alternative candidates would be "
                "compared for a similar case; and a repeated-evidence "
                "summary shows what would still be needed before any of "
                "that changed the Handbook's general guidance. Following "
                "one step through all four surfaces is the clearest way "
                "to see how they relate without collapsing into each "
                "other.",
            ),
        ),
        ConceptSection(
            "evidence-is-not-transitive",
            "Evidence is not transitive",
            (
                "'Used' is not 'validated.' A 'validated procedure' claim "
                "is not the same as a 'best result' claim. A 'winner' is "
                "not a 'universal best.' A 'historical win fraction' is "
                "not 'statistical confidence.' Every one of these pairs "
                "sounds close in plain conversation, and every one of "
                "them is a materially different claim once evidence is "
                "read carefully.",
            ),
        ),
        ConceptSection(
            "how-recommendations-mature",
            "How recommendations mature",
            (
                "A method does not become 'recommended' the moment it "
                "wins once. It moves through a research candidate stage, "
                "to execution-confirmed, to experimented, to promising "
                "within a defined class of targets, to preferred within "
                "that class, and only then to recommended or default "
                "within that class -- and each step up that ladder needs "
                "the kind of complete, denominator-honest evidence the "
                "Handbook's evidence-aggregation guidance requires, not "
                "just an accumulating count of wins.",
            ),
        ),
        ConceptSection(
            "reading-badges-and-links",
            "Reading badges and links",
            (
                "A claim's source, its execution history, which "
                "validation levels were actually checked and with what "
                "outcome, what experiment evidence supports it, whether a "
                "person reviewed it, and whether any of that is still "
                "current all read as separate facts, not one combined "
                "score. A cross-link between two pages is a pointer for "
                "navigation, not a second, independent piece of evidence "
                "-- citing the same underlying source twice is still "
                "citing it once.",
            ),
        ),
        ConceptSection(
            "why-old-recipes-dont-get-rewritten",
            "Why old Recipes don't get rewritten",
            (
                "A Recipe is a historical record of what one run actually "
                "did -- the version, the parameters, the method in effect "
                "at the time -- and it stays that way even after current "
                "Handbook guidance moves on. A Recipe can carry a dated "
                "note that guidance has since changed, but rewriting the "
                "run itself to match current defaults would destroy the "
                "one thing a Recipe exists to preserve: what genuinely "
                "happened.",
            ),
        ),
        ConceptSection(
            "declared-versus-effective-parameters",
            "Declared versus effective parameters",
            (
                "An ontology or config field states what was requested, "
                "not necessarily what ran. When a wrapper or adaptation "
                "step ignores, transforms, clamps, or overrides that "
                "declared value before execution -- the same adaptation "
                "Experiment Mode's own candidates go through -- a Recipe "
                "records the effective, executed value, not the "
                "declarative field. Reading a Recipe as if its parameters "
                "always match the ontology default would silently hide "
                "exactly the runtime deviations the Recipe exists to "
                "preserve.",
            ),
        ),
        ConceptSection(
            "cross-tool-equivalence-stays-a-separate-claim",
            "Cross-tool equivalence stays a separate claim",
            (
                "An Experiment preferring one tool over a comparable "
                "alternative on a given target does not establish that "
                "the two tools are algorithmically equivalent -- a Recipe "
                "recording which concrete path was used, a Validation "
                "event testing a specific equivalence claim, and an "
                "Experiment comparing two functional alternatives are "
                "three different kinds of fact, and none of them "
                "silently reclassifies how equivalent two tools actually "
                "are.",
            ),
        ),
        ConceptSection(
            "known-failure-modes",
            "Known failure modes",
            (
                "A few integration mistakes recur enough to name "
                "directly. Circular evidence: citing a Recipe from the "
                "Handbook and the Handbook from that same Recipe, then "
                "treating the pair as two confirmations instead of one. "
                "Validation laundering: a partial validation event "
                "quietly becoming an article-wide 'validated' badge. "
                "Winner laundering: one Experiment winner being described "
                "as if NOVA had validated the method. Historical rewrite: "
                "updating a Recipe to match current settings instead of "
                "preserving what actually ran. Metric laundering: a "
                "heuristic or proxy metric shown beside a validated badge "
                "getting read as physical proof. Stale evidence hidden by "
                "stable IDs: an unchanged article or claim ID surviving a "
                "real code, tool, or model change while its old evidence "
                "keeps being treated as current. Missing negative "
                "evidence: reporting a win count without the failed or "
                "rejected runs that are part of the same evidence set. "
                "Each of these has a plain-language name specifically so "
                "it can be caught by name during review.",
            ),
        ),
        ConceptSection(
            "reproducibility-and-versions",
            "Reproducibility and versions",
            (
                "Every one of these four surfaces depends on knowing "
                "exactly which code, tool, and model version produced "
                "it. A claim, a Recipe, a Validation event, and an "
                "Experiment run are only comparable to each other when "
                "their version identity is known and matched -- an "
                "unversioned record is not wrong, but it carries weaker "
                "evidence than a record whose exact identity can be "
                "checked against what NOVA runs today.",
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

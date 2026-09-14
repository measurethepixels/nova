"""Group A2 concept page: evidence, provenance, validation, and version drift.

Synthesized from Phase-2 research packet P03 (2026-08-27), the anchor page for
Handbook Phase 3 Group A -- every later concept and process page cites this
page's vocabulary rather than re-explaining it.
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
        "packet-p03",
        "Phase-2 research packet P03, 2026-08-27",
        "packet:P03:2026-08-27",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-handbook-concept-schema",
        "NOVA Handbook concept schema (ConceptArticle, Claim, EvidenceOrigin)",
        "nas_server/handbook_contract.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-handbook-concept-tests",
        "NOVA Handbook concept schema test suite",
        "tests/test_handbook_concepts.py",
        ProvenanceLabel.NOVA_EXECUTION_RECORD,
    ),
    EvidenceReference(
        "w3c-prov",
        "W3C PROV Data Model",
        "https://www.w3.org/TR/prov-dm/",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
    EvidenceReference(
        "nist-iqs",
        "NIST Information Quality Standards",
        "https://www.nist.gov/director/nist-information-quality-standards",
        ProvenanceLabel.VENDOR_DOCUMENTED,
    ),
)

_CLAIMS = (
    Claim(
        "evidence-not-one-badge",
        "A claim carries several independent evidence dimensions -- where the "
        "evidence came from, whether a person reviewed it, whether it still "
        "applies, whether it is recommended, and whether it conflicts with "
        "other evidence -- rather than a single confidence badge that "
        "collapses all of them into one number.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p03", "nova-handbook-concept-schema"),
    ),
    Claim(
        "schema-enforces-orthogonality",
        "The claim schema mechanically rejects a claim that declares itself "
        "human-validated without a resolvable validation event, and rejects "
        "not-rejected, run-local-winner, or historical-win-fraction evidence "
        "from claiming human validation on its own -- these checks run in "
        "the schema's own test suite, not just in review.",
        EvidenceOrigin.EXECUTION_CONFIRMED,
        ("nova-handbook-concept-tests",),
    ),
    Claim(
        "win-fraction-not-confidence",
        "A historical Experiment Mode win fraction describes how often a "
        "candidate was selected across recorded runs. It is not a "
        "calibrated statistical confidence figure, and the Handbook states "
        "it as a selection rate ('selected in 11 of 14 comparable runs') "
        "rather than a percentage confidence.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p03",),
    ),
    Claim(
        "negative-evidence-stays-distinct",
        "A candidate that fails to run, a candidate that runs but produces "
        "an unacceptable result, and a candidate that runs acceptably but "
        "loses to another are three different outcomes. NOVA's evidence "
        "model keeps them distinguishable instead of folding all three into "
        "a single loss.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p03", "nova-handbook-concept-schema"),
    ),
    Claim(
        "stale-and-contradicted-are-different",
        "Evidence that no longer applies because a referenced code, tool, "
        "or model version changed is stale. Two currently-applicable pieces "
        "of evidence that support different conclusions are contradicted. "
        "These are different states, and the Handbook does not let a newer "
        "result silently overwrite an older one that still applies.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p03",),
    ),
    Claim(
        "provenance-follows-external-grounding",
        "Separating what produced a result, the activity that produced it, "
        "and who is responsible for judging it follows the entity/activity/"
        "agent distinction in the W3C PROV data model, adapted for NOVA's "
        "own vocabulary rather than adopted literally.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("w3c-prov",),
    ),
    Claim(
        "reproducibility-needs-disclosed-method",
        "A reproducibility claim requires disclosing the data, assumptions, "
        "analytic methods, and procedures used to produce a result -- this "
        "follows NIST's published information-quality guidance on "
        "transparency, not a NOVA-specific invention.",
        EvidenceOrigin.VENDOR_DOCUMENTED,
        ("nist-iqs",),
    ),
)

_UNRESOLVED_GATES = (
    "How much repetition and stratification across data classes is required "
    "before the Handbook can say a method is 'usually preferred' for a "
    "class of targets, rather than describing individual run outcomes.",
    "Whether a validated claim should be scoped to one artifact per event, "
    "or whether a single structured review can validate several related "
    "claims with per-claim outcomes.",
    "Which code, tool, model, or metric changes should automatically mark "
    "existing evidence stale versus merely add a caveat to it.",
    "Who or what is authorized to resolve a public recommendation when "
    "evidence is genuinely mixed, versus leaving it explicitly unresolved.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="how-nova-knows",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="How NOVA Knows",
    subtitle="Sources, experiments, validation, and the strength of a claim",
    summary=(
        "What NOVA actually knows, how it knows it, and how strongly a "
        "reader should trust each claim -- the shared vocabulary every "
        "other Handbook page draws on instead of re-explaining its own."
    ),
    sections=(
        ConceptSection(
            "why-evidence-labels-exist",
            "Why evidence labels exist",
            (
                "The Handbook distinguishes several different kinds of "
                "knowing: a scientific principle, a vendor's documented "
                "behavior, NOVA's own source code, one execution of that "
                "code, a retained result, a comparison between candidates, "
                "and a person's judgment of the outcome. Each is real "
                "evidence, and each means something different. Collapsing "
                "them into one badge -- 'validated' or not -- would hide "
                "exactly the distinctions a careful reader needs.",
            ),
        ),
        ConceptSection(
            "a-claim-is-not-a-badge",
            "A claim is not a badge",
            (
                "A single Handbook statement, such as 'this step reduces "
                "background gradient without removing faint nebulosity,' "
                "can be supported by several kinds of evidence at once: the "
                "underlying physical principle, the vendor's own "
                "documentation of the tool, NOVA's source code invoking it "
                "a specific way, a record of real executions, a measured "
                "comparison against a control, and a person who looked at "
                "the result and agreed. The Handbook keeps these separate "
                "rather than merging them into one number, because a reader "
                "who only sees 'validated' cannot tell which of these "
                "actually happened.",
                "Concretely, a claim carries five separate facts, and none "
                "of them stands in for another: where the evidence "
                "originated, whether a person validated it, whether it is "
                "currently recommended, whether it still applies, and "
                "whether it conflicts with other evidence. 'Recommended' "
                "in particular is its own editorial status, not a "
                "byproduct of strong evidence or human review -- a claim "
                "can carry solid execution-confirmed evidence without "
                "being the recommended default for a given situation, and "
                "a recommended default still needs its own origin, "
                "validation, and applicability stated rather than "
                "borrowing credibility from the word 'recommended' alone.",
            ),
        ),
        ConceptSection(
            "evidence-origin",
            "Evidence origin",
            (
                "Evidence origin describes how a piece of support was "
                "obtained, not how strong it is: confirmed directly from "
                "NOVA's own source code, documented by a tool vendor, "
                "confirmed by watching NOVA actually execute a step, "
                "produced by comparing retained output artifacts, not yet "
                "rejected by available evidence, the winner of one run, a "
                "historical selection rate across many runs, or evidence "
                "that has been repeated more than once. These origins can "
                "combine on a single claim -- a claim can be both "
                "execution-confirmed and repeated, for example -- which is "
                "exactly why they are recorded as separate facts rather "
                "than reduced to a single label.",
            ),
        ),
        ConceptSection(
            "what-validation-actually-checks",
            "What validation actually checks",
            (
                "Saying something was 'validated' answers almost nothing on "
                "its own. NOVA's validation levels ask which of several "
                "distinct questions were actually checked: was the correct "
                "procedure followed, were the right parameters used, was "
                "the input/output state correct, was the resulting artifact "
                "inspected, did the tool behave as expected, is a "
                "cross-tool substitute genuinely equivalent, and did the "
                "result meet an explicit acceptance rule. A pass on one "
                "level is real evidence; it is not evidence for the levels "
                "nobody checked.",
            ),
        ),
        ConceptSection(
            "experiment-evidence",
            "Experiment evidence",
            (
                "Experiment Mode produces evidence, not automatic "
                "validation. A single winning candidate is a conclusion "
                "under one specific input, one candidate set, and one "
                "software/tool version -- real, but narrow. Repetition "
                "strengthens that evidence; a historical win fraction "
                "across many comparable runs is stronger than one run, but "
                "it is still a selection rate, not a statistical confidence "
                "figure. Controls and failed candidates are kept as part of "
                "that evidence rather than dropped once a winner is chosen, "
                "because how often something failed or tied is part of "
                "what the evidence actually shows.",
            ),
        ),
        ConceptSection(
            "human-validation",
            "Human validation",
            (
                "'Jeff validated' means a specific person reviewed a "
                "specific claim against a specific artifact. Event "
                "presence alone is never published as 'Jeff-validated "
                "evidence' -- a public validation statement always states "
                "four things together: the outcome (pass, partial, fail, "
                "or inconclusive), which validation dimensions the review "
                "actually covered, the exact claim revision that was "
                "reviewed, and whether that validation still currently "
                "applies. Any one of those missing means the statement is "
                "incomplete, not merely terse.",
                "It does not mean every related claim is now settled, that "
                "the review was blind, or that the result will still apply "
                "after the underlying code, tool, or model changes -- that "
                "is exactly why current applicability is stated alongside "
                "the outcome rather than assumed. Human review and machine "
                "measurement answer different questions and are recorded "
                "as complementary evidence rather than one replacing the "
                "other.",
            ),
        ),
        ConceptSection(
            "version-drift",
            "Version drift",
            (
                "A result that was true once is not guaranteed to stay "
                "true. NOVA's code, the ontology's candidate definitions, "
                "an external tool's version, a model's version, a metric's "
                "implementation, and even the evaluator making the "
                "comparison can all change independently. Older evidence "
                "is not deleted when this happens -- it stays visible as "
                "historical evidence -- but it is not silently treated as "
                "current for a system that has since moved on.",
            ),
        ),
        ConceptSection(
            "negative-and-contradictory-results",
            "Negative and contradictory results",
            (
                "NOVA records what did not work, not only what did. A "
                "candidate can fail to run, run but be rejected, run "
                "acceptably but still lose to another candidate, or show no "
                "meaningful improvement over doing nothing -- these are "
                "different outcomes with different meanings, and all of "
                "them are kept. Contradiction is treated the same way: when "
                "two currently-applicable pieces of evidence point to "
                "different conclusions, that tension is recorded explicitly "
                "rather than quietly resolved by whichever result arrived "
                "most recently.",
            ),
        ),
        ConceptSection(
            "cross-tool-equivalence",
            "Cross-tool equivalence",
            (
                "'Does the same job' and 'runs the same algorithm' are "
                "different claims. NOVA distinguishes an exact replay of "
                "another tool's method from a same-engine adaptation, an "
                "algorithmically equivalent alternative, a functional "
                "alternative that reaches a similar goal differently, a "
                "conceptual substitute, and a case with no direct "
                "equivalent at all. A well-validated functional alternative "
                "is not the same claim as a poorly-validated exact replay, "
                "even when both are described loosely as 'equivalent.'",
            ),
        ),
        ConceptSection(
            "reading-an-evidence-card",
            "Reading an evidence card",
            (
                "A fully-described claim states: what is being asserted, "
                "the scope it applies to, whether it currently applies, "
                "what source confirms it, what execution evidence exists, "
                "how many experiment runs (and failures) support it, what "
                "human validation covered, any open contradictions, and "
                "where each piece traces back to. Reading a claim this way "
                "-- rather than as a single pass/fail badge -- is what lets "
                "a reader judge how much weight it can actually carry.",
            ),
        ),
        ConceptSection(
            "what-remains-unknown",
            "What remains unknown",
            (
                "An explicit 'unknown' or 'unverified' label is a strength "
                "of this evidence model, not an embarrassment to hide. "
                "Stating plainly that something has not yet been checked, "
                "or that a historical result used a tool version nobody "
                "recorded, is more honest than implying certainty that was "
                "never established. The open questions below are tracked "
                "for the same reason: naming what is still unsettled is "
                "part of the evidence record, not separate from it.",
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

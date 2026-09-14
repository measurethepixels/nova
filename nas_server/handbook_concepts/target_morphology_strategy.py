"""Group D2 concept page: how target morphology should and should not shape
NOVA's processing decisions.

Synthesized from Phase-2 research packet P32 (2026-08-28), researched
against SeeStar-db `e6f359d3689f331ed195a574fafe1f99845739ab`, ontology
v1.3. Cross-cutting strategy layer, not a replacement for the individual
process-family articles it routes to.
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
        "packet-p32",
        "Phase-2 research packet P32, 2026-08-28",
        "packet:P32:2026-08-28",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-processing-ontology",
        "NOVA processing ontology v1.3, object-type buckets",
        "nas_server/processing_ontology.json",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-tool-params",
        "NOVA adaptive parameter computation by object type",
        "nas_server/tool_params.py",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-object-type-inference",
        "NOVA name-derived object-type inference",
        "nas_server/auto_process.py:_object_type_from_name",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nova-experiment-priors-by-type",
        "NOVA historical Experiment Mode priors partitioned by object type",
        "nas_server/experiments.py:get_learned_defaults",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "packet-p27-globular-evidence",
        "Phase-2 research packet P27, contradictory globular-cluster curve evidence",
        "packet:P27",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
    EvidenceReference(
        "nasa-hubble-galaxy-morphology",
        "NASA/Hubble spiral galaxy structure (bulge, arms, dust, star formation)",
        "https://science.nasa.gov/asset/hubble/spiral-galaxy-ngc-2841/",
        ProvenanceLabel.PRIMARY_RESEARCH_SUPPORTED,
    ),
    EvidenceReference(
        "esa-hubble-reflection-nebula",
        "ESA/Hubble: reflection nebulae are scattered starlight, not self-emission",
        "https://www.esa.int/ESA_Multimedia/Images/2025/07/The_young_stars_of_Taurus",
        ProvenanceLabel.PRIMARY_RESEARCH_SUPPORTED,
    ),
    EvidenceReference(
        "nasa-planetary-nebula-structure",
        "NASA: planetary nebulae as ionized gas with shells, knots, filaments",
        "https://science.nasa.gov/missions/hubble/stellar-voyage-of-a-butterfly-like-planetary-nebula/",
        ProvenanceLabel.PRIMARY_RESEARCH_SUPPORTED,
    ),
    EvidenceReference(
        "nasa-hubble-star-clusters",
        "NASA/Hubble: open clusters are loose/resolvable, globulars are dense/centrally concentrated",
        "https://science.nasa.gov/mission/hubble/science/universe-uncovered/hubble-star-clusters/",
        ProvenanceLabel.PRIMARY_RESEARCH_SUPPORTED,
    ),
    EvidenceReference(
        "target-morphology-page",
        "Handbook concept page: Target Morphology Strategy",
        "handbook:target-morphology-strategy",
        ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
    ),
)

_CLAIMS = (
    Claim(
        "ontology-priority-table-is-not-the-full-runtime-story",
        "Ontology v1.3's seven object-type buckets each list only two or "
        "three named 'priority qualities' and one stretch preference, but "
        "current NOVA code applies object type far more broadly than that "
        "summary implies -- among other operations, it changes MLT scale, "
        "BlurXTerminator nonstellar sharpening strength, "
        "HistogramTransformation target-background priors, Statistical "
        "Stretch's target range, STF's target background, GHS alpha, "
        "saturation boost, iHDR/HDRMT aggressiveness, LHE/CLAHE "
        "aggressiveness, and dark-structure-enhancement strength. The "
        "three-item ontology table is a small intent vocabulary, not a "
        "complete description of what object type actually does at "
        "runtime.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("nova-processing-ontology", "nova-tool-params"),
    ),
    Claim(
        "object-type-is-often-inferred-from-target-name-not-measured",
        "When no explicit object type is supplied, NOVA infers one from "
        "the target's name or catalog identifier -- a practical routing "
        "heuristic, not an astronomical classifier. It can misclassify "
        "mixed fields (a cluster embedded in nebulosity), galaxy groups "
        "or interacting systems, generic or unusual names, and mosaics "
        "where the named target occupies only part of the frame. A wrong "
        "inferred type does not necessarily invalidate a run, but it "
        "means every morphology-derived setting and prior was applied "
        "under the wrong hypothesis, so the effective type used -- not "
        "just the target name -- is what later analysis and provenance "
        "must record.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("nova-object-type-inference",),
    ),
    Claim(
        "object-type-partitions-experiment-priors-but-does-not-fix-confounding",
        "NOVA's historical Experiment Mode priors are queried by both "
        "step and object type, so a galaxy result is not blindly pooled "
        "with a globular-cluster result. That partitioning is "
        "directionally sound, but it does not by itself correct for "
        "selection bias, repeated non-independent targets, code or "
        "candidate-set version drift, or evaluator drift -- a morphology-"
        "partitioned historical win fraction is still not statistical "
        "confidence.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("nova-experiment-priors-by-type",),
    ),
    Claim(
        "globular-curve-preference-is-contradicted-by-later-runs-not-validated",
        "Preserved run history shows real disagreement rather than a "
        "settled globular-cluster preset: earlier M13/C80 runs recorded "
        "favorable experience with a particular curve strategy, but a "
        "later M12 run showed that same style of curve darkening the "
        "halo while brightening upper percentiles, and an M3 run showed "
        "a globular-core-rolloff curve crushing the sky while leaving "
        "the core near the top of the range. This is retained "
        "specifically because it is the concrete case against treating "
        "any morphology class as having one validated best method -- "
        "'globulars prefer curve X' is not supported by this evidence; "
        "'globular morphology creates crowding, core-compression, and "
        "halo-loss risks worth testing for' is what the evidence "
        "actually supports.",
        EvidenceOrigin.SOURCE_CONFIRMED,
        ("packet-p27-globular-evidence",),
    ),
)

_UNRESOLVED_GATES = (
    "Whether object type should become multi-label -- current single-type "
    "routing cannot represent an open cluster embedded in reflection "
    "nebulosity, a galaxy with significant integrated flux nebulosity, or "
    "a compact nebula dominated by a stellar field, as anything other "
    "than one primary class.",
    "Whether the effective object type used for a run (as opposed to the "
    "target name) is persisted explicitly enough with every experiment "
    "record to distinguish a user-supplied type from a name-inferred one "
    "after the fact.",
    "How accurate name-based object-type inference actually is across "
    "the real target corpus -- this needs a labeled audit rather than "
    "confidence built from a handful of well-known Messier examples.",
    "Which current ontology stretch preferences (galaxy to GHS, "
    "reflection nebula to Veralux, and so on) have controlled, repeated "
    "evidence behind them versus simply being the current unexamined "
    "default; until audited they remain priors, not conclusions.",
    "Which object-type-conditioned numeric differences (target "
    "background levels, sharpening multipliers, contrast aggressiveness) "
    "survive after blocking by SNR, filter/capture type, pixel scale, "
    "and target occupancy -- the real experiment question morphology "
    "partitioning alone does not answer.",
)

CONCEPT_ARTICLE = ConceptArticle(
    concept_id="target-morphology-strategy",
    schema_version=SCHEMA_VERSION,
    revision=1,
    title="Target Morphology Strategy",
    subtitle=(
        "What kind of object this is should change what NOVA protects "
        "and measures -- not hand it a fixed recipe"
    ),
    summary=(
        "A galaxy, an emission nebula, a reflection nebula, a planetary "
        "nebula, a globular cluster, and an open cluster can all contain "
        "stars, color, gradients, noise, and dynamic range. What "
        "actually differs between them is which structures are expensive "
        "to damage. NOVA uses object type as one input to adaptive "
        "settings and historical priors, but the effective decision "
        "still depends on measured noise, PSF, scale, target occupancy, "
        "clipping, capture/filter type, and processing state -- a "
        "morphology label is a priority and risk layer above the "
        "individual process articles, never a substitute for them."
    ),
    sections=(
        ConceptSection(
            "the-core-principle",
            "The core principle",
            (
                "Object type should change what NOVA protects, measures, "
                "and tests. It should not turn into an unquestioned "
                "preset recipe. A galaxy can have a bright nucleus and "
                "faint outer arms; processing that wins by darkening the "
                "sky can silently erase that outer structure. An "
                "emission nebula's visible signal can occupy most of the "
                "frame, so a background or contrast method can mistake "
                "target signal for sky. A reflection nebula's color is "
                "scattered starlight, not emitted light, so treating its "
                "blue dust as a color cast to neutralize is a category "
                "error. A planetary nebula can be tiny in the frame with "
                "fine shells and a bright core, so whole-frame metrics "
                "can be nearly blind to real damage inside it. A "
                "globular cluster is dominated by a dense, crowded core, "
                "so star shape and halo preservation are the central "
                "risk. An open cluster is more loosely crowded and can "
                "sit inside surrounding nebulosity that a single-label "
                "classification misses entirely. None of this requires a "
                "wholly different pipeline per class -- it requires "
                "knowing what failure would be most expensive for this "
                "kind of object.",
            ),
        ),
        ConceptSection(
            "the-seven-current-buckets",
            "The seven current object-type buckets",
            (
                "Ontology v1.3 defines seven public object-type buckets, "
                "each with a short list of priority qualities and a "
                "stretch preference: galaxy (color balance, detail "
                "level, dynamic range; GHS), emission nebula (noise, "
                "gradient, detail level; Statistical Stretch), reflection "
                "nebula (color balance, gradient; Veralux), globular "
                "cluster (star roundness, noise; Statistical Stretch), "
                "planetary nebula (detail level, star roundness; GHS), "
                "open cluster (star roundness, color balance; "
                "Statistical Stretch), and unknown (overall; Statistical "
                "Stretch). This is a useful small intent vocabulary, but "
                "it badly understates what object type actually changes "
                "at runtime -- see the next section -- and its named "
                "stretch preferences are current NOVA priors, not "
                "physically established rules; the Stretch article's own "
                "evidence-strength labeling remains the authority on "
                "each engine's actual comparative evidence.",
            ),
        ),
        ConceptSection(
            "what-object-type-actually-touches",
            "What object type actually touches",
            (
                "The ontology's compact per-type table is not the "
                "runtime strategy. Beyond the three listed priorities, "
                "object type currently also changes MLT's working scale "
                "for galaxies and nebulae relative to other classes, "
                "BlurXTerminator's nonstellar sharpening strength "
                "(higher for galaxies, lower or default elsewhere), "
                "HistogramTransformation's target-background priors, "
                "Statistical Stretch's target range (darker for "
                "galaxies), STF's target background (lower for galaxies "
                "and clusters), GHS's alpha (higher for galaxies), color "
                "saturation's computed boost (higher for nebula classes), "
                "iHDR/HDRMT aggressiveness (more for nebulae, less for "
                "clusters), LHE/CLAHE local-contrast aggressiveness "
                "(lower for smooth nebular structure, more permissive "
                "for galaxies), and dark-structure enhancement (boosted "
                "for galaxies, reduced for clusters). Treating morphology "
                "strategy as only 'priority qualities plus a stretch "
                "engine' misses most of what it actually does.",
            ),
        ),
        ConceptSection(
            "type-is-often-a-guess-not-a-measurement",
            "Type is often a guess, not a measurement",
            (
                "When a run has no explicit object type, NOVA infers one "
                "from the target's name or catalog identifier. That is a "
                "practical routing mechanism, not an astronomical "
                "classifier, and it can be wrong for mixed fields (a "
                "cluster with surrounding emission or reflection "
                "nebulosity), galaxy groups or interacting systems, "
                "generic names with no recognizable class token, targets "
                "whose catalog identity does not match the visually "
                "dominant structure in the crop, or mosaics where the "
                "named target occupies only part of the field. A wrong "
                "inferred type does not necessarily make a run invalid, "
                "but every morphology-derived setting and every "
                "historical prior consulted for that run was applied "
                "under the wrong hypothesis -- which is why the "
                "*effective* object type used, not merely the target "
                "name, is what provenance needs to record.",
            ),
        ),
        ConceptSection(
            "historical-priors-are-partitioned-not-proven",
            "Historical priors are partitioned, not proven",
            (
                "NOVA's Experiment Mode looks up historical priors by "
                "both processing step and object type, so a galaxy "
                "result is not pooled blindly with a globular-cluster "
                "result. That is a directionally sound partition, but it "
                "does not repair the underlying evidence problems this "
                "Handbook already names elsewhere: historical win "
                "fraction is still not statistical confidence, "
                "morphology partitioning does nothing to correct for "
                "selection bias, repeated non-independent runs on the "
                "same target, or drift in code, candidate set, or "
                "evaluator between runs.",
            ),
        ),
        ConceptSection(
            "the-globular-case-study",
            "The globular cluster case study",
            (
                "This is not a hypothetical risk. Preserved run history "
                "for globular clusters records real disagreement rather "
                "than a settled preset: an earlier globular curve "
                "strategy had favorable M13/C80 experience behind it, "
                "but a later M12 run showed that same style of curve "
                "darkening the halo while brightening upper percentiles, "
                "and an M3 run showed a globular-core-rolloff curve "
                "crushing the sky while leaving the core near the top of "
                "the range. The correct conclusion from this evidence is "
                "not 'globulars prefer curve X' -- it is that globular "
                "morphology creates predictable risks (crowding, core "
                "compression, star-shape damage, halo loss) that justify "
                "cluster-specific experiments and protected regions of "
                "interest, evaluated fresh each time rather than assumed "
                "settled.",
            ),
        ),
        ConceptSection(
            "morphology-by-object-class",
            "What each morphology makes expensive to damage",
            (
                "Galaxy -- core-to-outer-structure dynamic range, fine "
                "detail in arms and dust lanes where SNR supports it, "
                "faint structure near the sky floor, and broadband color "
                "balance; watch for a darker background quietly erasing "
                "the outer halo or arms.",
                "Emission nebula -- low-surface-brightness diffuse and "
                "filamentary structure that can occupy most of the "
                "frame; watch for background or gradient methods mistaking "
                "real field-filling nebulosity for sky, and for noise "
                "reduction erasing genuine faint filaments alongside "
                "noise.",
                "Reflection nebula -- faint dust and smooth low-surface-"
                "brightness structure whose broadband color is scattered "
                "starlight, not emitted light; watch for gradient "
                "extraction removing dust that fills the frame, and for "
                "blue/cool color being misread as a cast to neutralize.",
                "Planetary nebula -- small-scale nonstellar structure, "
                "central-star and shell integrity, and local dynamic "
                "range in a target that can be a tiny fraction of the "
                "frame; watch for whole-frame metrics being nearly blind "
                "to real damage inside the target itself.",
                "Globular cluster -- star roundness, crowded-core "
                "separation without ringing or false deblending, "
                "controlled core brightness, and halo preservation; "
                "watch for a darker sky improving apparent contrast "
                "while deleting real halo stars, and for raw star count "
                "or lower FWHM rewarding fragmentation or ringing rather "
                "than genuine resolution.",
                "Open cluster -- star shape and color, a clean but not "
                "artificially crushed background, and less aggressive "
                "core compression than a globular cluster deserves; "
                "watch for surrounding reflection or emission nebulosity "
                "(the Pleiades is the standing example) making a pure "
                "cluster treatment incomplete.",
                "Unknown/other -- a safe routing fallback, not a "
                "morphology claim; evidence produced under 'unknown' "
                "should stay visibly tagged as such, since its adaptive "
                "parameter path can differ from a correctly classified "
                "target.",
            ),
        ),
        ConceptSection(
            "confounders-that-break-a-morphology-claim",
            "Confounders that break a morphology claim",
            (
                "A claim like 'galaxies benefit from stronger GHS' or "
                "'globulars prefer Statistical Stretch' needs more than "
                "a pile of target wins to be credible. The recurring "
                "traps are: capture/filter confounding, since emission-"
                "nebula targets skew heavily toward LP or narrowband "
                "capture, so an apparent morphology preference can "
                "actually be a filter/channel-mapping preference; "
                "target-size/occupancy confounding, since a tiny "
                "planetary nebula and a frame-filling emission nebula "
                "give whole-frame metrics radically different "
                "sensitivity even though both are labeled 'nebula'; "
                "SNR/integration confounding, since a morphology class "
                "can correlate with typical integration time, so a "
                "denoise or sharpen win may reflect SNR rather than "
                "morphology; scale/resampling confounding, since native, "
                "drizzled, and mosaicked images give the same pixel-"
                "valued radius or tile parameter a different angular "
                "meaning; adaptive-tuning confounding, since NOVA "
                "computes parameters from both measurements and object "
                "type together, so a nominal method comparison can "
                "secretly be a tuning-policy comparison; name-"
                "classification confounding, since inferred-type error "
                "can contaminate a learned morphology prior at its "
                "source; and repeated-target dependence, since ten runs "
                "of the same galaxy are not ten independent replications "
                "of 'galaxy' as a class.",
            ),
        ),
        ConceptSection(
            "mixed-fields-break-a-single-label",
            "Mixed fields break a single label",
            (
                "The Pleiades is the clean public example of why a "
                "single categorical type cannot fully describe "
                "processing risk: it is simultaneously an open cluster "
                "and a reflection-nebula field, and a treatment aimed "
                "purely at star contrast will lose the surrounding dust. "
                "The same problem recurs for a galaxy with significant "
                "integrated flux nebulosity, a globular or open cluster "
                "with a background emission complex behind it, or a "
                "compact nebula embedded in a dense stellar field. Where "
                "a mixed field is recognized, the right response is to "
                "protect both the star and diffuse-signal regions of "
                "interest rather than commit to one class's recipe.",
            ),
        ),
        ConceptSection(
            "stronger-and-weaker-uses-of-morphology",
            "Stronger and weaker uses of morphology",
            (
                "Morphology is on solid ground when it changes which "
                "regions or features get protected, which failure modes "
                "count as expensive (ringing, star merging, faint-signal "
                "loss, gradient overfit, core clipping), which metrics "
                "and regions of interest carry weight, which candidate "
                "families are worth testing at all, and what a "
                "reasonable starting range looks like before measured "
                "image state constrains it further. It is on much "
                "weaker ground whenever it is used alone to justify one "
                "universally best stretch engine, one fixed target sky "
                "level, one sharpening or denoise strength, one "
                "saturation amount, one HDR iteration count, one CLAHE "
                "setting, one curves preset, one background-extraction "
                "model, or one palette/color treatment -- those "
                "decisions depend on measured SNR, PSF, scale, target "
                "occupancy, capture/filter type, linear/nonlinear state, "
                "clipping, background structure, engine/version, and "
                "prior processing, not on the object-type label alone.",
            ),
        ),
        ConceptSection(
            "how-this-page-is-meant-to-be-used",
            "How this page is meant to be used",
            (
                "This page does not duplicate tool instructions -- it "
                "routes to them. Stretch, Background Extraction, Color "
                "Calibration, Deconvolution, Denoise, and Star Correction "
                "each define their own methods, evidence, and failure "
                "modes; morphology only changes which of their concerns "
                "matter most for a given target and which regions of "
                "interest should be checked before accepting a result. "
                "If a claim here and a process article's own evidence "
                "ever disagree about a specific tool's comparative "
                "performance, the process article's evidence-strength "
                "labeling governs, since it is the article actually "
                "scoped to evaluate that tool.",
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

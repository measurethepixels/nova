"""Handbook Phase 3, Group F: Bright-Star Halo Suppression.

Synthesized from Phase-2 research packet P30 Part B (2026-08-28, SeeStar-db
`e6f359d3689f331ed195a574fafe1f99845739ab`). P30 explicitly recommends
publishing dark-structure enhancement and halo suppression as two separate
Handbook concepts -- this article covers only the latter; see
`dark_structure_enhancement.py` for the former. P30's completion comment on
#457 is authoritative per that issue's completion-evidence rule.

Corrected 2026-09-09 during re-verification against current source: P30's
research inspected only the raw upstream SASpro `compute_halo_b_gon()` and
concluded there is "no explicit star detector... in the inspected
current-upstream core." That is accurate for the raw upstream function, but
NOVA's own `seti_astro.halo_suppress()` wrapper does not call it naively --
it already builds a real bright-star luminance mask (99.5th-percentile
threshold, Gaussian-blurred annulus scaled by reduction level) and blends
the halo-reduced result in only around bright stars, specifically because
NOVA's own engineers discovered the raw upstream function degenerates into
global gamma darkening when used headlessly (its own lightness mask divides
by 255 with no PixInsight-GUI-applied star mask to compensate). This
star-confinement code (commit 1799c06, 2026-05-30) predates the P30 research
snapshot -- it is a real research gap in the original packet, not source
drift since it was written. The real IC 1805 failure evidence P30 cites
(critiques/20260707_080122_seestar_nebula.md, workflow 1.17.2, dated after
the mask fix) is preserved and re-framed accordingly: it is evidence the
star-confined masking approach itself can still fail catastrophically on a
sufficiently dense field, not evidence that NOVA applies an unmasked global
transform.
"""

from __future__ import annotations

from nas_server.handbook_contract import (
    SCHEMA_VERSION,
    EquivalenceClass,
    EvidenceReference,
    HandbookArticle,
    ProcessFamily,
    ProvenanceLabel,
    ToolGuidance,
)

P30_PACKET = EvidenceReference(
    reference_id="packet-p30-halo",
    title="Phase-2 research packet P30 (Part B: Bright-Star Halo Suppression), 2026-08-28",
    locator="packet:P30:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_HALO_SOURCE = EvidenceReference(
    reference_id="nova-halo-source",
    title="NOVA halo_suppress() star-confined masking wrapper, correcting the raw upstream global-darkening defect",
    locator="nas_server/seti_astro.py:halo_suppress (commit 1799c06, 2026-05-30)",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

HALO_NL_STEPS_GAP = EvidenceReference(
    reference_id="nova-halo-nl-steps-gap",
    title="halo_suppression absent from _NL_STEPS -- adaptive severity computation unreachable in standard Experiment Mode",
    locator="nas_server/experiments.py:_NL_STEPS,_STEP_CFG; nas_server/tool_params.py:compute_halo_suppress",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

IC1805_HALO_FAILURE_EVIDENCE = EvidenceReference(
    reference_id="nova-ic1805-halo-failure",
    title="IC 1805 (16,133-star dual-band field): level-1 halo suppression increased background noise 129% and produced colored rings, despite NOVA's star-confined mask",
    locator="critiques/20260707_080122_seestar_nebula.md (workflow 1.17.2)",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

M66_HALO_NOOP_EVIDENCE = EvidenceReference(
    reference_id="nova-m66-halo-noop",
    title="M66: halo_suppression, dark_enhance, CLAHE, and HDR-core-blend all lost to do-nothing on a clean stack",
    locator="critiques/20260715_194614_seestar_galaxy.md",
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

SASPRO_HALOBGON_UPSTREAM = EvidenceReference(
    reference_id="saspro-halobgon-upstream",
    title="Seti Astro Suite Pro current upstream Halo-B-Gon source (version-bound, not the pinned install)",
    locator="setiastro/setiastrosuitepro src/setiastro/saspro/halobgon.py, ref c2c941b88335c82cc42ced4a2016c43261610918",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

HALO_SUPPRESSION = HandbookArticle(
    article_id="bright-star-halo-suppression",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.HALO_SUPPRESSION,
    purpose=(
        "Optionally reduce visually distracting bright-star halo/bloat and "
        "color rings after the image is nonlinear. This is a cosmetic/"
        "artifact-mitigation finishing transform, not PSF recovery, "
        "deconvolution, or star correction -- and the correct action is "
        "frequently a no-op. It is a separate concept from dark-structure "
        "enhancement (a different article): different objective, different "
        "spatial target, different algorithm, different failure modes."
    ),
    observable_symptoms=(
        "One or a few bright stars have clearly measurable extended halo/"
        "bloat or color rings that dominate the presentation, and that halo "
        "survives comparison against the unchanged parent rather than being "
        "primarily a recombination, stretch, or optical/PSF defect.",
    ),
    intended_output=(
        "Reduced bright-star halo/bloat/color-ring severity confined to the "
        "affected stars, with local nebulosity/companions, background "
        "level, and field-wide color preserved outside the intended star "
        "regions. \"No halo suppression\" is a legitimate, often correct, "
        "outcome -- not a failure to finish the image."
    ),
    limits=(
        "Current upstream SASpro `compute_halo_b_gon()` contains no "
        "explicit star detector, stellar catalog, annulus measurement, PSF "
        "fit, or chromatic halo model -- it forms a grayscale/lightness "
        "image, builds an unsharp-derived lightness mask, constructs an "
        "inverse mask whose strength grows with level, multiplies the "
        "image by that mask, and applies a level-dependent gamma LUT "
        "(approximately gamma 1.2/1.5/1.8/2.2 for levels 0-3). Used "
        "headlessly with no PixInsight-GUI-applied star mask, that lightness "
        "mask divides an already-[0,1] image by 255, collapsing it toward "
        "zero and degenerating the operation into *global* gamma darkening "
        "that crushes the whole image.",
        "NOVA does not expose that raw defect to users. `seti_astro."
        "halo_suppress()` explicitly documents and works around it: it "
        "builds its own bright-star luminance mask (99.5th-percentile "
        "threshold on a grayscale/lightness map, Gaussian-blurred with "
        "sigma scaled by reduction level to cover the surrounding halo "
        "annulus) and blends the halo-reduced result in only where that "
        "mask is nonzero, leaving galaxy/background tone at mask~0 "
        "essentially untouched. This is real, current, already-live "
        "production behavior -- not something this article proposes -- and "
        "it directly corrects the framing that NOVA applies an "
        "undifferentiated global transform.",
        "Despite that star-confinement, halo suppression can still fail "
        "catastrophically on a sufficiently dense field: a real IC 1805 run "
        "(16,133-star dual-narrowband field, workflow 1.17.2, run after the "
        "star-confinement mask was already in place) shows level-1 halo "
        "suppression increasing background noise by ~129% (0.0283 to "
        "0.0647) and driving p99.9 from ~0.499 to ~0.997, with false "
        "magenta/cyan/green rings. In a field that dense, the bright-star "
        "mask can cover enough of the frame -- and enough mask-boundary "
        "transitions accumulate -- that the confinement stops meaningfully "
        "limiting the effect. This is strong condition-specific failure "
        "evidence for dense fields, not proof that Halo-B-Gon always fails "
        "or that a specific star-count threshold is universally safe.",
        "Ontology declares a public range of reduction levels 1-2 (default "
        "1, \"never use 3+\"), but `halo_minimal` passes reduction_level=0, "
        "and level 0 still applies gamma 1.2 under the raw upstream "
        "transform -- it is a real nonzero treatment, not a no-op. Only the "
        "explicit `none` candidate is a true unchanged control.",
        "`compute_halo_suppress()` in `tool_params.py` derives a severity "
        "heuristic from median stellar FWHM and large-star fraction and can "
        "compute reduction level up to 3, in tension with the ontology's "
        "own \"never use 3+\" guidance. But standard Experiment Mode does "
        "not currently invoke that adaptive computation at all: "
        "`halo_suppression` has a real entry in `_STEP_CFG`, yet is absent "
        "from the `_NL_STEPS` set the caller checks before applying "
        "adaptation -- confirmed still true as of this revision "
        "(2026-09-09). The standard path therefore runs fixed ontology "
        "candidate levels only; the adaptive severity code is currently "
        "dead for this step.",
        "FWHM and large-star fraction are proxy heuristics for \"stars "
        "might be bloated,\" not direct measurements of a colored halo "
        "annulus -- high FWHM can come from seeing, focus, sampling, "
        "registration, guiding, or star-stretch choices as easily as from a "
        "genuine broad PSF wing, and neither statistic identifies the cause.",
    ),
    required_input_state=(
        "A nonlinear image, after star recombination/finishing where "
        "applicable, with a clearly identifiable candidate halo defect that "
        "survives comparison against the unchanged parent -- not a defect "
        "actually originating in star correction/deconvolution, star split/"
        "recombination, or the stretch stage itself, which should be fixed "
        "at their own source rather than papered over here.",
    ),
    nova_action=(
        "`seti_astro.halo_suppress(reduction_level=1, is_linear=False)` "
        "calls SASpro's `compute_halo_b_gon()` to get the raw level-"
        "dependent transform, then separately builds a bright-star mask: "
        "grayscale/lightness conversion, threshold at the 99.5th "
        "percentile, Gaussian blur with sigma `6.0 + 3.0*reduction_level` "
        "to cover the surrounding annulus, gamma-shaped to 0.6, and blends "
        "`original*(1-mask) + halo_result*mask` so only bright-star regions "
        "receive the transform. Production overrides `reduction_level` per "
        "workflow/object-type policy; the ontology's `halo_minimal` "
        "candidate (level 0) still applies a real, nonzero gamma transform "
        "within that mask. The `_NL_STEPS`-gap means the standard "
        "Experiment Mode path uses fixed ontology candidate levels rather "
        "than the adaptive FWHM/large-star-fraction severity computation."
    ),
    nova_evidence_ids=(
        "nova-halo-source",
        "nova-halo-nl-steps-gap",
        "nova-ic1805-halo-failure",
        "nova-m66-halo-noop",
    ),
    use_when=(
        "One or a few bright stars have a clearly measurable extended halo/"
        "ring that dominates the presentation and survives comparison "
        "against the unchanged parent, local nebulosity/companions can be "
        "protected and checked, and a low-strength treatment produces a "
        "specific annular improvement without damaging the rest of the "
        "field.",
    ),
    skip_when=(
        "The field is dense/crowded -- the real IC 1805 failure evidence is "
        "strong condition-specific reason for caution here, though it does "
        "not establish a universal star-count threshold.",
        "There is no obvious halo defect, the apparent issue is actually "
        "star shape/PSF (belongs in earlier restoration, not here), the "
        "field contains faint signal under/near bright stars the broad "
        "transform could erase, or color rings/background noise/clipping "
        "worsen relative to the unchanged parent.",
        "A clean stack simply does not need finishing -- a later M66 run "
        "recorded halo suppression, dark-structure enhancement, CLAHE, and "
        "HDR-core-blend all losing to do-nothing.",
    ),
    scientific_and_aesthetic_notes=(
        "A rigorous halo-suppression comparison needs the exact same "
        "nonlinear parent after star recombination/finishing, an explicit "
        "`none` arm, candidate labels hidden from human/vision evaluators "
        "when practical, the actual reduction level and algorithm/package "
        "version recorded, star/annulus ROIs defined before judging the "
        "candidate, the same preview stretch/render for all arms, and no "
        "downstream curves/saturation differences before measurement. Block "
        "or stratify by star density, bright-star magnitude/intensity "
        "distribution, filter type/chromatic halo behavior, parent stretch "
        "and star-stretch/recombination strategy, seeing/focus/PSF, native "
        "vs. drizzled pixel scale, and whether the apparent halo is optical "
        "scatter, chromatic aberration, star-removal residue, recombination "
        "artifact, or simply a broad saturated star profile -- different "
        "causes should not be pooled as if one process has one response.",
        "Lower FWHM, fewer detected stars, darker p99/p99.9, reduced "
        "whole-frame entropy/high-frequency power, an improved \"star "
        "roundness\" score, a prettier perceptual result, lower saturation "
        "around a bright star, or historical winner frequency can all be "
        "achieved by globally darkening/shrinking stars or deleting faint "
        "companions -- none of them alone demonstrates a corrected halo.",
        "\"No halo suppression\" is often the most faithful choice, not a "
        "failure to finish an image -- teach the no-op result as a "
        "legitimate, frequently correct outcome.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="star-confined mask wrapper around SASpro Halo-B-Gon; adaptive severity computation currently unreachable in standard Experiment Mode",
            host="NOVA Python pipeline",
            instructions=(
                "Describe NOVA's halo suppression as a star-confined "
                "masked blend built specifically to correct the raw "
                "upstream global-darkening defect -- not as an "
                "undifferentiated global transform.",
                "Record the actual reduction level and whether the "
                "field-density condition resembles the IC 1805 failure "
                "case before trusting a low-level result on a dense field.",
                "Do not treat `halo_minimal` (level 0) as a no-op -- only "
                "the explicit `none` candidate is a true unchanged control.",
                "Do not assume the adaptive FWHM/large-star-fraction "
                "severity computation ran -- it is currently unreachable "
                "in the standard path (`halo_suppression` absent from "
                "`_NL_STEPS`), so fixed ontology candidate levels are what "
                "actually executed.",
            ),
            controls_and_starting_ranges=(
                ("halo_minimal", "reduction_level 0 -- a real nonzero gamma transform within the star mask, not a no-op"),
                ("halo_mild / halo_standard", "reduction_level 1 / 2 -- ontology's declared safe range"),
                ("none", "true control"),
            ),
            expected_result=(
                "Reduced halo/ring severity confined to bright-star "
                "regions, with background level, local nebulosity, and "
                "field-wide color outside those regions materially "
                "unaffected."
            ),
            failure_modes=(
                "Colored rings around stars, broad field darkening, or "
                "crushed faint stars -- especially on dense fields, per "
                "the real IC 1805 evidence.",
                "Destroyed faint nebulosity near stars from the mask "
                "covering more of the frame than intended on a crowded "
                "field.",
                "Apparent FWHM improvement caused by lost profile wings "
                "rather than a genuinely corrected halo.",
            ),
            recovery=("Revert to the unchanged parent; if the defect originates upstream, fix the true cause -- star correction/deconvolution for PSF shape, a different star split/recombination strategy for star-removal artifacts, or local/masked finishing for one isolated bright halo.",),
            mask_support="Native and central to the mechanism -- a bright-star luminance mask (threshold + Gaussian annulus, level-scaled sigma) confines the transform; this is the fix for the raw upstream function's global-darkening defect.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-halo-source", "nova-halo-nl-steps-gap", "nova-ic1805-halo-failure", "nova-m66-halo-noop"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="no dedicated halo-suppression tool independently confirmed as an equivalent packaged tool in this repo",
            host="PixInsight (context only for this family)",
            instructions=(
                "A comparable manual result is achievable via masking/"
                "curves around identified bright stars, but no single "
                "packaged PixInsight tool matching NOVA's specific "
                "star-confinement mask and gamma-LUT mechanism was "
                "confirmed in this repo's research.",
            ),
            controls_and_starting_ranges=(
                ("manual mask + curves/gamma", "build a bright-star mask and apply a local darkening curve only through it"),
            ),
            expected_result="A manually assembled star-confined halo treatment, not a one-to-one NOVA equivalent.",
            failure_modes=("Assuming a manual PixInsight result and NOVA's halo_suppress() are directly comparable without controlling mask geometry and gamma behavior.",),
            recovery=("Document the manual mask/gamma approach explicitly rather than presenting it as an equivalent engine.",),
            mask_support="Fully supported through PixInsight's native masking tools when manually constructed; not a packaged one-call equivalent.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-halo-source",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="Halo-B-Gon (compute_halo_b_gon) is the underlying transform; the star-confinement mask is a NOVA addition, not vendor SASpro behavior",
            host="Seti Astro Suite Pro Halo-B-Gon (as the underlying transform)",
            instructions=(
                "Current upstream source confirms no explicit star/annulus "
                "detection in the core transform -- it is a lightness-mask, "
                "multiply, gamma-LUT pipeline intended for GUI use with a "
                "user-applied star mask. NOVA's headless wrapper supplies "
                "that missing mask itself; treat the raw upstream function "
                "as version-bound context for the underlying transform, not "
                "a description of NOVA's actual effective behavior.",
            ),
            controls_and_starting_ranges=(
                ("reduction_level", "0-3 upstream, gamma approximately 1.2/1.5/1.8/2.2; NOVA's ontology restricts public candidates to 0-2"),
            ),
            expected_result="A level-dependent gamma-LUT darkening transform, subsequently confined by NOVA's external bright-star mask.",
            failure_modes=("Treating the raw upstream transform's behavior as NOVA's actual field-wide effect, ignoring the external confinement mask.",),
            recovery=("Document both the raw transform and NOVA's confinement mask when describing effective treatment.",),
            mask_support="SASpro's own lightness-derived mask is part of the internal transform; NOVA's external bright-star mask is a separate, corrective addition.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("saspro-halobgon-upstream", "nova-halo-source"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="no dedicated halo-suppression tool independently confirmed in this repo",
            host="Siril (context only for this family)",
            instructions=(
                "No packaged Siril tool matching this specific "
                "star-confined halo-suppression strategy was confirmed in "
                "this repo's research.",
            ),
            controls_and_starting_ranges=(
                ("availability", "not currently NOVA's dispatch path for this step"),
            ),
            expected_result="Not applicable to NOVA's current dispatch for this family; documented for completeness only.",
            failure_modes=("Assuming a general Siril tone/star adjustment is evidence about NOVA's halo-suppression candidates.",),
            recovery=("N/A -- not NOVA's current dispatch path for this family.",),
            mask_support="Not applicable to NOVA's current dispatch for this step.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-halo-source",),
        ),
    ),
    measurements=(
        "Bright-star radial intensity profiles at multiple radii, and "
        "annular excess relative to local background.",
        "Channel-specific radial profiles / chromatic ring magnitude, and "
        "halo radius at a defined contrast threshold.",
        "Preservation of stellar core position/shape, neighboring faint "
        "stars, and local nebulosity/galaxy signal behind and around the "
        "star.",
        "Background RMS/MAD away from stars, and new ring/edge artifacts in "
        "the difference image against the unchanged control.",
        "Clipping/highlight occupancy, field-wide color drift outside star "
        "halos, and effect stratified by stellar brightness rather than one "
        "brightest star alone.",
    ),
    acceptance_criteria=(
        "Halo/ring severity is reduced and confined to the affected "
        "bright-star regions, checked against a real unchanged control and "
        "against annular/radial profile measurements around the affected "
        "stars specifically -- not whole-frame statistics alone.",
        "Background level, local nebulosity, and field-wide color outside "
        "the intended star regions are not materially degraded.",
        "The actual reduction level, algorithm/package version, and "
        "whether the field resembles a known dense-field failure condition "
        "(IC 1805-like) are recorded.",
        "`halo_minimal` (level 0) is never described as a no-op -- only the "
        "explicit `none` candidate is.",
        "Lower FWHM, fewer detected stars, darker p99/p99.9, reduced "
        "entropy/high-frequency power, improved \"star roundness,\" or "
        "historical win rate are never presented as standalone proof of a "
        "corrected halo.",
        "A no-op result is recorded as a legitimate finding, not an "
        "incomplete pipeline run.",
    ),
    sources=(
        P30_PACKET,
        NOVA_HALO_SOURCE,
        HALO_NL_STEPS_GAP,
        IC1805_HALO_FAILURE_EVIDENCE,
        M66_HALO_NOOP_EVIDENCE,
        SASPRO_HALOBGON_UPSTREAM,
    ),
)

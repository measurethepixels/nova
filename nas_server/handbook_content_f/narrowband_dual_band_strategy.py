"""Handbook Phase 3, Group G: Narrowband and Dual-Band Color Strategy.

Synthesized from Phase-2 research packet P33 (2026-08-28, SeeStar-db
`cd6f1493a7d8efcbf304669b5d5068af8141ef4c`). P33's own completion conclusion
explicitly recommends publishing this as one umbrella concept rather than
splitting it, as long as the signal/channel layer and the display/palette
layer stay clearly separated -- this article follows that structure.

Re-verified against current source 2026-09-09: `NB_PALETTE_MAX_FLUX_RATIO`
(still 20.0), the `narrowband_hoo()` formula and its XP/RGB-proxy channel
fallback, `narrowband_normalize`'s `o3_boost` auto-scaling formula, the
Foraxx/SHO composite formulas, and the SCNR-skip trigger for all three
narrowband-active paths all confirmed still live and unchanged from the
research snapshot -- no material drift found. One real addition beyond the
packet's own summary: `narrowband_hoo()`'s actual RGB-proxy fallback formula
(`oiii_proxy = clip(0.5*G + 0.5*B - 0.55*Ha, 0, None)`, Gaussian-blurred and
normalized by its own 99.5th percentile) and the separate STF stretch
applied to XP Ha/OIII channels before the cap is computed, neither of which
the packet's summary spelled out in full.
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

P33_PACKET = EvidenceReference(
    reference_id="packet-p33",
    title="Phase-2 research packet P33, 2026-08-28",
    locator="packet:P33:2026-08-28",
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

NOVA_NARROWBAND_SOURCE = EvidenceReference(
    reference_id="nova-narrowband-source",
    title="NOVA nb_palette, narrowband_normalize, narrowband_hoo, and narrowband_composite implementations",
    locator=(
        "nas_server/seti_astro.py:narrowband_hoo; nas_server/auto_process.py "
        "NB_PALETTE_MAX_FLUX_RATIO, SCNR-skip logic; "
        "nas_server/processing_ontology.json nb_palette,narrowband_norm,narrowband_hoo,narrowband_composite"
    ),
    provenance=ProvenanceLabel.NOVA_SOURCE_CONFIRMED,
)

IC1805_FLUX_RATIO_EVIDENCE = EvidenceReference(
    reference_id="nova-ic1805-flux-ratio-evidence",
    title="IC 1805 real-run flux-ratio ~157 motivating the 20:1 nb_palette gate",
    locator=(
        "critiques/WORKFLOW_CHANGELOG.md, 1.9.0 -- 2026-06-12 entry, "
        "\"Extreme-flux-ratio fallback (NB_PALETTE_MAX_FLUX_RATIO = 20)\" "
        "(measured IC 1805 ratio 157, OIII contrast 0.89 vs Ha 3.06, no morphology)"
    ),
    provenance=ProvenanceLabel.NOVA_EXECUTION_RECORD,
)

HENRY_IC1805_REJECTION_QUOTE = EvidenceReference(
    reference_id="henry-ic1805-black-sky-rejection",
    title="Henry's IC 1805 bicolor-render rejection: 'Those all look bad. The sky should be black.' (2026-06-12)",
    locator=(
        "Henry, 2026-06-12 -- recorded contemporaneously as a source comment "
        "in nas_server/auto_process.py near NB_PALETTE_MAX_FLUX_RATIO usage; "
        "a code comment, not an independent execution/review record"
    ),
    provenance=ProvenanceLabel.HENRY_VALIDATED,
)

ZWO_SEESTAR_DUOBAND_SPEC = EvidenceReference(
    reference_id="zwo-seestar-duoband-spec",
    title="ZWO SeeStar S50 official duo-band filter specification (OIII 30nm / H-alpha 20nm)",
    locator="https://www.zwoastro.com/product/seestar-s50/",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

GAIA_DR3_XP_DOCUMENTATION = EvidenceReference(
    reference_id="gaia-dr3-xp-documentation",
    title="ESA Gaia DR3 BP/RP (XP) low-resolution spectrophotometry documentation",
    locator="https://www.cosmos.esa.int/web/gaia/dr3-what-colour-do-they-have",
    provenance=ProvenanceLabel.VENDOR_DOCUMENTED,
)

NARROWBAND_DUAL_BAND_STRATEGY = HandbookArticle(
    article_id="narrowband-dual-band-strategy",
    schema_version=SCHEMA_VERSION,
    revision=1,
    process_family=ProcessFamily.NARROWBAND_DUAL_BAND_STRATEGY,
    purpose=(
        "Answer two separate questions that narrowband/dual-band color work "
        "conflates if not taught explicitly: what emission-line signal do we "
        "think is actually present (the channel/signal layer), and how "
        "should that signal be mapped into display RGB (the palette layer)? "
        "NOVA has four related but non-alias strategies -- `nb_palette`, "
        "`narrowband_norm`, `narrowband_hoo`, and `narrowband_composite` -- "
        "that answer these two questions differently depending on what "
        "channels are actually available."
    ),
    observable_symptoms=(
        "The SeeStar S50's built-in duo-band filter or a set of true mono "
        "narrowband masters produced Ha/OIII(/SII) signal that needs a "
        "color-mapping decision distinct from ordinary broadband color "
        "calibration.",
    ),
    intended_output=(
        "A displayed color image whose channel provenance (measured, "
        "model-derived, or an RGB proxy) and palette mapping (HOO, "
        "synthetic SHO, Foraxx, true SHO) are both explicit and correctly "
        "labeled -- never presenting a model-derived channel estimate as "
        "measured spectroscopy, and never presenting a two-channel "
        "synthetic SHO as a true three-channel acquisition."
    ),
    limits=(
        "The SeeStar S50's duo-band filter is documented at approximately "
        "30nm around OIII and 20nm around H-alpha -- far wider than a "
        "classical 3-7nm mono narrowband filter, and it feeds an OSC Bayer "
        "sensor, not separate monochrome exposures. The resulting stack is "
        "not three independently measured emission-line channels: OIII near "
        "500.7nm registers across both green and blue Bayer photosites, and "
        "SII is not present as a dedicated measured channel at all in an "
        "ordinary SeeStar Ha/OIII capture. Any \"SHO-like\" display "
        "synthesized from only Ha and OIII must be labeled synthetic/"
        "pseudo-SHO, never true three-channel SHO.",
        "`nb_palette` uses Gaia-DR3-XP-informed channel extraction: a "
        "mixing matrix fit with non-negative-least-squares behavior against "
        "stars with known XP spectra, then applied to unmix the image into "
        "Ha/OIII component estimates. This is stronger than a naive RGB "
        "proxy, but the output remains a model-derived unmixing, not direct "
        "spatial spectroscopy of the nebula -- label it \"XP-calibrated / "
        "model-derived Ha and OIII component estimates,\" never \"ground-"
        "truth Ha/OIII.\" The fit is sensitive to which stars are available "
        "in the field, their color distribution, extinction, and catalogue/"
        "photometric matching quality; non-negative constraints prevent "
        "physically nonsensical negative estimates but do not prove "
        "uniqueness or correctness.",
        "`NB_PALETTE_MAX_FLUX_RATIO = 20.0` skips `nb_palette` when the "
        "extracted Ha/OIII flux ratio exceeds it, falling back to "
        "`narrowband_norm`. This is a real, source-confirmed project "
        "heuristic with a real motivating case (IC 1805 measured a ratio "
        "around 157, and Henry rejected the resulting renders outright: "
        "\"Those all look bad. The sky should be black.\", 2026-06-12) -- "
        "it is not an externally validated universal physical threshold, "
        "and must not be presented as one.",
        "`nbp_hoo_aggressive` is not merely a stronger HOO candidate -- it "
        "switches the stretch philosophy from linked to per-channel, which "
        "can materially change apparent line ratios independent of palette "
        "choice. A result comparing it against `nbp_hoo` is a strategy "
        "comparison (palette AND stretch philosophy changed together), not "
        "a clean palette-only comparison.",
        "`narrowband_hoo()`'s bounded-HOO cap (`O = min(OIII, oiii_cap * "
        "Ha)`, default `oiii_cap=0.85`) can execute on two materially "
        "different channel sources with the identical candidate label: "
        "real Gaia-XP-unmixed channels (`run_dir/xp_ha.fit` + "
        "`xp_oiii.fit`, preferred) when present, or an RGB-proxy fallback "
        "(`clip(0.5*G + 0.5*B - 0.55*Ha, 0, None)`, Gaussian-blurred and "
        "normalized by its own 99.5th percentile) when they are not. The "
        "function records which source ran (`oiii_source: \"xp_oiii\"` or "
        "`\"rgb_proxy\"`), and that provenance must be part of any cited "
        "evidence -- the two are not equivalent treatments despite sharing "
        "a candidate ID.",
        "`narrowband_normalize`'s `o3_boost` is auto-scaled by "
        "`clamp(0.50 + 0.395*ha_dominance_ratio, 1.25, 1.70)`, lifting weak "
        "OIII more strongly on more Ha-dominant fields. Boosting recorded "
        "weak OIII improves its visibility/balance -- it cannot recover "
        "OIII that was never captured above the noise floor, and a more "
        "balanced-looking palette is not evidence of more physically "
        "accurate line flux.",
        "SCNR is deliberately skipped whenever `narrowband_norm`, "
        "`narrowband_hoo`, or `nb_palette` is active, because OIII "
        "registers across both green and blue OSC response and broad green "
        "suppression would remove legitimate teal/OIII signal as if it "
        "were a color cast. A fair comparison between any two narrowband "
        "candidates must record whether SCNR was actually skipped for "
        "both -- comparing one candidate with SCNR suppressed against one "
        "without is not a palette-only comparison.",
        "`narrowband_hoo` and `narrowband_norm` are both `force_only` in "
        "the ontology -- neither runs automatically in the standard "
        "pipeline; both require an explicit job-level force to execute at "
        "all. `narrowband_composite` operates in the linear stage (not "
        "nonlinear, unlike the other three) and requires supplied mono Ha/"
        "OIII/optional-SII FITS paths via `extra_params['narrowband']` -- "
        "it is a materially different input-provenance class from the "
        "SeeStar duo-band OSC path, and the Handbook must not collapse "
        "\"narrowband file\" into one category across both.",
        "None of the four families currently expose a clear unchanged/"
        "no-op candidate the way several other Experiment Mode families "
        "do -- `nb_palette`'s four `experiment_variants` are all real "
        "palette transforms, and `narrowband_norm`/`narrowband_hoo`/"
        "`narrowband_composite` use a separate `variants` schema outside "
        "formal Experiment Mode dispatch entirely, with no `none` entry in "
        "any of the three. A future controlled comparison needs an "
        "explicit copied-input baseline that does not currently exist in "
        "the ontology for this family.",
        "Palette evaluation is confounded by star-recombination state: "
        "`nb_palette` operates on the starless branch and replaces the "
        "post-stretch starless working image, with stars re-screened "
        "afterward by the existing recombination path. Judging a starless "
        "palette says little about final star color, recombined broadband/"
        "OSC stars can make a palette look more natural even when the "
        "nebular mapping is unchanged, and comparing one candidate before "
        "recombination with another after is invalid.",
    ),
    required_input_state=(
        "For `nb_palette`/`narrowband_norm`/`narrowband_hoo`: a nonlinear, "
        "already-stretched image with the same background extraction/"
        "calibration state across any compared candidates (residual "
        "gradients/casts can change channel mixing) and, for the XP-"
        "informed paths, successful upstream XP channel extraction. For "
        "`narrowband_composite`: linear-stage supplied mono Ha/OIII/"
        "optional-SII masters with their own acquisition/filter provenance "
        "known.",
    ),
    nova_action=(
        "`nb_palette` builds Ha/OIII component estimates via a Gaia-XP-"
        "informed non-negative-least-squares mixing-matrix fit on the "
        "star-full post-background-extraction image, then applies that "
        "unmixing to the starless linear image at the stretch boundary; it "
        "self-skips (retaining the parent image) when XP channels are "
        "missing/dimension-mismatched, or when the measured Ha/OIII flux "
        "ratio exceeds `NB_PALETTE_MAX_FLUX_RATIO=20.0`. `narrowband_norm` "
        "runs PixInsight NarrowbandNormalization with `o3_boost` auto-"
        "scaled from Ha-dominance ratio; a prior `MaximumStars/Equalize` "
        "approach was a silent no-op under PixInsight 1.9.3 because the "
        "expected property did not exist, so the current supported path "
        "matters as real version-specific provenance. `narrowband_hoo()` "
        "computes `R=Ha, G=0.15*Ha + oiii_g*O, B=O` with `O=min(OIII, "
        "oiii_cap*Ha)` (default `oiii_cap=0.85`, `oiii_g=0.90`), preferring "
        "real XP-unmixed `xp_ha.fit`/`xp_oiii.fit` channels (each passed "
        "through a matching STF stretch so the cap compares like-for-like "
        "scales) and falling back to an RGB-proxy OIII estimate only when "
        "those files are absent. `narrowband_composite` maps supplied mono "
        "masters to SHO (`R=SII, G=Ha, B=OIII`) or Foraxx "
        "(`R=0.76*Ha+0.24*OIII, G=0.85*OIII+0.15*Ha, B=OIII`) when the "
        "required channels are provided via job `extra_params`."
    ),
    nova_evidence_ids=(
        "nova-narrowband-source",
        "nova-ic1805-flux-ratio-evidence",
        "henry-ic1805-black-sky-rejection",
    ),
    use_when=(
        "Real emission-line signal (SeeStar duo-band or true mono Ha/OIII/"
        "SII masters) needs a deliberate channel-and-palette decision "
        "distinct from ordinary broadband color calibration -- and the "
        "signal/channel provenance (measured, XP-model-derived, or RGB "
        "proxy) can be disclosed alongside whichever palette is chosen.",
    ),
    skip_when=(
        "The target is a reflection nebula or other broadband-continuum "
        "subject captured incidentally through a dual-band filter -- "
        "narrowband color mapping is not a substitute for broadband "
        "continuum color and should not be routed through this family "
        "merely because a duo-band filter was used.",
        "The extracted Ha/OIII flux ratio indicates the OIII channel is "
        "noise/pedestal-dominated rather than genuine bicolor signal -- the "
        "flux-ratio gate exists specifically to catch this for `nb_palette`.",
        "The goal is a literal three-channel SHO claim but only Ha and OIII "
        "were actually captured -- that goal cannot be met by this family "
        "without true mono SII data; use synthetic/pseudo-SHO language "
        "instead, or skip the SHO framing entirely.",
    ),
    scientific_and_aesthetic_notes=(
        "A valid palette/channel experiment holds fixed or explicitly "
        "blocks on: the same parent FITS and starless/star-full state, the "
        "same channel provenance (XP-derived vs. RGB-proxy HOO are not a "
        "pure palette comparison), the same extraction model/version, the "
        "same stretch philosophy (linked vs. per-channel is a separate "
        "factor from palette choice), the same background/calibration "
        "state, the same SCNR policy, the same star-recombination stage, "
        "the same crop/scale/registration, the same clipping/headroom "
        "policy, and the same display transform for previews.",
        "Ha/OIII flux ratio from NOVA's reconstructed channels is a model-"
        "derived diagnostic unless independently calibrated; OIII coverage "
        "fraction is a useful implementation diagnostic, not proof of real "
        "OIII emission; sky blackness can catch a field-flood failure but "
        "blacker sky is not universally better and can result from "
        "destructive clipping; saturation/chroma magnitude describes "
        "output colorfulness, not physical accuracy.",
        "Whole-frame SNR-like metrics, entropy, high-frequency power, "
        "generic sharpness, star count alone, lower background alone, "
        "stronger teal/gold separation, a perceptual preference score, "
        "historical win fraction, and \"not rejected\" by an artifact gate "
        "must never be promoted into palette or channel-reconstruction "
        "validation. A palette can improve every aesthetic criterion while "
        "becoming less faithful to original line ratios, and can preserve "
        "line ratios while being less visually preferred -- these are "
        "separate objectives that must not be conflated.",
        "Choice among HOO, synthetic SHO/gold, and Foraxx, and the degree "
        "of OIII emphasis, may legitimately be driven partly by aesthetic "
        "preference once physical/provenance constraints are disclosed -- "
        "that validates preference for the rendering, not a physical claim "
        "about the underlying line ratios.",
    ),
    tool_guidance=(
        ToolGuidance(
            tool_id="nova",
            tool_version="XP-informed nb_palette + PixInsight narrowband_normalize + narrowband_hoo (XP or RGB-proxy) + narrowband_composite (SHO/Foraxx from supplied mono masters)",
            host="NOVA Python pipeline",
            instructions=(
                "Record which of the four strategies actually ran and, for "
                "`narrowband_hoo`, whether the real XP channels or the "
                "RGB-proxy fallback supplied OIII -- the candidate label "
                "alone does not disclose this.",
                "Record whether `nb_palette` executed, self-skipped on "
                "missing/mismatched XP channels, or was gated by the "
                "flux-ratio check -- a retained parent image is not "
                "evidence the palette failed to help, it may mean it never "
                "ran.",
                "Never describe `nbp_sho_gold` or any Ha/OIII-only render "
                "as measured three-channel SHO.",
                "Do not compare `nbp_hoo` against `nbp_hoo_aggressive` as a "
                "pure palette test -- the stretch philosophy changes too.",
            ),
            controls_and_starting_ranges=(
                ("nbp_hoo / nbp_foraxx / nbp_sho_gold", "XP-derived palette strategies, linked stretch"),
                ("nbp_hoo_aggressive", "same HOO mapping, per-channel stretch -- a strategy change, not a strength probe"),
                ("hoo_oiii_soft / hoo_oiii_strong", "oiii_cap 0.6 / 1.0 (default 0.85)"),
                ("nbn_o3_soft / nbn_o3_strong", "o3_boost 1.3 / 1.7 (auto-scaled default clamp 1.25-1.70)"),
                ("sho_palette / foraxx_palette", "narrowband_composite mono-master mappings"),
            ),
            expected_result=(
                "A displayed color image with clearly disclosed channel "
                "provenance and palette formula, correct star/background "
                "separation, and no field-flood or false-signal artifacts."
            ),
            failure_modes=(
                "False OIII / field flood: weak reconstructed OIII spreads "
                "across the field, raising cyan/blue background -- from "
                "noise pedestal, imperfect unmixing, per-channel stretch, "
                "or overly strong OIII normalization.",
                "Suppressed real OIII from broad SCNR, too-low oiii_cap, "
                "linked stretch on an extremely weak channel, or extraction "
                "failure.",
                "Synthetic SHO misread as measured SII by a viewer.",
                "Star palette artifacts (unnatural green/magenta stars, "
                "halos) after synthetic mapping.",
            ),
            recovery=("Inspect the linear reconstructed channel and fit quality rather than fixing solely by darkening the final palette; compare XP-derived and proxy behavior; only increase OIII visibility if signal exists above noise; evaluate starless and recombined outputs separately.",),
            mask_support="Not exposed as a general control; narrowband_hoo's OIII cap is itself a spatially local, Ha-conditioned soft mask.",
            equivalence=EquivalenceClass.EXACT_REPLAY,
            provenance=(ProvenanceLabel.NOVA_SOURCE_CONFIRMED, ProvenanceLabel.NOVA_EXECUTION_RECORD),
            source_ids=("nova-narrowband-source", "nova-ic1805-flux-ratio-evidence"),
        ),
        ToolGuidance(
            tool_id="pixinsight",
            tool_version="NarrowbandNormalization; project runtime pin 1.9.3 Lockhart",
            host="PixInsight NarrowbandNormalization",
            instructions=(
                "A prior `MaximumStars/Equalize`-based approach was a "
                "silent no-op under PixInsight 1.9.3 because the expected "
                "property did not exist -- the current `o3_boost`-based "
                "path is the real supported mechanism; treat exact PI "
                "version as material provenance for this family.",
                "Boosting OIII improves visibility of recorded weak signal; "
                "it cannot recover OIII never captured above noise.",
            ),
            controls_and_starting_ranges=(
                ("o3_boost", "auto-scaled clamp(0.50 + 0.395*ha_dominance_ratio, 1.25, 1.70); manual soft/strong overrides 1.3/1.7"),
            ),
            expected_result="OIII channel visibility balanced against Ha according to the configured/auto-scaled boost.",
            failure_modes=("Presenting a more visually balanced palette as more physically accurate line flux.",),
            recovery=("Reduce o3_boost; compare against the XP-derived channel evidence before trusting a normalized result.",),
            mask_support="Not exposed by NOVA's current wrapper.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("nova-narrowband-source",),
        ),
        ToolGuidance(
            tool_id="saspro",
            tool_version="exact deployed package version for nb_palette's XP-extraction path not independently confirmed in this repo",
            host="Seti Astro Suite Pro (context for nb_palette's XP-informed extraction)",
            instructions=(
                "Treat the exact deployed SASpro version relevant to "
                "`nb_palette`'s channel extraction as unresolved/version-"
                "bound rather than guessed -- it was not determinable from "
                "public project metadata at research time.",
            ),
            controls_and_starting_ranges=(
                ("availability", "XP-informed extraction hook feeding nb_palette; exact version-bound"),
            ),
            expected_result="Gaia-XP-informed Ha/OIII component estimates feeding NOVA's nb_palette and narrowband_hoo XP-channel path.",
            failure_modes=("Treating the XP-derived channels as independently calibrated spectroscopy rather than a model-derived unmixing.",),
            recovery=("Fall back to the RGB-proxy path or narrowband_norm when XP extraction is unavailable or its fit quality is questionable.",),
            mask_support="Not applicable at this layer.",
            equivalence=EquivalenceClass.SAME_ENGINE_ADAPTED_HOST,
            provenance=(ProvenanceLabel.VENDOR_DOCUMENTED, ProvenanceLabel.NOVA_SOURCE_CONFIRMED),
            source_ids=("nova-narrowband-source", "gaia-dr3-xp-documentation"),
        ),
        ToolGuidance(
            tool_id="siril",
            tool_version="native narrowband/palette compositing tools; exact version not independently confirmed in this repo",
            host="Siril native narrowband/palette tools",
            instructions=(
                "Treat as the same conceptual family -- channel-to-palette "
                "mapping for narrowband/dual-band data -- with different "
                "implementation and provenance model than NOVA's XP-"
                "informed and PixInsight-based paths.",
                "Apply the same channel-provenance and palette-formula "
                "disclosure discipline used for NOVA's own candidates.",
            ),
            controls_and_starting_ranges=(
                ("native palette compositing", "typically operates on true mono narrowband masters, closest to NOVA's narrowband_composite input class"),
            ),
            expected_result="A composed narrowband/dual-band color image via Siril's own tooling.",
            failure_modes=("Treating a Siril palette result as evidence about NOVA's XP-derived or PixInsight-based candidates.",),
            recovery=("Document the actual input channel provenance and palette formula used, same as for NOVA's own candidates.",),
            mask_support="Not independently confirmed in this repo.",
            equivalence=EquivalenceClass.FUNCTIONAL_ALTERNATIVE,
            provenance=(ProvenanceLabel.REASONED_TRANSLATION,),
            source_ids=("nova-narrowband-source",),
        ),
    ),
    measurements=(
        "Residual error and conditioning of the fitted XP mixing/unmixing "
        "model on calibration stars, when assessing the channel model "
        "itself rather than the palette.",
        "Independent channel background median and robust RMS/MAD, channel "
        "clipping/negative-floor incidence, and spatial morphology "
        "agreement between expected nebular structures and reconstructed "
        "component maps.",
        "Per-channel clipping/headroom and percentile occupancy, sky "
        "pedestal and sky chroma after mapping, and target-ROI channel "
        "ratios interpreted as a display transformation rather than "
        "recovered physics.",
        "Star color/halo metrics after recombination, and difference maps "
        "showing where a strategy altered the parent signal.",
        "Whether SCNR was actually skipped, which OIII source "
        "(`xp_oiii`/`rgb_proxy`) supplied `narrowband_hoo`, and whether "
        "`nb_palette` executed, self-skipped, or was flux-ratio-gated.",
    ),
    acceptance_criteria=(
        "Channel provenance (measured mono master, XP-model-derived "
        "estimate, or RGB proxy) and the exact palette formula/coefficients "
        "used are both disclosed -- never presented as one undifferentiated "
        "\"narrowband palette applied\" fact.",
        "A synthetic SHO/gold render from only Ha/OIII is never described "
        "as measured three-channel SHO.",
        "A `nbp_hoo` vs. `nbp_hoo_aggressive` result is labeled a strategy "
        "comparison (stretch philosophy changed), never a clean palette-"
        "only comparison.",
        "Whole-frame SNR-like metrics, entropy, sharpness, star count, "
        "background darkness alone, perceptual preference, or historical "
        "win fraction are never presented as standalone proof of correct "
        "channel reconstruction.",
        "A starless-palette result and a final-recombined-image result are "
        "never pooled as if they assessed the same thing.",
    ),
    sources=(
        P33_PACKET,
        NOVA_NARROWBAND_SOURCE,
        IC1805_FLUX_RATIO_EVIDENCE,
        HENRY_IC1805_REJECTION_QUOTE,
        ZWO_SEESTAR_DUOBAND_SPEC,
        GAIA_DR3_XP_DOCUMENTATION,
    ),
)

"""
Workflow & processing-step documentation page.

Served at /workflows-doc. A static, human-readable reference that explains every
autoprocess workflow and every processing step: what it does, how it works, what
makes it good, how it fails, and how to analyze it. Before/after sliders use a
curated example set copied into <library>/_docs/_processed/wf/ (served via the
existing /image/_docs/wf/<name>.jpg route), so the page survives run cleanup.

Content is authored here (STEP_DOCS / WORKFLOW_NOTES); step parameters, variants
and workflow step-sequences are pulled live from processing_ontology.json so the
page stays in sync with the pipeline definition.
"""
import html as _html
import json as _json
from pathlib import Path

EX = "/image/_docs/wf"  # base URL for curated before/after example images

_ONTOLOGY_PATH = Path(__file__).parent / "processing_ontology.json"


def _load_ontology() -> dict:
    try:
        return _json.loads(_ONTOLOGY_PATH.read_text())
    except Exception:
        return {"processing_steps": {}, "workflows": {}, "quality_dimensions": {},
                "stretch_quality_targets": {}}


# ---------------------------------------------------------------------------
# Stage grouping (display order)
# ---------------------------------------------------------------------------

STAGES = [
    ("acq", "Acquisition &amp; stacking",
     "Turning a night of raw sub-frames into one calibrated linear master.",
     ["subframe_culling", "stacking"]),
    ("pre", "Pre-process — linear cleanup",
     "Cheap, lossless fixes applied to the master before any heavy processing.",
     ["crop", "remove_pedestal", "cosmetic_correction"]),
    ("lin", "Linear processing",
     "The science-critical stage. Everything here happens on linear data, where "
     "pixel values are still proportional to photons — the only place gradient "
     "removal, colour calibration and deconvolution are mathematically valid.",
     ["background_extraction", "color_calibration", "deconvolution",
      "denoise_linear", "star_sharpen"]),
    ("str", "Star separation &amp; stretch",
     "The perceptual turning point: stars are split off, the faint signal is "
     "stretched from linear to non-linear, and the stars are stretched separately.",
     ["remove_stars_linear", "stretch", "stretch_stars"]),
    ("nl", "Non-linear — colour &amp; detail",
     "Aesthetic and structural shaping on the stretched starless image.",
     ["scnr", "narrowband_norm", "background_neutralize", "color_boost",
      "clahe", "noise_reduction", "curves", "hdr_compression", "dark_enhance",
      "color_sat"]),
    ("fin", "Recombination &amp; finish",
     "Stars are screened back in and final star-halo cleanup is applied.",
     ["combine_stars_screen", "halo_suppression"]),
    ("nb", "Narrowband",
     "Optional duo/tri-band palette compositing for emission targets.",
     ["narrowband_composite"]),
]


# ---------------------------------------------------------------------------
# Authored per-step documentation
# Fields: icon, what, how, good, wrong, analyze, example (optional image key)
# ---------------------------------------------------------------------------

STEP_DOCS = {
    "subframe_culling": {
        "icon": "⌗",
        "what": "Ranks every individual sub-frame and rejects the worst before they "
                "ever reach the stack. A single trailed, cloud-hazed or wind-shaken "
                "frame can drag down an otherwise good integration.",
        "how": "SEP (Source-Extractor-Python) measures each frame: median star FWHM "
               "(focus/seeing), eccentricity (tracking/trailing), and star count "
               "(transparency). Frames are scored and the bottom <code>bottom_pct</code> "
               "fraction is dropped; any frame below <code>min_stars</code> is always "
               "rejected.",
        "good": "Tightens the FWHM and roundness of the stack without throwing away so "
                "many frames that read-noise creeps back in. 5–10% is the sweet spot "
                "for SeeStar alt-az data; EQ data can usually keep more.",
        "wrong": "Too aggressive (20%+) and you lose integration time — SNR scales with "
                 "√N, so dropping a quarter of your frames costs ~13% of your signal. "
                 "Too lax and one bad frame's bloated stars survive into the master.",
        "analyze": "Compare star FWHM / eccentricity of the master against the per-frame "
                   "median. If the stack is no tighter than a typical sub, culling did "
                   "nothing useful. Watch the frame-count: SNR gain from culling must "
                   "beat the √N loss from fewer frames.",
    },
    "stacking": {
        "icon": "⊞",
        "what": "Calibrates, registers (aligns) and integrates all surviving sub-frames "
                "into one master FITS. This is where read-noise is beaten down and the "
                "faint signal first becomes visible.",
        "how": "Three interchangeable engines. <b>Siril</b> — fast sigma-clipping "
               "integration, the clean well-tested default. <b>Image MM</b> — multi-frame "
               "deconvolution that recovers extra resolution during integration. "
               "<b>PixInsight WBPP / register</b> — noise-weighted integration and "
               "drizzle for the best SNR and sub-pixel detail, at the cost of speed. All "
               "engines plate-solve via the local Gaia DR3 catalog.",
        "good": "A clean master has a flat background, round stars across the whole "
                "frame, no walking-noise streaks, and a meaningful SNR gain over a single "
                "sub (≈√N for uncorrelated noise).",
        "wrong": "Registration failure → doubled or smeared stars. Too few rejection "
                 "frames → satellite trails and plane-lights survive. Drizzle on too few "
                 "frames → amplified noise instead of resolution. Over-large integration "
                 "canvas → blown VM disk (the original 2.9 TB qcow2 incident).",
        "analyze": "Check <code>frames_registered</code> ≈ N. Measure background "
                   "flatness and star roundness corner-to-corner. The stack-history page "
                   "compares engines side by side; efficiency = SNR_stack / "
                   "(median_single_SNR × √N) tells you whether integration was optimal.",
    },
    "crop": {
        "icon": "▢",
        "example": "crop",
        "what": "Trims the ragged stacking borders — the low-coverage edges where "
                "dithering and field rotation mean only some frames contributed, so the "
                "signal there is noisier and the gradient is worst.",
        "how": "Multi-candidate. It generates up to four crops — <b>canonical</b> "
               "(reproject onto the target's fixed per-target WCS so every session shares "
               "one frame), <b>coverage</b> (largest rectangle with ≥80% frame coverage), "
               "<b>intersection</b> (largest rectangle every frame covers), and <b>LIR</b> "
               "(largest inscribed rectangle in the coverage mask). Selection is "
               "canonical-preferred with a Claude vision override: if the canonical box "
               "would clip the subject, it falls back to the largest-area alternative.",
        "good": "Removes the noisy stacking apron without eating into the subject, and "
                "(via canonical framing) makes the same target reproducible night to "
                "night so sessions can be stacked cumulatively.",
        "wrong": "Too aggressive and you clip the outer halo / faint tidal tails. Too "
                 "loose and a low-coverage corner survives, where the gradient-removal "
                 "and stretch will later exaggerate the noise into an ugly bright edge.",
        "analyze": "The Haiku clip-veto is the guard: it answers only \"does this crop "
                   "cut the subject?\" Inspect each <code>auto_crop_&lt;cand&gt;.fit</code> "
                   "candidate; the chosen one should retain the full subject with minimal "
                   "low-coverage border left.",
    },
    "remove_pedestal": {
        "icon": "▿",
        "example": "pedestal",
        "what": "Removes the ADC bias pedestal — the constant electronic offset the "
                "camera adds so the sensor never reads true zero. It sets the real black "
                "point to exactly zero.",
        "how": "Subtracts the global per-channel minimum across the whole image. Channel "
               "ratios are preserved, so colour balance is untouched. Pure numpy, sub-"
               "second, always applied (force step).",
        "good": "Gives the stretch and gradient model a true zero to work from. Without "
                "it the black point floats, and every downstream brightness target (sky "
                "background bands) is offset.",
        "wrong": "Almost impossible to get wrong — it is a rigid subtraction. The only "
                 "risk is a single cold pixel pinning the minimum, which cosmetic "
                 "correction handles immediately after.",
        "analyze": "Subtle by eye (see slider — the change is a few ADU). Confirm the "
                   "post-step minimum is ≈0 and that the colour ratios (channel medians) "
                   "are unchanged.",
    },
    "cosmetic_correction": {
        "icon": "✦",
        "example": "cosmetic",
        "what": "Removes residual hot and cold pixels — single bright/dark pixels left by "
                "sensor defects, cosmic-ray hits, or stacking misses that survived "
                "rejection.",
        "how": "Per-channel local median filter: any pixel deviating more than "
               "<code>sigma</code>×MAD from its local neighbourhood is replaced with the "
               "local median. sigma=5 is conservative — only true outliers, never real "
               "signal. Fast scipy op (~0.5 s).",
        "good": "Clean point removal that leaves stars and nebulosity completely "
                "untouched. Critical before deconvolution, which would otherwise ring "
                "around every hot pixel.",
        "wrong": "Too low a sigma (≤3) starts eating the cores of faint stars and the "
                 "peaks of real detail. Too high and obvious hot pixels survive into the "
                 "stretch as coloured speckles.",
        "analyze": "Blink before/after at 100%: hot pixels should vanish, stars should "
                   "be pixel-for-pixel identical. If faint stars dimmed, sigma is too "
                   "aggressive.",
    },
    "background_extraction": {
        "icon": "▦",
        "example": "bge",
        "what": "Removes large-scale gradients — light pollution, moon glow, vignetting, "
                "and amp glow — that tilt or dome the background. The single most "
                "important step for a clean, neutral sky.",
        "how": "Models the background from sample points placed on star-free areas, then "
               "subtracts (additive gradients) or divides (multiplicative vignetting) it "
               "out. Engines: <b>GraXpert</b> (AI, robust on busy fields), <b>SASpro "
               "ADBE</b> (headless polynomial + RBF), and <b>PixInsight "
               "GradientCorrection</b>. Done on linear data — the only stage where the "
               "gradient is a simple additive/multiplicative term.",
        "good": "A flat, neutral, near-zero background with the nebulosity and galaxy "
                "halo fully preserved. After this, the sky should be the same brightness "
                "in every corner.",
        "wrong": "Over-fit (degree too high, samples on real signal) and it carves a dark "
                 "halo around the subject or eats faint outer structure — the classic "
                 "\"BGE ate my galaxy\" mistake. Under-fit and the gradient survives into "
                 "the stretch as a coloured wash. On big globulars the cluster halo can "
                 "fool the gradient metric (GraXpert is preferred there).",
        "analyze": "Measure corner-to-corner background uniformity. The gradient quality "
                   "dimension should rise. Beware a model that improves the metric while "
                   "subtracting real signal — check the subject halo is intact.",
    },
    "color_calibration": {
        "icon": "◑",
        "example": "spcc",
        "what": "Photometric colour calibration (SPCC). Sets a physically correct white "
                "balance so star colours — and therefore nebula/galaxy colours — match "
                "their real spectral types instead of the camera's raw response.",
        "how": "PixInsight SPCC cross-matches stars in the frame against the Gaia DR3 "
               "catalog (their measured colours are known) and solves the per-channel "
               "scaling that makes the image agree with the catalog, using the Sony "
               "SeeStar S50 sensor + filter profiles. Always applied (force step).",
        "good": "Sun-like (G-type) stars render neutral white, hot stars blue, cool "
                "stars amber — and Ha regions land at the correct red. Because it is "
                "photometric, it is repeatable and objective, not a taste call.",
        "wrong": "If plate-solving fails or too few catalog stars match, SPCC fails and "
                 "drops a <code>.spcc_failed</code> sentinel — downstream colour steps "
                 "must then switch to <i>unlinked</i> behaviour. A wrong LP-filter flag "
                 "skews the solve. Duo-band/OIII targets can look mis-calibrated if the "
                 "folio colour prior is wrong (the Thor's-Helmet trap).",
        "analyze": "Check the sentinel and the SPCC star-match count in the log. "
                   "Visually, a dense star field should show a natural spread of star "
                   "colours, not a uniform tint. Preserve linked colour downstream unless "
                   "SPCC failed.",
    },
    "deconvolution": {
        "icon": "◎",
        "example": "decon",
        "what": "Recovers spatial detail that seeing and optics blurred away — tightens "
                "stars and sharpens fine structure. Done on linear data, where the blur "
                "is a true convolution with the PSF.",
        "how": "<b>BlurXTerminator (BXT)</b> is the reference: an AI model trained to "
               "invert the PSF, with separate stellar (roundness/size) and non-stellar "
               "(detail) amounts and an auto-measured PSF. Alternatives: Cosmic Clarity "
               "AI sharpen and classical Richardson-Lucy. A globular variant masks the "
               "saturated core so only outer halo stars are deconvolved.",
        "good": "BXT is best-in-class on SeeStar data: rounder, smaller stars and crisper "
                "arms/filaments with no added noise. It is the one sharpening step that "
                "genuinely adds resolution rather than just local contrast.",
        "wrong": "Over-driven non-stellar amount creates dark rings around stars and a "
                 "\"painted\" texture. cc_sharpen can look sharper on a linear preview but "
                 "leaves ringing halos that become catastrophic after stretch + star "
                 "removal. Applying it after stretch (non-linear) is mathematically wrong "
                 "and produces artefacts.",
        "analyze": "Stars should shrink and round-up; FWHM drops. Inspect bright stars "
                   "for dark haloes (over-deconvolution). Always compare against BXT — if "
                   "an alternative isn't clearly better, BXT wins.",
    },
    "denoise_linear": {
        "icon": "░",
        "what": "Reduces noise while the data is still linear — the most effective place "
                "to denoise, because the noise statistics are well-behaved and the "
                "stretch hasn't yet amplified them.",
        "how": "<b>NoiseXTerminator (NXT)</b> is the reference: an AI denoiser with a "
               "detail-preservation control so it separates grain from real structure. "
               "Cosmic Clarity AI denoise is the headless fallback. Luma and colour noise "
               "are tuned separately (colour noise tolerates heavier reduction).",
        "good": "A smooth background and clean faint signal with stars and fine detail "
                "fully intact. Linear denoising means the later stretch lifts clean "
                "signal instead of amplifying grain.",
        "wrong": "Too strong and it plasticises the image — nebulosity goes waxy, faint "
                 "stars dissolve, and low-contrast detail is smeared. On dense star "
                 "fields heavy denoise inflates star FWHM, which is why star_sharpen runs "
                 "right after to re-tighten them.",
        "analyze": "Background σ (grain) should drop without the detail dimension "
                   "falling. Check faint stars survived and nebulosity didn't go "
                   "plastic. An SNR-gain objective gate decides whether it earned its "
                   "place.",
    },
    "star_sharpen": {
        "icon": "✶",
        "what": "BXT in correct-only mode: fixes star <i>shape</i> (roundness, "
                "elongation) without deblurring the background. Runs after denoise on "
                "dense fields where denoise has bloated the stars.",
        "how": "BXT AI model with auto PSF, but stellar-sharpen and non-stellar amounts "
               "both set to zero — pure geometric star correction, no detail sharpening. "
               "Force-variant <code>bxt_correct_only</code> in every workflow that uses "
               "it.",
        "good": "Round, tight, consistent stars across the frame with zero impact on "
                "nebulosity or noise — undoes the FWHM inflation that denoise causes.",
        "wrong": "Little can go wrong since it only corrects geometry; the failure mode "
                 "is running it when PI is unavailable (falls back to Cosmic Clarity "
                 "stellar, which is weaker).",
        "analyze": "Star eccentricity should drop; FWHM tightens slightly. Background and "
                   "noise metrics should be unchanged.",
    },
    "remove_stars_linear": {
        "icon": "✫",
        "what": "Splits the image into a starless layer and a stars-only layer before "
                "stretching. The starless layer goes through the rest of the pipeline; "
                "the stars are stretched separately and screened back in at the end.",
        "how": "AI star removal (SASpro DarkStar or PI StarXTerminator) in unscreen mode "
               "produces two FITS that recombine exactly. Splitting <i>before</i> stretch "
               "means the nebula can be pushed hard without bloating the stars, and star "
               "size/colour can be controlled independently.",
        "good": "Clean separation lets the stretch, curves and contrast steps act on "
                "nebulosity alone — no star bloat, no merged star cores in the final.",
        "wrong": "Imperfect removal leaves dark pits where bright stars were, or halo "
                 "residue that the recombine then doubles. Over-removal clips small stars "
                 "that never come back.",
        "analyze": "Inspect the starless layer for dark holes/residual halos around "
                   "bright stars. The recombined final should show no doubled or "
                   "ringed stars.",
    },
    "stretch": {
        "icon": "◈",
        "example": "stretch",
        "gallery": True,
        "what": "The critical perceptual step: converts linear data (where the faint "
                "signal is a fraction of a percent above black) into a non-linear image "
                "the eye can read. Everything before this is invisible; this is where the "
                "picture appears.",
        "how": "Several stretch functions compete and a physics picker chooses. "
               "<b>stat</b> targets a sky-background median; <b>GHS</b> (Generalised "
               "Hyperbolic) gives precise control of where contrast lands; <b>STF</b> "
               "mimics PixInsight's auto-stretch with a background target; <b>Veralux</b> "
               "is an arcsinh stretch with colour grip that preserves SPCC balance; "
               "<b>MAS</b> is PI's self-calibrating multiscale stretch. Linked stretches "
               "(one curve from luminance) protect the calibrated colour.",
        "good": "Sky background lands in the per-object band (galaxy 0.05–0.08, nebula "
                "0.06–0.10), the subject shows structure without clipped cores, and the "
                "result is <b>clean</b> — not grainy. Henry prefers galaxies darker / "
                "higher-contrast (p99 ≈ 0.56) than a naive bright stretch.",
        "wrong": "Over-stretch → washed-out, muddy sky above band and blown cores with no "
                 "headroom for curves. Under-stretch → flat, dim, structure buried. The "
                 "subtle one: a stretch that places the histogram well but amplifies "
                 "background grain (veralux_strong on thin data) — fixed by the "
                 "noise-aware picker below.",
        "analyze": "Measure the corner-median sky background against the band <i>and</i> "
                   "the corner-σ grain (the process-critique <code>fits_stats.py</code> "
                   "now reports both). A high grain when a cleaner candidate existed is a "
                   "picker failure. Check p99.9/p99.99 for a clipped core before curves.",
        "extra": "noise_picker",
    },
    "stretch_stars": {
        "icon": "★",
        "what": "Stretches the stars-only layer on its own, so star brightness, size and "
                "colour are controlled independently of the nebula.",
        "how": "SASpro Star Stretch. The stretch factor is auto-selected from the star "
               "layer's p90 brightness (target: p90 star → ~0.25 post-stretch, so stars "
               "stay secondary to the nebula). SCNR strength is inherited from the "
               "starless SCNR winner so the two layers match.",
        "good": "Stars that are present and colourful but restrained — punctuation, not "
                "the headline. Tight, saturated, not bloated.",
        "wrong": "Over-stretched stars dominate the frame and bloat on recombine; "
                 "under-stretched and they vanish, leaving a starless-looking image.",
        "analyze": "Check the stars-only preview: p90 should land near 0.25. After "
                   "recombine, stars should read as small coloured points, not white "
                   "blobs.",
    },
    "scnr": {
        "icon": "◐",
        "example": "scnr",
        "what": "Selective Colour Noise Reduction — removes the green/teal cast that OSC "
                "(one-shot-colour) sensors like the SeeStar produce, where the green "
                "channel is over-represented.",
        "how": "Caps the green channel at a neutral blend of red and blue (average or "
               "maximum neutral protection), optionally preserving lightness. Applied "
               "after stretch. <b>Skipped</b> on the narrowband path so OIII (which lands "
               "across green+blue on a duo-band OSC) isn't stripped before NBN balances "
               "it.",
        "good": "A neutral sky and natural colours with no magenta over-correction. "
                "Almost every broadband SeeStar image needs some green removal.",
        "wrong": "Too strong → magenta/purple cast and desaturated genuinely-green "
                 "features. Applied to narrowband → kills real OIII signal.",
        "analyze": "Background should go neutral grey, not magenta. Confirm it was "
                   "skipped for narrowband targets.",
    },
    "narrowband_norm": {
        "icon": "▤",
        "aesthetic": True,
        "what": "PixInsight NarrowbandNormalization — balances the Ha and OIII channels "
                "of duo-band data so neither dominates unnaturally.",
        "how": "Uses the HOO palette with an <b>OIII boost</b> (o3Boost) auto-scaled from "
               "Ha dominance — a more Ha-dominant target gets more boost to surface its weak "
               "teal core. A dedicated warm-gold/teal saturation preset follows. Runs where "
               "SCNR would, before background neutralize. An aesthetic, user/config-driven "
               "step — never objectively score-gated.",
        "good": "Both emission bands are legible — warm Ha dust and a vibrant teal OIII core "
                "both visible rather than one drowning the other or going muddy grey-tan.",
        "wrong": "Too little OIII boost leaves the result muddy yellow-brown; judging it by "
                 "an objective score (rather than as an aesthetic choice) can wrongly "
                 "suppress it.",
        "analyze": "This is a taste step — evaluate against the intended palette and the "
                   "folio colour prior, not a metric.",
    },
    "background_neutralize": {
        "icon": "▢",
        "example": "bgn",
        "what": "Post-stretch background neutralisation — removes any residual colour "
                "cast left in the darkest background regions after stretching.",
        "how": "Picks a background pivot (darkest region, median, or a conservative "
               "point) and shifts the channels so that region becomes neutral. Cheaper "
               "and gentler than re-running gradient removal.",
        "good": "A truly neutral grey/black sky that makes the subject's colours pop by "
                "contrast.",
        "wrong": "Aggressive pivots can tint the whole image if the chosen region "
                 "actually contained faint signal.",
        "analyze": "Corner background channels should converge (R≈G≈B). The gradient and "
                   "colour-balance dimensions should improve or hold.",
    },
    "color_boost": {
        "icon": "◓",
        "example": "boost",
        "what": "Hue-selective saturation boost — lifts the specific colours that matter "
                "for the target type while leaving the rest alone.",
        "how": "HSV Gaussian kernels target hue bands. <b>Galaxy</b> preset boosts the "
               "blue spiral arms (210–270°) and dampens muddy yellows; <b>nebula</b> "
               "preset boosts Ha-red (0/350°), OIII-cyan (190°) and the magenta blend "
               "zone. Applied after background neutralize. Force step (always on, "
               "conservative + masked).",
        "good": "Spiral arms go blue, HII regions go red, without over-saturating stars "
                "or the background. Selective masking keeps it from looking garish.",
        "wrong": "Pushed too hard it produces neon arms and coloured background noise; "
                 "the wrong preset boosts the wrong hues.",
        "analyze": "Target hues should strengthen while stars and sky stay natural. If "
                   "the background gained colour, the boost leaked past its mask.",
    },
    "clahe": {
        "icon": "▩",
        "what": "Contrast Limited Adaptive Histogram Equalisation — local contrast "
                "enhancement that brings out fine structure the global stretch left flat.",
        "how": "Divides the image into tiles and equalises each one's histogram, with a "
               "clip limit to stop noise being amplified, then blends tile boundaries. "
               "<code>clip_limit</code> controls strength, <code>tile_size</code> the "
               "scale of structure affected.",
        "good": "Dust lanes, filaments and arm structure gain local punch without "
                "touching global brightness.",
        "wrong": "High clip limits amplify background noise and create blocky tile "
                 "artefacts or halos around bright features.",
        "analyze": "Detail dimension should rise. Inspect flat background areas for "
                   "amplified grain or tile seams — the tell-tale of too much clip.",
    },
    "noise_reduction": {
        "icon": "▒",
        "example": "nr",
        "what": "A second, post-stretch noise pass for residual grain the stretch "
                "amplified that linear denoise couldn't pre-empt.",
        "how": "NXT or Cosmic Clarity again, but gentler — the stretch has changed the "
               "noise statistics, so this is touch-up, not the main event.",
        "good": "Cleans up the last of the background grain without flattening the "
                "detail the previous steps worked to reveal.",
        "wrong": "Easy to over-apply here because grain is most visible post-stretch — "
                 "over-doing it plasticises everything the pipeline just sharpened.",
        "analyze": "Background σ should drop a little; the detail dimension must not "
                   "fall. If detail dropped, this pass undid the deconvolution.",
    },
    "curves": {
        "icon": "∿",
        "what": "Parametric tone curve for the final brightness/contrast/colour shaping, "
                "applied to the starless stretched image before recombination.",
        "how": "Named shapes (s_mild, s_med, rolloff_highlights, etc.) at conservative "
               "amounts, or PI CurvesTransformation with calibrated shapes — e.g. "
               "<code>pi_globular_core_rolloff</code> darkens the sky and rolls off a "
               "bright globular core. Small adjustments only; the image is already well "
               "stretched.",
        "good": "A gentle S-curve adds contrast and depth; a rolloff protects bright "
                "cores. The best curve is almost invisible — it shapes, it doesn't "
                "rescue.",
        "wrong": "<code>lift_shadows</code> brightens the already-dark sky, kills "
                 "contrast and almost always scores worse — avoid it. A generic "
                 "data-driven curve overriding a calibrated named shape can flatten it to "
                 "near-identity (a real bug, now gated to pi_curves only).",
        "analyze": "Compare PRE/POST SNR — equal SNR means the curve did nothing (the "
                   "\"curves did nothing\" bug). A real curve moves the histogram shape "
                   "without crushing the sky out of band.",
    },
    "hdr_compression": {
        "icon": "◭",
        "what": "Wavelet HDR compression — tames blown highlights (galaxy cores, bright "
                "nebula knots) while lifting faint structure, expanding the usable "
                "dynamic range.",
        "how": "Decomposes the image into wavelet scales and compresses the high end "
                "under a luminance mask so only the brightest regions are pulled down. "
                "<code>compression_factor</code> &gt;1 compresses highlights; "
                "<code>n_scales</code> sets how broad a structure is affected.",
        "good": "A galaxy core that was a white blob resolves into structure; bright "
                "nebula centres show detail instead of clipping. Optional — only added "
                "when the adaptive plan finds a blown core, to avoid over-cooking "
                "low-dynamic-range disks.",
        "wrong": "Over-applied it greys-out the whole image and produces dark halos "
                 "around bright regions (the inverse of the desired effect).",
        "analyze": "Highlight percentiles (p99.9) should pull back from clipping while "
                   "midtone structure rises. Check bright cores for halos.",
    },
    "dark_enhance": {
        "icon": "◮",
        "what": "Wavelet dark-structure enhancement — boosts faint shadow detail: "
                "integrated flux nebulae (IFN), dust lanes, outer galaxy arms.",
        "how": "Wavelet decomposition with a darkness-boost multiplier under a mask, "
               "applied after HDR compression. Start gentle (boost 2.0) — above ~5.0 it "
               "amplifies noise faster than signal.",
        "good": "Faint outer structure that was lost in the sky becomes visible without "
                "the background going grey.",
        "wrong": "Too much boost lifts the background and turns sky noise into fake faint "
                 "nebulosity — the classic over-processed look.",
        "analyze": "Faint structure should appear while the sky background stays in band. "
                   "If the background brightened, the boost was too high.",
    },
    "color_sat": {
        "icon": "◔",
        "aesthetic": True,
        "what": "A uniform post-curves saturation lift to recover the muted star colours "
                "that statistical stretches wash out.",
        "how": "PI ColorSaturation HS delta — a uniform hue boost. 0.25 is a moderate "
               "lift; above ~0.4 risks garish stars.",
        "good": "Restores natural star and nebula colour after a desaturating stretch.",
        "wrong": "On globulars it has been shown to harm the result (introduces a green "
                 "cast, 8.2→6.2) — which is why it is removed from that workflow.",
        "analyze": "Colour-balance dimension should rise without a cast appearing. "
                   "Globulars: prefer none.",
    },
    "combine_stars_screen": {
        "icon": "✺",
        "example": "recombine",
        "what": "Screen-blends the separately-stretched stars layer back onto the "
                "processed starless image — the inverse of the star split, restoring the "
                "stars to a fully-processed nebula/galaxy.",
        "how": "Screen blend (the mathematically correct inverse of unscreen star "
               "removal). Must follow remove_stars_linear → stretch → stretch_stars. This "
               "is a <b>mandatory</b> step — the pipeline must never ship the starless "
               "intermediate as the final.",
        "good": "Stars sit naturally on the nebula with correct brightness and no halos "
                "or dark pits — indistinguishable from a never-split image, but with the "
                "nebula processed far harder than stars-in would have allowed.",
        "wrong": "If star removal left residue, the screen doubles it. If the run is "
                 "truncated before this step (the old early-stop bug), the final ships "
                 "starless — now prevented by the non-degradation finalise logic.",
        "analyze": "The final must contain stars (compare starless vs combined in the "
                   "slider). A starless final is a bug. Check bright stars for doubling "
                   "or ringing from imperfect removal.",
    },
    "halo_suppression": {
        "icon": "◌",
        "example": "halo",
        "what": "Reduces the colour halos and bloat around bright stars — the violet/blue "
                "rings refraction and the optics leave.",
        "how": "SASpro HaloBGon at a reduction level. Level 1 is minimal and safe for any "
               "field; level 2 is moderate. Dense star fields (Cygnus, Sagittarius) must "
               "stay at level 1 — higher levels crush thousands of stars at once and "
               "produce an artificial blue cast.",
        "good": "Bright stars lose their coloured halos and look tighter, with no impact "
                "on the surrounding nebula.",
        "wrong": "Over-driven it crushes star colour, shrinks real stars and casts the "
                 "field blue. A buggy global-gamma implementation once scored well while "
                 "crushing the sky 0.27→0.09 — a metric-fooling failure.",
        "analyze": "Bright-star halos should shrink while smaller stars are untouched and "
                   "the sky background stays in band. If the whole frame darkened, it "
                   "over-reached.",
    },
    "narrowband_composite": {
        "icon": "▥",
        "what": "Combines separate mono Ha / OIII / SII channels into a false-colour RGB "
                "using a narrowband palette.",
        "how": "<b>SHO</b> maps R=SII, G=Ha, B=OIII (the Hubble palette); <b>Foraxx</b> "
               "blends Ha and OIII dynamically (R=Ha·0.76+OIII·0.24, G=OIII·0.85+Ha·0.15, "
               "B=OIII) for smoother gold/teal transitions. Runs first, then the normal "
               "chain processes the composite.",
        "good": "Gives emission targets the dramatic gold-and-teal SHO/Foraxx look with "
                "clean channel separation.",
        "wrong": "Mis-registered channels produce coloured fringing; the palette is an "
                 "aesthetic choice, not a correctness one — judge by intent.",
        "analyze": "Check channel registration (no coloured edges on stars) and that the "
                   "palette matches the intended look.",
    },
}

# ---------------------------------------------------------------------------
# Manual PixInsight recipe per step — the process/script to use and how, if you
# were reproducing this step by hand in PixInsight. Rendered as its own block.
# ---------------------------------------------------------------------------

PI_MANUAL = {
    "subframe_culling": (
        "<b>Process: SubframeSelector</b>"
        "<ol>"
        "<li>Add all your light frames and run <i>Measure</i>.</li>"
        "<li>In the plots, sort by <code>FWHM</code> and <code>Eccentricity</code>; "
        "set an <i>approval expression</i> such as "
        "<code>FWHM &lt; 2.8 &amp;&amp; Eccentricity &lt; 0.55 &amp;&amp; SNRWeight &gt; 0.6</code> "
        "(use the medians as a guide — reject roughly the worst 5–10%).</li>"
        "<li>Set <i>Output → Action: Output approved only</i> and run; the approved subs "
        "go forward to registration.</li>"
        "</ol>"
        "It also writes per-frame weights you can feed to ImageIntegration."
    ),
    "stacking": (
        "<b>Script: WeightedBatchPreprocessing (WBPP)</b> does the whole chain in one go.<br>"
        "Manual equivalent, in order:"
        "<ol>"
        "<li><b>ImageCalibration</b> — apply master dark / flat / bias.</li>"
        "<li><b>CosmeticCorrection</b> — hot/cold pixel map.</li>"
        "<li><b>Debayer</b> — RGGB for the SeeStar OSC sensor.</li>"
        "<li><b>StarAlignment</b> — register all frames to a reference (enable "
        "<i>Generate drizzle data</i> if you'll drizzle).</li>"
        "<li><b>ImageIntegration</b> — Linear Fit clipping or Winsorized Sigma, "
        "noise-evaluation weighting, additive+scale normalization.</li>"
        "<li>(optional) <b>DrizzleIntegration</b> at 2× for extra resolution.</li>"
        "</ol>"
    ),
    "crop": (
        "<b>Process: DynamicCrop</b> (or the <b>Crop</b> instance)."
        "<ol>"
        "<li>Open DynamicCrop on the integration and drag the rectangle inside the "
        "ragged low-coverage border.</li>"
        "<li>To find the true common-coverage area, integrate with "
        "<i>Generate integration weight map</i> and crop to where the weight is full.</li>"
        "<li>Keep north-up; DynamicCrop updates the WCS/<code>CRPIX</code> for you.</li>"
        "</ol>"
    ),
    "remove_pedestal": (
        "<b>Process: PixelMath</b> (or set it at calibration time)."
        "<ol>"
        "<li>The clean way is <b>ImageCalibration → Output pedestal</b> when you build the "
        "master, so it's already removed.</li>"
        "<li>On an existing master, use PixelMath per channel: "
        "<code>$T - min($T)</code> (rescale off), which pins true black to zero while "
        "keeping channel ratios intact.</li>"
        "</ol>"
    ),
    "cosmetic_correction": (
        "<b>Process: CosmeticCorrection</b>."
        "<ol>"
        "<li>Run on the registered frames (ideally pre-integration).</li>"
        "<li>Without a master dark, enable <i>Use Auto detect</i> and set Hot/Cold "
        "<i>Sigma</i> ≈ 3–5 — only true outliers, never star cores.</li>"
        "<li>Use Real-Time Preview to confirm hot pixels vanish while stars are untouched.</li>"
        "</ol>"
    ),
    "background_extraction": (
        "<b>Process: GradientCorrection</b> (modern) or <b>DynamicBackgroundExtraction "
        "(DBE)</b>."
        "<ol>"
        "<li>DBE: place samples only on star-free background; raise <i>Tolerance</i> so "
        "samples on faint signal are rejected.</li>"
        "<li>Set <i>Correction: Subtraction</i> for additive light pollution, "
        "<i>Division</i> for vignetting.</li>"
        "<li>Inspect the generated background model — it must be smooth, with no imprint "
        "of the galaxy/nebula (that means it's eating real signal).</li>"
        "</ol>"
        "Do this on <i>linear</i> data only."
    ),
    "color_calibration": (
        "<b>Process: SpectrophotometricColorCalibration (SPCC)</b>."
        "<ol>"
        "<li>Plate-solve first (<b>ImageSolver</b> / Image &gt; Astrometry) so SPCC knows "
        "the field — it needs the local <b>Gaia DR3</b> database configured.</li>"
        "<li>Pick the <i>Sony Color Sensor</i> + filter that matches the SeeStar S50; "
        "white reference <i>Average Spiral Galaxy</i> is a safe default.</li>"
        "<li>Run on <i>linear</i> data; check the residual scatter plot for a tight fit.</li>"
        "</ol>"
        "If the solve fails, SPCC can't run — fall back to BackgroundNeutralization + "
        "manual ColorCalibration."
    ),
    "deconvolution": (
        "<b>Process: BlurXTerminator</b> (the modern choice)."
        "<ol>"
        "<li>Run on <i>linear</i> data, after BGE/SPCC.</li>"
        "<li>Set <i>Automatic PSF</i>; start <i>Sharpen Stars</i> ≈ 0.25 and "
        "<i>Sharpen Nonstellar</i> ≈ 0.7–0.9.</li>"
        "<li>Classical alternative: <b>Deconvolution</b> with an external PSF from "
        "<b>DynamicPSF</b> and a star mask + local deringing.</li>"
        "</ol>"
    ),
    "denoise_linear": (
        "<b>Process: NoiseXTerminator</b>."
        "<ol>"
        "<li>Run on <i>linear</i> data (it's linear-aware).</li>"
        "<li><i>Denoise</i> ≈ 0.8, <i>Detail</i> ≈ 0.15 — back off Detail if structure "
        "smears.</li>"
        "<li>Classical alternative: <b>MultiscaleLinearTransform</b> with noise reduction "
        "on the first 3–4 wavelet layers, or <b>TGVDenoise</b>.</li>"
        "</ol>"
    ),
    "star_sharpen": (
        "<b>Process: BlurXTerminator (Correct Only)</b>."
        "<ol>"
        "<li>Enable <i>Correct Only</i> — this fixes star <i>shape</i> with no deblurring.</li>"
        "<li>Set both <i>Sharpen</i> amounts to 0; let it round and de-elongate stars only.</li>"
        "<li>Run after denoise if denoise has bloated the stars on a dense field.</li>"
        "</ol>"
    ),
    "remove_stars_linear": (
        "<b>Process: StarXTerminator</b>."
        "<ol>"
        "<li>Enable <i>Generate star image</i> so you get both a starless layer and a "
        "stars-only layer that recombine by screen.</li>"
        "<li>Run it <i>before</i> stretching so the nebula can be pushed without bloating "
        "stars.</li>"
        "<li>Check the starless layer for dark pits where bright stars were.</li>"
        "</ol>"
    ),
    "stretch": (
        "<b>Process: GeneralizedHyperbolicStretch (GHS)</b>, "
        "<b>HistogramTransformation</b>, or <b>MaskedStretch / ArcsinhStretch</b>."
        "<ol>"
        "<li>Read the auto-STF first (ScreenTransferFunction → wrench) to see a good "
        "starting black/midtone point.</li>"
        "<li>GHS: set <i>SP</i> (symmetry point) near the sky peak in the log histogram, "
        "raise <i>D</i> for strength, <i>b</i> for local contrast — preview live.</li>"
        "<li>Or drag STF into <b>HistogramTransformation</b> and apply to bake it in. "
        "Keep RGB <i>linked</i> to protect the SPCC colour.</li>"
        "<li>Aim the sky background into its per-type band and leave highlight headroom "
        "(don't clip the core).</li>"
        "</ol>"
    ),
    "stretch_stars": (
        "<b>Process: ArcsinhStretch</b> or <b>HistogramTransformation</b> on the stars-only "
        "layer."
        "<ol>"
        "<li>Stretch the star image on its own, gentler than the nebula — keep stars "
        "secondary (p90 ≈ 0.25).</li>"
        "<li>ArcsinhStretch preserves star colour well; raise <i>Stretch factor</i> "
        "gradually with Real-Time Preview.</li>"
        "</ol>"
    ),
    "scnr": (
        "<b>Process: SCNR</b>."
        "<ol>"
        "<li>Colour to remove: <i>Green</i>; protection method <i>Average Neutral</i>, "
        "amount 1.0.</li>"
        "<li>Enable <i>Preserve lightness</i>.</li>"
        "<li><b>Skip on narrowband/duo-band</b> — it would strip real OIII that lands in "
        "green/teal.</li>"
        "</ol>"
    ),
    "narrowband_norm": (
        "<b>Script: NarrowbandNormalization</b> (Script → Utilities)."
        "<ol>"
        "<li>Feed the Ha and OIII channels; choose <i>Equalize</i> to balance them or a "
        "max-stars method to keep a dominant Ha.</li>"
        "<li>It's an aesthetic balance — judge against your intended palette, not a metric.</li>"
        "</ol>"
    ),
    "background_neutralize": (
        "<b>Process: BackgroundNeutralization</b>."
        "<ol>"
        "<li>Draw a preview over a genuinely empty sky region and set it as the "
        "<i>Reference image</i>.</li>"
        "<li>Run to force R≈G≈B in the darkest background after stretch.</li>"
        "</ol>"
    ),
    "color_boost": (
        "<b>Process: ColorSaturation</b> (hue-selective) or a saturation "
        "<b>CurvesTransformation</b>."
        "<ol>"
        "<li>ColorSaturation: lift the curve only over the target hues — blue (spiral "
        "arms) or red/cyan (Ha/OIII) — and hold the rest flat.</li>"
        "<li>Protect stars/background with a range or star mask so the boost doesn't leak "
        "into the sky.</li>"
        "</ol>"
    ),
    "clahe": (
        "<b>Process: LocalHistogramEqualization (LHE)</b>."
        "<ol>"
        "<li>Set <i>Kernel Radius</i> to the structure scale you want to pop (≈64–110), "
        "<i>Contrast Limit</i> ≈ 1.5–2.5, low <i>Amount</i> to start.</li>"
        "<li>Use a luminance/structure mask so flat background isn't roughened.</li>"
        "</ol>"
    ),
    "noise_reduction": (
        "<b>Process: NoiseXTerminator</b> (again, gentler) on the stretched image."
        "<ol>"
        "<li>The stretch changed the noise statistics, so this is a light touch-up — "
        "small <i>Denoise</i>, high <i>Detail</i>.</li>"
        "<li>Mask to background-only if you only want to clean the sky.</li>"
        "</ol>"
    ),
    "curves": (
        "<b>Process: CurvesTransformation</b>."
        "<ol>"
        "<li>Gentle S-curve on the RGB/K channel for contrast; pull a highlight rolloff "
        "point down to protect a bright core.</li>"
        "<li>Keep the shadow end anchored — don't lift the already-dark sky "
        "(that kills contrast).</li>"
        "<li>Use the Saturation channel here too for subtle colour depth.</li>"
        "</ol>"
    ),
    "hdr_compression": (
        "<b>Process: HDRMultiscaleTransform (HDRMT)</b>."
        "<ol>"
        "<li><i>Number of layers</i> ≈ 6, <i>Overdrive</i> 0 to start; apply through a "
        "luminance mask that exposes only the blown core.</li>"
        "<li>Follow with a slight CurvesTransformation to recover global contrast HDRMT "
        "flattens.</li>"
        "</ol>"
    ),
    "dark_enhance": (
        "<b>Process: LocalHistogramEqualization</b> or <b>MultiscaleLinearTransform</b> "
        "with a dark mask."
        "<ol>"
        "<li>Mask to the faint shadow regions (IFN, dust, outer arms) and boost large "
        "wavelet scales gently.</li>"
        "<li>Stop as soon as the background starts lifting — that's noise becoming fake "
        "nebulosity.</li>"
        "</ol>"
    ),
    "color_sat": (
        "<b>Process: ColorSaturation</b> (uniform)."
        "<ol>"
        "<li>A small global lift to recover star colour a statistical stretch washed out "
        "(HS delta ≈ 0.25).</li>"
        "<li>Avoid on globulars — it tends to introduce a green cast there.</li>"
        "</ol>"
    ),
    "combine_stars_screen": (
        "<b>Process: PixelMath</b> (screen blend)."
        "<ol>"
        "<li>With the processed <code>starless</code> and stretched <code>stars</code> "
        "images open, run PixelMath: "
        "<code>1 - (1 - starless) * (1 - stars)</code> "
        "(the screen operator, the exact inverse of StarXT unscreen removal).</li>"
        "<li>Check bright stars for doubling/halos from imperfect removal.</li>"
        "</ol>"
    ),
    "halo_suppression": (
        "<b>No native process — use a star mask + repair.</b>"
        "<ol>"
        "<li>Build a mask isolating bright-star halos (StarMask, or StarXT residual).</li>"
        "<li>Through the mask, pull the violet/blue ring with CurvesTransformation "
        "(reduce saturation + slight darken), or use MorphologicalTransformation erosion.</li>"
        "<li>On dense fields keep it minimal so you don't crush thousands of small stars "
        "or cast the field blue.</li>"
        "</ol>"
    ),
    "narrowband_composite": (
        "<b>Process: PixelMath</b> or <b>ChannelCombination</b>."
        "<ol>"
        "<li>SHO (Hubble): map R=SII, G=Ha, B=OIII via ChannelCombination.</li>"
        "<li>Foraxx-style: PixelMath blends, e.g. "
        "<code>R = Ha*0.76 + OIII*0.24</code>, "
        "<code>G = OIII*0.85 + Ha*0.15</code>, <code>B = OIII</code>.</li>"
        "<li>Then process the composite through the normal nonlinear chain.</li>"
        "</ol>"
    ),
}


# Extra inserts keyed by STEP_DOCS[...]["extra"]
EXTRA_BLOCKS = {
    "noise_picker": (
        '<div class="callout">'
        '<div class="callout-h">How the stretch is picked (noise-aware)</div>'
        'Each candidate is scored on a cost that combines how far the sky background '
        'sits from its target band, how much the subject is under- or over-blown, '
        '<b>and how grainy the background is</b> (corner-σ). The grain term '
        '(<code>noise_w · bg_noise</code>, weighted harder on thin stacks) was added '
        'after faint targets kept choosing a well-placed but grainy stretch. The '
        'lowest-cost candidate wins; ties break toward the cleaner, gentler stretch.'
        '</div>'
    ),
}


# ---------------------------------------------------------------------------
# Workflow descriptions (authored intent on top of the ontology sequence)
# ---------------------------------------------------------------------------

WORKFLOW_NOTES = {
    "seestar_broadband": "The general-purpose full pipeline for any OSC broadband target "
        "— the everything chain with star split, full non-linear shaping and HDR.",
    "seestar_galaxy": "Galaxy-tuned: GHS-leaning stretch, blue-arm colour boost, and HDR/"
        "dark-enhance left optional so low-dynamic-range disks aren't over-cooked.",
    "seestar_nebula": "Emission/reflection nebula chain with narrowband-normalize and "
        "nebula colour boost folded in; SCNR-aware of duo-band OIII.",
    "seestar_globular": "Globular-cluster chain calibrated from Henry's manual PI work: "
        "GraXpert BGE, core-masked BXT, strong NXT, stat_globular stretch, and a "
        "core-rolloff curve. color_sat removed (it added a green cast).",
    "seestar_narrowband": "Full Ha/OIII/SII palette workflow — SHO or Foraxx composite "
        "first, then the nebula chain with NBN.",
    "seestar_fast": "Quick look: crop → BGE → stretch → SCNR. For triage, not final art.",
    "quick_default": "A fast, opinionated default with every engine pinned to its "
        "best-in-class option and no intermediate assessments.",
    "seestar_starless_stretch": "Star-split stretch workflow for when star size/colour "
        "needs fully independent control.",
    "linear_only": "Stops after linear processing (no stretch) — for hand-stretching in "
        "PixInsight afterwards.",
    "experiment_full": "Renders every variant of every step and lets the scorer pick each "
        "winner — the research/calibration workflow.",
    "spcc_only": "A single-step SPCC test harness on an existing stack.",
}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _slider(key: str, caption: str) -> str:
    before = f"{EX}/{key}_before.jpg"
    after = f"{EX}/{key}_after.jpg"
    return (
        '<div class="ba-slider">'
        f'<img class="ba-after" src="{after}" alt="after {caption}" loading="lazy">'
        f'<img class="ba-before" src="{before}" alt="before {caption}" loading="lazy">'
        '<div class="ba-divider"></div>'
        '<span class="ba-lbl ba-lbl-l">before</span>'
        '<span class="ba-lbl ba-lbl-r">after</span>'
        '<input type="range" class="ba-range" value="50" min="0" max="100">'
        f'</div><div class="ba-cap">{caption}</div>'
    )


def _params_table(step_def: dict) -> str:
    params = step_def.get("parameters") or {}
    if not params:
        return ""
    rows = []
    for name, p in params.items():
        if not isinstance(p, dict):
            continue
        default = p.get("default", "")
        rng = ""
        if "min" in p and "max" in p:
            rng = f'{p["min"]}–{p["max"]}'
        elif "values" in p:
            rng = ", ".join(str(v) for v in p["values"])
        desc = _html.escape(str(p.get("description", "")))
        rows.append(
            f'<tr><td><code>{_html.escape(name)}</code></td>'
            f'<td>{_html.escape(str(default))}</td>'
            f'<td class="rng">{_html.escape(rng)}</td>'
            f'<td class="pdesc">{desc}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<details class="params"><summary>Parameters</summary>'
        '<table class="ptab"><thead><tr><th>name</th><th>default</th><th>range</th>'
        '<th>description</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table></details>'
    )


def _variants_block(step_def: dict) -> str:
    variants = step_def.get("experiment_variants") or []
    # color_boost / narrowband use a "variants" dict instead
    if not variants and isinstance(step_def.get("variants"), dict):
        variants = [{"id": k, "description": "", "params": v}
                    for k, v in step_def["variants"].items()]
    if not variants:
        return ""
    items = []
    for v in variants:
        vid = _html.escape(str(v.get("id", "")))
        desc = _html.escape(str(v.get("description", "")))
        eng = v.get("engine", "")
        eng_tag = f'<span class="veng">{_html.escape(eng)}</span>' if eng else ""
        items.append(f'<li><code>{vid}</code> {eng_tag}'
                     + (f'<span class="vdesc">{desc}</span>' if desc else "") + '</li>')
    return (
        '<details class="variants"><summary>'
        f'Variants ({len(items)})</summary><ul class="vlist">'
        + "".join(items) + '</ul></details>'
    )


def _step_card(step_key: str, step_def: dict, doc: dict) -> str:
    title = step_key.replace("_", " ")
    icon = doc.get("icon", "•")
    aesthetic = doc.get("aesthetic")
    stage = step_def.get("stage", "")
    force = step_def.get("force_apply")
    badges = []
    if stage:
        badges.append(f'<span class="sbadge st-{stage}">{stage}</span>')
    if force:
        badges.append('<span class="sbadge st-force">always on</span>')
    if aesthetic:
        badges.append('<span class="sbadge st-aes">aesthetic</span>')
    fn = step_def.get("seti_astro_fn")
    if fn:
        badges.append(f'<span class="sbadge st-fn">{_html.escape(str(fn))}</span>')

    example = ""
    if doc.get("gallery"):
        example = _stretch_gallery() + (_slider(doc["example"], "linear master → stretched (the moment the image appears)") if doc.get("example") else "")
    elif doc.get("example"):
        cap = {
            "crop": "ragged stacking border trimmed away",
            "pedestal": "ADC bias pedestal removed (subtle — a few ADU)",
            "cosmetic": "hot/cold pixels cleaned",
            "bge": "light-pollution gradient flattened",
            "spcc": "photometric white balance applied",
            "decon": "BlurXTerminator sharpening on linear data",
            "stretch": "linear master → stretched",
            "scnr": "green/teal OSC cast removed",
            "bgn": "residual background colour neutralised",
            "boost": "hue-selective saturation lift",
            "nr": "post-stretch grain reduced",
            "halo": "bright-star colour halos suppressed",
            "recombine": "stars screened back onto the processed nebula",
        }.get(doc["example"], step_key)
        example = _slider(doc["example"], cap)

    def sect(label, body):
        return (f'<div class="ssec"><span class="ssec-l">{label}</span>'
                f'<div class="ssec-b">{body}</div></div>')

    body = ""
    if doc.get("what"):
        body += sect("What it does", doc["what"])
    if doc.get("how"):
        body += sect("How it works", doc["how"])
    if doc.get("good"):
        body += sect("What makes it good", doc["good"])
    if doc.get("wrong"):
        body += sect("How it goes wrong", doc["wrong"])
    if doc.get("analyze"):
        body += sect("How to analyze it", doc["analyze"])
    if doc.get("extra") and doc["extra"] in EXTRA_BLOCKS:
        body += EXTRA_BLOCKS[doc["extra"]]
    pi = PI_MANUAL.get(step_key)
    if pi:
        body += (
            '<div class="pi-manual">'
            '<div class="pi-h"><span class="pi-ico">⌘</span>Do it manually in PixInsight</div>'
            f'<div class="pi-b">{pi}</div></div>'
        )

    return (
        f'<section class="step" id="step-{step_key}">'
        f'<h3 class="step-h"><span class="step-ico">{icon}</span>{title} '
        + " ".join(badges) +
        f'</h3>'
        f'<div class="step-grid">'
        f'<div class="step-text">{body}{_params_table(step_def)}{_variants_block(step_def)}</div>'
        f'<div class="step-media">{example}</div>'
        f'</div>'
        f'</section>'
    )


def _stretch_gallery() -> str:
    variants = [
        ("stat", "statistical — sky-median target"),
        ("stat_bright", "statistical, brighter"),
        ("ghs", "generalised hyperbolic"),
        ("ghs_soft", "GHS soft — gentle, clean"),
        ("ghs_strong", "GHS strong"),
        ("stf", "PI-style auto-stretch"),
        ("veralux", "arcsinh + colour grip"),
        ("veralux_strong", "arcsinh, strong (grainy on thin data)"),
        ("mas", "PI multiscale adaptive"),
    ]
    cells = []
    for vid, cap in variants:
        cells.append(
            f'<figure class="gcell"><a href="{EX}/var_{vid}.jpg" target="_blank">'
            f'<img src="{EX}/var_{vid}.jpg" loading="lazy" alt="{vid}"></a>'
            f'<figcaption><code>{vid}</code><span>{cap}</span></figcaption></figure>'
        )
    return (
        '<div class="gallery-wrap"><div class="gallery-h">The candidate stretches '
        '(same NGC 6914 linear master) — the picker scores all of these and chooses '
        'the lowest-cost, cleanest one:</div>'
        '<div class="gallery">' + "".join(cells) + '</div></div>'
    )


def _workflow_card(wf_key: str, wf: dict) -> str:
    note = WORKFLOW_NOTES.get(wf_key, "")
    desc = _html.escape(wf.get("description", ""))
    steps = wf.get("steps", [])
    fvars = wf.get("force_variants", {})
    chips = []
    for s in steps:
        if s.startswith("assess"):
            label = s.replace("assess_", "▸ ")
            chips.append(f'<span class="wchip wchip-assess">{label}</span>')
        else:
            fv = fvars.get(s)
            anchor = f'#step-{s}'
            extra = f' · {fv}' if fv else ""
            chips.append(f'<a class="wchip" href="{anchor}">{s.replace("_"," ")}'
                         f'{("<i>"+extra+"</i>") if extra else ""}</a>')
    fv_note = ""
    if fvars:
        fv_note = ('<div class="wfv">Pinned variants: '
                   + ", ".join(f'<code>{k}={v}</code>' for k, v in fvars.items())
                   + '</div>')
    return (
        f'<section class="wf" id="wf-{wf_key}">'
        f'<h3 class="wf-h"><code>{wf_key}</code></h3>'
        + (f'<p class="wf-note">{_html.escape(note)}</p>' if note else "")
        + (f'<p class="wf-desc">{desc}</p>' if desc else "")
        + f'<div class="wchips">{"".join(chips)}</div>'
        + fv_note
        + '</section>'
    )


def _scoring_section(ont: dict) -> str:
    dims = ont.get("quality_dimensions", {})
    targets = ont.get("stretch_quality_targets", {})
    dim_rows = []
    for name, d in dims.items():
        rem = ", ".join(d.get("remediation_steps", []))
        dim_rows.append(
            f'<tr><td><b>{name.replace("_"," ")}</b></td>'
            f'<td>{d.get("action_threshold","")}</td>'
            f'<td class="pdesc">{_html.escape(rem)}</td></tr>'
        )
    tgt_rows = []
    for name, t in targets.items():
        if name.startswith("_"):
            continue
        bg = t.get("bg_level")
        p99 = t.get("p99_range")
        bg_s = f'{bg[0]}–{bg[1]}' if bg else "—"
        p99_s = f'{p99[0]}–{p99[1]}' if p99 else "—"
        tgt_rows.append(
            f'<tr><td><b>{name.replace("_"," ")}</b></td>'
            f'<td>{bg_s}</td><td>{p99_s}</td>'
            f'<td class="pdesc">{_html.escape(t.get("note",""))}</td></tr>'
        )
    return (
        '<section class="block" id="scoring">'
        '<h2>How runs are scored</h2>'
        '<p>The pipeline is <b>physics-default</b>: objective per-step grading and '
        'optional-step gating come from pixel metrics (no API call), and Claude is '
        'reserved for the two jobs that genuinely need eyes — a Haiku crop clip-veto '
        'and a Sonnet final aesthetic pass (which may trigger one reduce-only '
        'corrective). Every checkpoint is graded on these dimensions:</p>'
        '<table class="ptab"><thead><tr><th>dimension</th><th>action&nbsp;&lt;</th>'
        '<th>remediation steps</th></tr></thead><tbody>'
        + "".join(dim_rows) + '</tbody></table>'
        '<h3 style="margin-top:1.4rem">Post-stretch pixel targets</h3>'
        '<p>The stretch is judged against measured pixel bands — sky background is the '
        'corner median, p99 is the subject brightness ceiling:</p>'
        '<table class="ptab"><thead><tr><th>type</th><th>sky&nbsp;bg</th><th>p99</th>'
        '<th>note</th></tr></thead><tbody>'
        + "".join(tgt_rows) + '</tbody></table>'
        '<div class="callout"><div class="callout-h">Non-degradation finalise</div>'
        'The pipeline always runs every mandatory step to completion. A high-water-mark '
        'of the best <i>publishable</i> checkpoint is kept, and if a later step degrades '
        'the result it is rolled back — so a run can never ship a truncated, starless, '
        'or worse-than-midway image.</div>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def workflow_docs_page() -> str:
    from nas_server.story import _page_shell
    ont = _load_ontology()
    steps_def = ont.get("processing_steps", {})
    workflows = ont.get("workflows", {})

    # --- TOC ---
    toc = ['<a href="#overview">Overview</a>',
           '<a href="#workflows">Workflows</a>']
    for sid, sname, _sdesc, _keys in STAGES:
        toc.append(f'<a href="#stage-{sid}">{sname}</a>')
    toc.append('<a href="#scoring">Scoring</a>')
    toc_html = '<nav class="toc"><div class="toc-h">Contents</div>' + "".join(toc) + '</nav>'

    # --- Workflows ---
    wf_cards = "".join(_workflow_card(k, workflows[k]) for k in workflows)

    # --- Steps by stage ---
    stage_html = ""
    for sid, sname, sdesc, keys in STAGES:
        cards = ""
        for sk in keys:
            doc = STEP_DOCS.get(sk)
            if not doc:
                continue
            cards += _step_card(sk, steps_def.get(sk, {}), doc)
        stage_html += (
            f'<section class="stage" id="stage-{sid}">'
            f'<h2 class="stage-h">{sname}</h2>'
            f'<p class="stage-d">{sdesc}</p>{cards}</section>'
        )

    body = f"""
<div class="wd-wrap">
  <header class="wd-head">
    <h1>SeeStar Processing — Workflow Reference</h1>
    <p class="lede">Every workflow and every processing step the autoprocess pipeline
    can run: what each does, how it works, what makes it good, how it goes wrong, and
    how to analyze it. Before/after sliders use real pipeline output — drag to compare.</p>
    <p class="lede small">Stages run in order: a night of sub-frames is stacked into a
    linear master, cleaned and calibrated while still linear, then stretched into a
    visible image and shaped non-linearly. Drag any slider; click a thumbnail to enlarge.</p>
  </header>
  <div class="wd-body">
    {toc_html}
    <main class="wd-main">
      <section class="block" id="overview">
        <h2>The shape of a run</h2>
        <ol class="flow">
          <li><b>Acquisition &amp; stacking</b> — cull bad subs, integrate the rest into one linear master.</li>
          <li><b>Pre-process</b> — crop the noisy border, zero the black point, clean hot pixels.</li>
          <li><b>Linear processing</b> — remove gradients, calibrate colour, deconvolve, denoise. <i>Only valid while linear.</i></li>
          <li><b>Star split &amp; stretch</b> — pull the stars aside, stretch the faint signal into visibility.</li>
          <li><b>Non-linear</b> — neutralise colour, boost the right hues, add local contrast and dynamic range.</li>
          <li><b>Recombine &amp; finish</b> — screen the stars back in, suppress halos.</li>
        </ol>
      </section>
      <section class="block" id="workflows">
        <h2>Workflows</h2>
        <p>A workflow is an ordered step list plus pinned engine choices. Click any step
        chip to jump to its full description. <span class="wchip wchip-assess">▸ assessment</span>
        chips are scoring checkpoints, not transforms.</p>
        {wf_cards}
      </section>
      {stage_html}
      {_scoring_section(ont)}
      <footer class="wd-foot">Generated from <code>processing_ontology.json</code> ·
      examples from real NGC&nbsp;6914 / M&nbsp;108 runs.</footer>
    </main>
  </div>
</div>
<script>
document.querySelectorAll('.ba-slider').forEach(function(s) {{
  var r = s.querySelector('.ba-range');
  var b = s.querySelector('.ba-before');
  var d = s.querySelector('.ba-divider');
  r.addEventListener('input', function() {{
    var pct = this.value;
    b.style.clipPath = 'inset(0 ' + (100 - pct) + '% 0 0)';
    d.style.left = pct + '%';
  }});
}});
(function() {{
  var links = document.querySelectorAll('.toc a');
  links.forEach(function(a) {{
    a.addEventListener('click', function(e) {{
      var t = document.querySelector(this.getAttribute('href'));
      if (t) {{ e.preventDefault(); t.scrollIntoView({{behavior:'smooth', block:'start'}}); }}
    }});
  }});
}})();
</script>
"""

    extra_css = """
  .wd-wrap { max-width: 1180px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
  .wd-head h1 { font-size: 1.7rem; margin-bottom: .5rem; }
  .lede { color: var(--text2); max-width: 70ch; line-height: 1.5; margin-bottom: .4rem; }
  .lede.small { font-size: .85rem; }
  .wd-body { display: flex; gap: 1.8rem; align-items: flex-start; margin-top: 1.5rem; }
  .toc { position: sticky; top: 1rem; flex: 0 0 200px; display: flex; flex-direction: column;
         gap: .15rem; font-size: .85rem; border-right: 1px solid var(--border); padding-right: 1rem; }
  .toc-h { text-transform: uppercase; letter-spacing: .08em; font-size: .7rem; color: var(--text2);
           margin-bottom: .35rem; }
  .toc a { color: var(--text2); padding: .25rem .4rem; border-radius: 5px; }
  .toc a:hover { color: var(--text); background: var(--bg3); text-decoration: none; }
  .wd-main { flex: 1 1 auto; min-width: 0; }
  .block, .stage { margin-bottom: 2.6rem; }
  .block h2, .stage-h { font-size: 1.3rem; border-bottom: 1px solid var(--border);
                        padding-bottom: .4rem; margin-bottom: .8rem; }
  .block p { color: var(--text2); line-height: 1.55; max-width: 75ch; margin-bottom: .7rem; }
  .stage-d { color: var(--text2); line-height: 1.55; max-width: 75ch; margin-bottom: 1.2rem;
             font-style: italic; }
  .flow { color: var(--text2); line-height: 1.7; padding-left: 1.2rem; max-width: 80ch; }
  .flow b { color: var(--text); }
  /* workflow cards */
  .wf { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
        padding: 1rem 1.1rem; margin-bottom: 1rem; }
  .wf-h { font-size: 1rem; margin-bottom: .3rem; }
  .wf-h code { background: var(--bg3); padding: 2px 8px; border-radius: 5px; color: var(--accent); }
  .wf-note { color: var(--text); font-size: .9rem; margin-bottom: .35rem; }
  .wf-desc { color: var(--text2); font-size: .82rem; margin-bottom: .6rem; }
  .wchips { display: flex; flex-wrap: wrap; gap: .35rem; }
  .wchip { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
           font-size: .76rem; padding: 2px 8px; border-radius: 20px; white-space: nowrap; }
  .wchip:hover { border-color: var(--accent); text-decoration: none; color: var(--accent); }
  .wchip i { color: var(--text2); font-style: normal; opacity: .8; }
  .wchip-assess { background: transparent; border-style: dashed; color: var(--text2); }
  .wfv { margin-top: .55rem; font-size: .76rem; color: var(--text2); }
  .wfv code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; margin: 0 2px; }
  /* step cards */
  .step { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
          padding: 1.1rem 1.2rem; margin-bottom: 1.1rem; }
  .step-h { font-size: 1.12rem; display: flex; align-items: center; flex-wrap: wrap; gap: .5rem;
            margin-bottom: .9rem; }
  .step-ico { display: inline-flex; width: 1.7rem; height: 1.7rem; align-items: center;
              justify-content: center; background: var(--bg3); border-radius: 7px;
              font-size: 1rem; color: var(--accent); }
  .sbadge { font-size: .66rem; padding: 1px 7px; border-radius: 20px; border: 1px solid var(--border);
            color: var(--text2); text-transform: lowercase; letter-spacing: .03em; }
  .st-force { color: #e3b341; border-color: #e3b341; }
  .st-aes { color: #bc8cff; border-color: #bc8cff; }
  .st-fn { color: var(--text2); font-family: monospace; text-transform: none; }
  .st-linear, .st-stretch, .st-nonlinear, .st-pre_process, .st-pre_stack, .st-pre_stretch
    { color: var(--accent); border-color: var(--accent); }
  .step-grid { display: grid; grid-template-columns: 1.15fr .85fr; gap: 1.4rem; }
  .step-media:empty { display: none; }
  .step-grid:has(.step-media:empty) { grid-template-columns: 1fr; }
  .ssec { margin-bottom: .7rem; }
  .ssec-l { display: block; font-size: .7rem; text-transform: uppercase; letter-spacing: .07em;
            color: var(--accent); margin-bottom: .15rem; font-weight: 600; }
  .ssec-b { color: var(--text2); line-height: 1.55; font-size: .9rem; }
  .ssec-b code { background: var(--bg3); padding: 0 4px; border-radius: 3px; font-size: .85em; }
  .ssec-b b { color: var(--text); }
  .callout { background: var(--bg3); border-left: 3px solid var(--accent); border-radius: 6px;
             padding: .7rem .9rem; margin: .8rem 0 .3rem; font-size: .86rem; color: var(--text2);
             line-height: 1.5; }
  .callout-h { color: var(--text); font-weight: 600; font-size: .82rem; margin-bottom: .25rem; }
  /* manual PixInsight recipe */
  .pi-manual { background: rgba(88,166,255,.07); border: 1px solid rgba(88,166,255,.35);
               border-radius: 8px; padding: .7rem .9rem; margin: .9rem 0 .3rem; }
  .pi-h { color: #58a6ff; font-weight: 600; font-size: .82rem; margin-bottom: .4rem;
          display: flex; align-items: center; gap: .45rem; }
  .pi-ico { display: inline-flex; width: 1.3rem; height: 1.3rem; align-items: center;
            justify-content: center; background: rgba(88,166,255,.18); border-radius: 5px;
            font-size: .8rem; }
  .pi-b { color: var(--text2); font-size: .85rem; line-height: 1.5; }
  .pi-b b { color: var(--text); }
  .pi-b ol { margin: .4rem 0 .2rem; padding-left: 1.2rem; }
  .pi-b li { margin-bottom: .28rem; }
  .pi-b code { background: var(--bg3); padding: 0 4px; border-radius: 3px; font-size: .85em; }
  .pi-b i { color: var(--text); font-style: normal; font-weight: 500; }
  /* params + variants */
  details.params, details.variants { margin-top: .6rem; }
  details summary { cursor: pointer; font-size: .8rem; color: var(--accent); padding: .2rem 0; }
  .ptab { width: 100%; border-collapse: collapse; margin-top: .5rem; font-size: .8rem; }
  .ptab th { text-align: left; color: var(--text2); font-weight: 500; border-bottom: 1px solid var(--border);
             padding: .3rem .5rem; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }
  .ptab td { padding: .3rem .5rem; border-bottom: 1px solid var(--border); vertical-align: top; color: var(--text); }
  .ptab td.rng { color: var(--text2); white-space: nowrap; }
  .ptab td.pdesc { color: var(--text2); }
  .ptab code { background: var(--bg3); padding: 0 4px; border-radius: 3px; }
  .vlist { list-style: none; margin-top: .5rem; display: flex; flex-direction: column; gap: .3rem; }
  .vlist li { font-size: .8rem; color: var(--text2); }
  .vlist code { background: var(--bg3); padding: 1px 6px; border-radius: 4px; color: var(--text); }
  .veng { font-size: .66rem; border: 1px solid var(--border); border-radius: 10px; padding: 0 6px;
          margin: 0 .4rem; color: var(--text2); }
  .vdesc { color: var(--text2); }
  /* before/after slider */
  .ba-slider { position: relative; overflow: hidden; border-radius: 8px; cursor: ew-resize;
               user-select: none; border: 1px solid var(--border); }
  .ba-slider .ba-after { display: block; width: 100%; }
  .ba-slider .ba-before { position: absolute; top: 0; left: 0; width: 100%;
                          clip-path: inset(0 50% 0 0); pointer-events: none; }
  .ba-slider .ba-divider { position: absolute; top: 0; left: 50%; width: 2px; height: 100%;
                           background: rgba(255,255,255,.8); pointer-events: none; transform: translateX(-50%); }
  .ba-slider .ba-lbl { position: absolute; top: 8px; background: rgba(0,0,0,.65); color: #fff;
                       font-size: .62rem; padding: 1px 6px; border-radius: 3px; pointer-events: none;
                       letter-spacing: .05em; text-transform: uppercase; }
  .ba-slider .ba-lbl-l { left: 8px; }
  .ba-slider .ba-lbl-r { right: 8px; }
  .ba-slider .ba-range { position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                         opacity: 0; cursor: ew-resize; margin: 0; }
  .ba-cap { font-size: .76rem; color: var(--text2); margin-top: .4rem; font-style: italic; }
  /* stretch gallery */
  .gallery-wrap { margin-bottom: .8rem; }
  .gallery-h { font-size: .82rem; color: var(--text2); margin-bottom: .55rem; line-height: 1.45; }
  .gallery { display: grid; grid-template-columns: repeat(3, 1fr); gap: .5rem; }
  .gcell { border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: var(--bg3); }
  .gcell img { display: block; width: 100%; }
  .gcell figcaption { padding: .3rem .4rem; font-size: .68rem; color: var(--text2); }
  .gcell figcaption code { color: var(--accent); display: block; }
  .gcell figcaption span { display: block; opacity: .8; }
  .wd-foot { color: var(--text2); font-size: .76rem; border-top: 1px solid var(--border);
             padding-top: 1rem; margin-top: 2rem; }
  .wd-foot code { background: var(--bg3); padding: 0 4px; border-radius: 3px; }
  @media (max-width: 820px) {
    .wd-body { flex-direction: column; }
    .toc { position: static; flex-basis: auto; border-right: none; border-bottom: 1px solid var(--border);
           padding-right: 0; padding-bottom: .8rem; flex-direction: row; flex-wrap: wrap; }
    .step-grid { grid-template-columns: 1fr; }
    .gallery { grid-template-columns: repeat(2, 1fr); }
  }
"""
    return _page_shell(title="Workflow Reference", body=body, extra_css=extra_css)

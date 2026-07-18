"""Claude API integration for astrophoto quality assessment and stretch optimization."""
import base64
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are an expert astrophotographer evaluating stacked astronomical images. "
    "You respond ONLY with valid JSON matching the exact schema provided. "
    "All numeric scores use one decimal place in range 1.0–10.0 (e.g. 6.5, 8.0) where 10.0 is best.\n\n"
    "CORE PHILOSOPHY — non-linear processing must be subtle:\n"
    "• The image has already been stretched. Every subsequent step risks destroying what the stretch built.\n"
    "• A good non-linear result looks barely different from before — the improvement is felt, not obvious.\n"
    "• If you have to ask 'is this better?', the answer is usually 'barely, and that's correct'.\n"
    "• Always prefer the gentler variant. Reserve stronger variants for images that genuinely need rescue.\n"
    "• skip=true is a valid and often correct answer for non-linear enhancement steps."
)


def _client():
    from nas_server.config import settings
    import anthropic
    key = settings.get("anthropic_api_key", "")
    if not key:
        raise RuntimeError("anthropic_api_key not set")
    return anthropic.Anthropic(api_key=key)


_PHYSICS_ONLY = False


def set_physics_only(enabled: bool) -> None:
    """Force physics-only mode: every API call raises immediately, so all callers
    fall through to their physics/default fallbacks exactly as in a real outage.
    Used to deterministically exercise (and trust) the no-API processing path."""
    global _PHYSICS_ONLY
    _PHYSICS_ONLY = bool(enabled)


class PhysicsOnlyMode(RuntimeError):
    """Raised by _messages_create when physics-only mode is active."""


def _messages_create(*, max_tokens: int, system, messages, model: str = MODEL,
                     label: str = "?"):
    """messages.create with diagnostics. Cloud-only — no local-model fallback.

    Calls the Anthropic API and records the call (success or failure) in
    api_diagnostics. On error the exception is re-raised so each caller's except
    branch returns its default; the pipeline's physics-only fallback then takes
    over (see grade_from_physics + the risky-step skip in auto_process).
    """
    from nas_server import api_diagnostics as _diag
    t0 = time.time()
    if _PHYSICS_ONLY:
        _diag.record(label, model, "physics_only", 0, 0, 0.0,
                     False, "physics-only mode")
        raise PhysicsOnlyMode("physics-only mode active")
    try:
        client = _client()
        resp = client.messages.create(model=model, max_tokens=max_tokens,
                                       system=system, messages=messages)
        _diag.record(label, model, "anthropic",
                     resp.usage.input_tokens, resp.usage.output_tokens,
                     time.time() - t0, True,
                     cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                     cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0)
        return resp
    except Exception as api_err:
        _diag.record(label, model, "error", 0, 0, time.time() - t0,
                     False, str(api_err))
        raise


_MAX_IMAGE_BYTES = 4_800_000  # stay under Anthropic's 5 MB base64-decoded limit


def _b64(path: str) -> str:
    """Base64-encode an image, resizing it down if it exceeds the API limit."""
    import io
    raw = Path(path).read_bytes()
    if len(raw) <= _MAX_IMAGE_BYTES:
        return base64.standard_b64encode(raw).decode()
    # Image too large — shrink with PIL until it fits
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        quality = 85
        scale = 1.0
        while True:
            scale *= (_MAX_IMAGE_BYTES / len(raw)) ** 0.5
            scale = min(scale, 0.95)  # always shrink at least 5%
            new_w = max(256, int(img.width * scale))
            new_h = max(256, int(img.height * scale))
            buf = io.BytesIO()
            img.resize((new_w, new_h), Image.LANCZOS).save(buf, format="JPEG", quality=quality)
            raw = buf.getvalue()
            if len(raw) <= _MAX_IMAGE_BYTES:
                break
            quality = max(60, quality - 10)
            if new_w <= 256 and quality <= 60:
                break  # give up — send what we have
        return base64.standard_b64encode(raw).decode()
    except Exception:
        # PIL unavailable or failed — just truncate to avoid hard crash
        return base64.standard_b64encode(raw[:_MAX_IMAGE_BYTES]).decode()


def _parse_json(raw: str) -> dict:
    """Parse JSON from a model response, tolerating fences and trailing prose.

    Claude sometimes wraps JSON in ```json fences or appends commentary after the
    closing brace; plain json.loads() then dies with "Extra data: ...". Strip fences,
    skip any leading prose to the first { or [, then raw_decode() the first complete
    value and ignore whatever trails it.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()
    start = next((i for i, c in enumerate(text) if c in "{["), 0)
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _baseline_content(baseline_jpg: str | Path | None) -> list[dict]:
    """Return message content blocks for the baseline image, if provided."""
    if baseline_jpg is None:
        return []
    p = str(baseline_jpg)
    try:
        return [
            {"type": "text", "text": (
                "The image above is the current result. "
                "Below is the quick_default baseline at this same processing stage "
                "(GraXpert→SPCC→BXT→NXT→StarXT→stat_stretch 0.08→SCNR→star_stretch→screen blend). "
                "Use it to calibrate your scores — the baseline is a known-good reference:"
            )},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": _b64(p),
            }},
        ]
    except Exception:
        return []


def _folio_prompt_block(target: str, folio: dict | None, mode: str = "step") -> str:
    """
    Build a folio context block for Claude prompts.
    mode='stack'  — post-stack assessment: includes key features, anchors, color expectations
    mode='step'   — processing step assessment: includes masking, challenge features, color
    """
    if not folio:
        return ""
    common = folio.get("common_name", target)
    thr = folio.get("quality_thresholds", {})
    proc = folio.get("processing_notes", {})
    vc = folio.get("visual_character", {})
    masking = proc.get("masking", {})
    color = proc.get("color", {})

    lines = [f"\nREFERENCE FOLIO — {common}:"]

    # Key features and assessment anchors (both modes)
    feats = folio.get("key_features", [])
    if feats:
        lines.append(f"Key features to look for: {', '.join(feats)}")
    anchors = folio.get("assessment_anchors", [])
    if anchors:
        lines.append("Assessment anchors:")
        lines.extend(f"  - {a}" for a in anchors)

    # Score thresholds
    if thr:
        lines.append(
            f"Score thresholds: excellent≥{thr.get('score_excellent','?')} "
            f"good≥{thr.get('score_good','?')} | "
            f"background target: {thr.get('bg_level_range','?')}"
        )

    # Visual character — colors and challenges
    colors = vc.get("dominant_colors", [])
    if colors:
        lines.append(f"Expected colors: {', '.join(colors[:4])}")
    challenges = vc.get("challenge_features", [])
    if challenges:
        lines.append(f"Known challenges: {'; '.join(challenges[:3])}")

    if mode == "stack":
        # For stack assessment: add color calibration expectations
        if color.get("spcc_recommended") is not None:
            spcc_str = "recommended" if color["spcc_recommended"] else "not applicable"
            lines.append(f"SPCC: {spcc_str}")
        sat = color.get("saturation_profile", "")
        if sat:
            lines.append(f"Color profile: {sat}")

    elif mode == "step":
        # For step assessment: add masking context and processing approach
        if masking:
            mask_items = [f"{k}: {v}" for k, v in masking.items()
                          if isinstance(v, str) and k not in ("emission_nebula_note",)]
            if mask_items:
                lines.append(f"Masking applied: {'; '.join(mask_items[:3])}")
        stretch = proc.get("stretch_approach", "")
        if stretch:
            lines.append(f"Stretch approach: {stretch[:120]}")
        scnr = proc.get("scnr", "")
        if scnr:
            lines.append(f"SCNR: {scnr[:80]}")

    return "\n".join(lines) + "\n"


def assess_stacked_image(
    target: str,
    jpeg_path: str,
    meta: dict,
    baseline_jpg: str | Path | None = None,
    physics: dict | None = None,
    reference_folio: dict | None = None,
    corrective_candidates: list[str] | None = None,
    measured_bg: dict | None = None,
) -> dict | None:
    """
    Analyze a stacked preview JPEG and return quality scores.

    meta keys: stackcnt, total_hours, obs_date, filter, object_type
    physics: optional dict of objective stack metrics from stack_assessor
             (snr_stack, fwhm_stack, ecc_stack, sigma_sky, flatness_rms,
              clipping_frac, star_count_stack, efficiency)
    baseline_jpg: optional quick_default reference image at the same stage.
    reference_folio: optional per-target folio dict from target_folios/*.json

    Returns dict with keys: overall, noise, gradient, star_roundness,
    stretch_quality, color_balance, issues, suggestions,
    raw_response, input_tokens, output_tokens.
    Returns None if no API key configured.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    obj_type = meta.get("object_type") or "deep sky object"

    # Build physics ontology block — gives Claude objective ground truth to calibrate
    # its visual scores against measured values
    physics_block = ""
    if physics:
        def _pval(key, fmt, unit=""):
            v = physics.get(key)
            return f"{fmt.format(v)}{unit}" if v is not None else "n/a"
        physics_block = (
            "\n\nEquipment context: ZWO SeeStar S50 (50mm f/5, built-in alt-az tracking). "
            "Normal ecc for this instrument: 0.35–0.50. "
            "Stack efficiency of 0.60–0.75 is normal for large frame counts with rejection. "
            "Do not flag these as issues unless they exceed the equipment-appropriate thresholds below.\n\n"
            "Objective physics measurements (use these to ground your visual scores):\n"
            f"- SNR: {_pval('snr_stack', '{:.1f}')}  "
            "(signal-to-noise ratio; >20 excellent, 10–20 good, <10 noisy)\n"
            f"- FWHM: {_pval('fwhm_stack', '{:.2f}', ' px')}  "
            "(star width; <2.0px excellent, 2–3px good, >3.5px soft/poor seeing)\n"
            f"- Star eccentricity: {_pval('ecc_stack', '{:.3f}')}  "
            "(star shape; <0.35 good, 0.35–0.55 acceptable for SeeStar, >0.60 elongated/flag)\n"
            f"- Sky sigma: {_pval('sigma_sky', '{:.1f}')}  "
            "(background noise; lower is better; context-dependent on exposure)\n"
            f"- Flatness RMS: {_pval('flatness_rms', '{:.4f}')}  "
            "(background gradient; <0.005 flat/excellent, 0.01–0.05 moderate, >0.05 strong gradient)\n"
            f"- Clipping fraction: {_pval('clipping_frac', '{:.1%}')}  "
            "(pixels lost to rejection; <5% normal, >15% aggressive rejection or bad data)\n"
            f"- Stars detected: {_pval('star_count_stack', '{:,.0f}')}  "
            "(total star count in stack; more is generally better for deep sky)\n"
            f"- Stack efficiency: {_pval('efficiency', '{:.3f}')}  "
            "(measured SNR / theoretical √N SNR; >0.80 excellent, 0.60–0.80 good, <0.55 poor)\n\n"
            "Cross-reference these numbers with what you see visually. If SNR is low, "
            "your noise score should reflect that. If FWHM is high, note it in star_roundness. "
            "If flatness_rms is high, gradient score should be lower. "
            "Your scores should be consistent with the physics — do not give a 9/10 noise "
            "score if SNR is 8."
        )

    folio_block = _folio_prompt_block(target, reference_folio, mode="stack")

    # Measured-background calibration. The grader repeatedly over-called "too dark /
    # crushed blacks" against backgrounds that MEASURE on-target (SH 2-273, NGC 4565,
    # IC 4592, IC 5146, M 42). Hand it the measured corner-median sky vs the per-target
    # band so darkness that is measured-correct isn't penalised by visual impression.
    measured_bg_block = ""
    if measured_bg and measured_bg.get("bg_level") is not None:
        _bg = measured_bg.get("bg_level")
        _tgt = measured_bg.get("bg_target", "?")
        _low = measured_bg.get("bg_low", False)
        _high = measured_bg.get("bg_high", False)
        if _low:
            _verdict = ("BELOW the band — genuinely crushed; faint-signal loss is a real "
                        "risk, score stretch_quality/dynamic_range accordingly")
        elif _high:
            _verdict = ("ABOVE the band — the sky is too bright/over-stretched (or carries "
                        "real diffuse emission for nebulae); do NOT call it 'too dark'")
        else:
            _verdict = ("WITHIN the on-target band — this darkness is measured-correct. Do "
                        "NOT score it down as 'too dark', 'crushed', or 'losing faint detail'")
        measured_bg_block = (
            f"\n\nMeasured background (corner-median sky): {_bg:.3f}; "
            f"per-target target band: {_tgt}.\n"
            f"→ The measured sky is {_verdict}. Trust this number over the visual "
            f"impression of darkness — a dark-but-in-band sky is the correct result, "
            f"not a defect.\n"
        )

    # Per-channel sky colour cast (#8). The luminance bg above is colour-blind, so a
    # neutral-looking-but-tinted sky (NGC 2244 NBN sky B/R 1.46) slips past the vision
    # grade. Hand the grader the measured corner-sky R/G/B balance so a real cast is scored.
    color_cast_block = ""
    _srgb = measured_bg.get("sky_rgb") if measured_bg else None
    if _srgb and _srgb.get("sky_r") is not None:
        _br = _srgb.get("sky_b_over_r", 1.0)
        _gr = _srgb.get("sky_g_over_r", 1.0)
        _imb = max(abs(_br - 1.0), abs(_gr - 1.0))
        if _imb > 0.12:
            if _br > 1.0 + 0.12 and _br >= _gr:
                _dom = "blue"
            elif _gr > 1.0 + 0.12 and _gr >= _br:
                _dom = "green"
            elif _br < 1.0 and _gr < 1.0:
                _dom = "red"
            else:
                _dom = "off-neutral"
            _cv = (f"The sky is NOT neutral — it carries a measurable {_dom} cast. "
                   f"Penalise color_balance accordingly; a neutral grey sky should read "
                   f"R≈G≈B. (Exception: a genuine narrowband palette can legitimately tint "
                   f"the whole field — judge whether this cast is in the SKY background.)")
        else:
            _cv = ("The sky is colour-neutral (R≈G≈B) — do not invent a cast the "
                   "measurement doesn't support.")
        color_cast_block = (
            f"\n\nMeasured sky colour balance (corner-median): "
            f"R={_srgb.get('sky_r', 0):.3f} G={_srgb.get('sky_g', 0):.3f} "
            f"B={_srgb.get('sky_b', 0):.3f} (B/R={_br:.2f}, G/R={_gr:.2f}).\n"
            f"→ {_cv}\n"
        )

    # Reduce-only corrective (WS5): when the image is over-processed, the pipeline can
    # re-run ONE recently-applied step at reduced strength (or drop it) and re-grade.
    # Claude may name a single candidate step to dial back — it can NEVER add a step,
    # raise a parameter, or introduce a new operation. See [[project-physics-default-pipeline]].
    corrective_block = ""
    corrective_schema = ""
    if corrective_candidates:
        corrective_block = (
            "\n\nReduce-only corrective option:\n"
            "If — and ONLY if — this image looks over-processed (over-saturated, crushed/"
            "blown highlights, halos, an over-bright or over-boosted look, harsh contrast), "
            "you may request that ONE of the following recently-applied steps be re-run at "
            "REDUCED strength (or dropped). This is the only change you can request and it is "
            "strictly reduce-only: you cannot add a step, raise any parameter, or suggest a new "
            "operation. If the image looks good or the problem isn't over-processing, return "
            'corrective: null.\n'
            f"Candidate steps (pick at most one, or null): {', '.join(corrective_candidates)}\n"
            "factor is the strength multiplier to apply (0.0 drops the step entirely; "
            "0.3–0.8 dials it back; never ≥ 1.0)."
        )
        corrective_schema = (
            ', "corrective": null | {"step": "<one candidate step>", '
            '"factor": <float 0.0-0.8>, "reason": "<short>"}'
        )

    prompt = (
        f"Evaluate this stacked astrophotography image of {target} ({obj_type}).\n\n"
        f"Stack metadata:\n"
        f"- Frames stacked: {meta.get('stackcnt', '?')}\n"
        f"- Total integration: {meta.get('total_hours', '?')} hours\n"
        f"- Filter: {meta.get('filter', 'IRCUT')}\n"
        f"- Date: {meta.get('obs_date', '?')}"
        f"{physics_block}"
        f"{folio_block}"
        f"{measured_bg_block}"
        f"{color_cast_block}"
        f"{corrective_block}\n\n"
        "Return ONLY this JSON schema, nothing else:\n"
        '{"overall": <float 1.0-10.0>, "noise": <float 1.0-10.0>, "gradient": <float 1.0-10.0>, '
        '"star_roundness": <float 1.0-10.0>, "stretch_quality": <float 1.0-10.0>, '
        '"color_balance": <float 1.0-10.0>, '
        '"issues": [<string>, ...], "suggestions": [<string>, ...]'
        f"{corrective_schema}" "}"
    )

    try:
        response = _messages_create(
            label="assess_stacked_image",
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": _b64(jpeg_path),
                }},
                *_baseline_content(baseline_jpg),
            ]}],
        )
        raw = response.content[0].text
        scores = _parse_json(raw)
        scores["raw_response"] = raw
        scores["input_tokens"] = response.usage.input_tokens
        scores["output_tokens"] = response.usage.output_tokens
        log.info(f"[claude] {target} assessment: overall={scores.get('overall')}/10 "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tokens)")
        return scores
    except Exception as e:
        log.error(f"[claude] assess_stacked_image failed for {target}: {e}")
        return None


def grade_from_physics(stats: dict, meta: dict | None = None) -> dict:
    """
    Derive a quality grade purely from objective pixel metrics — no API call.

    Used as the trusted fallback when assess_stacked_image returns None (API
    down / no key). Maps image_analyzer `stats` onto the same 1.0–10.0 schema
    Claude produces, using the calibration documented in
    auto_process._physics_score_dimensions, so a physics-only run still yields a
    defensible overall score and the pipeline can continue.

    Returns the assess schema with `_source: "physics"` so callers/UI can tell a
    physics grade from a Claude grade. Always returns a dict (never None).
    """
    def _c(v: float) -> float:
        return round(max(1.0, min(10.0, v)), 1)

    noise_b = stats.get("noise", {}) or {}
    bg_b    = stats.get("background", {}) or {}
    psf_b   = stats.get("psf", {}) or {}
    color_b = stats.get("color", {}) or {}
    hist_b  = stats.get("histogram", {}) or {}

    snr   = noise_b.get("snr", 0.0)
    grad  = bg_b.get("gradient_severity", 0.25)
    fwhm  = psf_b.get("fwhm_median", 3.5)
    green = color_b.get("green_excess", 0.0)
    dr    = hist_b.get("dynamic_range", 0.1)

    noise_s   = _c(snr * 0.37 + 0.4)
    gradient_s = _c(10.0 - grad * 40)
    star_s    = _c(12.5 - fwhm * 2.5)
    color_s   = _c(10.0 - abs(green) * 800)
    stretch_s = _c(dr * 25)

    # Overall: weighted toward noise/gradient/stars (the metrics physics measures
    # reliably); stretch/color are softer proxies so they carry less weight.
    overall = _c(
        noise_s * 0.30 + gradient_s * 0.25 + star_s * 0.20
        + stretch_s * 0.15 + color_s * 0.10
    )

    issues: list[str] = []
    suggestions: list[str] = []
    if noise_s < 6.0:
        issues.append(f"low SNR ({snr:.1f})")
        suggestions.append("apply additional noise reduction")
    if gradient_s < 6.0:
        issues.append(f"background gradient (severity {grad:.3f})")
        suggestions.append("run background extraction")
    if star_s < 6.0:
        issues.append(f"soft stars (FWHM {fwhm:.2f}px)")
    if color_s < 6.0:
        issues.append(f"colour cast (green excess {green:.4f})")
        suggestions.append("apply SCNR / colour calibration")

    return {
        "overall": overall,
        "noise": noise_s,
        "gradient": gradient_s,
        "star_roundness": star_s,
        "stretch_quality": stretch_s,
        "color_balance": color_s,
        "issues": issues,
        "suggestions": suggestions,
        "raw_response": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "_source": "physics",
    }


def pick_best_stretch(target: str, variants: list[dict]) -> dict | None:
    """
    Compare JPEG stretch variants and pick the best one.

    variants: list of dicts with keys: name (str), jpeg_path (str), command (str)

    Returns dict with keys: winner (name str), scores (dict name->int),
    reasoning (str), raw_response, input_tokens, output_tokens.
    Returns None if no API key or fewer than 2 variants.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None
    if len(variants) < 2:
        return None

    content = [
        {"type": "text", "text": (
            f"Compare these {len(variants)} stretch variants of the stacked image of {target}. "
            "Pick the one that best reveals detail while preserving highlights and natural color.\n\n"
        )},
    ]
    for v in variants:
        content.append({"type": "text", "text": f"Variant: {v['name']} ({v.get('command', '')})"})
        content.append({"type": "image", "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": _b64(v["jpeg_path"]),
        }})

    names = [v["name"] for v in variants]
    score_schema = ", ".join(f'"{n}": <float 1.0-10.0>' for n in names)
    content.append({"type": "text", "text": (
        f"Return ONLY this JSON, nothing else:\n"
        f'{{"winner": <one of {names}>, '
        f'"scores": {{{score_schema}}}, '
        f'"reasoning": <string>}}'
    )})

    try:
        response = _messages_create(
            label="pick_best_stretch",
            model=MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text
        result = _parse_json(raw)
        result["raw_response"] = raw
        result["input_tokens"] = response.usage.input_tokens
        result["output_tokens"] = response.usage.output_tokens
        log.info(f"[claude] {target} stretch winner: {result.get('winner')} "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tokens)")
        return result
    except Exception as e:
        log.error(f"[claude] pick_best_stretch failed for {target}: {e}")
        return None


def recommend_processing_step(
    target: str,
    jpeg_path: str,
    step_id: str,
    ontology_step: dict,
    current_scores: dict,
    learned_defaults: dict | None = None,
    baseline_jpg: str | Path | None = None,
    stats_before: dict | None = None,
) -> dict | None:
    """
    Inspect the current image and recommend parameters for `step_id`.

    ontology_step: the processing_steps entry from processing_ontology.json
    current_scores: latest Claude assessment scores dict
    learned_defaults: output of experiments.get_learned_defaults() — optional prior knowledge
    stats_before: objective pixel statistics from image_analyzer (pre-step)

    Returns {"parameters": {...overrides...}, "confidence": 1-10,
             "reasoning": str, "skip": bool} or None if no API key.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    params_desc = json.dumps(ontology_step.get("parameters", {}), indent=2)
    scores_desc = json.dumps(
        {k: v for k, v in current_scores.items()
         if isinstance(v, (int, float)) and not k.startswith("_")},
        indent=2,
    )
    note = ontology_step.get("note", "")

    prior_note = ""
    if learned_defaults and learned_defaults.get("sample_count", 0) >= 3:
        ld = learned_defaults
        prior_note = (
            f"\n\nLearned from {ld['sample_count']} past experiments "
            f"(confidence {int(ld['confidence']*100)}%): "
            f"preferred params = {json.dumps(ld.get('params', {}))}\n"
            f"Win rates by variant: {json.dumps(ld.get('win_rates', {}))}\n"
            f"Use these as a starting point but adjust based on the image."
        )

    if stats_before:
        _nb = stats_before.get("noise", {})
        _bb = stats_before.get("background", {})
        _pb = stats_before.get("psf", {})
        _cb = stats_before.get("color", {})
        stats_text = (
            f"\n**Objective image metrics (pre-step pixel measurements):**\n"
            f"- SNR: {_nb.get('snr',0):.1f}\n"
            f"- FWHM (star size): {_pb.get('fwhm_median',0):.2f}px "
            f"(p90={_pb.get('fwhm_p90',0):.2f}px)\n"
            f"- Gradient severity: {_bb.get('gradient_severity',0):.3f} "
            f"(0=flat, 1=severe)\n"
            f"- Sky background: {_bb.get('sky_background',0):.4f}\n"
            f"- Green excess: {_cb.get('green_excess',0):.5f}\n"
            f"- Dynamic range: {stats_before.get('histogram',{}).get('dynamic_range',0):.4f}\n"
            f"- Sharpness index: {stats_before.get('spatial_freq',{}).get('sharpness_index',0):.4f}\n"
        )
    else:
        stats_text = ""

    # Per-step visual quality guidance — teaches Claude what overdone looks like
    _STEP_GUIDANCE = {
        "curves": (
            "\n⚠️  CURVES — POST-STRETCH RULES:\n"
            "These are non-linear data. Curves must be very subtle. Visual signs of OVERDONE curves:\n"
            "  • Background goes muddy/crusty dark — shadow gradation is lost\n"
            "  • Faint nebula structure near the edges disappears\n"
            "  • Regions that had soft dark detail are clipped to pure black\n"
            "  • Image looks 'processed' rather than natural\n"
            "If the image already has good contrast, set skip=true.\n"
            "Prefer amount ≤ 0.20. Only go higher if the image is genuinely flat/washed-out.\n"
            "The 'feather' variant (0.12) is often the right answer.\n"
        ),
        "dark_enhance": (
            "\n⚠️  DARK ENHANCE — POST-STRETCH RULES:\n"
            "This lifts faint shadow detail via wavelet decomposition. Visual signs of OVERDONE:\n"
            "  • Clean black sky lifts to a grey/brownish haze\n"
            "  • Noise texture becomes visible in what should be empty sky\n"
            "  • Dark regions look muddy rather than deep and clean\n"
            "  • Background loses the 'depth of space' feeling\n"
            "Only use this step when there is genuine faint outer structure to reveal "
            "(outer halo, IFN, extended nebulosity). If the background is already clean and dark, "
            "set skip=true. Prefer boost_factor ≤ 1.3 (dse_whisper or dse_gentle).\n"
        ),
        "clahe": (
            "\n⚠️  CLAHE — POST-STRETCH RULES:\n"
            "Local contrast enhancement. Signs of overdone: halos around bright nebula cores, "
            "star halos amplified, artificial-looking 'cartoon' texture in smooth regions.\n"
            "Prefer subtle clip_limit values. If structure already has good local contrast, skip.\n"
        ),
        "color_sat": (
            "\n⚠️  COLOR SATURATION — POST-STRETCH RULES:\n"
            "Signs of overdone: background noise turns coloured/speckled, stars grow coloured halos, "
            "nebula hues look garish/neon rather than natural, faint colour gradations look banded.\n"
            "Prefer sat_feather (0.08) or sat_gentle (0.15). Only use sat_moderate/strong if the "
            "image is genuinely desaturated and pale. If colours already look vibrant, set skip=true.\n"
        ),
        "noise_reduction": (
            "\n⚠️  NOISE REDUCTION — POST-STRETCH RULES:\n"
            "Signs of overdone: nebula edges look smeared/painted, star cores become blobs with "
            "no point, fine detail washes away, image looks like a watercolour. "
            "Prefer nxt_gentle (0.45) for clean data; only step up to nxt_strong (0.80) for "
            "genuinely noisy images with visible grain. detail_preservation should stay ≥ 0.12.\n"
        ),
        "clahe": (
            "\n⚠️  CLAHE — LOCAL CONTRAST RULES:\n"
            "Signs of overdone: halos form around bright nebula cores, 'HDR grunge' texture appears "
            "in smooth regions, star halos are amplified, image looks artificial/crunchy.\n"
            "Prefer clahe_whisper (1.0) or clahe_mild (1.5). clip_limit above 2.0 is rarely correct "
            "for already-stretched astrophotos. If local contrast is already good, set skip=true.\n"
        ),
        "hdr_compression": (
            "\n⚠️  HDR COMPRESSION — RULES:\n"
            "Signs of overdone: halos around the bright nebula core, image looks 'tone-mapped', "
            "brightness gradients flatten out, highlights lose natural falloff.\n"
            "Only useful when the core is genuinely blown/clipped. Prefer hdr_whisper (1.1) or "
            "hdr_mild (1.3). If there is no bright clipped core, set skip=true.\n"
        ),
        "halo_suppression": (
            "\n⚠️  HALO SUPPRESSION — RULES (default should be skip=true):\n"
            "This step ONLY helps when bright stars have visible colour halos or are noticeably "
            "bloated/puffy with rings around them.\n"
            "Signs of overdone: overall image darkens, faint outer nebulosity is suppressed along "
            "with the halos, stars lose natural brightness, the image looks 'shrunk'.\n"
            "Critical: faint red/Ha emission around a nebula is NOT a halo — do not suppress it.\n"
            "If stars look like clean sharp points or small discs with no obvious colour rings, "
            "set skip=true. Use halo_minimal (level 0) only for the slightest bloat. "
            "halo_mild (level 1) is already significant. Reserve halo_standard (level 2) for "
            "severely bloated stars only. When in doubt, skip.\n"
        ),
    }
    step_guidance = _STEP_GUIDANCE.get(step_id, "")

    prompt = (
        f"You are optimizing the '{step_id}' processing step for an astrophoto of {target}.\n"
        + (f"Step purpose: {note}\n" if note else "")
        + f"\nCurrent quality scores (1.0–10.0, higher is better):\n{scores_desc}\n"
        + stats_text
        + step_guidance
        + f"\nAvailable parameters with ranges:\n{params_desc}\n"
        + prior_note
        + ("\n⚠️ RESCUE MODE: previous parameter attempts did not improve quality. "
           "Recommend a significantly different approach — could be gentler or more aggressive, "
           "whichever the image needs. Do not repeat similar values to what was already tried.\n"
           if current_scores.get("_rescue_mode") else "")
        + f"\n\nReview the image and recommend specific parameter values for this step. "
        f"Return ONLY values that differ from the defaults. "
        f"Set skip=true if this step would not improve quality.\n"
        f"\nReturn ONLY this JSON, nothing else:\n"
        f'{{"parameters": {{...overrides only...}}, '
        f'"confidence": <float 1.0-10.0>, "reasoning": <one sentence>, "skip": <bool>}}'
    )

    try:
        response = _messages_create(
            label=f"recommend_step:{step_id}",
            model=MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": _b64(jpeg_path),
                }},
                *_baseline_content(baseline_jpg),
            ]}],
        )
        raw = response.content[0].text
        result = _parse_json(raw)
        result.setdefault("skip", False)
        result.setdefault("parameters", {})
        log.info(f"[claude] {target} {step_id}: skip={result['skip']} "
                 f"confidence={result.get('confidence')} "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tokens)")
        return result
    except Exception as e:
        log.error(f"[claude] recommend_processing_step failed for {target}/{step_id}: {e}")
        return None


def assess_quality_dimensions(
    target: str,
    jpeg_path: str,
    dimensions: list[str],
    meta: dict,
    baseline_jpg: str | Path | None = None,
    stats_before: dict | None = None,
    stats_after: dict | None = None,
    stretch_stats: dict | None = None,
    reference_folio: dict | None = None,
) -> dict:
    """
    Targeted assessment: score only the specified quality dimensions.
    Faster and cheaper than full assess_stacked_image — used in the iteration loop
    to check whether a processing step actually improved the relevant qualities.

    dimensions: subset of quality dimension names, e.g. ["noise", "detail_level"]
    stats_before/stats_after: objective pixel statistics from image_analyzer — shown to Claude
    to calibrate visual scores with objective data.
    Returns dict mapping each dimension to a 1-10 score. Empty dict if no API key.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key") or not dimensions:
        return {}

    _dim_desc = {
        "noise":          "noise level (10=clean grain-free, 1=very noisy/grainy)",
        "gradient":       "background uniformity (10=flat even background, 1=strong gradient/vignette)",
        "star_roundness": "star shape quality (10=perfectly round compact stars, 1=trailed/elongated)",
        "stretch_quality": (
            "stretch quality — background darkness, midtone exposure, highlight preservation. "
            "10=sky near-black (≤0.08 for galaxies), structure well-exposed, no clipped cores. "
            "7=slight overstretch or minor clipping. "
            "4=clearly overstretched (brown/grey sky visible) or under-stretched (flat/faint). "
            "1=severely mis-stretched (washed-out or invisible)."
        ),
        "color_balance":  "colour accuracy (10=natural colours, 1=strong green/red/blue cast)",
        "detail_level":   "fine structure resolution (10=sharp crisp detail, 1=soft or smeared)",
        "dynamic_range":  "highlight/shadow balance (10=both preserved, 1=blown highlights or crushed blacks)",
        "overall":        "overall image quality (10=excellent, 1=poor)",
    }
    obj_type = meta.get("object_type") or "deep sky object"
    descriptions = "\n".join(f"- {d}: {_dim_desc.get(d, d)}" for d in dimensions)
    schema = ", ".join(f'"{d}": <float 1.0-10.0>' for d in dimensions)

    # Build objective stats note to calibrate Claude's visual assessment
    stats_note = ""
    if stats_before and stats_after:
        snr_b = stats_before.get("noise", {}).get("snr", 0)
        snr_a = stats_after.get("noise", {}).get("snr", 0)
        grad_b = stats_before.get("background", {}).get("gradient_severity", 0)
        grad_a = stats_after.get("background", {}).get("gradient_severity", 0)
        fwhm_b = stats_before.get("psf", {}).get("fwhm_median", 0)
        fwhm_a = stats_after.get("psf", {}).get("fwhm_median", 0)
        lines = ["\nObjective pixel statistics (before → after this processing step):"]
        if snr_b > 0:
            lines.append(f"  SNR: {snr_b:.1f} → {snr_a:.1f} "
                         f"({'improved' if snr_a > snr_b else 'degraded'})")
        if grad_b > 0:
            lines.append(f"  Gradient severity: {grad_b:.3f} → {grad_a:.3f} "
                         f"({'improved' if grad_a < grad_b else 'worsened'})")
        if fwhm_b > 0:
            lines.append(f"  Star FWHM: {fwhm_b:.2f}px → {fwhm_a:.2f}px "
                         f"({'sharper' if fwhm_a < fwhm_b else 'softer'})")
        lines.append("Use these objective metrics to calibrate your visual scores — "
                     "if stats show degradation your scores should reflect it.\n")
        stats_note = "\n".join(lines)

    if stretch_stats and "stretch_quality" in dimensions:
        bg = stretch_stats.get("bg_level", 0)
        p99 = stretch_stats.get("p99", 0)
        ok = stretch_stats.get("bg_ok", False)
        tgt = stretch_stats.get("bg_target", "0.05–0.09")
        stats_note += (
            f"\nPost-stretch pixel statistics:\n"
            f"  Sky background (corner median): {bg:.3f} "
            f"({'on target' if ok else 'TOO BRIGHT — overstretched'})\n"
            f"  Target range: {tgt} (galaxy) or 0.06–0.10 (nebula)\n"
            f"  99th percentile: {p99:.3f}"
            + ("  ← highlights likely clipped" if p99 > 0.93 else "") + "\n"
            "  Anchor your stretch_quality score to these numbers — "
            "a sky bg > 0.10 should score ≤ 5 regardless of visual impression.\n"
        )

    folio_note = _folio_prompt_block(target, reference_folio, mode="step")

    prompt = (
        f"Evaluate this astrophoto of {target} ({obj_type}) for these specific qualities only:\n"
        f"{descriptions}\n"
        f"{folio_note}"
        f"{stats_note}\n"
        f"Return ONLY this JSON, nothing else:\n"
        f"{{{schema}}}"
    )

    try:
        response = _messages_create(
            label="assess_quality_dimensions",
            model=MODEL,
            max_tokens=128,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": _b64(jpeg_path),
                }},
                # No baseline image here — stats_before/stats_after already provide the
                # objective before/after comparison; baseline doubles image token cost
                # across 20-30 calls/run for no measurable accuracy gain.
            ]}],
        )
        raw = response.content[0].text
        parsed = _parse_json(raw)
        scores = {d: parsed[d] for d in dimensions if d in parsed}
        log.info(f"[claude] {target} targeted [{', '.join(dimensions)}]: {scores} "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tok)")
        return scores
    except Exception as e:
        log.error(f"[claude] assess_quality_dimensions failed for {target}: {e}")
        return {}


def generate_critical_eval(
    target: str,
    workflow: str,
    object_type: str,
    initial_scores: dict,
    final_scores: dict,
    steps_applied: list[str],
    step_records: list[dict],
    final_jpeg: str | None,
    meta: dict,
) -> str | None:
    """
    Generate a critical evaluation of the completed auto-process run.

    Assesses what improved, what still needs work, whether more data is needed,
    and what the recommended next processing steps or data collection actions are.

    Returns a plain-text multi-paragraph evaluation, or None if no API key.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    def _fmt_scores(s: dict) -> str:
        return ", ".join(
            f"{k.replace('_', ' ')}={v}/10"
            for k, v in s.items()
            if isinstance(v, (int, float)) and k not in ("input_tokens", "output_tokens")
        )

    steps_summary = []
    for sr in step_records:
        step = sr.get("step", "")
        winner = sr.get("winner")
        reasoning = sr.get("reasoning", "")
        sb = sr.get("scores_before", {})
        sa = sr.get("scores_after", {})
        line = f"  • {step}"
        if winner:
            line += f" → winner: {winner}"
        if sb and sa:
            deltas = [
                f"{k}: {sb.get(k,'?')}→{sa.get(k,'?')}"
                for k in set(list(sb.keys()) + list(sa.keys()))
                if isinstance(sb.get(k), (int, float)) or isinstance(sa.get(k), (int, float))
            ]
            if deltas:
                line += f" ({', '.join(deltas)})"
        if reasoning:
            line += f"\n    Reasoning: {reasoning[:900]}"
        steps_summary.append(line)

    prompt = (
        f"You ran an automated astrophotography processing pipeline on {target} "
        f"({object_type.replace('_', ' ')}) using the '{workflow}' workflow.\n\n"
        f"Stack info: {meta.get('frame_count', '?')} frames, "
        f"{meta.get('total_hours', '?')}h integration, filter={meta.get('filter', 'IRCUT')}\n\n"
        f"Initial scores: {_fmt_scores(initial_scores) or 'not assessed'}\n"
        f"Final scores:   {_fmt_scores(final_scores) or 'not assessed'}\n\n"
        f"Steps applied:\n" + ("\n".join(steps_summary) or "  (none)") + "\n\n"
        "Write a critical evaluation covering:\n"
        "1. What the pipeline achieved — where did scores improve and why?\n"
        "2. What still needs work — which dimensions are still weak and what would help?\n"
        "3. Is more integration data needed, or would better processing fix the remaining issues?\n"
        "4. Specific recommended next actions (re-run with different workflow, collect more data, "
        "try PI/BXT/NXT, adjust a specific step, etc.)\n\n"
        "Be direct and specific. 3-5 short paragraphs. No headers. Plain text."
    )

    content: list[dict] = [{"type": "text", "text": prompt}]
    if final_jpeg and Path(final_jpeg).exists():
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": _b64(final_jpeg),
        }})
        content.append({"type": "text", "text": "The image above is the final processed result."})

    try:
        resp = _messages_create(
            label="generate_critical_eval",
            model=MODEL_EVAL,  # Haiku — narrative prose for the devlog, not a grade
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        text = resp.content[0].text.strip()
        log.info(f"[claude] critical eval for {target}: "
                 f"{resp.usage.input_tokens}+{resp.usage.output_tokens} tok")
        return text
    except Exception as e:
        log.error(f"[claude] generate_critical_eval failed: {e}")
        return None


def write_story_entry(target: str, data: dict) -> str | None:
    """
    Write a 2-4 sentence first-person journal paragraph for one target.
    data keys: first_date, last_date, session_count, total_subs, total_hours,
               object_type, processing_steps, latest_scores, pipeline_notes.
    Returns prose string or None if no API key.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    obj_type = (data.get("object_type") or "deep sky object").replace("_", " ")
    first = data.get("first_date", "")[:10]
    last = data.get("last_date", "")[:10]
    sessions = data.get("session_count") or 1
    subs = data.get("total_subs") or 0
    hours = round(data.get("total_hours") or 0, 1)
    steps = [s.get("step", "") for s in (data.get("processing_steps") or [])]
    scores = data.get("latest_scores") or {}
    overall = scores.get("overall", "")
    notes = data.get("pipeline_notes") or ""

    first_light = first == "2024-03-18" or (first and first <= "2024-04-01")
    used_ai = any("auto_process" in (s.get("engine") or "") for s in (data.get("processing_steps") or []))

    context_lines = [
        f"Telescope: Seestar S50 (50mm f/5 smart telescope, 10-second subs)",
        f"Target: {target} ({obj_type})",
        f"First captured: {first}" + (" — first weeks of the journey" if first_light else ""),
        f"Last session: {last}",
        f"Sessions: {sessions}, total subs: {subs:,}, integration: {hours}h",
    ]
    if steps:
        context_lines.append(f"Processing pipeline: {' → '.join(steps[:8])}")
    if overall:
        context_lines.append(f"Final quality score: {overall}/10")
    if used_ai:
        context_lines.append("Processed using Claude AI-driven auto_process pipeline")
    if notes:
        context_lines.append(f"Notes: {notes}")

    prompt = (
        "You are writing a personal astrophotography journal for Henry, who started "
        "this hobby on March 18, 2024 with a Seestar S50 smart telescope. "
        "Write a warm, first-person journal entry of 2-4 sentences for this target. "
        "Focus on the experience — what's interesting about this object, what the "
        "capture/processing journey was like, any personal milestones. "
        "Be specific, not generic. Don't start with 'I'.\n\n"
        "Target data:\n" + "\n".join(context_lines) + "\n\n"
        "Return ONLY the journal paragraph text, no quotes or labels."
    )

    try:
        response = _messages_create(
            label="write_story_entry",
            model="claude-haiku-4-5-20251001",  # prose generation, no reasoning needed
            max_tokens=256,
            system=("You write warm, specific, first-person astrophotography journal entries. "
                    "2-4 sentences max. No fluff. Return only the paragraph text."),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        log.info(f"[claude] story entry for {target}: {len(text)} chars "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tokens)")
        return text
    except Exception as e:
        log.error(f"[claude] write_story_entry failed for {target}: {e}")
        return None


def get_stretch_priors(object_type: str) -> dict:
    """
    Query claude_assessments for past stretch winners for similar object types.
    Returns {"recommended": name|None, "sample_count": int}.
    """
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT ca.recommendation
                FROM claude_assessments ca
                JOIN targets t ON t.target = ca.target
                WHERE ca.phase = 'stretch' AND t.type = ? AND ca.recommendation IS NOT NULL
                ORDER BY ca.created_at DESC LIMIT 20
            """, (object_type,)).fetchall()

        if not rows:
            return {"recommended": None, "sample_count": 0}

        counts: dict[str, int] = {}
        for row in rows:
            try:
                rec = json.loads(row[0])
                winner = rec.get("winner")
                if winner:
                    counts[winner] = counts.get(winner, 0) + 1
            except Exception:
                pass

        if not counts:
            return {"recommended": None, "sample_count": len(rows)}

        best = max(counts, key=lambda k: counts[k])
        return {"recommended": best, "sample_count": len(rows)}
    except Exception as e:
        log.debug(f"[claude] get_stretch_priors error: {e}")
        return {"recommended": None, "sample_count": 0}


_CROP_SCORE_DIMS = {
    "sharpness":         "detail/resolution quality (10=crisp stars and fine structure, 1=soft/blurry)",
    "noise":             "noise level (10=clean grain-free, 1=severe noise/grain)",
    "naturalness":       "perceptual realism (10=looks natural and well-balanced, 1=overprocessed/artificial)",
    "artifact_level":    "freedom from artifacts — halos, ringing, posterization (10=none visible, 1=severe)",
    "background_quality":"sky flatness and darkness (10=uniform dark sky, 1=bright gradient or vignette)",
    "star_quality":      "star shape and size (10=tight round stars, 1=bloated, elongated, or haloed stars)",
}

_CROP_WEIGHTS = {
    "galaxy":   {"sharpness": 1.5, "noise": 1.0, "naturalness": 1.0, "artifact_level": 1.5, "background_quality": 0.8, "star_quality": 0.7},
    "nebula":   {"sharpness": 1.0, "noise": 1.2, "naturalness": 1.5, "artifact_level": 1.0, "background_quality": 0.8, "star_quality": 0.5},
    "star_field":{"sharpness":1.2, "noise": 1.0, "naturalness": 0.8, "artifact_level": 1.2, "background_quality": 0.8, "star_quality": 2.0},
    "background":{"sharpness":0.5, "noise": 1.5, "naturalness": 1.0, "artifact_level": 0.8, "background_quality": 2.0, "star_quality": 0.2},
    "default":  {"sharpness": 1.0, "noise": 1.0, "naturalness": 1.0, "artifact_level": 1.0, "background_quality": 1.0, "star_quality": 1.0},
}


def analyze_crop_structured(
    image_b64: str,
    crop_name: str = "",
    target: str = "",
    physics: str = "",
    target_type: str = "default",
) -> dict:
    """
    Score a crop JPEG on six quality dimensions plus a text summary.

    Returns:
      {
        "scores": {"sharpness": 8.7, "noise": 7.1, ...},  # each 1.0-10.0
        "aggregate": 8.2,   # weighted composite score
        "summary": "...",   # 1-2 sentence narrative
        "concerns": [...],  # list of specific issues found
      }
    Returns empty dict on failure.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return {}

    context = ""
    if crop_name:
        context = f"This is a '{crop_name}' crop"
    if target:
        context += f" of {target}" if context else f"Crop of {target}"
    if context:
        context += ". "

    dim_list = "\n".join(f'- "{k}": {v}' for k, v in _CROP_SCORE_DIMS.items())
    physics_block = f"\n\nObjective pixel metrics:\n{physics}\nUse these to calibrate your scores." if physics else ""

    prompt = (
        f"{context}This is a stretched astrophotography image crop (JPEG preview).{physics_block}\n\n"
        "Score this crop on the following dimensions (each 1.0–10.0):\n"
        f"{dim_list}\n\n"
        "Also provide a 1–2 sentence summary and a list of specific concerns (empty list if none).\n\n"
        'Respond with ONLY a JSON object in this exact format:\n'
        '{"scores":{"sharpness":X,"noise":X,"naturalness":X,"artifact_level":X,'
        '"background_quality":X,"star_quality":X},"summary":"...","concerns":["..."]}'
    )

    try:
        msg = _messages_create(
            label="analyze_crop_structured",
            model=MODEL,
            max_tokens=512,
            system=("You are an expert astrophotographer evaluating image quality. "
                    "Return only valid JSON — no markdown, no commentary."),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        result = _parse_json(raw)
        scores = result.get("scores", {})
        # Clamp all scores to [1.0, 10.0]
        scores = {k: max(1.0, min(10.0, float(v))) for k, v in scores.items()}
        # Compute weighted aggregate
        weights = _CROP_WEIGHTS.get(target_type, _CROP_WEIGHTS["default"])
        w_total = sum(weights.get(k, 1.0) * scores.get(k, 5.0) for k in _CROP_SCORE_DIMS)
        w_max = sum(10.0 * weights.get(k, 1.0) for k in _CROP_SCORE_DIMS)
        aggregate = round(10.0 * w_total / w_max, 2) if w_max else 5.0
        return {
            "scores": scores,
            "aggregate": aggregate,
            "summary": result.get("summary", ""),
            "concerns": result.get("concerns", []),
        }
    except Exception as e:
        log.warning(f"[claude] analyze_crop_structured failed: {e}")
        return {}


def analyze_crop(image_b64: str, question: str, crop_name: str = "",
                 target: str = "", physics: str = "") -> str:
    """
    Send a crop JPEG to Claude and get a plain-text analysis answer.

    physics: optional string of objective pixel metrics (bg_rms, SNR, FWHM, etc.)
             formatted by crop_analysis.format_physics() — grounded Claude's answer.
    """
    context = ""
    if crop_name:
        context += f"This is a crop of the '{crop_name}' region"
    if target:
        context += f" from {target}" if context else f"From {target}"
    if context:
        context = context.rstrip() + ". "

    physics_block = ""
    if physics:
        physics_block = (
            f"\n\nObjective pixel measurements for this crop:\n{physics}\n"
            f"Use these numbers to calibrate your qualitative assessment."
        )

    prompt = (
        f"{context}This is a stretched astrophotography image crop (JPEG preview). "
        f"Please answer the following question about it:\n\n{question}"
        f"{physics_block}"
    )
    msg = _messages_create(
        label="analyze_crop",
        model=MODEL,
        max_tokens=800,
        system=("You are an expert astrophotographer. "
                "Give clear, specific, technical answers about image quality. "
                "Be concise — 2–5 sentences unless more detail is clearly needed."),
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return msg.content[0].text.strip()


# ---------------------------------------------------------------------------
# Adaptive workflow planning
# ---------------------------------------------------------------------------

MODEL_PLANNER_LINEAR = "claude-sonnet-4-6"    # physics confirmation + flag
MODEL_PLANNER_NONLINEAR = "claude-opus-4-8"   # optional step selection — Opus 4.8 at $5/$25 is only 1.67x Sonnet
MODEL_EVAL = "claude-haiku-4-5-20251001"     # end-of-run critical eval — narrative prose, not grading; Haiku (~$0.005/run vs ~$0.038 Opus)


def _fmt_adaptive_history(history: list[dict]) -> str:
    """Format adaptive decision history for injection into Claude prompts.

    User comments (decision_type='user_comment') are surfaced first and highlighted
    because they represent direct human aesthetic feedback.
    """
    if not history:
        return "No history yet for this target."

    lines = []

    # ── User comments (high-priority aesthetic feedback) ──────────────────
    comments = [d for d in history if d.get("decision_type") == "user_comment"]
    if comments:
        lines.append("USER AESTHETIC FEEDBACK (prioritise these):")
        for c in comments[:5]:
            ts = (c.get("timestamp") or "")[:10]
            lines.append(f"  [{ts}] \"{c.get('rationale', '').strip()}\"")
        lines.append("")

    # ── Adaptive planning history (planning decisions + outcomes) ─────────
    planning = [d for d in history if d.get("decision_type") != "user_comment"]
    if planning:
        seen_runs: dict[str, list[dict]] = {}
        for d in planning:
            rid = d.get("run_id", "unknown")
            seen_runs.setdefault(rid, []).append(d)
        for rid, entries in list(seen_runs.items())[:5]:  # show last 5 runs
            ts = entries[0].get("timestamp", "")[:10]
            tgt = entries[0].get("target_name", "")
            outcome = next((e.get("score_delta") for e in entries
                            if e.get("score_delta") is not None), None)
            outcome_str = f" → Δscore={outcome:+.1f}" if outcome is not None else ""
            lines.append(f"Run {ts} ({tgt}){outcome_str}:")
            for e in entries:
                dt = e.get("decision_type", "")
                sn = e.get("step_name") or "—"
                val = e.get("chosen_value", "")
                rat = (e.get("rationale") or "")[:120]
                lines.append(f"  [{e.get('phase','?')}] {dt} {sn}={val}  {rat}")

    return "\n".join(lines) if lines else "No history yet for this target."


def plan_linear_phase(
    target: str,
    jpeg_path: str,
    object_type: str,
    folio: dict | None,
    tool_params_dict: dict,
    available_variant_ids: dict,
    force_variant_overrides: dict,
    prior_decisions: list[dict],
    capture_filter: str = "IRCUT (broadband)",
) -> dict | None:
    """
    Phase 1 adaptive planner: examine the initial image and plan the LINEAR
    processing phase (pre-stretch: BGE, deconvolution, denoise).

    Physics is the authority for linear steps — Claude fills gaps in force_variants
    and may suggest gentle param nudges, but cannot increase aggressiveness beyond
    what tool_params computed.

    Returns dict with keys: variant_fills, param_nudges, flags, rationale, confidence
    or None if no API key / error.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    folio_block = _folio_prompt_block(target, folio, mode="step")

    # Format tool_params suggestions compactly
    tp_lines = []
    if tool_params_dict:
        for step_nm, params in tool_params_dict.items():
            if isinstance(params, dict):
                tp_lines.append(f"  {step_nm}: " +
                                ", ".join(f"{k}={v}" for k, v in params.items()))
    tp_text = ("\n".join(tp_lines) if tp_lines
               else "  (no tool_params computed — judge from image)")

    # Format available variants
    avail_lines = []
    for step_nm, vids in available_variant_ids.items():
        avail_lines.append(f"  {step_nm}: {', '.join(vids) if vids else '(none)'}")
    avail_text = "\n".join(avail_lines) or "  (none available)"

    # Format locked force_variants
    locked_text = (", ".join(f"{k}={v}" for k, v in force_variant_overrides.items())
                   or "(none — all steps are unlocked)")

    history_text = _fmt_adaptive_history(prior_decisions)

    _is_dualband = "dual-band" in capture_filter.lower() or "dualband" in capture_filter.lower()
    filter_guidance = ""
    if _is_dualband:
        filter_guidance = (
            "\nDUAL-BAND FILTER NOTE: This was shot through the SeeStar LP dual-band "
            "filter (Hα ~656nm + OIII ~500nm) — it is effectively dual-narrowband, NOT "
            "broadband. Do NOT describe this as a 'broadband OSC' capture or flag the "
            "'absence of a narrowband filter': the narrowband signal is already present. "
            "The muted pink-red (Hα) and any teal (OIII) are REAL emission, not a "
            "light-pollution cast. A grey-green sky background is still LP and SCNR is fine "
            "later, but do not erode the OIII teal. Plan BGE/decon/denoise on the assumption "
            "the colour carries genuine narrowband signal.\n"
        )

    prompt = f"""You are planning the LINEAR processing phase for an astrophoto of {target}.
Object type: {object_type.replace('_', ' ')}
Capture filter: {capture_filter}
{filter_guidance}{folio_block}
CALIBRATED FORCE VARIANTS (locked — do not override, only suggest param nudges):
  {locked_text}

AVAILABLE VARIANTS TO SUGGEST (only for steps NOT in the locked list above):
{avail_text}

PHYSICS-COMPUTED PARAMETER SUGGESTIONS (ceiling for linear steps — be at or below):
{tp_text}

RECENT ADAPTIVE HISTORY FOR THIS TARGET:
{history_text}

Examine the initial image. Plan the linear (pre-stretch) phase conservatively.
For locked force_variants: you may only suggest gentle param nudges within physics bounds.
For unlocked steps: choose the best variant based on what you see.

Return ONLY this JSON, nothing else:
{{
  "variant_fills": {{"step_name": "variant_id"}},
  "param_nudges": {{"step_name": {{"param": value}}}},
  "flags": ["concern1", "concern2"],
  "rationale": "one short paragraph",
  "confidence": <float 1.0-10.0>
}}"""

    try:
        response = _messages_create(
            label="plan_linear_phase",
            model=MODEL_PLANNER_LINEAR,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": _b64(jpeg_path),
                }},
            ]}],
        )
        raw = response.content[0].text
        result = _parse_json(raw)
        result.setdefault("variant_fills", {})
        result.setdefault("param_nudges", {})
        result.setdefault("flags", [])
        result.setdefault("rationale", "")
        result.setdefault("confidence", 5.0)
        log.info(f"[claude] {target} adaptive-linear: fills={list(result['variant_fills'].keys())} "
                 f"nudges={list(result['param_nudges'].keys())} "
                 f"flags={len(result['flags'])} confidence={result['confidence']} "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tok)")
        return result
    except Exception as e:
        log.error(f"[claude] plan_linear_phase failed for {target}: {e}")
        return None


def plan_nonlinear_phase(
    target: str,
    jpeg_path: str,
    object_type: str,
    folio: dict | None,
    pre_stretch_stats: dict | None,
    current_scores: dict,
    optional_steps_catalog: list[str],
    force_variants: dict,
    prior_decisions: list[dict],
    capture_filter: str = "IRCUT (broadband)",
) -> dict | None:
    """
    Phase 2 adaptive planner: examine the pre-stretch image and plan the NON-LINEAR
    phase (stretch, optional enhancements, curves).

    Claude has full authority for non-linear choices — can add/remove optional steps
    and override force_variants for post-stretch steps.

    Uses Opus 4.7 (higher-stakes creative decisions).

    Returns dict with keys: add_steps, skip_steps, variant_overrides, param_overrides,
    rationale, confidence — or None if no API key / error.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None

    folio_block = _folio_prompt_block(target, folio, mode="step")

    # Format pre-stretch stats
    ps_lines = []
    if pre_stretch_stats:
        _nb = pre_stretch_stats.get("noise", {})
        _bb = pre_stretch_stats.get("background", {})
        _pb = pre_stretch_stats.get("psf", {})
        _cb = pre_stretch_stats.get("color", {})
        ps_lines = [
            f"  SNR: {_nb.get('snr', 0):.1f}",
            f"  FWHM: {_pb.get('fwhm_median', 0):.2f}px",
            f"  Gradient severity: {_bb.get('gradient_severity', 0):.3f} (0=flat, 1=severe)",
            f"  Sky background: {_bb.get('sky_background', 0):.4f}",
            f"  Green excess: {_cb.get('green_excess', 0):.5f}",
        ]
    ps_text = "\n".join(ps_lines) or "  (no stats computed)"

    # Format current scores
    scores_text = ", ".join(
        f"{k}={v:.1f}" for k, v in current_scores.items()
        if isinstance(v, (int, float)) and not k.startswith("_")
    ) or "(not assessed)"

    # Format optional steps catalog with guidance
    _OPTIONAL_DESCRIPTIONS = {
        "scnr":           "Green cast removal. Use when green_excess > 0.003.",
        "clahe":          "Local contrast enhancement. Good for emission nebulae and large galaxies.",
        "color_sat":      "Saturation boost. CONFIRMED HARMFUL for globulars (green cast). Use cautiously for others.",
        "hdr_compression":"Core/halo recovery for bright objects. Use when core appears blown or flat.",
        "dark_enhance":   "Lift faint outer structure. Use when target has extensive faint halo or nebulosity.",
    }
    if optional_steps_catalog:
        opt_lines = [
            f"  {s}: {_OPTIONAL_DESCRIPTIONS.get(s, s)}"
            for s in optional_steps_catalog
        ]
        opt_text = "\n".join(opt_lines)
    else:
        opt_text = "  (no optional steps configured for this workflow)"

    # Format current force_variants for non-linear steps
    nl_fv_keys = {"stretch", "scnr", "curves", "clahe", "color_sat",
                  "hdr_compression", "dark_enhance"}
    nl_fv = {k: v for k, v in force_variants.items() if k in nl_fv_keys}
    fv_text = (", ".join(f"{k}={v}" for k, v in nl_fv.items())
               or "(none — you choose all variants)")

    history_text = _fmt_adaptive_history(prior_decisions)

    _is_dualband = "dual-band" in capture_filter.lower() or "dualband" in capture_filter.lower()
    filter_guidance = ""
    if _is_dualband:
        filter_guidance = (
            "\nDUAL-BAND FILTER NOTE: This was shot through the SeeStar LP dual-band "
            "filter (Hα ~656nm + OIII ~500nm). The red (Hα) and teal (OIII) colour is "
            "REAL emission signal, NOT a broadband cast or noise. Therefore: a saturation "
            "boost (color_sat) ENHANCES genuine nebula colour here — do not skip it on the "
            "assumption the colour is weak/noisy. Keep SCNR gentle: aggressive green removal "
            "can erode legitimate OIII teal. Do NOT recommend 'switch to a narrowband filter' "
            "— this already is one.\n"
        )

    prompt = f"""You are planning the NON-LINEAR processing phase for {target}.
Object type: {object_type.replace('_', ' ')}
Capture filter: {capture_filter}
{filter_guidance}{folio_block}
PRE-STRETCH IMAGE STATS (physics-measured):
{ps_text}

CURRENT QUALITY SCORES: {scores_text}

PLANNED CORE STEPS (will always run): stretch, curves, assess_final.

OPTIONAL STEPS AVAILABLE (you choose which to add):
{opt_text}

CURRENT NON-LINEAR FORCE VARIANTS (you may override these):
  {fv_text}

RECENT ADAPTIVE HISTORY (including reverted steps — do NOT repeat mistakes):
{history_text}

Examine the pre-stretch image carefully. Choose which optional steps to add based on
what the image actually needs. Be conservative — adding a step that makes things worse
will be reverted and logged. Do NOT add steps that were previously reverted for this
target or object type.

You have full authority over non-linear choices.

Return ONLY this JSON, nothing else:
{{
  "add_steps": ["step_name"],
  "skip_steps": ["step_name"],
  "variant_overrides": {{"step_name": "variant_id"}},
  "param_overrides": {{"step_name": {{"param": value}}}},
  "rationale": "one short paragraph explaining your choices",
  "confidence": <float 1.0-10.0>
}}"""

    try:
        response = _messages_create(
            label="plan_nonlinear_phase",
            model=MODEL_PLANNER_NONLINEAR,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": _b64(jpeg_path),
                }},
            ]}],
        )
        raw = response.content[0].text
        result = _parse_json(raw)
        result.setdefault("add_steps", [])
        result.setdefault("skip_steps", [])
        result.setdefault("variant_overrides", {})
        result.setdefault("param_overrides", {})
        result.setdefault("rationale", "")
        result.setdefault("confidence", 5.0)
        log.info(f"[claude] {target} adaptive-nonlinear (opus): "
                 f"add={result['add_steps']} skip={result['skip_steps']} "
                 f"overrides={list(result['variant_overrides'].keys())} "
                 f"confidence={result['confidence']} "
                 f"({response.usage.input_tokens}+{response.usage.output_tokens} tok)")
        return result
    except Exception as e:
        log.error(f"[claude] plan_nonlinear_phase failed for {target}: {e}")
        return None


def stretch_vision_tiebreak(jpg_a: str, name_a: str, jpg_b: str, name_b: str,
                            object_type: str = "",
                            folio_band: tuple | None = None) -> dict | None:
    """Cheap Haiku vision tiebreak between two close stretch candidates.

    Used ONLY when the physics picker can't separate the top two nebula variants
    (same sky + exposure bucket — grain alone decided), which is exactly the
    aesthetic call physics is weakest at. Returns {"winner": name_a|name_b,
    "reason": str} or None on any failure / physics-only mode (caller then keeps
    the physics pick). Haiku + 2 small JPEGs ≈ $0.005 / a few seconds — only fires
    on close calls, so hot-path cost is bounded. NOT a re-score of every step;
    this is a single bounded visual decision, per the physics-default architecture.
    """
    band_hint = ""
    if folio_band:
        band_hint = (f" This target's sky background should sit in "
                     f"[{folio_band[0]:.2f}, {folio_band[1]:.2f}] (normalised).")
    sys_prompt = (
        "You are judging two candidate stretches of the SAME astrophotograph "
        f"(object type: {object_type or 'deep-sky'}). Pick the better one on these "
        "criteria, in order: (1) sky background neither crushed to black nor washed "
        "grey; (2) faint nebulosity / outer structure preserved, not lost into the "
        "sky; (3) low background grain/noise; (4) bright cores not blown to flat "
        "white." + band_hint +
        " Image A is the first image, Image B is the second. Reply ONLY with JSON: "
        '{"winner": "A" or "B", "reason": "<one short clause>"}.')
    try:
        resp = _messages_create(
            max_tokens=120, model=MODEL_EVAL, label="stretch_tiebreak",
            system=sys_prompt,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Image A:"},
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": _b64(jpg_a)}},
                {"type": "text", "text": "Image B:"},
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": _b64(jpg_b)}},
            ]}])
        out = _parse_json(resp.content[0].text)
        pick = str(out.get("winner", "")).strip().upper()
        if pick not in ("A", "B"):
            return None
        return {"winner": name_a if pick == "A" else name_b,
                "reason": str(out.get("reason", ""))[:160]}
    except PhysicsOnlyMode:
        return None
    except Exception as e:
        log.warning(f"[claude] stretch_vision_tiebreak failed: {e}")
        return None

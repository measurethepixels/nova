"""
Experiment mode: run multiple processing variants, assess each with Claude,
select the winner, and persist results for statistical learning over time.

Usage:
    result = run_experiment(target, "background_extraction", input_fits_path)
    result["winner"]         # winning variant id
    result["output_path"]    # FITS path of the winning output
    result["learning_note"]  # what historical priors influenced the choice
"""
import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_active: dict[str, dict] = {}


def _sample_fits_background(fits_path: Path) -> float:
    """
    Return the median background level of a FITS image by averaging the median
    of four 64×64 corner regions (mirrors the JS sampleBackground() helper).
    Raises on read error — callers should catch.
    """
    import numpy as _np
    from astropy.io import fits as _fits
    with _fits.open(str(fits_path)) as hdul:
        data = hdul[0].data.astype(float)
    if data.ndim == 3:
        data = _np.mean(data, axis=0)
    h, w = data.shape
    sz = min(64, max(16, min(h, w) // 8))
    corners = [
        _np.median(data[:sz, :sz]),
        _np.median(data[:sz, w - sz:]),
        _np.median(data[h - sz:, :sz]),
        _np.median(data[h - sz:, w - sz:]),
    ]
    corners_s = sorted(corners)
    return float((corners_s[1] + corners_s[2]) / 2.0)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))
_active_lock = threading.Lock()

_ONTOLOGY_PATH = Path(__file__).parent / "processing_ontology.json"


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def get_experiment_status(target: str) -> dict | None:
    with _active_lock:
        s = _active.get(target)
        return dict(s) if s else None


def get_all_experiment_statuses() -> list[dict]:
    with _active_lock:
        return [{"target": t, **v} for t, v in _active.items()]


def _set_status(target: str, **kw):
    with _active_lock:
        _active.setdefault(target, {}).update(kw)


# ---------------------------------------------------------------------------
# Preview helper (shared with auto_process)
# ---------------------------------------------------------------------------

def _generate_preview(fits_path: Path, jpg_path: Path, linked: bool = True) -> bool:
    """Generate JPEG preview using PI STF algorithm. `linked` param kept for call-site compat."""
    from nas_server import seti_astro
    return seti_astro.generate_preview_stf(fits_path, jpg_path)


def _generate_linear_composite(fits_path: Path, jpg_path: Path) -> bool:
    """
    Side-by-side composite for linear step evaluation:
      Left  — normal STF (target_bg=0.30, matches PI STF display)
      Right — deep stretch (target_bg=0.85, reveals hidden gradients and faint structure)

    Helps Claude judge whether background extraction, color calibration, deconvolution, etc.
    fully resolved faint features that would be invisible at normal stretch.
    """
    try:
        import tempfile, numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from nas_server import seti_astro

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_normal = f.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_deep = f.name

        ok_n = seti_astro.generate_preview_stf(fits_path, tmp_normal, target_bg=0.30)
        ok_d = seti_astro.generate_preview_stf(fits_path, tmp_deep,
                                               target_bg=0.85, shadow_clip_k=1.25)

        if not ok_n:
            return False
        if not ok_d:
            # Fall back to single normal preview
            import shutil; shutil.copy(tmp_normal, str(jpg_path))
            return True

        img_n = Image.open(tmp_normal).convert("RGB")
        img_d = Image.open(tmp_deep).convert("RGB")

        # Gamma lift to push midtones brighter (reveal faint nebulosity / background structure)
        import numpy as _np
        _arr = _np.array(img_d, dtype=_np.float32) / 255.0
        _arr = _np.power(_arr, 0.55)
        img_d = Image.fromarray((_arr * 255.0).clip(0, 255).astype(_np.uint8))

        # Resize deep to match normal height
        if img_n.height != img_d.height:
            img_d = img_d.resize(img_n.size, Image.LANCZOS)

        label_h = 22
        composite = Image.new("RGB", (img_n.width + img_d.width, img_n.height + label_h), (20, 20, 20))
        composite.paste(img_n, (0, label_h))
        composite.paste(img_d, (img_n.width, label_h))

        draw = ImageDraw.Draw(composite)
        draw.text((4, 3),             "Normal stretch",     fill=(200, 200, 200))
        draw.text((img_n.width + 4, 3), "Aggressive stretch", fill=(200, 200, 200))

        composite.save(str(jpg_path), quality=88)

        import os; os.unlink(tmp_normal); os.unlink(tmp_deep)
        return True
    except Exception as e:
        log.warning(f"[experiment] linear composite failed for {fits_path}: {e}")
        return _generate_preview(fits_path, jpg_path)


# ---------------------------------------------------------------------------
# Variant execution
# ---------------------------------------------------------------------------

def _run_variant(variant: dict, input_fits: Path, output_fits: Path) -> dict:
    """
    Execute one experiment variant.  Returns {"ok": bool, "elapsed": float, "error": str|None}.
    Supports engines: seti_astro, pixinsight, none.
    """
    engine = variant.get("engine", "seti_astro")
    fn_name = variant.get("fn")
    params = dict(variant.get("params", {}))
    start = time.time()

    if engine == "none" or fn_name is None:
        shutil.copy2(str(input_fits), str(output_fits))
        return {"ok": True, "elapsed": 0.0, "error": None}

    if engine == "seti_astro":
        from nas_server import seti_astro
        fn = getattr(seti_astro, fn_name, None)
        if fn is None:
            return {"ok": False, "elapsed": 0.0, "error": f"seti_astro.{fn_name} not found"}
        try:
            result = fn(input_fits, output_fits, **params)
            ok = result.get("ok", False) and output_fits.exists()
            if ok:
                lm = variant.get("lum_mask")
                if lm:
                    try:
                        _lum_mask_blend(input_fits, output_fits, lm)
                        log.info(f"[experiment] lum mask blend applied to {fn_name}")
                    except Exception as be:
                        log.warning(f"[experiment] lum mask blend failed for {fn_name}: {be}")
            return {"ok": ok, "elapsed": time.time() - start,
                    "error": result.get("error") if not ok else None}
        except Exception as e:
            return {"ok": False, "elapsed": time.time() - start, "error": str(e)}

    if engine == "pixinsight":
        try:
            from nas_server.pixinsight import run_postprocess
            # Scale the PI timeout by image megapixels — NXT/BXT (TensorFlow on CPU)
            # on large (≥30 MP) frames routinely exceed a flat 600s and get killed
            # mid-run. Mirror the StarXT formula (auto_process.py): 900s baseline +
            # 30s/MP above 10 MP. Cheap PI tools finish well under this ceiling.
            try:
                from astropy.io.fits import getheader as _gethdr
                _vh = _gethdr(str(input_fits), memmap=False)
                _vmp = (_vh.get("NAXIS1", 3000) * _vh.get("NAXIS2", 3000)) / 1_000_000
            except Exception:
                _vmp = 10.0
            _pi_timeout = int(max(600, 900 + 30 * max(0, _vmp - 10)))
            # All PI steps off by default — only enable the ones being tested
            pi_kwargs: dict = {
                "dbe": False, "gradient_correction": False,
                "color_calibration": False, "bgn": False, "spcc": False,
                "mlt": False, "tgv": False,
                "bxt": False, "nxt": False, "starxt": False,
                "ht": False, "mas": False,
                "scnr": False, "hdrmt": False, "lhe": False,
                "color_sat": False, "curves": False,
                "ihdr": False,
                "cms": False,  # never apply CorrectMagentaStars to linear data
            }
            if fn_name == "dbe":
                pi_kwargs["dbe"] = True
                pi_kwargs["dbe_correction"] = params.get("dbe_correction", "subtraction")
            elif fn_name == "gradient_correction":
                pi_kwargs["gradient_correction"] = True
            elif fn_name == "color_calibration_cc":
                pi_kwargs["color_calibration"] = True
            elif fn_name == "bgn":
                pi_kwargs["bgn"] = True
            elif fn_name == "color_calibration_spcc":
                pi_kwargs["spcc"] = True
            elif fn_name == "mlt":
                pi_kwargs["mlt"] = True
                pi_kwargs["mlt_sharpen"] = params.get("mlt_sharpen", 0.20)
                pi_kwargs["mlt_denoise"] = params.get("mlt_denoise", 0.50)
            elif fn_name == "tgv":
                pi_kwargs["tgv"] = True
                pi_kwargs["tgv_strength"] = params.get("tgv_strength", 1.0)
            elif fn_name == "bxt":
                pi_kwargs["bxt"] = True
                pi_kwargs["bxt_psf"] = params.get("bxt_psf", 4.0)
                pi_kwargs["bxt_nonstellar"] = params.get("bxt_nonstellar", 0.30)
                pi_kwargs["bxt_stars"] = params.get("bxt_stars", 0.50)
                pi_kwargs["bxt_auto_psf"] = params.get("bxt_auto_psf", False)
                if params.get("bxt_correct_only"):
                    pi_kwargs["bxt_correct_only"] = True
                    pi_kwargs["bxt_stars"] = 0.0
                    pi_kwargs["bxt_nonstellar"] = 0.0
            elif fn_name == "denoise_nxt":
                pi_kwargs["nxt"] = True
                pi_kwargs["nxt_denoise"] = params.get("nxt_denoise", 0.7)
                pi_kwargs["nxt_iterations"] = params.get("nxt_iterations", 2)
                pi_kwargs["_nxt_two_pass"] = params.get("nxt_two_pass", False)
            elif fn_name == "star_removal_starxt":
                pi_kwargs["starxt"] = True
            elif fn_name == "ht":
                pi_kwargs["ht"] = True
                pi_kwargs["ht_target_bg"] = params.get("ht_target_bg", 0.12)
            elif fn_name == "scnr_pi":
                pi_kwargs["scnr"] = True
                pi_kwargs["scnr_amount"] = params.get("scnr_amount", 0.9)
            elif fn_name == "hdrmt":
                pi_kwargs["hdrmt"] = True
                pi_kwargs["hdrmt_layers"] = params.get("hdrmt_layers", 6)
                # Sample pre-HDRMT background so the JS anchor can correct any drift
                try:
                    pi_kwargs["bg_anchor_target"] = _sample_fits_background(input_fits)
                    log.debug(f"[bg_anchor] hdrmt pre-sample: {pi_kwargs['bg_anchor_target']:.4f}")
                except Exception as _bge:
                    log.debug(f"[bg_anchor] hdrmt sample failed: {_bge}")
            elif fn_name == "lhe":
                pi_kwargs["lhe"] = True
                pi_kwargs["lhe_amount"] = params.get("lhe_amount", 0.5)
                # Sample pre-LHE background so the JS anchor can correct any drift
                try:
                    pi_kwargs["bg_anchor_target"] = _sample_fits_background(input_fits)
                    log.debug(f"[bg_anchor] lhe pre-sample: {pi_kwargs['bg_anchor_target']:.4f}")
                except Exception as _bge:
                    log.debug(f"[bg_anchor] lhe sample failed: {_bge}")
            elif fn_name == "color_sat":
                pi_kwargs["color_sat"] = True
                pi_kwargs["color_sat_boost"] = params.get("color_sat_boost", 0.3)
            elif fn_name in ("pi_curves", "curves_pi"):   # "curves_pi" kept for back-compat
                pi_kwargs["curves"] = True
                pi_kwargs["curves_shape"] = params.get("curves_shape", "s_med")
                if params.get("curves_points"):
                    pi_kwargs["curves_points"] = params["curves_points"]
            elif fn_name == "mas":
                pi_kwargs["mas"] = True
                if "mas_noise_threshold" in params:
                    pi_kwargs["mas_noise_threshold"] = params["mas_noise_threshold"]
            elif fn_name == "ihdr":
                pi_kwargs["ihdr"] = True
                pi_kwargs["ihdr_iterations"]    = params.get("ihdr_iterations", 5)
                pi_kwargs["ihdr_preservation"]  = params.get("ihdr_preservation", 5)
                pi_kwargs["ihdr_mask_strength"] = params.get("ihdr_mask_strength", 1.25)

            # Pass luminance mask for this specific step if computed
            lm = variant.get("lum_mask")
            if lm:
                pi_kwargs["lum_masks"] = {fn_name: lm}

            two_pass = pi_kwargs.pop("_nxt_two_pass", False)

            result = run_postprocess(
                target="experiment",
                input_fits=str(input_fits),
                output_path=str(output_fits),   # .fit — astropy-readable for previews
                timeout=_pi_timeout,
                **pi_kwargs,
            )
            pi_ok = result.get("ok", False) and output_fits.exists()

            # NXT two-pass: run NXT a second time on its own output
            if pi_ok and two_pass:
                import tempfile as _tf
                _tmp = output_fits.with_suffix(".pass1.fit")
                shutil.copy2(str(output_fits), str(_tmp))
                try:
                    r2 = run_postprocess(
                        target="experiment",
                        input_fits=str(_tmp),
                        output_path=str(output_fits),
                        timeout=_pi_timeout,
                        **pi_kwargs,
                    )
                    if not (r2.get("ok", False) and output_fits.exists()):
                        shutil.copy2(str(_tmp), str(output_fits))
                        log.warning("[experiment] NXT second pass failed — keeping first pass")
                except Exception as _e2:
                    shutil.copy2(str(_tmp), str(output_fits))
                    log.warning(f"[experiment] NXT second pass error: {_e2}")
                finally:
                    _tmp.unlink(missing_ok=True)
            # For color calibration variants, a silent tool failure inside PI
            # still produces an output file — treat as variant failure so the
            # experiment runner falls back to the next option (pi_cc → none)
            if fn_name == "color_calibration_spcc" and result.get("spcc_failed"):
                pi_ok = False
                log.warning("[experiment] SPCC failed (no WCS or catalog miss) — falling back")
            elif fn_name == "color_calibration_cc" and result.get("cc_failed"):
                pi_ok = False
                log.warning("[experiment] PI ColorCalibration failed — falling back to none")
            return {"ok": pi_ok, "elapsed": time.time() - start,
                    "error": None if pi_ok else "PI pipeline failed"}
        except Exception as e:
            return {"ok": False, "elapsed": time.time() - start, "error": str(e)}

    return {"ok": False, "elapsed": 0.0, "error": f"Unknown engine: {engine}"}


# ---------------------------------------------------------------------------
# Claude comparison
# ---------------------------------------------------------------------------

def pick_best_variant(
    target: str,
    step: str,
    variants_data: list[dict],
    priors: dict | None = None,
    variant_metrics: dict | None = None,
) -> dict | None:
    """
    Claude compares all variant JPEGs and picks the best one.

    variants_data: [{id, jpeg_path, description, params}, ...]
    priors: output of get_experiment_priors() — injected into prompt as historical context
    variant_metrics: {variant_id: metrics_dict} — physics table prepended to prompt when provided

    Returns {"winner": variant_id, "scores": {id: 1-10, ...},
             "reasoning": str, "input_tokens": int, "output_tokens": int}
    or None if no API key or < 2 valid variants.
    """
    from nas_server.config import settings
    if not settings.get("anthropic_api_key"):
        return None
    valid = [v for v in variants_data if v.get("jpeg_path") and Path(v["jpeg_path"]).exists()]
    if len(valid) < 2:
        return None

    import base64
    from nas_server.claude_client import _client, SYSTEM_PROMPT

    content = []

    prior_note = ""
    if priors and priors.get("sample_count", 0) >= 3:
        top = priors.get("top_variant")
        wr = priors.get("variant_win_rate", {})
        prior_note = (
            f"\n\nHistorical learning from {priors['sample_count']} past experiments "
            f"on similar images — win rates: "
            + ", ".join(f"{k}: {int(v*100)}%" for k, v in sorted(wr.items(), key=lambda x: -x[1]))
            + (f"\nPreviously {top} has been preferred ({int(wr.get(top,0)*100)}% win rate). "
               f"Consider this but trust your visual assessment." if top else "")
        )

    physics_note = ""
    if variant_metrics:
        rows = []
        for v in valid:
            m = variant_metrics.get(v["id"], {})
            row = f"| {v['id']:<24}"
            row += f" | {m.get('bg_sigma_ratio') or 'n/a':>10}"
            fwhm_d = m.get("fwhm_delta_pct")
            row += f" | {(f'{fwhm_d:+.1f}%') if fwhm_d is not None else 'n/a':>9}"
            snr_a = m.get("snr_after")
            row += f" | {f'{snr_a:.1f}' if snr_a is not None else 'n/a':>9} |"
            rows.append(row)
        physics_note = (
            "\n\nPhysics assessment (auto-failed variants already excluded):\n"
            "| Variant                   | bg_σ_ratio | FWHM Δ%   | SNR_after |\n"
            "|---------------------------|------------|-----------|----------|\n"
            + "\n".join(rows)
        )

    content.append({"type": "text", "text": (
        f"Compare these {len(valid)} variants of the '{step}' processing step "
        f"applied to an astrophotography image of {target}.\n"
        f"Pick the variant that best improves image quality for its intended purpose.\n"
        f"Pay attention to: background flatness, gradient removal, detail preservation, "
        f"noise level, and natural appearance.{prior_note}{physics_note}\n\n"
    )})

    for v in valid:
        content.append({"type": "text", "text": f"[{v['id']}] {v.get('description', v['id'])}"})
        with open(v["jpeg_path"], "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64,
        }})

    ids = [v["id"] for v in valid]
    score_schema = ", ".join(f'"{i}": <1-10>' for i in ids)
    content.append({"type": "text", "text": (
        f"Return ONLY this JSON, nothing else:\n"
        f'{{"winner": "<one of {ids}>", '
        f'"scores": {{{score_schema}}}, '
        f'"reasoning": "<one sentence explaining the choice>"}}'
    )})

    try:
        client = _client()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text
        result = json.loads(raw)
        result["input_tokens"] = resp.usage.input_tokens
        result["output_tokens"] = resp.usage.output_tokens
        log.info(f"[experiment] {target}/{step} winner: {result.get('winner')} "
                 f"({resp.usage.input_tokens}+{resp.usage.output_tokens} tok)")
        return result
    except Exception as e:
        log.error(f"[experiment] pick_best_variant failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Learning priors
# ---------------------------------------------------------------------------

def get_learned_defaults(step: str, object_type: str = "unknown") -> dict:
    """
    Query experiment history to derive the best-known variant and parameters.

    Returns {
        "variant": best_variant_id or None,
        "params": {...averaged winning params...},
        "confidence": 0.0-1.0,
        "sample_count": N,
        "win_rates": {...},
    }
    """
    from nas_server.database import get_experiment_priors
    priors = get_experiment_priors(step, object_type)
    if priors.get("sample_count", 0) < 2:
        return {"variant": None, "params": {}, "confidence": 0.0,
                "sample_count": 0, "win_rates": {}}

    top = priors.get("top_variant")
    wins = priors.get("variant_wins", {})
    total = sum(wins.values()) or 1
    confidence = wins.get(top, 0) / total if top else 0.0

    return {
        "variant": top,
        "params": priors.get("top_variant_avg_params", {}),
        "confidence": round(confidence, 2),
        "sample_count": priors["sample_count"],
        "win_rates": priors.get("variant_win_rate", {}),
    }


# ---------------------------------------------------------------------------
# Stats-driven parameter adaptation
# ---------------------------------------------------------------------------

def _adapt_deconv_variants(variants: list[dict], input_fits: Path,
                            object_type: str = "unknown") -> list[dict]:
    """Override BXT params with values derived from measured PSF and image stats."""
    import copy
    try:
        from nas_server.image_analyzer import analyze
        from nas_server.tool_params import compute_bxt
        stats = analyze(str(input_fits))
        bxt = compute_bxt(stats, object_type)
        psf_diam   = bxt["bxt_psf"]
        stars_base = bxt["bxt_stars"]
        ns_base    = bxt["bxt_nonstellar"]
        log.info(
            f"[experiment] deconv adapt: psf={psf_diam}, stars={stars_base:.2f}, "
            f"nonstellar={ns_base:.2f}"
        )
    except Exception as e:
        log.warning(f"[experiment] deconv adapt failed, using ontology defaults: {e}")
        return variants

    # Determine ontology ratios from standard variant so conservative/aggressive
    # keep their relative spread even after stats-based calibration.
    std = next((v for v in variants if v.get("fn") == "bxt" and "standard" in v.get("id", "")), None)
    std_stars = std["params"].get("bxt_stars", 0.50) if std else 0.50
    std_ns    = std["params"].get("bxt_nonstellar", 0.30) if std else 0.30

    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        if v.get("fn") == "bxt":
            p = v.setdefault("params", {})
            ratio_stars = p.get("bxt_stars", std_stars) / max(std_stars, 1e-6)
            ratio_ns    = p.get("bxt_nonstellar", std_ns) / max(std_ns, 1e-6)
            p["bxt_psf"]        = psf_diam
            p["bxt_stars"]      = round(_clamp(stars_base * ratio_stars, 0.10, 0.90), 2)
            p["bxt_nonstellar"] = round(_clamp(ns_base * ratio_ns,    0.10, 0.80), 2)
        adapted.append(v)
    return adapted


def _adapt_stretch_variants(variants: list[dict], input_fits: Path,
                             object_type: str = "unknown") -> list[dict]:
    """Override GHS/stat stretch params with values derived from image stats."""
    import copy
    try:
        from nas_server.image_analyzer import analyze
        from nas_server.tool_params import compute_ghs, compute_stat_stretch
        stats = analyze(str(input_fits))
        ghs  = compute_ghs(stats, object_type)
        stat = compute_stat_stretch(stats, object_type)
        log.info(
            f"[experiment] stretch adapt: pivot={ghs['pivot']:.6f} alpha={ghs['alpha']:.2f} "
            f"target_median={stat['target_median']:.3f}"
        )
    except Exception as e:
        log.warning(f"[experiment] stretch adapt failed, using ontology defaults: {e}")
        return variants

    # Find the base GHS variant to compute ratios for other GHS variants
    std_ghs = next((v for v in variants if v.get("fn") == "ghs_stretch"
                    and "galaxy" not in v.get("id", "")), None)
    std_alpha = std_ghs["params"].get("alpha", 5.0) if std_ghs else 5.0

    # When SPCC failed upstream (seti_astro.spcc dropped a `.spcc_failed` sentinel in
    # the run dir), the green cast was never calibrated out. Every ontology stretch
    # variant is linked=True, which preserves that green through the stretch. Switch
    # stat/stf candidates to UNLINKED (per-channel) so each channel's background is
    # neutralised independently — the legitimate escape hatch since there's no SPCC
    # white balance left to protect.
    _spcc_failed = (input_fits.parent / ".spcc_failed").exists()
    if _spcc_failed:
        log.warning("[experiment] stretch: SPCC failed upstream — forcing UNLINKED "
                    "(per-channel) stat/stf variants to neutralise green cast")

    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        fn = v.get("fn", "")
        p  = v.setdefault("params", {})

        if fn == "ghs_stretch":
            ratio = p.get("alpha", std_alpha) / max(std_alpha, 1e-6)
            p["pivot"] = ghs["pivot"]
            p["alpha"] = round(_clamp(ghs["alpha"] * ratio, 1.0, 20.0), 2)

        elif fn == "stat_stretch":
            # Preserve the relative brightness difference between stat variants
            base_tm = 0.20   # ontology standard target_median
            ratio = p.get("target_median", base_tm) / base_tm
            p["target_median"]    = round(_clamp(stat["target_median"] * ratio, 0.10, 0.35), 3)
            p["blackpoint_sigma"] = stat.get("blackpoint_sigma", p.get("blackpoint_sigma", 5.0))
            if _spcc_failed:
                p["linked"] = False

        elif fn == "stf_stretch":
            if _spcc_failed:
                p["linked"] = False

        elif fn == "veralux_stretch":
            # Pass computed target_median so veralux iterates to the right level
            p["target_median"] = round(stat["target_median"], 3)

        adapted.append(v)
    return adapted


def _adapt_scnr_variants(variants: list[dict], run_dir: Path) -> list[dict]:
    """Soften SCNR when SPCC failed upstream.

    When the `.spcc_failed` sentinel is present, the stretch step already ran
    UNLINKED (per-channel) and neutralised the green cast channel-by-channel
    (see _adapt_stretch_variants / auto_process stretch_fns). Running a full
    90% green-removal SCNR on top of an already-neutral image over-subtracts
    green and tips the result magenta/warm — the exact overcorrection Henry
    flagged on NGC 7000. So when SPCC failed: cap the SCNR amount to a gentle
    ceiling and prefer `max` (maximum neutral protection) over `avg`. When SPCC
    succeeded, leave variants untouched — SCNR is the primary green killer.

    The sentinel lives in the run dir (proc_dir), NOT input_fits.parent — the
    SCNR input is the stretch experiment winner at run_dir/experiments/stretch/.
    """
    import copy
    if not (run_dir / ".spcc_failed").exists():
        return variants

    log.warning("[experiment] scnr: SPCC failed upstream — softening SCNR "
                "(unlinked stretch already neutralised green; full strength would push magenta)")
    _CEIL = 0.5
    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        if v.get("engine") == "none" or v.get("fn") is None:
            adapted.append(v)
            continue
        p = v.setdefault("params", {})
        amt = p.get("amount", 0.9)
        if amt > _CEIL:
            p["amount"] = _CEIL
        if p.get("mode") == "avg":
            p["mode"] = "max"  # maximum neutral protection — gentlest
        adapted.append(v)
    return adapted


def _lum_mask_blend(original_fits: Path, processed_fits: Path, mask_params: dict) -> None:
    """
    Blend processed output onto original using a soft luminance mask in-place.
        output = original × (1 - mask) + processed × mask
    Mask ramps smoothly from 0→1 around lower and 1→0 around upper using
    smoothstep, so transitions are gradual rather than hard-clipped.
    Modifies processed_fits in-place.
    """
    import numpy as np
    from astropy.io import fits as afits

    lower = float(mask_params.get("lower", 0.15))
    upper = float(mask_params.get("upper", 0.85))
    fuzz  = float(mask_params.get("fuzziness", 0.06))

    with afits.open(str(original_fits)) as h:
        orig   = h[0].data.astype(np.float32)
        header = h[0].header.copy()
    with afits.open(str(processed_fits)) as h:
        proc = h[0].data.astype(np.float32)

    # PI sometimes saves integer-scaled FITS (e.g. uint32 0..4.29e9). The mask
    # thresholds below assume [0,1] luminance — on raw integer data the midtone
    # mask evaluates to 0 everywhere and the blend silently reverts the whole
    # step (the 1.7.x NBN saturation-revert bug). Normalize out-of-range inputs.
    def _norm01(a):
        lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
        if (hi > 1.001 or lo < -0.001) and hi > lo:
            return (a - lo) / (hi - lo)
        return a

    orig = _norm01(orig)
    proc = _norm01(proc)

    # Per-pixel luminance from original (channels-first FITS layout)
    lum = np.mean(orig, axis=0) if orig.ndim == 3 else orig

    def smoothstep(edge0, edge1, x):
        t = np.clip((x - edge0) / max(abs(edge1 - edge0), 1e-6), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    mask = (smoothstep(lower - fuzz, lower + fuzz, lum) *
            (1.0 - smoothstep(upper - fuzz, upper + fuzz, lum))).astype(np.float32)

    if orig.ndim == 3:
        mask = mask[np.newaxis]   # (H,W) → (1,H,W) for broadcast across channels

    blended = orig * (1.0 - mask) + proc * mask
    afits.PrimaryHDU(blended.astype(np.float32), header=header).writeto(
        str(processed_fits), overwrite=True
    )


def _adapt_nonlinear_variants(variants: list[dict], input_fits: Path,
                               step: str = "",
                               object_type: str = "unknown") -> list[dict]:
    """
    Adapt non-linear experiment variants using per-step stats-driven compute functions.
    For each variant:
      1. Scales the primary parameter proportionally around a stats-driven baseline
         (same pattern as _adapt_stretch_variants: preserves mild/strong ratios).
      2. Injects luminance mask params from compute_lum_masks.
    """
    import copy
    from nas_server import tool_params as tp

    # Compute image stats once
    try:
        from nas_server.image_analyzer import analyze
        stats = analyze(str(input_fits))
    except Exception as e:
        log.warning(f"[experiment] {step}: stats analyze failed: {e}")
        return variants

    # Luminance masks (always computed)
    masks: dict = {}
    try:
        masks = tp.compute_lum_masks(stats, object_type)
    except Exception as e:
        log.warning(f"[experiment] {step}: lum mask compute failed: {e}")

    # Per-step compute functions and primary params to scale
    # Format: (compute_fn, primary_param, ontology_std_value, clamp_lo, clamp_hi)
    _STEP_CFG = {
        "clahe":           (tp.compute_clahe,           "clip_limit",         2.0, 0.5,  6.0),
        "hdr_compression": (tp.compute_hdr_compression, "compression_factor", 1.5, 1.0,  3.5),
        "dark_enhance":    (tp.compute_dark_enhance,    "boost_factor",       5.0, 1.0, 12.0),
        "curves":          (tp.compute_curves,          "amount",             0.5, 0.2,  0.8),
        "halo_suppression":(tp.compute_halo_suppress,   "reduction_level",    2,   1,    3),
    }

    # Compute step-level baseline
    step_params: dict = {}
    if step in _STEP_CFG:
        try:
            step_params = _STEP_CFG[step][0](stats, object_type)
            log.info(f"[experiment] {step} adapt: "
                     f"{_STEP_CFG[step][1]}={step_params.get(_STEP_CFG[step][1], '?')}")
        except Exception as e:
            log.warning(f"[experiment] {step}: param compute failed: {e}")

    # iHDR params (used when hdr_compression variants include ihdr fn)
    ihdr_params: dict = {}
    if step == "hdr_compression":
        try:
            ihdr_params = tp.compute_ihdr(stats, object_type)
        except Exception as e:
            log.warning(f"[experiment] ihdr compute failed: {e}")

    # HDRMT params (for pi_hdrmt variants inside hdr_compression)
    hdrmt_params: dict = {}
    if step == "hdr_compression":
        try:
            hdrmt_params = tp.compute_hdrmt(stats, object_type)
        except Exception as e:
            log.warning(f"[experiment] hdrmt compute failed: {e}")

    # LHE params (for pi_lhe variants inside dark_enhance)
    lhe_params: dict = {}
    if step == "dark_enhance":
        try:
            lhe_params = tp.compute_lhe(stats, object_type)
        except Exception as e:
            log.warning(f"[experiment] lhe compute failed: {e}")

    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        fn = v.get("fn", "")
        p  = v.setdefault("params", {})

        # 1. Inject luminance mask
        if fn in masks:
            v["lum_mask"] = masks[fn]

        # 2. Scale primary param proportionally
        if step in _STEP_CFG and step_params:
            _, primary, std_val, lo, hi = _STEP_CFG[step]

            if fn == step and primary in p:
                # Same fn as step: scale proportionally preserving variant ratio
                ratio = p[primary] / max(abs(std_val), 1e-6)
                new_val = step_params.get(primary, std_val) * ratio
                p[primary] = round(_clamp(new_val, lo, hi), 2)

                # Also propagate auxiliary params from compute function
                for aux in ("n_scales", "mask_gamma"):
                    if aux in step_params and aux in p:
                        p[aux] = step_params[aux]

            elif fn == "halo_suppress" and "reduction_level" in p and step_params:
                # Integer level: offset from computed base
                base  = step_params.get("reduction_level", 2)
                offset = p["reduction_level"] - int(std_val)
                p["reduction_level"] = max(1, min(3, int(base + offset)))

        # 3. Adapt PI-specific variants
        if fn == "ihdr" and ihdr_params:
            std_iter = 5
            ratio = p.get("ihdr_iterations", std_iter) / std_iter
            p["ihdr_iterations"]    = max(3, min(9, int(ihdr_params["ihdr_iterations"] * ratio)))
            p["ihdr_preservation"]  = ihdr_params["ihdr_preservation"]
            # Scale mask_strength by variant's ratio relative to standard 1.25
            ms_ratio = p.get("ihdr_mask_strength", 1.25) / 1.25
            p["ihdr_mask_strength"] = round(
                _clamp(ihdr_params["ihdr_mask_strength"] * ms_ratio, 0.8, 2.0), 2)

        elif fn == "hdrmt" and hdrmt_params:
            p["hdrmt_layers"]     = hdrmt_params.get("hdrmt_layers",     p.get("hdrmt_layers", 6))
            p["hdrmt_iterations"] = hdrmt_params.get("hdrmt_iterations", p.get("hdrmt_iterations", 3))
            p["hdrmt_overdrive"]  = hdrmt_params.get("hdrmt_overdrive",  p.get("hdrmt_overdrive", 0.0))

        elif fn == "lhe" and lhe_params:
            p["lhe_kernel_r"]    = lhe_params.get("lhe_kernel_r",    p.get("lhe_kernel_r", 64))
            p["lhe_slope_limit"] = lhe_params.get("lhe_slope_limit", p.get("lhe_slope_limit", 2.0))
            # Keep lhe_amount from variant (preserves mild/standard distinction)

        adapted.append(v)
    return adapted


def _adapt_bg_variants(variants: list[dict], input_fits: Path) -> list[dict]:
    """Override background extraction params with values derived from image gradient stats."""
    import copy
    try:
        from nas_server.image_analyzer import analyze
        from nas_server.tool_params import compute_gradient_correction
        stats = analyze(str(input_fits))
        gc = compute_gradient_correction(stats)
        sev = stats["background"]["gradient_severity"]
        correction = gc["graxpert_correction"]   # "Subtraction" or "Division"
        smoothing = gc["graxpert_smoothing"]      # 0.3–1.0 (higher = gentler)
        rbf_smooth = gc["gc_smoothness"]          # same range, maps to ADBE rbf_smooth
        log.info(
            f"[experiment] bg adapt: sev={sev:.3f}, correction={correction}, "
            f"smoothing={smoothing:.2f}, rbf_smooth={rbf_smooth:.2f}"
        )
    except Exception as e:
        log.warning(f"[experiment] bg adapt failed, using ontology defaults: {e}")
        return variants

    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        fn = v.get("fn", "")
        p = v.setdefault("params", {})
        if fn == "adbe":
            p["rbf_smooth"] = round(rbf_smooth, 2)
        elif fn == "background_extract":
            p["correction"] = correction
            p["smoothing"] = round(smoothing, 2)
        adapted.append(v)
    return adapted


# ---------------------------------------------------------------------------
# Manual review helpers
# ---------------------------------------------------------------------------

def _build_review_collage(variants_for_review: list[dict], out_path: Path) -> bool:
    """
    Build a blind A/B/C labelled collage JPEG from variant preview images.

    variants_for_review: [{id, jpeg_path, label}, ...] — label is 'A', 'B', etc.
    Returns True on success.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import random as _rnd

        n = len(variants_for_review)
        panel_w = min(700, 1280 // max(n, 1))
        label_h = 48
        panels = []
        for v in variants_for_review:
            img = Image.open(v["jpeg_path"]).convert("RGB")
            img_w, img_h = img.size
            scale = panel_w / img_w
            panel_h = int(img_h * scale)
            img = img.resize((panel_w, panel_h), Image.LANCZOS)
            canvas = Image.new("RGB", (panel_w, panel_h + label_h), (20, 20, 20))
            draw = ImageDraw.Draw(canvas)
            # Large label letter
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            except Exception:
                font = ImageFont.load_default()
            draw.text((panel_w // 2 - 12, 6), v["label"], fill=(255, 220, 0), font=font)
            canvas.paste(img, (0, label_h))
            panels.append(canvas)

        total_w = sum(p.width for p in panels)
        total_h = max(p.height for p in panels)
        collage = Image.new("RGB", (total_w, total_h), (10, 10, 10))
        x = 0
        for p in panels:
            collage.paste(p, (x, 0))
            x += p.width
        collage.save(str(out_path), "JPEG", quality=85)
        return True
    except Exception as e:
        log.warning(f"[experiment] collage build failed: {e}")
        return False


def _create_and_wait_for_review(
    target: str,
    step: str,
    experiment_run_id: str,
    input_fits_path: str,
    variants_for_review: list[dict],
    claude_winner_label: str | None,
    claude_reasoning: str | None,
    collage_path: Path,
    exp_dir: Path,
) -> str | None:
    """
    Create a manual_review DB record, send collage to Telegram, block up to 5 min.

    Returns the winning variant_id (from user or Claude on timeout), or raises
    ProcessingAbortedError / ProcessingRetryError.
    """
    import random as _rnd
    from datetime import datetime, timedelta, timezone
    from nas_server import review_events
    from nas_server.exceptions import ProcessingAbortedError, ProcessingRetryError
    from nas_server.database import (
        create_manual_review, get_manual_review, set_review_status
    )
    from nas_server.config import settings
    from nas_server import telegram

    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=300)
    expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build ordered_labels list for DB
    ordered_labels = [{"label": v["label"], "variant_id": v["id"]}
                      for v in variants_for_review]

    # Build variants_json (no claude scores — revealed post-decision)
    variants_json_items = []
    for v in variants_for_review:
        variants_json_items.append({
            "label": v["label"],
            "variant_id": v["id"],
            "jpeg_path": v.get("jpeg_path", ""),
            "metrics": v.get("metrics", {}),
            "claude_score": v.get("claude_score"),  # hidden until decided
        })

    review_id = create_manual_review(
        target=target,
        step=step,
        run_id=experiment_run_id,
        input_fits_path=input_fits_path,
        ordered_labels=ordered_labels,
        variants=variants_json_items,
        claude_winner_label=claude_winner_label,
        claude_reasoning=claude_reasoning,
        expires_at=expires_at,
    )

    ev = review_events.register(review_id)

    server_host = settings.get("server_host", "http://localhost:8000")
    review_url  = f"{server_host}/review/{review_id}"
    caption = (
        f"<b>Manual Review: {target} / {step}</b>\n"
        f"Variants: {', '.join(v['label'] for v in variants_for_review)}\n"
        f"Expires: 5 min\n{review_url}"
    )
    if collage_path.exists():
        n_variants = len(variants_for_review)
        if n_variants >= 5:
            # Split into two collages so Telegram file-size limit isn't hit
            mid = n_variants // 2
            collage_a = collage_path.parent / "review_collage_a.jpg"
            collage_b = collage_path.parent / "review_collage_b.jpg"
            _build_review_collage(variants_for_review[:mid], collage_a)
            _build_review_collage(variants_for_review[mid:], collage_b)
            telegram.send_photo(str(collage_a), caption + " (1/2)")
            if collage_b.exists():
                telegram.send_photo(str(collage_b), f"(2/2) {review_url}")
        else:
            telegram.send_photo(str(collage_path), caption)
    else:
        try:
            telegram.send(caption)
        except Exception:
            pass

    log.info(f"[review] waiting for manual review #{review_id}: {target}/{step} "
             f"— {review_url} (grace period 300s then queue released)")

    # --- Grace period: wait up to 5 min before releasing the queue ---
    deadline = datetime.now(timezone.utc) + timedelta(seconds=300)
    while datetime.now(timezone.utc) < deadline:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if ev.wait(timeout=min(10.0, remaining)):
            break  # User submitted during grace period

    review = get_manual_review(review_id)
    status = review["status"] if review else None

    if status == "decided":
        review_events.unregister(review_id)
        return review["final_winner_variant"]
    elif status == "aborted":
        review_events.unregister(review_id)
        raise ProcessingAbortedError(f"Manual review aborted: {target}/{step}")
    elif status == "retried":
        review_events.unregister(review_id)
        raise ProcessingRetryError(step)

    # --- Grace period expired, no decision yet — park and release queue ---
    from nas_server import queue_manager
    queue_manager.park_for_review(target)
    log.info(f"[review] #{review_id} grace period elapsed — queue released, "
             f"waiting indefinitely for user: {review_url}")
    try:
        telegram.send(
            f"⏸ <b>Review parked</b>: {target} / {step}\n"
            f"Queue continuing. Submit when ready: {review_url}"
        )
    except Exception:
        pass

    ev.wait()  # block indefinitely until user submits

    queue_manager.unpark_from_review(target)
    review_events.unregister(review_id)

    review = get_manual_review(review_id)
    status = review["status"] if review else "unknown"

    if status == "decided":
        return review["final_winner_variant"]
    elif status == "aborted":
        raise ProcessingAbortedError(f"Manual review aborted: {target}/{step}")
    elif status == "retried":
        raise ProcessingRetryError(step)
    else:
        log.warning(f"[review] #{review_id} ev.wait() returned but status={status!r}")
        return None


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    target: str,
    step: str,
    input_fits: str,
    object_type: str = "unknown",
    custom_variants: list[dict] | None = None,
    dry_run: bool = False,
    proc_dir: Path | None = None,
    manual_review: bool = False,
) -> dict:
    """
    Run all variants for a processing step, assess each with Claude, pick the winner,
    and store results for future learning.

    Returns {
        "ok": bool,
        "winner": variant_id,
        "output_path": str,
        "variants": [{id, ok, scores, elapsed}, ...],
        "reasoning": str,
        "learning_note": str,
        "priors": {...},
        "elapsed": float,
    }
    """
    from nas_server.database import record_experiment_variant, get_experiment_priors
    from nas_server.config import settings
    from nas_server.auto_process import _object_type_from_name
    from nas_server import step_assessor as _step_assessor

    start_ts = time.time()
    experiment_run_id = str(uuid.uuid4())
    if object_type == "unknown":
        object_type = _object_type_from_name(target)

    _set_status(target, phase=f"experiment:{step}", step=step, started_at=start_ts)

    # Load ontology variants
    try:
        with open(_ONTOLOGY_PATH) as f:
            ontology = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"Cannot load ontology: {e}"}

    step_def = ontology.get("processing_steps", {}).get(step)
    if step_def is None:
        return {"ok": False, "error": f"Unknown step: {step}"}

    variants = custom_variants or step_def.get("experiment_variants", [])
    if not variants:
        return {"ok": False, "error": f"No experiment variants defined for step: {step}"}

    input_path = Path(input_fits)
    if not input_path.exists():
        return {"ok": False, "error": f"Input not found: {input_fits}"}

    # Adapt background extraction params from measured gradient statistics
    if step == "background_extraction" and not custom_variants:
        variants = _adapt_bg_variants(variants, input_path)

    # Adapt BXT deconvolution params from measured PSF and image stats
    if step == "deconvolution" and not custom_variants:
        variants = _adapt_deconv_variants(variants, input_path, object_type)

    # Adapt stretch params from image statistics
    if step == "stretch" and not custom_variants:
        variants = _adapt_stretch_variants(variants, input_path, object_type)

    # Soften SCNR when SPCC failed (unlinked stretch already killed the green).
    # Sentinel lives in the run dir, so resolve proc_dir here (it defaults below).
    if step == "scnr" and not custom_variants:
        variants = _adapt_scnr_variants(variants, proc_dir or input_path.parent)

    # Inject luminance mask params for non-linear steps
    _NL_STEPS = {"clahe", "curves", "hdr_compression", "dark_enhance",
                 "local_contrast", "s_curve", "color_saturation",
                 "denoise_linear", "denoise_nonlinear"}
    if step in _NL_STEPS and not custom_variants:
        variants = _adapt_nonlinear_variants(variants, input_path, step, object_type)

    # Use explicitly passed proc_dir so all steps stay flat under _processed/experiments/
    # rather than nesting inside the previous step's experiment subdir.
    if proc_dir is None:
        proc_dir = input_path.parent
    exp_dir = proc_dir / "experiments" / step
    exp_dir.mkdir(parents=True, exist_ok=True)

    # Fetch historical priors to guide Claude
    priors = get_experiment_priors(step, object_type)
    learned = get_learned_defaults(step, object_type)
    learning_note = ""
    if learned["sample_count"] >= 3:
        learning_note = (
            f"Based on {learned['sample_count']} past experiments, "
            f"'{learned['variant']}' has been preferred ({int(learned['confidence']*100)}% win rate)"
        )
        log.info(f"[experiment] {target}/{step}: priors say '{learned['variant']}' "
                 f"({int(learned['confidence']*100)}% confidence, n={learned['sample_count']})")

    log.info(f"[experiment] {target}/{step}: running {len(variants)} variants "
             f"(dry_run={dry_run})")

    # Execute variants
    variant_results: list[dict] = []
    valid_for_claude: list[dict] = []

    for v in variants:
        vid = v["id"]
        out_fits = exp_dir / f"{vid}.fit"
        jpg_path = exp_dir / f"{vid}_preview.jpg"

        _set_status(target, current_variant=vid)

        if dry_run:
            variant_results.append({
                "id": vid,
                "description": v.get("description", vid),
                "ok": True,
                "elapsed": 0.0,
                "dry_run": True,
            })
            continue

        log.info(f"[experiment] {target}/{step}: running variant '{vid}'...")
        run_result = _run_variant(v, input_path, out_fits)
        vr: dict = {
            "id": vid,
            "description": v.get("description", vid),
            "params": v.get("params", {}),
            **run_result,
        }

        _LINEAR_STEPS = {"crop", "background_extraction", "color_calibration",
                         "deconvolution", "denoise_linear", "sharpen_linear"}
        if run_result["ok"] and out_fits.exists():
            if step in _LINEAR_STEPS:
                _generate_linear_composite(out_fits, jpg_path)
            else:
                _generate_preview(out_fits, jpg_path)
            # Physics metrics
            try:
                metrics = _step_assessor.assess_step(input_path, out_fits, step, object_type)
                vr["metrics"] = metrics
                vr["analytically_failed"] = metrics.get("analytically_failed", False)
            except Exception as _ae:
                log.debug(f"[experiment] step_assessor failed for {vid}: {_ae}")
                vr["metrics"] = {}
                vr["analytically_failed"] = False
            if jpg_path.exists():
                vr["jpeg_path"] = str(jpg_path)
                vr["output_path"] = str(out_fits)
                if not vr.get("analytically_failed"):
                    valid_for_claude.append({
                        "id": vid,
                        "description": v.get("description", vid),
                        "params": v.get("params", {}),
                        "jpeg_path": str(jpg_path),
                        "metrics": vr.get("metrics", {}),
                    })
                else:
                    log.info(f"[experiment] variant '{vid}' analytically rejected — excluded from Claude")
        else:
            log.warning(f"[experiment] variant '{vid}' failed: {run_result.get('error')}")

        variant_results.append(vr)

    if dry_run:
        return {
            "ok": True, "dry_run": True, "target": target, "step": step,
            "variants": variant_results, "winner": None, "output_path": None,
            "reasoning": "dry run — no variants executed",
            "learning_note": learning_note, "priors": priors,
            "elapsed": time.time() - start_ts,
        }

    if len(valid_for_claude) < 1:
        return {"ok": False, "error": "All variants failed — nothing to compare"}

    # Claude picks winner
    pick = None
    winner_id = valid_for_claude[0]["id"]
    reasoning = "Only one variant succeeded — selected by default"

    if len(valid_for_claude) >= 2 and settings.get("anthropic_api_key"):
        _set_status(target, current_variant="claude_comparison")
        v_metrics = {v["id"]: v.get("metrics", {}) for v in valid_for_claude}
        pick = pick_best_variant(target, step, valid_for_claude, priors=priors,
                                 variant_metrics=v_metrics)
        if pick and pick.get("winner") in [v["id"] for v in valid_for_claude]:
            winner_id = pick["winner"]
            reasoning = pick.get("reasoning", "")
            # Merge Claude scores back into variant_results
            for vr in variant_results:
                vr["claude_score"] = (pick.get("scores") or {}).get(vr["id"])

    # Manual review block (if enabled and we have a real Claude pick)
    if ((manual_review or settings.get("manual_review_enabled"))
            and len(valid_for_claude) >= 2
            and pick is not None):
        import random as _rnd
        labels = "ABCDEFGH"
        # Pull claude scores from variant_results into the comparison list
        vr_scores = {vr["id"]: vr.get("claude_score") for vr in variant_results}
        shuffled = [{**v, "claude_score": vr_scores.get(v["id"])} for v in valid_for_claude]
        _rnd.shuffle(shuffled)
        blind = [{"label": labels[i], **v} for i, v in enumerate(shuffled)]
        # Map winner_id → label for Claude
        claude_label = next((v["label"] for v in blind if v["id"] == winner_id), None)
        collage_path = exp_dir / "review_collage.jpg"
        _build_review_collage(blind, collage_path)
        from nas_server.exceptions import ProcessingAbortedError, ProcessingRetryError
        try:
            user_winner = _create_and_wait_for_review(
                target=target,
                step=step,
                experiment_run_id=experiment_run_id,
                input_fits_path=str(input_path),
                variants_for_review=blind,
                claude_winner_label=claude_label,
                claude_reasoning=reasoning,
                collage_path=collage_path,
                exp_dir=exp_dir,
            )
            if user_winner is not None:
                winner_id = user_winner
        except (ProcessingAbortedError, ProcessingRetryError):
            raise

    # Compute margin stats per variant
    all_scores: dict[str, float] = {}
    for vr in variant_results:
        if vr.get("claude_score") is not None:
            all_scores[vr["id"]] = vr["claude_score"]
    all_scores_json = json.dumps(all_scores) if all_scores else None

    # Store results in DB
    for vr in variant_results:
        if not vr.get("dry_run") and vr.get("ok"):
            scores = {}
            if vr.get("claude_score") is not None:
                scores["overall"] = vr["claude_score"]
            my_score = all_scores.get(vr["id"])
            other_scores = [s for vid, s in all_scores.items() if vid != vr["id"]]
            best_other = max(other_scores) if other_scores else my_score
            winning_margin = (my_score - best_other) if (my_score is not None and best_other is not None) else None
            runner_up_score = best_other
            try:
                record_experiment_variant(
                    target=target,
                    object_type=object_type,
                    step=step,
                    variant_id=vr["id"],
                    params=vr.get("params"),
                    scores=scores,
                    winner=(vr["id"] == winner_id),
                    reasoning=reasoning if vr["id"] == winner_id else None,
                    metrics_json=json.dumps(vr.get("metrics", {})) if vr.get("metrics") else None,
                    experiment_run_id=experiment_run_id,
                    all_scores_json=all_scores_json,
                    runner_up_score=runner_up_score,
                    winning_margin=winning_margin,
                )
            except Exception as db_err:
                log.debug(f"[experiment] DB record error: {db_err}")

    # Copy winner to standard output location
    winner_vr = next((v for v in variant_results if v["id"] == winner_id), None)
    winner_out = winner_vr.get("output_path") if winner_vr else None
    final_out = exp_dir / "winner.fit"
    if winner_out and Path(winner_out).exists():
        shutil.copy2(winner_out, str(final_out))

    elapsed = time.time() - start_ts
    _set_status(target, phase="done", winner=winner_id, elapsed=elapsed)

    log.info(f"[experiment] {target}/{step}: winner='{winner_id}' "
             f"in {elapsed:.0f}s — {reasoning[:900]}")

    return {
        "ok": True,
        "target": target,
        "step": step,
        "object_type": object_type,
        "winner": winner_id,
        "winner_description": (next((v for v in variants if v["id"] == winner_id), {})
                               .get("description", winner_id)),
        "output_path": str(final_out) if final_out.exists() else winner_out,
        "variants": variant_results,
        "reasoning": reasoning,
        "learning_note": learning_note,
        "priors": priors,
        "elapsed": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------

def run_experiment_bg(target: str, step: str, input_fits: str,
                      object_type: str = "unknown") -> None:
    """Fire run_experiment in a background thread."""
    import threading
    t = threading.Thread(
        target=run_experiment,
        args=(target, step, input_fits, object_type),
        daemon=True,
    )
    t.start()

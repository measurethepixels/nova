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
import os
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
_LEASE_HEARTBEAT_SECONDS = 30
_CANDIDATE_NEAR_IDENTITY_ATOL = 1e-7
# Real motivating case (M51, 2026-09-13): cc_sssc measured B/R=0.560 while
# three peer candidates agreed at ~1.0, with a solve RMS (0.51) well inside
# the accepted gate -- RMS alone did not catch it. Henry, 2026-09-13: "Let's
# make it so we use the ratios as quality gates. Error rms isn't reliable
# enough." Peer-relative rather than an absolute distance from 1.0, because
# the 'right' ratio is target-dependent (narrowband/LP compositions can
# legitimately sit far from neutral) -- what's suspicious is one candidate
# disagreeing sharply with OTHERS solving the identical calibration problem
# on the identical input, not a fixed distance from parity.
_COLOR_RATIO_OUTLIER_MIN_PEERS = 3
_COLOR_RATIO_OUTLIER_REL_THRESHOLD = 0.25
# How long _create_and_wait_for_review() waits before parking (releasing the
# queue for other targets) and waiting indefinitely for a decision. A module
# constant so tests can shrink it rather than waiting 5 real minutes.
_REVIEW_GRACE_PERIOD_SECONDS = 300


def _candidate_pixel_distance(path_a: str | Path,
                              path_b: str | Path) -> tuple[float, float] | None:
    """Return max/mean absolute FITS-pixel distance, or None if incomparable."""
    try:
        import numpy as np
        from astropy.io import fits
        with fits.open(str(path_a), memmap=False) as hdul:
            a = hdul[0].data.astype(np.float32)
        with fits.open(str(path_b), memmap=False) as hdul:
            b = hdul[0].data.astype(np.float32)
        if a.shape != b.shape:
            return None
        difference = np.abs(a - b)
        return float(difference.max(initial=0.0)), float(difference.mean())
    except Exception:
        return None


def _surface_collapsed_candidates(candidates: list[dict], *,
                                  atol: float = _CANDIDATE_NEAR_IDENTITY_ATOL
                                  ) -> dict[str, str]:
    """Identify candidates an evaluator must not be asked to distinguish."""
    collapsed: dict[str, str] = {}
    survivors: list[dict] = []
    # The untreated control is the semantic baseline, regardless of ontology
    # order.  When a treatment is ineffective, collapse it into ``none`` rather
    # than discarding the control in favour of the earlier treatment.
    ordered_candidates = sorted(candidates, key=lambda item: item.get("id") != "none")
    for candidate in ordered_candidates:
        path = candidate.get("output_path")
        representative = None
        if path:
            for survivor in survivors:
                distance = _candidate_pixel_distance(path, survivor["output_path"])
                if distance is not None and distance[0] <= atol:
                    representative = survivor["id"]
                    break
        if representative is None:
            survivors.append(candidate)
        else:
            collapsed[candidate["id"]] = representative
    return collapsed


def _surface_color_ratio_outliers(candidates: list[dict], step: str) -> dict[str, str]:
    """Flag color_calibration candidates whose measured B/R or G/R is a
    statistical outlier from its peers in the same run — see
    _COLOR_RATIO_OUTLIER_MIN_PEERS/_REL_THRESHOLD above for why this is
    peer-relative rather than a fixed distance from 1.0.

    The peer median is computed over one representative value PER FAMILY
    (`candidate["family"]` — see valid_for_claude's construction), not one
    vote per raw candidate. Real gap found against run 435's own data: 4
    declared SSSC-family variants (cc_sssc + its background-equalization
    race baseline/masked/raw siblings) clustered at ~0.56 would otherwise
    outvote the 3 genuinely distinct methods (cc_none/cc_pi/cc_spcc)
    clustered at ~1.00 by raw candidate count — the naive median of all 7
    raw values IS the crushed value (0.56), which would flag the three
    CORRECT candidates as the outliers instead of the four crushed ones.
    Grouping by family first, then taking one median per family, makes the
    peer vote about genuinely distinct methods, not about how many
    variants of one method happen to be declared in the ontology.

    Requires at least _COLOR_RATIO_OUTLIER_MIN_PEERS distinct families
    with a measured ratio for either channel before judging anyone an
    outlier on it — with fewer, "who's the outlier" is not decidable (a
    2-way disagreement could be either family).
    """
    if step != "color_calibration":
        return {}
    import statistics

    flagged: dict[str, str] = {}
    for channel_key, channel_label in (
            ("color_b_over_r_after", "B/R"), ("color_g_over_r_after", "G/R")):
        values = {}
        family_of = {}
        for c in candidates:
            v = c.get("metrics", {}).get(channel_key)
            if v is not None:
                values[c["id"]] = v
                family_of[c["id"]] = c.get("family", c["id"])
        if not values:
            continue
        by_family: dict[str, list[float]] = {}
        for vid, val in values.items():
            by_family.setdefault(family_of[vid], []).append(val)
        if len(by_family) < _COLOR_RATIO_OUTLIER_MIN_PEERS:
            continue
        family_reps = [statistics.median(vals) for vals in by_family.values()]
        peer_median = statistics.median(family_reps)
        if peer_median <= 0:
            continue
        for vid, val in values.items():
            if vid in flagged:
                continue
            rel_dev = abs(val - peer_median) / peer_median
            if rel_dev > _COLOR_RATIO_OUTLIER_REL_THRESHOLD:
                flagged[vid] = (f"color_ratio_outlier_vs_peers:{channel_label}="
                                f"{val:.3f}_peer_median={peer_median:.3f}")
    return flagged


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


def _set_variant_active(target: str, variant_id: str, active: bool) -> None:
    with _active_lock:
        status = _active.setdefault(target, {})
        variants = set(status.get("active_variants", []))
        (variants.add if active else variants.discard)(variant_id)
        status["active_variants"] = sorted(variants)
        status["current_variant"] = None if not variants else "concurrent"


# ---------------------------------------------------------------------------
# Preview helper (shared with auto_process)
# ---------------------------------------------------------------------------

def _generate_preview(fits_path: Path, jpg_path: Path, linked: bool = True) -> bool:
    """Generate JPEG preview using PI STF algorithm. `linked` param kept for call-site compat."""
    from nas_server import seti_astro
    return seti_astro.generate_preview_stf(fits_path, jpg_path)


def _generate_preview_nl(fits_path: Path, jpg_path: Path) -> bool:
    """Generate JPEG preview for already-stretched (non-linear) candidate data --
    no extra STF re-stretch. Mirrors auto_process.py's own linear/non-linear
    preview split (_generate_preview vs _generate_preview_nl); without this,
    review previews for steps like curves/clahe/dark_enhance re-stretch data
    that's already been stretched, washing the sky to flat grey noise instead
    of the real black background (Henry, 2026-09-16, M 100/curves review)."""
    from nas_server import seti_astro
    return seti_astro.generate_preview_nonlinear(fits_path, jpg_path)


def _generate_linear_composite(fits_path: Path, jpg_path: Path, *,
                                linked: bool = False,
                                rect_xywh: "tuple[int, int, int, int] | None" = None) -> bool:
    """
    Side-by-side composite for linear step evaluation:
      Left  — normal STF (target_bg=0.30, matches PI STF display)
      Right — deep stretch (target_bg=0.85, reveals hidden gradients and faint structure)

    Helps Claude judge whether background extraction, color calibration, deconvolution, etc.
    fully resolved faint features that would be invisible at normal stretch.

    linked: use PI-linked (shared-transform) STF instead of per-channel — required
    for a color_calibration review, since per-channel STF re-normalises each
    channel's black/white point independently and hides real color differences
    between calibration candidates (Henry, 2026-09-12).
    rect_xywh: optional background-equalization sample patch to outline on both
    panels (e.g. sssc_race_masked/raw's auto-detected patch).
    """
    try:
        import tempfile, numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from nas_server import seti_astro

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_normal = f.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_deep = f.name

        ok_n = seti_astro.generate_preview_stf(fits_path, tmp_normal, target_bg=0.30,
                                               linked=linked, rect_xywh=rect_xywh)
        ok_d = seti_astro.generate_preview_stf(fits_path, tmp_deep,
                                               target_bg=0.85, shadow_clip_k=1.25,
                                               linked=linked, rect_xywh=rect_xywh)

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


def _generate_star_detail_preview(fits_path: Path, jpg_path: Path) -> bool:
    """Render up to three bright-star crops for deconvolution evaluation."""
    try:
        import numpy as _np
        import sep
        from astropy.io import fits as _fits
        from PIL import Image, ImageDraw

        with _fits.open(str(fits_path)) as hdul:
            data = hdul[0].data.astype(_np.float32)
        if data.ndim == 3:
            data = _np.mean(data, axis=0)
        data = _np.ascontiguousarray(data)
        bkg = sep.Background(data, bw=256, bh=256, fw=3, fh=3)
        sub = data - bkg.back()
        rms = max(float(bkg.globalrms), 1e-12)
        objects = sep.extract(sub, 8.0 * rms, err=rms, minarea=9,
                              deblend_nthresh=8, deblend_cont=0.01, clean=True)
        objects = sorted(objects, key=lambda obj: float(obj["peak"]), reverse=True)

        crops = []
        h, w = sub.shape
        for obj in objects:
            fwhm = 2.355 * float(_np.sqrt(max(obj["a"] * obj["b"], 1e-12)))
            ecc = float(_np.sqrt(max(0.0, 1.0 - (obj["b"] / max(obj["a"], 1e-6)) ** 2)))
            if not (1.5 < fwhm < 15.0 and ecc < 0.5 and obj["peak"] / rms >= 20.0):
                continue
            radius = max(18, int(_np.ceil(fwhm * 5)))
            cx, cy = int(round(float(obj["x"]))), int(round(float(obj["y"])))
            if cx - radius < 0 or cy - radius < 0 or cx + radius >= w or cy + radius >= h:
                continue
            cut = sub[cy - radius:cy + radius + 1, cx - radius:cx + radius + 1]
            # A monotonic asinh display transform exposes small halo deviations
            # without clipping the star core.
            scaled = _np.arcsinh(cut / (3.0 * rms))
            lo, hi = _np.percentile(scaled, (0.5, 99.8))
            span = max(float(hi - lo), 1e-12)
            rendered = _np.clip((scaled - lo) / span * 255.0, 0, 255).astype(_np.uint8)
            resampling = getattr(Image, "Resampling", Image)
            crops.append(Image.fromarray(rendered, mode="L").convert("RGB").resize(
                (240, 240), resampling.LANCZOS))
            if len(crops) == 3:
                break
        if not crops:
            return False
        sheet = Image.new("RGB", (240 * len(crops), 264), (20, 20, 20))
        draw = ImageDraw.Draw(sheet)
        for index, crop in enumerate(crops):
            sheet.paste(crop, (index * 240, 24))
            draw.text((index * 240 + 5, 4), f"Bright star {index + 1}", fill=(220, 220, 220))
        sheet.save(str(jpg_path), quality=92)
        return True
    except Exception as exc:
        log.warning("[experiment] star-detail preview failed for %s: %s", fits_path, exc)
        return False


# ---------------------------------------------------------------------------
# Variant execution
# ---------------------------------------------------------------------------

def _is_noop_variant(variant: dict) -> bool:
    """Recognize every supported declaration of an unchanged-image control."""
    return (variant.get("engine") == "none"
            or variant.get("fn") is None
            or variant.get("fn") == "none")


def _run_variant(variant: dict, input_fits: Path, output_fits: Path, *,
                 remote_workspace=None, remote_input=None,
                 remote_output_name: str | None = None) -> dict:
    """
    Execute one experiment variant.  Returns {"ok": bool, "elapsed": float, "error": str|None}.
    Supports engines: seti_astro, ml_tools_gpu, pixinsight, none.
    """
    engine = variant.get("engine", "seti_astro")
    fn_name = variant.get("fn")
    params = dict(variant.get("params", {}))
    start = time.time()

    if _is_noop_variant(variant):
        shutil.copy2(str(input_fits), str(output_fits))
        return {"ok": True, "elapsed": 0.0, "error": None,
                "treatment_observations": {
                    "backend": "filesystem_copy", "resolved_params": params,
                    "mask_blend": "not_requested", "fallback": "none"}}

    if engine == "seti_astro":
        from nas_server import seti_astro
        fn = getattr(seti_astro, fn_name, None)
        if fn is None:
            return {"ok": False, "elapsed": 0.0, "error": f"seti_astro.{fn_name} not found"}
        try:
            result = fn(input_fits, output_fits, **params)
            ok = result.get("ok", False) and output_fits.exists()
            mask_blend = "not_requested"
            if ok:
                lm = variant.get("lum_mask")
                if lm:
                    try:
                        _lum_mask_blend(input_fits, output_fits, lm)
                        mask_blend = "applied"
                        log.info(f"[experiment] lum mask blend applied to {fn_name}")
                    except Exception as be:
                        mask_blend = "failed"
                        log.warning(f"[experiment] lum mask blend failed for {fn_name}: {be}")
            observations = {key: result[key] for key in
                            ("backend", "method", "model_version", "fallback")
                            if key in result}
            observations["resolved_params"] = params
            observations["mask_blend"] = mask_blend
            return {"ok": ok, "elapsed": time.time() - start,
                    "error": result.get("error") if not ok else None,
                    "treatment_observations": observations,
                    # background-equalization sample patch (sssc_race_masked/raw) —
                    # not part of the whitelisted `observations` fields above, but
                    # needed by the color_calibration preview overlay.
                    "rect_xywh": result.get("rect_xywh"),
                    # sssc_calibrate()'s own solve quality — not part of the
                    # whitelisted `observations` fields above, but real evidence
                    # for color_calibration decisions (does a worse-converged
                    # solve correlate with a color-ratio outlier?). None for
                    # every non-SSSC function, same pattern as rect_xywh.
                    "applied_rms": result.get("applied_rms"),
                    "n_stars": result.get("n_stars")}
        except Exception as e:
            return {"ok": False, "elapsed": time.time() - start, "error": str(e)}

    if engine == "ml_tools_gpu":
        try:
            operations = variant.get("operations")
            if operations:
                # A chained treatment preserves order by consuming each prior
                # stage's artifact. It intentionally does not masquerade as a
                # single worker operation or fall back to seti_astro.
                from nas_server.ml_tools_gpu import run_ml_tool_pipeline_gpu
                result = run_ml_tool_pipeline_gpu(
                    operations, input_fits, output_fits, params)
            else:
                from nas_server.ml_tools_gpu import run_ml_tool_gpu
                result = run_ml_tool_gpu(
                    fn_name,
                    remote_input if remote_input is not None else input_fits,
                    output_fits,
                    params,
                    workspace=remote_workspace,
                    output_name=remote_output_name,
                    local_source_path=input_fits if remote_workspace is not None else None,
                )
            ok = bool(result.get("ok") and output_fits.exists())
            observations = {key: result[key] for key in
                            ("backend", "method", "model_version", "fallback")
                            if key in result}
            observations.update({"backend": observations.get("backend", "runpod_ml_tools"),
                                 "resolved_params": params,
                                 "mask_blend": "not_requested"})
            return {"ok": ok,
                    "elapsed": result.get("elapsed", result.get("elapsed_s",
                                                                  time.time() - start)),
                    "error": result.get("error") if not ok else None,
                    "treatment_observations": observations}
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
            second_pass = "not_requested"

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
                second_pass = "started"
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
                        second_pass = "failed_kept_first"
                        shutil.copy2(str(_tmp), str(output_fits))
                        log.warning("[experiment] NXT second pass failed — keeping first pass")
                    else:
                        second_pass = "succeeded"
                except Exception as _e2:
                    second_pass = "failed_kept_first"
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
            fallback = "unknown"
            if result.get("spcc_failed"):
                fallback = "color_calibration"
            elif result.get("cc_failed"):
                fallback = "none"
            return {"ok": pi_ok, "elapsed": time.time() - start,
                    "error": None if pi_ok else "PI pipeline failed",
                    "treatment_observations": {
                        "backend": "pixinsight", "resolved_params": pi_kwargs,
                        "fallback": fallback, "second_pass": second_pass,
                        "mask_blend": "integrated" if lm else "not_requested"}}
        except Exception as e:
            return {"ok": False, "elapsed": time.time() - start, "error": str(e)}

    return {"ok": False, "elapsed": 0.0, "error": f"Unknown engine: {engine}"}


def _effective_treatment(
    variant: dict, run_result: dict, *, output_exists: bool,
) -> dict:
    """Describe only resolved/observed material facts, never declared defaults."""
    engine = variant.get("engine", "seti_astro")
    fn_name = variant.get("fn")
    observations = run_result.get("treatment_observations", {})
    if _is_noop_variant(variant):
        no_op = "declared_no_treatment"
    elif observations.get("no_op") is True:
        no_op = "runtime_no_op"
    elif not run_result.get("ok") and not output_exists:
        no_op = "failure_no_pixels"
    else:
        no_op = "normal"
    return {
        "resolution_status": ("complete" if run_result.get("ok")
                              or engine == "none" else "partial"),
        "no_op_semantics": no_op,
        "engine": engine,
        "function": fn_name,
        "resolved_params": observations.get("resolved_params", "unknown"),
        "luminance_mask": variant.get("lum_mask", "unknown"),
        "backend": observations.get("backend", "unknown"),
        "tool_method": observations.get("method", "unknown"),
        "model_version": observations.get("model_version", "unknown"),
        "fallback": observations.get("fallback", "unknown"),
        "mask_blend_result": observations.get("mask_blend", "unknown"),
        "second_pass_result": observations.get("second_pass", "unknown"),
    }


# ---------------------------------------------------------------------------
# Claude comparison
# ---------------------------------------------------------------------------

EVALUATOR_PAYLOAD_POLICY = "encoded-payload-budget/1.0.0"
EVALUATOR_PAYLOAD_BUDGET_BYTES = 20 * 1024 * 1024
EVALUATOR_PAYLOAD_FIXED_OVERHEAD_BYTES = 256 * 1024


def _encoded_file_size(path: str | None) -> int:
    """Return the JSON/base64 payload contribution, conservatively padded."""
    if not path or not Path(path).exists():
        return 0
    raw = Path(path).stat().st_size
    return 4 * ((raw + 2) // 3) + 1024


def _metric_vector(variant: dict, variant_metrics: dict | None) -> tuple[float, ...]:
    metrics = (variant_metrics or {}).get(variant["id"], {})
    values = []
    for name in ("bg_sigma_ratio", "fwhm_delta_pct", "snr_after"):
        value = metrics.get(name)
        values.append(float(value) if isinstance(value, (int, float)) else 0.0)
    return tuple(values)


def _diverse_variant_order(variants: list[dict], variant_metrics: dict | None) -> list[dict]:
    """Stable farthest-first coverage; this is not a winner/quality ranking."""
    remaining = sorted(variants, key=lambda item: str(item["id"]))
    control = next((item for item in remaining if str(item["id"]).lower() in
                    {"control", "incumbent", "baseline", "a"}), remaining[0])
    selected = [control]
    remaining.remove(control)
    while remaining:
        vectors = {item["id"]: _metric_vector(item, variant_metrics) for item in remaining}
        selected_vectors = [_metric_vector(item, variant_metrics) for item in selected]
        def distance(item):
            vector = vectors[item["id"]]
            return min(sum((a-b) ** 2 for a, b in zip(vector, prior))
                       for prior in selected_vectors)
        choice = max(remaining, key=lambda item: (distance(item), str(item["id"])))
        selected.append(choice)
        remaining.remove(choice)
    return selected


def _prepare_evaluator_presentation(
    variants: list[dict], step: str, variant_metrics: dict | None = None,
    *, budget_bytes: int = EVALUATOR_PAYLOAD_BUDGET_BYTES,
) -> tuple[list[dict], dict]:
    """Fit one evaluator call to an encoded-byte budget without changing eligibility."""
    variants = [dict(item) for item in variants]
    prompt_bytes = EVALUATOR_PAYLOAD_FIXED_OVERHEAD_BYTES + sum(
        len(str(item.get("description", item["id"])).encode()) + 512 for item in variants)
    full_bytes = prompt_bytes + sum(
        _encoded_file_size(item.get("jpeg_path"))
        + (_encoded_file_size(item.get("detail_jpeg_path")) if step == "deconvolution" else 0)
        for item in variants)
    detail_sheets = step == "deconvolution" and any(
        _encoded_file_size(item.get("detail_jpeg_path")) for item in variants)
    estimated = full_bytes
    if estimated > budget_bytes and detail_sheets:
        for item in variants:
            item.pop("detail_jpeg_path", None)
        detail_sheets = False
        estimated = prompt_bytes + sum(_encoded_file_size(item.get("jpeg_path"))
                                       for item in variants)

    presented = variants
    if estimated > budget_bytes:
        presented = []
        used = prompt_bytes
        ordered = _diverse_variant_order(variants, variant_metrics)
        for item in ordered:
            cost = _encoded_file_size(item.get("jpeg_path"))
            if used + cost <= budget_bytes:
                presented.append(item)
                used += cost
        # Keep a durable one-item presentation when no pair can fit.  The API
        # call remains disabled below, and its attempt records why comparison
        # was not performed.
        if not presented and ordered:
            presented = [ordered[0]]
            used += _encoded_file_size(ordered[0].get("jpeg_path"))
        estimated = used
    metadata = {
        "policy_revision": EVALUATOR_PAYLOAD_POLICY,
        "budget_bytes": budget_bytes,
        "estimated_encoded_bytes": estimated,
        "detail_sheets_included": detail_sheets,
        "eligible_variant_ids": sorted(str(item["id"]) for item in variants),
        "presented_variant_ids": sorted(str(item["id"]) for item in presented),
        "omitted_variant_ids": sorted(
            set(str(item["id"]) for item in variants)
            - set(str(item["id"]) for item in presented)),
    }
    return presented, metadata


def _blind_evaluator_roster(
    variants: list[dict], metrics: dict,
) -> tuple[list[dict], dict, dict[str, str]]:
    """Replace declared identities and descriptions with stable neutral labels."""
    labels = {v["id"]: f"Candidate {index + 1:02d}"
              for index, v in enumerate(sorted(variants, key=lambda item: item["id"]))}
    blinded = [{**v, "id": labels[v["id"]], "description": labels[v["id"]],
                "params": {}} for v in variants]
    blinded_metrics = {labels[key]: value for key, value in metrics.items() if key in labels}
    return blinded, blinded_metrics, labels

def pick_best_variant(
    target: str,
    step: str,
    variants_data: list[dict],
    priors: dict | None = None,
    variant_metrics: dict | None = None,
    payload_prepared: bool = False,
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
    if not payload_prepared:
        valid, _ = _prepare_evaluator_presentation(valid, step, variant_metrics)
    if len(valid) < 2:
        return None

    import base64
    from nas_server.claude_client import _client, SYSTEM_PROMPT, MODEL

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
    if variant_metrics and step == "color_calibration":
        # Real motivating case (M51, 2026-09-12): an SSSC candidate crushed
        # B/R to 0.56 in the target's own bright structure while three
        # independent candidates all agreed at ~1.0 -- invisible in a
        # stretched preview, unmistakable as a number. FWHM/SNR don't apply
        # to this step; show the measured color ratio instead. RMS/Stars
        # (2026-09-13, Henry) is sssc_calibrate()'s own solve-quality report
        # -- 'n/a' for any candidate not produced by an SSSC-family function
        # (SPCC, PI, control), real evidence for whether a color-ratio
        # outlier correlates with a poorly-converged star-photometry solve.
        rows = []
        for v in valid:
            m = variant_metrics.get(v["id"], {})
            b_r = m.get("color_b_over_r_after")
            g_r = m.get("color_g_over_r_after")
            rms = v.get("applied_rms")
            n_stars = v.get("n_stars")
            row = f"| {v['id']:<24}"
            row += f" | {f'{b_r:.3f}' if b_r is not None else 'n/a':>7}"
            row += f" | {f'{g_r:.3f}' if g_r is not None else 'n/a':>7}"
            row += f" | {f'{rms:.3f}' if rms is not None else 'n/a':>7}"
            row += f" | {n_stars if n_stars is not None else 'n/a':>5} |"
            rows.append(row)
        physics_note = (
            "\n\nMeasured color ratio in the target's own brightest 3% of pixels "
            "(auto-failed variants already excluded). A candidate whose ratio is a "
            "clear outlier from its peers is doing something unusual to that "
            "channel, whether or not that is visible in the preview. RMS/Stars "
            "is the SSSC solve's own quality report (n/a for non-SSSC candidates) "
            "-- a high RMS or low star count means that candidate's color solve "
            "was itself poorly constrained:\n"
            "| Variant                   |   B/R   |   G/R   |   RMS   |Stars|\n"
            "|---------------------------|---------|---------|---------|-----|\n"
            + "\n".join(rows)
        )
    elif variant_metrics:
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

    deconvolution_note = (
        " For deconvolution, explicitly inspect point sources for dark or bright "
        "ringing/halo artefacts; the labelled detail sheet shows stretched "
        "bright-star crops."
        if step == "deconvolution" else ""
    )
    content.append({"type": "text", "text": (
        f"Compare these {len(valid)} variants of the '{step}' processing step "
        f"applied to an astrophotography image of {target}.\n"
        f"Pick the variant that best improves image quality for its intended purpose.\n"
        f"Pay attention to: background flatness, gradient removal, detail preservation, "
        f"noise level, and natural appearance.{deconvolution_note}"
        f"{prior_note}{physics_note}\n\n"
    )})

    for v in valid:
        content.append({"type": "text", "text": f"[{v['id']}] {v.get('description', v['id'])}"})
        with open(v["jpeg_path"], "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64,
        }})
        detail_path = v.get("detail_jpeg_path")
        if step == "deconvolution" and detail_path and Path(detail_path).exists():
            content.append({"type": "text", "text": f"[{v['id']}] bright-star detail sheet"})
            with open(detail_path, "rb") as f:
                detail_b64 = base64.standard_b64encode(f.read()).decode()
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": detail_b64,
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
            model=MODEL,
            max_tokens=512,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text
        result = json.loads(raw)
        result["input_tokens"] = resp.usage.input_tokens
        result["output_tokens"] = resp.usage.output_tokens
        result["response_id"] = getattr(resp, "id", None)
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
        "historical_selection_rate": 0.0-1.0 (descriptive, not confidence),
        "sample_count": N,
        "win_rates": {...},
    }
    """
    from nas_server.database import get_experiment_priors
    priors = get_experiment_priors(step, object_type)
    if priors.get("sample_count", 0) < 2:
        return {"variant": None, "params": {}, "historical_selection_rate": 0.0,
                "sample_count": 0, "win_rates": {}}

    top = priors.get("top_variant")
    wins = priors.get("variant_wins", {})
    total = sum(wins.values()) or 1
    selection_rate = wins.get(top, 0) / total if top else 0.0

    return {
        "variant": top,
        "params": priors.get("top_variant_avg_params", {}),
        "historical_selection_rate": round(selection_rate, 2),
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
    # fn is "bxt_deconvolve" (issue #389 -- was "bxt" when this engine was
    # PixInsight); bxt_correct_only deliberately maps to a different function
    # (bxt_star_correct) and must NOT match here, since its bxt_stars/
    # bxt_nonstellar are intentionally pinned to 0.0 for pure geometric
    # correction -- adapting them away from 0.0 would defeat its purpose.
    std = next((v for v in variants if v.get("fn") == "bxt_deconvolve" and "standard" in v.get("id", "")), None)
    std_stars = std["params"].get("bxt_stars", 0.50) if std else 0.50
    std_ns    = std["params"].get("bxt_nonstellar", 0.30) if std else 0.30

    adapted = []
    for v in variants:
        v = copy.deepcopy(v)
        if v.get("fn") == "bxt_deconvolve":
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
            # Reuse the sky-target policy: stat sets the image median, while
            # Veralux shifts its sampled output background to target_bg.
            p["target_bg"] = round(stat["target_median"], 3)

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
        if _is_noop_variant(v):
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
        # Real motivating case (M51, 2026-09-12, Henry): "it's hard to tell in
        # those pictures which colors are right ... probably should add the
        # ratios to the decisions" -- an SSSC candidate crushed B/R to 0.56
        # while three peers agreed at ~1.0, invisible by eye in the collage.
        # Add a footer caption with the measured ratio when a candidate's own
        # step_assessor metrics have one (currently color_calibration only).
        footer_h = 26
        has_ratio = any(v.get("metrics", {}).get("color_b_over_r_after") is not None
                        for v in variants_for_review)
        # RMS (2026-09-13, Henry): sssc_calibrate()'s own solve-quality report,
        # a top-level key (not nested in "metrics" — it comes from the
        # function's own return dict via _run_variant, not step_assessor).
        # 'n/a' for any candidate not produced by an SSSC-family function.
        has_rms = any(v.get("applied_rms") is not None for v in variants_for_review)
        panels = []
        for v in variants_for_review:
            img = Image.open(v["jpeg_path"]).convert("RGB")
            img_w, img_h = img.size
            scale = panel_w / img_w
            panel_h = int(img_h * scale)
            img = img.resize((panel_w, panel_h), Image.LANCZOS)
            extra_h = footer_h * (int(has_ratio) + int(has_rms))
            canvas = Image.new("RGB", (panel_w, panel_h + label_h + extra_h), (20, 20, 20))
            draw = ImageDraw.Draw(canvas)
            # Large label letter
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            except Exception:
                font = ImageFont.load_default()
            draw.text((panel_w // 2 - 12, 6), v["label"], fill=(255, 220, 0), font=font)
            canvas.paste(img, (0, label_h))
            try:
                small_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            except Exception:
                small_font = font
            footer_y = label_h + panel_h + 4
            if has_ratio:
                b_r = v.get("metrics", {}).get("color_b_over_r_after")
                g_r = v.get("metrics", {}).get("color_g_over_r_after")
                ratio_text = (f"B/R {b_r:.2f}  G/R {g_r:.2f}"
                              if b_r is not None and g_r is not None else "B/R n/a")
                draw.text((6, footer_y), ratio_text, fill=(255, 255, 255), font=small_font)
                footer_y += footer_h
            if has_rms:
                rms = v.get("applied_rms")
                n_stars = v.get("n_stars")
                rms_text = (f"RMS {rms:.3f} (n={n_stars})" if rms is not None
                            else "RMS n/a")
                draw.text((6, footer_y), rms_text, fill=(255, 255, 255), font=small_font)
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
    Create a manual_review DB record, send collage to Telegram, wait for the
    decision -- a 5 min grace period, then park (release the queue for other
    targets) and wait indefinitely, mirroring crop_review.py's pattern.

    Real gap (Henry, 2026-09-14): the old version gave up after 300s and
    silently fell back to the AI's own pick, so a run that outlived the
    reviewer being awake would run right past every later manual-review step
    unattended -- the opposite of what requesting manual review means. Manual
    review must actually wait for a person, not time-box them.

    Returns the human-selected variant id. May raise ProcessingAbortedError /
    ProcessingRetryError.
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

    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=_REVIEW_GRACE_PERIOD_SECONDS)
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
        f"Waits for you — queue continues after 5 min\n{review_url}"
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
             f"— {review_url} (grace period 300s, then parks and waits for you)")

    # --- Grace period: wait up to 5 min before parking ---
    deadline = datetime.now(timezone.utc) + timedelta(seconds=_REVIEW_GRACE_PERIOD_SECONDS)
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

    # Grace elapsed with no decision yet — park (release the queue for other
    # targets, same as crop_review.py) and wait indefinitely rather than
    # falling back to the AI's own pick. Requesting manual review means the
    # run waits for a person; it does not mean "wait up to 5 minutes."
    from nas_server import queue_manager
    queue_manager.park_for_review(target)
    log.info(f"[review] #{review_id} grace elapsed — queue released, "
             f"waiting for user: {target}/{step} — {review_url}")
    try:
        telegram.send(f"⏸ <b>Manual review parked</b>: {target} / {step}\n"
                      f"Queue continuing. Submit when ready.\n{review_url}")
    except Exception:
        pass

    # Release the pipeline lock while parked: this thread is idle waiting on
    # the user, and holding PIPELINE_LOCK would block every other local
    # autoprocess job. Re-acquire before returning so the rest of this
    # pipeline run continues serialized, matching crop_review.py's pattern.
    from nas_server import auto_process as _ap
    _held = _ap.release_pipeline_lock_for_park()
    try:
        ev.wait()
    finally:
        if _held:
            _ap.reacquire_pipeline_lock_after_park()
    queue_manager.unpark_from_review(target)
    review_events.unregister(review_id)

    review = get_manual_review(review_id)
    status = review["status"] if review else None
    if status == "decided":
        return review["final_winner_variant"]
    elif status == "aborted":
        raise ProcessingAbortedError(f"Manual review aborted: {target}/{step}")
    elif status == "retried":
        raise ProcessingRetryError(step)
    log.warning(f"[review] #{review_id} woke with status={status!r}")
    return None


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------

def _persist_canonical_outcome(experiment_run_id: str) -> str:
    """Persist the recomputable summary without failing an applied result."""
    try:
        from nas_server.experiment_outcomes import derive_run_outcome
        derive_run_outcome(experiment_run_id, persist=True)
        return "available"
    except Exception as outcome_error:
        log.error(
            "[experiment] canonical outcome summary unavailable for %s: %s",
            experiment_run_id,
            outcome_error,
        )
        return "unavailable"


def _is_parallel_gpu_variant(variant: dict) -> bool:
    """Return whether a candidate can share the experiment GPU workspace."""
    gpu_functions = {"bxt_deconvolve", "bxt_star_correct", "denoise_nxt",
                     "starxtract", "starxtract_with_stars"}
    return (variant.get("engine") == "ml_tools_gpu"
            or (variant.get("engine", "seti_astro") == "seti_astro"
                and variant.get("fn") in gpu_functions))

def run_experiment(
    target: str,
    step: str,
    input_fits: str,
    object_type: str = "unknown",
    custom_variants: list[dict] | None = None,
    dry_run: bool = False,
    proc_dir: Path | None = None,
    manual_review: bool = False,
    pipeline_run_id: str | None = None,
    manifest_ref: str | None = None,
    registration_operation_key: str | None = None,
    experiment_question: str | None = None,
    experiment_intent: str = "candidate_comparison",
    precomputed_outputs: dict[str, str] | None = None,
    precomputed_rejections: dict[str, list[str]] | None = None,
    blind_comparison: bool = False,
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
    if precomputed_outputs is not None and set(precomputed_outputs) != {
            str(variant["id"]) for variant in variants}:
        return {"ok": False, "error": "Precomputed output roster does not match candidates"}
    variant_ids = {str(variant["id"]) for variant in variants}
    if precomputed_rejections is not None and not set(precomputed_rejections) <= variant_ids:
        return {"ok": False, "error": "Precomputed rejection roster contains unknown candidates"}

    input_path = Path(input_fits)
    if not input_path.exists():
        return {"ok": False, "error": f"Input not found: {input_fits}"}

    # Group-1 shadow evidence records declared intent, not the adapted or
    # runtime-resolved treatment. Parent hashing and the complete roster commit
    # before candidate 1; a failed registration means zero candidate execution.
    from nas_server.experiment_evidence import register_experiment_run
    try:
        evidence_run_id = register_experiment_run(
            input_path=input_path,
            process_family=step,
            candidates=variants,
            ontology_revision=str(ontology.get("version", "unknown")),
            operation_key=(registration_operation_key
                           or f"experiment-registration:{experiment_run_id}"),
            question=(experiment_question or f"Compare declared variants for {step}"),
            intent=experiment_intent,
            pipeline_run_id=pipeline_run_id,
            manifest_ref=manifest_ref,
        )
    except Exception as exc:
        log.error(f"[experiment] {target}/{step}: evidence registration failed: {exc}")
        return {"ok": False, "error": "Experiment evidence registration failed; no variants executed"}

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

    if proc_dir is None:
        proc_dir = input_path.parent
    from nas_server.experiment_artifacts import (
        apply_winner_projection, decide_winner, finalize_artifact,
        mark_artifact_failed, plan_candidate_artifact, prepare_run_storage,
        register_preview, renew_lease,
    )
    run_paths = None
    if dry_run:
        exp_dir = proc_dir / "experiments" / step / evidence_run_id
        exp_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_paths = prepare_run_storage(
            experiment_run_id=evidence_run_id, base_dir=proc_dir,
            process_family=step)
        exp_dir = run_paths.root

    # Fetch historical priors to guide Claude
    priors = get_experiment_priors(step, object_type)
    learned = get_learned_defaults(step, object_type)
    learning_note = ""
    if learned["sample_count"] >= 3:
        learning_note = (
            f"Based on {learned['sample_count']} past experiments, "
            f"'{learned['variant']}' has been preferred ({int(learned['historical_selection_rate']*100)}% descriptive selection rate)"
        )
        log.info(f"[experiment] {target}/{step}: priors say '{learned['variant']}' "
                 f"({int(learned['historical_selection_rate']*100)}% descriptive selection rate, n={learned['sample_count']})")

    log.info(f"[experiment] {target}/{step}: running {len(variants)} variants "
             f"(dry_run={dry_run})")

    # Execute variants
    variant_results: list[dict] = []
    valid_for_claude: list[dict] = []
    attempt_ids: dict[str, str] = {}
    candidate_artifact_ids: dict[str, str] = {}
    preview_artifact_ids: dict[str, str] = {}
    detail_preview_artifact_ids: dict[str, str] = {}
    planned_artifacts: dict[str, object] = {}
    dispatch_results: dict[str, dict] = {}

    if not dry_run:
        from nas_server.experiment_evidence import start_candidate_attempt
        for v in variants:
            vid = v["id"]
            attempt_id = start_candidate_attempt(
                experiment_run_id=evidence_run_id, declared_variant_id=vid,
                operation_key=f"{evidence_run_id}:{vid}:attempt:0")
            attempt_ids[vid] = attempt_id
            planned = plan_candidate_artifact(
                experiment_run_id=evidence_run_id, candidate_attempt_id=attempt_id,
                declared_variant_id=vid, run_paths=run_paths)
            planned_artifacts[vid] = planned
            candidate_artifact_ids[vid] = planned.artifact_id

        rcastro_variants = [v for v in variants
                            if _is_parallel_gpu_variant(v)
                            and v.get("engine", "seti_astro") == "seti_astro"]
        ml_tools_variants = [v for v in variants
                             if v.get("engine") == "ml_tools_gpu"]
        gpu_variants = rcastro_variants + ml_tools_variants
        parallel = bool(settings.get("experiment_gpu_parallel_enabled", False)
                        and len(gpu_variants) >= 2)
        rcastro_workers = int(settings.get("experiment_gpu_max_concurrency_rcastro", 3))
        ml_tools_workers = int(settings.get("experiment_gpu_max_concurrency_ml_tools", 7))
        workspace = None
        shared_ref = None
        if parallel:
            from nas_server.runpod_workspace import create_workspace
            workspace = create_workspace(run_id=evidence_run_id)
            shared_ref = workspace.stage_input(input_path, name="experiment_parent")

        def dispatch(v):
            vid = v["id"]
            _set_variant_active(target, vid, True)
            try:
                planned = planned_artifacts[vid]
                if precomputed_outputs is not None:
                    source = Path(precomputed_outputs[vid])
                    if not source.exists():
                        return {"ok": False, "elapsed": 0.0,
                                "error": "precomputed candidate artifact missing"}
                    shutil.copy2(source, planned.staging_path)
                    return {"ok": True, "elapsed": 0.0, "error": None,
                            "treatment_observations": {
                                "backend": "precomputed_candidate_import",
                                "method": v.get("fn", "unknown"),
                                "resolved_params": dict(v.get("params", {})),
                                "mask_blend": "not_requested", "fallback": "none"}}
                if workspace is not None and v.get("engine") == "ml_tools_gpu":
                    return _run_variant(
                        v, input_path, planned.staging_path,
                        remote_workspace=workspace, remote_input=shared_ref,
                        remote_output_name=f"candidate_{vid}")
                if workspace is not None and v in gpu_variants:
                    from nas_server.rcastro_gpu import experiment_remote_context
                    with experiment_remote_context(workspace, shared_ref, f"candidate_{vid}"):
                        return _run_variant(v, input_path, planned.staging_path)
                return _run_variant(v, input_path, planned.staging_path)
            finally:
                _set_variant_active(target, vid, False)

        if parallel:
            from contextlib import ExitStack
            from concurrent.futures import ThreadPoolExecutor, wait
            with ExitStack() as pools:
                futures = {}
                if rcastro_variants:
                    pool = pools.enter_context(ThreadPoolExecutor(
                        max_workers=rcastro_workers, thread_name_prefix="experiment-rcastro"))
                    futures.update({pool.submit(dispatch, v): v["id"]
                                    for v in rcastro_variants})
                if ml_tools_variants:
                    pool = pools.enter_context(ThreadPoolExecutor(
                        max_workers=ml_tools_workers, thread_name_prefix="experiment-ml-tools"))
                    futures.update({pool.submit(dispatch, v): v["id"]
                                    for v in ml_tools_variants})
                heartbeat_stop = threading.Event()

                def heartbeat_pending_gpu():
                    while not heartbeat_stop.wait(_LEASE_HEARTBEAT_SECONDS):
                        if any(not future.done() for future in futures):
                            renew_lease(evidence_run_id, run_paths.lease_token)

                heartbeat = threading.Thread(target=heartbeat_pending_gpu, daemon=True)
                heartbeat.start()
                try:
                    for v in variants:
                        if v not in gpu_variants:
                            dispatch_results[v["id"]] = dispatch(v)
                    pending = set(futures)
                    while pending:
                        done, pending = wait(
                            pending, timeout=_LEASE_HEARTBEAT_SECONDS)
                        renew_lease(evidence_run_id, run_paths.lease_token)
                        for future in done:
                            vid = futures[future]
                            try:
                                dispatch_results[vid] = future.result()
                            except Exception as exc:
                                dispatch_results[vid] = {"ok": False, "elapsed": 0.0,
                                                         "error": str(exc)}
                finally:
                    heartbeat_stop.set()
                    heartbeat.join()
            workspace.cleanup()
        else:
            for v in variants:
                dispatch_results[v["id"]] = dispatch(v)

    for v in variants:
        vid = v["id"]
        out_fits = ((run_paths.candidates / f"{vid}.fit") if run_paths
                    else exp_dir / f"{vid}.fit")
        jpg_path = ((run_paths.previews / f"{vid}_preview.jpg") if run_paths
                    else exp_dir / f"{vid}_preview.jpg")
        detail_jpg_path = ((run_paths.previews / f"{vid}_star_detail.jpg") if run_paths
                           else exp_dir / f"{vid}_star_detail.jpg")

        if dry_run:
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
        from nas_server.experiment_evidence import record_attempt_transition
        attempt_id = attempt_ids[vid]
        planned = planned_artifacts[vid]
        out_fits = planned.final_path
        run_result = dispatch_results[vid]
        staged_output_exists = planned.staging_path.exists()
        treatment = _effective_treatment(
            v, run_result, output_exists=staged_output_exists)
        execution_ok = bool(run_result.get("ok"))
        timed_out = (not execution_ok and
                     "timeout" in str(run_result.get("error", "")).lower())
        if execution_ok and staged_output_exists:
            finalize_artifact(planned.artifact_id)
        else:
            mark_artifact_failed(planned.artifact_id, run_result.get("error"))
        output_exists = out_fits.exists()
        record_attempt_transition(
            attempt_id=attempt_id,
            operation_key=f"{attempt_id}:dispatch-result",
            effective_treatment=treatment,
            updates={
                "execution_status": ("succeeded" if execution_ok else
                                     "timed_out" if timed_out else "failed"),
                "artifact_status": "produced" if output_exists else "failed",
                "assessment_status": "not_reached",
                "error_kind": (None if execution_ok else
                               "dispatch_timeout" if timed_out else "dispatch_failure"),
                "error_message": run_result.get("error"),
            },
        )
        vr: dict = {
            "id": vid,
            "description": v.get("description", vid),
            "params": v.get("params", {}),
            **run_result,
        }

        _LINEAR_STEPS = {"crop", "background_extraction", "color_calibration",
                         "deconvolution", "denoise_linear", "sharpen_linear"}
        if run_result["ok"] and out_fits.exists():
            staged_preview = run_paths.staging / f"{planned.artifact_id}.preview.jpg"
            if step in _LINEAR_STEPS:
                _generate_linear_composite(
                    out_fits, staged_preview,
                    linked=(step == "color_calibration"),
                    rect_xywh=(run_result.get("rect_xywh")
                               if step == "color_calibration" else None))
            else:
                _generate_preview_nl(out_fits, staged_preview)
            if staged_preview.exists():
                os.replace(staged_preview, jpg_path)
                preview = register_preview(
                    experiment_run_id=evidence_run_id,
                    candidate_artifact_id=planned.artifact_id,
                    declared_variant_id=vid,
                    preview_path=jpg_path,
                )
                preview_artifact_ids[vid] = preview
            # Physics metrics
            try:
                metrics = _step_assessor.assess_step(input_path, out_fits, step, object_type)
                vr["metrics"] = metrics
                vr["analytically_failed"] = metrics.get("analytically_failed", False)
                try:
                    from nas_server.experiment_measurements import record_measurement_bundle
                    record_measurement_bundle(
                        run_artifact_id=planned.artifact_id,
                        process_family=step,
                        metrics=metrics,
                    )
                except Exception as measurement_error:
                    log.error("[experiment] measurement evidence write failed for %s: %s",
                              vid, measurement_error)
                record_attempt_transition(
                    attempt_id=attempt_id,
                    operation_key=f"{attempt_id}:assessment-result",
                    updates={"assessment_status": ("rejected" if vr["analytically_failed"]
                                                   else "succeeded")},
                )
            except Exception as _ae:
                log.debug(f"[experiment] step_assessor failed for {vid}: {_ae}")
                vr["metrics"] = {}
                vr["analytically_failed"] = False
                try:
                    from nas_server.experiment_measurements import record_measurement_bundle
                    record_measurement_bundle(
                        run_artifact_id=planned.artifact_id,
                        process_family=step,
                        metrics={},
                    )
                except Exception as measurement_error:
                    log.error("[experiment] unavailable measurement evidence write failed "
                              "for %s: %s", vid, measurement_error)
                assessment_status = ("timed_out" if isinstance(_ae, TimeoutError)
                                     else "failed")
                record_attempt_transition(
                    attempt_id=attempt_id,
                    operation_key=f"{attempt_id}:assessment-result",
                    updates={"assessment_status": assessment_status,
                             "error_kind": ("assessment_timeout"
                                            if assessment_status == "timed_out"
                                            else "assessment_failure"),
                             "error_message": str(_ae)},
                )
            if jpg_path.exists():
                vr["jpeg_path"] = str(jpg_path)
                vr["output_path"] = str(out_fits)
                if step == "deconvolution" and _generate_star_detail_preview(
                        out_fits, detail_jpg_path):
                    vr["detail_jpeg_path"] = str(detail_jpg_path)
                    detail_preview_artifact_ids[vid] = register_preview(
                        experiment_run_id=evidence_run_id,
                        candidate_artifact_id=planned.artifact_id,
                        declared_variant_id=vid,
                        preview_path=detail_jpg_path,
                        preview_kind="star-detail-preview",
                    )
                from nas_server.experiment_selection import record_eligibility
                rejection_reasons = list((precomputed_rejections or {}).get(vid, []))
                if vr.get("analytically_failed"):
                    rejection_reasons.append("analytically_disqualifying_measurement")
                record_eligibility(evidence_run_id, vid,
                                   eligible=not rejection_reasons,
                                   reasons=rejection_reasons)
                if not rejection_reasons:
                    valid_for_claude.append({
                        "id": vid,
                        "description": v.get("description", vid),
                        "params": v.get("params", {}),
                        "output_path": str(out_fits),
                        "jpeg_path": str(jpg_path),
                        "detail_jpeg_path": vr.get("detail_jpeg_path"),
                        "metrics": vr.get("metrics", {}),
                        # sssc_calibrate()'s own solve RMS/star count — None for
                        # any candidate not produced by an SSSC-family function.
                        "applied_rms": vr.get("applied_rms"),
                        "n_stars": vr.get("n_stars"),
                        # Ontology-declared family — lets the color-ratio
                        # outlier gate group several declared variants of the
                        # SAME underlying method (e.g. an SSSC background-
                        # equalization race) as one vote rather than one vote
                        # each. Falls back to fn, then id, when undeclared.
                        # See _surface_color_ratio_outliers.
                        "family": v.get("family") or v.get("fn") or vid,
                    })
                else:
                    log.info("[experiment] variant '%s' rejected (%s) — excluded from Claude",
                             vid, ", ".join(rejection_reasons))
        else:
            log.warning(f"[experiment] variant '{vid}' failed: {run_result.get('error')}")
            from nas_server.experiment_selection import record_eligibility
            record_eligibility(evidence_run_id, vid, eligible=False,
                               reasons=["execution_or_artifact_failure"])

        variant_results.append(vr)
        renew_lease(evidence_run_id, run_paths.lease_token)

    # Never invite an evaluator to fabricate a distinction between candidate
    # artifacts whose pixels are identical or differ only at float roundoff.
    collapsed_candidates = _surface_collapsed_candidates(valid_for_claude)
    if collapsed_candidates:
        from nas_server.experiment_selection import record_eligibility
        for duplicate_id, representative_id in collapsed_candidates.items():
            reason = f"near_identical_candidate_output:{representative_id}"
            record_eligibility(evidence_run_id, duplicate_id, eligible=False,
                               reasons=[reason])
            for result in variant_results:
                if result["id"] == duplicate_id:
                    result["analytically_failed"] = True
                    result["collapse_reason"] = reason
                    break
            log.warning("[experiment] %s/%s: candidate '%s' collapsed into '%s'; "
                        "excluded from evaluator comparison", target, step,
                        duplicate_id, representative_id)
        valid_for_claude = [
            candidate for candidate in valid_for_claude
            if candidate["id"] not in collapsed_candidates
        ]

    # Real gate (Henry, 2026-09-13): a color_calibration candidate whose
    # measured ratio is a clear outlier from its peers is disqualified
    # before either the evaluator or manual review ever sees it — the RMS
    # quality gate alone missed the real M51 cc_sssc crush (0.51, well
    # inside the accepted gate, while B/R crushed to 0.560 vs peers at ~1.0).
    color_ratio_outliers = _surface_color_ratio_outliers(valid_for_claude, step)
    if color_ratio_outliers:
        from nas_server.experiment_selection import record_eligibility
        for outlier_id, reason in color_ratio_outliers.items():
            record_eligibility(evidence_run_id, outlier_id, eligible=False,
                               reasons=[reason])
            for result in variant_results:
                if result["id"] == outlier_id:
                    result["analytically_failed"] = True
                    result["color_ratio_outlier_reason"] = reason
                    break
            log.warning("[experiment] %s/%s: candidate '%s' excluded as a "
                        "color-ratio outlier vs peers (%s)", target, step,
                        outlier_id, reason)
        valid_for_claude = [
            candidate for candidate in valid_for_claude
            if candidate["id"] not in color_ratio_outliers
        ]

    if dry_run:
        return {
            "ok": True, "dry_run": True, "target": target, "step": step,
            "evidence_run_id": evidence_run_id,
            "variants": variant_results, "winner": None, "output_path": None,
            "reasoning": "dry run — no variants executed",
            "learning_note": learning_note, "priors": priors,
            "elapsed": time.time() - start_ts,
        }

    if len(valid_for_claude) < 1:
        return {"ok": False, "error": "All variants failed — nothing to compare"}

    # Claude picks winner
    pick = None
    winner_id = None
    reasoning = ""

    from nas_server.experiment_presentations import (
        append_human_review_event, create_presentation, record_evaluator_attempt,
    )
    prior_exposure = ("summary" if not blind_comparison and priors
                      and priors.get("sample_count", 0) >= 3
                      else "none")
    v_metrics = {v["id"]: v.get("metrics", {}) for v in valid_for_claude}
    ai_variants, payload_policy = _prepare_evaluator_presentation(
        valid_for_claude, step, v_metrics)
    ai_items = []
    blind_ids = (_blind_evaluator_roster(ai_variants, v_metrics)[2]
                 if blind_comparison else {})
    for v in ai_variants:
        variant_id = v["id"]
        ai_items.append({"preview_artifact_id": preview_artifact_ids[variant_id],
                         "variant_id": variant_id,
                         "blinded_label": blind_ids.get(variant_id),
                         "display_settings": {"source": "stored_preview",
                                              "view": "full-frame-composite"}})
        if v.get("detail_jpeg_path") and variant_id in detail_preview_artifact_ids:
            ai_items.append({
                "preview_artifact_id": detail_preview_artifact_ids[variant_id],
                "variant_id": variant_id,
                "display_settings": {"source": "stored_preview",
                                     "view": "bright-star-detail-sheet",
                                     "stretch": "asinh-local-sky"},
            })
    ai_presentation_id = create_presentation(
        experiment_run_id=evidence_run_id,
        operation_key=f"{evidence_run_id}:ai-comparison",
        evaluation_mode="informed" if prior_exposure != "none" else "strategy",
        items=ai_items, renderer_revision="experiment-preview/1.1.0",
        renderer_settings={"linear_composite_quality": 88,
                           "normal_target_bg": 0.30,
                           "deep_target_bg": 0.85,
                           "deep_shadow_clip_k": 1.25,
                           "deep_gamma": 0.55,
                           "payload_policy": payload_policy},
        display_transform_scope="candidate_relative",
        historical_prior_exposure=prior_exposure,
        prompt_template_revision="pick-best-variant/1.1.0",
    )

    if len(ai_variants) >= 2 and settings.get("anthropic_api_key"):
        _set_status(target, current_variant="claude_comparison")
        evaluator_variants = ai_variants
        evaluator_metrics = v_metrics
        if blind_comparison:
            evaluator_variants, evaluator_metrics, blind_ids = (
                _blind_evaluator_roster(ai_variants, v_metrics))
        pick = pick_best_variant(target, step, evaluator_variants,
                                 priors=None if blind_comparison else priors,
                                 variant_metrics=evaluator_metrics, payload_prepared=True)
        if pick and blind_comparison:
            reverse_blind = {label: variant for variant, label in blind_ids.items()}
            pick["winner"] = reverse_blind.get(pick.get("winner"), pick.get("winner"))
            pick["scores"] = {reverse_blind.get(key, key): value
                              for key, value in (pick.get("scores") or {}).items()}
        record_evaluator_attempt(
            presentation_id=ai_presentation_id, provider="anthropic",
            model="claude-sonnet-5", settings={"max_tokens": 512,
                                                "payload_policy": payload_policy},
            prompt_template_revision="pick-best-variant/1.1.0",
            status="succeeded" if pick else "failed", parsed_result=pick,
            response_id=pick.get("response_id") if pick else None,
            error_message=None if pick else "comparison returned no result",
        )
        if pick and pick.get("winner") in [v["id"] for v in ai_variants]:
            winner_id = pick["winner"]
            reasoning = pick.get("reasoning", "")
            from nas_server.experiment_selection import record_fact
            record_fact(evidence_run_id, "evaluator_preference", variant_id=winner_id,
                        reason=reasoning or "evaluator preference",
                        payload={"scores": pick.get("scores") or {}})
            # Merge Claude scores back into variant_results
            for vr in variant_results:
                vr["claude_score"] = (pick.get("scores") or {}).get(vr["id"])
    else:
        record_evaluator_attempt(
            presentation_id=ai_presentation_id, provider="anthropic",
            model="claude-sonnet-5", settings={"max_tokens": 512,
                                                "payload_policy": payload_policy},
            prompt_template_revision="pick-best-variant/1.0.0",
            status="comparison_not_performed",
            error_message=("payload budget left fewer than two presentable candidates"
                           if len(ai_variants) < 2 else "evaluator unavailable"),
        )

    # Manual review sees the same presentation-bound eligible artifacts and may
    # supply the comparative decision even when the evaluator is unavailable.
    human_winner = None
    if ((manual_review or settings.get("manual_review_enabled"))
            and len(valid_for_claude) >= 2):
        import random as _rnd
        import string as _string
        # A-Z (26) comfortably covers real eligible-candidate counts; the old
        # fixed "ABCDEFGH" (8) crashed with IndexError once deconvolution's
        # candidate roster grew past 8 eligible survivors (17 real eligible
        # candidates, M51, 2026-09-11 -- BXT/RL/Cosmic Clarity/Parallax
        # combined after PR #749 added six Parallax candidates).
        labels = _string.ascii_uppercase
        # Pull claude scores from variant_results into the comparison list
        vr_scores = {vr["id"]: vr.get("claude_score") for vr in variant_results}
        shuffled = [{**v, "claude_score": vr_scores.get(v["id"])} for v in valid_for_claude]
        _rnd.shuffle(shuffled)
        blind = [{"label": labels[i], **v} for i, v in enumerate(shuffled)]
        human_presentation_id = create_presentation(
            experiment_run_id=evidence_run_id,
            operation_key=f"{evidence_run_id}:human-comparison",
            evaluation_mode="blind",
            items=[{"preview_artifact_id": preview_artifact_ids[v["id"]],
                    "variant_id": v["id"], "blinded_label": v["label"],
                    "display_settings": {"collage_quality": 85}}
                   for v in blind],
            renderer_revision="manual-review-collage/1.0.0",
            renderer_settings={"jpeg_quality": 85, "label_height": 48},
            display_transform_scope="candidate_relative",
            historical_prior_exposure="none",
            prompt_template_revision="manual-review/1.0.0",
        )
        append_human_review_event(
            presentation_id=human_presentation_id, event_type="randomized",
            payload={"ordered_labels": [v["label"] for v in blind]},
        )
        # Map winner_id → label for Claude
        claude_label = (None if blind_comparison else
                        next((v["label"] for v in blind if v["id"] == winner_id), None))
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
                claude_reasoning="" if blind_comparison else reasoning,
                collage_path=collage_path,
                exp_dir=exp_dir,
            )
            if user_winner is not None:
                selected_label = next(v["label"] for v in blind if v["id"] == user_winner)
                append_human_review_event(
                    presentation_id=human_presentation_id, event_type="decision",
                    randomized_label=selected_label, selected_variant_id=user_winner,
                    payload={"source": "human"},
                )
                if user_winner != winner_id:
                    append_human_review_event(
                        presentation_id=human_presentation_id, event_type="disagreement",
                        selected_variant_id=user_winner,
                        payload={"ai_selected_variant_id": winner_id},
                    )
                append_human_review_event(
                    presentation_id=human_presentation_id, event_type="reveal",
                    selected_variant_id=user_winner,
                    payload={"ai_selected_variant_id": winner_id},
                )
                human_winner = user_winner
                from nas_server.experiment_selection import record_fact
                record_fact(evidence_run_id, "human_preference", variant_id=user_winner,
                            reason="blind human preference among eligible candidates")
            else:
                append_human_review_event(
                    presentation_id=human_presentation_id, event_type="timed_out",
                    payload={"reason": "operational window expired"},
                )
        except (ProcessingAbortedError, ProcessingRetryError) as exc:
            append_human_review_event(
                presentation_id=human_presentation_id,
                event_type=("retry" if isinstance(exc, ProcessingRetryError) else "aborted"),
                payload={"error": str(exc)},
            )
            raise

    from nas_server.experiment_selection import choose_operational_selection, record_fact
    selection = choose_operational_selection(
        [v["id"] for v in valid_for_claude],
        evaluator_preference=winner_id,
        human_preference=human_winner,
        fallback_policy=settings.get("experiment_operational_fallback"),
    )
    record_fact(
        evidence_run_id, "comparative_outcome", variant_id=(human_winner or winner_id),
        reason=selection["reason"],
        payload={"outcome": selection["comparative_outcome"]},
    )
    if not selection["ok"]:
        return {"ok": False, "error": selection["reason"],
                "evidence_run_id": evidence_run_id,
                "comparative_outcome": selection["comparative_outcome"]}
    winner_id = selection["variant_id"]
    reasoning = selection["reason"] if not reasoning or selection["source"] != "evaluator" else reasoning
    if selection["source"] == "fallback":
        record_fact(evidence_run_id, "fallback", variant_id=winner_id,
                    policy_id=selection["policy_id"], reason=selection["reason"],
                    payload={"policy_revision": selection["policy_revision"]})
    record_fact(evidence_run_id, "operational_selection", variant_id=winner_id,
                policy_id=selection.get("policy_id"), reason=selection["reason"],
                payload={"source": selection["source"]})

    # Compute margin stats per variant
    all_scores: dict[str, float] = {}
    for vr in variant_results:
        if vr.get("claude_score") is not None:
            all_scores[vr["id"]] = vr["claude_score"]
    all_scores_json = json.dumps(all_scores) if all_scores else None

    for vr in variant_results:
        attempt_id = attempt_ids.get(vr["id"])
        if attempt_id:
            eligible = bool(vr.get("ok") and not vr.get("analytically_failed"))
            record_attempt_transition(
                attempt_id=attempt_id,
                operation_key=f"{attempt_id}:comparison-result",
                updates={"comparison_status": (
                    "selected" if eligible and vr["id"] == winner_id
                    else "not_selected" if eligible else "invalid")},
            )

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
                    analytically_failed=vr.get("analytically_failed", False),
                )
                record_attempt_transition(
                    attempt_id=attempt_ids[vr["id"]],
                    operation_key=f"{attempt_ids[vr['id']]}:legacy-persistence",
                    updates={"persistence_status": "succeeded"},
                )
            except Exception as db_err:
                log.debug(f"[experiment] DB record error: {db_err}")
                record_attempt_transition(
                    attempt_id=attempt_ids[vr["id"]],
                    operation_key=f"{attempt_ids[vr['id']]}:legacy-persistence",
                    updates={"persistence_status": "failed",
                             "error_kind": "persistence_failure",
                             "error_message": str(db_err)},
                )
        elif attempt_ids.get(vr["id"]):
            record_attempt_transition(
                attempt_id=attempt_ids[vr["id"]],
                operation_key=f"{attempt_ids[vr['id']]}:legacy-persistence",
                updates={"persistence_status": "not_applicable"},
            )

    # The DB-selected artifact is authoritative. winner.fit is an atomic,
    # regenerable convenience projection with an explicit DECIDED -> APPLIED seam.
    winner_vr = next((v for v in variant_results if v["id"] == winner_id), None)
    winner_out = winner_vr.get("output_path") if winner_vr else None
    final_out = run_paths.winner
    selected_artifact_id = candidate_artifact_ids.get(winner_id)
    # Register the completed run's compatibility identity only after the selected
    # treatment is known. This is experiment provenance; nominal runs merely build
    # a transient descriptor when consulting it.
    try:
        from nas_server.experiment_compatibility import register_descriptor
        from nas_server.target_prior import build_current_context
        register_descriptor(
            experiment_run_id=evidence_run_id,
            descriptors=build_current_context(target, object_type, input_path),
        )
    except Exception as descriptor_error:
        # Evidence with incomplete identity remains visible but cannot influence a
        # prior. Never fail successful image processing solely on evidence plumbing.
        log.error("[experiment] compatibility descriptor unavailable for %s: %s",
                  evidence_run_id, descriptor_error)
    if winner_out and Path(winner_out).exists() and selected_artifact_id:
        decide_winner(evidence_run_id, selected_artifact_id)
        final_out = apply_winner_projection(evidence_run_id)

    # Persist the canonical Group-6 summary only after attempt statuses and the
    # authoritative operational artifact selection are final.
    # The selected artifact and its attempt evidence are already durable.
    # Summary evidence is recomputable, so expose a gap without unwinding it.
    outcome_summary_integrity = _persist_canonical_outcome(evidence_run_id)

    elapsed = time.time() - start_ts
    _set_status(target, phase="done", winner=winner_id, elapsed=elapsed)

    log.info(f"[experiment] {target}/{step}: winner='{winner_id}' "
             f"in {elapsed:.0f}s — {reasoning[:900]}")

    return {
        "ok": True,
        "experiment_run_id": experiment_run_id,
        "evidence_run_id": evidence_run_id,
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
        "outcome_summary_integrity": outcome_summary_integrity,
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

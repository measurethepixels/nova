"""
Automated post-processing pipeline driven by processing_ontology.json and Claude vision API.

Workflow:
  assess_initial → per-step: condition check → Claude param recommendation →
  seti_astro execution → mini-assess → iterate → assess_final → Telegram summary
"""
import inspect
import json
import logging
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_ONTOLOGY_PATH = Path(__file__).parent / "processing_ontology.json"

_active: dict[str, dict] = {}
_active_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Global PI pipeline lock — ensures only ONE autoprocess pipeline runs at a
# time (PI cannot safely run two concurrent --automation-mode instances).
# Both main.py (direct API) and queue_manager.py (post-stack) must hold this.
# ---------------------------------------------------------------------------
PIPELINE_LOCK = threading.Lock()
_pipeline_active_target: str | None = None
# Thread ident currently holding PIPELINE_LOCK. threading.Lock has no owner
# concept, so we track it ourselves — this lets a parked crop review release the
# lock *only* if this thread actually holds it (the laptop worker runs
# auto_process without the lock; blindly releasing there would corrupt state).
_pipeline_lock_owner: int | None = None


def mark_pipeline_lock_held():
    """Record that the current thread now holds PIPELINE_LOCK."""
    global _pipeline_lock_owner
    _pipeline_lock_owner = threading.get_ident()


def clear_pipeline_lock_held():
    """Clear the recorded PIPELINE_LOCK owner (call before/at release)."""
    global _pipeline_lock_owner
    _pipeline_lock_owner = None


def release_pipeline_lock_for_park() -> bool:
    """Release PIPELINE_LOCK if the current thread holds it. Returns True when
    released so the caller knows to re-acquire after the park wait. Used by a
    parked manual review: the thread is idle waiting for the user, so holding the
    lock would needlessly block every other local autoprocess job."""
    global _pipeline_lock_owner
    if _pipeline_lock_owner == threading.get_ident():
        _pipeline_lock_owner = None
        PIPELINE_LOCK.release()
        return True
    return False


def reacquire_pipeline_lock_after_park():
    """Re-acquire PIPELINE_LOCK after a park wait, blocking until free, then
    re-record ownership so the resumed pipeline runs serialized again."""
    global _pipeline_lock_owner
    PIPELINE_LOCK.acquire()
    _pipeline_lock_owner = threading.get_ident()


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _set_status(target: str, **kw):
    with _active_lock:
        if target not in _active:
            _active[target] = {}
        _active[target].update(kw)


def get_autoprocess_status(target: str) -> dict | None:
    with _active_lock:
        s = _active.get(target)
        return dict(s) if s else None


def get_all_autoprocess_statuses() -> list[dict]:
    with _active_lock:
        return [{"target": t, **v} for t, v in _active.items()]


# ---------------------------------------------------------------------------
# Cooperative abort registry — a running pipeline checks is_abort_requested()
# at each step boundary and bails out cleanly. Works on both VM and laptop
# worker (auto_process runs on both). main.py / laptop_worker.py call
# request_abort(); the step loop honors it.
# ---------------------------------------------------------------------------

_abort_requested: set[str] = set()
_abort_lock = threading.Lock()

# Above this measured Hα/OIII flux ratio the OIII channel is a noise pedestal
# with no morphology, so a bicolor palette cannot produce a black sky — the
# nb_palette step falls back to the narrowband_norm path. See the gate in the
# step loop. (IC 1805 measured 157; genuine bicolor fields run 1–5.)
NB_PALETTE_MAX_FLUX_RATIO = 20.0


def request_abort(target: str) -> None:
    with _abort_lock:
        _abort_requested.add(target)
    log.info(f"[abort] abort requested for '{target}'")


def is_abort_requested(target: str) -> bool:
    with _abort_lock:
        return target in _abort_requested


def clear_abort(target: str) -> None:
    with _abort_lock:
        _abort_requested.discard(target)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_ontology() -> dict:
    with open(_ONTOLOGY_PATH) as f:
        return json.load(f)


def _to_float(v, default: float = 5.0) -> float:
    """Coerce a score value to float; return default for non-numeric (e.g. 'cool', True)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _eval_condition(condition: str, scores: dict) -> bool:
    """Safely evaluate an apply_when expression using current score values."""
    if not condition:
        return True
    try:
        safe_vars = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        return bool(eval(condition, {"__builtins__": {}}, safe_vars))  # noqa: S307
    except Exception:
        return True


def _generate_preview(fits_path: Path, jpg_path: Path) -> bool:
    """Generate a JPEG preview using PI's STF algorithm for accurate linear-data rendering."""
    from nas_server import seti_astro
    return seti_astro.generate_preview_stf(fits_path, jpg_path)


def _generate_preview_nl(fits_path: Path, jpg_path: Path) -> bool:
    """Generate a JPEG preview for non-linear (already-stretched) data. No STF applied."""
    from nas_server import seti_astro
    return seti_astro.generate_preview_nonlinear(fits_path, jpg_path)


def _bxt_undershoot_check(input_path, output_path, block: int = 8,
                          drop_ratio: float = 0.5, min_blob_px: int = 40) -> dict:
    """Detect deconvolution ringing holes: output local minima far BELOW the input's.

    BXT overshoot/undershoot around bright sources digs regions to a fraction of
    the input's local floor (IC 1805 2026-07-07: smooth nebula → black blobs with
    magenta rims) while improving whole-frame FWHM/SNR — invisible to stats gates.
    Blocks the image into `block`-px cells, compares per-cell minima on the mean
    channel; a cell counts as a hole when out_min < drop_ratio × in_min while the
    input floor sits clearly above zero. Fails when a connected-ish cluster
    (≥ min_blob_px cells) of holes exists.

    min_blob_px calibrated 2026-07-07 on the labeled IC 1805 pair: the visually
    catastrophic run (black blobs, Henry-flagged) shows an 86-cell blob; the
    accepted 1.13.1 run (no visible artifact, scored 7.2) shows 24. Threshold 40
    fires on the former, stays quiet on the latter — mild sub-visible ringing is
    tolerated, holes are vetoed.
    """
    import numpy as np
    from astropy.io import fits as _f
    a = _f.getdata(str(input_path), memmap=False).astype(np.float32)
    b = _f.getdata(str(output_path), memmap=False).astype(np.float32)
    if a.shape != b.shape:
        return {"ok": True, "reason": "shape mismatch — skipped"}
    if a.ndim == 3:
        a = a.mean(axis=0); b = b.mean(axis=0)
    H, W = a.shape
    hb, wb = H // block, W // block
    a = a[:hb * block, :wb * block].reshape(hb, block, wb, block)
    b = b[:hb * block, :wb * block].reshape(hb, block, wb, block)
    amin = a.min(axis=(1, 3)); bmin = b.min(axis=(1, 3))
    floor = float(np.median(amin))
    holes = (bmin < drop_ratio * amin) & (amin > 0.25 * floor) & (amin > 1e-6)
    n = int(holes.sum())
    if n < min_blob_px:
        return {"ok": True, "reason": f"no undershoot ({n} cells)"}
    # require spatial clustering (a blob, not scattered noise cells)
    ys, xs = np.nonzero(holes)
    from collections import deque
    seen = set(); best = 0
    hs = set(zip(ys.tolist(), xs.tolist()))
    for start in hs:
        if start in seen:
            continue
        q = deque([start]); seen.add(start); size = 0
        while q:
            y, x = q.popleft(); size += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nb = (y + dy, x + dx)
                    if nb in hs and nb not in seen:
                        seen.add(nb); q.append(nb)
        best = max(best, size)
    if best >= min_blob_px:
        return {"ok": False,
                "reason": f"deconvolution undershoot holes: {n} cells, "
                          f"largest blob {best} cells ({best * block * block}px) "
                          f"below {drop_ratio}x input local floor"}
    return {"ok": True, "reason": f"{n} scattered undershoot cells (no blob)"}


def _objective_check(step_name: str, before: dict, after: dict) -> dict:
    """
    Compare image_analyzer stats before/after a step.
    Returns: {ok: bool, reason: str, should_try_harder: bool, improved: bool, metrics: dict}
    ok=True means the step met objective quality criteria (use result).
    should_try_harder=True means the improvement was minimal — worth iterating.
    improved=True means ANY objective improvement detected (for force_apply steps).
    """
    if not before or not after:
        return {"ok": True, "reason": "no stats", "should_try_harder": False,
                "improved": True, "metrics": {}}

    snr_b = before.get("noise", {}).get("snr", 0)
    snr_a = after.get("noise", {}).get("snr", 0)
    grad_b = before.get("background", {}).get("gradient_severity", 1)
    grad_a = after.get("background", {}).get("gradient_severity", 1)
    fwhm_b = before.get("psf", {}).get("fwhm_median", 4)
    fwhm_a = after.get("psf", {}).get("fwhm_median", 4)
    sharp_b = before.get("spatial_freq", {}).get("sharpness_index", 0)
    sharp_a = after.get("spatial_freq", {}).get("sharpness_index", 0)
    green_b = before.get("color", {}).get("green_excess", 0)
    green_a = after.get("color", {}).get("green_excess", 0)

    metrics = {
        "snr": (round(snr_b, 2), round(snr_a, 2)),
        "gradient": (round(grad_b, 3), round(grad_a, 3)),
        "fwhm": (round(fwhm_b, 2), round(fwhm_a, 2)),
        "sharpness": (round(sharp_b, 4), round(sharp_a, 4)),
        "green_excess": (round(green_b, 5), round(green_a, 5)),
    }

    if step_name == "crop":
        # Crop is a user-chosen framing operation (manual review / saved crop), not a
        # quality-optimized step. It reframes the image — a whole-frame SNR/gradient
        # before/after comparison is meaningless — so it is never objectively gated.
        return {"ok": True, "reason": "crop (framing — not gated)",
                "should_try_harder": False, "improved": True, "metrics": metrics}

    if step_name == "hdr_core_blend":
        # The masked-core blend touches 0.3–2.7% of the frame, so whole-frame
        # SNR/sharpness deltas are structurally blind to it — the "no improvement"
        # veto always fires even when the core genuinely recovered detail (the exact
        # failure that killed global HDR on M 42/M 31). Run/skip is decided by the
        # upstream clip gate; never stats-veto the blend itself.
        return {"ok": True, "reason": "hdr_core_blend (masked core — clip-gated upstream, "
                                      "not stats-gated)",
                "should_try_harder": False, "improved": True, "metrics": metrics}

    if step_name == "background_neutralize":
        # background_neutralize is a COLOUR step — it removes a residual sky cast
        # (dark-corner B/R or G/R drifting from 1.0); it does NOT change the gradient.
        # Gating it on gradient drop (as background_extraction is) guaranteed rejection
        # whenever the gradient was already flat — the exact bug that left IC 1805's blue
        # corner cast (sky B/R 1.25) untouched in the final. Gate on sky-cast improvement,
        # with an SNR-collapse guard so a corrupt output is still vetoed.
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        _cb = (before.get("color") or {})
        _ca = (after.get("color") or {})
        def _sky_imbal(c: dict) -> float:
            return max(abs(c.get("sky_b_over_r", 1.0) - 1.0),
                       abs(c.get("sky_g_over_r", 1.0) - 1.0))
        _imb_b, _imb_a = _sky_imbal(_cb), _sky_imbal(_ca)
        cast_drop = _imb_b - _imb_a
        metrics["sky_cast"] = (round(_imb_b, 3), round(_imb_a, 3))
        ok = cast_drop > 0.02 and snr_ratio > 0.85
        improved = _imb_a < _imb_b - 0.01
        should_try_harder = _imb_a > 0.15
        reason = (f"sky cast {_imb_b:.2f}→{_imb_a:.2f} (Δ{cast_drop:+.2f}) "
                  f"SNR×{snr_ratio:.2f}")

    elif step_name == "background_extraction":
        # Gate on the SKY-ONLY gradient when available: the all-cells metric is
        # dominated by target cells, so it rejected GraXpert runs that flattened
        # the sky perfectly (M 81 2026-06-10: span 0.00022→0.00003 yet metric
        # 0.176→0.248 because the denominator pedestal dropped).
        _gs_b = before.get("background", {}).get("gradient_severity_sky")
        _gs_a = after.get("background", {}).get("gradient_severity_sky")
        _sky_tag = ""
        if _gs_b is not None and _gs_a is not None:
            grad_b, grad_a = _gs_b, _gs_a
            metrics["gradient"] = (round(grad_b, 3), round(grad_a, 3))
            _sky_tag = "sky "
        # Primary goal: reduce gradient severity
        grad_drop = grad_b - grad_a
        grad_pct = grad_drop / max(grad_b, 0.01)
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = grad_drop > 0 and snr_ratio > 0.80
        should_try_harder = grad_pct < 0.10 and grad_b > 0.20
        improved = grad_a < grad_b
        # Large-target exception: for very large/bright targets (globular clusters,
        # extended galaxies) the target itself dominates the gradient metric. After
        # background subtraction the target's halo becomes more visible, which
        # increases the pixel-level gradient metric even though the image genuinely
        # improved. If SNR improves ≥3× (strong signal of real improvement) accept
        # even when gradient appears worse, provided it isn't catastrophically worse
        # (>50%). Confirmed: C 80 GraXpert SNR×3.9 but gradient +27% — rejected in
        # favour of ADBE, but Henry's manual GraXpert result scored 8.2 vs pipeline
        # ADBE 6.8/10 (gradient 8.8/10 vs 5.5/10). 2026-05-24.
        if not ok and snr_ratio >= 3.0 and grad_a <= grad_b * 1.5:
            ok = True
            should_try_harder = False
            improved = True
        # Frame-fill exception (1.17.0, NGC 7000): when nebula fills the frame,
        # every "sky" cell is nebula, so the gradient metric wobbles without
        # meaning — it rejected a GraXpert result on a +0.014 move (0.062→0.076)
        # that measurably FIXED the real corner wash (TL 1.029→1.006 vs median)
        # without eating faint structure (central IQR/med 0.112→0.126; Henry
        # eyeballed both: "with background extraction looks better"). A trivial
        # absolute move at low absolute levels is no evidence of harm — accept
        # when SNR held rather than veto on metric noise.
        if (not ok and abs(grad_a - grad_b) < 0.05 and grad_b < 0.25
                and grad_a < 0.25 and snr_ratio >= 0.90):
            ok = True
            should_try_harder = False
            improved = True
            reason_extra = " | frame-fill: metric delta is noise, SNR held — accepted"
        else:
            reason_extra = ""
        reason = (f"{_sky_tag}gradient {grad_b:.3f}→{grad_a:.3f} "
                  f"({grad_pct*100:.0f}% drop) SNR×{snr_ratio:.2f}{reason_extra}")
        # Per-channel sky-balance regression guard. A background step must not INTRODUCE
        # a sky colour cast — M 108's background_neutralize pushed the neutral sky green,
        # and a neutralize that leaves a blue cast (NGC 2244 sky B/R 1.46) under-delivered.
        # The gradient metric is colourblind, so check the sky-corner channel balance.
        _cb = (before.get("color") or {})
        _ca = (after.get("color") or {})
        if _cb.get("is_color") and _ca.get("is_color"):
            def _sky_imbal(c: dict) -> float:
                return max(abs(c.get("sky_b_over_r", 1.0) - 1.0),
                           abs(c.get("sky_g_over_r", 1.0) - 1.0))
            _imb_b, _imb_a = _sky_imbal(_cb), _sky_imbal(_ca)
            if _imb_a > _imb_b + 0.05 and _imb_a > 0.12:
                ok = False
                improved = False
                reason += f" | sky cast WORSENED {_imb_b:.2f}→{_imb_a:.2f} (reject)"
            elif _imb_a > 0.15:
                should_try_harder = True
                reason += f" | residual sky cast {_imb_a:.2f}"

    elif step_name == "deconvolution":
        # Primary: reduce FWHM (sharpen); secondary: maintain SNR
        fwhm_drop = (fwhm_b - fwhm_a) / max(fwhm_b, 0.01)
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        sharp_gain = (sharp_a - sharp_b) / max(sharp_b, 1e-9)
        ok = (fwhm_drop > 0.04 or sharp_gain > 0.02) and snr_ratio > 0.75
        should_try_harder = fwhm_drop < 0.02 and sharp_gain < 0.01
        improved = fwhm_a < fwhm_b or sharp_a > sharp_b
        reason = (f"FWHM {fwhm_b:.2f}→{fwhm_a:.2f}px "
                  f"({fwhm_drop*100:.0f}% drop) sharpness×{1+sharp_gain:.2f} SNR×{snr_ratio:.2f}")

    elif step_name == "denoise_linear":
        # Primary: improve SNR; secondary: don't smear stars.
        # Dense star fields (globular clusters, rich open clusters) naturally have higher
        # FWHM growth from denoising because the algorithm smooths across tightly packed stars.
        # For these targets a follow-up star_sharpen step corrects the star bloat, so we
        # allow up to 40% FWHM growth when star count is high (>300 detected stars).
        # Normal targets keep the 12% limit.
        snr_gain = (snr_a - snr_b) / max(snr_b, 0.01) if snr_b > 0 else 0
        fwhm_grow = (fwhm_a - fwhm_b) / max(fwhm_b, 0.01) if fwhm_b > 0 else 0
        _star_count_a = after.get("psf", {}).get("star_count", 0) if after else 0
        _dense = _star_count_a > 300
        _fwhm_limit = 0.40 if _dense else 0.12
        ok = snr_gain > 0.03 and fwhm_grow < _fwhm_limit
        should_try_harder = snr_gain < 0.01
        improved = snr_a > snr_b
        reason = (f"SNR {snr_b:.1f}→{snr_a:.1f} ({snr_gain*100:.0f}% gain) "
                  f"FWHM×{1+fwhm_grow:.2f} (limit={'40%' if _dense else '12%'}, "
                  f"stars={_star_count_a})")

    elif step_name == "crop":
        # Crop removes stacking edge artifacts — a purely geometric/structural step.
        # Gradient is NOT a valid objective for crop: large targets (e.g. Omega Centauri)
        # dominate the gradient metric and cause spurious failures. Claude is the gate
        # for whether to crop and how much. If Claude said crop, always accept.
        ok = True
        should_try_harder = False
        improved = True
        reason = f"crop accepted (structural step — gradient not a valid objective)"

    elif step_name in ("color_sat", "color_boost"):
        # Goal: boost saturation without damaging SNR. Post-stretch step — accept as long
        # as SNR doesn't crater. Colour metrics aren't meaningful here (we're intentionally
        # increasing colour cast). Always improved=True — if force_apply, just accept.
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.85
        should_try_harder = False
        improved = True  # saturation is always an improvement if SNR holds
        reason = f"{step_name} SNR×{snr_ratio:.2f}"

    elif step_name == "scnr":
        # Primary: reduce green excess without destroying SNR
        green_drop = green_b - green_a
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.85
        should_try_harder = green_b > 0.005 and green_drop < green_b * 0.3
        improved = green_a < green_b
        reason = (f"green_excess {green_b:.5f}→{green_a:.5f} SNR×{snr_ratio:.2f}")

    elif step_name == "sky_green_rebalance":
        # Sky-only green neutralization — self-gates internally (no-op when sky G/R is
        # already neutral) and never touches object/star colour, so the only failure
        # worth vetoing is SNR collapse. green_excess may read ~flat (whole-frame metric,
        # change confined to sky) — don't require it to move.
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.85
        should_try_harder = False
        improved = green_a <= green_b or snr_ratio >= 0.95
        reason = (f"green_excess {green_b:.5f}→{green_a:.5f} SNR×{snr_ratio:.2f}")

    elif step_name == "color_calibration":
        # Goal: neutralise green cast, improve color balance, without damaging SNR.
        # PI SPCC can't always fix globular colour (few background galaxy references)
        # so we accept as long as SNR holds; flag should_try_harder if cast remains.
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        green_toward_zero = abs(green_a) < abs(green_b) or abs(green_a) < 0.002
        ok = snr_ratio > 0.90
        should_try_harder = not green_toward_zero and abs(green_b) > 0.008
        improved = snr_ratio >= 0.95 and (green_toward_zero or abs(green_a) < 0.003)
        reason = (f"SNR×{snr_ratio:.2f} green_excess {green_b:.5f}→{green_a:.5f}")

    elif step_name == "star_sharpen":
        # Goal: improve FWHM (rounder/tighter stars) without losing SNR.
        # BXT correct-only doesn't touch background, so SNR should be nearly unchanged.
        # Accept if FWHM drops at least 2% OR stays flat (correct_only may not change
        # measured FWHM dramatically on already-round stars — accept if SNR holds).
        fwhm_drop = (fwhm_b - fwhm_a) / max(fwhm_b, 0.01)
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.90  # SNR must hold; FWHM improvement is a bonus
        should_try_harder = fwhm_drop < 0.01 and snr_ratio < 0.95
        improved = fwhm_a <= fwhm_b and snr_ratio >= 0.95
        reason = (f"FWHM {fwhm_b:.2f}→{fwhm_a:.2f}px "
                  f"({fwhm_drop*100:.0f}% drop) SNR×{snr_ratio:.2f}")

    elif step_name in ("curves", "narrowband_norm"):
        # Goal: adjust tone/channel balance without damaging SNR. Aesthetic/config-driven
        # steps (narrowband palette is a user choice) — loose SNR gate only, NOT objectively
        # score-gated. Accept unless SNR catastrophically collapses. See
        # [[feedback-aesthetic-steps]] and [[project-physics-default-pipeline]].
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.80
        should_try_harder = False
        improved = True  # not pixel-score-gated (aesthetic / palette choice)
        reason = f"{step_name} SNR×{snr_ratio:.2f}"

    elif step_name == "hdr_compression":
        # Goal: compress highlights while preserving local contrast (sharpness_index).
        # sharpness dropping > 12% = over-flattening; SNR can drop up to 25% (non-linear).
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        sharp_ratio = sharp_a / max(sharp_b, 1e-9) if sharp_b > 1e-9 else 1.0
        ok = snr_ratio > 0.75 and sharp_ratio > 0.88
        should_try_harder = sharp_ratio < 0.95 or snr_ratio < 0.85
        improved = snr_ratio > 0.80 and sharp_ratio >= 0.95
        reason = (f"SNR×{snr_ratio:.2f} sharpness×{sharp_ratio:.2f}")

    elif step_name in ("noise_reduction", "denoise_nonlinear"):
        # Post-stretch denoise: goal is SNR gain. FWHM is intentionally excluded here —
        # this step typically runs on a *starless* image (after remove_stars_linear), so
        # FWHM measures nebula structure width, not star sharpness; the check is meaningless
        # and was incorrectly vetoing valid denoise results (e.g. NGC 7000 FWHM ×1.60
        # on starless nebula). SNR-only gate.
        snr_gain = (snr_a - snr_b) / max(snr_b, 0.01) if snr_b > 0 else 0
        # Relative grain (σ/bg) on the *result* — the same proxy the stretch ranker uses
        # (auto_process.py ~713). A marginal SNR read can still leave a visibly grainy frame
        # on faint targets (NGC 7635, SH 2-273 rejected a valid denoise as linear "no
        # improvement"). When the output is still grainy and the denoise didn't cost SNR,
        # keep it and push harder rather than reverting to the grainier input. Guard the
        # ratio on a healthy sky (bg > 0.04) so a crushed background can't inflate it.
        _bg_rms_a = after.get("noise", {}).get("background_rms", 0.0)
        _bg_lvl_a = after.get("histogram", {}).get("median", 0.0)
        grain_a = (_bg_rms_a / _bg_lvl_a) if _bg_lvl_a > 0.04 else 0.0
        _grainy = grain_a > 0.18
        ok = snr_gain > 0.01 or (_grainy and snr_a >= snr_b * 0.97)
        should_try_harder = (0 < snr_gain < 0.03) or _grainy
        improved = snr_a > snr_b or _grainy
        reason = (f"SNR {snr_b:.1f}→{snr_a:.1f} ({snr_gain*100:.0f}% gain)"
                  f" grain={grain_a:.2f}{' grainy' if _grainy else ''}")

    elif step_name == "halo_suppression":
        # Dense-field aliasing guard (batch eval 2026-07-07 Pattern 1): on LP data
        # with 16k+ stars the ratio-based colour subtraction flags nebula adjacent
        # to overlapping halos as "halo colour" and paints vivid magenta/cyan/green
        # RINGS across the frame — bg noise DOUBLED (+129%/+157%) and p99.9 pegged
        # to ~1.0 in both v1.17.2 IC 1805 runs (cost −1.3 overall on one). Veto on
        # a bg-noise blowup or a highlight peg; hard_veto so a positive score delta
        # can't resurrect the damaged output (same class as decon undershoot).
        _rms_b = before.get("noise", {}).get("background_rms", 0.0)
        _rms_a = after.get("noise", {}).get("background_rms", 0.0)
        _noise_ratio = (_rms_a / _rms_b) if _rms_b > 0 else 1.0
        _p999_b = before.get("histogram", {}).get("p999", 0.0)
        _p999_a = after.get("histogram", {}).get("p999", 0.0)
        _pegged = _p999_a > 0.995 and _p999_b < 0.98
        # Output contract (workflow 1.21.0, CRITICAL in TWO consecutive batch evals
        # with different modes): halo suppression must not move the GLOBAL sky.
        # 07-13 2a: galaxy sky CRUSHED (−0.051/−0.066, NGC 4244/M 66); 07-13 2b:
        # level-3 sky RAISED (+0.042/+0.050, NGC 4565/M 108 — inversion bug). One
        # symmetric bound catches both: |sky delta| ≤ 0.020. p99.9 must also not
        # rise (halo redistribution mode: p99.9 rose while sky crushed).
        _sky_b = before.get("background", {}).get("sky_background")
        _sky_a = after.get("background", {}).get("sky_background")
        _sky_delta = (_sky_a - _sky_b) if (_sky_a is not None and _sky_b is not None) \
            else 0.0
        _sky_moved = abs(_sky_delta) > 0.020
        _p999_rose = _p999_a > _p999_b + 0.01
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = (_noise_ratio <= 1.5 and not _pegged and snr_ratio > 0.85
              and not _sky_moved and not _p999_rose)
        should_try_harder = False
        improved = ok and snr_ratio >= 0.95
        reason = (f"bg_noise×{_noise_ratio:.2f} sky {_sky_delta:+.3f} "
                  f"p99.9 {_p999_b:.3f}→{_p999_a:.3f} SNR×{snr_ratio:.2f}")
        if not ok and (_noise_ratio > 1.5 or _pegged or _sky_moved or _p999_rose):
            _mode = ("sky-raise" if _sky_delta > 0.020 else
                     "sky-crush" if _sky_delta < -0.020 else
                     "p99.9-rise" if _p999_rose else "aliasing")
            reason = f"halo output contract [{_mode}]: {reason}"
            return {"ok": False, "reason": reason, "should_try_harder": False,
                    "improved": False, "hard_veto": True, "metrics": metrics}

    else:
        # Generic: SNR shouldn't drop badly; only accept if SNR holds within 5%
        snr_ratio = snr_a / max(snr_b, 0.01) if snr_b > 0 else 1.0
        ok = snr_ratio > 0.85
        should_try_harder = False
        improved = snr_ratio >= 0.95  # was True — always accepted; now requires SNR to hold
        reason = f"SNR×{snr_ratio:.2f}"

    return {"ok": ok, "reason": reason, "should_try_harder": should_try_harder,
            "improved": improved, "metrics": metrics}


# ---------------------------------------------------------------------------
# Physics-based quality scoring (Claude-free for fully objective steps)
# ---------------------------------------------------------------------------

# Steps whose ALL quality_impact dimensions map directly to image_analyzer
# metrics. For these steps Claude is never called for dimension scoring,
# saving ~3–5 API calls per step across the pipeline.
_PHYSICS_SCORE_STEPS: frozenset = frozenset({
    "denoise_linear",        # noise          → SNR
    "background_extraction", # gradient       → gradient_severity
    "adbe",                  # gradient       → gradient_severity
    "deconvolution",         # star_roundness → FWHM; detail_level → sharpness_index
    "noise_reduction",       # noise          → SNR
    "denoise_nonlinear",     # noise          → SNR
    "remove_pedestal",       # dynamic_range  → histogram.dynamic_range (p99−p01)
    "cosmetic_correction",   # noise          → SNR
    "scnr",                  # color_balance  → green_excess (scnr-specific)
    "star_sharpen",          # star_roundness → FWHM (should decrease after stellar CC sharpen)
    "sky_green_rebalance",   # color_balance  → green_excess (sky-only SCNR substitute)
    "hdr_core_blend",        # dynamic_range/detail_level — masked blend; whole-frame
                             # metrics read ~flat by design, no visual call needed
})


# Subjective post-stretch steps that need a visual judgement call (Claude) to apply
# safely — they have no reliable objective metric and overdoing them degrades the
# image (e.g. color_boost amplifying background chroma noise). In physics-only mode
# (Claude unavailable → recommend_processing_step returns None) these are SKIPPED
# rather than applied with blind defaults, so a no-API run stays conservative and
# trustworthy. Objective physics-scored steps still run with defaults.
_RISKY_NONLINEAR_STEPS: frozenset = frozenset({
    "color_boost", "color_sat", "clahe", "curves", "dark_enhance",
    "hdr_compression", "halo_suppression", "lhe", "usm", "hdrmt", "curves_pi",
})

# Aesthetic post-stretch steps that must NOT be objectively score-gated — they raise
# chroma / local contrast on purpose, which the SNR/objective metrics misread. Physics
# decides to run them; they are then APPLIED, guarded only by the catastrophic stats-veto
# (SNR collapse → real corruption). See [[feedback-aesthetic-steps]].
_AESTHETIC_APPLY_STEPS: frozenset = frozenset({"color_boost", "color_sat"})

# WS5 reduce-only corrective: the single numeric "strength" knob to scale DOWN when the
# Sonnet final eval asks to dial back a step. A step with no entry here (e.g. color_boost,
# whose strength is preset-driven) is DROPPED instead of scaled — still strictly reduce-only.
# See [[project-physics-default-pipeline]].
_CORRECTIVE_REDUCE_KEY: dict = {
    "curves": "amount",
    "hdr_compression": "compression_factor",
    "hdr_core_blend": "compression_factor",
    "dark_enhance": "boost_factor",
    "clahe": "clip_limit",
    "scnr": "amount",
    "sky_green_rebalance": "amount",
    "color_sat": "color_sat_boost",
}

# Only these aesthetic-BOOST steps may ever be a corrective candidate. The corrective loop
# scales a step down (or drops a preset boost) — that is safe for boosts but DESTRUCTIVE for
# calibration/structural steps. A globular often physics-gates out every boost, leaving only
# calibration as "the last standard step"; without this allowlist the loop would hand Claude
# color_calibration and a factor<1 request would escalate to dropping SPCC entirely, ruining a
# final Claude already graded well. Calibration/registration/denoise/star/stretch steps are
# NEVER eligible. See [[project-physics-default-pipeline]].
_CORRECTIVE_ELIGIBLE_STEPS: frozenset = (
    frozenset(_CORRECTIVE_REDUCE_KEY) | _AESTHETIC_APPLY_STEPS | {"halo_suppression"}
)


def _physics_should_run(step_name: str, stats: dict | None,
                        object_type: str) -> tuple[bool, dict, str]:
    """Physics gate for optional/risky post-stretch steps (WS1).

    Returns ``(run, params, reason)``. ``params`` are measurement-driven overrides from
    tool_params, merged over the ontology defaults by the caller. This REPLACES the
    per-step Claude ``recommend_processing_step`` go/no-go: physics decides whether a step
    earns its keep and supplies its parameters — no per-step API call. Steps not gated
    here return ``(True, {}, ...)`` and run with their existing defaults. color_boost/
    color_sat stay standard (conservative + masked, applied via [[feedback-aesthetic-steps]]).
    hdr_compression keeps its own inline starless clip-gate upstream; here it only supplies
    params. See [[project-physics-default-pipeline]].
    """
    if not stats:
        return True, {}, "no stats — run with defaults"

    ot = (object_type or "").strip().lower().replace(" ", "_")
    _is_galaxy = "galaxy" in ot
    _is_cluster = "cluster" in ot

    sf = stats.get("spatial_freq", {})
    sharp = sf.get("sharpness_index", 0.5)
    snr = stats.get("noise", {}).get("snr", 0.0)
    median = stats.get("histogram", {}).get("median", 0.20)
    star = stats.get("stars", {})
    fwhm = star.get("fwhm_median", 3.0)
    large = star.get("large_star_fraction", 0.1)

    from nas_server import tool_params as _tp

    def _params(fn) -> dict:
        try:
            return fn(stats, object_type)
        except Exception as _pe:
            log.debug(f"[autoprocess] _physics_should_run {step_name} param calc failed: {_pe}")
            return {}

    if step_name == "clahe":
        # Local-contrast enhancement helps a SOFT frame; on an already-crisp image it only
        # spots noise. Clusters are mostly stars — no extended structure to enhance.
        if _is_cluster:
            return False, {}, "cluster — no extended structure for CLAHE"
        floor = 0.30 if _is_galaxy else 0.28
        if sharp >= floor:
            return False, {}, f"already crisp (sharpness {sharp:.2f} ≥ {floor})"
        return True, _params(_tp.compute_clahe), f"soft frame (sharpness {sharp:.2f} < {floor})"

    if step_name == "dark_enhance":
        # Shadow boost pays off only when shadows carry real signal; on a low-SNR frame it
        # amplifies noise. Clusters have minimal diffuse shadow structure.
        if _is_cluster:
            return False, {}, "cluster — minimal shadow structure"
        shadow_snr = snr * max(0.3, min(1.5, median / 0.20))
        # Galaxies (outer halo / IFN) and nebulae (faint reflection/emission veil) carry
        # real but low-SNR signal in the shadows that the blanket SNR<12 veto wrongly
        # suppressed (IC 4592, M 109, NGC 4565, NGC 6914, SH 2-273). Lower the bar for these
        # extended targets; keep the 12 floor for everything else as a noise-floor guard so
        # we never amplify a genuinely noise-dominated frame.
        _extended = _is_galaxy or "nebula" in ot
        _floor = 5.0 if _extended else 12.0
        if shadow_snr < _floor:
            return False, {}, f"shadow-SNR proxy low ({shadow_snr:.1f} < {_floor:.0f})"
        return True, _params(_tp.compute_dark_enhance), f"shadow-SNR proxy {shadow_snr:.1f} (floor {_floor:.0f})"

    if step_name == "halo_suppression":
        severity = max(0.0, min(3.0, fwhm / 6.0 + large * 1.5))
        if severity < 0.6:
            return False, {}, f"no significant halos (severity {severity:.2f} < 0.6)"
        p = _params(_tp.compute_halo_suppress)
        # Dense star fields (Cygnus Milky-Way fields, globular clusters) have so many
        # overlapping stellar halos that an aggressive reduction_level eats real stars and
        # darkens the field (NGC 6914). Cap at the gentlest level when the field is dense.
        _star_count = star.get("star_density", 0)
        if _is_cluster or _star_count > 1000:
            if int(p.get("reduction_level", 1)) > 1:
                p["reduction_level"] = 1
            return True, p, f"halo severity {severity:.2f} (dense field stars={_star_count} — level capped at 1)"
        # Sparse-field cap (workflow 1.21.0, batch eval 07-13 2b): reduction_level=3
        # on sparse galaxy fields RAISED the sky (+0.042/+0.050, NGC 4565/M 108 —
        # confirmed inversion). Level 3 is for crowded severe-halo fields; sparse
        # fields cap at 2. The output contract in _objective_check backstops this.
        if _star_count < 1000 and int(p.get("reduction_level", 1)) > 2:
            p["reduction_level"] = 2
            return True, p, (f"halo severity {severity:.2f} (sparse field "
                             f"stars={_star_count} — level capped at 2, "
                             f"level-3 sky-raise bug)")
        return True, p, f"halo severity {severity:.2f}"

    if step_name in ("hdr_compression", "hdr_core_blend"):
        # Clusters are point-source fields with no extended bright core to tone-map —
        # HDR compression only flattens star contrast and lifts background noise.
        if _is_cluster:
            return False, {}, "cluster — no extended core to compress"
        # Otherwise run/skip is decided by the inline starless clip-gate upstream; supply params only.
        return True, _params(_tp.compute_hdr_compression), "params from compute_hdr_compression"

    if step_name == "usm":
        p = _params(_tp.compute_usm)
        if p.get("usm") is False:
            return False, {}, f"frame not sharp enough for USM (sharpness {sharp:.2f})"
        return True, p, "params from compute_usm"

    # curves (data-driven points injected later), color_boost/color_sat (aesthetic),
    # and every other step: run standard with ontology defaults.
    return True, {}, "standard"


def _physics_score_dimensions(step_name: str, stats_after: dict,
                               quality_dims: list) -> dict | None:
    """
    Compute quality-dimension scores from image_analyzer physics metrics alone.
    Returns {dim: score 1–10} if ALL dims in quality_dims are computable, else None.

    When None is returned, the caller should fall back to Claude.

    Score calibration (1–10, same scale as Claude):
      noise          SNR:  25→9.7, 15→6.0,  5→2.3
      gradient       sev:  0.02→9.2, 0.10→6.0, 0.25→1.0
      star_roundness FWHM: 1.8→8.0, 2.5→6.3, 4.0→2.5
      detail_level   si:   0.30→9.0, 0.20→6.5, 0.10→4.0
      dynamic_range  dr:   0.40→10,  0.20→5.0, 0.08→2.0
      color_balance  ge:   0.000→10, 0.005→6.0, 0.010→2.0
    """
    if step_name not in _PHYSICS_SCORE_STEPS or not stats_after:
        return None

    snr   = stats_after.get("noise", {}).get("snr", 0)
    _bg   = stats_after.get("background", {})
    # sky-only gradient when available — the all-cells metric is object-dominated
    grad  = _bg.get("gradient_severity_sky", _bg.get("gradient_severity", 0.25))
    fwhm  = stats_after.get("psf", {}).get("fwhm_median", 3.5)
    sharp = stats_after.get("spatial_freq", {}).get("sharpness_index", 0.1)
    green = stats_after.get("color", {}).get("green_excess", 0)
    dr    = stats_after.get("histogram", {}).get("dynamic_range", 0.1)

    def _sc(v: float) -> float:
        return round(max(1.0, min(10.0, v)), 2)

    _dim_map: dict = {
        "noise":          lambda: _sc(snr * 0.37 + 0.4),
        "gradient":       lambda: _sc(10.0 - grad * 40),
        "star_roundness": lambda: _sc(12.5 - fwhm * 2.5),
        "detail_level":   lambda: _sc(sharp * 25 + 1.5),
        "dynamic_range":  lambda: _sc(dr * 25),
        "color_balance":  lambda: _sc(10.0 - abs(green) * 800),
    }

    scores: dict = {}
    for dim in quality_dims:
        fn = _dim_map.get(dim)
        if fn is None:
            return None  # dim not physics-computable — fall back to Claude
        scores[dim] = fn()
    return scores


def _stack_depth_factor(target: str, frame_count: int | None) -> float:
    """Integration-depth factor in [0,1] used to gate stretch aggressiveness.

    1.0 = deep / high-SNR stack (apply the full stretch); →0.0 = thin / noisy stack
    (cap alpha + relax the under-stretch floor so we don't pull the noise floor up
    chasing the highlight target). Driven by frame count (authoritative N) and the
    stack's own measured SNR (stacking_runs.snr_stack) — whichever is worse softens
    the stretch. Unknown frame count → assume full depth (don't soften on missing data).
    """
    n = int(frame_count or 0)
    n_f = 1.0 if n <= 0 else max(0.0, min(1.0, (n - 12.0) / 48.0))   # 12 frames→0, 60+→1
    snr_f = 1.0
    try:
        from nas_server import database as _db
        with _db.get_conn() as _c:
            r = _c.execute(
                "SELECT snr_stack FROM stacking_runs WHERE target=? AND success=1 "
                "ORDER BY id DESC LIMIT 1", (target,)).fetchone()
        if r and r["snr_stack"]:
            snr_f = max(0.0, min(1.0, (float(r["snr_stack"]) - 10.0) / 25.0))  # 10→0, 35+→1
    except Exception:
        pass
    return round(min(n_f, snr_f), 3)


def _compute_stretch_stats(fits_path: Path, object_type: str = "galaxy",
                           depth: float = 1.0, frame_fill: bool = False) -> dict:
    """
    Compute post-stretch pixel statistics for stretch quality assessment.
    Returns bg_level (corner sky estimate), p95, p99, and whether background is on-target.

    `depth` (0..1, from _stack_depth_factor) relaxes the under-stretch p99 floor for
    thin/noisy stacks: a shallow stack that can't reach the highlight target without
    amplifying noise shouldn't be flagged as under-stretched.

    `frame_fill` (from _frame_fill_detect, spec_frame_fill_detection.md): the target
    fills the frame, so corners are faint nebula, not sky. bg_level / bg_noise then
    anchor on the darkest-percentile pixels (p1–p3 luminance band — dust lanes /
    true dark) instead of corners, and the stats gain a p50 midtone target
    (0.15–0.20 by depth; the IC 1805 manual measured p50 0.244 vs pipeline 0.075).
    """
    try:
        from astropy.io import fits as _fits
        import numpy as np
        with _fits.open(str(fits_path)) as h:
            d = h[0].data.astype(np.float32)
        if d.ndim == 3 and d.shape[0] == 3:
            d = np.moveaxis(d, 0, -1)
        h_, w_ = d.shape[:2]
        margin = max(h_ // 20, w_ // 20, 50)
        corners = np.concatenate([
            d[:margin, :margin].ravel(), d[:margin, -margin:].ravel(),
            d[-margin:, :margin].ravel(), d[-margin:, -margin:].ravel()
        ])
        bg = float(np.median(corners))
        lum = d.mean(axis=-1) if d.ndim == 3 else d
        p50 = float(np.percentile(lum, 50))
        if frame_fill:
            # Dark anchor: "sky" = the darkest real structure, not the corners.
            p1, p3 = np.percentile(lum, [1, 3])
            dark = lum[(lum >= p1) & (lum <= p3)]
            if dark.size >= 500:
                bg = float(np.median(dark))
        # Background grain: σ of the sky corners — the direct "how noisy did the stretch
        # leave the sky" signal the picker was missing. An over-aggressive stretch on
        # faint data lifts grain into the sky band (and clips the median to ~0), which
        # histogram placement alone rewards; σ catches it where a robust MAD wouldn't
        # (>50% of corners clip to exactly 0, so MAD→0 but σ stays high). Pooled over
        # 4 corners of ≥2500 px each, a stray corner star can't dominate it.
        # See [[project-physics-default-pipeline]].
        bg_noise = float(np.std(corners))
        if frame_fill:
            # Grain on the dark anchor, not corners — corner σ on a frame-filler is
            # nebula STRUCTURE; charging it as grain double-penalises exactly the
            # bright placements this mode exists to allow.
            p1n, p5n = np.percentile(lum, [1, 5])
            dark_n = lum[(lum >= p1n) & (lum <= p5n)]
            if dark_n.size >= 500:
                bg_noise = float(np.std(dark_n))
        # Per-channel sky balance — corner-median R/G/B. The luminance bg above is
        # colour-blind, so a residual sky cast (NGC 2244 NBN sky B/R 1.46) is invisible
        # to it but is exactly the colour error the vision grader keeps missing. Feed it
        # to the grader (#8) so a real cast is scored.
        sky_rgb = None
        if d.ndim == 3 and d.shape[-1] >= 3:
            def _corner_ch(ci):
                return float(np.median(np.concatenate([
                    d[:margin, :margin, ci].ravel(), d[:margin, -margin:, ci].ravel(),
                    d[-margin:, :margin, ci].ravel(), d[-margin:, -margin:, ci].ravel()
                ])))
            _sr, _sg, _sb = _corner_ch(0), _corner_ch(1), _corner_ch(2)
            _rden = max(_sr, 1e-5)
            sky_rgb = {
                "sky_r": _sr, "sky_g": _sg, "sky_b": _sb,
                "sky_b_over_r": _sb / _rden,
                "sky_g_over_r": _sg / _rden,
            }
        flat = d.ravel()
        p95 = float(np.percentile(flat, 95))
        p99 = float(np.percentile(flat, 99))
        # Per-type sky background thresholds.
        # Emission nebulae: corners pick up diffuse Ha/OIII, so genuine emission fill
        # can read 0.10-0.16 even on a well-stretched image — don't flag as "too bright".
        # p99 (< 0.70) is the real under-stretch indicator for nebulae.
        _targets = {
            "galaxy":            (0.05, 0.08),
            "emission_nebula":   (0.06, 0.16),   # wide — corners contain real emission
            "reflection_nebula": (0.06, 0.11),
            "planetary_nebula":  (0.05, 0.09),   # compact — corners are true sky
            "supernova_remnant": (0.05, 0.13),
            "globular_cluster":  (0.04, 0.08),
            "open_cluster":      (0.04, 0.08),
            "nebula":            (0.06, 0.14),   # generic fallback for nebula subtype
        }
        # Normalise subtype strings (e.g. "emission nebula" → "emission_nebula")
        ot = object_type.strip().lower().replace(" ", "_") if object_type else "galaxy"
        # Coerce broad "nebula" keyword embedded in any subtype string
        if ot not in _targets:
            if "nebula" in ot:
                ot = "nebula"
            elif "cluster" in ot:
                ot = "open_cluster"
            else:
                ot = "galaxy"
        lo, hi = _targets[ot]
        # p99 health (under-stretch floor). Galaxies: Henry prefers a darker, high-
        # contrast look (preferred M51 p99≈0.56) — the old 0.70 floor wrongly flagged
        # that as under-stretched and drove over-brightening. Only genuinely flat
        # galaxy stretches (p99 < 0.40) are under-stretched. Nebulae stay at 0.65;
        # clusters at the generic 0.70.
        if ot == "galaxy":
            p99_lo = 0.40
        elif "nebula" in ot:
            p99_lo = 0.65
        else:
            p99_lo = 0.70
        # Thin/noisy stacks (depth < 1) can't reach the highlight target without
        # lifting noise — relax the under-stretch floor by up to 0.12 at depth 0 so a
        # correctly gentler stretch isn't penalised as under-stretched. See
        # [[project-physics-default-pipeline]] / faint-image stretch deep dive.
        if depth < 1.0:
            p99_lo = max(0.20, p99_lo - (1.0 - depth) * 0.12)
        # Frame-fill midtone target: the nebula body must sit bright (IC 1805 manual
        # p50 0.244). Scaled down on thin stacks (same rationale as the p99_lo
        # relaxation) — a shallow stack can't hold bright midtones without noise.
        p50_target_lo = 0.15 + 0.05 * max(0.0, min(depth, 1.0)) if frame_fill else None
        p50_dist = max(0.0, p50_target_lo - p50) if frame_fill else 0.0
        return {
            "bg_level": bg, "bg_noise": bg_noise, "p95": p95, "p99": p99,
            "bg_ok": lo <= bg <= hi,
            "bg_target": f"{lo:.2f}–{hi:.2f}",
            "bg_low_val": lo, "bg_high_val": hi,   # numeric band for feedback correction
            "bg_high": bg > hi,          # true = elevated (too bright or emission fill)
            "bg_low":  bg < lo,          # true = crushed blacks
            "p99_low": p99 < p99_lo,     # true = under-stretched (highlights too dim)
            "p99_lo_threshold": p99_lo,
            "is_nebula": "nebula" in ot,
            "sky_rgb": sky_rgb,
            "p50": p50,
            "frame_fill": frame_fill,    # True → bg/bg_noise are dark-anchored, not corner
            "p50_target_lo": p50_target_lo,
            "p50_dist": p50_dist,        # midtone under-brightness charge (frame_fill only)
        }
    except Exception as e:
        log.debug(f"[autoprocess] _compute_stretch_stats failed: {e}")
        return {}


def _sky_band_distance(stats: dict) -> float:
    """Distance of measured sky background from the target band (0.0 if inside)."""
    bg = stats.get("bg_level")
    lo = stats.get("bg_low_val")
    hi = stats.get("bg_high_val")
    if bg is None or lo is None or hi is None:
        return 0.0
    if bg < lo:
        return lo - bg
    if bg > hi:
        return bg - hi
    return 0.0


# Object types whose targets can genuinely fill the S50 frame. Reflection/planetary
# nebulae and clusters have true-sky corners by definition — never frame-fill.
_FRAME_FILL_TYPES = {"emission_nebula", "supernova_remnant", "nebula"}


def _small_target_recenter_crop(fits_path, target_arcmin: float,
                                 target: str | None = None) -> dict:
    """Compositional center-crop for a target much smaller than the S50 frame.

    The coverage-driven crop step only trims blank/edge borders, so a compact
    bright target (M 1 6', M 57 1.4', PNe, small galaxies) ships as a speck in a
    77'x43' field of empty sky (M 1: ~10% fill, grader "small relative to the
    field"). This locates the target as the brightest EXTENDED (non-star) blob —
    robust where a fresh plate solve on the final was not — MEASURES its extent
    in pixels, and crops to ~4.5x that radius centered on it. Sizing from the
    measured blob (not arcmin x an assumed pixel scale) is scale-free, which
    matters because drizzled finals are ~1.19"/px but not reliably inferable from
    the frame dimensions. In place, keeps the header.

    Only meaningful when target_arcmin << FoV short axis (43'); the caller gates
    on < 11'. No-op (ok, cropped=False) if the blob is weak or the crop wouldn't
    meaningfully shrink the frame.
    """
    import numpy as np
    from astropy.io import fits as _fits
    from scipy.ndimage import gaussian_filter, median_filter
    try:
        with _fits.open(str(fits_path)) as h:
            d = h[0].data.astype(np.float32)
            hdr = h[0].header.copy()
        chan_first = d.ndim == 3 and d.shape[0] in (3, 4)
        arr = np.moveaxis(d[:3], 0, -1) if chan_first else d
        lum = arr.mean(axis=-1) if arr.ndim == 3 else arr
        ny, nx = lum.shape
        # suppress stars (median kills point sources), smooth → extended blob
        sm = gaussian_filter(median_filter(lum, size=9), 25)
        m = np.zeros_like(sm, bool)
        m[int(ny * 0.12):int(ny * 0.88), int(nx * 0.12):int(nx * 0.88)] = True
        bb = np.where(m, sm, 0.0)
        yc, xc = np.unravel_index(int(np.argmax(bb)), bb.shape)
        peak = float(bb[yc, xc]); base = float(np.median(sm))

        # WCS-first centering (workflow 1.24.3, M 102 2026-07-18: the blob detector
        # locked onto a bright star's halo and cropped the galaxy OUT of frame —
        # star halos survive the median filter). At the crop stage the WCS is now
        # trustworthy (ASTAP ingest solve + the 1.24.2 CRPIX fix), so center on the
        # target's CATALOG position when we can project it; the blob is demoted to
        # a size/verification aid and to the fallback path.
        _wcs_center = None
        if target:
            try:
                import sqlite3 as _sq
                from astropy.wcs import WCS as _WCS
                from nas_server.config import settings as _st
                _db = _sq.connect(_st.get("db_path",
                    str(Path.home() / "seestar_database" / "astro_data.db")))
                _row = _db.execute("SELECT ra, dec FROM targets WHERE target=?",
                                   (target,)).fetchone()
                _db.close()
                if _row and _row[0] is not None:
                    _w = _WCS(hdr, naxis=2)
                    if _w.has_celestial:
                        _px = _w.celestial.wcs_world2pix([[float(_row[0]),
                                                           float(_row[1])]], 0)[0]
                        _tx, _ty = float(_px[0]), float(_px[1])
                        if 0.05 * nx < _tx < 0.95 * nx and 0.05 * ny < _ty < 0.95 * ny:
                            _wcs_center = (int(_tx), int(_ty))
                            log.info(f"[autoprocess] recenter: WCS catalog center "
                                     f"({_wcs_center[0]},{_wcs_center[1]}) for {target} "
                                     f"(blob was ({xc},{yc}))")
                        else:
                            log.warning(f"[autoprocess] recenter: catalog position "
                                        f"projects off-frame ({_tx:.0f},{_ty:.0f}) — "
                                        "blob fallback")
            except Exception as _we:
                log.warning(f"[autoprocess] recenter: WCS centering failed ({_we}) — "
                            "blob fallback")
        if _wcs_center is not None:
            xc, yc = _wcs_center
        elif peak < base * 1.15:
            return {"ok": True, "cropped": False, "reason": "no clear extended blob"}
        # measure blob radius: pixels above the half-peak threshold, connected-ish
        thr = base + 0.5 * (peak - base)
        ys, xs = np.where((sm > thr) & m)
        if _wcs_center is not None:
            # size from the catalog angular size via the WCS pixel scale — the blob
            # may be a different object entirely (the M 102 star), so never size
            # from it when we centered by catalog
            try:
                from astropy.wcs import WCS as _WCS2
                _w2 = _WCS2(hdr, naxis=2)
                _sc = float(np.sqrt(abs(np.linalg.det(
                    _w2.celestial.pixel_scale_matrix)))) * 3600.0   # "/px
                r = max((target_arcmin * 60.0 / 2.0) / max(_sc, 0.1), 30.0)
            except Exception:
                r = 80.0
        elif len(xs) < 30:
            return {"ok": True, "cropped": False, "reason": "blob too small to size"}
        else:
            # robust radius = 95th-pct distance of above-threshold pixels from centre
            r = float(np.percentile(np.hypot(xs - xc, ys - yc), 95))
        half = int(max(r * 4.5, 240))          # ~4.5x radius, native-res floor
        if 2 * half >= min(nx, ny) * 0.85:
            return {"ok": True, "cropped": False, "reason": "target already well-framed"}
        x0 = int(max(0, min(nx - 2 * half, xc - half)))
        y0 = int(max(0, min(ny - 2 * half, yc - half)))
        sl = (slice(y0, y0 + 2 * half), slice(x0, x0 + 2 * half))
        out = (d[:, sl[0], sl[1]] if chan_first else d[sl])
        # Shift the WCS reference pixel with the crop (workflow 1.24.2): "keeps the
        # header" kept CRPIX at the FULL-frame value, so every recentered small
        # target shipped a WCS pointing ~0.5° off — downstream SPCC then queried
        # Gaia at the wrong sky and always fell back (M 85/97/102, 2026-07-18).
        if "CRPIX1" in hdr and "CRPIX2" in hdr:
            hdr["CRPIX1"] = float(hdr["CRPIX1"]) - x0
            hdr["CRPIX2"] = float(hdr["CRPIX2"]) - y0
        _fits.writeto(str(fits_path), out, hdr, overwrite=True)
        return {"ok": True, "cropped": True, "center": [int(xc), int(yc)],
                "blob_radius_px": round(r, 0), "kept_px": 2 * half,
                "orig_px": [nx, ny]}
    except Exception as e:
        log.warning(f"[autoprocess] small-target recenter crop failed: {e}")
        return {"ok": False, "cropped": False, "error": str(e)}


def _frame_fill_detect(fits_path, object_type: str,
                       target_arcmin: float | None = None) -> dict:
    """Detect a frame-filling nebula on the post-BGE, pre-stretch linear starless image.

    On a frame-filler (IC 1805, NGC 7000, NGC 6888) the corner pixels are faint nebula,
    not sky — every corner-anchored sky mechanism then mutes the object (IC 1805 autopsy:
    manual 8.2 parks corners at 0.19 / p50 0.244 vs pipeline 0.068 / 0.075 and wins on
    luminosity alone). See docs/spec_frame_fill_detection.md.

    All tests run on a 32×32 box-smoothed luminance, because after BGE a uniformly
    FILLED frame and a uniformly EMPTY one are statistically alike in raw level stats
    (both low dynamic range vs local noise — measured 2026-07-10: dark-σ-unit
    thresholds passed nearly every field in the library, filled or not). Smoothing
    crushes uncorrelated sky noise 32× while real extended structure survives, so
    three tests separate cleanly (library sweep, fillers vs controls):
      1. structure — smoothed p2–p98 range ≥100× the smoothed noise floor (σ_dark/32);
         real large-scale structure exists (fillers ≥249, sky/small fields ≤69)
      2. separation — (corner median − p2) ≥15% of that range (corners hold signal;
         fillers 0.19–0.38, M 42/M 17/M 51/NGC 2359 ≤0.07)
      3. coverage — ≥65% of the frame sits ≥10% of range above p2

    Returns {"eligible", "frame_fill", corner_median, dark_floor,
    structure_over_noise, separation_frac, coverage}. eligible=False (and
    frame_fill=False) for object types outside _FRAME_FILL_TYPES; all failures
    return frame_fill=False.
    """
    out: dict = {"eligible": False, "frame_fill": False}
    try:
        ot = (object_type or "").strip().lower().replace(" ", "_")
        if ot not in _FRAME_FILL_TYPES:
            # generic "nebula"-ish subtype strings coerce like _compute_stretch_stats,
            # but reflection/planetary explicitly stay ineligible
            if "nebula" in ot and "reflection" not in ot and "planetary" not in ot:
                ot = "nebula"
            else:
                return out
        # Angular-size gate (workflow 1.22.1, M 1 false positive 2026-07-14): the
        # pixel tests CANNOT separate a small target in a dense star field from a
        # frame-filling nebula — M 1 (6', Taurus star field) and IC 1805 (150')
        # both measure corner_median/dark_floor = 1.04 pre-stretch. The one robust
        # discriminator is the target's own size. A target far smaller than the S50
        # frame (43'×77') cannot fill it; its high smoothed "coverage" is unresolved
        # stars + noise, not emission. Frame-fill over-brightened M 1's empty field
        # and amplified a residual gradient into a green band (grader 3.2). Require
        # ≥30' (keeps NGC 7000/IC 1805/IC 1396/M 16/M 42; drops M 1 and other
        # compact SNRs). Unknown size → fall through to pixels (no regression).
        if target_arcmin is not None and 0 < target_arcmin < 30.0:
            out["small_target_arcmin"] = round(target_arcmin, 1)
            return out
        import numpy as np
        from astropy.io import fits as _fits
        from scipy.ndimage import uniform_filter
        with _fits.open(str(fits_path)) as h:
            d = h[0].data.astype(np.float32)
        if d.ndim == 3 and d.shape[0] in (3, 4):
            d = np.moveaxis(d[:3], 0, -1)
        lum = d.mean(axis=-1) if d.ndim == 3 else d
        p5 = float(np.percentile(lum, 5))
        dark_sigma = float(np.std(lum[lum <= p5]))
        if dark_sigma <= 0:
            return out
        sm = uniform_filter(lum, 32)
        h_, w_ = sm.shape
        margin = max(h_ // 20, w_ // 20, 50)
        corners = np.concatenate([
            sm[:margin, :margin].ravel(), sm[:margin, -margin:].ravel(),
            sm[-margin:, :margin].ravel(), sm[-margin:, -margin:].ravel()
        ])
        corner_med = float(np.median(corners))
        p2s, p98s = (float(x) for x in np.percentile(sm, [2, 98]))
        rng = max(p98s - p2s, 1e-12)
        son = rng / max(dark_sigma / 32.0, 1e-12)
        sep = (corner_med - p2s) / rng
        cov = float((sm > p2s + 0.10 * rng).mean())
        out.update({
            "eligible": True,
            "frame_fill": bool(son >= 100.0 and sep >= 0.15 and cov >= 0.65),
            "corner_median": round(corner_med, 6),
            "dark_floor": round(p2s, 6),
            "structure_over_noise": round(son, 1),
            "separation_frac": round(sep, 4),
            "coverage": round(cov, 4),
        })
        return out
    except Exception as e:
        log.debug(f"[autoprocess] _frame_fill_detect failed: {e}")
        return out


# Tie-break order for stretch picks: calibration-preserving / well-behaved first.
_STRETCH_PREF_ORDER = ["mas", "stat", "stat_bright", "stf", "ghs_soft", "ghs",
                       "ghs_strong", "veralux", "veralux_strong"]


def _variant_saturation(fits_path) -> float:
    """Mean HSV saturation over signal pixels of an RGB stretch FITS (grain-robust —
    unlike Hasler colourfulness, which noise inflates). Used by the narrow colour-swap
    in _physics_pick_stretch. 0.0 on any failure. See [[stretch-picker-ab-faithful]]."""
    try:
        import numpy as _np
        from astropy.io import fits as _fits
        d = _fits.getdata(str(fits_path), memmap=False).astype(_np.float32)
        if d.ndim != 3:
            return 0.0
        d = _np.moveaxis(d[:3], 0, -1) if d.shape[0] in (3, 4) else d[..., :3]
        mx = float(d.max())
        if mx > 1.5:
            d = d / mx                                   # normalise (e.g. mas)
        lum = 0.2126*d[..., 0] + 0.7152*d[..., 1] + 0.0722*d[..., 2]
        corner = _np.concatenate([lum[:40, :40].ravel(), lum[-40:, -40:].ravel()])
        m = lum > (_np.median(corner) + 3*_np.std(corner))
        if m.sum() < 500:
            m = lum > _np.median(lum)
        return float(((d.max(-1) - d.min(-1)) / (d.max(-1) + 1e-6))[m].mean())
    except Exception:
        return 0.0


def _stretch_preview_path(run_dir, name: str):
    """Locate a stretch variant's preview JPG (for the vision tiebreak). None if absent."""
    from pathlib import Path as _P
    rd = _P(run_dir)
    for cand in (rd / f"auto_stretch_{name}_preview.jpg",
                 rd / f"09_auto_stretch_{name}_preview.jpg"):
        if cand.exists():
            return cand
    hits = sorted(rd.glob(f"*auto_stretch_{name}_preview.jpg"))
    return hits[0] if hits else None


def _physics_pick_stretch(variants: list[dict], run_dir, object_type: str,
                          depth: float = 1.0,
                          folio_band: tuple[float, float] | None = None,
                          vision_tiebreak: bool = False,
                          frame_fill: bool = False,
                          scored_out: list | None = None) -> str | None:
    """Pick the best stretch variant from pixel stats alone — the no-Claude path.

    Mirrors what Claude judges by eye, but with the numbers Claude never sees.
    Scores BOTH ends of the histogram (via _compute_stretch_stats):
      • sky background inside the per-type target band  (shadows: not crushed / washed)
      • p99 highlight health                            (not under-stretched, not blown)
    Channel-dead variants (a black-clipped colour channel) are rejected outright.
    `folio_band` (lo, hi): the per-TARGET sky band from the folio's
    quality_thresholds.bg_level_range — nebula branch only. The per-type band is
    deliberately wide (emission corners hold real Ha), so a variant can sit "in band"
    yet far outside what this specific target should look like (M 42: mas sky 0.128
    beat stat_bright 0.068 against folio band [0.05, 0.09] → −0.2 overall). Distance
    from the folio band is charged into placement so out-of-folio variants lose.
    Galaxies ignore it: their branch ignores ALL sky terms (sky_mute_masked handles
    the sky downstream).
    Returns the winning variant name, or None if nothing scored.
    """
    from pathlib import Path as _P
    is_nebula = "nebula" in (object_type or "")
    scored: list[dict] = []
    for v in variants:
        name = v["name"]
        fp = _P(v["fits"]) if v.get("fits") else _P(run_dir) / f"auto_stretch_{name}.fit"
        if not fp.exists():
            continue
        # Reject a variant that black-clipped a colour channel (== _stretch_dead test).
        meds = _dark_sky_channel_meds(fp)
        if meds is not None and min(meds) < 0.01 and max(meds) > 0.04:
            continue
        st = _compute_stretch_stats(fp, object_type, depth, frame_fill=frame_fill)
        if not st:
            continue
        bg_dist = _sky_band_distance(st)                       # 0 inside band
        p50_dist = st.get("p50_dist", 0.0)                     # frame_fill midtone charge
        # Frame-fill placement (1.20.1, NGC 7000 20260712 flat-grey stf): the 1.20.0
        # terms charged floor placement + p50 brightness but not tonal SPREAD, so a
        # candidate squeezed into 0.16–0.21 (spread 0.017 vs the manual reference's
        # 0.107) won both terms while being a contrastless wash the grader then scored
        # 4.2. Under frame_fill the placement charge becomes:
        #   - ceiling-only floor charge: a floor ABOVE band-hi is unfixable grey wash;
        #     a floor BELOW band-lo is fixable (the bg clamp lifts it additively,
        #     structure intact) so it is NOT charged
        #   - spread deficit ×1.5: max(0, spread_target − (p50 − floor)); flatness is
        #     the one defect nothing downstream can repair
        ff_place = 0.0
        if frame_fill:
            _ff_bg = st.get("bg_level", 0.0)
            _ff_hi = st.get("bg_high_val", 0.16)
            _spread_target = 0.06 + 0.04 * max(0.0, min(depth, 1.0))
            _spread = st.get("p50", 0.0) - _ff_bg
            ff_place = (max(0.0, _ff_bg - _ff_hi)
                        + 1.5 * max(0.0, _spread_target - _spread))
            # The liftable-floor exemption does NOT apply to a CLIPPED floor: on a
            # frame-filler the p1–p3 pixels are faint nebula, and a floor at ~0 with
            # ~zero dark σ means that signal was destroyed, not parked low (the clamp
            # can add a pedestal but not resurrect clipped structure). Charge it 2×
            # the band distance so it decisively loses to any un-clipped candidate
            # (IC 1805/IC 1396 A/B: veralux floor 0.000 σ 0.000 must not beat mas
            # floor 0.090 / ghs_soft 0.009-with-structure).
            if _ff_bg < 0.005 and st.get("bg_noise", 0.0) < 0.0015:
                ff_place += 2.0 * max(0.0, st.get("bg_low_val", 0.06) - _ff_bg)
        p99 = st.get("p99", 0.0)
        thr = st.get("p99_lo_threshold", 0.70)
        under = max(0.0, thr - p99)                            # highlights too dim
        blown = max(0.0, p99 - 0.99)                           # highlights clipped white
        # Noise-amplification penalty. Without this the cost rewards whatever stretch
        # best fills the sky band + p99, so on faint data the most aggressive variant
        # wins by lifting grain (NGC 6914: veralux_strong, sky σ 0.26, beat clean
        # ghs_soft at σ 0.07). σ is measured in the sky corners, so real nebula fill
        # isn't charged. Weight rises as integration depth falls — but the floor is high
        # enough to flip a grainy winner even when the depth proxy reads full.
        bg_noise = st.get("bg_noise", 0.0)
        noise_w = 1.8 + 1.5 * (1.0 - depth)
        # Relative grain (σ/bg) — what the eye actually reads as "grainy", and what the
        # critique measured (M 57: veralux_strong std/bg 0.93 beat ghs_soft at 0.13 on the
        # IDENTICAL background). Absolute σ alone under-charges this when the sky sits at a
        # normal level. Only active on a healthy sky (bg > 0.04) so a crushed/near-zero
        # background can't blow the ratio up. See [[project-physics-default-pipeline]].
        _bg_lvl = st.get("bg_level", 0.0)
        rel_grain = (bg_noise / _bg_lvl) if _bg_lvl > 0.04 else 0.0
        place = (ff_place + p50_dist) if frame_fill else (bg_dist + p50_dist)
        cost = place + under + blown + noise_w * bg_noise + 0.25 * rel_grain
        pref = _STRETCH_PREF_ORDER.index(name) if name in _STRETCH_PREF_ORDER \
            else len(_STRETCH_PREF_ORDER)
        scored.append({
            "name": name, "bg_dist": bg_dist, "p50_dist": p50_dist,
            "ff_place": ff_place, "p99": p99,
            "under": under,
            "blown": blown, "bg_noise": bg_noise, "cost": cost, "pref": pref,
            "grain": bg_noise + 0.25 * rel_grain,
            "bg_level": st.get("bg_level", 0.0),
            "p50": st.get("p50", 0.0),
            "fits": str(fp),
        })
    if not scored:
        return None

    # Instrumentation: caller-visible per-candidate metrics (run.log stretch_pick
    # record — feeds the episode candidate-grid shot). Same dict objects as `scored`,
    # so drop annotations below (grain DQ / grain cap) land on them too.
    _all_cands = list(scored)

    def _emit(best_name: str) -> None:
        if scored_out is None:
            return
        for c in _all_cands:
            rec = {k: (round(v, 4) if isinstance(v, float) else v)
                   for k, v in c.items() if k != "fits"}
            rec["winner"] = c["name"] == best_name
            scored_out.append(rec)

    is_galaxy = "galaxy" in (object_type or "")

    # Absolute grain disqualifier (workflow 1.21.0; GALAXY-SCOPED in 1.22.2): a
    # candidate whose sky/dark σ exceeds 0.10 is visually ruined for GALAXIES,
    # whose branch ignores grain by design (detail-first, 1.7.0) — that let
    # veralux_strong win M 108 at σ 0.198 (26× cleanest) and NGC 4565 at σ 0.143.
    # NOT applied to nebulae: on a faint nebula the CORRECT brighter stretch is
    # necessarily grainier, and a hard DQ kills it (SH 2-273 regressed 6.8→4.5,
    # 1.22.1: DQ dropped stat_bright → conservative dark ghs_soft won → faint Fox
    # Fur emission lost). The nebula branch already treats grain as a placement-
    # equal TIEBREAKER (not a disqualifier), which is the right discipline for
    # faint signal — see [[feedback-faint-nebula-too-dark]]. If every galaxy
    # candidate exceeds the cap, keep the single cleanest rather than None.
    if is_galaxy:
        _GRAIN_DQ = 0.10
        _clean = [c for c in scored if c["bg_noise"] <= _GRAIN_DQ]
        if _clean and len(_clean) < len(scored):
            _dq = [f"{c['name']}(σ{c['bg_noise']:.3f})" for c in scored if c not in _clean]
            for c in scored:
                if c not in _clean:
                    c["dropped"] = "grain_dq"
            log.info(f"[autoprocess] stretch grain-DQ dropped {_dq} (σ > {_GRAIN_DQ})")
            scored = _clean
        elif not _clean:
            _keep = min(scored, key=lambda c: c["bg_noise"])
            for c in scored:
                if c is not _keep:
                    c["dropped"] = "grain_dq"
            log.warning(f"[autoprocess] stretch grain-DQ: ALL candidates exceed σ "
                        f"{_GRAIN_DQ} — keeping cleanest {_keep['name']} "
                        f"(σ{_keep['bg_noise']:.3f})")
            scored = [_keep]

    if is_galaxy:
        # Galaxy directive (workflow 1.7.0): the sky is muted downstream by the
        # sky_mute_masked step, so the stretch MUST be chosen on galaxy-detail
        # retention, NOT sky placement or sky-corner grain. Every sky term the picker
        # normally leans on works against us here: bg_dist penalizes the bright
        # faint-preserving stretches (mas/veralux leave the sky high → big bg_dist),
        # and the grain-cap drops them for sky-corner σ (M 51: even with bg_dist zeroed,
        # the grain-cap still cut veralux/mas and ghs_strong won — but ghs_strong crushes
        # the faint arms/tidal bridge Henry wants). So for galaxies we keep ONLY highlight
        # health (under/blown, so the core isn't dim or clipped) + the dead-channel reject
        # (already applied above), then prefer the faint-preserving variants by a
        # galaxy-specific order. The sky is no longer this step's problem. See
        # [[feedback-galaxy-stretch-darker]] and the sky_mute_masked step.
        _GAL_PREF = ["mas", "veralux_strong", "veralux", "stat_bright", "stat",
                     "stf", "ghs_strong", "ghs", "ghs_soft"]

        # Sky-ceiling gate (workflow 1.24.0, batch eval 2026-07-16: M 85/88/91 all
        # −1.3..−1.6 regressions). "Ignore sky, mute downstream" only holds when the
        # winner's sky is within sky_mute's healthy pull range (~0.09→0.06 was the
        # validated M 51 case). On thin stacks mas landed 0.108–0.118 and the
        # downstream mute + halo had to drag the sky ≥0.05 — overshooting to CRUSHED
        # while ON-TARGET stf (0.079–0.083, lower grain) sat unpicked. When any
        # candidate is at/below the ceiling, drop those above it; a uniformly-bright
        # field keeps its candidates (relative rule, like the grain cap).
        _GAL_SKY_CEIL = 0.10
        _under_ceil = [c for c in scored if c["bg_level"] <= _GAL_SKY_CEIL]
        if _under_ceil and len(_under_ceil) < len(scored):
            _cut = [f"{c['name']}(bg{c['bg_level']:.3f})" for c in scored
                    if c not in _under_ceil]
            for c in scored:
                if c not in _under_ceil:
                    c["dropped"] = "sky_ceiling"
            log.info(f"[autoprocess] stretch galaxy sky-ceiling dropped {_cut} "
                     f"(bg_level > {_GAL_SKY_CEIL} — beyond sky_mute's healthy pull)")
            scored = _under_ceil

        def _gal_key(c: dict):
            gpref = _GAL_PREF.index(c["name"]) if c["name"] in _GAL_PREF \
                else len(_GAL_PREF)
            # Bucket highlight health to ~0.05 so the faint-preserving order decides
            # among comparably-exposed variants; a genuinely under/blown variant still
            # loses to a well-placed one.
            health = c["under"] + c["blown"]
            return (round(health / 0.05), gpref)
        scored.sort(key=_gal_key)
        best = scored[0]
        log.info(f"[autoprocess] physics stretch pick (galaxy, detail-first, "
                 f"sky-muted downstream): {best['name']} (p99={best['p99']:.2f} "
                 f"under={best['under']:.3f} blown={best['blown']:.3f} "
                 f"bg_noise={best['bg_noise']:.3f} — sky terms ignored)")
        _emit(best["name"])
        return best["name"]

    if is_nebula:
        # Sky placement DOMINATES for nebulae; grain only breaks ties among
        # comparably-placed candidates. The blended cost let a grossly-overshot-but-clean
        # stf beat a near-band-but-grainier ghs_soft (SH 2-273 stf 0.327 vs ghs_soft
        # 0.191; NGC 6914 stf 0.260 vs ghs_soft) because noise_w·bg_noise outweighed the
        # bg_dist gap. On emission/reflection nebulae the corners hold real Ha, so an
        # at-stretch sky overshoot CANNOT be pulled back downstream without crushing
        # signal — a near-band pick must win. placement = how well BOTH ends sit
        # (bg_dist + under + blown), bucketed to ~0.02 so grain decides only within an
        # equal-placement band. See workflow 1.6.0 / critiques NGC 6914 + SH 2-273.
        # Folio-aware sky placement (workflow 1.8.0): when this target has a folio
        # band, charge each candidate the distance of its corner sky from THAT band
        # in addition to the wide per-type band. See docstring (M 42 precedent).
        def _folio_dist(c: dict) -> float:
            if not folio_band:
                return 0.0
            # Frame-fill rollout guard (spec §6): folio bands were authored for CORNER
            # semantics; charging them against the dark anchor punishes exactly the
            # bright placements frame-fill mode exists for. Skip until folios are
            # re-authored with dark-anchor bands.
            if frame_fill:
                return 0.0
            _lo, _hi = folio_band
            _bg = c.get("bg_level", 0.0)
            if _bg < _lo:
                return _lo - _bg
            if _bg > _hi:
                return _bg - _hi
            return 0.0

        def _neb_key(c: dict):
            # Sky placement (un-fixable downstream — Ha corners can't be pulled back) is
            # PRIMARY. Highlight health is a COARSE secondary. Grain is the real tiebreaker.
            # (workflow 1.11.0) Previously under+blown were lumped into placement at 0.02
            # resolution, so on faint LP nebulae the large, variable `under` term spread
            # candidates across many buckets and the grain tiebreaker never fired — the most
            # aggressive/grainiest stretch won among in-band candidates (critique batch:
            # stf/veralux beating cleaner stat/stat_bright at equal sky placement). Splitting
            # sky from health, and bucketing health coarsely (0.05), lets grain decide among
            # candidates with comparable sky AND exposure. Validated by scripts/stretch_picker_ab.py
            # (9/21 nebula picks change: 7 cleaner, 2 prefer a better-exposed variant).
            # p50_dist (frame_fill midtone under-brightness) charges into the same
            # primary bucket as sky placement — like sky, a too-dark body can't be
            # recovered downstream without lifting noise. 0.0 unless frame_fill.
            _place = c.get("ff_place", 0.0) if frame_fill else c["bg_dist"]
            sky    = round((_place + _folio_dist(c) + c.get("p50_dist", 0.0)) / 0.02)
            health = round((c["under"] + c["blown"]) / 0.05)
            return (sky, health, round(c["grain"], 4), c["pref"])
        scored.sort(key=_neb_key)
        best = scored[0]

        # Narrow colour-swap (workflow 1.12.0): Henry's taste = maximise colour SUBJECT TO
        # clean. The picker already honours "clean" but doesn't reach for colour when it's
        # freely available, so it can ship a desaturated stretch (M 42 stf) when a
        # comparably-clean, more-colourful one (mas) exists. Only swap when a candidate is
        # (a) comparably clean — σ ≤ 1.2× the winner's σ, so GRAIN stays a hard constraint
        # (Henry consistently rejected grainy veralux even when more colourful), (b) well
        # placed — well-developed, sky neither washed nor crushed, and (c) MEANINGFULLY more
        # saturated (≥ winner + 0.05). Validated: reproduces 6/7 blind labels, changes only
        # M 42→mas. See [[stretch-picker-ab-faithful]].
        try:
            _hi = folio_band[1] if folio_band else 0.09
            _wash = max(0.16, _hi + 0.06)
            _w_sat = _variant_saturation(best["fits"])
            _cands = [c for c in scored if c["name"] != best["name"]
                      and c["under"] <= 0.15 and 0.02 <= c["bg_level"] <= _wash
                      and c["bg_noise"] <= 1.2 * best["bg_noise"]]
            _cands = [(c, _variant_saturation(c["fits"])) for c in _cands]
            _cands = [(c, s) for c, s in _cands if s >= _w_sat + 0.05]
            if _cands:
                _cw, _cs = max(_cands, key=lambda x: x[1])
                log.info(f"[autoprocess] stretch colour-swap: {_cw['name']} over "
                         f"{best['name']} (sat {_w_sat:.3f}->{_cs:.3f}, "
                         f"σ {best['bg_noise']:.4f}->{_cw['bg_noise']:.4f})")
                best = _cw
        except Exception as _e:
            log.warning(f"[autoprocess] stretch colour-swap skipped ({_e})")

        # Optional bounded vision tiebreak (default off; settings.stretch_vision_tiebreak).
        # Fires ONLY on a genuine close call — the top two share the same sky AND
        # exposure bucket, so grain alone separated them: exactly the aesthetic call
        # physics is weakest at (see [[stretch-picker-ab-faithful]], the M 57 case).
        # One cheap Haiku vision compare of the two previews; any failure / physics-only
        # keeps the physics pick. Not a per-step re-score — a single bounded decision.
        if vision_tiebreak and len(scored) >= 2:
            _rnr = scored[1]
            def _bucket(c):
                _place = c.get("ff_place", 0.0) if frame_fill else c["bg_dist"]
                return (round((_place + _folio_dist(c) + c.get("p50_dist", 0.0)) / 0.02),
                        round((c["under"] + c["blown"]) / 0.05))
            if _bucket(best) == _bucket(_rnr) and best["name"] != _rnr["name"]:
                _pa = _stretch_preview_path(run_dir, best["name"])
                _pb = _stretch_preview_path(run_dir, _rnr["name"])
                if _pa and _pb:
                    try:
                        from nas_server import claude_client as _cc
                        _vt = _cc.stretch_vision_tiebreak(
                            str(_pa), best["name"], str(_pb), _rnr["name"],
                            object_type, folio_band)
                        if _vt and _vt.get("winner") == _rnr["name"]:
                            log.info(f"[autoprocess] stretch vision tiebreak: "
                                     f"{_rnr['name']} over {best['name']} "
                                     f"(grain {best['grain']:.3f}->{_rnr['grain']:.3f}; "
                                     f"{_vt.get('reason','')})")
                            best = _rnr
                        elif _vt:
                            log.info(f"[autoprocess] stretch vision tiebreak confirmed "
                                     f"physics pick {best['name']} ({_vt.get('reason','')})")
                    except Exception as _e:
                        log.warning(f"[autoprocess] stretch vision tiebreak error "
                                    f"({_e}) — keeping physics pick")

        _fol_tag = (f" folio_dist={_folio_dist(best):.3f} "
                    f"(band {folio_band[0]:.2f}–{folio_band[1]:.2f})") if folio_band else ""
        _ff_tag = (f" FRAME-FILL dark-anchor p50={best.get('p50', 0):.3f} "
                   f"p50_dist={best.get('p50_dist', 0):.3f}") if frame_fill else ""
        log.info(f"[autoprocess] physics stretch pick (nebula, placement-first): "
                 f"{best['name']} (bg_dist={best['bg_dist']:.3f} p99={best['p99']:.2f} "
                 f"under={best['under']:.3f} grain={best['grain']:.3f}"
                 f"{_fol_tag}{_ff_tag})")
        _emit(best["name"])
        return best["name"]

    # Non-nebula (galaxy/globular/broadband): blended cost, validated by prior batches.
    # Grain discipline (workflow 1.6.0): the blended cost lets a brighter-but-grainier
    # in-band stretch beat a much cleaner in-band one, because the brighter variant's
    # lower under-exposure term outweighs noise_w·bg_noise (M 51 6/9: veralux grain 0.248
    # beat stat_bright 0.145 — both ON-TARGET sky — and the final noise score tanked to
    # 5.5, tripping a non-degradation restore). When a clean in-band candidate exists,
    # refuse to let a candidate ≥1.5× grainier win: drop the grainy ones, then take the
    # lowest cost among survivors. Reference grain is the cleanest IN-BAND variant, so a
    # uniformly-grainy field (no clean in-band option) is left untouched — the cap is
    # 1.5× the grainy variant's own grain there, so it survives. See critiques
    # 20260609_021519_seestar_galaxy + [[galaxy-stretch-darker]].
    _GRAIN_CAP = 1.5
    _in_band = [c for c in scored if c["bg_dist"] <= 1e-6]
    _ref_pool = _in_band or scored
    _clean_ref = min(c["grain"] for c in _ref_pool)
    if _clean_ref > 0:
        _survivors = [c for c in scored if c["grain"] <= _GRAIN_CAP * _clean_ref]
        if _survivors and len(_survivors) < len(scored):
            _cut = [c["name"] for c in scored if c not in _survivors]
            for c in scored:
                if c not in _survivors:
                    c["dropped"] = "grain_cap"
            log.info(f"[autoprocess] stretch grain-cap dropped {_cut} "
                     f"(>{_GRAIN_CAP}× cleanest in-band grain {_clean_ref:.3f})")
            scored = _survivors
    scored.sort(key=lambda c: (round(c["cost"], 4), c["pref"]))
    best = scored[0]
    log.info(f"[autoprocess] physics stretch pick: {best['name']} "
             f"(bg_dist={best['bg_dist']:.3f} p99={best['p99']:.2f} "
             f"under={best['under']:.3f} bg_noise={best['bg_noise']:.3f})")
    _emit(best["name"])
    return best["name"]


def _physics_grade_nonlinear(fits_path, object_type: str, depth: float = 1.0,
                             frame_fill: bool = False) -> dict:
    """Physics grade for a *stretched* (non-linear) image — no API call.

    grade_from_physics is calibrated for LINEAR stacks (SNR~100, gradient~0.16);
    applied to a stretched frame (SNR~3, gradient saturated) it floors every score
    and false-aborts. For non-linear images the meaningful quality signal is the
    stretch itself, so this grades from _compute_stretch_stats (sky band + p99) plus
    the colour/star metrics that stay valid post-stretch. Returns the assess schema
    with `_source: "physics_nl"`. Always returns a dict.
    """
    def _c(v: float) -> float:
        return round(max(1.0, min(10.0, v)), 1)
    from pathlib import Path as _P
    fp = _P(fits_path)
    st = _compute_stretch_stats(fp, object_type, depth, frame_fill=frame_fill) or {}
    bg_dist = _sky_band_distance(st)
    p99 = st.get("p99", 0.0)
    thr = st.get("p99_lo_threshold", 0.70)
    under = max(0.0, thr - p99)
    blown = max(0.0, p99 - 0.99)
    # Core non-linear axis: how well the stretch placed shadows + highlights, minus a
    # background-grain penalty. The placement terms alone over-graded grainy stretches
    # (M105's starless veralux_strong scored 8.4 → tripped early-stop), so charge sky σ
    # the same way the picker does. Measured in the corners → real nebula fill is spared.
    bg_noise = st.get("bg_noise", 0.0)
    stretch_s = _c(10.0 - bg_dist * 60 - under * 25 - blown * 40 - bg_noise * 22)

    snr = grad = fwhm = green = None
    try:
        from nas_server.image_analyzer import analyze as _an
        ia = _an(str(fp)) or {}
        snr = (ia.get("noise", {}) or {}).get("snr")
        green = (ia.get("color", {}) or {}).get("green_excess")
        fwhm = (ia.get("psf", {}) or {}).get("fwhm_median")
    except Exception:
        pass
    # Post-stretch SNR is compressed — grade gently, don't floor it.
    noise_s = _c(snr * 1.2 + 3.0) if snr is not None else 6.0
    color_s = _c(10.0 - abs(green) * 600) if green is not None else 7.0
    star_s = _c(12.0 - fwhm * 1.3) if fwhm is not None else 6.0
    # Post-stretch gradient metric saturates and is unreliable — derive from sky
    # uniformity (in-band background) instead.
    gradient_s = _c(8.5 - bg_dist * 30)

    overall = _c(stretch_s * 0.35 + color_s * 0.20 + noise_s * 0.15
                 + star_s * 0.15 + gradient_s * 0.15)

    issues, suggestions = [], []
    if stretch_s < 5:
        issues.append("under/over-stretched (sky or highlights off-target)")
        suggestions.append("re-stretch toward the target sky band and p99")
    if color_s < 6:
        issues.append("residual colour cast")
    if star_s < 5:
        issues.append("soft or bloated stars")
    return {
        "overall": overall, "noise": noise_s, "gradient": gradient_s,
        "star_roundness": star_s, "stretch_quality": stretch_s,
        "color_balance": color_s, "issues": issues, "suggestions": suggestions,
        "raw_response": "", "input_tokens": 0, "output_tokens": 0,
        "_source": "physics_nl",
    }


def _ghs_sky_correct(out_path: Path, object_type: str, target: str = "") -> bool:
    """
    Land a GHS-stretched image's sky background inside the per-object-type band by
    pulling its black point in the (already non-linear) output domain.

    GHS exposes no direct sky/background target — its sky floats high on faint
    targets (NGC 6888: GHS sky ~0.36–0.50 vs the 0.06–0.16 emission band), and the
    alpha/pivot levers saturate before reaching band while distorting the midtones.
    A linear black-point subtraction (new = clip((x − bp)/(1 − bp))) hits the target
    exactly in one shot and preserves the nebula-to-sky contrast far better. Only
    over-bright skies are corrected; under-stretched output is left for the alpha
    retry to handle.

    Returns True if a correction was applied.
    """
    st = _compute_stretch_stats(out_path, object_type)
    if not st or st.get("bg_ok"):
        return False
    sky = st.get("bg_level")
    lo = st.get("bg_low_val")
    hi = st.get("bg_high_val")
    if not (sky and lo and hi) or sky <= hi:
        return False  # only pull down an elevated sky
    desired = (lo + hi) / 2.0
    bp = (sky - desired) / (1.0 - desired)
    bp = float(min(max(bp, 0.0), 0.9))
    if bp < 1e-3:
        return False
    try:
        from astropy.io import fits as _af
        import numpy as np
        with _af.open(str(out_path), mode="update", memmap=False) as h:
            d = h[0].data.astype(np.float32)
            h[0].data = np.clip((d - bp) / (1.0 - bp), 0.0, 1.0).astype(np.float32)
            h.flush()
        log.info(f"[autoprocess] {target}: GHS sky correction — "
                 f"sky {sky:.3f}→~{desired:.3f} (black-point {bp:.3f})")
        return True
    except Exception as e:
        log.warning(f"[autoprocess] {target}: GHS sky correction failed: {e}")
        return False


def _dark_sky_channel_meds(fits_path) -> tuple | None:
    """Return (R,G,B) sigma-free medians over the dark-sky region (luminance below
    the 40th percentile) for an RGB FITS, normalised to [0,1]. None if not 3-channel
    or too few sky pixels. Used to detect colour casts / dead channels."""
    try:
        from astropy.io import fits as _af
        import numpy as np
        with _af.open(str(fits_path), memmap=False) as h:
            d = h[0].data.astype(np.float32)
        if d.ndim != 3 or d.shape[0] != 3:
            return None
        mx = float(d.max())
        if mx > 1.5:
            d = d / mx
        r, g, b = d[0], d[1], d[2]
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        sky = lum < np.percentile(lum, 40)
        if sky.sum() < 100:
            return None
        return (float(np.median(r[sky])), float(np.median(g[sky])), float(np.median(b[sky])))
    except Exception:
        return None


def _rescue_dead_channel(out_path: Path, target: str = "") -> bool:
    """
    After stretch, detect an RGB channel the stretch black-clipped to ~zero while the
    others survived (a 'dead channel'), and lift it with a flat pedestal so its dark-sky
    median matches the surviving channels.

    Why: on a dim broadband Ha nebula the red sky background can sit far below green, and
    GHS's black point drives red to exactly 0. The downstream SCNR step then caps green at
    the max of the other channels — with red at 0 that yields a saturated single-hue cast
    (the pure-teal NGC 6888 failure). Lifting the dead channel to a neutral grey pedestal
    before SCNR breaks that cascade. Fires only on the pathological collapse signature.

    Returns True if a channel was rescued.
    """
    meds = _dark_sky_channel_meds(out_path)
    if not meds:
        return False
    lo_m, hi_m = min(meds), max(meds)
    if not (lo_m < 0.01 and hi_m > 0.04):
        return False  # not a dead-channel collapse
    try:
        from astropy.io import fits as _af
        import numpy as np
        target_med = float(np.median(meds))  # neutral grey target
        names = "RGB"
        lifted = []
        with _af.open(str(out_path), mode="update", memmap=False) as h:
            d = h[0].data.astype(np.float32)
            mx = float(d.max())
            scaled = mx > 1.5
            if scaled:
                d = d / mx
            for ci in range(3):
                if meds[ci] < target_med - 0.01:
                    ped = target_med - meds[ci]
                    d[ci] = np.clip(d[ci] + ped, 0.0, 1.0)
                    lifted.append(f"{names[ci]}+{ped:.3f}")
            if not lifted:
                return False
            h[0].data = (d * mx if scaled else d).astype(np.float32)
            h.flush()
        log.info(f"[autoprocess] {target}: dead-channel rescue — sky medians "
                 f"R={meds[0]:.3f} G={meds[1]:.3f} B={meds[2]:.3f} → pedestal {','.join(lifted)}")
        return True
    except Exception as e:
        log.warning(f"[autoprocess] {target}: dead-channel rescue failed: {e}")
        return False


def _guard_channel_crush(out_path: Path, target: str = "") -> bool:
    """
    Belt-and-suspenders guard against a downstream step leaving a per-channel colour
    cast in the dark sky (e.g. IC 434 post-SCNR sky MEAN R=0.025 / G=0.004 / B=0.045 —
    a strong magenta cast). After SPCC the sky background is neutral by construction, so
    any per-channel divergence in the dark sky is a step artifact, not real colour.

    Detection uses the per-channel sky MEAN, not the median: a hard black-clip (SCNR's
    green clip, an over-eager black point) drives the channel median to exactly 0 while
    leaving a skewed MEAN — so a median-based test reads 0/0/0 and misses the very cast
    it should catch (verified on IC 434's 1.2.0 final). When the channels diverge in the
    dark sky regime (max mean > 2x min mean, in absolute terms small), lift the dimmer
    channels UP to the brightest channel's sky mean — re-neutralising additively so we
    never darken real signal (Henry is sensitive to faint-signal loss). Distinct from
    _rescue_dead_channel, which fires only on the post-stretch lo<0.01/hi>0.04 collapse.
    Returns True if a channel was lifted.
    """
    try:
        from astropy.io import fits as _af
        import numpy as np
        with _af.open(str(out_path), memmap=False) as _h:
            _d0 = _h[0].data
        if _d0 is None or _d0.ndim != 3 or _d0.shape[0] < 3:
            return False
        d0 = _d0.astype(np.float32)
        mx = float(d0.max())
        if mx > 1.5:
            d0 = d0 / mx
        r, g, b = d0[0], d0[1], d0[2]
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        sky = lum < np.percentile(lum, 40)
        if int(sky.sum()) < 100:
            return False
        means = [float(r[sky].mean()), float(g[sky].mean()), float(b[sky].mean())]
        lo_m, hi_m = min(means), max(means)
        # Sky-regime per-channel cast: bright channel clearly above the dim one, but the
        # whole sky still dark (not a bright/structured frame), and the gap is real.
        if not (hi_m < 0.15 and hi_m > 2.0 * lo_m and (hi_m - lo_m) > 0.004):
            return False
    except Exception as e:
        log.debug(f"[autoprocess] {target}: channel-crush guard probe failed: {e}")
        return False
    try:
        from astropy.io import fits as _af
        import numpy as np
        names = "RGB"
        lifted = []
        with _af.open(str(out_path), mode="update", memmap=False) as h:
            d = h[0].data.astype(np.float32)
            if d.ndim != 3 or d.shape[0] < 3:
                return False
            mx2 = float(d.max())
            scaled = mx2 > 1.5
            if scaled:
                d = d / mx2
            for ci in range(3):
                if means[ci] < hi_m - 0.001:
                    ped = hi_m - means[ci]
                    d[ci] = np.clip(d[ci] + ped, 0.0, 1.0)
                    lifted.append(f"{names[ci]}+{ped:.4f}")
            if not lifted:
                return False
            h[0].data = (d * mx2 if scaled else d).astype(np.float32)
            h.flush()
        log.info(f"[autoprocess] {target}: channel-crush guard — sky means "
                 f"R={means[0]:.4f} G={means[1]:.4f} B={means[2]:.4f} cast → "
                 f"neutralised to {hi_m:.4f} ({','.join(lifted)})")
        return True
    except Exception as e:
        log.warning(f"[autoprocess] {target}: channel-crush guard failed: {e}")
        return False


def _mute_sky(out_path: Path, object_type: str, target: str = "",
              desired_bg: float = 0.06) -> bool:
    """
    Mute an over-bright sky on the *final* image: when the corner sky background sits
    above the per-object-type band, pull it down to `desired_bg` with the bright
    structure protected, so the background darkens without lifting the galaxy or
    amplifying sky noise.

    Henry prefers galaxies with a dark, clean sky and the structure left where it is
    (M51, 2026-06-03) — the pipeline's curves+combine tail tends to raise the galaxy
    sky to ~0.09 (muddy/brown, above the 0.05-0.08 band). A galaxy-protected linear
    black-point pull lands the sky at ~0.06 while p95/p99 stay put and sky noise is
    unchanged (measured x1.03). Nebulae are skipped: their elevated corners can be
    real diffuse emission (Ha/OIII), so darkening them would crush genuine signal.

    Returns True if a mute was applied.
    """
    st = _compute_stretch_stats(out_path, object_type)
    if not st or st.get("is_nebula"):
        return False
    bg = st.get("bg_level")
    hi = st.get("bg_high_val")
    if not (bg and hi) or bg <= hi:
        return False  # already in/below band — nothing to mute
    bp = (bg - desired_bg) / (1.0 - desired_bg)
    bp = float(min(max(bp, 0.0), 0.5))
    if bp < 1e-3:
        return False
    try:
        from astropy.io import fits as _af
        import numpy as np
        from scipy.ndimage import gaussian_filter
        with _af.open(str(out_path), mode="update", memmap=False) as h:
            d = h[0].data.astype(np.float32)
            rgb = d.ndim == 3 and d.shape[0] == 3
            arr = np.moveaxis(d, 0, -1) if rgb else d
            lum = arr.mean(-1) if arr.ndim == 3 else arr
            # Structure-protect mask: 1 = keep original (galaxy), 0 = pull (sky).
            # Ramp centred ~2.5× the band top so the sky and faintest disk are pulled
            # while the bright disk/core/stars are untouched.
            knee = hi * 2.5
            w = np.clip((lum - (knee - 0.05)) / 0.10, 0.0, 1.0)
            w = gaussian_filter(w, 3)
            pulled = np.clip((arr - bp) / (1.0 - bp), 0.0, 1.0)
            wf = w[..., None] if arr.ndim == 3 else w
            out = (arr * wf + pulled * (1.0 - wf)).astype(np.float32)
            h[0].data = (np.moveaxis(out, -1, 0) if rgb else out).astype(np.float32)
            h.flush()
        log.info(f"[autoprocess] {target}: sky mute — bg {bg:.3f}→~{desired_bg:.3f} "
                 f"(black-point {bp:.3f}, structure protected)")
        return True
    except Exception as e:
        log.warning(f"[autoprocess] {target}: sky mute failed: {e}")
        return False


def _recover_contrast_nebula(out_path: Path, object_type: str, target: str = "",
                             depth: float = 1.0) -> bool:
    """Signal-aware contrast recovery for NEBULAE (workflow 1.13.1 — retuned).

    The colour-first pick (mas, 1.12.0) can leave a brighter sky that softens contrast.
    Recover it by pulling the sky toward the BAND FLOOR (not to black) and expanding the
    nebula-body contrast — while (a) keeping the sky IN BAND, (b) protecting the faint
    floor (sky+3σ) from crushing, and (c) protecting the bright cores from clipping.

    1.13.1 fixes the 1.13.0 critique regressions: target_sky was 0.05 (BELOW the nebula
    band floor 0.06) → it CRUSHED the sky (M 42 0.125-in-band → 0.050-crushed, faint loss)
    AND the body expansion blew the cores (Trapezium white mass). Now: sky target is the
    band floor + margin (never below band), the gate only fires on a genuinely-soft sky
    (> band top region), and the expansion is HIGHLIGHT-PROTECTED (identity above a knee)
    so cores don't clip. See the 2026-07-01 critique batch.
    """
    st = _compute_stretch_stats(out_path, object_type)
    if not st or not st.get("is_nebula"):
        return False
    sky = float(st.get("bg_level", 0.0))
    noise = float(st.get("bg_noise", 0.0))
    band_hi = float(st.get("bg_high_val") or 0.14)   # nebula band top from stats
    band_lo = 0.06                                   # nebula band floor — NEVER go below
    target_sky = band_lo + 0.02                      # land mid-low IN BAND (~0.08), not black
    # Gate: only fire when the sky is genuinely SOFT (comfortably above the target) — else
    # the stretch already placed it well; darkening in-band would only crush + amplify grain.
    if sky <= target_sky + 0.02:
        return False
    faint_floor = sky + 3.0 * noise                  # faintest wisps — protect below
    guard = 1.5 * noise
    bp = (sky - target_sky) / (1.0 - target_sky)     # maps sky→target_sky (stays in band)
    bp = min(bp, max(0.0, faint_floor - guard))      # never clip into the faint signal
    bp = float(min(max(bp, 0.0), 0.5))
    if bp < 1e-3:
        return False
    gain, hi_knee = 1.15, 0.80        # body expansion; protect cores above the knee
    try:
        from astropy.io import fits as _af
        import numpy as np
        with _af.open(str(out_path), mode="update", memmap=False) as h:
            d = h[0].data.astype(np.float32)
            rgb = d.ndim == 3 and d.shape[0] == 3
            arr = np.moveaxis(d, 0, -1) if rgb else d
            x = np.clip((arr - bp) / (1.0 - bp), 0.0, 1.0)
            L = x.mean(-1) if x.ndim == 3 else x
            f = max(0.0, (faint_floor - bp) / (1.0 - bp))          # faint floor, re-based
            _mid = (L > f) & (L <= hi_knee)
            body = float(np.median(L[_mid])) if _mid.any() else 0.30
            expanded = np.clip(body + (L - body) * gain, f, 1.0)
            # HIGHLIGHT PROTECT: blend expanded→identity from the knee to 1.0 so bright
            # cores don't get pushed to clip; SHADOW PROTECT: identity ≤ faint floor.
            hw = np.clip((L - hi_knee) / (1.0 - hi_knee + 1e-6), 0.0, 1.0)
            Lc = np.where(L <= f, L, expanded * (1.0 - hw) + L * hw)
            ratio = (Lc / (L + 1e-6))
            wf = ratio[..., None] if x.ndim == 3 else ratio
            out = np.clip(x * wf, 0.0, 1.0).astype(np.float32)
            h[0].data = (np.moveaxis(out, -1, 0) if rgb else out).astype(np.float32)
            h.flush()
        log.info(f"[autoprocess] {target}: contrast recovery — sky {sky:.3f}→~{target_sky:.3f} "
                 f"(IN BAND [{band_lo}-{band_hi:.2f}], bp {bp:.3f}, gain {gain}, "
                 f"cores protected >{hi_knee})")
        return True
    except Exception as e:
        log.warning(f"[autoprocess] {target}: contrast recovery failed: {e}")
        return False


def _stretch_with_sky_feedback(forced_variant: dict, input_path: Path,
                               out_path: Path, object_type: str,
                               run_variant_fn, target: str = "",
                               max_corrections: int = 2,
                               frame_fill: bool = False) -> tuple:
    """
    Run a forced stretch variant, measure the resulting sky background, and re-run
    with a proportionally-adjusted brightness target if the sky landed outside the
    per-object-type band (from _compute_stretch_stats).

    A statistical stretch targets the pixel *median*; for dense fields (globulars)
    that median is star-dominated, so a fixed target_median does not reliably place
    the sky background.  This closes the loop: measure actual sky, scale the target
    by (desired_sky / actual_sky), re-stretch, keep whichever pass lands closest.

    Adjusts `target_median` (stat_stretch family) or `target_bg` (STF family).
    Returns (result_dict, output_path, final_stats).
    """
    params = forced_variant.get("params", {})
    if "target_median" in params:
        bright_key, b_min, b_max = "target_median", 0.04, 0.35
    elif "target_bg" in params:
        bright_key, b_min, b_max = "target_bg", 0.04, 0.35
    else:
        bright_key = None  # no brightness control — run once, no feedback

    res = run_variant_fn(forced_variant, input_path, out_path)
    if not (res.get("ok") and out_path.exists()) or bright_key is None:
        # GHS has no target_median/target_bg lever, so the proportional loop below is
        # skipped — instead land its sky in band with a black-point pull.
        if (res.get("ok") and out_path.exists()
                and "alpha" in params and bright_key is None):
            _ghs_sky_correct(out_path, object_type, target)
        return res, out_path, (_compute_stretch_stats(out_path, object_type, frame_fill=frame_fill)
                               if out_path.exists() else {})

    best_out, best_res = out_path, res
    best_stats = _compute_stretch_stats(out_path, object_type, frame_fill=frame_fill)
    best_dist = _sky_band_distance(best_stats)
    cur_variant = forced_variant

    for i in range(max_corrections):
        if best_stats.get("bg_ok"):
            break
        lo = best_stats.get("bg_low_val")
        hi = best_stats.get("bg_high_val")
        actual = best_stats.get("bg_level")
        if not (lo and hi and actual and actual > 1e-6):
            break
        desired = (lo + hi) / 2.0
        cur_val = float(cur_variant["params"][bright_key])
        new_val = round(max(b_min, min(b_max, cur_val * (desired / actual))), 4)
        if abs(new_val - cur_val) < 0.005:
            log.info(f"[autoprocess] {target}: sky feedback converged "
                     f"({bright_key}={cur_val:.3f}, no further change)")
            break
        fv2 = dict(cur_variant)
        fv2["params"] = {**cur_variant["params"], bright_key: new_val}
        out2 = out_path.with_name(f"{out_path.stem}_skyfix{i+1}{out_path.suffix}")
        log.info(f"[autoprocess] {target}: sky feedback — measured bg={actual:.3f} "
                 f"(target {lo:.2f}–{hi:.2f}); adjusting {bright_key} "
                 f"{cur_val:.3f}→{new_val:.3f} and re-stretching")
        r2 = run_variant_fn(fv2, input_path, out2)
        if not (r2.get("ok") and out2.exists()):
            log.warning(f"[autoprocess] {target}: sky feedback re-stretch failed — keeping prior")
            break
        s2 = _compute_stretch_stats(out2, object_type, frame_fill=frame_fill)
        d2 = _sky_band_distance(s2)
        cur_variant = fv2
        if d2 < best_dist:
            best_out, best_res, best_stats, best_dist = out2, r2, s2, d2
        if s2.get("bg_ok"):
            log.info(f"[autoprocess] {target}: sky feedback landed in band "
                     f"(bg={s2.get('bg_level'):.3f})")
            break

    if best_out != out_path:
        # Promote the winning pass to the canonical output path so callers are unaware.
        import shutil as _sh
        _sh.copy2(str(best_out), str(out_path))
        best_res = dict(best_res)
        best_res["output_path"] = str(out_path)
    return best_res, out_path, best_stats


# Maps quick_default step names to their output FITS filename in the run dir.
# Used to locate baseline intermediates when threading them into experiment evaluations.
_BASELINE_FITS_MAP: dict[str, list[str]] = {
    "background_extraction": ["auto_background_extraction_forced.fit"],
    "color_calibration":     ["auto_color_calibration_a0.fit"],
    "deconvolution":         ["auto_deconvolution_forced.fit"],
    "denoise_linear":        ["auto_denoise_linear_forced.fit"],
    "remove_stars_linear":   ["auto_starless.fit"],
    "stretch":               ["auto_stretch_stat_default.fit", "auto_stretch_forced.fit"],
    "scnr":                  ["auto_scnr_forced.fit", "auto_scnr_a0.fit"],
    "stretch_stars":         ["auto_stars_stretched.fit"],
    "combine_stars_screen":  ["final.fit"],
}

# Maps assess checkpoint labels to the step whose baseline preview is most relevant.
_ASSESS_BASELINE_STEP = {
    "pre_stretch":   "denoise_linear",
    "post_stretch":  "stretch",
    "final":         "combine_stars_screen",
}


def _collect_baseline_previews(run_dir: Path) -> dict[str, Path]:
    """
    Build step_name → preview JPEG mapping from a completed quick_default run dir.
    Generates STF previews for forced-step FITS files that have no preview yet.
    """
    previews: dict[str, Path] = {}
    for step, candidates in _BASELINE_FITS_MAP.items():
        fits_path: Path | None = None
        for name in candidates:
            p = run_dir / name
            if p.exists():
                fits_path = p
                break
        if fits_path is None:
            continue

        # Prefer an already-generated preview to avoid redundant work
        existing = [
            run_dir / f"auto_preview_{step}_a0.jpg",
            run_dir / f"auto_preview_{step}_forced.jpg",
            run_dir / f"auto_preview_{step}_baseline.jpg",
        ]
        jpg_path: Path | None = next((p for p in existing if p.exists()), None)
        if jpg_path is None:
            jpg_path = run_dir / f"auto_preview_{step}_baseline.jpg"
            _generate_preview(fits_path, jpg_path)

        if jpg_path and jpg_path.exists():
            previews[step] = jpg_path

    # Final combined result
    for fname in ("final_preview.jpg",):
        p = run_dir / fname
        if p.exists():
            previews["final"] = p
            previews.setdefault("combine_stars_screen", p)

    log.info(f"[autoprocess] baseline previews collected: {sorted(previews)}")
    return previews


def _number_run_files(run_dir: Path) -> None:
    """
    Rename files in a processing run directory with sequential numeric prefixes
    so they sort in pipeline order in a file explorer.

    Examples:
        auto_preview_initial.jpg          → 01_auto_preview_initial.jpg
        auto_preview_pre_crop.jpg         → 02_auto_preview_pre_crop.jpg
        auto_crop_a0.fit                  → 02_auto_crop_a0.fit
        auto_stretch_stat_default.fit     → 09_auto_stretch_stat_default.fit
        final.fit                         → 22_final.fit

    Skips: run.log, source stack FITS (files not starting with "auto_" or "final"),
    files in subdirectories, and already-prefixed files (idempotent).
    """
    # Ordered list of (substring_in_filename, seq_num).
    # More-specific patterns are listed first — first match wins.
    _ORDER: list[tuple[str, int]] = [
        # ── Assessment checkpoint previews  auto_preview_{label}  ──────────────
        ("auto_preview_initial",                  1),
        ("auto_preview_pre_stretch_adaptive",      7),
        ("auto_preview_pre_stretch",               7),
        ("auto_preview_post_stretch",             11),
        ("auto_preview_final",                    22),
        # ── "Before" previews  auto_preview_pre_{step}  ───────────────────────
        # Written before each standard step; gets the same seq as the step itself.
        ("pre_crop",                               2),
        ("pre_background_extraction",              3),
        ("pre_color_calibration",                  4),
        ("pre_deconvolution",                      5),
        ("pre_denoise_linear",                     6),
        ("pre_scnr",                              12),
        ("pre_background_neutralize",             13),
        ("pre_clahe",                             14),
        ("pre_noise_reduction",                   15),
        ("pre_curves",                            16),
        ("pre_hdr_compression",                   17),
        ("pre_dark_enhance",                      18),
        ("pre_halo_suppression",                  19),
        ("pre_stretch_stars",                     20),
        ("pre_combine_stars_screen",              21),
        # ── Standard step outputs  auto_{step}_*  ─────────────────────────────
        # Each step may produce FITS outputs (auto_{step}_*) AND after-step preview
        # JPEGs (auto_preview_{step}_*).  Both patterns are listed so all files match.
        ("auto_crop",                              2),
        ("preview_crop",                           2),   # auto_preview_crop_*
        ("auto_background_extraction",             3),
        ("preview_background_extraction",          3),
        ("auto_color_calibration",                 4),
        ("preview_color_calibration",              4),
        ("auto_deconvolution",                     5),
        ("preview_deconvolution",                  5),
        ("auto_denoise_linear",                    6),
        ("preview_denoise_linear",                 6),
        ("auto_starless",                          8),   # remove_stars_linear
        ("preview_starless",                       8),
        ("auto_stars_stretched",                  20),   # BEFORE auto_stars
        ("preview_stars_stretched",               20),
        ("auto_stars",                             8),   # stars aux layer
        ("preview_stars",                          8),
        ("auto_stretch_retry",                    10),   # BEFORE auto_stretch
        ("preview_stretch_retry",                 10),
        ("auto_stretch",                           9),
        ("preview_stretch",                        9),
        ("post_stretch",                          11),   # vrg/rescue previews after stretch assess
        ("auto_scnr",                             12),
        ("preview_scnr",                          12),
        ("auto_background_neutralize",            13),
        ("preview_background_neutralize",         13),
        ("auto_clahe",                            14),
        ("preview_clahe",                         14),
        ("auto_noise_reduction",                  15),
        ("preview_noise_reduction",               15),
        ("auto_curves",                           16),
        ("preview_curves",                        16),
        ("auto_hdr_compression",                  17),
        ("preview_hdr_compression",               17),
        ("auto_dark_enhance",                     18),
        ("preview_dark_enhance",                  18),
        ("auto_halo_suppression",                 19),
        ("preview_halo_suppression",              19),
        ("auto_combined",                         21),   # combine_stars_screen output
        ("preview_combined",                      21),
        # ── Final output ───────────────────────────────────────────────────────
        ("final",                                 22),
    ]

    try:
        files = sorted(run_dir.iterdir())
    except Exception:
        return

    renamed = 0
    for p in files:
        if not p.is_file():
            continue
        if p.name == "run.log":
            continue
        name = p.name
        # Only rename pipeline outputs — skip source stack FITS (e.g. "M 51_2026-05-24.fit")
        if not (name.startswith("auto_") or name.startswith("final")):
            continue
        # Idempotent: already has a two-digit numeric prefix
        if len(name) > 3 and name[:2].isdigit() and name[2] == "_":
            continue

        seq: int | None = None
        for pattern, num in _ORDER:
            if pattern in name:
                seq = num
                break

        if seq is None:
            continue

        new_name = f"{seq:02d}_{name}"
        dest = run_dir / new_name
        if dest.exists():
            continue  # avoid clobbering
        try:
            p.rename(dest)
            renamed += 1
        except Exception as e:
            log.debug(f"[autoprocess] _number_run_files: {name} → {new_name} failed: {e}")

    if renamed:
        log.info(f"[autoprocess] _number_run_files: {renamed} files renamed in {run_dir.name}")


def _object_type_from_db(target: str) -> str | None:
    """Map the DB ``targets.type`` (free-form catalog string) to a canonical object_type
    key, or None when the target/type is absent. The DB catalog type is authoritative —
    it fixes name-heuristic collisions like ``_object_type_from_name('M 109')`` matching
    the ``'m 10'`` globular substring and mis-routing the barred spiral to globular."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT type FROM targets WHERE target=? AND type IS NOT NULL "
                "AND TRIM(type)<>''", (target,)).fetchone()
    except Exception as e:
        log.debug(f"[autoprocess] _object_type_from_db failed for {target}: {e}")
        return None
    if not row or not row[0]:
        return None
    t = str(row[0]).strip().lower()
    if "galax" in t or "lenticular" in t or "interacting" in t:
        return "galaxy"
    if "globular" in t:
        return "globular_cluster"
    if "open cluster" in t or t == "cluster":
        return "open_cluster"
    if "planetary" in t:
        return "planetary_nebula"
    if "reflection" in t:
        return "reflection_nebula"
    if "supernova" in t or "remnant" in t:
        return "supernova_remnant"
    if "emission" in t or "h ii" in t or "hii" in t:
        return "emission_nebula"
    if "nebula" in t:        # dark/generic nebula → generic nebula path
        return "emission_nebula"
    return None


def _object_type_from_name(target: str) -> str:
    t = target.lower()
    if any(x in t for x in ["galaxy", "gal", "andromeda", "m 31", "m 33", "m 51",
                              "m 81", "m 82", "m 101", "m 104", "m 106", "m 64", "m 63",
                              "c 77", "ngc 5128", "ngc 4565", "ngc 891", "ngc 253"]):
        return "galaxy"
    if any(x in t for x in ["emission", "hii", "h ii", "sh2", "orion", "eagle", "rosette",
                              "lagoon", "omega", "trifid", "ngc 1976"]):
        return "emission_nebula"
    if any(x in t for x in ["reflection", "pleiades", "m 45"]):
        return "reflection_nebula"
    # NOTE: single-digit M-numbers (m 2, m 3, m 4, m 5, m 9) are deliberately omitted —
    # they are 3-char strings that prefix 2-digit entries ("m 2" in "m 27" = True → M27 Dumbbell
    # would be misclassified). Two-digit M-numbers (m 10+) are safe since no real Messier
    # target has 3 digits starting with those prefixes, except m 10x → caught by galaxy check first.
    # Folios are the authoritative source; this list is a name-only fallback for folio-less targets.
    if any(x in t for x in ["globular", "m 10", "m 12", "m 13", "m 14", "m 15",
                              "m 19", "m 22", "m 28", "m 30", "m 53", "m 54", "m 55",
                              "m 56", "m 62", "m 68", "m 69", "m 70", "m 71", "m 72",
                              "m 75", "m 79", "m 80", "m 92", "ngc 104", "ngc 288",
                              "ngc 362", "ngc 1851", "ngc 2808", "ngc 5139", "ngc 6752",
                              "ngc 7089", " gc "]):
        return "globular_cluster"
    if any(x in t for x in ["planetary", "ring", "m 57", "m 27", "cat's eye"]):
        return "planetary_nebula"
    if any(x in t for x in ["open cluster", "m 44", "m 45", "hyades", "beehive"]):
        return "open_cluster"
    return "unknown"


def _data_integrity_flags(target: str, source_fits) -> list[dict]:
    """Detect provenance/pointing problems so a run isn't credited or blamed for them.

    Returns a list of ``{"flag", "detail"}`` dicts (empty = clean). Catches the three
    cases the critique batch surfaced: a WRONG FIELD (plate solve disagrees with the
    catalog by a wide margin, e.g. M 97 66° off), a SPOOFED pre-EQ pointing (header Dec
    sign flipped vs the true catalog Dec — the ALP southern-location spoof that breaks
    seqplatesolve), and a MANUAL-MASTER source fed into the pipeline (e.g. IC 1805's
    hand-processed .xisf, whose framing/stretch are Henry's input, not a pipeline result).
    """
    flags: list[dict] = []
    from pathlib import Path as _P
    sp = _P(str(source_fits))

    # (c) manual-source: a non-pipeline container or a hand-master fed in as the source.
    if sp.suffix.lower() in (".xisf", ".tif", ".tiff", ".png") or "manual" in sp.name.lower():
        flags.append({"flag": "manual_source",
                      "detail": f"source {sp.name} is a manual/non-pipeline master — "
                                f"framing & stretch are user input, not pipeline output"})

    # DB catalog coords (authoritative target position).
    db_ra = db_dec = None
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT ra, dec FROM targets WHERE target=?", (target,)).fetchone()
        if row:
            db_ra, db_dec = row[0], row[1]
    except Exception as e:
        log.debug(f"[autoprocess] integrity: DB coords lookup failed for {target}: {e}")

    # Header center (solved CRVAL) + telescope pointing (OBJCTDEC).
    crval1 = crval2 = obj_dec = None
    try:
        from astropy.io.fits import getheader as _gh
        _h = _gh(str(sp))
        crval1 = _h.get("CRVAL1"); crval2 = _h.get("CRVAL2")
        _od = _h.get("OBJCTDEC")
        if _od is not None:
            try:
                _s = str(_od).strip().replace(":", " ")
                parts = _s.split()
                sign = -1.0 if _s.lstrip().startswith("-") else 1.0
                obj_dec = sign * (abs(float(parts[0])) + float(parts[1]) / 60.0
                                  + (float(parts[2]) / 3600.0 if len(parts) > 2 else 0.0))
            except Exception:
                obj_dec = None
    except Exception as e:
        log.debug(f"[autoprocess] integrity: header read failed for {target}: {e}")

    # (a) wrong-field: solved center vs catalog position, angular separation.
    if crval1 is not None and crval2 is not None and db_ra is not None and db_dec is not None:
        try:
            import math
            r1, d1 = math.radians(float(crval1)), math.radians(float(crval2))
            r2, d2 = math.radians(float(db_ra)), math.radians(float(db_dec))
            sep = math.degrees(math.acos(max(-1.0, min(1.0,
                math.sin(d1) * math.sin(d2)
                + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)))))
            if sep > 2.0:
                flags.append({"flag": "wrong_field",
                              "detail": f"solved center ({crval1:.3f},{crval2:.3f}) is "
                                        f"{sep:.1f}° from catalog {target} "
                                        f"({db_ra:.3f},{db_dec:.3f}) — frame is off-target"})
        except Exception as e:
            log.debug(f"[autoprocess] integrity: sep calc failed for {target}: {e}")

    # (b) pointing spoof: header Dec sign flipped vs true catalog Dec (pre-EQ southern
    # ALP location spoof). Real signal, fake coords — solve blind, don't seed from header.
    if obj_dec is not None and db_dec is not None and abs(db_dec) > 3.0 \
            and (obj_dec > 0) != (db_dec > 0):
        flags.append({"flag": "pointing_spoof",
                      "detail": f"header Dec {obj_dec:+.1f}° sign-flipped vs catalog "
                                f"{db_dec:+.1f}° — pre-EQ location spoof; seqplatesolve "
                                f"seed is unreliable, use a blind solve"})
    return flags


def _load_folio(target: str) -> dict | None:
    """Load per-target reference folio JSON if it exists. Delegates to
    folio_generator so catalog/synonym alias resolution stays centralized."""
    try:
        from nas_server.folio_generator import load_folio as _lf
        return _lf(target)
    except Exception as e:
        log.debug(f"[autoprocess] folio load failed for {target}: {e}")
        return None


def _estimate_autoprocess_minutes(target: str, workflow: str) -> int:
    """Estimate autoprocess duration in minutes from history, falling back to defaults."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT AVG(elapsed_s) FROM processing_runs WHERE target=? AND workflow=? LIMIT 5",
                (target, workflow)
            ).fetchone()
            if row and row[0]:
                return max(1, int(row[0] / 60))
    except Exception:
        pass
    defaults = {
        "quick_default": 5,
        "seestar_broadband": 20,
        "seestar_galaxy": 20,
        "seestar_nebula": 20,
        "experiment_full": 35,
        "linear_only": 8,
    }
    for key, mins in defaults.items():
        if key in workflow:
            return mins
    return 20


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

# BAYERPAT header value → OpenCV debayer code name
_BAYER_CV2 = {
    "GRBG": "COLOR_BayerGR2RGB",
    "RGGB": "COLOR_BayerRG2RGB",
    "BGGR": "COLOR_BayerBG2RGB",
    "GBRG": "COLOR_BayerGB2RGB",
}


def _strip_bayerpat_for_pi(source_fits: Path, run_dir: Path, dry_run: bool) -> Path:
    """Return a PI-ready copy of source_fits in run_dir.

    Siril stacks OSC frames in CFA mode (NAXIS=2, BAYERPAT=GRBG). PI and GraXpert
    receive a single-channel Bayer image which makes SPCC/CC fail. This function
    debayers CFA data to RGB before the pipeline starts.

    - NAXIS=2 + BAYERPAT: debayer with OpenCV → write RGB FITS (NAXIS=3).
    - NAXIS=3 + BAYERPAT: strip keyword only (data already RGB, keyword spurious).
    - Otherwise: copy as-is.
    """
    if dry_run:
        return source_fits
    try:
        import numpy as _np
        from astropy.io import fits as _fits
        dest = run_dir / source_fits.name
        with _fits.open(str(source_fits)) as hdus:
            hdr = hdus[0].header
            data = hdus[0].data.astype(_np.float32)
            bayerpat = str(hdr.get("BAYERPAT", "")).strip().upper()

            if data.ndim == 2 and bayerpat in _BAYER_CV2:
                import cv2 as _cv2
                cv2_code = getattr(_cv2, _BAYER_CV2[bayerpat], None)
                if cv2_code is not None:
                    lo, hi = float(data.min()), float(data.max())
                    scale = max(hi - lo, 1e-9)
                    raw16 = (((data - lo) / scale) * 65535).astype(_np.uint16)
                    rgb16 = _cv2.cvtColor(raw16, cv2_code)          # (H, W, 3)
                    rgb_f = rgb16.astype(_np.float32) / 65535.0 * scale + lo
                    rgb_chw = _np.moveaxis(rgb_f, -1, 0)            # (3, H, W)
                    new_hdr = hdr.copy()
                    for key in ("BAYERPAT", "BAYER", "XBAYEROFF", "YBAYEROFF"):
                        new_hdr.remove(key, ignore_missing=True)
                    _fits.PrimaryHDU(rgb_chw, header=new_hdr).writeto(str(dest), overwrite=True)
                    log.info(f"[autoprocess] debayered CFA ({bayerpat}) → RGB: {dest.name}")
                    return dest

            # 3D RGB or unknown — just strip Bayer keywords
            for key in ("BAYERPAT", "BAYER", "XBAYEROFF", "YBAYEROFF"):
                if key in hdr:
                    hdr.remove(key, ignore_missing=True)
            hdus.writeto(str(dest), overwrite=True)
        log.info(f"[autoprocess] prepared source FITS for PI: {dest.name}")
        return dest
    except Exception as e:
        log.warning(f"[autoprocess] FITS prepare-for-PI failed ({e}) — using original source")
        return source_fits


def _detect_lp_filter(fits_path) -> bool:
    """Return True if FITS FILTER header indicates a light-pollution filter."""
    try:
        from astropy.io import fits as _fits
        with _fits.open(str(fits_path), memmap=False) as hdul:
            filt = str(hdul[0].header.get("FILTER", "")).upper()
        LP_KEYWORDS = {"LP", "DUALBAND", "DUAL", "SHO", "NARROWBAND", "NB", "OPTOLONG"}
        return any(kw in filt for kw in LP_KEYWORDS)
    except Exception:
        return False


def _filter_label(fits_path) -> str:
    """Human-readable capture filter for Claude prompts (assessment + planning).

    The hardcoded "IRCUT" default that used to feed these prompts told Claude
    every SeeStar capture was broadband, even when the FITS said FILTER=LP. The
    S50's "LP" filter is its built-in DUAL-BAND filter (Hα ~656nm/20nm +
    OIII ~500nm/30nm) — effectively dual-narrowband — so treating it as broadband
    made Claude call dual-band emission data "weak Hα broadband" and recommend
    switching to the filter already in use. Return a truthful label so the
    assessments and the nonlinear planner reason about the real data.
    """
    try:
        from astropy.io import fits as _fits
        with _fits.open(str(fits_path), memmap=False) as hdul:
            raw = str(hdul[0].header.get("FILTER", "")).strip()
    except Exception:
        raw = ""
    up = raw.upper()
    if not up:
        return "IRCUT (broadband, assumed — no FILTER keyword)"
    if _detect_lp_filter(fits_path):
        return ("LP / dual-band (SeeStar built-in dual-narrowband: "
                "Hα ~656nm/20nm + OIII ~500nm/30nm — Hα-red and OIII-teal are "
                "real signal, not broadband noise)")
    if "IRCUT" in up or "UVIR" in up or "UV/IR" in up:
        return "IRCUT (broadband UV/IR-cut)"
    return f"{raw} (broadband)"


def auto_process(
    target: str,
    workflow: str = "seestar_broadband",
    dry_run: bool = False,
    experiment_mode: bool = False,
    source_file: str | None = None,
    manual_review: bool = False,
    _baseline_run: bool = False,
    extra_params: dict | None = None,
    physics_only: bool = False,
) -> dict:
    """
    Run the automated processing pipeline for `target`.

    Returns {"ok": bool, "target": str, "workflow": str, "steps_applied": [...],
             "final_scores": {...}, "output_path": str, "elapsed": float, "dry_run": bool}.

    experiment_mode: if True, steps that have experiment_variants run as experiments
    (all variants tried, Claude picks winner, results stored for learning).
    """
    from nas_server.config import settings
    from nas_server import telegram
    from nas_server.database import (
        get_processed_files, get_conn, log_processing_step, save_processing_run,
        is_raw_stack,
    )
    from nas_server import seti_astro
    from nas_server.claude_client import (
        assess_stacked_image,
        recommend_processing_step,
        generate_critical_eval,
    )
    from nas_server.experiments import run_experiment, get_learned_defaults

    import datetime as _dt
    from nas_server import api_diagnostics
    from nas_server import claude_client as _cc

    # Physics-only mode: also accepted via extra_params so the queue plumbing
    # (queue_manager.add_job → _run_job) needs no new fields to trigger a run.
    physics_only = bool(physics_only or (extra_params or {}).get("physics_only", False))
    _cc.set_physics_only(physics_only)
    if physics_only:
        log.info(f"[autoprocess] {target}: PHYSICS-ONLY mode — all AI calls disabled, "
                 "grades/recs from pixel metrics only")

    start_ts = time.time()
    started_at = _dt.datetime.utcnow().isoformat()
    _diag_mark = api_diagnostics.mark()  # baseline for this run's AI-call accounting
    _set_status(target, phase="starting", started_at=start_ts, workflow=workflow, dry_run=dry_run)
    clear_abort(target)  # drop any stale abort flag from a prior run

    try:
        ontology = _load_ontology()
    except Exception as e:
        err = f"Cannot load ontology: {e}"
        _set_status(target, phase="error", error=err)
        return {"ok": False, "error": err}

    wf = ontology["workflows"].get(workflow)
    if not wf:
        err = f"Unknown workflow: {workflow}. Available: {list(ontology['workflows'])}"
        _set_status(target, phase="error", error=err)
        return {"ok": False, "error": err}

    rules = ontology.get("iteration_rules", {})
    max_iters = rules.get("max_iterations_per_step", 3)
    improvement_threshold = rules.get("improvement_threshold", 0.5)
    force_variants: dict = dict(wf.get("force_variants", {}))  # step → variant id to use directly

    # ── Mode 1 (force a normally-optional step) + Mode 2 (branch a prior run) ──
    # Both are driven through extra_params so the queue plumbing needs no changes.
    #   extra_params["force_steps"]: list[str] — steps to force-apply this run
    #   extra_params["force_variants"]: {step: variant_id} — pin a variant this run
    #   extra_params["branch"]: {start_step, image, stars, suffix} — resume a prior
    #     run from its post-stretch starless snapshot, run the tail, save an alt final.
    _force_steps: set[str] = set((extra_params or {}).get("force_steps", []))
    force_variants.update((extra_params or {}).get("force_variants", {}))
    # Aesthetic steps (e.g. narrowband_norm) are a deliberate look, not a quality
    # play. When one is forced this run, exempt the run from the adaptive learning
    # backfill so its score never teaches Claude to skip/keep unrelated steps.
    _ps_defs = ontology["processing_steps"]
    _aesthetic_forced = any(
        _ps_defs.get(s, {}).get("aesthetic") or _ps_defs.get(s, {}).get("force_only")
        for s in _force_steps)
    if _aesthetic_forced:
        log.info(f"[autoprocess] {target}: aesthetic step forced — run exempt from "
                 f"adaptive outcome learning")
    _branch: dict = (extra_params or {}).get("branch") or {}
    _branch_start: str | None = _branch.get("start_step")
    _output_suffix: str = (_branch.get("suffix") or "").strip()
    if _branch_start:
        # Branch implies its own forced steps (e.g. narrowband_norm) come from
        # extra_params["force_steps"]; the caller sets both together.
        log.info(f"[autoprocess] {target}: BRANCH mode — start_step={_branch_start} "
                 f"image={_branch.get('image')} suffix={_output_suffix!r}")

    # Locate the source FITS
    lib = settings["seestar_library_path"]
    proc_dir = Path(lib) / target / "_processed"

    if _branch_start:
        # Branch mode: the "source" is the prior run's starless snapshot. It lives
        # in a run dir, not proc_dir, so reference it by absolute path.
        _branch_img = Path(_branch.get("image", ""))
        if not _branch_img.exists():
            err = f"Branch image not found: {_branch_img}"
            _set_status(target, phase="error", error=err)
            return {"ok": False, "error": err}
        source_fits = _branch_img
        latest = {"filename": _branch_img.name}
    elif source_file:
        # Explicit filename provided — use it directly, no DB needed
        latest = {"filename": source_file}
        source_fits = proc_dir / source_file
        if not source_fits.exists():
            err = f"Source FITS not found: {source_fits}"
            _set_status(target, phase="error", error=err)
            return {"ok": False, "error": err}
    else:
        # Try DB first, fall back to filesystem scan (handles empty worker DB).
        # Pick the newest RAW STACK, never a processed output: raw stacks and
        # auto_final/manual finals share _processed/ and this table, so files[0]
        # (newest row) would grab the processed final after a re-scan and either
        # trip the linear-input guard or reprocess an already-finished image.
        files = get_processed_files(target)
        raw_stacks = [f for f in files
                      if is_raw_stack(f.get("filename", ""), f.get("step"))]
        if raw_stacks:
            latest = raw_stacks[0]
            if files and not is_raw_stack(files[0].get("filename", ""), files[0].get("step")):
                log.info(f"[autoprocess] {target}: newest file "
                         f"{files[0].get('filename')!r} is a processed output — "
                         f"selecting latest raw stack {latest.get('filename')!r} instead")
        elif files:
            latest = files[0]
            log.warning(f"[autoprocess] {target}: no raw stack found in DB — "
                        f"falling back to newest file {latest.get('filename')!r}")
        else:
            # Scan _processed/ for the most recent FITS (by mtime), preferring raw
            # stacks so an empty-DB worker doesn't grab a leftover auto_final.fit.
            fits_candidates = sorted(
                proc_dir.glob("*.fit*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if proc_dir.exists() else []
            if not fits_candidates:
                err = f"No processed files found for {target}"
                _set_status(target, phase="error", error=err)
                return {"ok": False, "error": err}
            raw_candidates = [p for p in fits_candidates if is_raw_stack(p.name)]
            chosen = raw_candidates[0] if raw_candidates else fits_candidates[0]
            latest = {"filename": chosen.name}
            log.info(f"[autoprocess] {target}: DB empty — using filesystem scan → "
                     f"{latest['filename']}"
                     + ("" if raw_candidates else " (no raw stack found, using newest FITS)"))
        source_fits = proc_dir / latest["filename"]
        if not source_fits.exists():
            err = f"Source FITS not found: {source_fits}"
            _set_status(target, phase="error", error=err)
            return {"ok": False, "error": err}

    # Backfill frame count / integration time from the stacked FITS header when the
    # DB record lacks them (e.g. worker nodes with an empty local DB, branch mode, or
    # an explicit source_file). The stacker embeds STACKCNT (frames) and LIVETIME
    # (total integration seconds), so the header is the authoritative source.
    if latest.get("frame_count") in (None, 0) or latest.get("total_integration") in (None, 0):
        try:
            from astropy.io.fits import getheader as _gethdr_meta
            _mhdr = _gethdr_meta(str(source_fits))
            if latest.get("frame_count") in (None, 0):
                _sc = _mhdr.get("STACKCNT") or _mhdr.get("NCOMBINE") or _mhdr.get("NFRAMES")
                if _sc:
                    latest["frame_count"] = int(_sc)
            if latest.get("total_integration") in (None, 0):
                _lt = _mhdr.get("LIVETIME")
                if not _lt:
                    # LIVETIME absent: derive total as frames × per-frame exposure.
                    # TOTALEXP/EXPTIME on Seestar stacks hold the per-frame value, not
                    # the integrated total, so multiply by the frame count.
                    _per = _mhdr.get("EXPTIME") or _mhdr.get("TOTALEXP")
                    _fc = latest.get("frame_count")
                    if _per and _fc:
                        _lt = float(_per) * int(_fc)
                if _lt:
                    latest["total_integration"] = float(_lt)
        except Exception as _mhe:
            log.debug(f"[autoprocess] {target}: FITS header meta backfill failed: {_mhe}")
        # Last resort for legacy SASpro/PI stacks (no STACKCNT/LIVETIME header):
        # the integration seconds are encoded in the filename, e.g. "..._3290s_...".
        if latest.get("total_integration") in (None, 0):
            import re as _re_meta
            _m = _re_meta.search(r"_(\d+)s_", str(source_fits.name))
            if _m:
                latest["total_integration"] = float(_m.group(1))

    # LP (dual-narrowband) detection happens ONCE, on the raw stack: the crop
    # step strips FILTER from run-dir intermediates, so sniffing current_path
    # mid-pipeline silently returns False on every real run.
    _is_lp_run = _detect_lp_filter(source_fits)
    if _is_lp_run:
        log.info(f"[autoprocess] {target}: LP/dual-band filter detected on source stack "
                 f"({_filter_label(source_fits)})")

    # Integration-depth factor (0..1) gating stretch aggressiveness for this run:
    # thin/noisy stacks get a capped GHS alpha + a relaxed under-stretch floor so the
    # stretch doesn't chase the highlight target into the noise. See faint-image deep dive.
    _run_depth = _stack_depth_factor(target, latest.get("frame_count"))
    log.info(f"[autoprocess] {target}: stretch depth factor = {_run_depth:.2f} "
             f"(frames={latest.get('frame_count')})")

    object_type = _object_type_from_name(target)
    # DB catalog type beats the name heuristic (which collides on substrings like
    # 'm 10' ⊂ 'm 109'). Folio, if present, still wins below — it's hand-curated.
    _db_type = _object_type_from_db(target)
    if _db_type and _db_type != object_type:
        log.info(f"[autoprocess] {target}: object_type override "
                 f"{object_type!r} → {_db_type!r} (from DB targets.type)")
        object_type = _db_type
    obj_cfg = ontology["object_types"].get(object_type, ontology["object_types"]["unknown"])

    # ── Guard: refuse to run the linear pipeline on already-stretched input ──
    # A standard workflow expects a LINEAR stack and applies its own stretch. If the
    # source FITS is already non-linear (e.g. a branch snapshot fed in by mistake, or
    # a re-queued post-stretch image), running crop/deconvolution/StarXT/stretch on it
    # produces garbage and wastes an hour of GPU. Branch mode legitimately starts from
    # a stretched snapshot, so it is exempt (it fast-forwards past the linear steps).
    if not _branch_start and "stretch" in wf.get("steps", []) and not dry_run:
        try:
            from nas_server.image_analyzer import analyze as _analyze_guard
            _hist = _analyze_guard(str(source_fits)).get("histogram", {})
            if _hist.get("is_linear") is False:
                err = (f"Input appears already stretched (non-linear): median "
                       f"p50={_hist.get('p50'):.3f} ≥ 0.15. Workflow '{workflow}' expects "
                       f"a LINEAR stack and would re-run the full linear flow on it. "
                       f"Use the narrowband_norm/branch endpoint to resume a prior run, "
                       f"or pick a linear source stack.")
                log.error(f"[autoprocess] {target}: {err}")
                _set_status(target, phase="error", error=err)
                try:
                    telegram.send(
                        f"⛔ <b>AutoProcess refused</b>: <code>{target}</code>\n"
                        f"Source is already stretched (non-linear, p50="
                        f"{_hist.get('p50'):.3f}). Won't run the linear "
                        f"<code>{workflow}</code> flow on it.")
                except Exception:
                    pass
                return {"ok": False, "error": err, "non_linear_input": True}
        except Exception as _ge:
            log.warning(f"[autoprocess] {target}: non-linear input guard failed "
                        f"(continuing): {_ge}")

    # Create a timestamped run directory — all intermediates, previews, and
    # experiment variants live here. proc_dir is only used for source FITS lookup
    # and for the final output copy.
    run_stamp = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    _run_tag = f"{workflow}_{_output_suffix}" if _output_suffix else workflow
    run_dir = proc_dir / "runs" / f"{run_stamp}_{_run_tag}"
    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)

    folio = _load_folio(target)
    if folio:
        # Override object_type from folio if available — more reliable than name matching
        # Folio uses key "type"; fall back to "object_type" for older folios
        _folio_type = folio.get("type") or folio.get("object_type")
        if _folio_type and _folio_type != object_type:
            log.info(f"[autoprocess] {target}: object_type override "
                     f"{object_type!r} → {_folio_type!r} (from folio)")
            object_type = _folio_type
        log.info(f"[autoprocess] {target}: loaded reference folio — {folio.get('common_name', '')}")
    # Per-target sky band for the folio-aware nebula stretch pick (workflow 1.8.0).
    _folio_band: tuple[float, float] | None = None
    if folio:
        _fb = (folio.get("quality_thresholds") or {}).get("bg_level_range")
        if isinstance(_fb, (list, tuple)) and len(_fb) == 2:
            try:
                _folio_band = (float(_fb[0]), float(_fb[1]))
            except (TypeError, ValueError):
                _folio_band = None
    if not folio:
        try:
            from nas_server.database import add_agent_suggestion
            fname = target.replace(" ", "_").replace("/", "_") + ".json"
            add_agent_suggestion(
                description=f"Create folio for {target} — target is being processed but has no folio",
                file_hint=f"nas_server/target_folios/{fname}",
                source="planner",
                dedup_key=f"folio:{target}",
            )
        except Exception:
            pass

    log.info(f"[autoprocess] {target}: workflow={workflow} "
             f"object_type={object_type} dry_run={dry_run} run_dir={run_dir}")

    # Provenance/pointing QA — surface problems so the run isn't graded as if the
    # framing/coords were the pipeline's doing (wrong field, spoofed pre-EQ pointing,
    # or a hand-processed master fed in as the source). Recorded into run.log so the
    # critique layer sees them; non-fatal.
    integrity_flags: list[dict] = []
    try:
        integrity_flags = _data_integrity_flags(target, source_fits)
    except Exception as _ie:
        log.debug(f"[autoprocess] {target}: integrity check failed: {_ie}")
    for _f in integrity_flags:
        log.warning(f"[autoprocess] {target}: DATA-INTEGRITY {_f['flag']}: {_f['detail']}")
        if not dry_run:
            try:
                telegram.send(
                    f"⚠️ <b>Data-integrity flag</b>: <code>{target}</code>\n"
                    f"<b>{_f['flag']}</b> — {_f['detail']}")
            except Exception:
                pass

    if not dry_run:
        step_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(wf["steps"]))
        frame_str = latest.get("frame_count", "?")
        hours_str = round((latest.get("total_integration") or 0) / 3600, 1)
        eta_min = _estimate_autoprocess_minutes(target, workflow)
        telegram.send(
            f"🚀 <b>AutoProcess starting</b>: <code>{target}</code>\n"
            f"Workflow: <code>{workflow}</code> | {object_type}"
            f" | {frame_str} frames | {hours_str}h | ETA ~{eta_min}min\n\n"
            f"<b>Plan:</b>\n{step_list}"
        )

    baseline_result: dict = {}
    baseline_previews: dict[str, Path] = {}  # kept for API compat; no longer pre-populated

    current_path = _strip_bayerpat_for_pi(source_fits, run_dir, dry_run)
    # ASTAP re-solve at ingest (workflow 1.14.0): replace the Siril/PI solution —
    # which astropy readers misinterpret (axis-inverted/offset → SSSC wrong-star
    # matching, mirrored previews, drizzled XP no-op) — with an astropy-consistent
    # ASTAP solution on the working copy. PI/Siril steps are unaffected (they
    # re-solve internally). Failure is non-fatal: header stays as-was and the
    # 1.13.2 SSSC sanity gate remains the backstop.
    # DEFAULT OFF (1.14.1): the ingest solve regressed the golden trio — the corrected
    # WCS changes what every downstream consumer computes: crop selected wrong regions
    # (M 42 5.8 dim mids, M 13 6.2 blue corner glow), and previews FLIP-FLOPPED between
    # steps because PI steps re-solve internally and rewrite a WCS WITHOUT the PLTSOLVD
    # marker (mixed flip rules). Re-enable via settings.astap_ingest_solve=true ONLY
    # after the WCS-consumer audit (crop/canonical/PI-rewrite/renderer-marker). The
    # SSSC sanity gate (1.13.2) remains the green-cast protection meanwhile.
    try:
        from nas_server.config import settings as _ing_cfg
        # Default ON since 1.16.1 (F4 audit complete, golden trio re-baselined
        # 2026-07-04): nodes without the settings key — e.g. the laptop worker —
        # must solve at ingest too, or their runs revert to the Siril-convention
        # WCS and the migrated crop boxes land 36' off (round-5 failure mode).
        _do_ingest_solve = bool(_ing_cfg.get("astap_ingest_solve", True))
    except Exception:
        _do_ingest_solve = False
    if not dry_run and _do_ingest_solve:
        try:
            _asr = seti_astro.astap_solve(current_path)
            log.info(f"[autoprocess] {target}: ingest ASTAP solve "
                     f"{'OK' if _asr.get('ok') else 'FAILED (keeping original WCS)'} "
                     f"({_asr.get('elapsed_s')}s)")
        except Exception as _ase:
            log.warning(f"[autoprocess] {target}: ingest ASTAP solve error: {_ase}")
    _capture_filter = _filter_label(source_fits)  # truthful filter for Claude prompts
    log.info(f"[autoprocess] {target}: capture filter — {_capture_filter}")
    current_scores: dict = {}
    steps_applied: list[str] = []
    initial_scores: dict = {}
    step_records: list[dict] = []   # structured per-step data for the run report
    _nl_masks: dict = {}            # luminance mask params computed after stretch
    aux_paths: dict[str, Path] = {}  # secondary paths e.g. stars layer from remove_stars_split
    step_context: dict = {}         # carries key results between steps (e.g. scnr_amount)
    # Frame-fill context (spec_frame_fill_detection.md) — computed once at the stretch
    # boundary on the post-BGE pre-stretch starless image; mutable dict so the assess
    # closures (defined earlier in this function) read the updated value at call time.
    frame_fill_info: dict = {"eligible": False, "frame_fill": False}
    checkpoints: dict[str, Path] = {}  # fits path snapshots at assess steps for rollback
    # Non-degradation high-water mark: the best-scoring *publishable* (stars-present)
    # state seen during the run. Replaces the old early-stop break — instead of
    # truncating the pipeline (which skipped mandatory star recombination and shipped
    # M105 starless), we always run to completion and, if the final regressed below the
    # best publishable checkpoint, publish that checkpoint instead.
    _best_pub: dict = {"score": -1.0, "label": None, "path": None, "scores": None}
    stretch_retried: bool = False   # True after one stretch retry attempt
    _nonlinear: bool = False        # True after stretch step wins — all previews use direct render
    _xp_after_bge: bool = False     # passed the background_extraction step boundary
    _xp_extract_done: bool = False  # XP Ha/OIII extraction hook already ran (or no-opped)

    # Branch mode: the source is already a post-stretch starless image. Seed the
    # nonlinear state that the early steps would normally have established —
    # mark nonlinear (so previews render correctly), attach the prior run's stars
    # layer for combine_stars_screen, and compute the luminance masks the tail
    # steps (clahe, color_boost) consume.
    _branch_reached: bool = not _branch_start  # if not branching, every step runs
    if _branch_start and not dry_run:
        _nonlinear = True
        _bstars = _branch.get("stars")
        if _bstars and Path(_bstars).exists():
            aux_paths["stars"] = Path(_bstars)
            log.info(f"[autoprocess] {target}: branch seeded stars layer {Path(_bstars).name}")
        else:
            log.warning(f"[autoprocess] {target}: branch has no stars layer — "
                        "final will be starless")
        try:
            from nas_server.image_analyzer import analyze as _analyze_nl0
            from nas_server.tool_params import compute_lum_masks as _compute_lm0
            _nl_masks = _compute_lm0(_analyze_nl0(str(current_path)), object_type)
            log.info(f"[autoprocess] {target}: branch lum masks computed")
        except Exception as _be:
            log.warning(f"[autoprocess] {target}: branch lum mask compute failed: {_be}")

    def _meta() -> dict:
        return {
            "stackcnt": latest.get("frame_count"),
            "total_hours": round((latest.get("total_integration") or 0) / 3600, 2),
            "obs_date": None,
            "filter": _capture_filter,
            "object_type": object_type,
        }

    def _do_assess(label: str) -> dict:
        jpg = (run_dir if not dry_run else proc_dir) / f"auto_preview_{label}.jpg"
        # Use non-linear renderer for post-stretch assessments (final, post_stretch)
        _assess_gen = _generate_preview_nl if _nonlinear else _generate_preview
        ok = _assess_gen(current_path, jpg)
        if not ok or not jpg.exists():
            return {}

        # ── Physics-default routing (WS2) ────────────────────────────────────
        # Objective checkpoints grade from pixel metrics — NO API call. Only the
        # FINAL checkpoint calls Claude (Sonnet, WS5: reduce-only corrective +
        # narrowband palette context). See [[project-physics-default-pipeline]].
        #   initial / pre_stretch → grade_from_physics   (linear-calibrated)
        #   post_stretch          → _physics_grade_nonlinear (sky band + p99)
        #   final                 → assess_stacked_image (Sonnet), physics fallback
        scores: dict | None = None
        model_tag = "physics"
        if label == "final":
            _bl_step = _ASSESS_BASELINE_STEP.get(label)
            _bl_jpg = baseline_previews.get(_bl_step) if _bl_step else None
            # WS5 reduce-only corrective candidate: the last applied ELIGIBLE aesthetic-boost
            # step (it feeds straight into the star-combine tail, so re-running it on its saved
            # input and redoing the tail is sound — no dependent steps are skipped). Only
            # reducible boosts are eligible; calibration/structural steps are never handed to
            # the corrective loop. See [[project-physics-default-pipeline]].
            _corr_cands = [
                r["step"] for r in step_records
                if r.get("type") == "standard" and not r.get("skipped")
                and r.get("input_path") and r["step"] in _CORRECTIVE_ELIGIBLE_STEPS
            ][-1:]
            try:
                _meas_bg = None
                try:
                    if label in ("post_stretch", "final") or _nonlinear:
                        _meas_bg = _compute_stretch_stats(
                            current_path, object_type, _run_depth,
                            frame_fill=frame_fill_info["frame_fill"])
                except Exception:
                    _meas_bg = None
                scores = assess_stacked_image(target, str(jpg), _meta(),
                                              baseline_jpg=_bl_jpg,
                                              reference_folio=folio,
                                              corrective_candidates=_corr_cands or None,
                                              measured_bg=_meas_bg)
                if scores:
                    model_tag = "claude-sonnet-4-6"
            except Exception as _fe:
                log.warning(f"[autoprocess] {target}: final assess_stacked_image "
                            f"failed ({_fe}) — physics fallback")
                scores = None
        if not scores:
            try:
                if label in ("post_stretch", "final") or _nonlinear:
                    # Stretched image — grade from the stretch itself (sky band + p99),
                    # not the linear-calibrated metric grade which would false-abort.
                    scores = _physics_grade_nonlinear(
                        current_path, object_type, _run_depth,
                        frame_fill=frame_fill_info["frame_fill"])
                else:
                    from nas_server.claude_client import grade_from_physics
                    from nas_server.image_analyzer import analyze as _an
                    scores = grade_from_physics(_an(str(current_path)), _meta())
                log.info(f"[autoprocess] {target}: physics grade "
                         f"({label}, {scores.get('_source')}): "
                         f"overall={scores.get('overall')}/10")
            except Exception as _ge:
                log.warning(f"[autoprocess] {target}: physics grade failed "
                            f"({_ge}) — assessment skipped ({label})")
                return {}
        try:
            # Store numeric scores in the `scores` column (used by story page).
            # Keep a copy in `recommendation` for backward compat with other queries.
            scores_json = json.dumps(scores)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO claude_assessments "
                    "(target, phase, model, scores, recommendation, created_at) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                    (target, f"auto_process:{label}", model_tag, scores_json, scores_json),
                )
        except Exception as db_err:
            log.debug(f"[autoprocess] DB insert error: {db_err}")
        return scores

    def _run_final_corrective(corrective: dict, orig_final: Path) -> Path | None:
        """WS5 reduce-only loop-back: re-run ONE recently-applied step at REDUCED
        strength (or drop it), redo the star-combine tail, and accept the result only
        if the physics grade does not regress. Strictly reduce-only — never adds a step
        or raises a parameter. Single pass, one step. See [[project-physics-default-pipeline]].
        Returns the accepted combined FITS path, or None to keep the original final."""
        try:
            c_step = corrective.get("step")
            c_factor = corrective.get("factor")
            if not c_step or c_factor is None:
                return None
            if c_step not in _CORRECTIVE_ELIGIBLE_STEPS:
                # Never reduce/drop a calibration or structural step — that ruins a final
                # Claude already graded well. See [[project-physics-default-pipeline]].
                log.info(f"[autoprocess] {target}: corrective step {c_step!r} not an "
                         f"eligible aesthetic boost — keeping original final")
                return None
            c_factor = float(c_factor)
            if not (0.0 <= c_factor < 1.0):  # strictly reduce-only
                log.info(f"[autoprocess] {target}: corrective factor {c_factor} not "
                         f"reduce-only — ignored")
                return None
            rec = next((r for r in reversed(step_records)
                        if r.get("type") == "standard" and r.get("step") == c_step
                        and r.get("input_path")), None)
            if rec is None:
                log.info(f"[autoprocess] {target}: corrective step {c_step!r} not in "
                         f"records — skipped")
                return None
            in_path = Path(rec["input_path"])
            if not in_path.exists():
                log.info(f"[autoprocess] {target}: corrective input {in_path} missing — skipped")
                return None

            corr_starless = run_dir / f"auto_corrective_{c_step}.fit"
            _rk = _CORRECTIVE_REDUCE_KEY.get(c_step)
            _params = dict(rec.get("params") or {})
            _drop = (c_factor == 0.0) or not (
                _rk and isinstance(_params.get(_rk), (int, float)))
            if _drop:
                # No scalar strength knob (or factor 0): drop the step — corrected layer
                # is just its saved input. Still strictly reduce-only.
                shutil.copy2(str(in_path), str(corr_starless))
                log.info(f"[autoprocess] {target}: corrective — DROP {c_step} "
                         f"(factor={c_factor:.2f}, no scalar knob or drop requested)")
            else:
                _sd = ontology["processing_steps"].get(c_step, {})
                _fnn = rec.get("fn_name") or _sd.get("seti_astro_fn")
                _fn = getattr(seti_astro, _fnn, None) if _fnn else None
                if _fn is None:
                    log.info(f"[autoprocess] {target}: corrective — no fn for {c_step}, skipped")
                    return None
                _params[_rk] = type(_params[_rk])(_params[_rk] * c_factor)
                log.info(f"[autoprocess] {target}: corrective — {c_step} {_rk} "
                         f"×{c_factor:.2f} → {_params[_rk]}")
                _sig = set(inspect.signature(_fn).parameters)
                _vp = {k: v for k, v in _params.items() if k in _sig}
                _res = _fn(in_path, corr_starless, **_vp)
                if not _res.get("ok") or not corr_starless.exists():
                    log.warning(f"[autoprocess] {target}: corrective re-run of {c_step} failed")
                    return None

            # Redo the cheap tail. Whether the star layer must be screened back on depends
            # on WHERE the corrective step ran: optional nonlinear steps (hdr/dark_enhance/
            # color_sat/halo) run AFTER combine on the already-combined image, so re-running
            # one yields the final directly. Starless-phase steps (color_boost/clahe/curves)
            # run BEFORE combine, so the star layer must be re-screened on.
            _combine_idx = next((i for i, r in enumerate(step_records)
                                 if r.get("type") == "star_combine"), None)
            _rec_idx = next((i for i, r in enumerate(step_records) if r is rec), None)
            _pre_combine = (_combine_idx is not None and _rec_idx is not None
                            and _rec_idx < _combine_idx)
            stars_src = aux_paths.get("stars_stretched") or aux_paths.get("stars")
            corr_final = run_dir / "auto_corrective_final.fit"
            if _pre_combine and stars_src and Path(stars_src).exists():
                _fc = getattr(seti_astro, "combine_stars_screen", None)
                if _fc is None:
                    return None
                _cres = _fc(corr_starless, stars_src, corr_final)
                if not _cres.get("ok") or not corr_final.exists():
                    log.warning(f"[autoprocess] {target}: corrective combine failed")
                    return None
            else:
                corr_final = corr_starless
            try:
                _mute_sky(corr_final, object_type, target)
            except Exception as _mse:
                log.debug(f"[autoprocess] {target}: corrective sky mute failed: {_mse}")

            # Physics regrade gate — accept ONLY if the corrected final does not regress.
            try:
                _orig_g = _physics_grade_nonlinear(orig_final, object_type, _run_depth,
                                                   frame_fill=frame_fill_info["frame_fill"])
                _corr_g = _physics_grade_nonlinear(corr_final, object_type, _run_depth,
                                                   frame_fill=frame_fill_info["frame_fill"])
            except Exception as _ge:
                log.warning(f"[autoprocess] {target}: corrective regrade failed ({_ge}) "
                            f"— keeping original")
                return None
            _o = _to_float(_orig_g.get("overall", 0))
            _n = _to_float(_corr_g.get("overall", 0))
            log.info(f"[autoprocess] {target}: corrective physics regrade "
                     f"orig={_o:.2f} → corrected={_n:.2f}")
            if _n + 0.05 >= _o:  # no regression (small tolerance)
                telegram.send(f"♻️ <b>corrective</b>: <code>{target}</code> — reduced "
                              f"{c_step} ×{c_factor:.2f}, physics {_o:.1f}→{_n:.1f}, accepted")
                return corr_final
            log.info(f"[autoprocess] {target}: corrective regressed physics "
                     f"({_o:.2f}→{_n:.2f}) — keeping original")
            telegram.send(f"♻️ <b>corrective</b>: <code>{target}</code> — reduced {c_step} "
                          f"regressed physics ({_o:.1f}→{_n:.1f}), discarded")
            return None
        except Exception as _ce:
            log.warning(f"[autoprocess] {target}: corrective failed ({_ce}) — keeping original")
            return None

    # ── Adaptive planning state ─────────────────────────────────────────────
    import datetime as _dtnow
    _adaptive_run_id = f"{target}_{_dtnow.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    _adaptive_linear: dict = {}
    _adaptive_nonlinear: dict = {}
    _adaptive_param_nudges: dict = {}  # {step_name: {param: value}} from linear plan
    _skip_steps: set[str] = set()      # steps to skip (from nonlinear plan)
    force_variants = dict(force_variants)  # make mutable copy (was from wf.get)

    # ── Video documentary session ────────────────────────────────────────────
    _video: "VideoSession | None" = None  # type: ignore[name-defined]
    if not dry_run and not _baseline_run:
        try:
            from nas_server.video_logger import VideoSession as _VS
            _ts_vid = _dtnow.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            _video = _VS.for_run(target, run_id=f"{_ts_vid}_{workflow}")
            log.info(f"[video] {target}: session started → {_video._dir}")
            # Link this run dir to its video session so a later branch run can
            # find and prepend these frames (see branch import below).
            try:
                (run_dir / "video_session.txt").write_text(_video.session_id)
            except Exception:
                pass
            # Branch mode: prepend the source run's early-step frames (linear
            # phase + stretch) so the branch video is complete — the branch
            # fast-forwards past those steps and never re-renders them.
            if _branch_start and _branch.get("image"):
                try:
                    from nas_server.video_logger import find_run_video_session as _frvs
                    _src_run_dir = Path(_branch["image"]).parent
                    _src_sess = _frvs(_src_run_dir, target)
                    if _src_sess:
                        _src_vdir = _video._dir.parent / _src_sess
                        _video.import_frames_from(_src_vdir, stop_before_step=_branch_start)
                    else:
                        log.info(f"[autoprocess] {target}: branch source run has no "
                                 f"resolvable video session — branch video starts "
                                 f"at {_branch_start}")
                except Exception as _vie:
                    log.debug(f"[video] {target}: branch frame import failed: {_vie}")
                # Seed _last_image with the branched starting image so every
                # image-less frame (step intros) shows a real picture. A normal
                # run gets this seed from assess_initial; the branch skips it, so
                # without this the early branch steps render a blank panel.
                try:
                    _seed_jpg = run_dir / "auto_preview_branch_seed.jpg"
                    (_generate_preview_nl if _nonlinear else _generate_preview)(
                        current_path, _seed_jpg)
                    if _seed_jpg.exists():
                        _video._last_image = _seed_jpg
                except Exception as _vse:
                    log.debug(f"[video] {target}: branch seed preview failed: {_vse}")
        except Exception as _ve:
            log.debug(f"[video] {target}: session init failed: {_ve}")

    # ── Hook 1: Adaptive linear phase plan — REMOVED (physics-default) ───────
    # The Claude plan_linear_phase planner is gone. Linear-step variants come only
    # from the workflow's force_variants; param nudges are physics-computed at the
    # step. _adaptive_param_nudges stays empty, so the force_variant nudge path is a
    # no-op. See [[project-physics-default-pipeline]].

    # ── Mutable step queue — allows dynamic injection of optional steps ─────
    _steps_queue = list(wf["steps"])

    # ── Physics-default optional-step injection ─────────────────────────────
    # Replaces the removed Claude nonlinear planner: inject every optional nonlinear
    # step into the queue just before assess_final. The WS1 physics gate
    # (_physics_should_run) then decides run/skip per step from pixel metrics — no API.
    # See [[project-physics-default-pipeline]].
    if not dry_run:
        _opt_inject = [s for s in wf.get("optional_nonlinear_steps", [])
                       if s not in _steps_queue]
        if _opt_inject:
            _inj_at = next((i for i, s in enumerate(_steps_queue) if s == "assess_final"),
                           len(_steps_queue))
            for _s in reversed(_opt_inject):
                _steps_queue.insert(_inj_at, _s)
            log.info(f"[autoprocess] {target}: optional steps injected (physics-gated): "
                     f"{_opt_inject}")

    # The starless layer right before stars are screened back in — captured so the
    # hdr_compression gate can measure core highlight-clipping on the starless image
    # (stars otherwise pin the top of the normalized range and mask whether the
    # nebula/galaxy core is actually blown).
    _starless_pre_combine: Path | None = None

    # Walk workflow steps
    for step_name in _steps_queue:
        # Branch mode: fast-forward past every step before the branch point.
        # current_path was already seeded with the post-stretch starless snapshot.
        if not _branch_reached:
            if step_name == _branch_start:
                _branch_reached = True
                log.info(f"[autoprocess] {target}: branch resuming at {step_name}")
            else:
                continue
        # ── XP channel extraction hooks (workflow 1.9.x, Phase B1) ────────
        # Two phases, both LP-only side computations that never touch the
        # working image; every failure mode (no XP library / too few star
        # matches / ill-conditioned A — incl. old drizzled stacks whose WCS
        # doesn't match the pixel data) no-ops and the existing approximation
        # path runs unchanged.
        #   Phase 1 — first boundary after background_extraction AND
        #   color_calibration: fit the mixing matrix A from Gaia-XP star
        #   photometry. Stars must still be PRESENT, gradients removed, WCS
        #   intact. Must fit AFTER color_calibration: SSSC (LP default, 1.9.0)
        #   rescales channels per-pixel, so an A fit pre-calibration would be
        #   stale by the time it's NNLS-applied at the stretch boundary — fit
        #   and apply must see the same color domain.
        #   Phase 2 — at the stretch boundary (after remove_stars_linear, data
        #   still linear): NNLS-apply A to the now-STARLESS image so the
        #   xp_ha/xp_oiii channels feed nb_palette star-free, and the measured
        #   flux ratio reflects nebula rather than star continuum.
        if step_name in ("background_extraction", "color_calibration"):
            _xp_after_bge = True
        elif _xp_after_bge and "xp_fit" not in step_context:
            step_context["xp_fit"] = {"ok": False, "error": "not_attempted"}
            if not dry_run and _is_lp_run:
                _set_status(target, phase="xp_fit_matrix")
                log.info(f"[autoprocess] {target}: xp_fit_matrix — "
                         "Gaia-XP mixing-matrix fit on LP data")
                try:
                    from nas_server import seti_astro as _xp_sa
                    step_context["xp_fit"] = _xp_sa.xp_fit_matrix(current_path)
                except Exception as _xpe:
                    step_context["xp_fit"] = {"ok": False, "error": f"exception: {_xpe}"}
                _xpf = step_context["xp_fit"]
                if _xpf.get("ok"):
                    log.info(f"[autoprocess] {target}: xp_fit_matrix OK — "
                             f"{_xpf.get('n_used')}/{_xpf.get('n_stars')} stars, "
                             f"cond={_xpf.get('cond')}")
                else:
                    step_records.append({
                        "step": "xp_channel_extract", "type": "xp_extract",
                        "skipped": True,
                        "skip_reason": f"fit: {_xpf.get('error', 'unknown')}",
                    })
                    log.info(f"[autoprocess] {target}: xp_fit_matrix no-op "
                             f"({_xpf.get('error')}) — approximation path unchanged")
        if (step_name == "stretch" and not _xp_extract_done
                and step_context.get("xp_fit", {}).get("ok")):
            _xp_extract_done = True
            _set_status(target, phase="xp_channel_extract")
            try:
                from nas_server import seti_astro as _xp_sa
                _xpres = _xp_sa.xp_apply_matrix(
                    current_path, run_dir, step_context["xp_fit"]["A"])
            except Exception as _xpe:
                _xpres = {"ok": False, "error": f"exception: {_xpe}"}
            if _xpres.get("ok"):
                _xpf = step_context["xp_fit"]
                step_context["xp_extract"] = {**_xpf, **_xpres}
                step_records.append({
                    "step": "xp_channel_extract", "type": "xp_extract",
                    "params": {k: step_context["xp_extract"].get(k) for k in
                               ("A", "cond", "n_stars", "n_used", "counts",
                                "flux_ratio", "elapsed_s")},
                    "line1_path": _xpres.get("line1_path"),
                    "line2_path": _xpres.get("line2_path"),
                    "skipped": False,
                })
                log.info(f"[autoprocess] {target}: xp_channel_extract OK — "
                         f"{_xpf.get('n_used')}/{_xpf.get('n_stars')} stars, "
                         f"cond={_xpf.get('cond')}, "
                         f"flux_ratio={_xpres.get('flux_ratio')}")
                try:
                    telegram.send(
                        f"🌈 <b>xp_channel_extract</b>: <code>{target}</code>\n"
                        f"  Ha/OIII unmixed from {_xpf.get('n_used')} Gaia-XP stars "
                        f"(cond {_xpf.get('cond')}, ratio {_xpres.get('flux_ratio')})")
                except Exception:
                    pass
            else:
                step_records.append({
                    "step": "xp_channel_extract", "type": "xp_extract",
                    "skipped": True, "skip_reason": _xpres.get("error", "unknown"),
                })
                log.info(f"[autoprocess] {target}: xp_channel_extract no-op "
                         f"({_xpres.get('error')}) — approximation path unchanged")
        if step_name in _skip_steps:
            log.info(f"[autoprocess] {target}: adaptive skip {step_name}")
            continue
        # Narrowband path: skip SCNR entirely. On SeeStar duo-band OSC, OIII
        # (~500nm) registers across both green AND blue pixels, so SCNR removes
        # OIII as if it were a green cast — which suppressed the teal in earlier
        # NBN runs. NarrowbandNormalization does the channel balancing instead
        # (matches the manual workflow). Triggered when narrowband_norm is forced.
        if step_name == "scnr" and ("narrowband_norm" in _force_steps
                                     or "narrowband_hoo" in _force_steps
                                     or "nb_palette" in _force_steps
                                     or any(s.split("[", 1)[0] == "nb_palette"
                                            for s in steps_applied)):
            log.info(f"[autoprocess] {target}: skipping SCNR on narrowband path "
                     "(narrowband_norm/hoo/nb_palette active) — preserves OIII/teal")
            continue
        # nb_palette needs the true Ha/OIII channels from the xp_channel_extract
        # hook — skip silently when they don't exist this run (broadband capture,
        # failed fit, missing XP library). This also keeps experiment mode from
        # burning 4 variants that would all fail with xp_channels_missing.
        if (step_name == "nb_palette"
                and not step_context.get("xp_extract", {}).get("ok")):
            log.info(f"[autoprocess] {target}: nb_palette — no xp channels this "
                     "run, skipping")
            continue
        # Extreme-flux-ratio fallback: a bicolor palette needs a real OIII
        # channel. On strongly Hα-dominant fields (IC 1805: ratio 157), NNLS
        # correctly attributes ALL structure to Hα and leaves OIII a flat noise
        # pedestal (contrast 0.89 vs Hα 3.06) with NO morphology. Compositing
        # that pedestal paints the sky teal/blue and can never go black — there
        # is no OIII signal to map. So above the threshold we skip nb_palette and
        # let the proven narrowband_norm path (Ha≈R, OIII≈G/B + o3_boost) handle
        # it. nb_palette is for genuine bicolor targets only (Rosette, Veil).
        # (Henry, 2026-06-12: "Those all look bad. The sky should be black.")
        if step_name == "nb_palette":
            _fr = step_context.get("xp_extract", {}).get("flux_ratio")
            if _fr is not None and _fr > NB_PALETTE_MAX_FLUX_RATIO:
                log.info(f"[autoprocess] {target}: nb_palette skipped — flux_ratio "
                         f"{_fr:.1f} > {NB_PALETTE_MAX_FLUX_RATIO} (OIII is noise, "
                         "no bicolor signal); falling back to narrowband_norm")
                step_records.append({
                    "step": "nb_palette", "type": "palette", "skipped": True,
                    "skip_reason": f"flux_ratio {_fr:.1f} > "
                                   f"{NB_PALETTE_MAX_FLUX_RATIO} (OIII noise-only)",
                })
                continue
        # SPCC-success path: skip SCNR entirely. SPCC (color_calibration) applies a
        # LINKED multiplicative white balance with fixed per-channel ratios and a
        # neutral background. SCNR's Average-Neutral is a NON-linked per-pixel green
        # clip (green → min(green, (R+B)/2)) that breaks those ratios — it crushes the
        # SPCC-neutralised sky toward [0,0,0] and pulls real signal down, producing the
        # near-black uneven-pedestal cast (IC 434 trace: SPCC sky neutral → post-SCNR
        # sky [0.0, 0.0019, 0.0002], "G/R=140" artifact). With SPCC having already
        # neutralised the green, SCNR is redundant and harmful. Only run SCNR when SPCC
        # did NOT succeed (failed → .spcc_failed sentinel, or never ran → not in
        # steps_applied), where an uncalibrated green cast may still need clipping.
        # EXCEPTION — SSSC-calibrated LP runs (1.15.0, M 42 F4 post-mortem): when the
        # calibrator was SSSC (`.sssc_applied`), still run SCNR. SSSC solves star-based
        # per-channel GAINS — it cannot rebalance the dual-band OIII emission that lands
        # in G, so the nebula body stays green through the (linked) stretch. Every good
        # M 42 run (8.2, colour 7.5-7.8) had SCNR; all three SSSC-succeeded runs that
        # skipped it scored 5.5-6.2 with "dominant green cast" as issue #1. The NBN/
        # nb_palette skip above still protects true narrowband-palette runs.
        _sssc_calibrated = (run_dir / ".sssc_applied").exists()
        if (step_name == "scnr"
                and any(s.split("[", 1)[0] == "color_calibration" for s in steps_applied)
                and not (run_dir / ".spcc_failed").exists()
                and not _sssc_calibrated):
            log.info(f"[autoprocess] {target}: skipping SCNR — SPCC succeeded "
                     "(color_calibration applied, no .spcc_failed); SCNR would break the "
                     "linked SPCC white balance and crush the neutral sky")
            continue
        if step_name == "scnr" and _sssc_calibrated:
            log.info(f"[autoprocess] {target}: SCNR allowed — calibrator was SSSC "
                     "(gain-only, can't rebalance dual-band OIII-in-G emission)")
        # ── Cooperative abort check — honored at each step boundary ──────
        if is_abort_requested(target):
            clear_abort(target)
            log.warning(f"[autoprocess] {target}: ABORTED by user at step '{step_name}'")
            try:
                telegram.send(f"⏹ <b>{target}</b>: pipeline aborted by user at <code>{step_name}</code>")
            except Exception:
                pass
            _set_status(target, phase="aborted", error="aborted by user")
            return {
                "ok": False, "aborted": True, "target": target,
                "reason": f"aborted by user at step {step_name}",
                "steps_applied": list(steps_applied),
            }
        _set_status(target, phase=step_name)

        # Update video step panel state for every frame rendered this step
        if _video:
            _video.set_step_context(
                all_steps=[s for s in _steps_queue if s not in _skip_steps],
                completed_steps=list(steps_applied),
                current_step=step_name,
            )
            # One intro slide per step — image persists from last frame;
            # assess_ steps get a richer card below so skip their intro
            if not step_name.startswith("assess_"):
                _video.add_frame(
                    "process", step_name,
                    stage="process",
                    duration_s=1.5,
                )

        # ── assessment checkpoints ──────────────────────────────────────
        if step_name.startswith("assess_"):
            if _baseline_run:
                log.info(f"[autoprocess] {target}: skipping {step_name} — baseline run")
                continue
            label = step_name[len("assess_"):]
            log.info(f"[autoprocess] {target}: assess ({label})")
            if not dry_run:
                scores = _do_assess(label)
                # The pre_stretch checkpoint also drives the adaptive nonlinear
                # plan (Hook 2 below), which injects steps like dark_enhance and
                # is independent of the visual score. Proceed even if the visual
                # assessment came back empty (API/parse error) so a flaky
                # assessment can't silently drop the plan. The score-dependent
                # sub-blocks (abort gates, initial-only stats) don't apply to
                # pre_stretch, so an empty scores dict is harmless here.
                if scores or label == "pre_stretch":
                    current_scores.update(scores)
                    if label == "initial":
                        initial_scores = dict(scores)
                        # ── Channel stats diagnostic ──────────────────────────
                        # Log G/R and B/R ratios from the raw stack so we always
                        # know going in whether SPCC has easy or hard work to do.
                        try:
                            import numpy as _np_cs
                            from astropy.io import fits as _af_cs
                            with _af_cs.open(str(current_path), memmap=True) as _ch:
                                _cd = _ch[0].data
                                if _cd.ndim == 3 and _cd.shape[0] == 3:
                                    # Use 10th-percentile as sky background estimate
                                    # (robust against bright nebula biasing the mean)
                                    _r10 = float(_np_cs.percentile(_cd[0], 10))
                                    _g10 = float(_np_cs.percentile(_cd[1], 10))
                                    _b10 = float(_np_cs.percentile(_cd[2], 10))
                                    _gr  = _g10 / max(_r10, 1e-9)
                                    _br  = _b10 / max(_r10, 1e-9)
                                    _pedestal = float(_np_cs.min(_cd))
                                    _cs_msg = (
                                        f"📐 <b>Channel stats</b>: <code>{target}</code>\n"
                                        f"  Sky bkg (p10)  R={_r10:.5f}  G={_g10:.5f}  B={_b10:.5f}\n"
                                        f"  G/R={_gr:.2f}  B/R={_br:.2f}  pedestal={_pedestal:.5f}\n"
                                        + (f"  ⚠️ Green-dominant stack (G/R={_gr:.2f}) — SPCC critical"
                                           if _gr > 1.5 else
                                           f"  ✅ Channel balance looks reasonable (G/R={_gr:.2f})")
                                    )
                                    log.info(f"[autoprocess] {target}: channel stats "
                                             f"G/R={_gr:.2f} B/R={_br:.2f} pedestal={_pedestal:.5f}")
                                    telegram.send(_cs_msg)
                        except Exception as _cse:
                            log.debug(f"[autoprocess] {target}: channel stats failed: {_cse}")
                    overall = current_scores.get("overall", 0)
                    log.info(f"[autoprocess] {target}: {label} overall={overall}/10")
                    step_records.append({
                        "step": step_name, "type": "assess", "label": label,
                        "scores": dict(scores),
                        "preview": f"auto_preview_{label}.jpg",
                    })
                    score_lines = "\n".join(
                        f"  {k}: {v}/10" for k, v in scores.items()
                        if isinstance(v, (int, float)) and k not in ("input_tokens", "output_tokens")
                    )
                    telegram.send(
                        f"📊 <b>assess_{label}</b>: <code>{target}</code>\n{score_lines}"
                    )
                    assess_jpg = (run_dir if not dry_run else proc_dir) / f"auto_preview_{label}.jpg"
                    if assess_jpg.exists():
                        telegram.send_photo(str(assess_jpg), caption=f"{target} — {label}")

                    # Save checkpoint for potential rollback
                    checkpoints[label] = current_path

                    # ── Non-degradation high-water mark ───────────────────
                    # Track the best-scoring *publishable* state. "Publishable"
                    # excludes the still-linear assessments and the starless
                    # interval (after remove_stars_*, before combine_stars_*) —
                    # a starless image must never be shipped as the final. See
                    # [[project-physics-default-pipeline]].
                    _starless_now = (
                        any(s.startswith("remove_stars") for s in steps_applied)
                        and not any(s.startswith("combine_stars") for s in steps_applied)
                    )
                    _publishable = label not in ("initial", "pre_stretch") and not _starless_now
                    if _publishable and _to_float(overall) > _best_pub["score"]:
                        try:
                            _bp = run_dir / "auto_best_publishable.fit"
                            shutil.copy2(str(current_path), str(_bp))
                            _best_pub = {"score": _to_float(overall), "label": label,
                                         "path": _bp, "scores": dict(current_scores)}
                            log.info(f"[autoprocess] {target}: best publishable = "
                                     f"{label} ({_best_pub['score']}/10)")
                        except Exception as _bpe:
                            log.debug(f"[autoprocess] {target}: best-pub snapshot failed: {_bpe}")

                    # ── WS5 reduce-only corrective loop-back (final only) ──
                    # The Sonnet final eval may ask to dial back ONE over-aggressive
                    # step. Re-run it reduced, redo the tail, accept only if physics
                    # doesn't regress. See [[project-physics-default-pipeline]].
                    if label == "final" and isinstance(scores.get("corrective"), dict):
                        _new_final = _run_final_corrective(scores["corrective"], current_path)
                        if _new_final and _new_final.exists():
                            current_path = _new_final
                            checkpoints[label] = current_path
                            try:
                                _generate_preview_nl(current_path, assess_jpg)
                                if assess_jpg.exists():
                                    telegram.send_photo(
                                        str(assess_jpg),
                                        caption=f"{target} — final (corrected)")
                            except Exception as _cpe:
                                log.debug(f"[autoprocess] {target}: corrected preview "
                                          f"failed: {_cpe}")

                    # ── Video frame: assess checkpoint ────────────────────
                    if _video and assess_jpg.exists():
                        try:
                            _vid_stage = ("done" if label == "final"
                                          else "stack" if label == "initial"
                                          else "process")
                            _vid_step_label = {
                                "initial":    "Raw Stack",
                                "pre_stretch": "Pre-Stretch Assessment",
                                "post_stretch": "Post-Stretch Check",
                                "final":      "Final Result",
                            }.get(label, f"{label.replace('_',' ').title()} Assessment")
                            _vid_stats = {
                                k.replace("_", " ").title(): f"{v}/10"
                                for k, v in scores.items()
                                if isinstance(v, (int, float))
                                and k not in ("input_tokens", "output_tokens", "overall")
                            }
                            _vid_overall = scores.get("overall")
                            _vid_prev_score = (
                                initial_scores.get("overall") if label != "initial" else None
                            )
                            _vid_delta = (
                                (_vid_overall - _vid_prev_score)
                                if (_vid_overall and _vid_prev_score) else None
                            )
                            # Histogram inset for post-stretch and final frames
                            _assess_viz: dict | None = None
                            if label in ("post_stretch", "final") and current_path.exists():
                                _ss = step_context.get("stretch_stats", {})
                                _assess_viz = {
                                    "type":       "histogram",
                                    "image_path": str(current_path),
                                    "percentiles": {
                                        "sky_bg": _ss.get("bg_level"),
                                        "p95":    _ss.get("p95"),
                                        "p99":    _ss.get("p99"),
                                    },
                                }
                            _video.add_frame(
                                act=_vid_stage,
                                step_name=f"assess_{label}",
                                image_path=assess_jpg,
                                stage=_vid_stage,
                                step_label=_vid_step_label,
                                caption=(
                                    f"score {_vid_overall}/10"
                                    if _vid_overall else ""
                                ),
                                stats=_vid_stats,
                                score=_vid_overall,
                                score_delta=_vid_delta,
                                duration_s=4.0 if label in ("initial", "final") else 3.0,
                                data_viz=_assess_viz,
                            )
                        except Exception as _vfe:
                            log.debug(f"[video] assess frame failed: {_vfe}")

                    # ── Hook 2: Adaptive non-linear plan — REMOVED (physics-default) ──
                    # The Claude plan_nonlinear_phase planner is gone. Optional nonlinear
                    # steps are injected deterministically up front (see queue build) and
                    # the WS1 physics gate (_physics_should_run) decides run/skip per step.
                    # See [[project-physics-default-pipeline]].

                    # Abort gates — don't waste time processing unrecoverable data
                    abort_initial = rules.get("abort_if_initial_below", 2)
                    abort_post_stretch = rules.get("abort_if_post_stretch_below", 3)
                    warn_final = rules.get("warn_if_final_below", 5)
                    if label == "initial" and overall <= abort_initial:
                        msg = (f"⛔ <b>Aborting {target}</b>: initial quality {overall}/10 "
                               f"is at or below threshold ({abort_initial}). "
                               f"Data likely has calibration problems that post-processing cannot fix.")
                        log.warning(f"[autoprocess] {target}: ABORT — initial score {overall} <= {abort_initial}")
                        telegram.send(msg)
                        _set_status(target, phase="aborted", error=f"initial score {overall} too low")
                        return {
                            "ok": False, "aborted": True, "target": target,
                            "reason": f"initial score {overall}/10 below abort threshold {abort_initial}",
                            "initial_scores": initial_scores,
                        }
                    if label == "post_stretch" and overall <= abort_post_stretch:
                        pre_stretch = step_context.get("pre_stretch_fits")
                        if not stretch_retried and pre_stretch and Path(pre_stretch).exists():
                            # One retry: re-stretch with boosted params before aborting
                            stretch_retried = True
                            telegram.send(
                                f"⚠️ <b>{target}</b>: stretch quality {overall}/10 is low "
                                f"— retrying with boosted parameters..."
                            )
                            log.info(f"[autoprocess] {target}: post_stretch score {overall} low — "
                                     "retrying stretch with boosted params")
                            try:
                                from nas_server.image_analyzer import analyze as _ra
                                from nas_server.tool_params import compute_ghs as _rcghs
                                from nas_server.tool_params import compute_stat_stretch as _rcstat
                                _rs = _ra(str(pre_stretch))
                                _rghs_p = _rcghs(_rs, object_type)
                                _rstat_p = _rcstat(_rs, object_type)
                            except Exception:
                                _rghs_p = {"alpha": 7.0, "beta": 0.0, "gamma": 3.0, "pivot": 0.025}
                                _rstat_p = {"target_median": 0.13, "blackpoint_sigma": 4.0}

                            retry_variants = []
                            for _rname, _rfn in [
                                ("ghs_boost",  lambda i, o: seti_astro.ghs_stretch(
                                    i, o,
                                    alpha=round(_rghs_p["alpha"] * 1.6, 2),
                                    beta=_rghs_p.get("beta", 0.0),
                                    gamma=_rghs_p.get("gamma", 3.0),
                                    pivot=_rghs_p["pivot"],
                                )),
                                ("stat_boost", lambda i, o: seti_astro.stat_stretch(
                                    i, o,
                                    target_median=min(_rstat_p["target_median"] * 1.4, 0.35),
                                    blackpoint_sigma=_rstat_p.get("blackpoint_sigma", 5.0),
                                )),
                            ]:
                                _ro = run_dir / f"auto_stretch_retry_{_rname}.fit"
                                _rj = run_dir / f"auto_stretch_retry_{_rname}_preview.jpg"
                                try:
                                    _rr = _rfn(pre_stretch, _ro)
                                    if _rr.get("ok") and _ro.exists() and _generate_preview_nl(_ro, _rj):
                                        retry_variants.append({"name": _rname, "fits": _ro,
                                                               "jpeg_path": str(_rj),
                                                               "command": f"{_rname}_stretch"})
                                except Exception as _re:
                                    log.warning(f"[autoprocess] stretch retry {_rname} failed: {_re}")

                            if retry_variants:
                                # Pick best retry variant by physics (no Claude).
                                # See [[project-physics-default-pipeline]].
                                _rwinner = retry_variants[0]["name"]
                                if len(retry_variants) >= 2:
                                    _rp = _physics_pick_stretch(
                                        retry_variants, run_dir, object_type, _run_depth,
                                        folio_band=_folio_band,
                                        frame_fill=frame_fill_info["frame_fill"])
                                    if _rp:
                                        _rwinner = _rp
                                _rbest = next((v for v in retry_variants if v["name"] == _rwinner),
                                             retry_variants[0])
                                current_path = _rbest["fits"]

                                # Re-grade the retried stretch from its own pixel stats
                                # (physics-default — no API). See [[project-physics-default-pipeline]].
                                _retry_jpg = run_dir / "auto_preview_post_stretch_retry.jpg"
                                _generate_preview_nl(current_path, _retry_jpg)
                                _retry_scores = _physics_grade_nonlinear(
                                    current_path, object_type, _run_depth,
                                    frame_fill=frame_fill_info["frame_fill"])
                                _retry_overall = _to_float(_retry_scores.get("overall", 0))
                                telegram.send(
                                    f"🔄 <b>stretch retry</b>: <code>{target}</code> "
                                    f"({_rwinner}) → {_retry_overall}/10"
                                )
                                if _retry_jpg.exists():
                                    telegram.send_photo(str(_retry_jpg),
                                                        caption=f"{target} — stretch retry: {_rwinner}")
                                steps_applied.append(f"stretch_retry[{_rwinner}]")
                                if _retry_scores:
                                    current_scores.update(_retry_scores)
                                overall = _retry_overall

                        if overall <= abort_post_stretch:
                            msg = (f"⛔ <b>Aborting {target}</b>: post-stretch quality {overall}/10 "
                                   f"is at or below threshold ({abort_post_stretch}) even after retry. "
                                   f"Stretch may have failed.")
                            log.warning(f"[autoprocess] {target}: ABORT — post_stretch score {overall} "
                                        f"<= {abort_post_stretch} after retry")
                            telegram.send(msg)
                            _set_status(target, phase="aborted", error=f"post_stretch score {overall} too low")
                            return {
                                "ok": False, "aborted": True, "target": target,
                                "reason": f"post_stretch score {overall}/10 below abort threshold {abort_post_stretch}",
                                "steps_applied": steps_applied,
                            }
                    if label == "final" and overall < warn_final:
                        telegram.send(
                            f"⚠️ <b>Low quality result</b>: <code>{target}</code> "
                            f"finished at {overall}/10 (warn threshold: {warn_final}/10)"
                        )

                    # Early-stop REMOVED. A high checkpoint score used to break the
                    # loop, but that skipped mandatory tail steps — in starless
                    # workflows it shipped the final without recombining stars
                    # (M105). The pipeline now always runs to completion; the
                    # non-degradation guard at finalise restores the best
                    # publishable checkpoint if the final regressed below it.
            continue

        # ── stretch: multi-variant pick ─────────────────────────────────
        if step_name == "stretch":
            preferred = obj_cfg.get("stretch_preference", "stat")
            log.info(f"[autoprocess] {target}: stretch (preferred={preferred})")
            if dry_run:
                steps_applied.append(f"stretch[{preferred}]")
                continue

            # Frame-fill detection (spec_frame_fill_detection.md): once per run, on the
            # post-BGE pre-stretch linear starless image. Flips every sky-anchored
            # mechanism downstream (picker, clamp, feedback, grades) to the dark anchor.
            try:
                _ff_arcmin = None
                try:
                    from nas_server.planner import _folio_info as _ff_fi
                    _ff_arcmin = _ff_fi(target).get("angular_size_arcmin")
                except Exception:
                    _ff_arcmin = None
                frame_fill_info.update(
                    _frame_fill_detect(current_path, object_type,
                                       target_arcmin=_ff_arcmin))
                if frame_fill_info.get("small_target_arcmin"):
                    log.info(f"[autoprocess] {target}: frame-fill INELIGIBLE — "
                             f"target {_ff_arcmin}' < 30' (too small to fill frame)")
                if frame_fill_info.get("eligible"):
                    log.info(f"[autoprocess] {target}: frame-fill detect — "
                             f"frame_fill={frame_fill_info['frame_fill']} "
                             f"(struct={frame_fill_info.get('structure_over_noise')} "
                             f"sep={frame_fill_info.get('separation_frac')} "
                             f"coverage={frame_fill_info.get('coverage')})")
            except Exception as _ffe:
                log.warning(f"[autoprocess] {target}: frame-fill detect failed: {_ffe}")

            # force_variant: workflow bypasses multi-variant experiment (e.g. quick_default)
            forced_stretch_id = force_variants.get("stretch")
            if forced_stretch_id:
                stretch_step_def = ontology["processing_steps"].get("stretch", {})
                forced_variant = next(
                    (v for v in stretch_step_def.get("experiment_variants", [])
                     if v["id"] == forced_stretch_id), None
                )
                if forced_variant:
                    log.info(f"[autoprocess] {target}: stretch — force_variant={forced_stretch_id}")
                    from nas_server.experiments import _run_variant
                    forced_out = run_dir / f"auto_stretch_{forced_stretch_id}.fit"
                    # For STF variants, merge physics-computed params (adaptive shadow_clip_k)
                    forced_variant = dict(forced_variant)
                    forced_variant["params"] = dict(forced_variant.get("params", {}))
                    if forced_stretch_id.startswith("stf_"):
                        try:
                            from nas_server.image_analyzer import analyze as _stf_analyze
                            from nas_server.tool_params import compute_stf_params as _cstf
                            _stf_stats = _stf_analyze(str(current_path))
                            _stf_p = _cstf(_stf_stats, object_type)
                            forced_variant["params"].update(_stf_p)
                            log.info(f"[autoprocess] {target}: STF physics params — "
                                     f"target_bg={_stf_p['target_bg']:.3f} "
                                     f"shadow_clip_k={_stf_p['shadow_clip_k']:.3f}")
                        except Exception as _stf_e:
                            log.warning(f"[autoprocess] {target}: STF params compute failed: {_stf_e}")
                    fv_res, forced_out, _sky_stats = _stretch_with_sky_feedback(
                        forced_variant, current_path, forced_out, object_type,
                        _run_variant, target=target,
                        frame_fill=frame_fill_info["frame_fill"],
                    )
                    if fv_res.get("ok") and forced_out.exists():
                        step_context["pre_stretch_fits"] = current_path
                        _wcs_src_prev = current_path  # audit F2
                        current_path = forced_out
                        try:    # WCS/marker continuity (audit F2) — forced stretch
                            from nas_server.seti_astro import _preserve_celestial_wcs as _pwx
                            _pwx(_wcs_src_prev, current_path)
                        except Exception:
                            pass
                        _nonlinear = True  # stretch complete — all subsequent previews use direct render
                        steps_applied.append(f"stretch[{forced_stretch_id}]")
                        if _sky_stats:
                            log.info(f"[autoprocess] {target}: stretch force_variant done "
                                     f"(sky bg={_sky_stats.get('bg_level',0):.3f}, "
                                     f"target {_sky_stats.get('bg_target','?')}, "
                                     f"{'on-target' if _sky_stats.get('bg_ok') else 'best-effort'})")
                        else:
                            log.info(f"[autoprocess] {target}: stretch force_variant done")
                        if _video:
                            try:
                                _st_vjpg = run_dir / "auto_preview_stretch_vframe.jpg"
                                _generate_preview_nl(current_path, _st_vjpg)
                                if _st_vjpg.exists():
                                    _video.add_frame(
                                        act="process", step_name="stretch",
                                        image_path=_st_vjpg, stage="process",
                                        duration_s=2.5)
                            except Exception as _stve:
                                log.debug(f"[video] stretch fv frame failed: {_stve}")
                        continue
                    else:
                        log.warning(f"[autoprocess] {target}: stretch force_variant {forced_stretch_id} failed "
                                    f"— falling through to experiment: {fv_res.get('error','')}")

            step_context["pre_stretch_fits"] = current_path  # saved for retry if post_stretch aborts

            # When SPCC failed upstream, color_calibration drops a `.spcc_failed`
            # sentinel in run_dir (seti_astro.spcc). The green cast was never
            # calibrated out, and a LINKED stretch preserves it through every
            # variant → the post_stretch gate aborts (NGC 7000, 2026-06-01). With no
            # SPCC white balance left to protect, switch the stat variants to UNLINKED
            # (per-channel) so each channel's background is neutralised independently,
            # killing the green. See [[feedback-linked-color]] for the linked-vs-unlinked
            # rule and its SPCC-failed exception. STF is already unlinked by default.
            _spcc_failed = (run_dir / ".spcc_failed").exists()
            _stat_linked = not _spcc_failed
            if _spcc_failed:
                # The sentinel means "no calibrated white balance to protect" — either a
                # real SPCC failure OR the deliberate LP skip (lp_no_spcc: SPCC's
                # broadband white ref is wrong for dual-band data, 1.16.0). Distinguish
                # them in the log; both correctly get the unlinked/SCNR semantics.
                try:
                    _sc = (run_dir / ".spcc_failed").read_text()
                except Exception:
                    _sc = ""
                _why = ("LP data — SPCC deliberately skipped (broadband white ref "
                        "wrong for dual-band)") if "lp_no_spcc" in _sc else \
                       "SPCC failed"
                log.warning(f"[autoprocess] {target}: {_why} (.spcc_failed present) — "
                            f"stat/stat_bright stretch → UNLINKED to neutralise green cast")

            # Compute stats-driven params once for all variants
            _ghs_p  = {"alpha": 5.0, "beta": 0.0, "gamma": 3.0, "pivot": 0.025}
            _stat_p = {"target_median": 0.08, "blackpoint_sigma": 4.0}
            try:
                from nas_server.image_analyzer import analyze as _analyze
                from nas_server.tool_params import compute_ghs as _cghs
                from nas_server.tool_params import compute_stat_stretch as _cstat
                _s = _analyze(str(current_path))
                _ghs_p  = _cghs(_s, object_type)
                _stat_p = _cstat(_s, object_type)
                # Depth-gated alpha cap: a thin/noisy stack can't support an aggressive
                # GHS without lifting the noise floor. Scale alpha 0.65×..1.0× by depth
                # (0.65× at depth 0, full at depth 1). The paired under-stretch-floor
                # relaxation (_compute_stretch_stats) lets the gentler result win the pick.
                if _run_depth < 1.0:
                    _alpha0 = _ghs_p["alpha"]
                    _ghs_p["alpha"] = round(_alpha0 * (0.65 + 0.35 * _run_depth), 2)
                    log.info(f"[autoprocess] {target}: depth {_run_depth:.2f} → GHS alpha "
                             f"capped {_alpha0:.2f} → {_ghs_p['alpha']:.2f}")
                log.info(f"[autoprocess] {target}: stretch params — "
                         f"pivot={_ghs_p['pivot']:.6f} alpha={_ghs_p['alpha']:.2f} "
                         f"target_median={_stat_p['target_median']:.3f}")
            except Exception as _se:
                log.warning(f"[autoprocess] {target}: stretch stats failed: {_se}")

            def _run_ghs(inp, out, alpha_mult=1.0):
                r = seti_astro.ghs_stretch(
                    inp, out,
                    alpha=round(_ghs_p["alpha"] * alpha_mult, 2),
                    beta=_ghs_p.get("beta", 0.0),
                    gamma=_ghs_p.get("gamma", 3.0),
                    pivot=_ghs_p["pivot"],
                )
                # Lift a genuinely under-stretched result (dim highlights) before the
                # sky-band correction pulls the black point down.
                if r.get("ok") and Path(out).exists():
                    import numpy as np
                    from astropy.io import fits as _afits
                    with _afits.open(str(out)) as _h:
                        _med = float(np.median(_h[0].data))
                    if _med < 0.05:
                        log.info(f"[autoprocess] GHS under-stretched (median={_med:.3f}), retrying alpha x1.6")
                        r = seti_astro.ghs_stretch(inp, out,
                            alpha=round(_ghs_p["alpha"] * alpha_mult * 1.6, 2),
                            beta=_ghs_p.get("beta", 0.0),
                            gamma=_ghs_p.get("gamma", 3.0),
                            pivot=_ghs_p["pivot"],
                        )
                # Land the sky in the object-type band via a black-point pull. This
                # replaces the old median>0.30 alpha-reduction retry, which raised the
                # sky (lower alpha → brighter sky) and never reached band.
                if r.get("ok") and Path(out).exists():
                    _ghs_sky_correct(Path(out), object_type, target)
                return r

            def _run_mas(inp, out):
                try:
                    from nas_server.pixinsight import run_postprocess as _pi_pp
                    r = _pi_pp(
                        target=target, input_fits=str(inp), output_path=str(out),
                        mas=True,
                        dbe=False, gradient_correction=False, color_calibration=False,
                        bgn=False, spcc=False, mlt=False, tgv=False,
                        bxt=False, nxt=False, starxt=False,
                        ht=False, scnr=False, hdrmt=False, lhe=False,
                        color_sat=False, curves=False, cms=False, morph=False,
                        timeout=300,
                    )
                    # PI MaskedStretch writes an int32-scaled FITS (BZERO=2^31, values up to
                    # ~4.27e9), NOT normalized 0-1 like every other stretch variant. Left raw
                    # it breaks _compute_stretch_stats (garbage bg/p99 → MAS can never be
                    # picked) and would corrupt downstream float-0-1 steps. Rescale to 0-1 by
                    # the white point so MAS is a usable, comparable variant. See
                    # [[project-ghs-wrapper-limits]].
                    if r.get("ok") and Path(out).exists():
                        try:
                            from astropy.io import fits as _fits
                            import numpy as np
                            with _fits.open(str(out)) as _hd:
                                _d = _hd[0].data.astype(np.float32)
                                _hdr = _hd[0].header
                            _mx = float(np.nanmax(_d))
                            if _mx > 1.5:
                                _d = np.clip(_d / _mx, 0.0, 1.0).astype(np.float32)
                                for _k in ("BZERO", "BSCALE", "DATAMIN", "DATAMAX"):
                                    _hdr.pop(_k, None)
                                _fits.writeto(str(out), _d, _hdr, overwrite=True)
                                log.info(f"[autoprocess] MAS stretch normalized to 0-1 "
                                         f"(was int-scaled, max={_mx:.3g})")
                        except Exception as _ne:
                            log.warning(f"[autoprocess] MAS normalize failed: {_ne}")
                    return r
                except Exception as e:
                    return {"ok": False, "error": str(e)}

            stretch_fns = {
                "stat":        lambda i, o: seti_astro.stat_stretch(
                                   i, o,
                                   target_median=_stat_p["target_median"],
                                   blackpoint_sigma=_stat_p.get("blackpoint_sigma", 5.0),
                                   linked=_stat_linked, curves_boost=0.05),
                "stat_bright": lambda i, o: seti_astro.stat_stretch(
                                   i, o,
                                   target_median=min(_stat_p["target_median"] * 1.4, 0.35),
                                   blackpoint_sigma=_stat_p.get("blackpoint_sigma", 5.0),
                                   linked=_stat_linked, curves_boost=0.05),
                "ghs":         lambda i, o: _run_ghs(i, o),
                "ghs_strong":  lambda i, o: _run_ghs(i, o, alpha_mult=1.5),
                "ghs_soft":    lambda i, o: _run_ghs(i, o, alpha_mult=0.7),
                "stf":         lambda i, o: seti_astro.stf_stretch(
                                   i, o,
                                   target_bg=0.09 if object_type not in {"galaxy"} else 0.07),
                "veralux":     lambda i, o: seti_astro.veralux_stretch(
                                   i, o, target_bg=_stat_p["target_median"],
                                   color_grip=1.0),
                "veralux_strong": lambda i, o: seti_astro.veralux_stretch(
                                   i, o,
                                   target_bg=min(_stat_p["target_median"] * 1.3, 0.30),
                                   color_grip=1.0),
                "mas":         _run_mas,
            }
            variants = []
            _stretch_scored: list = []   # per-candidate physics metrics for run.log
            for sname, sfn in stretch_fns.items():
                out = run_dir / f"auto_stretch_{sname}.fit"
                jpg = run_dir / f"auto_stretch_{sname}_preview.jpg"
                try:
                    r = sfn(current_path, out)
                    if r.get("ok") and out.exists() and _generate_preview_nl(out, jpg):
                        variants.append({"name": sname, "jpeg_path": str(jpg),
                                         "command": f"{sname}_stretch"})
                except Exception as e:
                    log.warning(f"[autoprocess] stretch variant {sname} failed: {e}")

            winner_name = preferred
            if len(variants) >= 2:
                # Physics-default stretch pick (Claude pick_best_stretch removed).
                # Don't blindly fall to `preferred` (=ghs for galaxies) — that
                # under-stretched M51. Choose by physics: sky-band + p99 health,
                # reject dead channels. See [[project-physics-default-pipeline]].
                try:
                    from nas_server.config import settings as _cfg
                    _vis_tb = bool(_cfg.get("stretch_vision_tiebreak", False))
                except Exception:
                    _vis_tb = False
                _phys_winner = _physics_pick_stretch(variants, run_dir, object_type,
                                                     _run_depth, folio_band=_folio_band,
                                                     vision_tiebreak=_vis_tb,
                                                     frame_fill=frame_fill_info["frame_fill"],
                                                     scored_out=_stretch_scored)
                if _phys_winner:
                    winner_name = _phys_winner
                    log.info(f"[autoprocess] {target}: stretch winner={winner_name} "
                             "(physics pick)")

            # ── Colour-preservation guard ────────────────────────────────────
            # When SPCC succeeded, stretch variants are LINKED (one luminance-derived curve
            # applied to R,G,B), so they preserve the SPCC-calibrated channel ratio. (When
            # SPCC failed, stat/stat_bright are unlinked — there's no calibration to protect
            # and unlinked kills the residual green.) But a linked
            # GHS whose shared black point sits above a very dim channel still clips that
            # channel to exactly 0 — e.g. faint broadband Ha nebulae where red sky is far
            # below green. SCNR then turns the dead channel into a saturated cast (the
            # pure-teal NGC 6888 failure). If the picked winner killed a channel but
            # another variant kept all three alive, switch to it (prefer linked stat/STF)
            # rather than patching colour per-channel after the fact.
            def _stretch_dead(_n: str) -> bool:
                _m = _dark_sky_channel_meds(run_dir / f"auto_stretch_{_n}.fit")
                return _m is not None and min(_m) < 0.01 and max(_m) > 0.04
            if _stretch_dead(winner_name):
                _alive = [v["name"] for v in variants if not _stretch_dead(v["name"])]
                if _alive:
                    _pref = ["stat", "stat_bright", "stf", "mas", "ghs_soft"]
                    _alt = next((p for p in _pref if _alive.count(p)), _alive[0])
                    log.warning(f"[autoprocess] {target}: stretch winner '{winner_name}' "
                                f"black-clipped a channel (would cast under SCNR) — "
                                f"switching to colour-preserving '{_alt}'")
                    telegram.send(f"🎨 <b>stretch</b>: <code>{target}</code> — '{winner_name}' "
                                  f"killed a colour channel; using '{_alt}' to preserve "
                                  f"calibrated colour")
                    winner_name = _alt

            winner_fits = run_dir / f"auto_stretch_{winner_name}.fit"
            winner_jpg = run_dir / f"auto_stretch_{winner_name}_preview.jpg"
            if winner_fits.exists():
                _wcs_src_prev = current_path  # audit F2
                current_path = winner_fits
                try:    # WCS/marker continuity (audit F2) — stretch winner
                    from nas_server.seti_astro import _preserve_celestial_wcs as _pwx
                    _pwx(_wcs_src_prev, current_path)
                except Exception:
                    pass

            # ── Background convergence clamp ──────────────────────────────────
            # The multi-variant picker selects on histogram health, but the variants
            # carry different target backgrounds (veralux_strong reaches target_median
            # ×1.3 → 0.20+), so the winning sky can land well outside the per-type band
            # — shipped finals ranged from crushed-black (M 83 0.000) to washed
            # (NGC 6914 0.203, M 57 0.170, NGC 6334 0.168). Pull the winner's sky to the
            # nearest band edge with a single structure-preserving additive shift: clamp
            # the washed ceiling, and lift only a genuinely crushed floor (sky far below
            # band-lo) so a deliberately dark but unclipped galaxy sky is left alone.
            # See [[feedback-galaxy-stretch-darker]] / [[feedback-faint-nebula-too-dark]].
            if not dry_run and winner_fits.exists():
                try:
                    import numpy as _np
                    from astropy.io import fits as _cf
                    _cs = _compute_stretch_stats(current_path, object_type, _run_depth,
                                                 frame_fill=frame_fill_info["frame_fill"])
                    _cbg = _cs.get("bg_level")
                    _clo = _cs.get("bg_low_val")
                    _chi = _cs.get("bg_high_val")
                    if _cbg is not None and _clo is not None and _chi is not None:
                        _crush = max(0.0, _clo - 0.02)
                        _shift = 0.0
                        if _cbg > _chi + 0.005:
                            _shift = _chi - _cbg          # washed — pull down to ceiling
                        elif _cbg < _crush:
                            _shift = _clo - _cbg          # crushed — lift to floor
                        if abs(_shift) > 0.003:
                            with _cf.open(str(current_path)) as _wh:
                                _wd = _wh[0].data.astype("float32")
                                _whdr = _wh[0].header.copy()
                            _wd = _np.clip(_wd + _shift, 0.0, 1.0)
                            _cf.writeto(str(current_path), _wd, _whdr, overwrite=True)
                            _generate_preview_nl(current_path, winner_jpg)  # refresh stale preview
                            log.info(f"[autoprocess] {target}: stretch bg clamp "
                                     f"{_cbg:.3f}→{_cbg + _shift:.3f} "
                                     f"(band {_clo:.2f}–{_chi:.2f}, shift {_shift:+.3f})")
                except Exception as _cle:
                    log.warning(f"[autoprocess] {target}: stretch bg clamp failed: {_cle}")

            # ── Stretch Claude fine-tune — REMOVED (physics-default) ──────────
            # The Claude-driven per-param stretch fine-tune (assess_quality_dimensions +
            # recommend_processing_step loop) is gone. The winner is the physics pick from
            # the multi-variant stretch; the galaxy-protected sky_mute in the combine tail
            # lands the final tone. See [[project-physics-default-pipeline]] and
            # [[project-ghs-wrapper-limits]].

            # Last-resort dead-channel rescue: only fires if the winner (and every
            # alternative) black-clipped a channel — i.e. the colour-preservation guard
            # above had no live variant to switch to. Lifts the dead channel to a neutral
            # pedestal so SCNR can't produce a saturated cast. Per-channel and lossy, so
            # it is deliberately the fallback, not the primary path.
            if not dry_run:
                _rescue_dead_channel(current_path, target)

            # Switch to non-linear preview rendering for all subsequent steps
            # (set here before any telegram calls so it's guaranteed even if telegram throws)
            _nonlinear = True
            step_label = f"stretch[{winner_name}]"
            steps_applied.append(step_label)
            log_processing_step(
                target, step=step_label, engine="auto_process",
                scores_before=current_scores or None,
                claude_reasoning=None,
            )
            step_records.append({
                "step": "stretch", "type": "stretch_pick",
                "winner": winner_name,
                "frame_fill": dict(frame_fill_info),
                "spcc_failed": _spcc_failed,
                # sentinel alone isn't enough: sssc_calibrate writes it before the
                # pipeline gate, so a vetoed/reverted step leaves it stale
                "sssc_applied": ((run_dir / ".sssc_applied").exists()
                                 and "color_calibration" in steps_applied),
                "stat_linked": _stat_linked,  # False when SPCC failed → per-channel green kill
                "variants": [{"id": v["name"], "preview": f"auto_stretch_{v['name']}_preview.jpg"}
                             for v in variants],
                # Per-candidate physics metrics (cost, bg_dist, p99, under, blown,
                # bg_noise, grain, winner, dropped-reason) — the episode candidate-grid
                # shot renders straight from this. Empty on forced-variant runs.
                "candidate_scores": _stretch_scored,
                "reasoning": "",
                "preview_after": f"auto_stretch_{winner_name}_preview.jpg",
                "scores_before": {k: current_scores.get(k) for k in ["stretch_quality", "color_balance"]
                                  if current_scores.get(k) is not None},
            })
            reasoning_snippet = ""
            telegram.send(
                f"🌟 <b>stretch</b>: <code>{target}</code>\n"
                f"  Winner: <code>{winner_name}</code>\n"
                + (f"  {reasoning_snippet}" if reasoning_snippet else "")
            )
            if winner_jpg.exists():
                telegram.send_photo(str(winner_jpg), caption=f"{target} — stretch: {winner_name}")
            # Compute post-stretch pixel stats for quality assessment context
            _stretch_stats = _compute_stretch_stats(current_path, object_type, _run_depth,
                                                    frame_fill=frame_fill_info["frame_fill"])
            if _stretch_stats:
                step_context["stretch_stats"] = _stretch_stats
                _bg   = _stretch_stats.get("bg_level", 0)
                _p99  = _stretch_stats.get("p99", 0)
                _ok   = _stretch_stats.get("bg_ok", False)
                _tgt  = _stretch_stats.get("bg_target", "0.05–0.09")
                _high = _stretch_stats.get("bg_high", False)
                _low  = _stretch_stats.get("bg_low", False)
                _p99_low  = _stretch_stats.get("p99_low", False)
                _is_nebula = _stretch_stats.get("is_nebula", False)
                # Build contextual bg status icon + note
                if _ok:
                    _bg_flag = "✅"
                    _bg_note = ""
                elif _high and _is_nebula:
                    _bg_flag = "🌫️"
                    _bg_note = " (emission fill — normal for this target)"
                elif _high:
                    _bg_flag = "⚠️"
                    _bg_note = " too bright"
                else:  # _low
                    _bg_flag = "⚠️"
                    _bg_note = " crushed blacks"
                # p99 note
                _p99_note = ""
                if _p99 > 0.93:
                    _p99_note = " ← highlights may clip"
                elif _p99_low:
                    _p99_note = " ⚠️ under-stretched"
                telegram.send(
                    f"📐 <b>Stretch stats</b>: <code>{target}</code>\n"
                    f"  Sky bg: {_bg:.3f} (target {_tgt}) {_bg_flag}{_bg_note}\n"
                    f"  p99: {_p99:.3f}{_p99_note}"
                )
            # Compute luminance mask params from the post-stretch image for non-linear steps
            try:
                from nas_server.image_analyzer import analyze as _analyze_nl
                from nas_server.tool_params import compute_lum_masks as _compute_lm
                _nl_masks = _compute_lm(_analyze_nl(str(current_path)), object_type)
                log.info(f"[autoprocess] {target}: non-linear lum masks computed "
                         f"(clahe lower={_nl_masks.get('clahe', {}).get('lower', '?')})")
            except Exception as _nle:
                log.warning(f"[autoprocess] {target}: lum mask compute failed: {_nle}")
            # Deterministic branch snapshot: the post-stretch starless image is the
            # canonical entry point for a later NBN (or other) branch of this run.
            if not _branch_start:
                try:
                    shutil.copy2(str(current_path), str(run_dir / "nbn_branch_image.fit"))
                except Exception as _bse:
                    log.debug(f"[autoprocess] {target}: branch image snapshot failed: {_bse}")
            if _video:
                try:
                    _st_vjpg = run_dir / "auto_preview_stretch_vframe.jpg"
                    _generate_preview_nl(current_path, _st_vjpg)
                    if _st_vjpg.exists():
                        _video.add_frame(
                            act="process", step_name="stretch",
                            image_path=_st_vjpg, stage="process",
                            duration_s=2.5)
                except Exception as _stve:
                    log.debug(f"[video] stretch frame failed: {_stve}")
            continue

        # ── star-split / star-process / star-combine ────────────────────
        step_def_early = ontology["processing_steps"].get(step_name, {})
        step_type = step_def_early.get("step_type", "standard")

        if step_type == "star_split":
            if dry_run:
                steps_applied.append(step_name)
                continue
            starless_out = run_dir / "auto_starless.fit"
            stars_out    = run_dir / "auto_stars.fit"
            log.info(f"[autoprocess] {target}: {step_name} — splitting stars (StarXT)")
            telegram.send(f"⭐ <b>{step_name}</b>: <code>{target}</code> — splitting stars via StarXT")
            split_ok = False
            split_elapsed = 0

            # Try PI StarXTerminator first — highest quality, generates stars natively
            try:
                from nas_server.pixinsight import run_postprocess as _pi_pp
                # Scale timeout by image megapixels: StarXT on large (≥20MP) images
                # can easily take 15–25 min. Formula: 900s baseline + 30s/MP above 10MP.
                try:
                    from astropy.io.fits import getheader as _gethdr
                    _hdr = _gethdr(str(current_path), memmap=False)
                    _mp = (_hdr.get("NAXIS1", 3000) * _hdr.get("NAXIS2", 3000)) / 1_000_000
                except Exception:
                    _mp = 10.0
                _starxt_timeout = int(max(900, 900 + 30 * max(0, _mp - 10)))
                log.info(f"[autoprocess] {target}: StarXT timeout={_starxt_timeout}s "
                         f"(image {_mp:.1f}MP)")
                pi_res = _pi_pp(
                    target=target,
                    input_fits=str(current_path),
                    output_path=str(starless_out),
                    starxt=True,
                    starxt_stars_output=str(stars_out),
                    # all other tools off — this pass is star-split only
                    dbe=False, gradient_correction=False,
                    color_calibration=False, bgn=False, spcc=False,
                    mlt=False, tgv=False, bxt=False, nxt=False,
                    ht=False, scnr=False, hdrmt=False, lhe=False,
                    color_sat=False, curves=False, cms=False, morph=False,
                    timeout=_starxt_timeout,
                )
                if pi_res.get("ok") and starless_out.exists() and stars_out.exists():
                    split_ok = True
                    split_elapsed = pi_res.get("elapsed", 0)
                    log.info(f"[autoprocess] {target}: StarXT split done in {split_elapsed:.0f}s")
                else:
                    log.warning(f"[autoprocess] {target}: StarXT split failed — "
                                f"starless={starless_out.exists()} stars={stars_out.exists()}")
            except Exception as e:
                log.warning(f"[autoprocess] {target}: StarXT split exception: {e}")

            # Fall back to DarkStar (SASpro) if StarXT failed
            if not split_ok:
                log.info(f"[autoprocess] {target}: falling back to DarkStar for star split")
                telegram.send(f"⚠️ <b>{step_name}</b>: StarXT failed, trying DarkStar fallback")
                res = seti_astro.remove_stars_split(current_path, starless_out, stars_out)
                if res.get("ok"):
                    split_ok = True
                    split_elapsed = res.get("elapsed_s", 0)
                    log.info(f"[autoprocess] {target}: DarkStar split done in {split_elapsed:.0f}s")
                else:
                    log.warning(f"[autoprocess] {target}: star_split failed: {res.get('error')}")

            if split_ok:
                _wcs_src_prev = current_path  # audit F2
                current_path = starless_out
                try:    # WCS/marker continuity (audit F2) — starless
                    from nas_server.seti_astro import _preserve_celestial_wcs as _pwx
                    _pwx(_wcs_src_prev, current_path)
                except Exception:
                    pass
                try:    # WCS/marker continuity (audit F4) — STARS aux layer, so its
                        # previews render with the same rule as the main chain
                    from nas_server.seti_astro import _preserve_celestial_wcs as _pwx2
                    _pwx2(_wcs_src_prev, stars_out)
                except Exception:
                    pass
                aux_paths["stars"] = stars_out
                # Galaxies (workflow 1.7.0): cap residual post-SPCC green excess in the
                # starless GALAXY SIGNAL before the stretch amplifies it into a lime
                # cast. Runs on the starless layer so star colors keep SPCC calibration;
                # sky-subtracted + masked so the SPCC-neutral sky is untouched (safe
                # despite the SPCC-success SCNR skip below). Self-gates on measured G/R.
                if "galaxy" in (object_type or ""):
                    _gcm = getattr(seti_astro, "green_cap_masked", None)
                    if _gcm is not None:
                        _gc = _gcm(starless_out, starless_out, amount=0.7)
                        if _gc.get("ok") and not _gc.get("skipped"):
                            log.info(f"[autoprocess] {target}: green_cap_masked signal "
                                     f"G/R {_gc.get('gr_before', 0):.3f}→"
                                     f"{_gc.get('gr_after', 0):.3f}")
                            steps_applied.append("green_cap")
                            step_records.append({
                                "step": "green_cap", "type": "green_cap",
                                "gr_before": _gc.get("gr_before"),
                                "gr_after": _gc.get("gr_after")})
                # Deterministic branch snapshot of the linear stars layer so a later
                # branch can re-stretch + screen the stars onto its alternate result.
                if not _branch_start:
                    try:
                        shutil.copy2(str(stars_out), str(run_dir / "nbn_branch_stars.fit"))
                    except Exception as _bss:
                        log.debug(f"[autoprocess] {target}: branch stars snapshot failed: {_bss}")
                steps_applied.append(step_name)
                step_records.append({"step": step_name, "type": "star_split",
                                     "elapsed_s": split_elapsed})
                telegram.send(f"✅ <b>{step_name}</b>: <code>{target}</code> — done, {split_elapsed:.0f}s")
                if _video:
                    try:
                        _ss_vjpg = run_dir / "auto_preview_starless_vframe.jpg"
                        (_generate_preview_nl if _nonlinear else _generate_preview)(
                            current_path, _ss_vjpg)
                        if _ss_vjpg.exists():
                            _video.add_frame(
                                act="process", step_name=step_name,
                                image_path=_ss_vjpg, stage="process",
                                caption="Starless layer", duration_s=2.5)
                    except Exception as _ssve:
                        log.debug(f"[video] star_split frame failed: {_ssve}")
            continue

        if step_type == "star_process":
            stars_in = aux_paths.get("stars")
            if stars_in is None or not stars_in.exists():
                log.info(f"[autoprocess] {target}: {step_name} — no stars layer, skipping")
                continue
            if dry_run:
                steps_applied.append(step_name)
                continue
            fn_ss = getattr(seti_astro, "star_stretch", None)
            if fn_ss is None:
                log.warning("[autoprocess] star_stretch not found — skipping")
                continue
            params = {k: v["default"] for k, v in step_def_early.get("parameters", {}).items()
                      if "default" in v}
            # Match SCNR treatment to what was applied to the starless image
            if "scnr_amount" in step_context:
                params["scnr_amount"] = step_context["scnr_amount"]

            # ── Data-driven stretch_factor ─────────────────────────────────
            # The stretch formula is:  out = x * f / (x*f + 1)  where f = 3^factor.
            # We target the 90th-percentile bright star → 0.25 post-stretch ("subtle"):
            #   f_ideal = 0.25 / (0.75 * p90)  = 0.333 / p90
            #   factor  = log(f_ideal) / log(3)
            # 0.25 keeps stars clearly secondary to the nebula: faint stars ≈0.05–0.10,
            # typical bright stars ≈0.25, only genuinely bright stars approach 0.6–0.9.
            # Stars should complement the nebula, not compete with it.
            # Clamped to [1.0, 3.0] across sparse galaxies to crowded Cygnus/Sagittarius.
            #
            # Must replicate the full normalisation chain from star_stretch():
            #   1. _load_fits normalises [min,max] → [0,1]   (handles PI uint32 range)
            #   2. star_stretch auto-scale: if p99.9 < 0.1, divide by p99.9 → p99.9=1.0
            # Both steps are mirrored here so p90_nonzero is on the same scale the
            # stretch formula actually receives.
            try:
                import numpy as _np_ss
                from astropy.io import fits as _af_ss
                with _af_ss.open(str(stars_in)) as _sh:
                    _sd = _sh[0].data.astype(_np_ss.float32)
                # Step 1: replicate _load_fits min-max normalisation
                _lo_ss = float(_np_ss.nanmin(_sd))
                _hi_ss = float(_np_ss.nanmax(_sd))
                if _hi_ss > _lo_ss:
                    _sd = (_sd - _lo_ss) / (_hi_ss - _lo_ss)
                # Luminance: max across channels for 3-channel data
                if _sd.ndim == 3:
                    _sd = _np_ss.max(_sd, axis=0)
                # Step 2: replicate star_stretch per-image auto-scale
                _p999_ss = float(_np_ss.percentile(_sd, 99.9))
                if 0 < _p999_ss < 0.1:
                    _sd = _np_ss.clip(_sd / _p999_ss, 0.0, 1.0)
                # Compute target factor
                _nz = _sd[_sd > 0.001].ravel()
                if len(_nz) > 200:
                    _p90 = float(_np_ss.percentile(_nz, 90))
                    if _p90 > 0.005:
                        _f_ideal = 0.333 / _p90         # target 0.25 post-stretch (subtle)
                        _raw_factor = float(_np_ss.log(_f_ideal) / _np_ss.log(3.0))
                        _clamped = float(_np_ss.clip(_raw_factor, 1.0, 3.0))
                        # Snap to nearest 0.5 for clean logging
                        _auto_factor = round(_clamped * 2) / 2
                        params["stretch_factor"] = _auto_factor
                        log.info(
                            f"[autoprocess] {target}: stretch_stars auto factor={_auto_factor} "
                            f"(p90={_p90:.3f}→0.25, raw={_raw_factor:.2f}, stars_px={len(_nz):,})"
                        )
            except Exception as _ssf_err:
                log.debug(f"[autoprocess] {target}: stretch_stars auto-factor failed: {_ssf_err}")

            stars_stretched = run_dir / "auto_stars_stretched.fit"
            log.info(f"[autoprocess] {target}: {step_name} params={params}")
            res = fn_ss(stars_in, stars_stretched, **{k: v for k, v in params.items()
                        if k in set(inspect.signature(fn_ss).parameters)})
            # Star-layer grain gate (workflow 1.22.0, batch eval 07-13 P4+P5): the
            # combine step DOUBLES final bg_noise when the stretched star layer
            # carries background speckle (NGC 2359 0.050→0.104, NGC 281 0.035→0.069
            # across combine; the starless layer was clean). Measure the star
            # layer's sky-corner σ against the starless layer's; if > 2×, re-stretch
            # the stars one factor step gentler (bounded, single retry) — the grain
            # rides the aggressive star stretch, so backing off the multiplier fixes
            # it at the source without touching the clean starless.
            if res.get("ok") and stars_stretched.exists():
                try:
                    import numpy as _sgn
                    from astropy.io import fits as _sgf
                    def _corner_sigma(_p):
                        _d = _sgf.getdata(str(_p)).astype("float32")
                        if _d.ndim == 3:
                            _d = _sgn.moveaxis(_d[:3], 0, -1).mean(axis=-1)
                        _h, _w = _d.shape
                        _m = max(_h // 20, _w // 20, 50)
                        return float(_sgn.std(_sgn.concatenate([
                            _d[:_m, :_m].ravel(), _d[:_m, -_m:].ravel(),
                            _d[-_m:, :_m].ravel(), _d[-_m:, -_m:].ravel()])))
                    _sl_sig = _corner_sigma(stars_stretched)
                    _nl_sig = _corner_sigma(current_path)
                    if _nl_sig > 0 and _sl_sig > 2.0 * _nl_sig:
                        _f_cur = float(params.get("stretch_factor", 2.5))
                        _f_retry = max(1.0, _f_cur - 0.5)
                        log.warning(f"[autoprocess] {target}: star layer grainy "
                                    f"(σ {_sl_sig:.4f} > 2× starless {_nl_sig:.4f})"
                                    f" — re-stretching at factor {_f_retry}")
                        _p2 = dict(params); _p2["stretch_factor"] = _f_retry
                        _r2 = fn_ss(stars_in, stars_stretched,
                                    **{k: v for k, v in _p2.items()
                                       if k in set(inspect.signature(fn_ss).parameters)})
                        if _r2.get("ok"):
                            res = _r2
                            params = _p2
                            _sl2 = _corner_sigma(stars_stretched)
                            log.info(f"[autoprocess] {target}: star layer retry σ "
                                     f"{_sl_sig:.4f}→{_sl2:.4f} "
                                     f"(factor {_f_cur}→{_f_retry})")
                            step_records.append({"step": "star_layer_grain_gate",
                                                 "type": "guard",
                                                 "sigma_before": round(_sl_sig, 4),
                                                 "sigma_after": round(_sl2, 4),
                                                 "factor": _f_retry})
                except Exception as _sge:
                    log.debug(f"[autoprocess] {target}: star grain gate failed: {_sge}")
            if res.get("ok"):
                try:    # WCS/marker continuity (audit F4) — stretched stars layer
                    from nas_server.seti_astro import _preserve_celestial_wcs as _pwx3
                    _pwx3(stars_in, stars_stretched)
                except Exception:
                    pass
                aux_paths["stars_stretched"] = stars_stretched
                steps_applied.append(step_name)
                step_records.append({"step": step_name, "type": "star_process",
                                     "params": params, "elapsed_s": res.get("elapsed_s")})
                stars_jpg = run_dir / "auto_stars_stretched_preview.jpg"
                # Star layer preview: the star layer is already nonlinear (most pixels are zero
                # background so median≈0, which causes generate_preview_stf to incorrectly
                # re-stretch the data and blow out the stars).  Normalise by p99.9 of nonzero
                # pixels then clip — this shows the true stretched result without double-STF.
                try:
                    import numpy as _np_spv
                    from astropy.io import fits as _af_spv
                    from PIL import Image as _Im_spv
                    with _af_spv.open(str(stars_stretched)) as _sh_spv:
                        _sd_spv = _sh_spv[0].data.astype(_np_spv.float32)
                    if _sd_spv.ndim == 3:
                        _sd_spv = _np_spv.transpose(_sd_spv, (1, 2, 0))
                    hi_spv = float(_sd_spv.max())
                    if hi_spv > 1.5:
                        lo_spv = float(_sd_spv.min())
                        _sd_spv = (_sd_spv - lo_spv) / max(hi_spv - lo_spv, 1e-9)
                    # Rescale by nonzero p99.9 to fill the dynamic range without re-stretching
                    _nz_spv = _sd_spv[_sd_spv > 0.001]
                    if len(_nz_spv) > 100:
                        _scale = float(_np_spv.percentile(_nz_spv, 99.9))
                        if 0 < _scale < 1.0:
                            _sd_spv = _np_spv.clip(_sd_spv / _scale, 0.0, 1.0)
                    _img_spv = (_np_spv.clip(_sd_spv, 0.0, 1.0) * 255).astype(_np_spv.uint8)
                    _Im_spv.fromarray(_img_spv, mode="RGB").save(str(stars_jpg), quality=90)
                except Exception as _spv_err:
                    log.debug(f"[autoprocess] {target}: stars preview fallback: {_spv_err}")
                    _generate_preview(stars_stretched, stars_jpg)
                telegram.send(f"✅ <b>{step_name}</b>: <code>{target}</code> — stars stretched")
                if _video and stars_jpg.exists():
                    try:
                        _video.add_frame(
                            act="process", step_name=step_name,
                            image_path=stars_jpg, stage="process",
                            caption="Stretched star layer", duration_s=2.5)
                    except Exception as _spve:
                        log.debug(f"[video] star_process frame failed: {_spve}")
            else:
                log.warning(f"[autoprocess] {target}: star_stretch failed: {res.get('error')}")
            continue

        if step_type == "star_combine":
            stars_src = aux_paths.get("stars_stretched") or aux_paths.get("stars")
            if stars_src is None or not stars_src.exists():
                log.info(f"[autoprocess] {target}: {step_name} — no stars layer to combine, skipping")
                continue
            if dry_run:
                steps_applied.append(step_name)
                continue
            fn_comb = getattr(seti_astro, "combine_stars_screen", None)
            if fn_comb is None:
                log.warning("[autoprocess] combine_stars_screen not found — skipping")
                continue
            combined_out = run_dir / "auto_combined.fit"
            _starless_pre_combine = current_path  # starless layer for the HDR gate
            log.info(f"[autoprocess] {target}: {step_name} — screen blend recombine")
            res = fn_comb(current_path, stars_src, combined_out)
            if res.get("ok"):
                _wcs_src_prev = current_path  # audit F2
                current_path = combined_out
                try:    # WCS/marker continuity (audit F2) — combine
                    from nas_server.seti_astro import _preserve_celestial_wcs as _pwx
                    _pwx(_wcs_src_prev, current_path)
                except Exception:
                    pass
                # Mute an over-bright sky with the galaxy structure protected.
                # Galaxies (workflow 1.7.0): the stretch is chosen for galaxy detail and
                # deliberately leaves the sky bright, so use the auto-tuned MASKED sky-mute
                # — it darkens the sky relative to the frame's own sigma-clipped sky stats
                # while protecting the extended galaxy (arms/tidal bridge). Other
                # non-nebula targets keep the band-based _mute_sky (only fires above band).
                # Nebulae are skipped by both (corners can be real Ha/OIII emission).
                # See [[feedback-galaxy-stretch-darker]].
                _muted = False
                if "galaxy" in (object_type or ""):
                    _smm = getattr(seti_astro, "sky_mute_masked", None)
                    if _smm is not None:
                        _sk = _smm(combined_out, combined_out)
                        _muted = bool(_sk.get("ok"))
                        if _muted:
                            log.info(f"[autoprocess] {target}: sky_mute_masked sky "
                                     f"{_sk.get('sky_before', 0):.3f}→"
                                     f"{_sk.get('sky_after', 0):.3f} "
                                     f"(galcov {100*_sk.get('galaxy_coverage', 0):.0f}%)")
                elif "nebula" in (object_type or ""):
                    # Nebulae get the signal-aware contrast recovery instead of a blind
                    # sky-mute (their corners can be real Ha/OIII). Recovers the contrast
                    # the colour-first mas pick trades away, protecting the faint floor.
                    _rec = _recover_contrast_nebula(combined_out, object_type, target,
                                                    depth=_run_depth)
                    if _rec:
                        steps_applied.append("contrast_recovery")
                        step_records.append({"step": "contrast_recovery",
                                             "type": "contrast_recovery"})
                else:
                    _muted = _mute_sky(combined_out, object_type, target)
                if _muted:
                    steps_applied.append("sky_mute")
                    step_records.append({"step": "sky_mute", "type": "sky_mute"})
                # Galaxy post-mute crush guard (workflow 1.21.0, batch eval 07-13
                # Pattern 1a downstream): the detail-first galaxy stretch is
                # deliberately bright, and the curves + sky_mute chain can
                # overcorrect it BELOW the band floor (measured finals: NGC 4244
                # 0.045, M 66 0.034, NGC 7331 0.031 vs the 0.05 floor). Additive
                # lift back to the floor — same structure-preserving mechanism as
                # the stretch bg clamp. Fix the crush, NOT the pick (1.7.0 design).
                if "galaxy" in (object_type or "") and not dry_run:
                    try:
                        import numpy as _np
                        from astropy.io import fits as _cgf
                        _gs = _compute_stretch_stats(combined_out, object_type,
                                                     _run_depth)
                        _gbg = _gs.get("bg_level")
                        _glo = _gs.get("bg_low_val", 0.05)
                        if _gbg is not None and _gbg < _glo - 0.005:
                            _gshift = _glo - _gbg
                            with _cgf.open(str(combined_out)) as _gh:
                                _gd = _gh[0].data.astype("float32")
                                _ghdr = _gh[0].header.copy()
                            _gd = _np.clip(_gd + _gshift, 0.0, 1.0)
                            _cgf.writeto(str(combined_out), _gd, _ghdr,
                                         overwrite=True)
                            log.info(f"[autoprocess] {target}: post-mute crush "
                                     f"lift {_gbg:.3f}→{_glo:.3f} "
                                     f"(shift {_gshift:+.3f})")
                            step_records.append({"step": "post_mute_crush_lift",
                                                 "type": "guard",
                                                 "bg_before": round(_gbg, 4),
                                                 "bg_after": _glo})
                    except Exception as _gce:
                        log.warning(f"[autoprocess] {target}: crush-lift guard "
                                    f"failed: {_gce}")
                # Generate combined preview without STF re-stretch — the combined
                # image is already non-linear but median is often < 0.15 (dark
                # background), which would trigger a second STF stretch and produce
                # wildly wrong colours. Render directly: clip to [0,1] → uint8.
                _comb_jpg = run_dir / "auto_preview_combined.jpg"
                _generate_preview_nl(combined_out, _comb_jpg)
                steps_applied.append(step_name)
                step_records.append({"step": step_name, "type": "star_combine",
                                     "elapsed_s": res.get("elapsed_s")})
                telegram.send(f"✅ <b>{step_name}</b>: <code>{target}</code> — stars recombined")
                if _video and _comb_jpg.exists():
                    try:
                        _video.add_frame(
                            act="process", step_name=step_name,
                            image_path=_comb_jpg, stage="process",
                            caption="Stars recombined", duration_s=2.5)
                    except Exception as _cve:
                        log.debug(f"[video] star_combine frame failed: {_cve}")
            else:
                log.warning(f"[autoprocess] {target}: combine_stars_screen failed: {res.get('error')}")
            continue

        # ── generic processing step ─────────────────────────────────────
        step_def = ontology["processing_steps"].get(step_name)
        if step_def is None:
            log.warning(f"[autoprocess] Unknown step '{step_name}' — skipping")
            continue

        condition = step_def.get("apply_when", "")
        if current_scores and condition and not _eval_condition(condition, current_scores):
            log.info(f"[autoprocess] {target}: {step_name} skipped (condition: {condition})")
            continue

        if dry_run:
            steps_applied.append(step_name)
            continue

        # ── force_variant: workflow specifies exact variant to use (e.g. quick_default) ──
        forced_variant_id = force_variants.get(step_name)
        if forced_variant_id and step_def.get("experiment_variants") and not dry_run:
            fv = next((v for v in step_def["experiment_variants"] if v["id"] == forced_variant_id), None)
            if fv:
                # Apply adaptive param nudges from Phase 1 linear plan (physics-bounded).
                # Translate tool_params key names → variant function param names, then
                # only apply keys that exist in the variant's original params (whitelist).
                if _adaptive_param_nudges.get(step_name):
                    _NUDGE_ALIASES = {
                        "graxpert_smoothing": "smoothing",
                        "graxpert_correction": "correction",
                    }
                    _base_params = fv.get("params", {})
                    _translated = {
                        _NUDGE_ALIASES.get(k, k): v
                        for k, v in _adaptive_param_nudges[step_name].items()
                    }
                    _applicable = {k: v for k, v in _translated.items() if k in _base_params}
                    if _applicable:
                        fv = dict(fv)
                        fv["params"] = {**_base_params, **_applicable}
                        log.info(f"[autoprocess] {target}: {step_name} adaptive param nudges "
                                 f"applied to force_variant: {_applicable}")
                    else:
                        log.debug(f"[autoprocess] {target}: {step_name} adaptive param nudges "
                                  f"had no applicable keys after translation")
                log.info(f"[autoprocess] {target}: {step_name} — force_variant={forced_variant_id}")
                from nas_server.experiments import _run_variant
                # ── PRE-STATS ────────────────────────────────────────────────
                _fv_stats_before: dict | None = None
                _fv_analyze = None
                try:
                    from nas_server.image_analyzer import analyze as _fv_analyze
                    _fv_stats_before = _fv_analyze(str(current_path))
                    _fv_nb = _fv_stats_before.get("noise", {})
                    _fv_bb = _fv_stats_before.get("background", {})
                    _fv_pb = _fv_stats_before.get("psf", {})
                    log.info(f"[autoprocess] {target}: {step_name} PRE-STATS: "
                             f"SNR={_fv_nb.get('snr',0):.1f} "
                             f"FWHM={_fv_pb.get('fwhm_median',0):.2f}px "
                             f"gradient={_fv_bb.get('gradient_severity',0):.3f} "
                             f"green_excess={_fv_stats_before.get('color',{}).get('green_excess',0):.5f}")
                except Exception:
                    pass
                # ── Data-driven param injection ───────────────────────────
                # For steps where optimal parameters depend on the CURRENT image's
                # pixel distribution, compute them now and inject into the variant.
                # This is the "measure then create the right tool" philosophy:
                # static presets are replaced by bespoke per-image parameters.
                _data_viz_fv: dict | None = None
                # Only inject data-driven control points when the variant is the generic
                # pi_curves step.  Named/calibrated variants like pi_globular_core_rolloff
                # have hand-tuned shapes; injecting generic points would override them.
                if step_name == "curves" and _fv_stats_before and forced_variant_id == "pi_curves":
                    try:
                        from nas_server.tool_params import compute_curves as _compute_cv
                        _cv_result = _compute_cv(_fv_stats_before, object_type)
                        if _cv_result.get("curves_points"):
                            fv = dict(fv)
                            fv["params"] = {**fv.get("params", {}), **_cv_result}
                            _sky_in  = _fv_stats_before.get("background", {}).get("sky_background")
                            _sky_out = _cv_result["curves_points"][2][1] if len(_cv_result["curves_points"]) > 2 else None
                            log.info(
                                f"[autoprocess] {target}: curves — injected "
                                f"{len(_cv_result['curves_points'])} data-driven control points "
                                f"(sky_bg={_sky_in or 0:.3f}→{_sky_out or '?'})"
                            )
                            _data_viz_fv = {
                                "type":       "curve",
                                "points":     _cv_result["curves_points"],
                                "sky_before": _sky_in,
                                "sky_after":  _sky_out,
                            }
                    except Exception as _cve:
                        log.warning(f"[autoprocess] {target}: curves data-driven params failed: {_cve}")

                # Save pre-step input so fallbacks apply to the ORIGINAL image,
                # not to the (possibly damaged) forced-variant output.
                _fv_input_path = current_path
                fv_out = run_dir / f"auto_{step_name}_forced.fit"
                fv_res = _run_variant(fv, _fv_input_path, fv_out)
                # Core-rolloff inversion guard (workflow 1.22.0, batch eval 07-07 P4):
                # pi_globular_core_rolloff BRIGHTENED the core (p99 +0.022/+0.025)
                # while darkening midtones in both v1.14.x globular runs — the exact
                # opposite of its purpose. A rolloff must never raise p99: revert.
                if (fv_res.get("ok") and fv_out.exists()
                        and forced_variant_id == "pi_globular_core_rolloff"):
                    try:
                        import numpy as _crn
                        from astropy.io import fits as _crf
                        def _p99(_p):
                            _d = _crf.getdata(str(_p)).astype("float32")
                            return float(_crn.percentile(_d, 99))
                        _p99_in, _p99_out = _p99(_fv_input_path), _p99(fv_out)
                        if _p99_out > _p99_in + 0.005:
                            log.warning(f"[autoprocess] {target}: core_rolloff "
                                        f"REVERTED — p99 rose {_p99_in:.3f}→"
                                        f"{_p99_out:.3f} (inverted curve)")
                            step_records.append({"step": step_name, "type": "standard",
                                                 "skipped": True,
                                                 "skip_reason": "core_rolloff inversion "
                                                 f"guard: p99 {_p99_in:.3f}→{_p99_out:.3f}"})
                            fv_res = {"ok": False,
                                      "error": "core_rolloff inversion guard"}
                    except Exception as _cre:
                        log.debug(f"[autoprocess] core_rolloff guard failed: {_cre}")
                if fv_res.get("ok") and fv_out.exists():
                    current_path = fv_out
                    steps_applied.append(f"{step_name}[{forced_variant_id}]")
                    # Aesthetic saturation steps amplify background chroma noise into
                    # speckle. The standard step path masks these to galaxy midtones;
                    # the forced-variant path must do the same so a force-variant'd
                    # color_boost isn't saturating the dark, noisy background.
                    if step_name in ("color_boost", "color_sat") and _fv_stats_before:
                        try:
                            from nas_server.tool_params import compute_lum_masks as _fv_clm
                            from nas_server.experiments import _lum_mask_blend as _fv_lmb
                            _fv_mask = _fv_clm(_fv_stats_before, object_type).get(step_name)
                            if _fv_mask:
                                _fv_lmb(_fv_input_path, current_path, _fv_mask)
                                log.info(f"[autoprocess] {target}: {step_name} — midtone lum "
                                         f"mask applied (forced path): {_fv_mask}")
                        except Exception as _fvme:
                            log.warning(f"[autoprocess] {target}: {step_name} forced-path "
                                        f"lum mask failed: {_fvme}")
                    # ── POST-STATS + objective check ──────────────────────────
                    _fv_obj: dict = {"ok": True, "reason": "no stats",
                                     "should_try_harder": False, "improved": True}
                    try:
                        _fv_stats_after = _fv_analyze(str(current_path))
                        _fv_na = _fv_stats_after.get("noise", {})
                        _fv_ba = _fv_stats_after.get("background", {})
                        _fv_pa = _fv_stats_after.get("psf", {})
                        _fv_obj = _objective_check(step_name, _fv_stats_before, _fv_stats_after)
                        log.info(f"[autoprocess] {target}: {step_name} POST-STATS: "
                                 f"SNR={_fv_na.get('snr',0):.1f} "
                                 f"FWHM={_fv_pa.get('fwhm_median',0):.2f}px "
                                 f"gradient={_fv_ba.get('gradient_severity',0):.3f} | "
                                 f"obj: {'OK' if _fv_obj['ok'] else 'FAIL'} "
                                 f"try_harder={_fv_obj.get('should_try_harder',False)} "
                                 f"— {_fv_obj['reason']}")
                        # NXT near-identity labeling (workflow 1.22.0, batch eval
                        # 07-07 P5): NXT sometimes passes clean stacks through
                        # unchanged (40% no-op rate measured) while the critique
                        # counts it as a contribution. Label honestly — output kept
                        # (harmless), record marked near-identity.
                        if step_name == "denoise_linear":
                            _nx_b = (_fv_stats_before or {}).get("noise", {}) \
                                .get("background_rms", 0.0)
                            _nx_a = _fv_na.get("background_rms", 0.0)
                            if _nx_b > 0 and _nx_a > 0.90 * _nx_b:
                                log.info(f"[autoprocess] {target}: denoise_linear "
                                         f"near-identity (rms {_nx_b:.4f}→{_nx_a:.4f}"
                                         f", <10% reduction) — labelled no-op")
                                step_records.append({
                                    "step": step_name, "type": "standard",
                                    "near_identity": True,
                                    "note": f"NXT no-op: rms {_nx_b:.4f}→{_nx_a:.4f}"})
                    except Exception:
                        pass

                    # should_try_harder Claude param-nudge REMOVED (physics-default).
                    # The force_variant path runs in normal (non-experiment) workflows,
                    # so a recommend_processing_step call here would violate the WS1 rule
                    # of zero per-step Claude outside experiment_mode. A marginal-objective
                    # forced variant is simply accepted as-is. See
                    # [[project-physics-default-pipeline]].

                    # ── Fallback: forced variant failed objective → try alternatives ──
                    # Each fallback is applied to _fv_input_path (pre-step), NOT to the
                    # failed forced output (which may be damaged/partially processed).
                    if not _fv_obj.get("ok") and _fv_stats_before and _fv_analyze:
                        _fallback_variants = [
                            v for v in step_def.get("experiment_variants", [])
                            if v["id"] != forced_variant_id and v["id"] != "none"
                        ]
                        # BUG FIX: initialise to _fv_input_path (pre-step), NOT current_path
                        # which at this point is already the failed forced-variant output.
                        # When all fallbacks fail, we revert to the original pre-step image.
                        _best_fv_path = _fv_input_path
                        _best_fv_obj_ok = False
                        log.info(f"[autoprocess] {target}: {step_name} — all fallbacks will "
                                 f"revert to pre-step input if none pass objective")
                        for _fb_v in _fallback_variants:
                            _fb_out = run_dir / f"auto_{step_name}_fb_{_fb_v['id']}.fit"
                            try:
                                # ← Fixed: apply to _fv_input_path, not the failed output
                                _fb_res = _run_variant(_fb_v, _fv_input_path, _fb_out)
                                if not (_fb_res.get("ok") and _fb_out.exists()):
                                    continue
                                _fv_stats_fb = _fv_analyze(str(_fb_out))
                                _fv_obj_fb = _objective_check(step_name, _fv_stats_before,
                                                              _fv_stats_fb)
                                _fv_nb2 = _fv_stats_fb.get("noise", {})
                                _fv_bb2 = _fv_stats_fb.get("background", {})
                                _fv_pb2 = _fv_stats_fb.get("psf", {})
                                log.info(f"[autoprocess] {target}: {step_name} fallback "
                                         f"'{_fb_v['id']}': "
                                         f"SNR={_fv_nb2.get('snr',0):.1f} "
                                         f"FWHM={_fv_pb2.get('fwhm_median',0):.2f}px "
                                         f"gradient={_fv_bb2.get('gradient_severity',0):.3f} | "
                                         f"obj: {'OK' if _fv_obj_fb['ok'] else 'FAIL'}")
                                if _fv_obj_fb.get("ok") and not _best_fv_obj_ok:
                                    _best_fv_path = _fb_out
                                    _best_fv_obj_ok = True
                                    steps_applied[-1] = f"{step_name}[{_fb_v['id']}]"
                                    log.info(f"[autoprocess] {target}: {step_name} → "
                                             f"switched to fallback '{_fb_v['id']}' (meets objective)")
                                    break
                            except Exception as _fbe:
                                log.debug(f"[autoprocess] {target}: {step_name} fallback "
                                          f"'{_fb_v['id']}' error: {_fbe}")
                        current_path = _best_fv_path
                    # Preserve WCS across forced PI steps too (denoise_linear/star_sharpen/
                    # curves via PI drop it — bypasses the main-loop fix). Re-inject from the
                    # input so orientation propagates to the final (workflow 1.12.1).
                    try:
                        from nas_server.seti_astro import _preserve_celestial_wcs as _pw
                        _pw(_fv_input_path, current_path)
                    except Exception:
                        pass
                    log.info(f"[autoprocess] {target}: {step_name} force_variant done")
                    # ── Video frame: after forced-variant step ────────────
                    if _video:
                        try:
                            _vfv_jpg = run_dir / f"auto_preview_{step_name}_forced.jpg"
                            if not _vfv_jpg.exists():
                                _vfv_jpg = run_dir / f"auto_preview_{step_name}_a0.jpg"
                            if not _vfv_jpg.exists():
                                _vfv_jpg_tmp = run_dir / f"auto_preview_{step_name}_vframe.jpg"
                                (_generate_preview_nl if _nonlinear else _generate_preview)(current_path, _vfv_jpg_tmp)
                                _vfv_jpg = _vfv_jpg_tmp
                            if _vfv_jpg.exists():
                                _vfv_before = _fv_stats_before or {}
                                _vfv_nb = _vfv_before.get("noise", {})
                                _vfv_pb = _vfv_before.get("psf", {})
                                _vfv_bb = _vfv_before.get("background", {})
                                _vfv_stats: dict[str, str] = {}
                                if _vfv_nb.get("snr"):
                                    _vfv_stats["SNR"] = f"{_vfv_nb['snr']:.1f}"
                                if _vfv_pb.get("fwhm_median"):
                                    _vfv_stats["FWHM"] = f"{_vfv_pb['fwhm_median']:.2f}px"
                                if _vfv_bb.get("gradient_severity"):
                                    _vfv_stats["Gradient"] = f"{_vfv_bb['gradient_severity']:.2f}"
                                _used_variant = steps_applied[-1] if steps_applied else forced_variant_id
                                _vfv_stats["Variant"] = _used_variant.split("[")[-1].rstrip("]")
                                _video.add_frame(
                                    act="process", step_name=step_name,
                                    image_path=_vfv_jpg,
                                    stage="process",
                                    stats=_vfv_stats,
                                    duration_s=2.5,
                                    data_viz=_data_viz_fv,
                                )
                        except Exception as _vfve:
                            log.debug(f"[video] force_variant frame failed: {_vfve}")
                else:
                    log.warning(f"[autoprocess] {target}: force_variant {forced_variant_id} failed — "
                                f"continuing with previous: {fv_res.get('error','')}")
                continue

        # ── experiment mode: run all variants, let Claude pick winner ──
        # Must run BEFORE seti_astro_fn check — PI-only steps (e.g. color_calibration)
        # have seti_astro_fn=null but still have experiment_variants.
        if experiment_mode and step_def.get("experiment_variants"):
            if dry_run:
                steps_applied.append(f"{step_name}[experiment:?]")
                continue
            log.info(f"[autoprocess] {target}: {step_name} — experiment mode")
            _exp_input_path = current_path  # save for post-winner fine-tuning

            from nas_server.exceptions import ProcessingAbortedError, ProcessingRetryError
            _retry_done = False
            _exp_call_step = step_name
            while True:
                try:
                    exp_result = run_experiment(
                        target=target,
                        step=_exp_call_step,
                        input_fits=str(current_path),
                        object_type=object_type,
                        proc_dir=run_dir,
                        manual_review=manual_review,
                    )
                    break
                except ProcessingAbortedError:
                    log.warning(f"[autoprocess] {target}: processing aborted by user review")
                    telegram.send(f"⛔ Processing aborted by user: <code>{target}</code>")
                    return {"ok": False, "error": "aborted", "target": target}
                except ProcessingRetryError as _rte:
                    if _retry_done:
                        log.warning(f"[autoprocess] {target}: retry limit reached for {_exp_call_step}")
                        exp_result = {"ok": False, "error": "retry limit"}
                        break
                    log.info(f"[autoprocess] {target}: retrying {_exp_call_step} by user request")
                    _retry_done = True
                    continue
            if exp_result.get("ok") and exp_result.get("output_path"):
                winner_path = Path(exp_result["output_path"])
                if winner_path.exists():
                    current_path = winner_path

                    # ── Fine-tune experiment winner (seti_astro variants only) ──
                    _ew_id = exp_result["winner"]
                    _ew_def = next((v for v in step_def.get("experiment_variants", [])
                                    if v["id"] == _ew_id), None)
                    if (_ew_def and _ew_def.get("engine", "seti_astro") == "seti_astro"
                            and _ew_def.get("fn") and settings.get("anthropic_api_key")
                            and not dry_run):
                        from nas_server import seti_astro as _ft_sa
                        from nas_server.claude_client import assess_quality_dimensions as _ft_aqd
                        _ft_fn = getattr(_ft_sa, _ew_def["fn"], None)
                        if _ft_fn:
                            _ft_sig = set(inspect.signature(_ft_fn).parameters)
                            _ft_dims = step_def.get("quality_impact", ["overall"])
                            _ew_prev = (run_dir / "experiments" / step_name
                                        / f"{_ew_id}_preview.jpg")
                            _ew_init_scores: dict = {}
                            if _ew_prev.exists():
                                _ew_init_scores = _ft_aqd(
                                    target, str(_ew_prev), _ft_dims, _meta(),
                                    baseline_jpg=baseline_previews.get(step_name),
                                    reference_folio=folio)
                            _ft_best_sum = sum(_to_float(_ew_init_scores.get(d, 5))
                                               for d in _ft_dims)
                            _ft_init_sum = _ft_best_sum
                            _ft_best_path = current_path
                            _ft_params_cur = dict(_ew_def.get("params", {}))
                            _ft_exp_dir = run_dir / "experiments" / step_name
                            log.info(f"[autoprocess] {target}: {step_name} fine-tune "
                                     f"winner={_ew_id} base={_ft_best_sum:.2f}")
                            telegram.send(
                                f"🔧 <b>Fine-tuning {step_name}</b>: <code>{target}</code> — "
                                f"optimizing <code>{_ew_id}</code>..."
                            )
                            for _ei in range(max_iters):
                                _evp = {k: v for k, v in _ft_params_cur.items()
                                        if k in _ft_sig}
                                _efit = _ft_exp_dir / f"{_ew_id}_tune{_ei}.fit"
                                _ejpg = _ft_exp_dir / f"{_ew_id}_tune{_ei}_preview.jpg"
                                try:
                                    _er = _ft_fn(_exp_input_path, _efit, **_evp)
                                    if not _er.get("ok") or not _efit.exists() \
                                            or not _generate_preview(_efit, _ejpg):
                                        break
                                except Exception as _ee:
                                    log.warning(f"[autoprocess] {step_name} fine-tune "
                                                f"iter {_ei}: {_ee}")
                                    break
                                _es = _ft_aqd(
                                    target, str(_ejpg), _ft_dims, _meta(),
                                    baseline_jpg=baseline_previews.get(step_name),
                                    reference_folio=folio)
                                _es_sum = sum(_to_float(_es.get(d, 5)) for d in _ft_dims)
                                _ft_delta = _es_sum - _ft_best_sum
                                log.info(f"[autoprocess] {target}: {step_name} fine-tune "
                                         f"iter {_ei} Δ={_ft_delta:+.2f} params={_evp}")
                                if _es_sum > _ft_best_sum:
                                    _ft_best_sum = _es_sum
                                    _ft_best_path = _efit
                                if _ft_delta >= improvement_threshold:
                                    break
                                if _ei + 1 < max_iters and _ejpg.exists():
                                    _enrec = recommend_processing_step(
                                        target, str(_ejpg), step_name, step_def,
                                        {**current_scores, **_es,
                                         "_attempt": _ei + 1, "_winner_fn": _ew_id},
                                        baseline_jpg=baseline_previews.get(step_name),
                                    )
                                    if not (_enrec and _enrec.get("parameters")):
                                        break
                                    _entp = {k: v for k, v in _enrec["parameters"].items()
                                             if k in _ft_sig}
                                    if not _entp or all(
                                            _ft_params_cur.get(k) == v
                                            for k, v in _entp.items()):
                                        break
                                    _ft_params_cur.update(_entp)
                                    log.info(f"[autoprocess] {target}: {step_name} "
                                             f"fine-tune nudge → {_entp}")
                            if _ft_best_path != current_path:
                                current_path = _ft_best_path
                                exp_result = {
                                    **exp_result,
                                    "winner": f"{_ew_id}_tuned",
                                    "reasoning": (
                                        exp_result.get("reasoning", "")
                                        + f" [fine-tuned +{_ft_best_sum - _ft_init_sum:.1f}]"
                                    ),
                                }
                                log.info(f"[autoprocess] {target}: {step_name} fine-tune "
                                         f"improved by {_ft_best_sum - _ft_init_sum:+.2f}")
                                telegram.send(
                                    f"✨ <b>{step_name} fine-tuned</b>: "
                                    f"<code>{target}</code> — "
                                    f"Δ={_ft_best_sum - _ft_init_sum:+.2f}"
                                )
                            else:
                                log.info(f"[autoprocess] {target}: {step_name} fine-tune "
                                         "no improvement")

                    step_label = f"{step_name}[{exp_result['winner']}]"
                    steps_applied.append(step_label)
                    log_processing_step(
                        target, step=step_label, engine="experiment",
                        params=None,
                        scores_before=current_scores or None,
                        claude_reasoning=exp_result.get("reasoning"),
                        elapsed_s=round(exp_result.get("elapsed", 0), 1),
                    )
                    # Build variant list for report (include preview paths)
                    _exp_dir_rel = f"experiments/{step_name}"
                    variant_records = [
                        {
                            "id": vr["id"],
                            "description": vr.get("description", vr["id"]),
                            "score": vr.get("claude_score"),
                            "ok": vr.get("ok", False),
                            "preview": f"{_exp_dir_rel}/{vr['id']}_preview.jpg",
                            "winner": vr["id"] == exp_result["winner"],
                            "params": vr.get("params") or {},
                        }
                        for vr in exp_result.get("variants", [])
                    ]
                    _winner_params = next(
                        (vr.get("params") or {} for vr in exp_result.get("variants", [])
                         if vr["id"] == exp_result["winner"]),
                        {},
                    )
                    step_records.append({
                        "step": step_name, "type": "experiment",
                        "winner": exp_result["winner"],
                        "winner_params": _winner_params,
                        "winner_description": exp_result.get("winner_description", ""),
                        "variants": variant_records,
                        "reasoning": exp_result.get("reasoning", ""),
                        "learning_note": exp_result.get("learning_note", ""),
                        "scores_before": {k: current_scores.get(k) for k in
                                         step_def.get("quality_impact", [])
                                         if current_scores.get(k) is not None},
                        "preview_winner": f"{_exp_dir_rel}/winner_preview.jpg",
                        "elapsed_s": round(exp_result.get("elapsed", 0), 1),
                    })
                    log.info(f"[autoprocess] {target}: {step_name} winner="
                             f"'{exp_result['winner']}' — {exp_result.get('reasoning','')[:900]}")
                    telegram.send(
                        f"🔬 <b>{step_name}</b> [experiment]: <code>{target}</code>\n"
                        f"  Winner: <code>{exp_result['winner']}</code>\n"
                        f"  {exp_result.get('reasoning', '')[:900]}"
                    )

                    # Render the carried-forward winner once, reused for both the
                    # Telegram photo and the documentary video frame. run_experiment
                    # only saves winner.fit (no winner_preview.jpg), and after
                    # fine-tuning the winner image may differ from any variant
                    # preview — so render fresh from current_path.
                    _ew_jpg = run_dir / f"auto_preview_{step_name}_vframe.jpg"
                    try:
                        (_generate_preview_nl if _nonlinear else _generate_preview)(
                            current_path, _ew_jpg)
                    except Exception as _ewpe:
                        log.debug(f"[autoprocess] {target}: {step_name} winner preview failed: {_ewpe}")
                    if not _ew_jpg.exists():
                        _legacy_prev = run_dir / "experiments" / step_name / "winner_preview.jpg"
                        _ew_jpg = _legacy_prev if _legacy_prev.exists() else None

                    if _ew_jpg and _ew_jpg.exists():
                        telegram.send_photo(str(_ew_jpg),
                                            caption=f"{target} — {step_name}: {exp_result['winner']}")

                    # ── Video frame: experiment winner ───────────────────────
                    # Experiment steps (clahe, hdr_compression, dark_enhance, scnr,
                    # background_neutralize, …) previously emitted no frame, so they
                    # vanished from the documentary.
                    if _video and _ew_jpg and _ew_jpg.exists():
                        try:
                            _ew_vstats: dict[str, str] = {"Variant": exp_result["winner"]}
                            try:
                                from nas_server.image_analyzer import analyze as _ew_an
                                _ew_st = _ew_an(str(current_path))
                                if _ew_st.get("noise", {}).get("snr"):
                                    _ew_vstats["SNR"] = f"{_ew_st['noise']['snr']:.1f}"
                            except Exception:
                                pass
                            _video.add_frame(
                                act="process", step_name=step_name,
                                image_path=_ew_jpg, stage="process",
                                stats=_ew_vstats, duration_s=2.5,
                            )
                        except Exception as _ewve:
                            log.debug(f"[video] experiment frame failed: {_ewve}")

                    # Carry forward key results for downstream steps
                    if step_name == "scnr":
                        for vr in exp_result.get("variants", []):
                            if vr["id"] == exp_result["winner"]:
                                p = vr.get("params") or {}
                                step_context["scnr_amount"] = p.get("scnr_amount",
                                    p.get("amount", 0.9))
                                break
            else:
                log.warning(f"[autoprocess] {target}: experiment for {step_name} failed — "
                            "falling through to standard execution")
            continue

        # Standard (non-experiment) execution: requires a seti_astro function
        fn_name = step_def.get("seti_astro_fn")
        if fn_name is None:
            log.info(f"[autoprocess] {target}: {step_name} — no seti_astro implementation, skipping")
            continue

        fn = getattr(seti_astro, fn_name, None)
        if fn is None:
            log.warning(f"[autoprocess] seti_astro.{fn_name} not found — skipping {step_name}")
            continue

        # Pre-step preview (used as the step's before/after thumbnail + documentary frame).
        rec_jpg = run_dir / f"auto_preview_pre_{step_name}.jpg"
        (_generate_preview_nl if _nonlinear else _generate_preview)(current_path, rec_jpg)
        # Compute force_apply HERE (before skip check) so it reflects the current step,
        # not the previous iteration's value.
        force_apply = step_def.get("force_apply", False) or (
            step_name in wf.get("force_apply_steps", [])) or (
            step_name in _force_steps)

        # Force-only steps (aesthetic palettes like narrowband_norm) are never
        # Claude's choice — they run ONLY when explicitly forced this run. Skip
        # silently otherwise, without even asking Claude.
        if step_def.get("force_only", False) and not force_apply:
            log.info(f"[autoprocess] {target}: {step_name} — force-only step, "
                     f"not forced this run; skipping")
            continue

        # ── Crop: saved-crop reuse, or first-process manual review ──
        # The crop is critical and hard to fully automate, so the user reviews it
        # once (first process) and the choice is remembered forever. On later runs
        # the saved sky-box is reprojected onto the new stack — identical framing
        # every session — with no review. A per-run re_crop flag (or clearing the
        # saved crop from the target page) forces a fresh review. The Claude veto
        # is gone. Both branches override `fn`; the generic loop below then runs the
        # override as the single crop attempt (force_apply skips the geometry gate).
        if (step_name == "crop" and fn_name == "crop_multi"
                and not dry_run and not _baseline_run and not experiment_mode):
            from nas_server import target_crop as _tcrop
            _re_crop = bool((extra_params or {}).get("re_crop")
                            or (extra_params or {}).get("recrop"))
            _saved_crop = _tcrop.get_target_crop(target)
            try:
                _cov_p = source_fits.with_name(source_fits.stem + "_coverage.fit")
                _cov_p = str(_cov_p) if _cov_p.exists() else ""
            except Exception:
                _cov_p = ""

            if _saved_crop and not _re_crop:
                force_apply = True
                log.info(f"[autoprocess] {target}: crop — reusing saved crop "
                         f"(source={_saved_crop.get('source')})")

                def _fn_saved_crop(_in, _out, _t=target, _cov=_cov_p, **_kw):
                    r = _tcrop.apply_saved_crop(_t, str(_in), str(_out))
                    if r.get("ok"):
                        return r
                    log.warning(f"[autoprocess] {_t}: saved crop failed "
                                f"({r.get('error')}) — falling back to crop_multi")
                    return seti_astro.crop_multi(str(_in), str(_out),
                                                 target=_t, coverage_path=_cov)
                fn = _fn_saved_crop
            else:
                force_apply = True
                from nas_server.crop_review import run_crop_review
                from nas_server.exceptions import (
                    ProcessingAbortedError, ProcessingRetryError)
                _cr_winner = None
                _cr_retry_done = False
                while True:
                    try:
                        _cr_winner = run_crop_review(
                            target=target, run_id=run_stamp,
                            input_fits=str(current_path), out_fits=str(
                                run_dir / "auto_crop_reviewed.fit"),
                            run_dir=run_dir, object_type=object_type,
                            coverage_path=_cov_p)
                        break
                    except ProcessingAbortedError:
                        log.warning(f"[autoprocess] {target}: crop review aborted by user")
                        telegram.send(f"⛔ Crop review aborted: <code>{target}</code>")
                        return {"ok": False, "error": "aborted", "target": target}
                    except ProcessingRetryError:
                        if _cr_retry_done:
                            log.warning(f"[autoprocess] {target}: crop retry limit reached")
                            _cr_winner = {"ok": False}
                            break
                        _cr_retry_done = True
                        log.info(f"[autoprocess] {target}: retrying crop review")
                        continue

                if _cr_winner and _cr_winner.get("ok"):
                    _cr_path = _cr_winner["output_path"]

                    def _fn_reviewed_crop(_in, _out, _src=_cr_path, **_kw):
                        import shutil as _sh
                        _sh.copy2(str(_src), str(_out))
                        return {"ok": True, "output_path": str(_out)}
                    fn = _fn_reviewed_crop
                else:
                    log.warning(f"[autoprocess] {target}: crop review produced no "
                                f"output — falling back to crop_multi default")

        # Crop is geometry-driven, not a vision judgment: if the stack has a measurable
        # blank/low-coverage border (max-framing footprint, low-framed subs), crop MUST
        # run — Claude can't skip it. We measure the fraction of the frame the largest
        # fully-covered rectangle would keep; if meaningful border exists, force crop.
        if step_name == "crop" and not force_apply:
            try:
                import numpy as np
                from astropy.io import fits as _afits
                from nas_server.seti_astro import _detect_crop_bounds_coverage
                _cd = _afits.getdata(str(current_path)).astype("float32")
                *_unused, _cinfo = _detect_crop_bounds_coverage(_cd, aggressiveness="balanced")
                _kept = _cinfo.get("kept_frac_of_frame", 1.0)
                if _kept < 0.95:
                    force_apply = True
                    log.info(f"[autoprocess] {target}: crop forced by geometry — "
                             f"largest covered rect keeps {_kept:.0%} of frame "
                             f"(blank={_cinfo.get('blank_rejected')} "
                             f"noise={_cinfo.get('noise_rejected')} tiles); not skippable")
                # Vignette / uneven-corner gate: even when coverage reads full, residual
                # corner gradients (stacking falloff, dual-band amp glow) leave dark/uneven
                # corners a pure coverage check misses. Measure the spread across the four
                # corner sky patches relative to the sky level; force a crop when corners
                # are markedly uneven (NGC 6914, M 83, NGC 6334 shipped vignetted).
                if not force_apply:
                    _arr = _cd
                    if _arr.ndim == 3:
                        if _arr.shape[0] in (3, 4):      # channel-first (C,H,W)
                            _lum = 0.2126*_arr[0] + 0.7152*_arr[1] + 0.0722*_arr[2]
                        elif _arr.shape[-1] in (3, 4):   # channel-last (H,W,C)
                            _lum = 0.2126*_arr[..., 0] + 0.7152*_arr[..., 1] + 0.0722*_arr[..., 2]
                        else:
                            _lum = _arr[0]
                    else:
                        _lum = _arr
                    _h, _w = _lum.shape[-2], _lum.shape[-1]
                    # Measure corner unevenness on a DISPLAY-stretched luminance, not the
                    # raw linear pedestal: the crop runs pre-stretch, so on linear data all
                    # four corner medians sit near the same tiny noise floor and the spread
                    # never clears 0.40 (NGC 6914 shipped vignetted because its 0.64 spread
                    # only appears post-stretch). Percentile-normalize + sqrt gives a
                    # screen-stretch-like view where the 0.40 threshold is meaningful.
                    _lo = float(np.percentile(_lum, 1.0))
                    _hi = float(np.percentile(_lum, 99.5))
                    _lumn = np.clip((_lum - _lo) / (_hi - _lo + 1e-6), 0.0, 1.0) ** 0.5
                    _sz = max(16, min(96, min(_h, _w) // 8))
                    _corners = sorted([
                        float(np.median(_lumn[:_sz, :_sz])),
                        float(np.median(_lumn[:_sz, _w - _sz:])),
                        float(np.median(_lumn[_h - _sz:, :_sz])),
                        float(np.median(_lumn[_h - _sz:, _w - _sz:])),
                    ])
                    _bg = (_corners[1] + _corners[2]) / 2.0
                    _ratio = (_corners[-1] - _corners[0]) / _bg if _bg > 1e-4 else 0.0
                    if _ratio > 0.4:
                        force_apply = True
                        log.info(f"[autoprocess] {target}: crop forced by corner vignette — "
                                 f"corner spread/bg {_ratio:.2f} > 0.40 "
                                 f"(stretched corners={[round(c, 4) for c in _corners]}); not skippable")
                    # One-sided hard black border: a rotation notch / stacking edge leaves a
                    # thin near-zero strip along one side that both the full-frame coverage
                    # check and the symmetric corner spread miss (M 83 / IC 1805 shipped with
                    # a black edge). Scan the outer ~2% band of each edge; force a directional
                    # crop when a strip is near-black across a meaningful fraction of its length.
                    if not force_apply:
                        _band = max(2, int(round(0.02 * min(_h, _w))))
                        _thr = _lo + 0.02 * (_hi - _lo)
                        _edges = {
                            "top": _lum[:_band, :],
                            "bottom": _lum[_h - _band:, :],
                            "left": _lum[:, :_band],
                            "right": _lum[:, _w - _band:],
                        }
                        for _en, _eb in _edges.items():
                            _blackfrac = float(np.mean(_eb <= _thr))
                            if _blackfrac > 0.30:
                                force_apply = True
                                log.info(f"[autoprocess] {target}: crop forced by hard {_en} "
                                         f"edge — {_blackfrac:.0%} of the outer {_band}px band "
                                         f"is near-black; not skippable")
                                break
            except Exception as _ce:
                log.warning(f"[autoprocess] {target}: crop geometry check failed: {_ce}")

        # Snapshot objective stats before this step — used for Claude param recommendation
        # AND for post-step objective quality check.
        _stats_before: dict | None = None
        try:
            from nas_server.image_analyzer import analyze as _img_analyze
            _stats_before = _img_analyze(str(current_path))
        except Exception:
            pass
        if _stats_before:
            _nb = _stats_before.get("noise", {})
            _bb = _stats_before.get("background", {})
            _pb = _stats_before.get("psf", {})
            log.info(f"[autoprocess] {target}: {step_name} PRE-STATS: "
                     f"SNR={_nb.get('snr',0):.1f} "
                     f"FWHM={_pb.get('fwhm_median',0):.2f}px "
                     f"gradient={_bb.get('gradient_severity',0):.3f} "
                     f"green_excess={_stats_before.get('color',{}).get('green_excess',0):.5f}")

        # HDR gate: multiscale highlight compression only helps when the core is
        # actually clipping (blown / near-blown). On a non-clipped core it pulls
        # contrast out of the bright structure — dimming the core and lifting noise
        # (M17 run 130 regression: HDR dimmed a smooth Swan core into harsh clumps and
        # the grader rated it *up*). Measure the highlight-clip fraction on the
        # starless layer (stars otherwise pin the normalized top and hide the core),
        # and skip unless a meaningful blown region exists.
        if step_name in ("hdr_compression", "hdr_core_blend") and not force_apply \
                and not step_def.get("force_apply", False):
            _clip_src = _starless_pre_combine if (
                _starless_pre_combine and _starless_pre_combine.exists()) else current_path
            _clip_frac = None
            _hot_frac = 0.0
            try:
                from astropy.io import fits as _afits
                _hd = _afits.getdata(str(_clip_src)).astype("float32")
                _lum = _hd.mean(axis=0) if _hd.ndim == 3 else _hd
                _mx = float(_lum.max())
                if _mx > 0:
                    _ln = _lum / _mx
                    # Fraction of the (starless) frame sitting in the top decile of its
                    # own range. A blown EXTENDED core occupies several percent here; a
                    # mid-toned, non-clipped core stays well under 1%.
                    _clip_frac = float((_ln >= 0.90).mean())
                    # Small-but-saturated core: a compact intensely-blown core (e.g. the
                    # M 42 Trapezium, ~0.2% of pixels pinned at white) barely moves the
                    # top-decile fraction yet is exactly what HDR exists for. Measure the
                    # near-white fraction directly so the gate doesn't skip it.
                    _hot_frac = float((_ln >= 0.98).mean())
            except Exception as _he:
                log.warning(f"[autoprocess] {target}: HDR clip-frac calc failed: {_he}")
            if _clip_frac is not None and _clip_frac < 0.01 and _hot_frac < 0.0010:
                log.info(f"[autoprocess] {target}: {step_name} SKIPPED — core not "
                         f"clipping (starless top-decile frac={_clip_frac:.4f} < 1%, "
                         f"near-white frac={_hot_frac:.4f} < 0.1%, "
                         f"src={_clip_src.name})")
                step_records.append({
                    "step": step_name, "type": "standard",
                    "scores_before": current_scores, "scores_after": None,
                    "skipped": True,
                    "skip_reason": (f"core not clipping — starless top-decile "
                                    f"frac={_clip_frac:.4f} < 0.01, near-white "
                                    f"frac={_hot_frac:.4f} < 0.001"),
                })
                telegram.send(f"➖ <b>{step_name}</b>: <code>{target}</code> — "
                              f"core not clipping ({_clip_frac:.1%}), skipped")
                continue

        # ── Physics gate (WS1) ──────────────────────────────────────────────
        # Physics decides run/skip AND supplies measurement-driven params. This replaces
        # the per-step Claude recommend_processing_step go/no-go (now gone outside
        # experiment_mode). force_apply overrides a physics skip. See
        # [[project-physics-default-pipeline]].
        rec = None  # no per-step Claude recommendation in physics-default mode
        phys_run, phys_params, phys_reason = _physics_should_run(
            step_name, _stats_before, object_type)
        if not phys_run and not step_def.get("force_apply", False) and not force_apply:
            log.info(f"[autoprocess] {target}: {step_name} — physics gate SKIP "
                     f"({phys_reason})")
            step_records.append({
                "step": step_name, "type": "standard",
                "skipped": True, "skip_reason": f"physics gate: {phys_reason}",
            })
            telegram.send(f"➖ <b>{step_name}</b>: <code>{target}</code> — "
                          f"physics gate skip ({phys_reason})")
            continue
        if phys_reason not in ("standard", "no stats — run with defaults"):
            log.info(f"[autoprocess] {target}: {step_name} — physics gate RUN ({phys_reason})")

        # Build param dict: ontology defaults → physics overrides → filter to fn signature
        params = {k: v["default"] for k, v in step_def.get("parameters", {}).items()
                  if "default" in v}
        if phys_params:
            params.update(phys_params)
        # Masked-core HDR: core mask onset is per-type — galaxy cores (M 31 bulge) sit
        # higher on the smoothed luminance than nebula cores (M 42 Trapezium region).
        # Thresholds validated visually on the 2026-06-10 prototype (0.72/0.80).
        if step_name == "hdr_core_blend":
            params["threshold"] = 0.80 if "galaxy" in (object_type or "") else 0.72
        # Signal-green cap gate (workflow 1.22.0): the sky-only rebalance now also
        # caps a genuine SIGNAL green lead (NGC 281 G p99 lead +0.116) — but ONLY
        # when the folio colour prior doesn't claim green/teal/OIII dominance
        # (Thor's Helmet is legitimately teal — [[project-folio-color-priors]]).
        if step_name == "sky_green_rebalance":
            # dominant_colors is ORDERED — only the FIRST entry defines dominance.
            # Substring-matching the whole list blocked NGC 281 (whose "blue-green
            # OIII" is a SECONDARY accent under a crimson-Ha first entry) and even
            # M 42. Thor's Helmet's first entry is "blue-green/teal (OIII dominant)".
            _teal_prior = False
            try:
                _dcl = ((folio or {}).get("visual_character", {})
                        .get("dominant_colors", []) or [])
                _first = str(_dcl[0]).lower() if _dcl else ""
                _teal_prior = any(k in _first for k in ("green", "teal", "oiii"))
            except Exception:
                pass
            params["allow_signal_green"] = not _teal_prior
            if _teal_prior:
                log.info(f"[autoprocess] {target}: sky_green_rebalance — signal cap "
                         f"disabled (folio colour prior: green/teal/OIII-dominant)")
        # LP filter: detected once from the SOURCE stack header (crop strips
        # FILTER from run-dir intermediates), overrides ontology default
        if step_name == "color_calibration":
            params["spcc_lp_filter"] = _is_lp_run
            # SSSC only on the narrowband-palette branch (1.16.0, Henry 2026-07-04):
            # on the STANDARD chain, spectrally-faithful SSSC renders dual-band
            # nebulae green-teal (M 42 bright-nebula G/R 1.21 vs PI CC's 0.71 — the
            # approved 8.2 look). Henry picked "PI CC look, keep it simple": the
            # standard chain goes straight to the PI SPCC/CC path; SSSC remains the
            # calibrator for NBN/palette runs, which it was built for (1.9.0).
            params["allow_sssc"] = bool(
                _is_lp_run and ("narrowband_norm" in _force_steps
                                or "nb_palette" in _force_steps))
            log.info(f"[autoprocess] {target}: LP filter detected={params['spcc_lp_filter']}"
                     f" allow_sssc={params['allow_sssc']}")
            # Hue-selective ColorSaturation preset: pass object-type aware preset to PI
            _sat_p = ("galaxy" if object_type == "galaxy"
                      else "nebula" if "nebula" in object_type
                      else "uniform")
            params["sat_preset"] = _sat_p
            log.info(f"[autoprocess] {target}: sat_preset={_sat_p} for PI ColorSaturation")

        # Narrowband normalization: the real vibrancy lever is PI's o3Boost on the HOO
        # palette (the old MaximumStars/Equalize `method` is a silent no-op in PI 1.9.3
        # — `normalizationMode` doesn't exist). This force_only step has no "parameters"
        # block, so we set params["o3_boost"] explicitly here. Auto-pick by Hα dominance:
        # a more Hα-dominant target needs MORE OIII boost to surface its weak teal core,
        # so o3Boost scales with the ratio. Curve fitted to IC 1805 (ratio 2.54→1.50) and
        # Rosette (2.16→1.35) trial sweet spots (2026-06-08). force_variants may pin a
        # manual override (nbn_o3_soft=1.3 / nbn_o3_strong=1.7) for hand-tailoring.
        if step_name == "narrowband_norm":
            _nbn_pin = force_variants.get("narrowband_norm")
            if _nbn_pin == "nbn_o3_soft":
                params["o3_boost"] = 1.3
                log.info(f"[autoprocess] {target}: narrowband_norm o3Boost pinned soft=1.3")
            elif _nbn_pin == "nbn_o3_strong":
                params["o3_boost"] = 1.7
                log.info(f"[autoprocess] {target}: narrowband_norm o3Boost pinned strong=1.7")
            else:
                from nas_server.image_analyzer import ha_dominance_ratio
                _ratio = ha_dominance_ratio(str(current_path))
                _o3 = 0.50 + 0.395 * _ratio
                _o3 = max(1.25, min(1.7, _o3))
                params["o3_boost"] = round(_o3, 2)
                log.info(f"[autoprocess] {target}: narrowband_norm auto — "
                         f"Hα/OIII={_ratio:.2f} → o3Boost={params['o3_boost']}")
            params["hoo_boost"] = 0.0   # JS HOO restore off; Python nbn color_boost is the sole sat pass
            # NOTE: the XP-measured LINEAR flux ratio (xp_channel_extract step record)
            # is NOT comparable to ha_dominance_ratio (stretched-domain, which this
            # o3Boost curve was fitted on) — deliberately not wired in here. Compare
            # the two in run.log across a few LP runs before ever switching inputs.
            if step_context.get("xp_extract"):
                log.info(f"[autoprocess] {target}: narrowband_norm — XP linear "
                         f"flux_ratio={step_context['xp_extract'].get('flux_ratio')} "
                         f"(diagnostic; o3Boost still uses stretched-domain ratio)")
            log.info(f"[autoprocess] {target}: narrowband_norm o3_boost={params['o3_boost']}")

        # narrowband_hoo needs the run dir to find the real xp_ha/xp_oiii channels
        # (falls back to the RGB proxy when absent). oiii_cap comes from the ontology
        # params / soft-strong variants.
        if step_name == "narrowband_hoo":
            params["run_dir"] = str(run_dir)
            _hoo_pin = force_variants.get("narrowband_hoo")
            if _hoo_pin == "hoo_oiii_soft":
                params["oiii_cap"] = 0.6
            elif _hoo_pin == "hoo_oiii_strong":
                params["oiii_cap"] = 1.0
            log.info(f"[autoprocess] {target}: narrowband_hoo oiii_cap="
                     f"{params.get('oiii_cap', 0.85)}")

        # color_boost on an NBN run: the ontology default preset is "galaxy" (and the
        # standard param build reads the ontology "params" via step_def — but the nbn
        # palette is neither galaxy nor the generic nebula Ha-red look). Route to the
        # dedicated "nbn" preset (warm gold/copper dust + teal OIII core) and add the
        # validated global saturation lift. This is the SOLE saturation pass for nbn —
        # the in-JS HOO restore is disabled (hoo_boost=0). Detect nbn from the forced
        # steps so it fires whether or not narrowband_norm has run yet this loop.
        if step_name == "color_boost" and "narrowband_norm" in _force_steps:
            params["preset"] = "nbn"
            params["global_sat_lift"] = 0.06
            log.info(f"[autoprocess] {target}: color_boost → nbn preset "
                     f"(gold/amber/teal, global_sat_lift=0.06)")

        # Curves: compute bespoke data-driven control points from the current image.
        # This replaces static named presets — every image gets its own curve computed
        # from its actual sky level, halo distribution, and highlight clipping extent.
        if step_name == "curves" and _stats_before:
            try:
                from nas_server.tool_params import compute_curves as _compute_cv2
                _cv2 = _compute_cv2(_stats_before, object_type)
                if _cv2.get("curves_points"):
                    params.update(_cv2)
                    log.info(f"[autoprocess] {target}: curves standard — injected "
                             f"{len(_cv2['curves_points'])} data-driven control points")
            except Exception as _cve2:
                log.warning(f"[autoprocess] {target}: curves std data-driven failed: {_cve2}")

        # Crop (multi-candidate): inject target + the stack coverage map path so
        # crop_multi can build the canonical/coverage/intersection candidates.
        if step_name == "crop" and fn_name == "crop_multi":
            params["target"] = target
            try:
                _cov = source_fits.with_name(source_fits.stem + "_coverage.fit")
                params["coverage_path"] = str(_cov) if _cov.exists() else ""
            except Exception:
                params["coverage_path"] = ""
            log.info(f"[autoprocess] {target}: crop_multi coverage_path="
                     f"{params.get('coverage_path') or '(none)'}")

        # Small-target compositional recenter (workflow 1.23.0, M 1 under-framed
        # 2026-07-15): after the coverage crop, a target much smaller than the S50
        # frame is a speck in empty sky. Wrap the crop fn to center-crop to ~3.5x
        # the folio angular size. Gated: only when NO saved/reviewed crop exists
        # (a user's manual framing is authoritative — [[feedback-never-veto-crop]])
        # and the target is < 11' (25% of the 43' short axis).
        if step_name == "crop" and not dry_run:
            _stc_arcmin = None
            try:
                from nas_server.planner import _folio_info as _stc_fi
                _stc_arcmin = _stc_fi(target).get("angular_size_arcmin")
            except Exception:
                _stc_arcmin = None
            # Only a genuine MANUAL crop (source="manual", saved from the web UI)
            # blocks the recenter. The headless crop review auto-saves its LIR/
            # canonical pick with source=<variant> — that's an automatic default,
            # NOT a user's framing choice, so the recenter still applies on top.
            _stc_manual = False
            try:
                from nas_server import target_crop as _stc_tc
                _sc = _stc_tc.get_target_crop(target)
                _stc_manual = bool(_sc and _sc.get("source") == "manual")
            except Exception:
                _stc_manual = False
            if (_stc_arcmin is not None and 0 < _stc_arcmin < 11.0
                    and not _stc_manual and not experiment_mode and not _baseline_run):
                _stc_inner = fn

                def _fn_small_target(_in, _out, _sz=_stc_arcmin, _f=_stc_inner, **_kw):
                    _r = _f(_in, _out, **{k: v for k, v in _kw.items()})
                    _op = _r.get("output_path", str(_out)) if isinstance(_r, dict) else str(_out)
                    if not (isinstance(_r, dict) and _r.get("ok")):
                        return _r
                    _cr = _small_target_recenter_crop(_op, _sz, target=target)
                    if _cr.get("cropped"):
                        log.info(f"[autoprocess] {target}: small-target recenter — "
                                 f"{_sz:.1f}' target, cropped to {_cr['kept_px']}px "
                                 f"centered {_cr['center']} (from {_cr['orig_px']})")
                    else:
                        log.info(f"[autoprocess] {target}: small-target recenter "
                                 f"skipped ({_cr.get('reason', _cr.get('error'))})")
                    return _r
                fn = _fn_small_target

        # Narrowband composite: inject ha/oiii/sii paths from job extra_params
        if step_name == "narrowband_composite":
            _nb = (extra_params or {}).get("narrowband", {})
            if _nb:
                _nb_dir = current_path.parent
                for _key in ("ha_path", "oiii_path", "sii_path"):
                    _fname = _nb.get(_key) or _nb.get(_key.replace("_path", ""))
                    if _fname:
                        params[_key] = str(_nb_dir / _fname) if not _fname.startswith("/") \
                                       else _fname
                _pal = _nb.get("palette", "foraxx")
                params["palette"] = _pal
                log.info(f"[autoprocess] {target}: narrowband_composite "
                         f"ha={params.get('ha_path')} oiii={params.get('oiii_path')} "
                         f"palette={_pal}")

        # CC Denoise chunk_size scaling: default 256px gives 700+ tiles on large mosaics,
        # causing 2+ hours per pass and tiling boundary artefacts. Scale chunk_size
        # proportionally to image width so the tile count stays ~200 regardless of size.
        if fn_name == "cc_denoise_inprocess" and "chunk_size" in set(inspect.signature(fn).parameters):
            try:
                from astropy.io.fits import getheader as _gethdr2
                _h2 = _gethdr2(str(current_path), memmap=False)
                _w2 = _h2.get("NAXIS1", 3000)
                _h2v = _h2.get("NAXIS2", 3000)
                # Target ~200 tiles total. chunk = sqrt(w*h / 200), clamped [256, 1024].
                # NGC 7000 (6122×7916): sqrt(48.5M/200)≈492 → 512px, ~192 tiles, ~22 min.
                # Typical SeeStar (1080×1920): sqrt(2M/200)≈102 → stays at 256px.
                import math as _math
                _chunk = max(256, min(1024, int(_math.sqrt(_w2 * _h2v / 200))))
                if _chunk > 256:
                    params["chunk_size"] = _chunk
                    _n_tiles = ((_w2 + _chunk - 1) // _chunk) * ((_h2v + _chunk - 1) // _chunk)
                    log.info(f"[autoprocess] {target}: cc_denoise chunk_size scaled "
                             f"to {_chunk}px ({_n_tiles} tiles, image {_w2}×{_h2v})")
            except Exception:
                pass
        sig_params = set(inspect.signature(fn).parameters)
        valid_params = {k: v for k, v in params.items() if k in sig_params}

        quality_dims = step_def.get("quality_impact", [])
        # Track the best result seen across ALL attempts (not just stop-on-threshold)
        best_path = current_path
        best_dim_scores = {d: _to_float(current_scores.get(d, 5)) for d in quality_dims}
        best_sum = sum(best_dim_scores.values())
        scores_before = {d: current_scores.get(d) for d in quality_dims
                         if current_scores.get(d) is not None}

        # Physics-default: params are computed once (no Claude param-nudge loop), so a
        # single deterministic attempt is all that's meaningful. experiment_mode is the
        # escape hatch for full multi-variant runs.
        step_max_iters = 1

        last_good_output: Path | None = None  # last attempt that produced a valid file
        _obj: dict = {"ok": True, "reason": "no stats", "should_try_harder": False,
                      "improved": True, "metrics": {}}  # default before first attempt
        _plateau_tracker = type('_PT', (), {'best_delta': 0})()  # mutable namespace for plateau

        _last_hard_veto: dict | None = None   # last artifact-class veto, for run.log
        for attempt in range(step_max_iters):
            out_path = run_dir / f"auto_{step_name}_a{attempt}.fit"
            try:
                result = fn(current_path, out_path, **valid_params)
            except Exception as e:
                log.warning(f"[autoprocess] {target}: {step_name} a{attempt} exception: {e}")
                break
            if not result.get("ok") or not out_path.exists():
                log.warning(f"[autoprocess] {target}: {step_name} a{attempt} tool failed")
                break

            # Preserve the celestial WCS across the step (workflow 1.12.1): PI-based
            # steps (deconvolution/BXT, NXT, star_sharpen) drop the astropy-readable WCS
            # from their output, which makes the preview renderer flip the image to
            # north-up from the (now-missing) WCS — the "preview flipped after
            # deconvolution" bug. 1.11.1 fixed only the color_calibration path; this covers
            # every standard step generically. Re-injects the input WCS when the output
            # lost it. See [[nbextract-sssc]] / the 1.11.1 note.
            try:
                from nas_server.seti_astro import _preserve_celestial_wcs
                _preserve_celestial_wcs(current_path, out_path)
            except Exception:
                pass

            last_good_output = out_path

            # Apply luminance mask blend if this step has computed mask params.
            # Exception: the nbn color_boost preset runs UNMASKED — the pathfound
            # vibrant SHO look (IC 1805 / Rosette trials 2026-06-08) needs the
            # saturation restore in the brightest cores too, which the midtone
            # mask (upper≈0.85) would exclude. Galaxy/nebula presets keep the mask.
            _skip_lum_mask = (fn_name == "color_boost"
                              and valid_params.get("preset") == "nbn")
            if fn_name in _nl_masks and not _skip_lum_mask:
                try:
                    from nas_server.experiments import _lum_mask_blend
                    _lum_mask_blend(current_path, out_path, _nl_masks[fn_name])
                    log.info(f"[autoprocess] {target}: {step_name} — lum mask blend applied")
                except Exception as _me:
                    log.warning(f"[autoprocess] {target}: {step_name} lum mask blend failed: {_me}")
            elif _skip_lum_mask and fn_name in _nl_masks:
                log.info(f"[autoprocess] {target}: {step_name} — lum mask skipped (nbn preset, unmasked by design)")

            # Targeted mini-assess: only score the dimensions this step affects
            mini_jpg = run_dir / f"auto_preview_{step_name}_a{attempt}.jpg"
            (_generate_preview_nl if _nonlinear else _generate_preview)(out_path, mini_jpg)
            mini_scores: dict = {}
            _stats_after: dict | None = None
            try:
                _stats_after = _img_analyze(str(out_path))
            except Exception:
                pass

            # Objective check — compare before/after pixel statistics
            _obj = _objective_check(step_name, _stats_before, _stats_after)
            # BXT local-undershoot guard (1.17.2, IC 1805 post-mortem): deconvolution
            # ringing digs holes BELOW the input's local floor around bright sources —
            # rendered as black blobs with magenta rims — while whole-frame FWHM/SNR
            # IMPROVE, so _objective_check is structurally blind to it (Henry caught
            # it in the step preview). Compare local minima directly; veto on holes.
            if step_name == "deconvolution" and _obj.get("ok"):
                try:
                    _us = _bxt_undershoot_check(current_path, out_path)
                    if not _us["ok"]:
                        _obj = {"ok": False, "improved": False, "should_try_harder": False,
                                "hard_veto": True,   # artifact — never accept via score delta
                                "reason": f"undershoot guard: {_us['reason']}",
                                "metrics": _obj.get("metrics", {})}
                        log.warning(f"[autoprocess] {target}: deconvolution VETOED — "
                                    f"{_us['reason']}")
                except Exception as _ue:
                    log.warning(f"[autoprocess] {target}: undershoot guard failed ({_ue}) "
                                "— accepting objective result")
            if _obj.get("hard_veto"):
                _last_hard_veto = _obj
            if _stats_after:
                _na = _stats_after.get("noise", {})
                _ba = _stats_after.get("background", {})
                _pa = _stats_after.get("psf", {})
                log.info(f"[autoprocess] {target}: {step_name} attempt {attempt+1} POST-STATS: "
                         f"SNR={_na.get('snr',0):.1f} "
                         f"FWHM={_pa.get('fwhm_median',0):.2f}px "
                         f"gradient={_ba.get('gradient_severity',0):.3f} | "
                         f"obj_check: {'OK' if _obj['ok'] else 'FAIL'} — {_obj['reason']}")

            if quality_dims:
                # Physics-based scoring for objective metric steps — no API call. For
                # non-physics-scored steps mini_scores stays empty; acceptance is then
                # handled by the objective/aesthetic gate below (physics-default mode —
                # no per-step Claude grading). See [[project-physics-default-pipeline]].
                _phys = _physics_score_dimensions(step_name, _stats_after, quality_dims)
                if _phys is not None:
                    mini_scores = _phys
                    log.debug(f"[autoprocess] {target}: {step_name} physics scores: "
                              + ", ".join(f"{d}={v}" for d, v in _phys.items()))

            new_sum = sum(_to_float(mini_scores.get(d, best_dim_scores.get(d, 5))) for d in quality_dims)
            delta = new_sum - best_sum
            dim_summary = ", ".join(f"{d}={mini_scores.get(d, '?')}" for d in quality_dims)
            log.info(f"[autoprocess] {target}: {step_name} attempt {attempt+1}/{step_max_iters} "
                     f"Δ={delta:+.2f} ({dim_summary}) | "
                     f"obj={'OK' if _obj['ok'] else 'FAIL'}: {_obj['reason']} | "
                     f"params={valid_params}")

            # Always keep the best result seen so far — UNLESS the objective check
            # hard-vetoed it (artifact class, e.g. deconvolution undershoot holes:
            # the ringing SHARPENS stars, so the physics score delta is positive and
            # would happily accept the damaged output — IC 1805 2026-07-07, the veto
            # fired but this line applied the result anyway).
            if new_sum > best_sum and not _obj.get("hard_veto"):
                best_path = out_path
                best_sum = new_sum
                best_dim_scores = {d: mini_scores.get(d, best_dim_scores.get(d, 5))
                                   for d in quality_dims}

            # Plateau / stagnation detection — stop only when improvement has peaked
            # or no improvement is occurring at all.
            # Goal: "best", not just "good enough". Keep iterating as long as we're
            # still improving. Stop if:
            #   (a) this attempt's gain is ≤25% of the best gain (diminishing returns), OR
            #   (b) we've made 2+ attempts with consistently negative delta (step not helping)
            _best_delta_seen = getattr(_plateau_tracker, 'best_delta', 0)
            _consec_neg = getattr(_plateau_tracker, 'consec_neg', 0)
            if delta > _best_delta_seen:
                _plateau_tracker.best_delta = delta  # type: ignore[attr-defined]
                _best_delta_seen = delta
                _plateau_tracker.consec_neg = 0  # type: ignore[attr-defined]
            elif delta <= 0:
                _plateau_tracker.consec_neg = _consec_neg + 1  # type: ignore[attr-defined]
                _consec_neg = _plateau_tracker.consec_neg  # type: ignore[attr-defined]
            else:
                _plateau_tracker.consec_neg = 0  # type: ignore[attr-defined]
                _consec_neg = 0
            _plateau = (
                (_best_delta_seen > 0 and delta <= _best_delta_seen * 0.25
                 and _obj.get("ok") and attempt > 0)          # diminishing returns
                or (_consec_neg >= 2 and _best_delta_seen <= 0)  # never improved
            )
            if _plateau:
                _reason = ("diminishing returns" if _best_delta_seen > 0
                           else f"{_consec_neg} consecutive non-improvements")
                log.info(f"[autoprocess] {target}: {step_name} — plateau ({_reason}) "
                         f"at attempt {attempt+1}, stopping")
                break

        # Accept the best result.
        #  • Physics-scored steps (_PHYSICS_SCORE_STEPS): accepted above only if their
        #    objective metric improved (best_path already moved off current_path).
        #  • Everything else physics chose to run, plus force_apply steps: accept the single
        #    deterministic output here. Aesthetic colour steps are applied unconditionally
        #    (only the catastrophic stats-veto below guards them — see
        #    [[feedback-aesthetic-steps]]); other physics-gated steps must pass the
        #    objective check, like force_apply.
        _phys_apply = (best_path == current_path and last_good_output is not None
                       and step_name not in _PHYSICS_SCORE_STEPS)
        if (force_apply or _phys_apply) and best_path == current_path and last_good_output:
            if step_name in _AESTHETIC_APPLY_STEPS:
                best_path = last_good_output
                log.info(f"[autoprocess] {target}: {step_name} applied "
                         f"(aesthetic — catastrophic stats-veto only)")
            elif (step_name == "color_calibration"
                  and isinstance(result, dict) and result.get("method") == "sssc"):
                # SSSC is a solved spectrophotometric calibration: per-channel gains
                # (k_R can exceed 2x on LP dual-band data) legitimately shift the
                # whole-frame SNR metric, so the SNR×0.90 objective gate would veto
                # correct calibrations. Quality is already gated inside sssc_calibrate
                # (applied-stage RMS < 2.0) — accept, like the crop exemption.
                best_path = last_good_output
                log.info(f"[autoprocess] {target}: {step_name} applied "
                         f"(SSSC — solver RMS-gated, whole-frame SNR check bypassed)")
            elif _stats_before:
                try:
                    _fa_stats = _img_analyze(str(last_good_output))
                    _fa_obj = _objective_check(step_name, _stats_before, _fa_stats)
                    if _fa_obj.get("ok") or _fa_obj.get("improved"):
                        best_path = last_good_output
                        log.info(f"[autoprocess] {target}: {step_name} applied "
                                 f"({'force_apply' if force_apply else 'physics-gate'}) — "
                                 f"{_fa_obj.get('reason', '')}")
                    else:
                        log.warning(f"[autoprocess] {target}: {step_name} REJECTED "
                                    f"({'force_apply' if force_apply else 'physics-gate'}) — "
                                    f"{_fa_obj.get('reason', '')} — step skipped")
                        telegram.send(f"⚠️ <b>{step_name}</b>: rejected — "
                                      f"{_fa_obj.get('reason', 'objective not met')}, step skipped")
                        # best_path stays as current_path — step has no acceptable output
                except Exception as _fa_e:
                    log.warning(f"[autoprocess] {target}: {step_name} objective check "
                                f"failed ({_fa_e}) — accepting last_good_output blind")
                    best_path = last_good_output  # can't check stats, accept blind
            else:
                best_path = last_good_output  # no baseline stats available, accept blind

        # Stats-based veto: if objective pixel stats show the accepted result is significantly
        # worse than the input, revert — catches catastrophic tool failures Claude may miss.
        # Crop is fully exempt: it is the user's explicit framing choice from the manual crop
        # review (or the persisted saved crop), so the pipeline must NOT second-guess it with
        # objective stats. Cropping reframes the image (different pixel population), so a
        # whole-frame SNR before/after comparison is meaningless anyway.
        if best_path != current_path and _stats_before and step_name != "crop":
            try:
                _sv_stats = _img_analyze(str(best_path))
                _snr_b = _stats_before.get("noise", {}).get("snr", 0)
                _snr_a = _sv_stats.get("noise", {}).get("snr", 0)
                # Sky-only gradient when available — the all-cells metric is object-
                # dominated and vetoed GraXpert results that flattened the sky perfectly.
                _bg_b = _stats_before.get("background", {})
                _bg_a = _sv_stats.get("background", {})
                _grad_b = _bg_b.get("gradient_severity_sky", _bg_b.get("gradient_severity", 1))
                _grad_a = _bg_a.get("gradient_severity_sky", _bg_a.get("gradient_severity", 1))
                # Aesthetic colour steps (color_boost/color_sat) intentionally raise chroma,
                # which the SNR metric misreads as noise — and on nonlinear starless images
                # the metric is unreliable anyway. Per the aesthetic-steps rule these must NOT
                # be score-gated, so only a catastrophic SNR collapse (real corruption: black/
                # NaN output) vetoes them. Everything else keeps the standard 15% guard.
                # SSSC calibration gets the same leniency: its per-channel gains
                # legitimately move the whole-frame SNR metric (solver RMS gate is
                # the real quality check) — only catastrophic collapse vetoes it.
                _snr_veto_lenient = (step_name in ("color_boost", "color_sat")
                                     or (step_name == "color_calibration"
                                         and isinstance(result, dict)
                                         and result.get("method") == "sssc"))
                _snr_veto_floor = 0.50 if _snr_veto_lenient else 0.85
                if _snr_b > 0 and _snr_a < _snr_b * _snr_veto_floor:
                    log.warning(f"[autoprocess] {target}: {step_name} STATS VETO — "
                                f"SNR {_snr_b:.1f}→{_snr_a:.1f} (>15% drop), reverting")
                    telegram.send(f"⚠️ <b>{step_name}</b>: stats veto — SNR dropped "
                                  f"{_snr_b:.1f}→{_snr_a:.1f} (>15%), result discarded")
                    best_path = current_path
                elif _grad_b > 0 and _grad_a > _grad_b * 1.20 and step_name in (
                        "background_extraction", "background_neutralize", "denoise_linear"):
                    log.warning(f"[autoprocess] {target}: {step_name} STATS VETO — "
                                f"gradient {_grad_b:.3f}→{_grad_a:.3f} (>20% worse), reverting")
                    telegram.send(f"⚠️ <b>{step_name}</b>: stats veto — gradient worsened "
                                  f"{_grad_b:.3f}→{_grad_a:.3f} (>20%), result discarded")
                    best_path = current_path
            except Exception as _sv_e:
                log.debug(f"[autoprocess] stats veto check failed: {_sv_e}")

        if best_path != current_path:
            _pre_step_path = current_path  # save for visual revert gate
            current_path = best_path
            current_scores.update(best_dim_scores)
            steps_applied.append(step_name)
            log_processing_step(
                target, step=step_name, engine="auto_process",
                params=valid_params,
                scores_before=scores_before or None,
                scores_after=best_dim_scores or None,
                claude_reasoning=phys_reason,
                elapsed_s=round(time.time() - start_ts, 1),
            )
            _std_rec = {
                "step": step_name, "type": "standard",
                "params": valid_params,
                "input_path": str(_pre_step_path),  # FITS this step ran on (WS5 corrective)
                "fn_name": fn_name,
                "scores_before": scores_before,
                "scores_after": best_dim_scores,
                "reasoning": phys_reason,
                "skipped": False,
                "preview_before": f"auto_preview_pre_{step_name}.jpg",
                "preview_after": f"auto_preview_{step_name}_a0.jpg",
            }
            # color_calibration: surface which engine ran (sssc/spcc/pi_cc_fallback)
            # plus the SSSC solve diagnostics for validation review
            if step_name == "color_calibration" and isinstance(result, dict):
                _std_rec["color_cal"] = {
                    k: result.get(k) for k in (
                        "method", "spcc_fell_back", "stage", "n_stars", "bv_span",
                        "residual_rms", "stage_rms", "applied_rms", "session_id",
                        "prior_seeded", "lp") if k in result}
            step_records.append(_std_rec)
            delta_parts = [
                f"{d}: {scores_before.get(d,'?')}→{best_dim_scores.get(d,'?')}"
                for d in quality_dims
            ]
            telegram.send(
                f"✅ <b>{step_name}</b>: <code>{target}</code>\n"
                f"  {' | '.join(delta_parts)}"
            )
            step_jpg = run_dir / f"auto_preview_{step_name}_a0.jpg"
            if step_jpg.exists():
                telegram.send_photo(str(step_jpg), caption=f"{target} — after {step_name}")

            # ── Video frame: standard step result ─────────────────────────
            # Standard-execution steps (e.g. narrowband_norm, color_boost when
            # not run as experiments) previously emitted only the image-less
            # intro slide, so they showed a blank panel. Render the step's actual
            # output so every applied step has a real picture.
            if _video:
                try:
                    _std_vjpg = run_dir / f"auto_preview_{step_name}_vframe.jpg"
                    if not _std_vjpg.exists():
                        (_generate_preview_nl if _nonlinear else _generate_preview)(
                            current_path, _std_vjpg)
                    if not _std_vjpg.exists() and step_jpg.exists():
                        _std_vjpg = step_jpg
                    if _std_vjpg.exists():
                        _std_stats: dict[str, str] = {}
                        if _stats_after:
                            _ssn = _stats_after.get("noise", {})
                            _ssp = _stats_after.get("psf", {})
                            if _ssn.get("snr"):
                                _std_stats["SNR"] = f"{_ssn['snr']:.1f}"
                            if _ssp.get("fwhm_median"):
                                _std_stats["FWHM"] = f"{_ssp['fwhm_median']:.2f}px"
                        _video.add_frame(
                            act="process", step_name=step_name,
                            image_path=_std_vjpg, stage="process",
                            stats=_std_stats or None, duration_s=2.5,
                        )
                except Exception as _stdve:
                    log.debug(f"[video] standard step frame failed: {_stdve}")

            # Visual revert gate (Claude assess_quality_dimensions on Claude-added
            # optional steps) REMOVED (physics-default). The nonlinear planner that
            # populated _claude_added_steps is gone, so optional steps now come only
            # from the WS1 physics gate (_physics_should_run) and are governed by the
            # STATS VETO above — no per-step Claude. See [[project-physics-default-pipeline]].
        else:
            log.info(f"[autoprocess] {target}: {step_name} — no improvement after "
                     f"{step_max_iters} attempt(s), keeping previous")
            # Persist the real rejection cause: a hard-vetoed step (artifact class —
            # decon undershoot, halo output contract, …) previously collapsed to the
            # generic "no improvement", losing the reason + metrics the veto measured.
            _skip_rec = {
                "step": step_name, "type": "standard",
                "params": valid_params,
                "scores_before": scores_before,
                "scores_after": None,
                "reasoning": phys_reason,
                "skipped": True,
                "skip_reason": (f"hard_veto: {_last_hard_veto['reason']}"
                                if _last_hard_veto else "no improvement"),
                "preview_before": f"auto_preview_pre_{step_name}.jpg",
            }
            if _last_hard_veto:
                _skip_rec["hard_veto"] = True
                _skip_rec["veto_metrics"] = _last_hard_veto.get("metrics") or {}
            step_records.append(_skip_rec)
            telegram.send(f"➖ <b>{step_name}</b>: <code>{target}</code> — no improvement, skipped")

    # ── finalise ────────────────────────────────────────────────────────
    elapsed = time.time() - start_ts
    _set_status(target, phase="finalizing")

    # ── Non-degradation guard ────────────────────────────────────────────
    # If the final state regressed below the best publishable checkpoint, ship
    # the best one instead. Replaces the old early-stop break (see assess loop):
    # protect a good image from later steps without ever truncating mandatory
    # tail steps. Margin avoids churn on noise-level score wobble.
    if not dry_run and _best_pub.get("path") and Path(_best_pub["path"]).exists():
        _cur_overall = _to_float(current_scores.get("overall", 0))
        if _best_pub["score"] > _cur_overall + 0.3:
            log.info(f"[autoprocess] {target}: non-degradation — final "
                     f"{_cur_overall}/10 < best publishable {_best_pub['label']} "
                     f"{_best_pub['score']}/10; reverting to best.")
            telegram.send(
                f"↩️ <b>non-degradation</b>: <code>{target}</code> final "
                f"{_cur_overall}/10 regressed below {_best_pub['label']} "
                f"{_best_pub['score']}/10 — shipping the better checkpoint."
            )
            current_path = Path(_best_pub["path"])
            if _best_pub.get("scores"):
                current_scores = dict(_best_pub["scores"])

    output_path = str(current_path)
    final_jpg_path = None
    if not dry_run and current_path != source_fits:
        # Belt-and-suspenders: catch any downstream step (combine/curves) that left a
        # channel black-clipped to a coloured near-black pedestal, re-neutralising the
        # sky before we publish. No-op unless the crush signature is present.
        if _guard_channel_crush(current_path, target):
            steps_applied.append("channel_crush_guard")
            # Keep the recorded score honest (workflow 1.24.0/1.24.1, batch eval
            # 2026-07-16 "final ≠ best_publishable" mystery): the guard mutates
            # pixels AFTER the final evaluation, so run.log's final score described
            # an image nobody ships. 1.24.0 REPLACED final_scores with the physics
            # re-grade — wrong: physics scores a different scale (M 109 color 1.0
            # tanked a 7.2 to 6.5), breaking cross-run comparability. 1.24.1: keep
            # the evaluator's final_scores authoritative, ATTACH the shipped-pixel
            # physics grade alongside so critiques can see both.
            try:
                _rg = _physics_grade_nonlinear(
                    current_path, object_type, _run_depth,
                    frame_fill=frame_fill_info.get("frame_fill", False))
                if _rg and _rg.get("overall") is not None:
                    log.info(f"[autoprocess] {target}: shipped-pixel physics grade "
                             f"after channel_crush_guard: {_rg['overall']} "
                             f"(final eval {current_scores.get('overall')} kept)")
                    current_scores = dict(current_scores)
                    current_scores["post_guard_physics"] = {
                        k: _rg.get(k) for k in ("overall", "noise", "gradient",
                                                "stretch_quality", "color_balance")}
            except Exception as _rge:
                log.warning(f"[autoprocess] {target}: post-guard physics grade "
                            f"failed ({_rge})")
        # final.fit lives inside the run dir
        final_fits = run_dir / "final.fit"
        shutil.copy2(str(current_path), str(final_fits))
        # Safety net (workflow 1.12.1): guarantee the FINAL carries the WCS even if some
        # forced step dropped it, so the final preview renders orientation-consistent.
        # WCS is invariant through the pipeline (no rotation), so copy it from an early
        # WCS-bearing intermediate (crop/background_extraction, same dimensions).
        try:
            from nas_server.seti_astro import _preserve_celestial_wcs as _pw
            import glob as _g
            _wsrc = (sorted(_g.glob(str(run_dir / "*auto_crop*.fit")))
                     or sorted(_g.glob(str(run_dir / "*background_extraction*.fit"))))
            if _wsrc:
                _pw(_wsrc[0], final_fits)
        except Exception:
            pass
        final_jpg = run_dir / "final_preview.jpg"
        (_generate_preview_nl if _nonlinear else _generate_preview)(final_fits, final_jpg)
        output_path = str(final_fits)
        if final_jpg.exists():
            final_jpg_path = str(final_jpg)
        # also keep a copy at proc_dir for quick external access. Suffix branch
        # outputs (auto_final_nbn.fit) so they don't clobber the original run.
        _ext_fit = f"auto_final_{_output_suffix}.fit" if _output_suffix else "auto_final.fit"
        _ext_jpg = (f"auto_final_{_output_suffix}_preview.jpg"
                    if _output_suffix else "auto_final_preview.jpg")
        shutil.copy2(str(final_fits), str(proc_dir / _ext_fit))
        shutil.copy2(str(final_jpg), str(proc_dir / _ext_jpg))

    log.info(f"[autoprocess] {target}: done in {elapsed:.0f}s — steps: {steps_applied}")

    if not dry_run:
        # Critical evaluation from Claude (with final image)
        critical_eval = None
        try:
            critical_eval = generate_critical_eval(
                target=target,
                workflow=workflow,
                object_type=object_type,
                initial_scores=initial_scores,
                final_scores=current_scores,
                steps_applied=steps_applied,
                step_records=step_records,
                final_jpeg=final_jpg_path,
                meta={
                    "frame_count": latest.get("frame_count"),
                    "total_hours": round((latest.get("total_integration") or 0) / 3600, 2),
                    "filter": _capture_filter,
                },
            )
        except Exception as e:
            log.warning(f"[autoprocess] {target}: critical eval failed: {e}")

        # ── Hook 3: Backfill adaptive decision outcomes ──────────────────
        if _aesthetic_forced:
            log.info(f"[autoprocess] {target}: skipping adaptive outcome backfill "
                     f"(aesthetic-forced run — score reflects look, not decisions)")
        elif _adaptive_run_id and (initial_scores or current_scores):
            try:
                from nas_server.database import update_adaptive_outcomes as _uao
                _final_sc = _to_float(current_scores.get("overall", 5.0))
                _init_sc = _to_float(initial_scores.get("overall", 5.0))
                _uao(_adaptive_run_id, _final_sc, _init_sc)
                log.info(f"[autoprocess] {target}: adaptive outcomes updated — "
                         f"initial={_init_sc:.1f} final={_final_sc:.1f} "
                         f"delta={_final_sc - _init_sc:+.1f}")
            except Exception as _h3_err:
                log.warning(f"[autoprocess] {target}: adaptive outcome backfill failed: {_h3_err}")

        # ── Video: final done frame + background compile ──────────────────
        if _video:
            try:
                _final_img = Path(final_jpg_path) if final_jpg_path else None
                _folio_common = (folio or {}).get("common_name", "") if folio else ""
                _total_hours = round((latest.get("total_integration") or 0) / 3600, 2)
                _frame_count = latest.get("frame_count") or 0
                _final_sc_v = _to_float(current_scores.get("overall", 5.0))
                _init_sc_v = _to_float(initial_scores.get("overall", 5.0))
                _video.add_frame(
                    act="done", step_name="final_result",
                    image_path=_final_img,
                    stage="done",
                    step_label="Final Result",
                    caption=(
                        f"{_folio_common or target} · "
                        f"{_frame_count:,} frames · {_total_hours:.1f}h"
                    ),
                    stats={
                        "Score":   f"{_final_sc_v:.1f}/10",
                        "Steps":   str(len(steps_applied)),
                        "Time":    f"{elapsed:.0f}s",
                        "Δ":  f"{_final_sc_v - _init_sc_v:+.1f}",
                    },
                    score=_final_sc_v,
                    score_delta=_final_sc_v - _init_sc_v,
                    duration_s=5.0,
                )
                # Compile in a background daemon thread so the pipeline returns immediately
                import threading as _threading
                _threading.Thread(
                    target=_video.compile_video,
                    daemon=True,
                    name=f"video-compile-{target}",
                ).start()
                log.info(f"[video] {target}: background compile started "
                         f"({_video.frame_count()} frames)")
            except Exception as _vfe:
                log.debug(f"[video] final frame/compile failed: {_vfe}")

        # AI-model usage diagnostics for this run (calls/tokens/latency by label+backend)
        try:
            api_diag = api_diagnostics.summarize(api_diagnostics.collect(_diag_mark))
            _bk = api_diag.get("by_backend", {})
            _bk_str = ", ".join(f"{k}x{g['calls']}" for k, g in _bk.items()) or "none"
            log.info(
                f"[autoprocess] {target}: AI usage — {api_diag['calls']} calls, "
                f"{api_diag['total_tokens']:,} tok, {api_diag['total_latency_s']:.1f}s "
                f"(backends: {_bk_str})"
            )
        except Exception as _de:
            log.debug(f"[autoprocess] {target}: api diagnostics summarize failed: {_de}")
            api_diag = None

        # Write run.log JSON into the run directory
        try:
            from nas_server.workflow_version import workflow_version
            run_log = {
                "target": target, "workflow": workflow,
                "workflow_version": workflow_version(),
                "object_type": object_type,
                "started_at": started_at, "elapsed_s": round(elapsed, 1),
                "steps_applied": steps_applied,
                "initial_scores": initial_scores,
                "final_scores": current_scores,
                "step_records": step_records,
                "frame_fill": frame_fill_info,
                "api_diagnostics": api_diag,
                "data_integrity_flags": integrity_flags,
                "run_dir": str(run_dir),
            }
            (run_dir / "run.log").write_text(
                json.dumps(run_log, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            log.warning(f"[autoprocess] {target}: run.log write failed: {e}")

        # Save the full run record for the report page
        try:
            _run_id = save_processing_run(
                target=target,
                workflow=workflow,
                started_at=started_at,
                elapsed_s=round(elapsed, 1),
                steps=step_records,
                initial_scores=initial_scores,
                final_scores=current_scores,
                critical_eval=critical_eval,
                output_path=output_path,
                dry_run=dry_run,
                api_diagnostics=api_diag,
            )
            if not dry_run:
                try:
                    from nas_server.folio_generator import maybe_update_hero
                    _ov = current_scores.get("overall") if isinstance(current_scores, dict) else None
                    if isinstance(_ov, (int, float)):
                        maybe_update_hero(target, _run_id, output_path, float(_ov))
                except Exception as _he:
                    log.debug(f"[autoprocess] {target}: hero update skipped: {_he}")
        except Exception as e:
            log.warning(f"[autoprocess] {target}: save_processing_run failed: {e}")

        def _score_line(scores: dict) -> str:
            return "\n".join(
                f"  {k}: {v}/10"
                for k, v in scores.items()
                if isinstance(v, (int, float))
                and k not in ("input_tokens", "output_tokens")
            )

        before = _score_line(initial_scores) if initial_scores else "  (no initial scores)"
        after = _score_line(current_scores) if current_scores else "  (no final scores)"
        telegram.send(
            f"🤖 <b>AutoProcess complete</b>: <code>{target}</code>\n"
            f"Workflow: {workflow} | {elapsed:.0f}s\n"
            f"Steps applied: {', '.join(steps_applied) or 'none'}\n"
            f"Before:\n{before}\n"
            f"After:\n{after}"
        )
        if final_jpg_path and Path(final_jpg_path).exists():
            telegram.send_photo(final_jpg_path, caption=f"{target} — final result")


        # Auto-generate story narrative now that we have fresh scores and history
        if settings.get("anthropic_api_key") and steps_applied and not physics_only:
            try:
                from nas_server.database import get_story_data, get_conn
                from nas_server.claude_client import write_story_entry
                story_rows = get_story_data(target)
                if story_rows:
                    narrative = write_story_entry(target, story_rows[0])
                    if narrative:
                        with get_conn() as conn:
                            conn.execute(
                                "DELETE FROM claude_assessments "
                                "WHERE target=? AND phase='story_narrative'",
                                (target,),
                            )
                            conn.execute(
                                "INSERT INTO claude_assessments "
                                "(target, phase, model, recommendation, created_at) "
                                "VALUES (?, 'story_narrative', 'claude-sonnet-4-6', ?, datetime('now'))",
                                (target, narrative),
                            )
                        log.info(f"[autoprocess] {target}: story narrative updated")
            except Exception as e:
                log.warning(f"[autoprocess] {target}: story narrative failed: {e}")

        # Number run files for sequential sort in file explorer
        try:
            _number_run_files(run_dir)
        except Exception as _nrf_err:
            log.debug(f"[autoprocess] {target}: _number_run_files failed: {_nrf_err}")

    _set_status(
        target,
        phase="done",
        elapsed=elapsed,
        steps_applied=steps_applied,
        final_scores=current_scores,
    )

    return {
        "ok": True,
        "target": target,
        "workflow": workflow,
        "steps_applied": steps_applied,
        "final_scores": current_scores,
        "output_path": output_path,
        "elapsed": elapsed,
        "dry_run": dry_run,
    }

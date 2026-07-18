"""First-process manual crop review.

On a target's first auto-process the crop step generates candidate crops, opens a
manual review (Cropper.js editor + candidate previews), and blocks until the user
picks one or draws their own. The chosen crop is persisted via `target_crop` and
reused — without any review — on every later run.

Queue handling mirrors the experiment review: a 5-minute grace wait, then the queue
is released (parked) so other targets proceed while this one waits indefinitely for
the user. Abort/retry propagate as ProcessingAbortedError / ProcessingRetryError.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("seestar.crop_review")

_DISPLAY = {
    "artifact":     "Artifact trim",
    "canonical":    "Canonical frame",
    "coverage":     "Coverage ≥80%",
    "intersection": "Intersection",
    "lir":          "LIR (full)",
}


def _await_decision(review_id: int, target: str, ev) -> None:
    """Block until the user decides. Raises Abort/Retry; returns on 'decided'."""
    from nas_server.database import get_manual_review
    from nas_server import review_events, queue_manager, telegram
    from nas_server.exceptions import ProcessingAbortedError, ProcessingRetryError

    deadline = datetime.now(timezone.utc) + timedelta(seconds=300)
    while datetime.now(timezone.utc) < deadline:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if ev.wait(timeout=min(10.0, remaining)):
            break

    review = get_manual_review(review_id)
    status = review["status"] if review else None
    if status == "decided":
        review_events.unregister(review_id)
        return
    if status == "aborted":
        review_events.unregister(review_id)
        raise ProcessingAbortedError(f"Crop review aborted: {target}")
    if status == "retried":
        review_events.unregister(review_id)
        raise ProcessingRetryError("crop")

    # Grace elapsed — park (release queue) and wait indefinitely.
    queue_manager.park_for_review(target)
    logger.info(f"[crop_review] #{review_id} grace elapsed — queue released, "
                f"waiting for user: {target}")
    try:
        telegram.send(f"⏸ <b>Crop review parked</b>: {target}\n"
                      f"Queue continuing. Submit when ready.")
    except Exception:
        pass

    # Release the pipeline lock while parked: this thread is idle waiting on the
    # user, and holding PIPELINE_LOCK would block every other local autoprocess
    # job (the next queued target would 'start' then stall on the lock). Re-acquire
    # before returning so the rest of this pipeline runs serialized again.
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
    status = review["status"] if review else "unknown"
    if status == "decided":
        return
    if status == "aborted":
        raise ProcessingAbortedError(f"Crop review aborted: {target}")
    if status == "retried":
        raise ProcessingRetryError("crop")
    logger.warning(f"[crop_review] #{review_id} woke with status={status!r}")


def run_crop_review(target: str, run_id, input_fits: str, out_fits: str,
                    run_dir, object_type: str = "", coverage_path: str = "") -> dict:
    """Generate crop candidates, review with the user, write the chosen crop to
    out_fits, and persist it for reuse. Returns {"ok", "output_path", "chosen"}.

    Raises ProcessingAbortedError / ProcessingRetryError on user abort/retry.
    """
    from nas_server import seti_astro, review_events, telegram
    from nas_server.database import create_manual_review, get_manual_review
    from nas_server.config import settings
    from nas_server import target_crop as _tc

    exp_dir = Path(run_dir) / "crop_review"
    exp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate candidates (crop_multi writes auto_crop_<name>.fit beside default_out)
    default_out = exp_dir / "auto_crop_default.fit"
    res = seti_astro.crop_multi(str(input_fits), str(default_out), target=target,
                                coverage_path=coverage_path or "")
    cands = res.get("candidates", {}) if res.get("ok") else {}
    default_choice = res.get("chosen")

    # 2. Build review variants (largest-area first), each with an STF preview
    order = sorted(cands.items(), key=lambda kv: -(kv[1].get("area") or 0))
    variants_json: list[dict] = []
    ordered_labels: list[dict] = []
    for name, meta in order:
        cpath = meta.get("path")
        if not cpath or not Path(cpath).exists():
            continue
        jpg = exp_dir / f"crop_{name}.jpg"
        try:
            seti_astro.generate_preview_stf(str(cpath), str(jpg))
            jpg_str = str(jpg) if jpg.exists() else ""
        except Exception:
            jpg_str = ""
        label = _DISPLAY.get(name, name)
        variants_json.append({
            "label": label, "variant_id": name, "jpeg_path": jpg_str,
            "metrics": {"shortPx": meta.get("short"), "areaPx": meta.get("area")},
        })
        ordered_labels.append({"label": label, "variant_id": name})

    if not variants_json:
        if res.get("ok") and default_out.exists():
            shutil.copy2(str(default_out), str(out_fits))
            return {"ok": True, "output_path": str(out_fits), "chosen": default_choice}
        return {"ok": False, "error": "no crop candidates"}

    default_label = _DISPLAY.get(default_choice, default_choice or "")

    # 3. Open the review
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=300)
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
    review_id = create_manual_review(
        target=target, step="crop", run_id=str(run_id),
        input_fits_path=str(input_fits),
        ordered_labels=ordered_labels, variants=variants_json,
        claude_winner_label=default_label,
        claude_reasoning=f"Auto-default crop = {default_label}",
        expires_at=expires_at,
    )
    ev = review_events.register(review_id)

    server_host = settings.get("server_host", "http://localhost:8000")
    review_url = f"{server_host}/review/{review_id}"
    try:
        telegram.send(
            f"✂ <b>Crop review: {target}</b>\n"
            f"Pick a crop or draw your own — remembered for future runs.\n{review_url}"
        )
    except Exception:
        pass
    logger.info(f"[crop_review] #{review_id} {target}: {len(variants_json)} candidates "
                f"(default={default_label}) — {review_url}")

    # 4. Block for the decision (grace → park → wait)
    _await_decision(review_id, target, ev)

    review = get_manual_review(review_id)
    final = review.get("final_winner_variant") if review else None

    # 5. Resolve the chosen FITS and persist for reuse
    if final == "manual_crop":
        winner = exp_dir / "winner.fit"
        if not winner.exists():
            logger.warning(f"[crop_review] {target}: manual winner.fit missing — "
                           f"using default '{default_choice}'")
            final = default_choice
        else:
            shutil.copy2(str(winner), str(out_fits))
            # Persistence for manual crops is done in the apply-crop endpoint
            # (it knows the exact geometry incl. rotation).
            logger.info(f"[crop_review] {target}: applied manual crop")
            return {"ok": True, "output_path": str(out_fits), "chosen": "manual"}

    cpath = (cands.get(final) or {}).get("path")
    if not cpath or not Path(cpath).exists():
        cpath = (cands.get(default_choice) or {}).get("path")
        final = default_choice
    if not cpath or not Path(cpath).exists():
        return {"ok": False, "error": "chosen crop FITS missing"}

    shutil.copy2(str(cpath), str(out_fits))
    try:
        frac = None if final == "canonical" else _tc.frac_bounds_from_crop(
            str(input_fits), str(cpath))
        _tc.save_crop_from_fits(target, str(cpath), source=str(final), frac=frac)
    except Exception as e:
        logger.warning(f"[crop_review] {target}: persist saved crop failed: {e}")
    logger.info(f"[crop_review] {target}: applied + saved crop '{final}'")
    return {"ok": True, "output_path": str(out_fits), "chosen": final}

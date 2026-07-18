"""
Step-by-step diagnostic walkthrough of the quick_default pipeline.

Runs ONE step at a time, saves FITS + JPG, sends Telegram, then PAUSES and
waits for a keypress before moving to the next step.

Usage:
    python3 -m nas_server.debug_walkthrough "M 51"
    python3 -m nas_server.debug_walkthrough "M 51" --from-step 3   # resume from a step
    python3 -m nas_server.debug_walkthrough "M 51" --dir <existing-dir>  # reuse existing dir
"""

import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Bootstrap Telegram (not called at startup when running standalone)
# ---------------------------------------------------------------------------
def _init_telegram():
    from nas_server.config import settings
    from nas_server import telegram
    telegram.configure(
        settings.get("telegram_token", ""),
        settings.get("telegram_chat_id", ""),
    )


# ---------------------------------------------------------------------------
# Preview — normal STF so the image looks like what you'd actually see
# ---------------------------------------------------------------------------
def _preview(fits_path: Path, jpg_path: Path, label: str) -> bool:
    try:
        from nas_server import seti_astro
        from PIL import Image, ImageDraw
        # PI STF: target_bg=0.25, shadow_clip_k=2.8σ, Boost background=2.00.
        # The boost is an internal PI correction, not simply ×2 on target_bg.
        # Empirically ~0.30 matches PI's visual output for post-GraXpert data.
        ok = seti_astro.generate_preview_stf(
            fits_path, jpg_path, target_bg=0.30, shadow_clip_k=2.8
        )
        if not ok or not jpg_path.exists():
            return False
        img = Image.open(str(jpg_path)).convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, img.width, 22], fill=(15, 15, 15))
        draw.text((4, 4), label, fill=(220, 220, 220))
        img.save(str(jpg_path), quality=90)
        return True
    except Exception as e:
        log.warning(f"Preview failed for {label}: {e}")
        return False


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _tg(msg: str) -> None:
    try:
        from nas_server import telegram
        telegram.send(msg)
    except Exception as e:
        log.warning(f"Telegram text failed: {e}")


def _tg_photo(jpg_path: Path, caption: str) -> None:
    try:
        from nas_server import telegram
        telegram.send_photo(str(jpg_path), caption=caption)
    except Exception as e:
        log.warning(f"Telegram photo failed: {e}")


# ---------------------------------------------------------------------------
# PI variant runner
# ---------------------------------------------------------------------------
def _run_pi(target: str, extra: dict, inp: Path, out: Path) -> bool:
    from nas_server.pixinsight import run_postprocess
    params = dict(
        target=target, input_fits=str(inp), output_path=str(out),
        dbe=False, gradient_correction=False,
        color_calibration=False, bgn=False, spcc=False,
        mlt=False, tgv=False,
        bxt=False, nxt=False, starxt=False,
        ht=False, scnr=False, hdrmt=False, lhe=False,
        color_sat=False, curves=False, cms=False, morph=False,
        timeout=600,
    )
    params.update(extra)
    r = run_postprocess(**params)
    return bool(r.get("ok") and out.exists())


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------
STEPS = [
    "background_extraction",
    "color_calibration",
    "deconvolution",
    "denoise_linear",
    "remove_stars_linear",
    "stretch_stars",
    "stretch",
    "combine_stars_screen",
]

STEP_DESCRIPTIONS = {
    "background_extraction": "GraXpert — subtract background gradient",
    "color_calibration":     "SPCC → ColorCalibration fallback",
    "deconvolution":         "BlurXTerminator — sharpen stars/detail",
    "denoise_linear":        "NoiseXTerminator — reduce noise (linear)",
    "remove_stars_linear":   "StarXTerminator — split stars from nebulosity",
    "stretch_stars":         "Stretch the stars layer",
    "stretch":               "STF stretch — starless to non-linear",
    "combine_stars_screen":  "Screen-blend stars back in",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_walkthrough(target: str, from_step: int = 1, existing_dir: str | None = None):
    from nas_server.config import settings
    from nas_server import seti_astro
    from nas_server.database import get_processed_files
    from nas_server.auto_process import _strip_bayerpat_for_pi

    lib = settings["seestar_library_path"]
    proc_dir = Path(lib) / target / "_processed"

    # --- Locate source FITS ---
    files = get_processed_files(target)
    if not files:
        log.error(f"No processed files for {target}")
        return
    source_fits = proc_dir / files[0]["filename"]
    if not source_fits.exists():
        log.error(f"Source FITS not found: {source_fits}")
        return

    # --- Output directory ---
    if existing_dir:
        out_dir = Path(existing_dir)
    else:
        from datetime import datetime
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_dir = proc_dir / "runs" / f"{ts}_walkthrough"
        out_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Walkthrough: {target}  out={out_dir}")
    print(f"\nOutput dir: {out_dir}\n")

    # --- Step 0: prepare source (strip BAYERPAT) ---
    if from_step == 1:
        current = _strip_bayerpat_for_pi(source_fits, out_dir, dry_run=False)
        raw_jpg = out_dir / "00_source.jpg"
        _tg(
            f"🔬 <b>Walkthrough</b>: <code>{target}</code>\n"
            f"Source: <code>{source_fits.name}</code>\n"
            f"Dir: <code>{out_dir}</code>"
        )
        if _preview(current, raw_jpg, "0  source"):
            _tg_photo(raw_jpg, f"{target} — 0  source (BAYERPAT stripped)")
        _tg(f"Steps: {' → '.join(STEPS)}")
    else:
        # Resume: current = output of the previous step
        prev_i = from_step - 1
        prev_name = STEPS[prev_i - 1]
        current = out_dir / f"{prev_i:02d}_{prev_name}.fit"
        if not current.exists():
            log.error(f"Previous step output not found: {current}")
            return
        log.info(f"Resuming from step {from_step}, current={current.name}")

    stars_path: Path | None = None

    for i, step_name in enumerate(STEPS, 1):
        if i < from_step:
            continue

        step_desc = STEP_DESCRIPTIONS[step_name]
        out_fit = out_dir / f"{i:02d}_{step_name}.fit"
        out_jpg = out_dir / f"{i:02d}_{step_name}.jpg"
        label   = f"{i}/{len(STEPS)}  {step_name}"

        print(f"\n{'='*60}")
        print(f"STEP {label}")
        print(f"  {step_desc}")
        print(f"  Input:  {current.name}")
        print(f"  Output: {out_fit.name}")
        print(f"{'='*60}")

        _tg(f"▶ <b>Step {label}</b>\n{step_desc}\nInput: <code>{current.name}</code>")

        t0 = time.time()
        ok = False

        if step_name == "background_extraction":
            r = seti_astro.background_extract(
                current, out_fit, correction="Subtraction", smoothing=0.5
            )
            ok = r.get("ok", False) and out_fit.exists()

        elif step_name == "color_calibration":
            r = seti_astro.spcc(current, out_fit, spcc_lp_filter=False, target=target)
            ok = r.get("ok", False) and out_fit.exists()

        elif step_name == "deconvolution":
            ok = _run_pi(target, {
                "bxt": True, "bxt_psf": 4.0,
                "bxt_nonstellar": 0.3, "bxt_stars": 0.5,
            }, current, out_fit)

        elif step_name == "denoise_linear":
            ok = _run_pi(target, {
                "nxt": True, "nxt_denoise": 0.7, "nxt_iterations": 2,
            }, current, out_fit)

        elif step_name == "remove_stars_linear":
            stars_fit = out_dir / f"{i:02d}_{step_name}_stars.fit"
            stars_jpg = out_dir / f"{i:02d}_{step_name}_stars.jpg"
            ok = _run_pi(target, {
                "starxt": True,
                "starxt_stars_output": str(stars_fit),
            }, current, out_fit)
            if ok and stars_fit.exists():
                stars_path = stars_fit
                if _preview(stars_fit, stars_jpg, f"{label}  stars layer"):
                    _tg_photo(stars_jpg, f"{target} — {label}  stars layer")

        elif step_name == "stretch_stars":
            if stars_path is None or not stars_path.exists():
                _tg(f"⚠️ No stars layer — skipping {step_name}")
                print("  SKIPPED — no stars layer")
                continue
            r = seti_astro.star_stretch(
                stars_path, out_fit,
                stretch_factor=5.0, saturation=1.2,
                do_scnr=True, scnr_amount=0.9, gamma=1.0,
            )
            ok = r.get("ok", False) and out_fit.exists()
            if ok:
                stars_path = out_fit

        elif step_name == "stretch":
            r = seti_astro.stf_stretch(
                current, out_fit,
                target_bg=0.07, shadow_clip_k=1.25, linked=True,
            )
            ok = r.get("ok", False) and out_fit.exists()

        elif step_name == "combine_stars_screen":
            if stars_path is None or not stars_path.exists():
                _tg(f"⚠️ No stars layer — skipping {step_name}")
                print("  SKIPPED — no stars layer")
                continue
            r = seti_astro.combine_stars_screen(current, stars_path, out_fit)
            ok = r.get("ok", False) and out_fit.exists()

        elapsed = round(time.time() - t0, 1)

        if ok:
            current = out_fit
            if _preview(out_fit, out_jpg, f"{label}  ({elapsed:.0f}s)"):
                _tg_photo(out_jpg, f"{target} — {label}  ({elapsed:.0f}s)")
            _tg(f"✅ <b>Step {label}</b> done in {elapsed:.0f}s")
            print(f"  ✅ Done in {elapsed:.0f}s")
            print(f"  FITS: {out_fit}")
        else:
            _tg(f"❌ <b>Step {label}</b> FAILED after {elapsed:.0f}s")
            print(f"  ❌ FAILED after {elapsed:.0f}s")
            print(f"  FITS exists: {out_fit.exists()}")

        # --- PAUSE: wait for keypress ---
        print(f"\nPress ENTER to continue to the next step, or Ctrl+C to stop.")
        try:
            input()
        except KeyboardInterrupt:
            print("\nStopped.")
            _tg(f"⏸ Walkthrough paused at step {label}")
            return

    _tg(f"🏁 <b>Walkthrough complete</b>: <code>{target}</code>")
    print("\nWalkthrough complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="M 51")
    parser.add_argument("--from-step", type=int, default=1, dest="from_step")
    parser.add_argument("--dir", dest="existing_dir", default=None)
    args = parser.parse_args()

    _init_telegram()
    run_walkthrough(args.target, from_step=args.from_step, existing_dir=args.existing_dir)

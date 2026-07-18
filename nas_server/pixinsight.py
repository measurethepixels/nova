"""PixInsight headless post-processing wrapper — full tool suite."""
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

import os as _os
PI_BIN         = _os.environ.get("PI_BIN", "/opt/PixInsight/bin/PixInsight")
PI_JS          = str(Path(__file__).parent / "pi_postprocess.js")
PI_IHDR_JS     = str(Path(__file__).parent / "pi_ihdr.js")
PI_STACK_JS    = str(Path(__file__).parent / "pi_stack.js")
PI_SOLVE_JS    = str(Path(__file__).parent / "pi_solve.js")
PI_REGISTER_JS             = str(Path(__file__).parent / "pi_register.js")
PI_DRIZZLE_JS              = str(Path(__file__).parent / "pi_drizzle.js")
PI_REGISTER_AND_DRIZZLE_JS = str(Path(__file__).parent / "pi_register_and_drizzle.js")

# Environment required to run PI headlessly (offscreen Qt, bundled libs)
PI_ENV_OVERLAY = {
    "LD_LIBRARY_PATH": "/opt/PixInsight/bin/lib",
    "QT_QPA_PLATFORM": "offscreen",
    "QT_PLUGIN_PATH": "/opt/PixInsight/bin/lib/qt-plugins",
    "QT_QPA_PLATFORM_PLUGIN_PATH": "/opt/PixInsight/bin/lib/qt-plugins/platforms",
    "QT_LOGGING_RULES": "*=false",
    "LC_ALL": "en_US.utf8",
    "MKL_ENABLE_INSTRUCTIONS": "AVX2",
    "MKL_NUM_THREADS": "4",
    "OMP_NUM_THREADS": "4",
    "AVAHI_COMPAT_NOWARN": "1",
    "LIBGL_ALWAYS_SOFTWARE": "1",   # force Mesa software rendering (required on WSL2/no-GPU)
}

# Set PI_FORCE_XVFB=1 in environment to always wrap PI with xvfb-run (e.g. on laptop worker)
_PI_FORCE_XVFB: bool = _os.environ.get("PI_FORCE_XVFB", "0") == "1"

PI_CACHE_DIR = Path.home() / ".PixInsight"


def _cleanup_pi_workspace(output_dir: str | None = None) -> None:
    """Remove PI ImageIntegration cache files and rejection maps after a run."""
    # Clear II cache files (can grow very large between runs)
    for cache_file in PI_CACHE_DIR.glob("ImageIntegration-*.cache"):
        try:
            size_mb = cache_file.stat().st_size // (1024 * 1024)
            cache_file.unlink()
            log.info(f"[pi] cleanup: removed {cache_file.name} ({size_mb} MB)")
        except Exception as e:
            log.warning(f"[pi] cleanup: could not remove {cache_file}: {e}")

    # Remove rejection maps and normalised frames left in the output directory
    if output_dir:
        out_path = Path(output_dir)
        for pattern in ("*_rjmap.fits", "*rejection_high.fits", "*rejection_low.fits",
                        "*_norm.fits", "*_norm.fit", "*integration_rejected*.fits"):
            for f in out_path.glob(pattern):
                try:
                    f.unlink()
                    log.info(f"[pi] cleanup: removed rejection map {f.name}")
                except Exception as e:
                    log.warning(f"[pi] cleanup: could not remove {f}: {e}")


def _kill_lingering_pi() -> None:
    """Kill any PixInsight processes left from a previous run or crash, and clear
    PI's crash-detection shared memory so the next invocation doesn't see
    'A running instance has crashed' and exit in <10 seconds.

    PI uses Qt QSharedMemory objects in /dev/shm/ to track running instances.
    When PI is killed (SIGKILL on timeout, OOM, or manual restart), these objects
    remain in /dev/shm/. The next PI invocation reads the stale 'alive' flag and
    immediately exits with the crash message. Deleting the files clears this state.
    """
    import os, signal as _signal, glob as _glob
    try:
        result = subprocess.run(["pgrep", "-f", "PixInsight"], capture_output=True, text=True)
        if result.returncode == 0:
            pids = [int(p) for p in result.stdout.split() if p.strip()]
            for pid in pids:
                try:
                    os.kill(pid, _signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if pids:
                time.sleep(2)
                log.info(f"[pi] killed {len(pids)} lingering PI process(es) before new run")
    except Exception:
        pass

    # Clear PI crash-detection state: Qt shared memory and semaphore objects
    # that persist in /dev/shm/ after a SIGKILL or OOM kill.
    cleared = 0
    for pattern in ("/dev/shm/qipc_sharedmemory_PIXINSIGHT*",
                    "/dev/shm/sem.qipc_systemsem_PIXINSIGHT*"):
        for shm_path in _glob.glob(pattern):
            try:
                os.unlink(shm_path)
                log.info(f"[pi] cleared PI shared memory: {Path(shm_path).name}")
                cleared += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                log.warning(f"[pi] could not clear PI shared memory {shm_path}: {e}")

    # Always wait briefly so the kernel fully closes any IPC handles the previous
    # PI process had open.  Without this, the next PI launch sometimes sees a
    # stale "running instance" flag and exits in <5 s before running any script.
    time.sleep(1)


def _pi_preexec() -> None:
    """Raise the open-file-descriptor soft limit to the hard limit before exec'ing PI.

    PI opens one fd per input frame during ImageIntegration. With 9K+ frames the
    default 1024 soft limit is exhausted, causing GLib-ERROR 'Too many open files'.
    The hard limit on this system is 524288 — set the soft limit to match.
    """
    import resource as _resource
    try:
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (_hard, _hard))
    except Exception:
        pass


def _run_pi(script_path: str, job_path: str, timeout: int = 600,
            use_xvfb: bool = False,
            use_automation_mode: bool = True) -> tuple[bool, str]:
    """Run a PI JS script headlessly. Returns (ok, log_text).

    use_xvfb: wrap with xvfb-run for a real virtual X display (required for DI).
    use_automation_mode: pass --automation-mode to PI. Set False for DrizzleIntegration,
        which silently fails in automation mode (PI 1.9.3 confirmed limitation).
    """
    import os
    _kill_lingering_pi()
    Path("/tmp/pi_job_path.txt").write_text(job_path)

    env = os.environ.copy()
    env.update(PI_ENV_OVERLAY)

    pi_flags = ["--automation-mode"] if use_automation_mode else []
    pi_cmd = [PI_BIN] + pi_flags + ["-n", f"-r={script_path}", "--force-exit"]
    if use_xvfb or _PI_FORCE_XVFB:
        # Use a real virtual framebuffer instead of Qt offscreen
        env.pop("QT_QPA_PLATFORM", None)
        cmd = ["xvfb-run", "-a", "--server-args=-screen 0 1280x1024x24"] + pi_cmd
    else:
        cmd = pi_cmd
    log.info(f"[pi] running: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, preexec_fn=_pi_preexec)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        # Always write PI's raw stdout+stderr for debugging
        raw = f"exit_code={proc.returncode}\n---STDOUT---\n{stdout}\n---STDERR---\n{stderr}"
        Path("/tmp/pi_last_stdout_stderr.log").write_text(raw)
        if proc.returncode != 0 or stderr.strip():
            log.warning(f"[pi] exit={proc.returncode} stderr={stderr[:500].strip()!r}")
        log_path = Path("/tmp/pi_postprocess_last.log")
        log_text = log_path.read_text() if log_path.exists() else (stdout + stderr)
        return True, log_text
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()  # drain buffers and wait for full process death
        time.sleep(2)       # extra margin for PI lock file cleanup by kernel
        return False, "PI subprocess timed out"
    except Exception as e:
        try:
            proc.kill()
            proc.communicate()
        except Exception:
            pass
        return False, str(e)


def run_postprocess(
    target: str,
    input_fits: str,
    output_path: str | None = None,
    # ── Background extraction ────────────────────────────────────────────────
    dbe: bool = False,
    dbe_correction: str = "subtraction",   # "subtraction"|"division"
    gradient_correction: bool = True,      # skipped automatically if dbe=True
    # ── Color calibration ───────────────────────────────────────────────────
    color_calibration: bool = True,
    bgn: bool = False,                     # BackgroundNeutralization
    spcc: bool = False,                    # SpectrophotometricColorCalibration
    spcc_lp_filter: bool = False,         # True when LP filter was used during capture
    # ── Linear sharpening / denoising ───────────────────────────────────────
    nbn: bool = False,                     # NarrowbandNormalization (duo-band Ha/OIII)
    nbn_method: str = "MaximumStars",      # "MaximumStars" | "Equalize" (PI 1.9.3 no-op — kept for logging)
    nbn_o3_boost: float = 1.0,             # PI o3Boost on HOO palette — OIII vibrancy lever
    nbn_hoo_boost: float = 0.0,            # in-JS post-NBN ColorSaturation pass (0=off)
    mlt: bool = True,                      # MultiscaleLinearTransform (CPU)
    mlt_sharpen: float = 0.20,
    mlt_denoise: float = 0.50,
    mlt_layers: int = 4,
    tgv: bool = True,                      # TGVDenoise (CPU, no GPU needed)
    tgv_strength: float = 1.0,
    tgv_edge: float = 0.001,
    tgv_iterations: int = 100,
    # ── AI tools — RC-Astro plugins, CPU TF models confirmed working (2026-04-24)
    bxt: bool = True,                      # BlurXTerminator 2.0.4 (CPU confirmed)
    bxt_psf: float = 4.0,
    bxt_nonstellar: float = 0.30,
    bxt_stars: float = 0.50,
    bxt_auto_psf: bool = True,             # default auto PSF (matches GUI default)
    bxt_adjust_halos: float = 0.0,
    bxt_correct_only: bool = False,        # only correct star shapes, no sharpening/deblur
    nxt: bool = True,                      # NoiseXTerminator 2.3.3 (CPU confirmed)
    nxt_denoise: float = 0.70,
    nxt_iterations: int = 2,               # NXT iterations (not "detail" — confirmed from GUI)
    starxt: bool = False,                  # StarXTerminator
    starxt_stars_output: str | None = None,  # path to save stars-only FITS (enables stars=true)
    # ── Stretch ─────────────────────────────────────────────────────────────
    mas: bool = False,                     # MultiscaleAdaptiveStretch (PI native, self-calibrating)
    mas_noise_threshold: float = -1,       # -1 = use PI default; >0 overrides noise/clipping floor
    ht: bool = False,                      # HistogramTransformation auto-stretch
    ht_clip_low: float = 0.0,
    ht_target_bg: float = 0.12,
    # ── Non-linear ──────────────────────────────────────────────────────────
    scnr: bool = False,                    # SCNR green removal
    scnr_amount: float = 0.9,
    hdrmt: bool = False,                   # HDRMultiscaleTransform
    hdrmt_layers: int = 6,
    hdrmt_iterations: int = 3,
    hdrmt_overdrive: float = 0.0,
    lhe: bool = False,                     # LocalHistogramEqualization
    lhe_amount: float = 0.5,
    lhe_kernel_r: int = 64,
    lhe_slope_limit: float = 2.0,
    bg_anchor: bool = True,               # Re-anchor background after HDRMT/LHE
    bg_anchor_target: float | None = None, # None = auto-detect from post-stretch sample
    color_sat: bool = False,               # ColorSaturation boost
    color_sat_boost: float = 0.3,
    sat_preset: str | None = None,         # "galaxy"|"nebula"|"uniform" (overrides uniform HS curve)
    curves: bool = False,                  # CurvesTransformation
    curves_shape: str = "s_med",
    curves_points: list | None = None,     # data-driven control points [[in,out],...] — overrides curves_shape
    usm: bool = False,                     # UnsharpMask (post-stretch)
    usm_sigma: float = 2.0,
    usm_amount: float = 0.7,
    usm_threshold: float = 0.02,
    cms: bool = True,                      # CorrectMagentaStars — OSC pink star cores
    morph: bool = False,                   # MorphologicalTransformation star shrink
    morph_amount: float = 0.3,
    morph_iterations: int = 2,
    # ── iHDR (second PI pass — Uri Darom's multiscale HDR script) ────────────
    ihdr: bool = False,
    ihdr_iterations: int = 5,
    ihdr_preservation: int = 5,
    ihdr_mask_strength: float = 1.25,
    # ── Python-side pre-processing ───────────────────────────────────────────
    adbe: bool = False,                    # SASpro ADBE before PI
    adbe_degree: int = 2,
    adbe_num_samples: int = 100,
    adbe_use_rbf: bool = True,
    adbe_rbf_smooth: float = 0.1,
    # ── Luminance masks (non-linear steps) ───────────────────────────────────
    lum_masks: dict | None = None,   # {fn_name: {lower, upper, fuzziness, blur}}
    # ── Catalog paths (worker-node overrides) ────────────────────────────────
    gaia_db_path: str | None = None,  # Override GAIA DR3 SP catalog dir (remote workers)
    # ── Misc ─────────────────────────────────────────────────────────────────
    timeout: int = 900,
) -> dict:
    """
    Run the PI post-processing pipeline on a FITS file.

    Linear stage:   DBE|GC → CC → BGN → SPCC → MLT → TGV → BXT → NXT
    Stretch:        HT (optional auto-stretch)
    Non-linear:     StarXT → SCNR → HDRMT → LHE → ColorSat → Curves
    Save:           XISF

    Returns:
        {"ok": bool, "output_path": str|None, "steps": [...],
         "elapsed": float, "log": str, <step>_failed: bool, ...}
    """
    from nas_server.database import log_processing_step

    start = time.time()
    input_path = Path(input_fits)

    if not input_path.exists():
        return {"ok": False, "error": f"Input not found: {input_fits}"}

    if output_path is None:
        output_path = str(input_path.parent / "pi_processed.xisf")

    # ── Optional SASpro ADBE pre-pass ────────────────────────────────────────
    adbe_applied = False
    adbe_failed  = False
    gc_forced_off = False
    if adbe:
        from nas_server import seti_astro
        adbe_out = input_path.parent / (input_path.stem + "_adbe.fit")
        log.info(f"[pi] {target}: running ADBE (degree={adbe_degree} rbf={adbe_use_rbf})")
        adbe_result = seti_astro.adbe(
            input_path, adbe_out,
            degree=adbe_degree,
            num_samples=adbe_num_samples,
            use_rbf=adbe_use_rbf,
            rbf_smooth=adbe_rbf_smooth,
        )
        if adbe_result.get("ok") and adbe_out.exists():
            input_path = adbe_out
            gradient_correction = False
            dbe = False
            gc_forced_off = True
            adbe_applied = True
            log.info(f"[pi] {target}: ADBE done — using {adbe_out.name}")
        else:
            adbe_failed = True
            log.warning(f"[pi] {target}: ADBE failed — continuing with original input")

    # ── Build job dict ────────────────────────────────────────────────────────
    job = {
        "input":              str(input_path),
        "output":             output_path,
        # background
        "dbe":                dbe,
        "dbe_correction":     dbe_correction,
        "gradient_correction": gradient_correction,
        # color
        "color_calibration":  color_calibration,
        "bgn":                bgn,
        "spcc":               spcc,
        "spcc_lp_filter":     spcc_lp_filter,
        "gaia_db_path":       gaia_db_path,   # None on VM (uses PI prefs); set on laptop worker
        "nbn":                nbn,
        "nbn_method":         nbn_method,
        "nbn_o3_boost":       nbn_o3_boost,
        "nbn_hoo_boost":      nbn_hoo_boost,
        # linear sharp/denoise
        "mlt":                mlt,
        "mlt_sharpen":        mlt_sharpen,
        "mlt_denoise":        mlt_denoise,
        "mlt_layers":         mlt_layers,
        "tgv":                tgv,
        "tgv_strength":       tgv_strength,
        "tgv_edge":           tgv_edge,
        "tgv_iterations":     tgv_iterations,
        # AI plugins
        "bxt":                bxt,
        "bxt_psf":            bxt_psf,
        "bxt_nonstellar":     bxt_nonstellar,
        "bxt_stars":          bxt_stars,
        "bxt_auto_psf":       bxt_auto_psf,
        "bxt_adjust_halos":   bxt_adjust_halos,
        "bxt_correct_only":   bxt_correct_only,
        "nxt":                nxt,
        "nxt_denoise":        nxt_denoise,
        "nxt_iterations":     nxt_iterations,
        "starxt":             starxt,
        "starxt_stars_output": starxt_stars_output,
        "mas":                mas,
        "mas_noise_threshold": mas_noise_threshold,
        # stretch
        "ht":                 ht,
        "ht_clip_low":        ht_clip_low,
        "ht_target_bg":       ht_target_bg,
        # non-linear
        "scnr":               scnr,
        "scnr_amount":        scnr_amount,
        "hdrmt":              hdrmt,
        "hdrmt_layers":       hdrmt_layers,
        "hdrmt_iterations":   hdrmt_iterations,
        "hdrmt_overdrive":    hdrmt_overdrive,
        "lhe":                lhe,
        "lhe_amount":         lhe_amount,
        "lhe_kernel_r":       lhe_kernel_r,
        "lhe_slope_limit":    lhe_slope_limit,
        "bg_anchor":          bg_anchor,
        "bg_anchor_target":   bg_anchor_target,
        "color_sat":          color_sat,
        "color_sat_boost":    color_sat_boost,
        "sat_preset":         sat_preset or "uniform",
        "curves":             curves,
        "curves_shape":       curves_shape,
        "curves_points":      curves_points,
        "usm":                usm,
        "usm_sigma":          usm_sigma,
        "usm_amount":         usm_amount,
        "usm_threshold":      usm_threshold,
        "cms":                cms,
        "morph":              morph,
        "morph_amount":       morph_amount,
        "morph_iterations":   morph_iterations,
        "lum_masks":          lum_masks or {},
    }

    proc_dir = Path(input_fits).parent
    job_path = str(proc_dir / "pi_job.json")
    Path(job_path).write_text(json.dumps(job))

    enabled = [k for k, v in job.items()
               if isinstance(v, bool) and v and k not in ("bxt_auto_psf", "bxt_adjust_halos")]
    log.info(f"[pi] {target}: enabled={enabled} → {output_path}")

    ok, log_text = _run_pi(PI_JS, job_path, timeout=timeout)
    _cleanup_pi_workspace(str(Path(input_fits).parent))

    # Re-read job file (PI writes per-step status back)
    result_job = job.copy()
    try:
        result_job = json.loads(Path(job_path).read_text())
    except Exception:
        pass

    Path(job_path).unlink(missing_ok=True)

    elapsed = time.time() - start
    output_exists = Path(output_path).exists()

    _fail = lambda key: result_job.get(key, False)

    steps = []
    if adbe_applied:                             steps.append("adbe")
    if dbe and not _fail("dbe_failed"):          steps.append("dbe")
    if gradient_correction and not _fail("gc_failed"):  steps.append("gradient_correction")
    if color_calibration and not _fail("cc_failed"):    steps.append("color_calibration")
    if bgn and not _fail("bgn_failed"):          steps.append("bgn")
    if spcc and not _fail("spcc_failed"):        steps.append("spcc")
    if nbn and not _fail("nbn_failed"):          steps.append("nbn")
    if mlt and not _fail("mlt_failed"):          steps.append("mlt")
    if tgv and not _fail("tgv_failed"):          steps.append("tgv")
    if bxt and not _fail("bxt_failed"):          steps.append("bxt")
    if nxt and not _fail("nxt_failed"):          steps.append("nxt")
    if ht and not _fail("ht_failed"):            steps.append("ht")
    if starxt and not _fail("starxt_failed"):    steps.append("starxt")
    if scnr and not _fail("scnr_failed"):        steps.append("scnr")
    if hdrmt and not _fail("hdrmt_failed"):      steps.append("hdrmt")
    if lhe and not _fail("lhe_failed"):          steps.append("lhe")
    if color_sat and not _fail("color_sat_failed"):     steps.append("color_sat")
    if curves and not _fail("curves_failed"):    steps.append(f"curves[{curves_shape}]")
    if usm and not _fail("usm_failed"):          steps.append("usm")
    if cms and not _fail("cms_failed"):          steps.append("cms")
    if morph and not _fail("morph_failed"):      steps.append("morph")

    # ── iHDR second pass (separate PI invocation to avoid script conflicts) ──
    ihdr_failed = False
    ihdr_log = ""
    if ok and output_exists and ihdr:
        log.info(f"[pi] {target}: running iHDR second pass (iter={ihdr_iterations})")
        ihdr_job = {
            "input":               output_path,
            "output":              output_path,   # overwrite in-place
            "ihdr_iterations":     ihdr_iterations,
            "ihdr_preservation":   ihdr_preservation,
            "ihdr_mask_strength":  ihdr_mask_strength,
        }
        Path(job_path).write_text(json.dumps(ihdr_job))
        ihdr_ok, ihdr_log = _run_pi(PI_IHDR_JS, job_path, timeout=300)
        try:
            ihdr_result = json.loads(Path(job_path).read_text())
            if not ihdr_result.get("ihdr_output_exists") or not ihdr_ok:
                ihdr_failed = True
        except Exception:
            ihdr_failed = not ihdr_ok
        Path(job_path).unlink(missing_ok=True)
        if not ihdr_failed:
            steps.append("ihdr")
            log.info(f"[pi] {target}: iHDR done")
        else:
            log.warning(f"[pi] {target}: iHDR failed")

    success = ok and output_exists

    if success:
        log_processing_step(
            target,
            step="pixinsight:" + "+".join(steps),
            engine="pixinsight",
            params={k: v for k, v in job.items() if isinstance(v, bool)},
            elapsed_s=round(elapsed, 1),
        )
        log.info(f"[pi] {target}: done in {elapsed:.0f}s — steps: {steps}")
    else:
        log.warning(f"[pi] {target}: pipeline failed after {elapsed:.0f}s")
        log.debug(f"[pi] log:\n{log_text[-3000:]}")

    return {
        "ok":               success,
        "output_path":      output_path if output_exists else None,
        "steps":            steps,
        "elapsed":          round(elapsed, 1),
        "log":              log_text[-4000:],
        "adbe_applied":     adbe_applied,
        "adbe_failed":      adbe_failed,
        "dbe_failed":       _fail("dbe_failed"),
        "gc_failed":        _fail("gc_failed"),
        "cc_failed":        _fail("cc_failed"),
        "bgn_failed":       _fail("bgn_failed"),
        "spcc_failed":      _fail("spcc_failed"),
        "nbn_failed":       _fail("nbn_failed"),
        "mlt_failed":       _fail("mlt_failed"),
        "tgv_failed":       _fail("tgv_failed"),
        "bxt_failed":       _fail("bxt_failed"),
        "nxt_failed":       _fail("nxt_failed"),
        "ht_failed":        _fail("ht_failed"),
        "starxt_failed":    _fail("starxt_failed"),
        "scnr_failed":      _fail("scnr_failed"),
        "hdrmt_failed":     _fail("hdrmt_failed"),
        "lhe_failed":       _fail("lhe_failed"),
        "color_sat_failed": _fail("color_sat_failed"),
        "curves_failed":    _fail("curves_failed"),
        "usm_failed":       _fail("usm_failed"),
        "cms_failed":       _fail("cms_failed"),
        "morph_failed":     _fail("morph_failed"),
        "ihdr_failed":      ihdr_failed,
    }


def run_stack(
    input_files: list[str],
    output_path: str,
    rejection: str = "winsorized",
    sigma_low: float = 3.0,
    sigma_high: float = 3.0,
    weight_mode: str = "snr",
    weight_scale: str = "avgdev",
    normalization: str = "additive_scaling",
    evaluate_snr: bool = False,
    timeout: int = 3600,
) -> dict:
    """
    Run PI ImageIntegration on a list of registered frames.

    weight_scale: "ikss" (most accurate, slowest) | "avgdev" (fast, good for consistent data)
                  | "mad" | "bwmv" — controls how SNR weights are computed per frame.
    normalization: "additive_scaling" (default, handles varying sky) | "additive" (faster,
                   single-session data with consistent background)

    Returns {"ok": bool, "frames_used": int, "log": str, "elapsed": float}
    """
    import tempfile, time as _time

    job = {
        "input_files":   input_files,
        "output_path":   output_path,
        "rejection":     rejection,
        "sigma_low":     sigma_low,
        "sigma_high":    sigma_high,
        "weight_mode":   weight_mode,
        "weight_scale":  weight_scale,
        "normalization": normalization,
        "evaluate_snr":  evaluate_snr,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_stack.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    t0 = _time.time()
    log.info(f"[pi] stack: {len(input_files)} frames → {output_path}")
    pi_ok, log_text = _run_pi(PI_STACK_JS, job_path, timeout=timeout)
    elapsed = _time.time() - t0
    _cleanup_pi_workspace(str(Path(output_path).parent))

    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}

    Path(job_path).unlink(missing_ok=True)

    # Read stack-specific log
    stack_log = Path("/tmp/pi_stack_last.log")
    if stack_log.exists():
        log_text = stack_log.read_text()

    ok = pi_ok and result.get("stack_ok", False) and Path(output_path).exists()
    log.info(f"[pi] stack done in {elapsed:.0f}s — ok={ok}")

    return {
        "ok":         ok,
        "frames_used": result.get("frames_used", len(input_files)),
        "log":        log_text[-3000:],
        "elapsed":    elapsed,
    }


def run_solve(
    input_fits: str,
    ra_hint: float | None = None,
    dec_hint: float | None = None,
    search_radius: float = 5.0,
    timeout: int = 300,
) -> dict:
    """
    Plate-solve a FITS file using PI ImageSolver + local GAIA DR3.
    WCS is written directly into the FITS file header.

    Returns {"ok": bool, "ra_solved": float, "dec_solved": float,
             "resolution_arcsec": float, "error": str|None, "elapsed": float}
    """
    import tempfile
    import time as _time

    job = {
        "input_fits":    input_fits,
        "ra_hint":       ra_hint,
        "dec_hint":      dec_hint,
        "search_radius": search_radius,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_solve.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    t0 = _time.time()
    log.info(f"[pi] solve: {input_fits}")
    pi_ok, log_text = _run_pi(PI_SOLVE_JS, job_path, timeout=timeout)
    elapsed = _time.time() - t0

    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}

    Path(job_path).unlink(missing_ok=True)

    # Read dedicated solve log if available
    solve_log = Path("/tmp/pi_solve_last.log")
    if solve_log.exists():
        log_text = solve_log.read_text()

    ok = pi_ok and result.get("ok", False)
    log.info(f"[pi] solve done in {elapsed:.0f}s — ok={ok}")

    return {
        "ok":               ok,
        "flipped":          result.get("flipped", False),
        "ra_solved":        result.get("ra_solved"),
        "dec_solved":       result.get("dec_solved"),
        "resolution_arcsec": result.get("resolution_arcsec"),
        "error":            result.get("error"),
        "log":              log_text[-2000:],
        "elapsed":          elapsed,
    }


def run_register(
    input_files: list[str],
    output_dir: str,
    timeout: int = 3600,
) -> dict:
    """
    Run PI StarAlignment with generateDrizzleData=true.

    Produces registered _r.xisf + _r.xdrz sidecar files in output_dir.
    Input frames must be raw CFA (not debayered) — same constraint as Siril drizzle.

    Returns {"ok": bool, "frames_registered": int, "log": str, "elapsed": float}
    """
    import tempfile
    import time as _time

    job = {
        "input_files": input_files,
        "output_dir":  output_dir,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_register.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    t0 = _time.time()
    log.info(f"[pi] register: {len(input_files)} CFA frames → {output_dir}")
    pi_ok, log_text = _run_pi(PI_REGISTER_JS, job_path, timeout=timeout)
    elapsed = _time.time() - t0

    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}

    Path(job_path).unlink(missing_ok=True)

    reg_log = Path("/tmp/pi_register_last.log")
    if reg_log.exists():
        log_text = reg_log.read_text()

    ok = pi_ok and result.get("ok", False)
    log.info(f"[pi] register done in {elapsed:.0f}s — ok={ok} "
             f"frames_registered={result.get('frames_registered', '?')}")

    return {
        "ok":                ok,
        "frames_registered": result.get("frames_registered", 0),
        "log":               log_text[-3000:],
        "elapsed":           elapsed,
    }


def run_drizzle(
    xdrz_files: list[str],
    output_xisf: str,
    timeout: int = 7200,
) -> dict:
    """
    Run PI DrizzleIntegration on .xdrz sidecar files from run_register().

    scale=2, dropShrink=1.0 (CFA/Bayer default), kernelFunction=Square,
    Winsorized rejection. Output is XISF — caller must convert to FITS.

    Returns {"ok": bool, "frames_used": int, "output_xisf": str|None,
             "log": str, "elapsed": float}
    """
    import tempfile
    import time as _time

    job = {
        "xdrz_files":  xdrz_files,
        "output_xisf": output_xisf,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_drizzle.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    t0 = _time.time()
    log.info(f"[pi] drizzle: {len(xdrz_files)} .xdrz files → {output_xisf}")
    pi_ok, log_text = _run_pi(PI_DRIZZLE_JS, job_path, timeout=timeout)
    elapsed = _time.time() - t0

    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}

    Path(job_path).unlink(missing_ok=True)

    drizzle_log = Path("/tmp/pi_drizzle_last.log")
    if drizzle_log.exists():
        log_text = drizzle_log.read_text()

    _cleanup_pi_workspace(str(Path(output_xisf).parent))

    actual_xisf = result.get("output_xisf") or output_xisf
    ok = pi_ok and result.get("ok", False) and Path(actual_xisf).exists()
    log.info(f"[pi] drizzle done in {elapsed:.0f}s — ok={ok}")

    return {
        "ok":          ok,
        "frames_used": result.get("frames_used", len(xdrz_files)),
        "output_xisf": actual_xisf if ok else None,
        "log":         log_text[-3000:],
        "elapsed":     elapsed,
    }


def run_register_and_drizzle(
    input_files: list[str],
    output_dir: str,
    output_xisf: str,
    timeout: int = 10800,
    reference_image: str | None = None,
    drizzle: bool = True,
) -> dict:
    """
    Run Debayer + StarAlignment + DrizzleIntegration in a single PI session.

    SA and DI must share the same PI process — splitting them across two
    separate headless invocations causes DI to fail silently (session-specific
    xdrz state is lost between PI processes).

    Returns {"ok": bool, "frames_registered": int, "frames_used": int,
             "output_xisf": str|None, "log": str, "elapsed": float}
    """
    import tempfile
    import time as _time

    # ImageIntegration peak RAM ≈ baseline + canvas_Mpx × ~10.7 MB × N_frames (one resident
    # RGB-float frame per sub). The bufferSizeMB/stackSizeMB knobs are inert under PI's
    # required --automation-mode, so memory is bounded by the frame-count cap in stacker.py,
    # not tuned here. No memory params threaded to the JS.
    job = {
        "input_files":  input_files,
        "output_dir":   output_dir,
        "output_xisf":  output_xisf,
        "drizzle":      bool(drizzle),
        "n_threads":    _os.cpu_count() or 4,
    }
    if reference_image:
        job["reference_image"] = reference_image

    with tempfile.NamedTemporaryFile(mode="w", suffix="_pi_rad.json",
                                     delete=False, prefix="/tmp/") as tf:
        json.dump(job, tf, indent=2)
        job_path = tf.name

    t0 = _time.time()
    log.info(f"[pi] register+drizzle: {len(input_files)} CFA frames → {output_xisf}")
    # Automation mode: PI hangs at boot WITHOUT --automation-mode on this VM
    # (a startup dialog blocks on the virtual display). DrizzleIntegration works
    # in automation mode when the result window is retrieved via
    # DI.integrationImageId (the old "DI fails in automation mode" note predates
    # that fix — the real failure was a guessed window id returning null).
    pi_ok, log_text = _run_pi(PI_REGISTER_AND_DRIZZLE_JS, job_path, timeout=timeout,
                               use_xvfb=True, use_automation_mode=True)
    elapsed = _time.time() - t0

    try:
        result = json.loads(Path(job_path).read_text())
    except Exception:
        result = {}

    Path(job_path).unlink(missing_ok=True)

    rad_log = Path("/tmp/pi_register_drizzle_last.log")
    if rad_log.exists():
        log_text = rad_log.read_text()

    _cleanup_pi_workspace(str(Path(output_xisf).parent))

    actual_xisf = result.get("output_xisf") or output_xisf
    ok = pi_ok and result.get("ok", False) and Path(actual_xisf).exists()
    log.info(f"[pi] register+drizzle done in {elapsed:.0f}s — ok={ok} "
             f"registered={result.get('frames_registered', 0)}")

    return {
        "ok":               ok,
        "frames_registered": result.get("frames_registered", 0),
        "frames_used":      result.get("frames_used", 0),
        "output_xisf":      actual_xisf if ok else None,
        "log":              log_text[-5000:],
        "elapsed":          elapsed,
    }

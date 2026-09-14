"""Pure command and path contract for the NOVA ML-tools RunPod worker.

The worker intentionally shells out to the public tools' supported CLIs.  It
does not download models, accept arbitrary commands, or permit paths outside
the mounted RunPod network volume.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


VOLUME = Path("/runpod-volume")
SUPPORTED_OPERATIONS = frozenset(
    {
        "cosmic_sharpen",
        "cosmic_correct",
        "cosmic_denoise",
        "cosmic_both",
        "cosmic_satellite",
        "darkstar",
        "graxpert_subtraction",
        "graxpert_division",
        "graxpert_denoise",
        "syqon_parallax_correct",
        "syqon_parallax_sharpen",
        "syqon_parallax_star_reduce",
    }
)
MODEL_ROOT = VOLUME / "ml-models"
MODEL_MANIFEST = MODEL_ROOT / "worker-model-manifest.json"
PARALLAX_MODELS = {
    "natural": {
        "syqon_parallax_correct": MODEL_ROOT / "syqon-parallax/natural/correction.pth",
        "syqon_parallax_sharpen": MODEL_ROOT / "syqon-parallax/natural/sharpen.pth",
        "syqon_parallax_star_reduce": MODEL_ROOT / "syqon-parallax/natural/star-reduction.pth",
    },
    "defined": {
        "syqon_parallax_correct": MODEL_ROOT / "syqon-parallax/defined/correction.pt",
        "syqon_parallax_sharpen": MODEL_ROOT / "syqon-parallax/defined/sharpen.pt",
        "syqon_parallax_star_reduce": MODEL_ROOT / "syqon-parallax/defined/star-reduction.pt",
    },
}
_SASPRO_MODELS = Path("saspro/runtime/py312/models")
# Matches GraXpert's own lookup path under XDG_DATA_HOME (see Dockerfile's
# XDG_DATA_HOME=/runpod-volume/ml-models/xdg), not an arbitrary layout choice.
_GRAXPERT_MODELS = Path("xdg/GraXpert")
REQUIRED_MODEL_PATHS = {
    # Without these, validate_model_manifest("graxpert_denoise") only checks
    # that *some* correctly-hashed file exists somewhere the manifest claims
    # -- it never confirms that path is the one GraXpert's CLI actually reads
    # from. Confirmed live earlier this session: a manifest entry pointing at
    # the wrong (pre-XDG-fix) location still validated as "available" while
    # GraXpert silently re-downloaded its real model at runtime. These
    # entries close that gap the same way the SASpro operations below do.
    "graxpert_subtraction": {_GRAXPERT_MODELS / "bge-ai-models/1.0.1/model.onnx"},
    "graxpert_division": {_GRAXPERT_MODELS / "bge-ai-models/1.0.1/model.onnx"},
    "graxpert_denoise": {_GRAXPERT_MODELS / "denoise-ai-models/3.0.2/model.onnx"},
    "cosmic_sharpen": {
        _SASPRO_MODELS / "deep_sharp_stellar_AI4.pth",
        _SASPRO_MODELS / "deep_nonstellar_sharp_conditional_psf_AI4.pth",
        _SASPRO_MODELS / "deep_correct_stellar_V2_AI4.pth",
    },
    "cosmic_correct": {_SASPRO_MODELS / "deep_correct_stellar_V2_AI4.pth"},
    "cosmic_denoise": {
        _SASPRO_MODELS / f"deep_denoise_{channel}_{suffix}.pth"
        for channel in ("mono", "color")
        for suffix in ("AI4", "AI4_lite", "AI4_1w")
    },
    "cosmic_satellite": {
        # Confirmed live: satellite detection loads the .onnx variants
        # specifically (resources.CC_SAT_DETECT1_ONNX), not the .pth ones --
        # FileNotFoundError before these were provisioned. Keeping the .pth
        # requirement too since it's unconfirmed whether another code path
        # (e.g. removal) still needs it; no evidence yet that it doesn't.
        _SASPRO_MODELS / "satellite_trail_detector_AI3.5.pth",
        _SASPRO_MODELS / "satellite_trail_detector_mobilenetv2.5.pth",
        _SASPRO_MODELS / "satelliteRemovalAI4.pth",
        _SASPRO_MODELS / "satellite_trail_detector_AI3.5.onnx",
        _SASPRO_MODELS / "satellite_trail_detector_mobilenetv2.5.onnx",
        _SASPRO_MODELS / "satelliteRemovalAI4.onnx",
    },
    "darkstar": {
        # _resolve_darkstar_model_paths() returns (mono_pt, mono_onnx,
        # color_pt, color_onnx) -- confirmed live: FileNotFoundError for
        # darkstar_mono_AI4.onnx before this was provisioned. Same pattern
        # as cosmic_satellite's ONNX requirement above.
        _SASPRO_MODELS / "darkstar_mono_AI4.pt",
        _SASPRO_MODELS / "darkstar_color_AI4.pt",
        _SASPRO_MODELS / "darkstar_mono_AI4.onnx",
        _SASPRO_MODELS / "darkstar_color_AI4.onnx",
    },
}
REQUIRED_MODEL_PATHS["cosmic_both"] = (
    REQUIRED_MODEL_PATHS["cosmic_sharpen"] | REQUIRED_MODEL_PATHS["cosmic_denoise"]
)
for _operation in PARALLAX_MODELS["natural"]:
    REQUIRED_MODEL_PATHS[_operation] = {
        path.relative_to(MODEL_ROOT)
        for mode in PARALLAX_MODELS.values()
        for op, path in mode.items()
        if op == _operation
    }


@dataclass(frozen=True)
class OperationCommand:
    argv: tuple[str, ...]
    generated_output: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_manifest(operation: str, *, manifest_path: Path = MODEL_MANIFEST,
                            model_root: Path = MODEL_ROOT) -> dict:
    """Validate operation-specific model evidence before paid execution."""
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported ML operation: {operation}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"model manifest unavailable: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise RuntimeError("unsupported model manifest schema")
    entry = (manifest.get("operations") or {}).get(operation)
    if not isinstance(entry, dict) or not entry.get("available"):
        raise RuntimeError(f"models not provisioned for {operation}")
    evidence = []
    recorded_paths = set()
    root = model_root.resolve()
    for item in entry.get("models") or []:
        relative = Path(str(item.get("path", "")))
        path = (root / relative).resolve()
        if relative.is_absolute() or (path != root and root not in path.parents):
            raise RuntimeError(f"model path escapes model root: {relative}")
        expected_hash = str(item.get("sha256", ""))
        expected_bytes = int(item.get("bytes", 0))
        if len(expected_hash) != 64 or expected_bytes <= 0:
            raise RuntimeError(f"incomplete model evidence for {relative}")
        if not path.is_file() or path.stat().st_size != expected_bytes:
            raise RuntimeError(f"model missing or size mismatch: {relative}")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise RuntimeError(f"model hash mismatch: {relative}")
        evidence.append({"path": str(relative), "bytes": expected_bytes,
                         "sha256": actual_hash})
        recorded_paths.add(relative)
    if not evidence:
        raise RuntimeError(f"no model evidence recorded for {operation}")
    missing = REQUIRED_MODEL_PATHS.get(operation, set()) - recorded_paths
    if missing:
        names = ", ".join(sorted(str(path) for path in missing))
        raise RuntimeError(f"required models absent from manifest for {operation}: {names}")
    return {"operation": operation, "models": evidence}


def volume_path(relative: str, *, volume: Path = VOLUME) -> Path:
    """Resolve one relative volume path and reject traversal/absolute input."""
    candidate = Path(relative)
    if candidate.is_absolute() or not relative:
        raise ValueError("worker paths must be non-empty and relative to the volume")
    root = volume.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes mounted volume: {relative}")
    return resolved


def _float(params: dict, name: str, default: float, lo: float, hi: float) -> float:
    value = float(params.get(name, default))
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return value


def _int_choice(params: dict, name: str, default: int, choices: set[int]) -> int:
    value = int(params.get(name, default))
    if value not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}")
    return value


def _cosmic_command(operation: str, input_path: Path, output_path: Path,
                    params: dict) -> OperationCommand:
    mode = operation.removeprefix("cosmic_")
    cmd = ["cosmicclarity", "cc", mode, "-i", str(input_path), "-o", str(output_path)]
    cmd.extend(("--chunk-size", str(_int_choice(params, "chunk_size", 256, {128, 256, 384, 512}))))
    cmd.extend(("--overlap", str(_int_choice(params, "overlap", 64, {0, 32, 64, 96, 128}))))

    if mode in {"sharpen", "both"}:
        sharpen_mode = str(params.get("sharpening_mode", "Both"))
        if sharpen_mode not in {"Both", "Stellar Only", "Non-Stellar Only"}:
            raise ValueError("invalid sharpening_mode")
        cmd.extend(("--sharpening-mode", sharpen_mode))
        cmd.extend(("--stellar-amount", str(_float(params, "stellar_amount", 0.5, 0, 1))))
        cmd.extend(("--nonstellar-amount", str(_float(params, "nonstellar_amount", 0.5, 0, 1))))
        cmd.extend(("--nonstellar-psf", str(_float(params, "nonstellar_psf", 3.0, 0.5, 12))))
        correct_mode = str(params.get("stellar_correct_mode", "sharpen_only"))
        if correct_mode not in {"sharpen_only", "correct_only", "correct_sharpen"}:
            raise ValueError("invalid stellar_correct_mode")
        cmd.extend(("--stellar-correct-mode", correct_mode))
        if params.get("auto_psf", True) is False:
            cmd.append("--no-auto-psf")
    if mode in {"denoise", "both"}:
        cmd.extend(("--denoise-luma", str(_float(params, "denoise_luma", 0.5, 0, 1))))
        cmd.extend(("--denoise-color", str(_float(params, "denoise_color", 0.5, 0, 1))))
        denoise_model = str(params.get("denoise_model", "standard"))
        if denoise_model not in {"standard", "lite", "walking"}:
            raise ValueError("denoise_model must be standard, lite, or walking")
        if denoise_model != "standard":
            cmd.append("--denoise-lite" if denoise_model == "lite" else "--denoise-walking")
        denoise_mode = str(params.get("denoise_mode", "full"))
        if denoise_mode not in {"full", "luminance"}:
            raise ValueError("denoise_mode must be full or luminance")
        cmd.extend(("--denoise-mode", denoise_mode))
        if params.get("separate_channels", False) is True:
            cmd.append("--separate-channels")
    if mode == "satellite":
        cmd.extend(("--sensitivity", str(_float(params, "sensitivity", 0.1, 0, 1))))
        satellite_mode = str(params.get("mode", "full"))
        if satellite_mode not in {"full", "luminance"}:
            raise ValueError("satellite mode must be full or luminance")
        cmd.extend(("--mode", satellite_mode))
        if params.get("clip_trail", True) is False:
            cmd.append("--no-clip-trail")
    # SASpro's own save_image() strips whatever extension it's given and
    # always appends ".fits" for FITS output (confirmed at the source:
    # legacy/image_manager.py's save_image(), base, _ = splitext(filename);
    # filename = f"{base}.fits") -- it does NOT preserve the requested -o
    # extension. handler.py names outputs after the *input* file's suffix,
    # so any input that isn't already ".fits" (e.g. the common ".fit")
    # silently diverges from what the CLI actually writes. Confirmed live:
    # a job reported success pointing at a "cosmic_correct.fit" that never
    # existed, while "cosmic_correct.fits" (the real file) sat next to it.
    return OperationCommand(tuple(cmd), output_path.with_suffix(".fits"))


def _darkstar_command(input_path: Path, output_path: Path,
                      params: dict) -> OperationCommand:
    mode = str(params.get("mode", "unscreen"))
    if mode not in {"unscreen", "additive"}:
        raise ValueError("darkstar mode must be unscreen or additive")
    path = str(params.get("processing_path", "hybrid_luma_color"))
    if path not in {"mono_per_channel", "hybrid_luma_color", "color_only"}:
        raise ValueError("invalid DarkStar processing_path")
    cmd = (
        "cosmicclarity", "cc", "darkstar", "-i", str(input_path), "-o", str(output_path),
        "--star-removal-mode", mode, "--processing-path", path,
        "--chunk-size", str(_int_choice(params, "chunk_size", 512, {256, 384, 512, 768, 1024})),
    )
    # Same cosmicclarity CLI / save_image() as _cosmic_command above -- always
    # writes ".fits" regardless of the requested -o extension.
    return OperationCommand(cmd, output_path.with_suffix(".fits"))


def _graxpert_command(operation: str, input_path: Path, output_path: Path,
                      params: dict) -> OperationCommand:
    cmd = ["GraXpert", str(input_path), "-cli", "-gpu", "true"]
    if operation == "graxpert_denoise":
        cmd.extend(("-cmd", "denoising"))
        cmd.extend(("-strength", str(_float(params, "strength", 0.5, 0, 1))))
        cmd.extend(("-batch_size", str(_int_choice(params, "batch_size", 4, {1, 2, 4, 8, 16, 32}))))
    else:
        correction = "Subtraction" if operation.endswith("subtraction") else "Division"
        cmd.extend(("-cmd", "background-extraction", "-correction", correction))
        cmd.extend(("-smoothing", str(_float(params, "smoothing", 0.5, 0, 1))))
    # GraXpert 3.0.2's reliable CLI contract writes this default-named file.
    generated = input_path.with_name(f"{input_path.stem}_GraXpert.fits")
    return OperationCommand(tuple(cmd), generated)


def _parallax_command(operation: str, input_path: Path, output_path: Path,
                      params: dict) -> OperationCommand:
    mode = str(params.get("mode", "natural")).lower()
    if mode not in PARALLAX_MODELS:
        raise ValueError("Parallax mode must be natural or defined")
    cmd = ["python3", "/opt/nova-worker/parallax_runner.py", operation,
           "-i", str(input_path), "-o", str(output_path),
           "--mode", mode, "--model", str(PARALLAX_MODELS[mode][operation])]
    cmd.extend(("--tile", str(_int_choice(params, "tile", 512, {256, 384, 512, 768, 1024}))))
    cmd.extend(("--overlap", str(_int_choice(params, "overlap", 64, {32, 64, 96, 128}))))
    cmd.extend(("--pad", str(_int_choice(params, "pad", 96, {32, 64, 96, 128}))))
    batch_size = str(params.get("batch_size", "Auto"))
    if batch_size not in {"Auto", "1", "2", "4", "8"}:
        raise ValueError("batch_size must be Auto, 1, 2, 4, or 8")
    cmd.extend(("--batch-size", batch_size))
    cmd.extend(("--mtf-target", str(_float(params, "mtf_target", 0.10, 0.01, 0.5))))
    if params.get("use_mtf", True) is False:
        cmd.append("--no-mtf")
    if operation == "syqon_parallax_sharpen":
        cmd.extend(("--alpha", str(_float(params, "alpha", 0.5, 0, 2))))
    elif operation == "syqon_parallax_star_reduce":
        levels = set(range(1, 8)) if mode == "defined" else set(range(1, 11))
        cmd.extend(("--level", str(_int_choice(params, "level", 5, levels))))
    return OperationCommand(tuple(cmd), output_path)


def build_command(operation: str, input_path: Path, output_path: Path,
                  params: dict | None = None) -> OperationCommand:
    params = dict(params or {})
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported ML operation: {operation}")
    if operation.startswith("cosmic_"):
        return _cosmic_command(operation, input_path, output_path, params)
    if operation == "darkstar":
        return _darkstar_command(input_path, output_path, params)
    if operation.startswith("syqon_parallax_"):
        return _parallax_command(operation, input_path, output_path, params)
    return _graxpert_command(operation, input_path, output_path, params)


def run_operation(operation: str, input_path: Path, output_path: Path,
                  params: dict | None = None, *, timeout_s: int = 1800,
                  runner: Callable = subprocess.run) -> dict:
    """Run one allowlisted operation and normalize its output contract."""
    command = build_command(operation, input_path, output_path, params)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command.generated_output.unlink(missing_ok=True)
    started = time.monotonic()
    proc = runner(command.argv, capture_output=True, text=True, timeout=timeout_s)
    elapsed = time.monotonic() - started
    result = {
        "operation": operation,
        "elapsed_s": round(elapsed, 3),
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }
    if proc.returncode != 0:
        return {**result, "ok": False, "error": f"{operation} exited {proc.returncode}"}
    # Confirmed live, twice: an is_file()/exists() check taken immediately
    # after subprocess.run() returns can miss a file the subprocess *did*
    # genuinely finish writing (returncode 0, its own log confirms the save)
    # -- a network-volume write-visibility race on this process's cached view
    # of the mount, not a real failure. A single non-retried existence check
    # before the move (the previous version of this code) inherited the same
    # race for tools whose generated_output differs from output_path (e.g.
    # GraXpert, and cosmic_*/darkstar once the true .fits name below is
    # accounted for). Retry the relocate-then-check sequence as a unit,
    # generously (~18s total), rather than trying to special-case which step
    # needs the retry -- a genuine failure still fails, just a few seconds
    # later, which is trivial next to discarding a real multi-minute result.
    def relocate_and_check() -> bool:
        if (command.generated_output != output_path
                and command.generated_output.is_file()
                and command.generated_output.stat().st_size > 0):
            shutil.move(str(command.generated_output), str(output_path))
        return output_path.is_file() and output_path.stat().st_size > 0

    for attempt in range(8):
        if relocate_and_check():
            return {**result, "ok": True, "output_file": str(output_path)}
        time.sleep(0.5 * (attempt + 1))
    # One last check after the final sleep -- the loop above only checks
    # *before* each sleep, so without this a real file that appears during
    # the final 4s sleep (the 15-18s window) was being discarded unseen.
    if relocate_and_check():
        return {**result, "ok": True, "output_file": str(output_path)}
    return {**result, "ok": False, "error": f"{operation} produced no output"}

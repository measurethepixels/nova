"""Capability probes for ML-heavy processing engines.

Probes are read-only and distinguish an executable from a usable operation.
An engine that supports a command but lacks its model is not available.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def graxpert_data_root() -> Path:
    """Return GraXpert's platform data root without creating it."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "GraXpert" / "GraXpert"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "GraXpert"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "GraXpert"


def sys_platform() -> str:
    # Kept behind a tiny function so tests can model macOS without patching the
    # process-wide sys.platform value used by unrelated imports.
    import sys

    return sys.platform


def resolve_executable(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(value)
    return Path(found).resolve() if found else None


def probe_graxpert(
    executable: str,
    *,
    data_root: Path | None = None,
    background_model_version: str = "1.0.1",
    denoise_model_version: str = "3.0.2",
    include_hashes: bool = False,
) -> dict[str, object]:
    """Report per-operation GraXpert availability and pinned model evidence."""
    binary = resolve_executable(executable)
    root = data_root or graxpert_data_root()
    models = {
        "background": root / "bge-ai-models" / background_model_version / "model.onnx",
        "denoise": root / "denoise-ai-models" / denoise_model_version / "model.onnx",
    }
    model_evidence: dict[str, dict[str, object]] = {}
    for name, path in models.items():
        present = path.is_file() and path.stat().st_size > 0
        evidence: dict[str, object] = {
            "path": str(path),
            "present": present,
            "bytes": path.stat().st_size if present else 0,
        }
        if present and include_hashes:
            evidence["sha256"] = sha256_file(path)
        model_evidence[name] = evidence

    executable_present = binary is not None
    return {
        "engine": "graxpert",
        "executable": str(binary) if binary else executable,
        "executable_present": executable_present,
        "models": model_evidence,
        "operations": {
            "background_extraction": executable_present and model_evidence["background"]["present"],
            "denoise": executable_present and model_evidence["denoise"]["present"],
        },
        "available": executable_present and any(
            (model_evidence["background"]["present"], model_evidence["denoise"]["present"])
        ),
    }

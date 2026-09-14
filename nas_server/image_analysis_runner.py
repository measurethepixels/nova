"""Crash-isolated controller for native FITS image analysis."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class ImageAnalysisError(RuntimeError):
    """A contained image-analysis worker failure."""


def _analysis_timeout_seconds() -> float:
    raw = os.environ.get("SEESTAR_IMAGE_ANALYSIS_TIMEOUT_SECONDS", "300")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = 300.0
    return max(1.0, timeout)


def analyze(fits_path: str) -> dict:
    """Analyze a FITS file in a bounded, disposable worker process.

    SEP is a native extension: a SIGSEGV cannot be caught by Python and used to
    terminate the long-running uvicorn process during the 2026-08-27 M31 run.
    Keep native analysis outside the service process and turn worker crashes,
    timeouts, and invalid output into ordinary, visible Python failures.
    """
    path = Path(fits_path)
    if not path.exists():
        raise FileNotFoundError(fits_path)

    timeout = _analysis_timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="seestar_image_analysis_") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        cmd = [
            sys.executable, "-m", "nas_server.image_analyzer_worker",
            str(path), str(result_path),
        ]
        worker_env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        existing_pythonpath = worker_env.get("PYTHONPATH")
        worker_env["PYTHONPATH"] = (
            package_root
            if not existing_pythonpath
            else os.pathsep.join((package_root, existing_pythonpath))
        )
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=worker_env,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"analysis worker timed out after {timeout:g}s for {path.name}"
            log.error("[analyzer] %s", message)
            raise ImageAnalysisError(message) from exc

        if completed.returncode != 0:
            if completed.returncode < 0:
                detail = f"terminated by signal {-completed.returncode}"
            else:
                stderr = (completed.stderr or "").strip().splitlines()
                detail = stderr[-1][:300] if stderr else f"exit {completed.returncode}"
            message = f"analysis worker failed for {path.name}: {detail}"
            log.error("[analyzer] %s", message)
            raise ImageAnalysisError(message)

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"analysis worker returned invalid output for {path.name}"
            log.error("[analyzer] %s", message)
            raise ImageAnalysisError(message) from exc
        if not isinstance(payload, dict):
            message = f"analysis worker returned non-object output for {path.name}"
            log.error("[analyzer] %s", message)
            raise ImageAnalysisError(message)
        return payload

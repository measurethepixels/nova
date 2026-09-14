"""Crash-isolated controller for SEP-backed post-stack assessment."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class StackAssessmentError(RuntimeError):
    """A contained post-stack assessment worker failure."""


def _timeout_seconds() -> float:
    raw = os.environ.get("SEESTAR_STACK_ASSESSMENT_TIMEOUT_SECONDS", "300")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = 300.0
    return max(1.0, timeout)


def assess_stack(
    fits_path: Path | str,
    frame_count: int,
    single_frame_snrs: list[float] | None = None,
    mask_zero_border: bool = False,
) -> dict:
    """Run post-stack metrics outside uvicorn and validate the worker result."""
    path = Path(fits_path)
    if not path.exists():
        raise FileNotFoundError(path)

    timeout = _timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="seestar_stack_assessment_") as temp_dir:
        temp = Path(temp_dir)
        request_path = temp / "request.json"
        result_path = temp / "result.json"
        request_path.write_text(
            json.dumps({
                "fits_path": str(path),
                "frame_count": int(frame_count),
                "single_frame_snrs": list(single_frame_snrs or []),
                "mask_zero_border": bool(mask_zero_border),
            }, allow_nan=False),
            encoding="utf-8",
        )
        cmd = [
            sys.executable, "-m", "nas_server.stack_assessor_worker",
            str(request_path), str(result_path),
        ]
        worker_env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        existing_pythonpath = worker_env.get("PYTHONPATH")
        worker_env["PYTHONPATH"] = (
            package_root if not existing_pythonpath
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
            message = f"stack assessment timed out after {timeout:g}s for {path.name}"
            log.error("[assess] %s", message)
            raise StackAssessmentError(message) from exc

        if completed.returncode != 0:
            if completed.returncode < 0:
                detail = f"terminated by signal {-completed.returncode}"
            else:
                stderr = (completed.stderr or "").strip().splitlines()
                detail = stderr[-1][:300] if stderr else f"exit {completed.returncode}"
            message = f"stack assessment failed for {path.name}: {detail}"
            log.error("[assess] %s", message)
            raise StackAssessmentError(message)

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"stack assessment returned invalid output for {path.name}"
            log.error("[assess] %s", message)
            raise StackAssessmentError(message) from exc
        if not isinstance(payload, dict):
            message = f"stack assessment returned non-object output for {path.name}"
            log.error("[assess] %s", message)
            raise StackAssessmentError(message)
        return payload

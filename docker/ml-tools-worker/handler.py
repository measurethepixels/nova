#!/usr/bin/env python3
"""RunPod handler for Cosmic Clarity, DarkStar, and GraXpert operations."""
from __future__ import annotations

import shutil
from pathlib import Path

import runpod

from worker_core import (
    MODEL_MANIFEST,
    SUPPORTED_OPERATIONS,
    run_operation,
    validate_model_manifest,
    volume_path,
)


def _capabilities() -> dict:
    available = {}
    errors = {}
    for operation in sorted(SUPPORTED_OPERATIONS):
        try:
            validate_model_manifest(operation)
            available[operation] = True
        except Exception as exc:
            available[operation] = False
            errors[operation] = str(exc)
    return {
        "ok": True,
        "operations": available,
        "model_manifest": str(MODEL_MANIFEST),
        "errors": errors,
        "executables": {
            "cosmicclarity": shutil.which("cosmicclarity") is not None,
            "GraXpert": shutil.which("GraXpert") is not None,
        },
    }


def handler(job):
    payload = job.get("input") or {}
    if payload.get("action") == "capabilities":
        return _capabilities()

    input_path = volume_path(str(payload.get("input_path", "")))
    output_dir = volume_path(str(payload.get("output_dir", "")))
    if not input_path.is_file():
        return {"ok": False, "error": f"input not found: {payload.get('input_path')}"}

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "steps must be a non-empty list"}

    output_dir.mkdir(parents=True, exist_ok=True)
    current = input_path
    results = []
    for index, step in enumerate(steps, 1):
        operation = str(step.get("operation", ""))
        output_path = output_dir / f"{index:02d}_{operation}{input_path.suffix}"
        try:
            model_evidence = validate_model_manifest(operation)
            result = run_operation(operation, current, output_path, step.get("params"))
            result["model_evidence"] = model_evidence
        except Exception as exc:
            result = {"ok": False, "operation": operation, "error": str(exc)}
        result["output_file"] = (
            str(output_path.relative_to(volume_path("."))) if result.get("ok") else None
        )
        results.append(result)
        if not result.get("ok"):
            return {"ok": False, "error": f"step {index} failed", "steps": results}
        current = output_path

    return {
        "ok": True,
        "final_output": str(current.relative_to(volume_path("."))),
        "steps": results,
    }


runpod.serverless.start({"handler": handler})

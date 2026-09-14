"""Optional RunPod dispatch for the public ML-tools worker.

This module is deliberately separate from the RC-Astro endpoint because its
image, model provenance, and licensing boundaries differ.  It never silently
falls back to another algorithm: experiment mode must retain each engine as a
distinct candidate for manual comparison.
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from nas_server.ml_benchmark import OPERATIONS
from nas_server.runpod_workspace import RemoteWorkspace, WorkspaceRef


logger = logging.getLogger("seestar.ml_tools_gpu")
_JOB_TIMEOUT_S = 1800
_POLL_INTERVAL_S = 5
_TERMINAL_FAILURE_STATUSES = {"FAILED", "CANCELLED", "TIMED_OUT"}
_REMOTE_OPERATIONS = frozenset(
    {
        "graxpert_subtraction", "graxpert_division", "graxpert_denoise",
        "cosmic_sharpen", "cosmic_correct", "cosmic_denoise", "cosmic_both",
        "cosmic_satellite", "darkstar",
        "syqon_parallax_correct", "syqon_parallax_sharpen",
        "syqon_parallax_star_reduce",
    }
)


def _settings() -> dict[str, Any]:
    from nas_server.config import settings  # noqa: PLC0415

    return settings


def _http_post(url: str, headers: dict[str, str], json_body: dict) -> dict:
    import requests  # noqa: PLC0415

    response = requests.post(url, headers=headers, json=json_body, timeout=30)
    response.raise_for_status()
    return response.json()


def _http_get(url: str, headers: dict[str, str]) -> dict:
    import requests  # noqa: PLC0415

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _s3_client():
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    settings = _settings()
    region = settings["runpod_rcastro_region"]
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3api-{region}.runpod.io/",
        aws_access_key_id=settings["runpod_s3_access_key_id"],
        aws_secret_access_key=settings["runpod_s3_secret_access_key"],
        config=Config(s3={"addressing_style": "path"}),
    )


def _cleanup(bucket: str, prefix: str) -> bool:
    try:
        client = _s3_client()
        objects = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for item in objects.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=item["Key"])
        return True
    except Exception as exc:
        logger.warning("[ml_tools_gpu] cleanup of %s failed: %s", prefix, exc)
        return False


def _cancel(base_url: str, headers: dict[str, str], job_id: str) -> None:
    try:
        _http_post(f"{base_url}/cancel/{job_id}", headers, {})
    except Exception as exc:
        logger.warning("[ml_tools_gpu] cancel of %s failed: %s", job_id, exc)


def _worker_operation(operation_id: str) -> str | None:
    spec = OPERATIONS.get(operation_id)
    if spec is None or operation_id not in _REMOTE_OPERATIONS:
        return None
    return operation_id


def run_ml_tool_gpu(operation_id: str, input_path: str | Path | WorkspaceRef,
                    output_path: str | Path, params: dict | None = None,
                    *, workspace: RemoteWorkspace | None = None,
                    output_name: str | None = None,
                    local_source_path: str | Path | None = None) -> dict:
    """Execute one registered public ML operation remotely; never raises."""
    result = _run_ml_tool_gpu(operation_id, input_path, output_path, params,
                              workspace=workspace, output_name=output_name,
                              local_source_path=local_source_path)
    _record_gpu_tool_call_best_effort(operation_id, result)
    return result


def run_ml_tool_pipeline_gpu(operation_ids: list[str], input_path: str | Path,
                             output_path: str | Path, params: dict | None = None) -> dict:
    """Run an ordered ML-tools treatment, failing closed at the first stage."""
    if not operation_ids:
        return {"ok": False, "error": "ML-tools pipeline requires at least one operation",
                "elapsed_s": 0}
    started = time.time()
    destination = Path(output_path)
    with tempfile.TemporaryDirectory(prefix="nova-ml-pipeline-") as temp_dir:
        current = Path(input_path)
        stages = []
        for index, operation_id in enumerate(operation_ids):
            stage_output = (destination if index == len(operation_ids) - 1 else
                            Path(temp_dir) / f"stage-{index}.fit")
            result = run_ml_tool_gpu(operation_id, current, stage_output, params or {})
            stages.append({"operation": operation_id, "ok": bool(result.get("ok"))})
            if not result.get("ok") or not stage_output.is_file():
                destination.unlink(missing_ok=True)
                return {"ok": False,
                        "error": f"{operation_id}: {result.get('error', 'missing output')}",
                        "elapsed_s": int(time.time() - started), "stages": stages}
            current = stage_output
    return {"ok": True, "output_path": str(destination),
            "elapsed_s": int(time.time() - started), "execution": "runpod_gpu_pipeline",
            "stages": stages}


def _run_ml_tool_gpu(operation_id: str, input_path: str | Path,
                     output_path: str | Path, params: dict | None = None,
                     *, workspace: RemoteWorkspace | None = None,
                     output_name: str | None = None,
                     local_source_path: str | Path | None = None) -> dict:
    """Implement the remote call separately from its observational telemetry."""
    started = time.time()
    bucket = ""
    prefix = ""
    job_id: str | None = None
    terminal = False
    reservation_token: str | None = None
    try:
        worker_operation = _worker_operation(operation_id)
        if worker_operation is None:
            return {"ok": False, "error": f"operation is not supported by ML worker: {operation_id}",
                    "elapsed_s": 0}

        settings = _settings()
        if not settings.get("ml_tools_gpu_enabled", False):
            return {"ok": False, "error": "ml_tools_gpu_enabled is false", "elapsed_s": 0}
        endpoint_id = settings.get("runpod_ml_tools_endpoint_id", "")
        api_key = settings.get("runpod_api_key", "")
        bucket = workspace.bucket if workspace else settings.get("runpod_rcastro_volume_id", "")
        if not endpoint_id or not api_key or not bucket:
            return {"ok": False, "error": "RunPod ML-tools endpoint is not configured",
                    "elapsed_s": 0}

        from nas_server.rcastro_gpu import _candidate6_reserve  # noqa: PLC0415
        admission_error, reservation_token = _candidate6_reserve("ml_tools")
        if admission_error:
            return {"ok": False, "error": admission_error, "elapsed_s": 0}

        if workspace is not None and output_name is None:
            return {"ok": False, "error": "output_name is required with workspace", "elapsed_s": 0}
        if isinstance(input_path, WorkspaceRef) and (workspace is None or not workspace.contains(input_path)):
            return {"ok": False, "error": "WorkspaceRef does not belong to workspace", "elapsed_s": 0}
        source = Path(local_source_path or input_path) if not isinstance(input_path, WorkspaceRef) or local_source_path else None
        destination = Path(output_path)
        prefix = workspace.prefix if workspace else f"nova_ml_jobs/{uuid.uuid4().hex}"
        client = workspace._s3 if workspace else _s3_client()  # noqa: SLF001
        if isinstance(input_path, WorkspaceRef):
            input_key = input_path.key
        elif workspace:
            input_key = workspace.stage_input(input_path, name=f"{output_name}_input").key
        else:
            input_key = f"{prefix}/input{source.suffix}"
            client.upload_file(str(source), bucket, input_key)
        output_dir = workspace.output_dir_key(output_name) if workspace else f"{prefix}/out"
        base_url = f"https://api.runpod.ai/v2/{endpoint_id}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        submission = _http_post(
            f"{base_url}/run", headers,
            {"input": {"input_path": input_key, "output_dir": output_dir,
                       "steps": [{"operation": worker_operation, "params": params or {}}]}},
        )
        job_id = submission["id"]
        deadline = time.time() + _JOB_TIMEOUT_S
        completed = None
        while time.time() < deadline:
            body = _http_get(f"{base_url}/status/{job_id}", headers)
            status = body.get("status")
            if status == "COMPLETED":
                terminal = True
                from nas_server.rcastro_gpu import _record_candidate6_gpu_spend  # noqa: PLC0415
                _record_candidate6_gpu_spend(
                    tool=operation_id, endpoint_id=endpoint_id, job_id=job_id,
                    terminal_body=body, engine="ml_tools")
                completed = body
                break
            if status in _TERMINAL_FAILURE_STATUSES:
                terminal = True
                from nas_server.rcastro_gpu import _record_candidate6_gpu_spend  # noqa: PLC0415
                _record_candidate6_gpu_spend(
                    tool=operation_id, endpoint_id=endpoint_id, job_id=job_id,
                    terminal_body=body, engine="ml_tools")
                return {"ok": False, "error": f"RunPod job {status}: {body.get('output')}",
                        "elapsed_s": int(time.time() - started)}
            time.sleep(_POLL_INTERVAL_S)
        if completed is None:
            _cancel(base_url, headers, job_id)
            from nas_server.rcastro_gpu import _record_candidate6_gpu_spend  # noqa: PLC0415
            _record_candidate6_gpu_spend(
                tool=operation_id, endpoint_id=endpoint_id, job_id=job_id,
                engine="ml_tools")
            return {"ok": False, "error": "RunPod job timed out (client-side deadline)",
                    "elapsed_s": int(time.time() - started)}

        output = completed.get("output") or {}
        if not output.get("ok"):
            return {"ok": False, "error": output.get("error", "worker reported failure"),
                    "elapsed_s": int(time.time() - started)}
        if workspace and not str(output.get("final_output", "")).startswith(
                f"{output_dir}/"):
            return {"ok": False, "error": "worker output escaped assigned workspace directory",
                    "elapsed_s": int(time.time() - started)}
        destination.parent.mkdir(parents=True, exist_ok=True)
        from nas_server.s3_consistency import retry_not_found  # noqa: PLC0415
        retry_not_found(
            lambda: client.download_file(
                bucket, output["final_output"], str(destination)))
        if not destination.is_file() or destination.stat().st_size == 0:
            return {"ok": False, "error": "downloaded output is missing or empty",
                    "elapsed_s": int(time.time() - started)}
        from nas_server.seti_astro import _preserve_celestial_wcs  # noqa: PLC0415

        if source is not None:
            _preserve_celestial_wcs(source, destination)
        return {"ok": True, "output_path": str(destination),
                "elapsed_s": int(time.time() - started), "execution": "runpod_gpu"}
    except Exception as exc:
        if job_id is not None and not terminal:
            _cancel(base_url, headers, job_id)
            try:
                from nas_server.rcastro_gpu import _record_candidate6_gpu_spend  # noqa: PLC0415
                _record_candidate6_gpu_spend(
                    tool=operation_id, endpoint_id=endpoint_id, job_id=job_id,
                    engine="ml_tools")
            except Exception as spend_error:
                logger.warning("[ml_tools_gpu] spend accounting failed: %s", spend_error)
        return {"ok": False, "error": str(exc), "elapsed_s": int(time.time() - started)}
    finally:
        from nas_server.rcastro_gpu import _release_candidate6_reservation  # noqa: PLC0415
        _release_candidate6_reservation(reservation_token)
        # Guard against calling _cleanup with prefix="" (an exception before
        # `prefix` is assigned, e.g. from a malformed operation_id) -- an
        # empty prefix matches every key in the bucket via list_objects_v2,
        # which would delete the entire shared volume, not just this job's.
        if bucket and prefix and workspace is None:
            _cleanup(bucket, prefix)


def _record_gpu_tool_call_best_effort(operation_id: str, result: dict) -> None:
    """Record one invocation without allowing telemetry to affect processing."""
    try:
        _record_gpu_tool_call(
            tool=operation_id,
            backend="gpu",
            ok=bool(result.get("ok")),
            elapsed_s=float(result.get("elapsed_s", 0.0)),
            error=result.get("error"),
        )
    except Exception as exc:
        logger.warning("[ml_tools_gpu] call recording failed: %s", exc)


def _record_gpu_tool_call(**values) -> None:
    """Late import keeps database settings out of pure client imports/tests."""
    from nas_server.database import record_gpu_tool_call  # noqa: PLC0415

    record_gpu_tool_call(**values)

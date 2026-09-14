"""RC-Astro (BXT/SXT/NXT) dispatch: RunPod Serverless GPU first, local CLI
fallback. See docs: RCAstro_GPU_Serverless_Investigation note (2026-08-18)
for the full build/validation history.

Files never travel through the RunPod job payload (it caps at ~10-20MB) --
they go through the network volume via RunPod's S3-compatible API. Every job
gets a unique prefix so concurrent calls don't collide, and inputs/outputs
are cleaned up after each call.

Heavy clients (requests, boto3) are imported lazily inside the small `_http_*`
/ `_s3_client` wrappers below, not at module level, so this module stays
importable under the repository's pure-logic CI (which installs only
pytest). Tests substitute fakes for those wrappers directly -- see
tests/test_rcastro_gpu.py -- rather than needing the real packages present.
"""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# Pure-stdlib (dataclasses/uuid/pathlib/typing only) -- safe to import at
# module level under the pure-logic CI, unlike requests/boto3 below.
from nas_server.runpod_workspace import RemoteWorkspace, WorkspaceRef

_EXPERIMENT_REMOTE = threading.local()


class experiment_remote_context:
    """Thread-local shared-input context; preserves local fallback semantics."""
    def __init__(self, workspace, input_ref, output_name):
        self.values = (workspace, input_ref, output_name)
    def __enter__(self):
        _EXPERIMENT_REMOTE.values = self.values
    def __exit__(self, *_exc):
        _EXPERIMENT_REMOTE.values = None

logger = logging.getLogger("seestar.rcastro_gpu")

_JOB_TIMEOUT_S = 480  # cold start (~90s) + compute + generous margin
_POLL_INTERVAL_S = 5
_TERMINAL_FAILURE_STATUSES = {"FAILED", "CANCELLED", "TIMED_OUT"}
_GPU_SPEND_EVENTS: list[dict[str, Any]] = []
_GPU_SPEND_RESERVATIONS: dict[str, float] = {}
_GPU_SPEND_LOCK = threading.Lock()


def _settings() -> dict[str, Any]:
    from nas_server.config import settings  # noqa: PLC0415

    return settings


_REMOTE_ENV_SETTINGS = {
    "NOVA_RUNPOD_S3_ACCESS_KEY_ID": "runpod_s3_access_key_id",
    "NOVA_RUNPOD_S3_SECRET_ACCESS_KEY": "runpod_s3_secret_access_key",
    "NOVA_RUNPOD_VOLUME_ID": "runpod_rcastro_volume_id",
    "NOVA_RUNPOD_REGION": "runpod_rcastro_region",
    "NOVA_RUNPOD_API_KEY": "runpod_api_key",
    "NOVA_RUNPOD_RCASTRO_ENDPOINT_ID": "runpod_rcastro_endpoint_id",
}


def _remote_settings() -> dict[str, Any]:
    """Resolve remote settings without persisting injected worker secrets.

    Disposable CPU workers receive S3 configuration through their process
    environment. Their generated application-settings overlay deliberately
    contains no credentials, so shared pipeline code must merge those values
    in memory. Empty environment values never erase normal NAS configuration.
    """
    resolved = dict(_settings())
    for env_name, setting_name in _REMOTE_ENV_SETTINGS.items():
        value = os.environ.get(env_name, "")
        if value:
            resolved[setting_name] = value
    return resolved


def _s3_settings() -> dict[str, Any]:
    """Backward-compatible narrow name for the shared remote resolver."""
    return _remote_settings()


def reset_gpu_spend_events() -> None:
    """Start a fresh disposable-worker job accounting window."""
    with _GPU_SPEND_LOCK:
        _GPU_SPEND_EVENTS.clear()
        _GPU_SPEND_RESERVATIONS.clear()


def gpu_spend_events() -> list[dict[str, Any]]:
    """Return a copy suitable for the worker's authenticated poll response."""
    with _GPU_SPEND_LOCK:
        return [dict(event) for event in _GPU_SPEND_EVENTS]


def _candidate6_reserve(engine: str = "rcastro") -> tuple[str | None, str | None]:
    """Atomically admit and reserve estimated spend before concurrent fan-out."""
    raw = os.environ.get("NOVA_RUNPOD_BUDGET_BASELINE", "")
    if not raw:
        return None, None
    try:
        baseline = json.loads(raw)
        estimate_name = {
            "rcastro": "NOVA_RUNPOD_RCASTRO_GPU_FALLBACK_ESTIMATE_USD",
            "ml_tools": "NOVA_RUNPOD_ML_TOOLS_GPU_FALLBACK_ESTIMATE_USD",
        }[engine]
        estimate = float(os.environ[estimate_name])
        if not math.isfinite(estimate) or estimate <= 0:
            raise ValueError("GPU fallback estimate must be positive and finite")
        with _GPU_SPEND_LOCK:
            gpu_spent = sum(float(event["cost_usd"]) for event in _GPU_SPEND_EVENTS)
            reserved = sum(_GPU_SPEND_RESERVATIONS.values())
            for name, cap in {"session": 2.0, "daily": 5.0, "monthly": 25.0}.items():
                projected = float(baseline[name]) + gpu_spent + reserved + estimate
                if not math.isfinite(projected) or projected > cap:
                    return f"RunPod GPU admission refused: {name}", None
            token = uuid.uuid4().hex
            _GPU_SPEND_RESERVATIONS[token] = estimate
            return None, token
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return f"RunPod GPU admission refused: invalid budget context ({exc})", None


def _candidate6_admission() -> str | None:
    """Compatibility probe; immediately releases its temporary reservation."""
    error, token = _candidate6_reserve()
    if token:
        with _GPU_SPEND_LOCK:
            _GPU_SPEND_RESERVATIONS.pop(token, None)
    return error


def _release_candidate6_reservation(token: str | None) -> None:
    if token:
        with _GPU_SPEND_LOCK:
            _GPU_SPEND_RESERVATIONS.pop(token, None)


def _record_candidate6_gpu_spend(
    *, tool: str, endpoint_id: str, job_id: str,
    terminal_body: dict[str, Any] | None = None, engine: str = "rcastro",
) -> None:
    """Capture attributable Serverless execution cost for VM reconciliation.

    A cancellation/status response does not always expose ``executionTime``.
    Once a Candidate 6 job has been submitted, zero is not a safe accounting
    value, so those paths use the same conservative estimate admitted before
    submission.
    """
    if not os.environ.get("NOVA_RUNPOD_BUDGET_BASELINE", ""):
        return
    cost_usd: float | None = None
    try:
        execution_ms = float((terminal_body or {})["executionTime"])
        rate_name = {
            "rcastro": "NOVA_RUNPOD_RCASTRO_GPU_RATE_USD_PER_SECOND",
            "ml_tools": "NOVA_RUNPOD_ML_TOOLS_GPU_RATE_USD_PER_SECOND",
        }[engine]
        rate = float(os.environ[rate_name])
        if math.isfinite(execution_ms) and execution_ms >= 0 \
                and math.isfinite(rate) and rate > 0:
            cost_usd = execution_ms / 1000.0 * rate
    except (KeyError, TypeError, ValueError):
        pass
    if cost_usd is None:
        estimate_name = {
            "rcastro": "NOVA_RUNPOD_RCASTRO_GPU_FALLBACK_ESTIMATE_USD",
            "ml_tools": "NOVA_RUNPOD_ML_TOOLS_GPU_FALLBACK_ESTIMATE_USD",
        }[engine]
        cost_usd = float(os.environ[estimate_name])
        if not math.isfinite(cost_usd) or cost_usd <= 0:
            raise ValueError("RunPod GPU fallback estimate must be positive and finite")
    event = {
        "event_key": f"candidate6:serverless:{endpoint_id}:{job_id}:gpu",
        "backend": f"runpod-serverless:{endpoint_id}",
        "operation": f"{engine}:{tool}",
        "cost_usd": cost_usd,
    }
    with _GPU_SPEND_LOCK:
        if not any(item["event_key"] == event["event_key"] for item in _GPU_SPEND_EVENTS):
            _GPU_SPEND_EVENTS.append(event)


def _http_post(url: str, headers: dict[str, str], json_body: dict) -> dict:
    import requests  # noqa: PLC0415

    resp = requests.post(url, headers=headers, json=json_body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _http_get(url: str, headers: dict[str, str]) -> dict:
    import requests  # noqa: PLC0415

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _s3_client():
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    s = _s3_settings()
    region = s["runpod_rcastro_region"]
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3api-{region}.runpod.io/",
        aws_access_key_id=s["runpod_s3_access_key_id"],
        aws_secret_access_key=s["runpod_s3_secret_access_key"],
        # RunPod's S3-compatible endpoint 307-redirects some operations
        # (observed: DeleteObjects) under virtual-hosted-style addressing.
        config=Config(s3={"addressing_style": "path"}),
    )


def _volume_bucket() -> str:
    return _s3_settings()["runpod_rcastro_volume_id"]


def _cancel_job(base_url: str, headers: dict[str, str], job_id: str) -> dict[str, Any] | None:
    """Best-effort cancel -- never raises. Called before falling back to
    local CLI on a client-side timeout or a post-submit exception, so an
    abandoned remote job doesn't keep running (and billing) unattended. Not
    called for FAILED/CANCELLED/TIMED_OUT -- RunPod already stopped those
    jobs remotely, so a cancel call would be a redundant no-op."""
    try:
        body = _http_post(f"{base_url}/cancel/{job_id}", headers, {})
        logger.info(f"[rcastro_gpu] cancelled orphaned job {job_id}")
        return body
    except Exception as cancel_exc:
        logger.warning(f"[rcastro_gpu] failed to cancel job {job_id}: {cancel_exc}")
        return None


def _cancel_and_record_gpu_spend(
    *, tool: str, endpoint_id: str, base_url: str,
    headers: dict[str, str], job_id: str,
) -> None:
    """Cancel a submitted job and account for its paid execution idempotently."""
    cancel_body = _cancel_job(base_url, headers, job_id)
    terminal_body = cancel_body
    if os.environ.get("NOVA_RUNPOD_BUDGET_BASELINE", ""):
        try:
            status_body = _http_get(f"{base_url}/status/{job_id}", headers)
            if "executionTime" in status_body:
                terminal_body = status_body
        except Exception as status_exc:
            logger.warning(
                f"[rcastro_gpu] could not obtain post-cancel cost for {job_id}: {status_exc}"
            )
        _record_candidate6_gpu_spend(
            tool=tool, endpoint_id=endpoint_id, job_id=job_id,
            terminal_body=terminal_body,
        )


def _cleanup_job_prefix(bucket: str, job_prefix: str) -> None:
    """Best-effort volume cleanup -- never raises, so a cleanup failure never
    masks or replaces the primary result already computed by the caller."""
    try:
        s3 = _s3_client()
        objects = s3.list_objects_v2(Bucket=bucket, Prefix=job_prefix)
        # Bulk DeleteObjects 307-redirects on this endpoint; delete one at a time.
        for obj in objects.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
    except Exception as cleanup_exc:
        logger.warning(f"[rcastro_gpu] cleanup of {job_prefix} failed: {cleanup_exc}")


def _download_atomic(s3, bucket: str, remote_key: str, dest_path: Path) -> None:
    """Download to a sibling temp path on the same filesystem, then rename
    into `dest_path`. Root-cause fix for a real live bug (#586, #588):
    downloading straight into `dest_path` lets a reader that opens the SAME
    NAME while the SMB/CIFS-mounted write is still landing observe a
    torn/partial file that happens to already report the right size/shape.
    A rename onto `dest_path` only ever exposes it as either "not there yet"
    or "fully written" -- there is no name a concurrent reader can open that
    shows a partial state. Same directory (not /tmp) so the rename is a
    same-filesystem move, not a cross-filesystem copy."""
    tmp_path = dest_path.with_name(f".{dest_path.stem}.tmp-{uuid.uuid4().hex}{dest_path.suffix}")
    try:
        from nas_server.s3_consistency import retry_not_found
        retry_not_found(lambda: s3.download_file(bucket, remote_key, str(tmp_path)))
        os.replace(tmp_path, dest_path)
    finally:
        # Best-effort: only relevant if download_file raised after creating
        # the temp file, or replace() itself failed -- never masks the real
        # exception already propagating past this function.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def run_rcastro_gpu(tool: str, input_path: str | Path | WorkspaceRef,
                    output_path: str | Path | None = None,
                    args: dict[str, str] | None = None,
                    stars_output_path: str | Path | None = None,
                    workspace: RemoteWorkspace | None = None,
                    output_name: str | None = None) -> dict:
    """Run one RC-Astro tool on the RunPod GPU endpoint. Returns the usual
    {"ok", "output_path"/"error", "elapsed_s"} shape; never raises.

    stars_output_path: for SXT's --stars mode, also download the stars-only
    sidecar the worker reports as final_stars_output.

    workspace / output_name (Candidate 1, RunPod CPU-pod planning session
    2026-08-23): pass a RemoteWorkspace to run this call inside a shared,
    caller-owned prefix instead of a fresh one this call creates and
    cleans up on its own. `output_name` is then required -- it's the name
    this stage's output is registered under in the workspace. `input_path`
    may be a WorkspaceRef (a prior stage's real output, skipping a
    redundant upload) instead of a local path. `output_path` becomes
    optional in workspace mode -- when omitted, this call skips the local
    download entirely and the caller chains the returned "workspace_ref"
    into the next endpoint call instead of round-tripping through the NAS.
    Cleanup of the workspace prefix is NOT done here -- only the
    workspace's own owner does that, once every stage using it is done
    (see runpod_workspace.RemoteWorkspace.cleanup).

    Legacy behavior (no workspace) is completely unchanged: fresh
    per-call UUID prefix, required output_path, own-prefix cleanup in
    finally, exactly as before this parameter existed.
    """
    if workspace is not None and output_name is None:
        return {"ok": False, "error": "output_name is required when workspace is given",
               "elapsed_s": 0}
    if workspace is None and output_path is None:
        return {"ok": False, "error": "output_path is required outside workspace mode",
               "elapsed_s": 0}
    if isinstance(input_path, WorkspaceRef) and workspace is None:
        return {"ok": False, "error": "a WorkspaceRef input requires a workspace",
               "elapsed_s": 0}
    if isinstance(input_path, WorkspaceRef) and workspace is not None \
            and not workspace.contains(input_path):
        # A ref naming a key outside the supplied workspace -- a stale ref
        # from a prior run, a programming mistake, or (worst case) another
        # workspace's live data -- must never be silently consumed as if it
        # were this workspace's own.
        return {"ok": False,
               "error": f"WorkspaceRef {input_path.key!r} does not belong to "
                        f"workspace {workspace.run_id!r}",
               "elapsed_s": 0}

    s = _remote_settings()
    if not s.get("rcastro_gpu_enabled", True):
        return {"ok": False, "error": "rcastro_gpu_enabled is false", "elapsed_s": 0}
    endpoint_id = s.get("runpod_rcastro_endpoint_id", "")
    api_key = s.get("runpod_api_key", "")
    if not endpoint_id or not api_key:
        return {"ok": False, "error": "RunPod GPU endpoint not configured", "elapsed_s": 0}
    admission_error, reservation_token = _candidate6_reserve()
    if admission_error:
        return {"ok": False, "error": admission_error, "elapsed_s": 0}

    t0 = time.time()
    is_workspace_mode = workspace is not None
    if is_workspace_mode:
        bucket = workspace.bucket
        input_name = f"{output_name}_input"
        # Recorded *before* this call attempts anything, so the finally
        # block can tell "I reserved this, so a failure means I should
        # release it for a retry" apart from "this was already reserved by
        # a different, still-live stage, so I must never touch it."
        output_name_was_free = not workspace.is_reserved(output_name)
        input_name_was_free = not workspace.is_reserved(input_name)
    else:
        job_prefix = f"nova_jobs/{uuid.uuid4().hex}"
        bucket = _volume_bucket()
    base_url = f"https://api.runpod.ai/v2/{endpoint_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    job_id: str | None = None
    # RunPod itself has already stopped a job once it reports a terminal
    # status; cancelling it afterward (e.g. because a later download step
    # raised) would be a no-op at best and a contract violation of
    # _cancel_job's "only for abandoned/possibly-still-running jobs" intent.
    job_already_terminal = False
    job_succeeded = False

    try:
        s3 = workspace._s3 if is_workspace_mode else _s3_client()  # noqa: SLF001
        # output_dir_key() lives inside this try (not above, before it) so a
        # collision -- including a same-stage retry racing its own prior
        # reservation -- is caught by the except block below like any other
        # failure, instead of raising past run_rcastro_gpu's documented
        # "never raises" contract.
        if is_workspace_mode:
            output_dir_key = workspace.output_dir_key(output_name)
        else:
            output_dir_key = f"{job_prefix}/out"

        if isinstance(input_path, WorkspaceRef):
            input_key = input_path.key
        elif is_workspace_mode:
            input_key = workspace.stage_input(input_path, name=input_name).key
        else:
            input_key = f"{job_prefix}/input{Path(input_path).suffix}"
            s3.upload_file(str(input_path), bucket, input_key)

        payload = {
            "input": {
                "input_path": input_key,
                "steps": [{"tool": tool, "args": args or {}}],
                "output_dir": output_dir_key,
            }
        }
        submit_body = _http_post(f"{base_url}/run", headers, payload)
        job_id = submit_body["id"]

        deadline = time.time() + _JOB_TIMEOUT_S
        result = None
        while time.time() < deadline:
            body = _http_get(f"{base_url}/status/{job_id}", headers)
            status = body.get("status")
            if status == "COMPLETED":
                job_already_terminal = True
                _record_candidate6_gpu_spend(
                    tool=tool, endpoint_id=endpoint_id, job_id=job_id, terminal_body=body
                )
                result = body
                break
            if status in _TERMINAL_FAILURE_STATUSES:
                job_already_terminal = True
                _record_candidate6_gpu_spend(
                    tool=tool, endpoint_id=endpoint_id, job_id=job_id, terminal_body=body
                )
                elapsed = int(time.time() - t0)
                logger.warning(f"[rcastro_gpu] {tool} job {status}: {body.get('output') or body}")
                return {"ok": False, "error": f"RunPod job {status}: {body.get('output')}",
                       "elapsed_s": elapsed}
            time.sleep(_POLL_INTERVAL_S)
        else:
            elapsed = int(time.time() - t0)
            logger.warning(f"[rcastro_gpu] {tool} job timed out client-side after {elapsed}s"
                           f" -- cancelling job {job_id}")
            _cancel_and_record_gpu_spend(
                tool=tool, endpoint_id=endpoint_id, base_url=base_url,
                headers=headers, job_id=job_id,
            )
            return {"ok": False, "error": "RunPod job timed out (client-side deadline)",
                   "elapsed_s": elapsed}

        output = result.get("output") or {}
        if not output.get("ok"):
            elapsed = int(time.time() - t0)
            return {"ok": False, "error": output.get("error", "worker reported failure"),
                   "elapsed_s": elapsed}

        remote_output = output["final_output"]
        elapsed = int(time.time() - t0)
        if is_workspace_mode and not remote_output.startswith(f"{output_dir_key}/"):
            # Don't trust a worker-reported path outside the directory this
            # call actually asked it to write into -- wrapping it as a
            # workspace_ref would hand the caller a ref that silently reaches
            # outside this workspace (or another stage's directory inside
            # it), the exact cross-workspace leak the whole ref/workspace
            # boundary exists to prevent.
            return {"ok": False,
                   "error": f"worker reported output {remote_output!r} outside "
                            f"its assigned output directory {output_dir_key!r}",
                   "elapsed_s": elapsed}
        response: dict = {"ok": True, "elapsed_s": elapsed}
        if is_workspace_mode:
            response["workspace_ref"] = WorkspaceRef(remote_output)
        if output_path is not None:
            _download_atomic(s3, bucket, remote_output, Path(output_path))
            response["output_path"] = str(output_path)
        if stars_output_path is not None:
            remote_stars = output.get("final_stars_output")
            if not remote_stars:
                logger.warning(f"[rcastro_gpu] {tool} did not report a stars output; "
                               f"caller requested stars_output_path -- treating as failure "
                               f"so the caller's fallback chain (local CLI) gets a real attempt")
                return {"ok": False,
                       "error": "GPU worker completed but did not produce the requested stars sidecar",
                       "elapsed_s": elapsed}
            _download_atomic(s3, bucket, remote_stars, Path(stars_output_path))

        logger.info(f"[rcastro_gpu] {tool} done in {elapsed}s (GPU)"
                    f"{' [workspace]' if is_workspace_mode else ''}")
        job_succeeded = True
        return response
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.warning(f"[rcastro_gpu] {tool} exception ({e}) after {elapsed}s")
        if job_id is not None and not job_already_terminal:
            _cancel_and_record_gpu_spend(
                tool=tool, endpoint_id=endpoint_id, base_url=base_url,
                headers=headers, job_id=job_id,
            )
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}
    finally:
        _release_candidate6_reservation(reservation_token)
        # Workspace-mode cleanup is explicitly NOT done here -- deleting a
        # shared prefix from inside one stage's call would destroy a still-
        # pending sibling stage's input/output. Only the workspace's own
        # owner cleans up, once every stage using it is done (see
        # RemoteWorkspace.cleanup).
        if not is_workspace_mode:
            _cleanup_job_prefix(bucket, job_prefix)
        elif not job_succeeded:
            # Release only names THIS call actually reserved (was free
            # beforehand, still reserved now) -- a same-stage retry can then
            # reuse output_name/input_name. A name that was already reserved
            # before this call started belongs to a different, still-live
            # stage and must never be released here.
            if output_name_was_free and workspace.is_reserved(output_name):
                workspace.release_name(output_name)
            if input_name_was_free and workspace.is_reserved(input_name):
                workspace.release_name(input_name)


def run_rcastro_local(tool: str, input_path: str | Path, output_path: str | Path,
                      args: dict[str, str] | None = None,
                      stars_output_path: str | Path | None = None) -> dict:
    """Run one RC-Astro tool via the local CLI (CPU) -- the fallback when the
    GPU endpoint is unavailable or fails. Same tool, same output
    characteristics as the GPU path, just slower."""
    s = _settings()
    rcastro_bin = s.get("rcastro_bin", "rc-astro")
    t0 = time.time()
    # Point the CLI at a unique temp sibling of output_path, never the real
    # name -- same root-cause fix as _download_atomic (#586/#588): a reader
    # that opens output_path while the CLI is still writing it can observe a
    # torn file that already reports the right size/shape. Renaming onto
    # output_path only after the CLI exits means no name a concurrent reader
    # can open ever shows a partial state.
    out = Path(output_path)
    tmp_output = out.with_name(f".{out.stem}.tmp-{uuid.uuid4().hex}{out.suffix}")
    cmd = [rcastro_bin, "--no-banner", tool, "--device", "cpu", "--overwrite",
           str(input_path), "-o", str(tmp_output)]
    for flag, value in (args or {}).items():
        cmd.append(flag)
        if value != "":
            cmd.append(str(value))
    # expected_stars is derived from tmp_output's unique uuid-bearing name, so
    # unlike the pre-atomic-rename version of this function, it can neither
    # collide with input_path nor already exist from a previous attempt --
    # both of those hazards only applied when RC-Astro wrote its sidecar next
    # to the caller's stable, reused output_path. No pre-flight check needed.
    expected_stars = tmp_output.with_name(f"{tmp_output.stem}-stars{tmp_output.suffix}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        elapsed = int(time.time() - t0)
        if proc.returncode != 0 or not tmp_output.exists():
            logger.warning(f"[rcastro_local] {tool} failed (rc={proc.returncode}): "
                           f"{proc.stderr[-500:]}")
            return {"ok": False, "error": proc.stderr[-500:] or "rc-astro CLI failed",
                   "elapsed_s": elapsed}
        if stars_output_path is not None:
            if not expected_stars.exists():
                logger.warning(f"[rcastro_local] {tool} completed but did not produce the "
                               f"requested stars sidecar (expected {expected_stars})")
                return {"ok": False,
                       "error": "local rc-astro CLI completed but did not produce the requested stars sidecar",
                       "elapsed_s": elapsed}
            expected_stars.replace(stars_output_path)
        tmp_output.replace(output_path)
        logger.info(f"[rcastro_local] {tool} done in {elapsed}s (CPU)")
        return {"ok": True, "output_path": str(output_path), "elapsed_s": elapsed}
    except Exception as e:
        elapsed = int(time.time() - t0)
        logger.warning(f"[rcastro_local] {tool} exception ({e}) after {elapsed}s")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}
    finally:
        # Best-effort: only relevant if something above raised or returned
        # early after the CLI already wrote the temp file (e.g. the missing-
        # stars-sidecar failure path) -- never masks a real exception.
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass


def run_rcastro(tool: str, input_path: str | Path, output_path: str | Path,
                args: dict[str, str] | None = None,
                stars_output_path: str | Path | None = None) -> dict:
    """GPU first, local CLI fallback. This is the entry point callers use."""
    remote = getattr(_EXPERIMENT_REMOTE, "values", None)
    if remote:
        workspace, input_ref, output_name = remote
        result = run_rcastro_gpu(tool, input_ref, output_path, args, stars_output_path,
                                 workspace=workspace, output_name=output_name)
    else:
        result = run_rcastro_gpu(tool, input_path, output_path, args, stars_output_path)
    backend = "gpu"
    if not result.get("ok"):
        logger.info(f"[rcastro] {tool} GPU path unavailable ({result.get('error')}) "
                   f"-- falling back to local CLI")
        result = run_rcastro_local(tool, input_path, output_path, args, stars_output_path)
        backend = "cpu"
    _record_gpu_tool_call_best_effort(tool, backend, result)
    return result


def _record_gpu_tool_call_best_effort(tool: str, backend: str, result: dict) -> None:
    """Persist final-backend telemetry without affecting image processing."""
    try:
        elapsed_s = float(result.get("elapsed_s", 0.0))
        rate = float(_settings().get("runpod_rcastro_gpu_rate_usd_per_second", 0.0))
        cost_usd = elapsed_s * rate if backend == "gpu" else 0.0
        _record_gpu_tool_call(
            tool=tool,
            backend=backend,
            ok=bool(result.get("ok")),
            elapsed_s=elapsed_s,
            cost_usd=cost_usd,
            error=result.get("error"),
        )
    except (Exception, SystemExit):
        logger.exception("[rcastro] unable to record %s %s telemetry", tool, backend)


def _record_gpu_tool_call(**values) -> None:
    """Lazy database seam so pure-logic callers can substitute persistence."""
    from nas_server.database import record_gpu_tool_call  # noqa: PLC0415

    record_gpu_tool_call(**values)

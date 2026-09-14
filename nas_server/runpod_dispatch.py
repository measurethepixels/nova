"""Default-off orchestration for one disposable RunPod CPU worker.

This is the Candidate 6 integration boundary.  It deliberately owns no
background loop: a caller supplies one queue job, and this controller either
returns its completed worker response or raises after ensuring every pod with
a known ID has a confirmed HTTP-204 deletion.  Real enablement remains a
separate Henry-gated queue setting.

RunPod REST v2 DELETE 204 confirms acceptance; a subsequent authoritative GET
404 is required before the lifecycle is marked gone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nas_server import runpod_pod_client as pod_client
from nas_server import runpod_pod_lifecycle as lifecycle
from nas_server import runpod_spend_policy as spend_policy

MANAGED_BY = "seestar-nova-candidate6"
WORKER_JOBS_API_CONTRACT = "cpu-pod-jobs-v1"
READY_CONFIRMATIONS = 2
RECONCILIATION_MAX_AGE_SECONDS = 30 * 60
_SAFE_ID_RE = re.compile(r"[^a-z0-9-]+")
logger = logging.getLogger(__name__)


class DispatchError(RuntimeError):
    """A bounded dispatch failed without leaving known paid compute alive."""


class ReviewRequired(DispatchError):
    """Automation stopped because repeating the operation would be unsafe."""


@dataclass(frozen=True)
class DispatchConfig:
    tier: str | None
    availability_first: bool
    estimated_cost_usd: float
    hourly_rate_usd: float
    auth_token: str = field(repr=False)
    s3_access_key_id: str = field(repr=False)
    s3_secret_access_key: str = field(repr=False)
    runpod_api_key: str = field(repr=False)
    rcastro_endpoint_id: str
    rcastro_gpu_fallback_estimate_usd: float
    rcastro_gpu_rate_usd_per_second: float
    volume_id: str
    region: str
    expected_image_version: str | None = None
    startup_poll_seconds: float = 5.0
    job_poll_seconds: float = 5.0
    job_timeout_seconds: float = 7200.0

    def validate(self) -> None:
        required = {
            "auth_token": self.auth_token,
            "s3_access_key_id": self.s3_access_key_id,
            "s3_secret_access_key": self.s3_secret_access_key,
            "runpod_api_key": self.runpod_api_key,
            "rcastro_endpoint_id": self.rcastro_endpoint_id,
            "volume_id": self.volume_id,
            "region": self.region,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "CPU worker configuration is incomplete: " + ", ".join(missing)
            )
        for name, value in (
            ("estimated_cost_usd", self.estimated_cost_usd),
            ("hourly_rate_usd", self.hourly_rate_usd),
            ("rcastro_gpu_fallback_estimate_usd", self.rcastro_gpu_fallback_estimate_usd),
            ("rcastro_gpu_rate_usd_per_second", self.rcastro_gpu_rate_usd_per_second),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.startup_poll_seconds <= 0 or self.job_poll_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if self.job_timeout_seconds <= 0:
            raise ValueError("job_timeout_seconds must be positive")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _worker_client():
    """Lazy import keeps pure orchestration tests dependency-light."""
    from nas_server import worker_client

    return worker_client


def reconciliation_backstop_ready(
    settings: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """Require a recent healthy #426 heartbeat before paid dispatch.

    This makes #425 impossible to activate safely until reconciliation is
    deployed and has successfully inventoried RunPod.  A configuration flag
    alone is not evidence that the cleanup backstop is alive.
    """
    state_path = str(settings.get("runpod_reconciliation_state_path", "")).strip()
    if not state_path:
        return False
    try:
        payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(str(payload["checked_at"]).replace("Z", "+00:00"))
        age = ((now or _utcnow()) - checked_at.astimezone(timezone.utc)).total_seconds()
        return payload.get("healthy") is True and 0 <= age <= RECONCILIATION_MAX_AGE_SECONDS
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _parse_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def ownership_for(record: lifecycle.PodRecord) -> tuple[str, dict[str, str]]:
    """Return the human-readable name and positive ownership markers.

    The work key itself is never sent to RunPod; only its SHA-256 digest is.
    #426 can therefore reconcile only resources explicitly marked by NOVA.
    """
    digest = hashlib.sha256(record.work_key.encode("utf-8")).hexdigest()
    lifecycle_id = str(record.id)
    safe_id = _SAFE_ID_RE.sub("-", lifecycle_id.lower()).strip("-") or "unknown"
    return (
        f"nova-c6-{safe_id}-{digest[:12]}",
        {
            "NOVA_MANAGED_BY": MANAGED_BY,
            "NOVA_LIFECYCLE_ID": lifecycle_id,
            "NOVA_WORK_KEY_HASH": digest,
        },
    )


class RunPodDispatcher:
    def __init__(
        self,
        config: DispatchConfig,
        *,
        now: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self.now = now
        self.sleep = sleep
        self._pod_hourly_rates: dict[str, float] = {}

    def _worker_ready(self, pod_id: str) -> tuple[bool, str | None]:
        try:
            platform = pod_client.get_pod_health(pod_id)
        except Exception as exc:
            logger.warning(
                "RunPod readiness pod=%s gate=platform result=error error_type=%s",
                pod_id,
                type(exc).__name__,
            )
            return False, None
        worker_url = platform.get("worker_url")
        running = platform.get("running", platform.get("reachable"))
        if not running or not worker_url:
            logger.info(
                "RunPod readiness pod=%s gate=platform result=waiting status=%s worker_url=%s",
                pod_id,
                platform.get("desired_status") or "unknown",
                "present" if worker_url else "missing",
            )
            return False, None
        logger.info("RunPod readiness pod=%s gate=platform result=pass", pod_id)
        health = _worker_client().ping(worker_url, auth_token=self.config.auth_token)
        if not health:
            logger.info("RunPod readiness pod=%s gate=ping result=waiting", pod_id)
            return False, worker_url
        if health.get("backend") != "runpod-cpu-pod":
            logger.warning("RunPod readiness pod=%s gate=ping result=backend_mismatch", pod_id)
            return False, worker_url
        expected = self.config.expected_image_version
        if expected and health.get("image_version") != expected:
            logger.warning("RunPod readiness pod=%s gate=ping result=image_mismatch", pod_id)
            return False, worker_url
        logger.info("RunPod readiness pod=%s gate=ping result=pass", pod_id)
        readiness = _worker_client().dispatch_ready(
            worker_url, auth_token=self.config.auth_token
        )
        if not readiness or readiness.get("ready") is not True:
            logger.info("RunPod readiness pod=%s gate=dispatch_ready result=waiting", pod_id)
            return False, worker_url
        if readiness.get("backend") != "runpod-cpu-pod":
            logger.warning(
                "RunPod readiness pod=%s gate=dispatch_ready result=backend_mismatch", pod_id
            )
            return False, worker_url
        if readiness.get("jobs_api_contract") != WORKER_JOBS_API_CONTRACT:
            logger.warning(
                "RunPod readiness pod=%s gate=dispatch_ready result=contract_mismatch", pod_id
            )
            return False, worker_url
        if expected and readiness.get("image_version") != expected:
            logger.warning(
                "RunPod readiness pod=%s gate=dispatch_ready result=image_mismatch", pod_id
            )
            return False, worker_url
        logger.info("RunPod readiness pod=%s gate=dispatch_ready result=ready", pod_id)
        return True, worker_url

    def _terminate_and_account(
        self,
        record: lifecycle.PodRecord,
        *,
        reason: str,
        job: dict[str, Any],
        pod_id_override: str | None = None,
    ) -> None:
        pod_id = pod_id_override or record.pod_id
        if not pod_id:
            raise ReviewRequired(
                f"lifecycle {record.id} has no pod ID; ownership reconciliation required"
            )
        lifecycle.mark_terminating(record.id, reason)
        if not pod_client.terminate_pod(pod_id):
            raise ReviewRequired(f"RunPod did not confirm deletion of {pod_id}")
        if not pod_client.confirm_pod_absent(pod_id):
            raise ReviewRequired(
                f"RunPod accepted deletion of {pod_id}, but absence is not confirmed"
            )
        ended = self.now()
        lifecycle.mark_terminated(record.id, now=ended)
        started = _parse_db_time(record.created_at)
        # The configured rate is a hard conservative ceiling. Use the actual
        # allocation rate when known; falling back to the ceiling ensures an
        # exceptional cleanup path cannot under-record spend.
        hourly_rate = self._pod_hourly_rates.pop(pod_id, self.config.hourly_rate_usd)
        cost = max(0.0, (ended - started).total_seconds()) / 3600 * hourly_rate
        from nas_server.database import record_runpod_spend

        record_runpod_spend(
            event_key=f"candidate6:pod:{pod_id}:cpu",
            job_id=str(job.get("id")) if job.get("id") is not None else None,
            backend=f"runpod-cpu-pod:{pod_id}",
            resource_type="cpu",
            target=job.get("target"),
            operation=job.get("operation") or job.get("workflow"),
            cost_usd=cost,
        )

    def _start_once(
        self,
        work_key: str,
        job: dict[str, Any],
        *,
        session_start: datetime | None = None,
        budget_baseline: dict[str, float] | None = None,
    ) -> tuple[lifecycle.PodRecord, str]:
        record = lifecycle.provision(work_key, now=self.now())
        name, env = ownership_for(record)
        env.update({
            "NOVA_WORKER_AUTH_TOKEN": self.config.auth_token,
            "NOVA_RUNPOD_S3_ACCESS_KEY_ID": self.config.s3_access_key_id,
            "NOVA_RUNPOD_S3_SECRET_ACCESS_KEY": self.config.s3_secret_access_key,
            "NOVA_RUNPOD_VOLUME_ID": self.config.volume_id,
            "NOVA_RUNPOD_REGION": self.config.region,
            "NOVA_RUNPOD_API_KEY": self.config.runpod_api_key,
            "NOVA_RUNPOD_RCASTRO_ENDPOINT_ID": self.config.rcastro_endpoint_id,
            "NOVA_RUNPOD_RCASTRO_GPU_FALLBACK_ESTIMATE_USD": str(
                self.config.rcastro_gpu_fallback_estimate_usd
            ),
            "NOVA_RUNPOD_RCASTRO_GPU_RATE_USD_PER_SECOND": str(
                self.config.rcastro_gpu_rate_usd_per_second
            ),
        })
        if session_start is not None:
            env["NOVA_RUNPOD_SESSION_START"] = session_start.astimezone(timezone.utc).isoformat()
        if budget_baseline is not None:
            env["NOVA_RUNPOD_BUDGET_BASELINE"] = json.dumps(
                budget_baseline, sort_keys=True, separators=(",", ":")
            )
        try:
            provisioned = pod_client.create_pod(
                tier=self.config.tier,
                name=name,
                env=env,
                availability_first=self.config.availability_first,
            )
            pod_id = provisioned.pod_id
            actual_rate = provisioned.hourly_rate_usd
            if actual_rate is not None:
                self._pod_hourly_rates[pod_id] = actual_rate
        except Exception as exc:
            # A transport failure can occur after RunPod accepted creation.
            # With no returned pod ID, only #426 can reconcile the positive
            # ownership marker.  Retrying here could create duplicate spend.
            lifecycle.mark_failed(record.id, f"create ambiguous: {exc}")
            raise ReviewRequired(
                f"pod creation outcome is ambiguous for lifecycle {record.id}; do not retry"
            ) from exc
        try:
            record = lifecycle.mark_pod_id(record.id, pod_id)
        except Exception as exc:
            # RunPod returned a real ID, so local persistence failure is no
            # longer ambiguous: delete that exact resource before stopping.
            self._terminate_and_account(
                record,
                pod_id_override=pod_id,
                reason=f"failed to persist pod ID: {exc}",
                job=job,
            )
            raise ReviewRequired("pod ID persistence failed; pod was terminated") from exc
        if actual_rate is None and self.config.availability_first:
            self._terminate_and_account(
                record, reason="allocation price unavailable", job=job
            )
            raise ReviewRequired("allocation price unavailable; pod was terminated")
        if actual_rate is not None and actual_rate > self.config.hourly_rate_usd:
            self._terminate_and_account(
                record,
                reason=(
                    f"allocation rate ${actual_rate:.6f}/hr exceeds configured "
                    f"ceiling ${self.config.hourly_rate_usd:.6f}/hr"
                ),
                job=job,
            )
            raise ReviewRequired("allocation price exceeded configured ceiling; pod was terminated")
        deadline = _parse_db_time(record.ready_deadline or record.created_at)
        consecutive_ready = 0
        while self.now() <= deadline:
            try:
                ready, worker_url = self._worker_ready(pod_id)
            except Exception:
                ready, worker_url = False, None
            if ready and worker_url:
                consecutive_ready += 1
                if consecutive_ready >= READY_CONFIRMATIONS:
                    return lifecycle.mark_ready(record.id, now=self.now()), worker_url
            else:
                consecutive_ready = 0
            self.sleep(self.config.startup_poll_seconds)
        lifecycle.mark_failed(record.id, "startup readiness deadline exceeded")
        raise DispatchError("startup readiness deadline exceeded")

    def run(self, *, work_key: str, job: dict[str, Any], session_start: datetime) -> dict[str, Any]:
        """Run one job; retry only a confirmed-terminated startup failure."""
        if spend_policy.is_work_blocked(work_key):
            raise ReviewRequired(f"work_key {work_key!r} is blocked pending review")

        for attempt in (1, 2):
            # Recheck against the SAME persisted logical-session boundary
            # before a retry: the failed first pod's recorded runtime now
            # counts toward all caps and may make a replacement inadmissible.
            admission = spend_policy.check_admission(
                session_start=session_start,
                estimated_cost_usd=self.config.estimated_cost_usd,
                now=self.now(),
            )
            if not admission.allowed:
                raise DispatchError(f"RunPod admission refused: {admission.violated_cap}")
            record: lifecycle.PodRecord | None = None
            try:
                record, worker_url = self._start_once(
                    work_key,
                    job,
                    session_start=session_start,
                    budget_baseline=admission.projected_totals,
                )
            except ReviewRequired:
                raise
            except DispatchError as exc:
                record = lifecycle.get_active_pod_for_work(work_key)
                if record is None:
                    raise ReviewRequired("startup failed without a lifecycle record") from exc
                self._terminate_and_account(record, reason=str(exc), job=job)
                decision = spend_policy.record_pod_failure(work_key, str(exc))
                if decision.action != "retry" or attempt == 2:
                    raise ReviewRequired("two consecutive startup failures; review required") from exc
                continue

            claimed = lifecycle.claim_for_job(record.id, str(job.get("id") or work_key), now=self.now())
            backend = f"runpod-cpu-pod:{claimed.pod_id}"
            remote_id = _worker_client().dispatch(
                worker_url,
                job,
                auth_token=self.config.auth_token,
                backend=backend,
            )
            if remote_id is None:
                self._terminate_and_account(claimed, reason="dispatch acceptance ambiguous", job=job)
                raise ReviewRequired("dispatch acceptance is ambiguous; automatic retry forbidden")

            deadline = self.now().timestamp() + self.config.job_timeout_seconds
            while self.now().timestamp() <= deadline:
                status = _worker_client().poll(
                    worker_url,
                    remote_id,
                    auth_token=self.config.auth_token,
                    backend=backend,
                )
                try:
                    self._reconcile_gpu_spend(status, job=job)
                except Exception as exc:
                    self._terminate_and_account(
                        claimed, reason="GPU spend reconciliation failed", job=job
                    )
                    raise ReviewRequired(
                        "GPU spend reconciliation failed; pod was terminated"
                    ) from exc
                if status and status.get("done"):
                    lifecycle.release_after_job(record.id, now=self.now())
                    spend_policy.clear_retry_state(work_key)
                    self._terminate_and_account(
                        lifecycle.get_active_pod_for_work(work_key) or record,
                        reason="single-job worker complete",
                        job=job,
                    )
                    return status
                self.sleep(self.config.job_poll_seconds)

            _worker_client().abort(worker_url, remote_id, auth_token=self.config.auth_token)
            self._terminate_and_account(claimed, reason="job timeout", job=job)
            raise ReviewRequired("job outcome timed out; automatic retry forbidden")

        raise AssertionError("unreachable")

    @staticmethod
    def _reconcile_gpu_spend(status: dict[str, Any] | None, *, job: dict[str, Any]) -> None:
        """Persist worker-reported Serverless charges in the VM shared ledger."""
        if not status:
            return
        from nas_server.database import record_runpod_spend

        for event in status.get("runpod_gpu_spend", []):
            record_runpod_spend(
                event_key=str(event["event_key"]),
                job_id=str(job.get("id")) if job.get("id") is not None else None,
                backend=str(event["backend"]),
                resource_type="gpu",
                target=job.get("target"),
                operation=str(event["operation"]),
                cost_usd=float(event["cost_usd"]),
            )


def config_from_settings(settings: dict[str, Any]) -> DispatchConfig:
    """Build a fail-closed controller config from validated NOVA settings."""
    if not reconciliation_backstop_ready(settings):
        raise DispatchError("RunPod reconciliation backstop is absent, stale, or unhealthy")
    availability_first = settings.get("runpod_cpu_availability_first", True) is True
    tier = str(settings.get("runpod_cpu_pod_tier", "")).strip() or None
    if not availability_first and not tier:
        raise DispatchError("runpod_cpu_pod_tier is required when CPU dispatch is enabled")
    estimate = float(settings.get("runpod_cpu_dispatch_estimate_usd", 0.0))
    hourly = float(settings.get("runpod_cpu_pod_hourly_rate_usd", 0.0))
    gpu_estimate = float(settings.get("runpod_rcastro_gpu_fallback_estimate_usd", 0.0))
    gpu_rate = float(settings.get("runpod_rcastro_gpu_rate_usd_per_second", 0.0))
    if estimate <= 0 or hourly <= 0 or gpu_estimate <= 0 or gpu_rate <= 0:
        raise DispatchError(
            "positive RunPod CPU/GPU estimates and rates are required"
        )
    return DispatchConfig(
        tier=tier,
        availability_first=availability_first,
        estimated_cost_usd=estimate,
        hourly_rate_usd=hourly,
        auth_token=str(settings.get("worker_auth_token", "")).strip(),
        s3_access_key_id=str(settings.get("runpod_s3_access_key_id", "")).strip(),
        s3_secret_access_key=str(settings.get("runpod_s3_secret_access_key", "")).strip(),
        runpod_api_key=str(settings.get("runpod_api_key", "")).strip(),
        rcastro_endpoint_id=str(settings.get("runpod_rcastro_endpoint_id", "")).strip(),
        rcastro_gpu_fallback_estimate_usd=gpu_estimate,
        rcastro_gpu_rate_usd_per_second=gpu_rate,
        volume_id=str(settings.get("runpod_rcastro_volume_id", "")).strip(),
        region=str(settings.get("runpod_rcastro_region", "")).strip(),
        expected_image_version=(
            str(settings.get("runpod_cpu_expected_image_version", "")).strip() or None
        ),
    )

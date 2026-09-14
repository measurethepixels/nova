"""Thin client for RunPod's on-demand Pod REST API.

This module provisions and inspects disposable CPU Pods.  It deliberately
does not own NOVA's pod lifecycle state machine, queue integration, spend
accounting, or worker-level ``/health`` probe.  Callers combine those pieces
and must not mark a pod READY until both this platform status and the worker's
authenticated health endpoint succeed.

RunPod's current control-plane API is ``https://api.runpod.io/v2``.  This is
distinct from the ``api.runpod.ai`` Serverless job-execution host used by the
worker clients elsewhere in this repository.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.runpod.io/v2"
REQUEST_TIMEOUT_S = 30
# urllib.request sends no User-Agent by default, which RunPod's Cloudflare
# front end blocks outright (403, "error code: 1010") regardless of a valid
# API key or which API version is called -- live-verified 2026-09-08: the
# identical request against this exact v2 endpoint succeeds with a real
# User-Agent and fails without one. This is the actual root cause of the
# reconciliation heartbeat's persistent "inventory_failed" state; the v1->v2
# migration (#632/#650) was a real, separate fix but did not address this.
USER_AGENT = "NOVA-RunPod-PodClient/1.0"
CPU_TIERS = frozenset({"cpu3c", "cpu3g", "cpu3m", "cpu5c", "cpu5g", "cpu5m"})
_POD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class RunPodPodClientError(RuntimeError):
    """A configuration, transport, protocol, or RunPod API failure."""


@dataclass(frozen=True)
class ProvisionedPod:
    pod_id: str
    hourly_rate_usd: float | None


def _settings() -> dict[str, Any]:
    from nas_server.config import settings

    return settings


def _credentials() -> tuple[str, dict[str, Any]]:
    settings = _settings()
    api_key = str(settings.get("runpod_api_key", "")).strip()
    if not api_key:
        raise RunPodPodClientError("runpod_api_key is not configured")
    return api_key, settings


def _error_detail(body: bytes) -> str:
    if not body:
        return "no response body"
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")[:300]
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            if payload.get(key):
                return str(payload[key])[:300]
    return str(payload)[:300]


def _request(
    method: str,
    path: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    accepted_statuses: frozenset[int],
    allow_list: bool = False,
) -> tuple[int, dict[str, Any] | list[Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        body = exc.read()
        if exc.code == 404 and 404 in accepted_statuses:
            return 404, {}
        raise RunPodPodClientError(
            f"RunPod {method} {path} failed with HTTP {exc.code}: "
            f"{_error_detail(body)}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RunPodPodClientError(
            f"RunPod {method} {path} transport failure: {exc}"
        ) from exc

    if status not in accepted_statuses:
        raise RunPodPodClientError(
            f"RunPod {method} {path} returned unexpected HTTP {status}: "
            f"{_error_detail(body)}"
        )
    if not body:
        return status, {}
    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunPodPodClientError(
            f"RunPod {method} {path} returned invalid JSON"
        ) from exc
    if not isinstance(decoded, dict) and not (allow_list and isinstance(decoded, list)):
        raise RunPodPodClientError(
            f"RunPod {method} {path} returned a non-object JSON response"
        )
    return status, decoded


def _normalized_env(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        result = {}
        for item in value:
            if isinstance(item, dict) and item.get("key") is not None:
                result[str(item["key"])] = str(item.get("value", ""))
        return result
    return {}


def list_pods() -> list[dict[str, Any]]:
    """Return normalized RunPod Pod inventory, including ownership env.

    A failure raises rather than returning an empty list.  Reconciliation
    must distinguish "inventory unavailable" from authoritative "no pods";
    treating those as equivalent could trigger destructive false positives.
    """
    api_key, _ = _credentials()
    _, payload = _request(
        "GET", "/pods?includeClusterPods=false", api_key=api_key,
        accepted_statuses=frozenset({200}),
    )
    raw_pods = payload.get("pods") if isinstance(payload, dict) else payload
    if not isinstance(raw_pods, list):
        raise RunPodPodClientError("RunPod pod inventory did not contain a list")
    pods = []
    for raw in raw_pods:
        if not isinstance(raw, dict):
            raise RunPodPodClientError("RunPod pod inventory contained a non-object")
        pod_id = raw.get("id")
        if not isinstance(pod_id, str) or not pod_id.strip():
            raise RunPodPodClientError("RunPod pod inventory entry has no pod ID")
        pods.append({
            "pod_id": pod_id.strip(),
            "name": raw.get("name"),
            "desired_status": str(raw.get("status", "")).upper(),
            "env": _normalized_env(raw.get("env")),
        })
    return pods


def _pod_path(pod_id: str) -> str:
    pod_id = pod_id.strip()
    if not pod_id:
        raise ValueError("pod_id must not be empty")
    if not _POD_ID_RE.fullmatch(pod_id):
        raise ValueError("pod_id contains unsupported characters")
    return f"/pods/{quote(pod_id, safe='')}"


def _availability_rank(value: Any) -> int | None:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(value).upper())


def _select_available_cpu_tier(api_key: str, vcpu_count: int) -> str:
    """Choose one allowed flavor from authoritative, read-only v2 catalog data."""
    query = urlencode({
        "include": "AVAILABILITY", "product": "POD", "vcpuCount": vcpu_count,
    })
    _, payload = _request(
        "GET", f"/catalog/cpus?{query}", api_key=api_key,
        accepted_statuses=frozenset({200}),
    )
    raw_cpus = payload.get("cpus") if isinstance(payload, dict) else None
    if not isinstance(raw_cpus, list):
        raise RunPodPodClientError("RunPod CPU catalog did not contain a list")
    candidates: list[tuple[int, str]] = []
    for raw in raw_cpus:
        if not isinstance(raw, dict):
            raise RunPodPodClientError("RunPod CPU catalog contained a non-object")
        tier = raw.get("id")
        rank = _availability_rank(raw.get("availability"))
        if tier in CPU_TIERS and rank is not None:
            candidates.append((rank, tier))
    if not candidates:
        raise RunPodPodClientError(
            "RunPod CPU catalog reported no available allowed flavor"
        )
    return min(candidates)[1]


def create_pod(
    *,
    tier: str | None,
    name: str,
    image: str | None = None,
    env: dict[str, str] | None = None,
    availability_first: bool = False,
) -> ProvisionedPod:
    """Provision a CPU Pod and return its ID and allocation price.

    ``tier`` is one of RunPod's documented CPU flavor IDs.  ``image`` may be
    supplied per call; otherwise ``runpod_cpu_pod_image`` is required in
    settings.  ``runpod_cpu_pod_vcpu_count`` must explicitly size the Pod;
    NOVA never relies on RunPod's API default.  This call raises on all
    failures and never mutates NOVA's separate lifecycle state.
    """
    if not availability_first and tier not in CPU_TIERS:
        choices = ", ".join(sorted(CPU_TIERS))
        raise ValueError(f"unsupported RunPod CPU tier {tier!r}; choose {choices}")
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > 191:
        raise ValueError("name must contain at most 191 characters")
    normalized_env: dict[str, str] = {}
    for key, value in (env or {}).items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"unsupported environment variable name {key!r}")
        if not isinstance(value, str):
            raise ValueError(f"environment variable {key!r} must have a string value")
        if not value or len(value) > 4096 or "\x00" in value:
            raise ValueError(f"environment variable {key!r} has an unsupported value")
        normalized_env[key] = value

    api_key, settings = _credentials()
    resolved_image = (image or str(settings.get("runpod_cpu_pod_image", ""))).strip()
    if not resolved_image:
        raise RunPodPodClientError(
            "CPU Pod image is not configured; pass image or set runpod_cpu_pod_image"
        )
    registry_auth_id = str(settings.get("runpod_cpu_registry_auth_id", "")).strip()
    if not registry_auth_id:
        raise RunPodPodClientError(
            "runpod_cpu_registry_auth_id is required for the private CPU Pod image"
        )
    vcpu_count = settings.get("runpod_cpu_pod_vcpu_count", 0)
    if (
        isinstance(vcpu_count, bool)
        or not isinstance(vcpu_count, int)
        or not 2 <= vcpu_count <= 128
        or vcpu_count & (vcpu_count - 1)
    ):
        raise RunPodPodClientError(
            "runpod_cpu_pod_vcpu_count must be a power-of-two integer from 2 through 128"
        )
    selected_tier = (
        _select_available_cpu_tier(api_key, vcpu_count)
        if availability_first else tier
    )
    port = int(settings.get("runpod_cpu_pod_port", 8002))
    payload = {
        "cpu": {"id": selected_tier, "vcpuCount": vcpu_count},
        "image": resolved_image,
        "registry": registry_auth_id,
        "name": name,
        "ports": [f"{port}/http"],
    }
    if normalized_env:
        payload["env"] = normalized_env
    _, response = _request(
        "POST",
        "/pods",
        api_key=api_key,
        payload=payload,
        accepted_statuses=frozenset({201}),
    )
    pod_id = response.get("id")
    if not isinstance(pod_id, str) or not pod_id.strip():
        raise RunPodPodClientError("RunPod create response did not contain a pod ID")
    raw_rate = response.get("cost")
    hourly_rate: float | None = None
    if raw_rate is not None:
        try:
            hourly_rate = float(raw_rate)
        except (TypeError, ValueError):
            hourly_rate = None
        if hourly_rate is not None and (
            not math.isfinite(hourly_rate) or hourly_rate <= 0
        ):
            hourly_rate = None
    return ProvisionedPod(pod_id=pod_id.strip(), hourly_rate_usd=hourly_rate)


def get_pod_health(pod_id: str) -> dict[str, Any]:
    """Return normalized platform status for a Pod.

    ``reachable`` means RunPod reports the Pod's status as RUNNING.  The v2
    runtime block may be absent while a Pod starts, so ports do not gate a
    proxy probe.  It does *not* mean NOVA's
    worker endpoint answered; callers must probe ``worker_url`` with
    :mod:`nas_server.worker_client` before marking the lifecycle READY.
    """
    api_key, settings = _credentials()
    _, pod = _request(
        "GET",
        _pod_path(pod_id),
        api_key=api_key,
        accepted_statuses=frozenset({200}),
    )
    desired_status = str(pod.get("status", "")).upper()
    running = desired_status == "RUNNING"
    port = int(settings.get("runpod_cpu_pod_port", 8002))
    normalized_pod_id = pod_id.strip()
    worker_url = f"https://{normalized_pod_id}-{port}.proxy.runpod.net"
    return {
        "pod_id": str(pod.get("id") or pod_id),
        "desired_status": desired_status,
        "running": running,
        "reachable": running,
        "worker_url": worker_url,
        "public_ip": _runtime_public_ip(pod.get("runtime")),
        "port_mappings": _runtime_port_mappings(pod.get("runtime")),
        "cpu_tier": (
            pod["cpu"].get("id") if isinstance(pod.get("cpu"), dict) else None
        ),
    }


def _runtime_ports(runtime: Any) -> list[dict[str, Any]]:
    if not isinstance(runtime, dict) or not isinstance(runtime.get("ports"), list):
        return []
    return [item for item in runtime["ports"] if isinstance(item, dict)]


def _runtime_public_ip(runtime: Any) -> str | None:
    return next((item.get("ip") for item in _runtime_ports(runtime)
                 if isinstance(item.get("ip"), str) and item["ip"]), None)


def _runtime_port_mappings(runtime: Any) -> dict[str, int]:
    result = {}
    for item in _runtime_ports(runtime):
        private, public = item.get("private"), item.get("public")
        if isinstance(private, int) and isinstance(public, int):
            result[str(private)] = public
    return result


def terminate_pod(pod_id: str) -> bool:
    """Delete a Pod and return whether RunPod confirms deletion.

    RunPod documents only HTTP 204 as successful deletion.  Every other
    result, including an undocumented HTTP 404, raises and must leave the
    lifecycle unconfirmed unless a separate read proves absence.
    """
    api_key, _ = _credentials()
    status, _ = _request(
        "DELETE",
        _pod_path(pod_id),
        api_key=api_key,
        accepted_statuses=frozenset({204}),
    )
    return status == 204


def confirm_pod_absent(pod_id: str) -> bool:
    """Return true only when an authoritative follow-up GET reports 404."""
    api_key, _ = _credentials()
    status, _ = _request(
        "GET", _pod_path(pod_id), api_key=api_key,
        accepted_statuses=frozenset({200, 404}),
    )
    return status == 404

"""Typed, validated application settings with a mapping-compatible runtime view."""

from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable


SETTINGS_PATH = os.environ.get(
    "SEESTAR_SETTINGS",
    os.path.join(os.path.expanduser("~"), "seestar_database", "settings.json"),
)

SECRET_FIELDS = frozenset(
    {
        "anthropic_api_key",
        "telegram_token",
        "telegram_chat_id",
        "telegram_api_id",
        "telegram_api_hash",
        "youtube_api_key",
        "runpod_api_key",
        "runpod_s3_access_key_id",
        "runpod_s3_secret_access_key",
    }
)
DEPRECATED_FIELDS = frozenset({"siril_path"})
PATH_FIELDS = frozenset(
    {
        "calibration_library_path",
        "db_path",
        "gaia_db_path",
        "astap_star_db_path",
        "local_workdir",
        "nas_work_path",
        "nina_calibration_path",
        "nina_capture_path",
        "pi_binary",
        "pixinsight_cache_dir",
        "relay_dir",
        "seestar_incoming_path",
        "seestar_library_path",
        "telegram_archive_session_path",
        "telegram_archive_dir",
        "telegram_archive_db_path",
    }
)


class SettingsValidationError(ValueError):
    """Raised when settings JSON does not match the supported schema."""


class SettingsDeprecationWarning(UserWarning):
    """Visible warning for a supported legacy settings key."""


DEFAULT_OWNER_PROFILE: dict[str, Any] = {
    "display_name": "NOVA Operator",
    "location_label": "",
    "bortle_class": None,
    "telescope_model": "SeeStar S50",
    "show_location_publicly": False,
}


@dataclass(frozen=True)
class SettingsModel:
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    db_path: str = field(
        default_factory=lambda: str(
            Path.home() / "seestar_database" / "astro_data.db"
        )
    )
    seestar_incoming_path: str = "/mnt/seestar/incoming"
    seestar_library_path: str = "/mnt/seestar/library"
    nas_work_path: str = "/mnt/nas_data/_stack_work"
    local_workdir: str = "/tmp/ap_work"
    calibration_library_path: str = ""
    pi_binary: str = "/opt/PixInsight/bin/PixInsight"
    pixinsight_cache_dir: str = ""
    gaia_db_path: str = ""
    graxpert_bin: str = "GraXpert"
    cosmicclarity_bin: str = "cosmicclarity"
    astap_ingest_solve: bool = True
    astap_star_db_path: str = ""
    observer_lat: float = 0.0
    observer_lon: float = 0.0
    observer_elevation_m: int = 0
    observer_horizon: list[list[float]] = field(default_factory=list)
    stability_wait_seconds: int = 60
    web_link_host: str = ""
    server_host: str = "http://localhost:8000"
    vm_url: str = ""
    worker_name: str = "laptop"
    worker_port: int = 8001
    worker_auth_token: str = ""
    remote_workers: list[dict[str, Any]] = field(default_factory=list)
    nina_vm_ip: str = "127.0.0.1"
    nina_api_port: int = 1888
    nina_capture_path: str = ""
    nina_calibration_path: str = ""
    anthropic_api_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_archive_session_path: str = ""
    telegram_archive_dir: str = "/mnt/nas_data/telegram"
    telegram_archive_db_path: str = field(
        default_factory=lambda: str(
            Path.home() / "seestar_database" / "telegram_archive.sqlite"
        )
    )
    youtube_api_key: str = ""
    youtube_channel_id: str = ""
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_vision_model: str = ""
    relay_watcher_enabled: bool = False
    relay_dir: str = ""
    codex_relay_dispatcher_enabled: bool = False
    claude_relay_dispatcher_enabled: bool = False
    auto_assess: bool = True
    stretch_auto_optimize: bool = True
    stretch_vision_tiebreak: bool = False
    subframe_claude_threshold: float = 0.10
    cosmic_clarity_enabled: bool = False
    cosmic_clarity_gpu: bool = True
    auto_process_enabled: bool = False
    auto_process_workflow: str = "seestar_broadband"
    manual_review_enabled: bool = False
    experiment_operational_fallback: dict[str, Any] | None = None
    owner_profile: dict[str, Any] | None = None
    target_priors_enabled: bool = False
    background_neutralize_race_enabled: bool = False
    canonical_framing_auto: bool = True
    exclude_alignment_outliers: bool = True
    pi_register_mem_budget_gb: float = 32.0
    pi_ii_buffer_budget_mb: int = 16384
    pi_ii_stack_size_mb: int = 1024
    rcastro_gpu_enabled: bool = True
    experiment_gpu_parallel_enabled: bool = False
    # Legacy shared cap retained for settings-file compatibility; dual-pool
    # dispatch uses the engine-specific ceilings below.
    experiment_gpu_max_concurrency: int = 2
    experiment_gpu_max_concurrency_rcastro: int = 3
    experiment_gpu_max_concurrency_ml_tools: int = 7
    ml_tools_gpu_enabled: bool = False
    rcastro_bin: str = "rc-astro"
    runpod_api_key: str = ""
    runpod_cpu_pod_image: str = ""
    runpod_cpu_registry_auth_id: str = ""
    runpod_cpu_pod_port: int = 8002
    runpod_cpu_pod_vcpu_count: int = 0
    runpod_cpu_pod_disk_gb: int = 0
    runpod_cpu_dispatch_enabled: bool = False
    runpod_cpu_availability_first: bool = True
    runpod_cpu_pod_tier: str = ""
    runpod_cpu_pod_hourly_rate_usd: float = 0.0
    runpod_cpu_dispatch_estimate_usd: float = 0.0
    runpod_rcastro_gpu_fallback_estimate_usd: float = 0.0
    runpod_rcastro_gpu_rate_usd_per_second: float = 0.0
    runpod_cpu_expected_image_version: str = ""
    runpod_reconciliation_state_path: str = ""
    runpod_s3_access_key_id: str = ""
    runpod_s3_secret_access_key: str = ""
    runpod_rcastro_endpoint_id: str = ""
    runpod_ml_tools_endpoint_id: str = ""
    runpod_rcastro_volume_id: str = ""
    runpod_rcastro_region: str = ""

    def to_mapping(self) -> dict[str, Any]:
        value = asdict(self)
        # Compatibility for the one legacy queue read. Canonical input and all
        # runtime aliases always resolve to the same path.
        value["library_path"] = self.seestar_library_path
        return value


_MODEL_FIELDS = frozenset(item.name for item in fields(SettingsModel))
_ALIASES = {"library_path": "seestar_library_path"}


def _type_error(key: str, expected: str, value: Any) -> SettingsValidationError:
    return SettingsValidationError(
        f"setting '{key}' must be {expected}; got {type(value).__name__}"
    )


def _require_string(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise _type_error(key, "a string", value)
    return value


def _require_path(key: str, value: Any) -> str:
    raw = _require_string(key, value)
    return str(Path(raw).expanduser()) if raw else ""


def _require_bool(key: str, value: Any) -> bool:
    if type(value) is not bool:
        raise _type_error(key, "a boolean", value)
    return value


def _require_int(
    key: str, value: Any, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    if type(value) is not int:
        raise _type_error(key, "an integer", value)
    if minimum is not None and value < minimum:
        raise SettingsValidationError(f"setting '{key}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SettingsValidationError(f"setting '{key}' must be <= {maximum}")
    return value


def _require_float(
    key: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _type_error(key, "a number", value)
    normalized = float(value)
    if minimum is not None and normalized < minimum:
        raise SettingsValidationError(f"setting '{key}' must be >= {minimum}")
    if maximum is not None and normalized > maximum:
        raise SettingsValidationError(f"setting '{key}' must be <= {maximum}")
    return normalized


def _require_horizon(key: str, value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        raise _type_error(key, "a list of [azimuth, altitude] pairs", value)
    result: list[list[float]] = []
    for index, pair in enumerate(value):
        if not isinstance(pair, list) or len(pair) != 2:
            raise SettingsValidationError(
                f"setting '{key}[{index}]' must be an [azimuth, altitude] pair"
            )
        azimuth = _require_float(f"{key}[{index}][0]", pair[0], minimum=0, maximum=360)
        altitude = _require_float(
            f"{key}[{index}][1]", pair[1], minimum=-90, maximum=90
        )
        result.append([azimuth, altitude])
    return result


def _require_remote_workers(key: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _type_error(key, "a list of worker objects", value)
    result: list[dict[str, Any]] = []
    for index, worker in enumerate(value):
        if not isinstance(worker, dict):
            raise _type_error(f"{key}[{index}]", "an object", worker)
        for required in ("name", "url"):
            if required not in worker:
                raise SettingsValidationError(
                    f"setting '{key}[{index}]' is missing '{required}'"
                )
            _require_string(f"{key}[{index}].{required}", worker[required])
        if "enabled" in worker:
            _require_bool(f"{key}[{index}].enabled", worker["enabled"])
        if "dispatch_types" in worker:
            dispatch_types = worker["dispatch_types"]
            if not isinstance(dispatch_types, list) or not all(
                isinstance(item, str) for item in dispatch_types
            ):
                raise _type_error(
                    f"{key}[{index}].dispatch_types", "a list of strings", dispatch_types
                )
        result.append(dict(worker))
    return result


_BOOLEAN_FIELDS = frozenset(
    {
        "astap_ingest_solve",
        "auto_assess",
        "auto_process_enabled",
        "canonical_framing_auto",
        "claude_relay_dispatcher_enabled",
        "codex_relay_dispatcher_enabled",
        "cosmic_clarity_enabled",
        "cosmic_clarity_gpu",
        "exclude_alignment_outliers",
        "experiment_gpu_parallel_enabled",
        "manual_review_enabled",
        "target_priors_enabled",
        "background_neutralize_race_enabled",
        "ml_tools_gpu_enabled",
        "rcastro_gpu_enabled",
        "runpod_cpu_dispatch_enabled",
        "runpod_cpu_availability_first",
        "relay_watcher_enabled",
        "stretch_auto_optimize",
        "stretch_vision_tiebreak",
    }
)
_INTEGER_RANGES = {
    "api_port": (1, 65535),
    "experiment_gpu_max_concurrency_rcastro": (1, 16),
    "experiment_gpu_max_concurrency_ml_tools": (1, 16),
    "experiment_gpu_max_concurrency": (1, 16),
    "nina_api_port": (1, 65535),
    "observer_elevation_m": (-500, 10000),
    "pi_ii_buffer_budget_mb": (1, None),
    "pi_ii_stack_size_mb": (1, None),
    "stability_wait_seconds": (0, None),
    "worker_port": (1, 65535),
    "runpod_cpu_pod_port": (1, 65535),
    "runpod_cpu_pod_vcpu_count": (0, 128),
    "runpod_cpu_pod_disk_gb": (0, 500),
    "telegram_api_id": (0, None),
}
_FLOAT_RANGES = {
    "observer_lat": (-90.0, 90.0),
    "observer_lon": (-180.0, 180.0),
    "pi_register_mem_budget_gb": (0.01, None),
    "subframe_claude_threshold": (0.0, 1.0),
    "runpod_cpu_pod_hourly_rate_usd": (0.0, None),
    "runpod_cpu_dispatch_estimate_usd": (0.0, None),
    "runpod_rcastro_gpu_fallback_estimate_usd": (0.0, None),
    "runpod_rcastro_gpu_rate_usd_per_second": (0.0, None),
}


def _validate_field(key: str, value: Any) -> Any:
    if key in _BOOLEAN_FIELDS:
        return _require_bool(key, value)
    if key in _INTEGER_RANGES:
        minimum, maximum = _INTEGER_RANGES[key]
        return _require_int(key, value, minimum=minimum, maximum=maximum)
    if key in _FLOAT_RANGES:
        minimum, maximum = _FLOAT_RANGES[key]
        return _require_float(key, value, minimum=minimum, maximum=maximum)
    if key == "observer_horizon":
        return _require_horizon(key, value)
    if key == "remote_workers":
        return _require_remote_workers(key, value)
    if key in {"experiment_operational_fallback", "owner_profile"}:
        if value is not None and not isinstance(value, dict):
            raise _type_error(key, "an object or null", value)
        return value
    if key in PATH_FIELDS:
        return _require_path(key, value)
    return _require_string(key, value)


def resolved_owner_profile(
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the small public/operator profile with anonymous safe defaults."""
    runtime = settings if source is None else source
    raw = runtime.get("owner_profile") if isinstance(runtime, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    profile = dict(DEFAULT_OWNER_PROFILE)

    for key in ("display_name", "location_label", "telescope_model"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            profile[key] = value.strip()

    bortle = raw.get("bortle_class")
    if isinstance(bortle, int) and not isinstance(bortle, bool) and 1 <= bortle <= 9:
        profile["bortle_class"] = bortle
    profile["show_location_publicly"] = raw.get("show_location_publicly") is True
    return profile


def validate_settings(
    saved: dict[str, Any],
    *,
    warn: Callable[[str], None] | None = None,
) -> SettingsModel:
    """Validate raw JSON settings and return the typed, canonical model."""
    if not isinstance(saved, dict):
        raise SettingsValidationError("settings file must contain a JSON object")

    warn = warn or (
        lambda message: warnings.warn(
            message,
            SettingsDeprecationWarning,
            stacklevel=2,
        )
    )
    unknown = set(saved) - _MODEL_FIELDS - set(_ALIASES) - DEPRECATED_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise SettingsValidationError(f"unknown setting(s): {names}")

    normalized = dict(saved)
    for deprecated in sorted(DEPRECATED_FIELDS & set(normalized)):
        normalized.pop(deprecated)
        warn(f"setting '{deprecated}' is deprecated and ignored")

    if "library_path" in normalized:
        legacy_value = normalized.pop("library_path")
        if "seestar_library_path" not in normalized:
            normalized["seestar_library_path"] = legacy_value
        warn("setting 'library_path' is deprecated; use 'seestar_library_path'")

    defaults = asdict(SettingsModel())
    validated = {
        key: _validate_field(key, normalized.get(key, default))
        for key, default in defaults.items()
    }
    return SettingsModel(**validated)


def redact_settings(value: SettingsModel | dict[str, Any]) -> dict[str, Any]:
    """Return a safe mapping with credential values replaced, never copied."""
    result = value.to_mapping() if isinstance(value, SettingsModel) else dict(value)
    for key in SECRET_FIELDS:
        if key in result:
            result[key] = "<redacted>" if result[key] else ""
    return result


DEFAULTS = SettingsModel().to_mapping()


def load_settings(path: str | os.PathLike[str] = SETTINGS_PATH) -> dict[str, Any]:
    settings_path = Path(path)
    if not settings_path.exists():
        print(f"[config] No settings file found at {settings_path}.")
        print("[config] Copy settings.example.json and edit it.")
        sys.exit(1)
    try:
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        model = validate_settings(saved)
    except (json.JSONDecodeError, SettingsValidationError) as exc:
        raise SettingsValidationError(f"{settings_path}: {exc}") from exc
    return model.to_mapping()


def save_setting(key: str, value: Any) -> None:
    """Validate and persist one key, then update the runtime mapping."""
    settings_path = Path(SETTINGS_PATH)
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SettingsValidationError("settings file must contain a JSON object")
    data[key] = value
    model = validate_settings(data)
    settings_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    settings.clear()
    settings.update(model.to_mapping())


settings = load_settings()

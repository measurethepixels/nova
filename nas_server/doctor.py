"""Read-only environment and capability diagnostics.

The doctor intentionally reports availability without changing settings,
starting services, or gating pipeline execution.  Every probe is isolated so a
missing optional dependency cannot prevent the rest of the report.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping


def _result(name: str, available: bool | None, detail: str, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": name,
        "available": available,
        "detail": detail,
    }
    value.update(extra)
    return value


def _version(command: str, *, env: Mapping[str, str] | None = None) -> str | None:
    try:
        completed = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (completed.stdout or completed.stderr).strip().splitlines()
    return text[0][:200] if text else None


def probe_executable(name: str, configured: str) -> dict[str, Any]:
    """Report whether an executable exists, without executing it for success."""
    configured = str(configured or "").strip()
    if not configured:
        return _result(name, False, "no executable configured")
    resolved = shutil.which(configured)
    if resolved is None and ("/" in configured or configured.startswith("~")):
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = str(candidate)
    if resolved is None:
        return _result(name, False, f"not found: {configured}", configured=configured)
    version = _version(resolved)
    return _result(
        name,
        True,
        f"found at {resolved}" + (f" ({version})" if version else ""),
        configured=configured,
        path=resolved,
        version=version,
    )


def probe_siril() -> dict[str, Any]:
    """Report Siril availability and the supported headless-version contract."""
    result = probe_executable("siril", "siril-cli")
    result["minimum_version"] = "1.4.3"
    version_text = result.get("version") or ""
    match = re.search(r"(?:siril\s+)?(\d+)\.(\d+)\.(\d+)", version_text, re.I)
    if not result["available"]:
        result["compatible"] = False
        return result
    if match is None:
        result["compatible"] = None
        result["detail"] += "; compatibility unknown (could not parse version; need >= 1.4.3)"
        return result
    installed = tuple(int(value) for value in match.groups())
    result["compatible"] = installed >= (1, 4, 3)
    if not result["compatible"]:
        result["detail"] += "; unsupported for headless stacking (need >= 1.4.3)"
    return result


def probe_pixinsight(configured: str) -> dict[str, Any]:
    """Like probe_executable, but applies the headless env overlay before
    running --version -- PixInsight's bundled libs aren't on the default
    library path, so a bare subprocess call reports a spurious load error
    even when the binary is genuinely runnable (see nas_server.pixinsight
    PI_ENV_OVERLAY, which every real invocation already applies)."""
    configured = str(configured or "").strip()
    if not configured:
        return _result("pixinsight", False, "no executable configured")
    resolved = shutil.which(configured)
    if resolved is None and ("/" in configured or configured.startswith("~")):
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = str(candidate)
    if resolved is None:
        return _result("pixinsight", False, f"not found: {configured}", configured=configured)
    try:
        from nas_server.pixinsight import PI_ENV_OVERLAY
        env = {**os.environ, **PI_ENV_OVERLAY}
    except ImportError:
        env = None
    version = _version(resolved, env=env)
    return _result(
        "pixinsight",
        True,
        f"found at {resolved}" + (f" ({version})" if version else ""),
        configured=configured,
        path=resolved,
        version=version,
    )


def probe_astap() -> dict[str, Any]:
    """Use the installed VM path first, then the normal PATH fallback."""
    configured = "/opt/astap/astap_cli"
    candidate = Path(configured)
    resolved = (str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK)
                else shutil.which("astap_cli") or shutil.which("astap"))
    if resolved is None:
        return _result("astap", False,
                        "not found: /opt/astap/astap_cli or astap_cli/astap on PATH")
    version = _version(resolved)
    return _result("astap", True, f"found at {resolved}" + (f" ({version})" if version else ""), path=resolved, version=version)


def probe_path(name: str, configured: str, *, kind: str = "path") -> dict[str, Any]:
    configured = str(configured or "").strip()
    if not configured:
        return _result(name, None, f"no {kind} configured")
    path = Path(configured).expanduser()
    if path.exists():
        return _result(name, True, f"exists: {path}", path=str(path))
    return _result(name, False, f"missing: {path}", path=str(path))


def probe_python_package(name: str, module: str) -> dict[str, Any]:
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        found = False
    return _result(
        name,
        found,
        f"Python package {'available' if found else 'not importable'}: {module}",
        module=module,
    )


def probe_gpu() -> dict[str, Any]:
    """Probe CUDA without making GPU support a hard dependency."""
    try:
        import torch  # type: ignore

        available = bool(torch.cuda.is_available())
        detail = f"torch.cuda.is_available()={available}"
        if available:
            detail += f" ({torch.cuda.get_device_name(0)})"
        return _result("cosmic_clarity_gpu", available, detail, source="torch")
    except (ImportError, AttributeError, RuntimeError, OSError):
        pass
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _result("cosmic_clarity_gpu", None, "GPU status unknown (torch/nvidia-smi unavailable)")
    if completed.returncode == 0 and completed.stdout.strip():
        return _result("cosmic_clarity_gpu", True, f"nvidia-smi: {completed.stdout.strip()}", source="nvidia-smi")
    return _result("cosmic_clarity_gpu", False, "nvidia-smi found no usable GPU", source="nvidia-smi")


def probe_nina(settings: Mapping[str, Any]) -> dict[str, Any]:
    ip = str(settings.get("nina_vm_ip", "127.0.0.1"))
    port = int(settings.get("nina_api_port", 1888))
    url = f"http://{ip}:{port}/v2/api"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return _result("nina", True, f"reachable: HTTP {response.status}", url=url)
    except urllib.error.HTTPError as exc:
        return _result("nina", True, f"reachable: HTTP {exc.code}", url=url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _result("nina", False, f"unreachable: {exc}", url=url)


def run_doctor(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe report for all supported optional capabilities."""
    probes: list[dict[str, Any]] = []

    def safe(probe):
        try:
            return probe()
        except Exception as exc:  # a broken optional probe must not abort the report
            return _result(getattr(probe, "__name__", "probe"), None, f"probe error: {exc}")

    probes.append(safe(probe_siril))
    probes.append(safe(lambda: probe_pixinsight(str(settings.get("pi_binary", "")))))
    probes.append(safe(lambda: probe_python_package("saspro", "setiastro.saspro")))
    probes.append(safe(lambda: probe_python_package("imagemm", "setiastro.saspro.mfdeconv")))
    probes.append(safe(probe_astap))

    astap_db = settings.get("astap_star_db_path") or os.environ.get("ASTAP_STAR_DB_PATH", "")
    if astap_db:
        probes.append(safe(lambda: probe_path("astap_star_database", str(astap_db), kind="star database")))
    else:
        probes.append(_result("astap_star_database", None, "unknown: no ASTAP star-database path configured"))

    probes.append(safe(lambda: probe_executable("cosmic_clarity", str(settings.get("cosmicclarity_bin", "cosmicclarity")))))
    probes.append(safe(probe_gpu))
    probes.append(safe(lambda: probe_executable("graxpert", str(settings.get("graxpert_bin", "GraXpert")))))
    probes.append(safe(lambda: probe_path("pixinsight_gaia_database", str(settings.get("gaia_db_path", "")), kind="GAIA database path")))
    probes.append(safe(lambda: probe_executable("ffmpeg", "ffmpeg")))
    probes.append(safe(lambda: _result("anthropic_api_key", bool(settings.get("anthropic_api_key")),
                                       "configured" if settings.get("anthropic_api_key") else "not configured")))
    probes.append(safe(lambda: probe_nina(settings)))

    available = sum(item["available"] is True for item in probes)
    missing = sum(item["available"] is False for item in probes)
    unknown = sum(item["available"] is None for item in probes)
    return {
        "status": "ok",
        "summary": {"total": len(probes), "available": available, "missing": missing, "unknown": unknown},
        "probes": probes,
    }

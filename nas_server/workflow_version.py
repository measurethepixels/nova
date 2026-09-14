"""Single source of truth for the autoprocess workflow version.

The current version is the last entry in ``critiques/workflow_history.json`` (the
append-only, machine-readable history). Bump the pipeline by appending a new entry
there and mirroring it into ``critiques/WORKFLOW_CHANGELOG.md`` — code auto-reads the
latest, so there is exactly one number to change.

Semver policy for a *scoring* pipeline:
  MAJOR — scoring model or step add/remove; scores no longer comparable across versions.
  MINOR — param/threshold/gate tuning that changes output but keeps comparability.
  PATCH — bug fix not expected to change good-run output.
"""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import date
from typing import Any

_HISTORY = Path(__file__).resolve().parent.parent / "critiques" / "workflow_history.json"
_FALLBACK = "1.0.0"

_TOOL_VERSION_COMPONENTS = {
    "pixinsight": "PixInsight",
    "siril": "Siril",
    "setiastrosuitepro": "SASpro",
    "rcastro_local": "RC-Astro CLI",
    "rcastro_runpod": "RC-Astro worker image",
    "graxpert": "GraXpert",
}


def environment_tool_versions(conn, *, captured_on: date | None = None) -> dict[str, str]:
    """Freeze the current checked environment in workflow-history shape."""
    conn.row_factory = sqlite3.Row
    rows = {
        row["name"]: dict(row)
        for row in conn.execute(
            "SELECT name, installed, status, stale, last_successful_check "
            "FROM environment_components"
        )
    }
    snapshot = {
        "detected": (captured_on or date.today()).isoformat(),
        "note": (
            "Authoring-time snapshot from environment_components. Values are frozen "
            "with this workflow version; UNKNOWN means no successful installed identity "
            "was recorded at capture time. Status is not approval."
        ),
    }
    for key, component in _TOOL_VERSION_COMPONENTS.items():
        row = rows.get(component)
        snapshot[key] = (
            str(row["installed"])
            if row and row.get("installed") and not row.get("stale")
            else "UNKNOWN"
        )
    return snapshot


def append_workflow_version(history_path: str | Path, entry: dict[str, Any], conn,
                            *, captured_on: date | None = None) -> None:
    """Append one version with an immutable environment-derived tool snapshot."""
    path = Path(history_path)
    history = json.loads(path.read_text(encoding="utf-8"))
    versions = history.get("versions")
    if not isinstance(versions, list):
        raise ValueError("workflow history versions must be a list")
    required = {"version", "date", "bump", "summary", "rationale",
                "critiques_evaluated", "changes"}
    missing = sorted(required - entry.keys())
    if missing:
        raise ValueError(f"workflow version entry missing: {', '.join(missing)}")
    if "tool_versions" in entry:
        raise ValueError("tool_versions is sourced from environment_components")
    version = str(entry["version"])
    if any(str(item.get("version")) == version for item in versions):
        raise ValueError(f"workflow version {version} already exists")

    snapshot = environment_tool_versions(conn, captured_on=captured_on)
    new_entry = dict(entry)
    new_entry["tool_versions"] = snapshot
    versions.append(new_entry)
    history["current"] = version
    # Compatibility projection for readers that still use the top-level snapshot.
    history["tool_versions"] = snapshot

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(history, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def workflow_version() -> str:
    try:
        data = json.loads(_HISTORY.read_text(encoding="utf-8"))
        versions = data.get("versions", [])
        if versions:
            return str(versions[-1]["version"])
        cur = data.get("current")
        if cur:
            return str(cur)
    except Exception:
        pass
    return _FALLBACK


WORKFLOW_VERSION = workflow_version()

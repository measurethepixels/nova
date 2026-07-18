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
from pathlib import Path

_HISTORY = Path(__file__).resolve().parent.parent / "critiques" / "workflow_history.json"
_FALLBACK = "1.0.0"


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

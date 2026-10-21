"""Dependency-free, fail-closed filter plans for the stack entry point.

Eligibility (quality, explicit exposure, alignment, EQ and files) is decided
once by the caller over all associated targets/panels. Automatic/all requests
split that population; no dominance or mixed-filter mode remains (#1027).
"""
import hashlib
import json
import math
from pathlib import Path


def canonical_filter(value):
    label = str(value or "").strip().upper()
    # Only verified equivalent capture labels; unknown is never broadband.
    return {"IRCUT": "IRCUT", "IR-CUT": "IRCUT", "IR CUT": "IRCUT",
            "LP": "LP"}.get(label)


def plan_filter_stacks(frames, eligible_paths, requested="auto"):
    """Return deterministic single-filter plans, refusing ambiguous metadata.

    An explicit known filter may exclude frames with unknown labels. An
    automatic/all request refuses those frames and unsupported filter routes.
    Source identity covers IDs, paths, exposures and canonical filter labels.
    """
    mode = str(requested or "auto").strip().lower()
    named = None if mode in ("auto", "all", "") else canonical_filter(requested)
    if named is None and mode not in ("auto", "all", ""):
        raise ValueError(f"Unsupported filter {requested!r}; select LP or IRCUT")
    by_path = {}
    for frame in frames:
        path = str(frame["file_path"])
        if path in by_path and by_path[path] != frame:
            raise ValueError(f"Conflicting metadata for source {path}")
        by_path[path] = frame
    groups = {}
    populations = {}
    audit = {}
    available = 0
    for path in sorted({str(p) for p in eligible_paths}):
        if not Path(path).is_file():
            continue
        frame = by_path.get(path)
        if frame is None:
            raise ValueError(f"Missing source metadata for {path}")
        available += 1
        filt = canonical_filter(frame.get("filter"))
        try:
            seconds = float(frame.get("exposure_time"))
        except (TypeError, ValueError):
            seconds = float("nan")
        valid_exposure = math.isfinite(seconds) and seconds > 0
        label = filt or f"UNKNOWN:{frame.get('filter')!r}"
        summary = audit.setdefault(label, {"frames": 0, "integration_seconds": 0.0,
                                           "invalid_exposures": 0})
        summary["frames"] += 1
        if valid_exposure:
            summary["integration_seconds"] += seconds
        else:
            summary["invalid_exposures"] += 1
        if named and filt != named:
            continue
        if filt is None:
            raise ValueError(f"Unknown/missing capture filter for {path}; select an explicit known filter")
        if not valid_exposure:
            raise ValueError(f"Insufficient exposure metadata for {path}")
        source = {"id": frame.get("id"), "path": path, "filter": filt,
                  "exposure_seconds": seconds}
        groups.setdefault(filt, []).append(source)
    if not groups:
        raise ValueError(f"No eligible frames for filter {requested!r}; no fallback")
    for filt, sources in sorted(groups.items()):
        populations[filt] = {"frames": len(sources),
                             "integration_seconds": sum(s["exposure_seconds"] for s in sources)}
    plans = []
    for filt, sources in sorted(groups.items()):
        digest = hashlib.sha256(json.dumps(sources, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        plans.append({"filter": filt, "paths": [s["path"] for s in sources],
                      "selected_ids": [s["id"] for s in sources],
                      **populations[filt], "source_hash": digest,
                      "eligible_filters": audit,
                      "excluded_frames": available - len(sources),
                      "ineligible_or_missing_frames": len(by_path) - available})
    return plans

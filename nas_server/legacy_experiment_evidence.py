"""Read-only, uncertainty-preserving view of legacy experiment evidence."""
from __future__ import annotations

import json


def legacy_evidence_report() -> dict:
    """Report every legacy row without fabricating candidates or provenance."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM experiment_results ORDER BY id")]
        known_runs = {r[0] for r in conn.execute("SELECT experiment_run_id FROM experiment_runs")}
        roster_counts = dict(conn.execute("SELECT experiment_run_id,COUNT(*) FROM experiment_run_roster GROUP BY experiment_run_id"))
        treatment_counts = dict(conn.execute("SELECT experiment_run_id,COUNT(effective_treatment_id) FROM candidate_attempts GROUP BY experiment_run_id"))
    legacy_counts = {}
    for row in rows:
        if row.get("experiment_run_id"):
            legacy_counts[row["experiment_run_id"]] = legacy_counts.get(row["experiment_run_id"], 0) + 1
    groups = {}
    counts = {"complete": 0, "partial": 0, "unavailable": 0}
    for row in rows:
        run_id = row.get("experiment_run_id")
        if run_id and run_id in known_runs:
            provenance_level = "partial"
            denominator_level = ("complete" if roster_counts.get(run_id) == legacy_counts[run_id]
                                 else "partial")
            treatment_level = ("partial" if treatment_counts.get(run_id, 0) >= legacy_counts[run_id]
                               else "unavailable")
            level = "partial"
            key = run_id
        elif run_id:
            level, key = "partial", run_id
            provenance_level = denominator_level = treatment_level = "unavailable"
        else:
            level, key = "unavailable", f"legacy-row:{row['id']}"
            provenance_level = denominator_level = treatment_level = "unavailable"
        counts[level] += 1
        item = {"legacy_row_id": row["id"], "experiment_run_id": run_id,
                "target": row["target"], "step": row["step"],
                "variant_id": row["variant_id"], "winner_recorded": bool(row.get("winner")),
                "reconstruction_level": level,
                "provenance_reconstruction": provenance_level,
                "denominator_reconstruction": denominator_level,
                "treatment_reconstruction": treatment_level,
                "selection_basis_recoverable": False,
                "tool_version": None, "model_version": None,
                "missing_candidates_inferred": False}
        groups.setdefault(key, {"experiment_run_id": run_id,
                                "reconstruction_level": level, "rows": []})["rows"].append(item)
    return {"read_only": True, "legacy_row_count": len(rows),
            "reconstruction_counts": counts, "groups": list(groups.values())}


def report_json() -> str:
    return json.dumps(legacy_evidence_report(), sort_keys=True, indent=2)

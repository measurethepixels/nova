"""
Tool implementations for the local AI agent.

Each function corresponds to one tool the LLM can call. All I/O is plain
Python dicts so they're easy to serialize for the tool-result message.
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# ── CLI whitelist ─────────────────────────────────────────────────────────────

_CLI_ALLOWED = {
    "status", "mounts", "pipeline", "scan", "check",
    "stack", "stack-status", "assess", "queue", "report",
    "processed", "logs", "score",
}

# ── Tool schemas (OpenAI function-calling format) ────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": (
                "Run a read-only SQL SELECT query against the SeeStar astrophotography database. "
                "Tables: targets, light_files, stacked_files, processed_files, claude_assessments, "
                "processing_history, experiment_results, processing_runs, stacking_runs, manual_reviews. "
                "Always use SELECT — never INSERT/UPDATE/DELETE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid SQLite SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Run a write SQL statement (UPDATE, INSERT, or DELETE) against the database. "
                "ALWAYS describe what will change and get user confirmation before calling — "
                "state the SQL and why, then ask 'OK to run?' Wait for approval before calling. "
                "Do NOT use for SELECT — use query_db for reads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The SQL statement to execute (UPDATE/INSERT/DELETE).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English description of what this will change.",
                    },
                },
                "required": ["sql", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cli",
            "description": (
                "Run a seestar CLI command against the server. "
                f"Allowed commands: {', '.join(sorted(_CLI_ALLOWED))}. "
                "Examples: run_cli('status', []) or run_cli('stack', ['M51', '--engine', 'siril'])."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "CLI subcommand name."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional positional/flag arguments.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_script",
            "description": (
                "Write a helper Python script and execute it. Use this for one-off data tasks "
                "not covered by the CLI (e.g., batch-update exclude flags, generate a CSV export). "
                "The script runs in /tmp/agent_scripts/ and may import standard library modules "
                "and nas_server.database / nas_server.config. It must NOT modify nas_server source "
                "files. Output is the stdout/stderr of the script (first 3000 chars)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "One sentence describing what the script does.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python script source.",
                    },
                },
                "required": ["description", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": (
                "Search past Claude assessments, processing evaluations, and step-reasoning "
                "by semantic meaning — not exact keywords. Use for qualitative questions: "
                "which targets had gradient problems, what was the best nebula result, "
                "similar processing challenges to a given target. "
                "Do NOT use for exact counts or numeric filters — use query_db for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_suggestion",
            "description": (
                "Log a suggested improvement or bug fix for the base nas_server Python code. "
                "The agent must NOT modify base code directly — use this to record ideas for "
                "review in Claude Code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Clear description of the issue or improvement.",
                    },
                    "file_hint": {
                        "type": "string",
                        "description": "Relevant file(s) (e.g., 'nas_server/stacker.py').",
                    },
                    "code_snippet": {
                        "type": "string",
                        "description": "Optional suggested code patch or pseudocode.",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_target_context",
            "description": (
                "Get a full context bundle for a specific astrophotography target: folio data "
                "(recommended integration, drizzle benefit, processing tips, known challenges), "
                "the last 3 stacking runs with Claude assessment scores, and the target's "
                "preference/learning score. Call this whenever Henry mentions a target by name "
                "to inform your recommendations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target name with space (e.g. 'M 92', 'NGC 6888').",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tonight_plan",
            "description": (
                "Get tonight's weather forecast and AI-generated observing schedule. "
                "Returns whether conditions are clear, a weather summary, the top scored "
                "targets for tonight, and the time-slotted schedule. Use when Henry asks "
                "what to image, what the plan is, or whether to observe tonight."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_target_history",
            "description": (
                "Get recent stacking and processing history for a target, including Claude "
                "assessment scores per run. Use when Henry asks how a past stack went, "
                "what the scores were, or whether a target needs re-stacking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target name with space (e.g. 'M 51').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of runs to return (default 5).",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_nina_ready",
            "description": (
                "Unblock the NINA sequence on the Windows VM — call this when Henry says "
                "polar alignment is complete, 'aligned', 'polar done', 'ready to go', or "
                "any similar confirmation that he has finished setting up the telescope mount. "
                "This sets the ready flag so NINA will proceed with its sequence."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nina_status",
            "description": (
                "Get the current status of the NINA Windows VM — whether it is reachable, "
                "what the camera is doing, and the current sequence status. Use when Henry "
                "asks about NINA, the Windows VM, the second SeeStar, or whether a NINA "
                "session is running."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ── Implementations ──────────────────────────────────────────────────────────


def query_db(sql: str) -> dict:
    """Execute a read-only SELECT and return rows as list of dicts."""
    sql_stripped = sql.strip()
    if not re.match(r"(?i)^\s*SELECT\b", sql_stripped):
        return {"error": "Only SELECT queries are allowed."}
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            cur = conn.execute(sql_stripped)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(200)]
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def execute_sql(sql: str, description: str) -> dict:
    """Execute a write SQL statement (UPDATE/INSERT/DELETE) and return rows affected."""
    sql_stripped = sql.strip()
    if re.match(r"(?i)^\s*SELECT\b", sql_stripped):
        return {"error": "Use query_db for SELECT statements."}
    if not re.match(r"(?i)^\s*(UPDATE|INSERT|DELETE)\b", sql_stripped):
        return {"error": "Only UPDATE, INSERT, or DELETE statements are allowed."}
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            cur = conn.execute(sql_stripped)
            conn.commit()
            rows_affected = cur.rowcount
        log.info(f"[agent/execute_sql] {rows_affected} rows affected — {description[:100]}")
        return {"rows_affected": rows_affected, "description": description}
    except Exception as e:
        return {"error": str(e)}


def run_cli(command: str, args: list[str] | None = None) -> dict:
    """Run a whitelisted seestar CLI command and return its output."""
    if command not in _CLI_ALLOWED:
        return {"error": f"Command '{command}' is not allowed. Allowed: {sorted(_CLI_ALLOWED)}"}
    cmd = ["seestar", command] + [str(a) for a in (args or [])]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        out = (result.stdout or "") + (result.stderr or "")
        return {
            "exit_code": result.returncode,
            "output": out[:3000],
        }
    except subprocess.TimeoutExpired:
        return {"error": "CLI command timed out after 60s"}
    except FileNotFoundError:
        return {"error": "seestar CLI not found — is /usr/local/bin/seestar installed?"}
    except Exception as e:
        return {"error": str(e)}


def write_script(description: str, code: str) -> dict:
    """Write and execute a helper Python script in a sandbox directory."""
    script_dir = Path("/tmp/agent_scripts")
    script_dir.mkdir(exist_ok=True)
    script_path = script_dir / f"agent_{uuid.uuid4().hex[:8]}.py"

    # Reject attempts to modify source files
    forbidden = re.search(
        r"open\s*\([^)]*nas_server[^)]*['\"][wxa]",
        code, re.IGNORECASE,
    )
    if forbidden:
        return {"error": "Script may not open nas_server source files for writing."}

    script_path.write_text(code)
    log.info(f"[agent] executing helper script: {script_path} — {description}")
    try:
        result = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        out = (result.stdout or "") + (result.stderr or "")
        return {
            "exit_code": result.returncode,
            "output": out[:3000],
            "script": str(script_path),
        }
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out after 30s"}
    except Exception as e:
        return {"error": str(e)}


def semantic_search(query: str, top_k: int = 5) -> dict:
    """Search indexed assessment/processing text by semantic similarity."""
    try:
        from nas_server.rag import semantic_search as _search
        top_k = min(int(top_k), 10)
        results = _search(query, top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "results": []}


def log_suggestion(description: str, file_hint: str = "", code_snippet: str = "") -> dict:
    """Save a code improvement suggestion to the database."""
    try:
        from nas_server.database import add_agent_suggestion
        sid = add_agent_suggestion(description, file_hint, code_snippet)
        log.info(f"[agent] logged suggestion #{sid}: {description[:80]}")
        return {"suggestion_id": sid, "message": "Suggestion logged. View at /suggestions."}
    except Exception as e:
        return {"error": str(e)}


def get_target_context(target: str) -> dict:
    """Full context bundle: folio + recent stacks + preference score."""
    import json as _json
    result: dict = {"target": target}

    try:
        from nas_server.folio_generator import load_folio
        folio = load_folio(target)
        if folio:
            cat = folio.get("catalog", {})
            achiev = folio.get("s50_achievability", {})
            proc = folio.get("processing_notes", {})
            result["folio"] = {
                "common_name": folio.get("common_name"),
                "type": folio.get("type"),
                "object_type": cat.get("object_type"),
                "best_season": cat.get("best_season"),
                "min_integration_hours": achiev.get("min_integration_hours"),
                "recommended_integration_hours": achiev.get("recommended_integration_hours"),
                "drizzle_benefit": achiev.get("drizzle_benefit"),
                "detail_ceiling": achiev.get("detail_ceiling"),
                "stretch_approach": proc.get("stretch_approach"),
                "scnr_amount": (proc.get("color") or {}).get("scnr_amount"),
                "known_challenges": proc.get("known_challenges", []),
                "assessment_anchors": folio.get("assessment_anchors", []),
            }
        else:
            result["folio"] = None
    except Exception as e:
        result["folio_error"] = str(e)

    try:
        from nas_server.database import get_stacking_runs_with_scores
        runs = get_stacking_runs_with_scores(target, limit=3)
        recent = []
        for run in runs:
            scores = {}
            if run.get("claude_scores_json"):
                try:
                    scores = _json.loads(run["claude_scores_json"])
                except Exception:
                    pass
            recent.append({
                "date": (run.get("started_at") or "")[:10],
                "engine": run.get("engine"),
                "frame_count": run.get("frame_count"),
                "drizzle": bool(run.get("drizzle")),
                "hero": bool(run.get("hero")),
                "snr_stack": run.get("snr_stack"),
                "overall_score": scores.get("overall"),
                "recommendation": (run.get("claude_rec") or "")[:200],
            })
        result["recent_stacks"] = recent
    except Exception as e:
        result["recent_stacks_error"] = str(e)

    try:
        from nas_server.database import get_target_learn_scores
        result["pref_score"] = get_target_learn_scores().get(target)
    except Exception as e:
        result["pref_score_error"] = str(e)

    return result


_CLOUD_PCT = {1: 3, 2: 13, 3: 25, 4: 38, 5: 50, 6: 63, 7: 75, 8: 88, 9: 97}
_WIND_LABEL = {1: "calm", 2: "light", 3: "gentle", 4: "moderate",
               5: "fresh", 6: "strong", 7: "near-gale", 8: "storm"}
_PREC_LABEL = {"rain": "rain", "snow": "snow", "frzr": "freezing rain", "icep": "sleet"}


def _check_weather(lat: float, lon: float) -> tuple[bool, str]:
    """Standalone 7Timer weather check — no APScheduler dependency."""
    import urllib.request
    import json as _json
    url = (f"http://www.7timer.info/bin/api.pl"
           f"?lon={lon}&lat={lat}&product=astro&output=json")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
        series = data["dataseries"][:3]
        clouds = [d.get("cloudcover", 1) for d in series]
        avg_cloud = sum(clouds) / len(clouds)
        cloud_pct = _CLOUD_PCT.get(round(avg_cloud), int(avg_cloud * 11))
        max_wind = max(d.get("wind10m", {}).get("speed", 1) for d in series)
        wind_label = _WIND_LABEL.get(max_wind, f"speed {max_wind}")
        prec_types = [d.get("prec_type", "none") for d in series]
        prec = next((p for p in prec_types if p != "none"), "none")
        prec_label = _PREC_LABEL.get(prec)
        unstable = min(d.get("lifted_index", 10) for d in series) < -2
        parts = [f"{cloud_pct}% clouds"]
        if max_wind >= 5:
            parts.append(f"{wind_label} winds")
        if prec_label:
            parts.append(prec_label)
        if unstable:
            parts.append("unstable air")
        return avg_cloud < 4.0, " · ".join(parts)
    except Exception as e:
        log.warning(f"[agent] weather check failed (fail-open): {e}")
        return True, ""


def get_tonight_plan() -> dict:
    """Weather forecast + computed observing schedule for tonight."""
    from datetime import datetime, timezone
    from nas_server.config import settings

    lat = settings.get("observer_lat", 33.18296)
    lon = settings.get("observer_lon", -111.57295)
    elevation = settings.get("observer_elevation_m", 440)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    result: dict = {}

    is_clear, wx = _check_weather(lat, lon)
    result["weather"] = {
        "is_clear": is_clear,
        "summary": wx or ("clear" if is_clear else "cloudy"),
    }

    if not is_clear:
        result["schedule"] = []
        result["note"] = "Bad weather tonight — plan not computed"
        return result

    try:
        from nas_server.planner import compute_plan, compute_schedule
        targets = compute_plan(today, today, lat, lon, elevation)
        schedule = compute_schedule(targets)
        result["top_targets"] = [
            {
                "target": t["target"],
                "combined_score": round(t.get("combined_score", 0), 3),
                "max_alt": round(t.get("max_alt", 0), 1),
                "int_hours": round(t.get("int_hours", 0), 1),
                "time_visible_h": round(t.get("time_visible_h", 0), 1),
            }
            for t in targets[:8]
        ]
        result["schedule"] = [
            {
                "target": s["target"],
                "start": s.get("start_hhmm"),
                "end": s.get("end_hhmm"),
                "planned_h": round(s.get("planned_h", 0), 1),
                "int_hours": round(s.get("int_hours", 0), 1),
            }
            for s in schedule
        ]
    except Exception as e:
        result["plan_error"] = str(e)
        result["schedule"] = []

    return result


def get_target_history(target: str, limit: int = 5) -> dict:
    """Recent stacking runs with Claude assessment scores for a target."""
    import json as _json
    try:
        from nas_server.database import get_stacking_runs_with_scores
        runs = get_stacking_runs_with_scores(target, limit=limit)
        history = []
        for run in runs:
            scores = {}
            if run.get("claude_scores_json"):
                try:
                    scores = _json.loads(run["claude_scores_json"])
                except Exception:
                    pass
            history.append({
                "date": (run.get("started_at") or "")[:10],
                "engine": run.get("engine"),
                "frames": run.get("frame_count"),
                "drizzle": bool(run.get("drizzle")),
                "hero": bool(run.get("hero")),
                "snr": run.get("snr_stack"),
                "fwhm": run.get("fwhm_stack"),
                "overall_score": scores.get("overall"),
                "noise_score": scores.get("noise"),
                "detail_score": scores.get("detail_level"),
                "recommendation": (run.get("claude_rec") or "")[:300],
            })
        return {"target": target, "stack_count": len(history), "history": history}
    except Exception as e:
        return {"error": str(e)}


def set_nina_ready() -> dict:
    """Call the /nina/set_ready endpoint to unblock the NINA sequence."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/nina/set_ready",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            import json as _json
            return _json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def get_nina_status() -> dict:
    """Get NINA VM reachability + sequence status."""
    try:
        from nas_server.nina_client import get_status, is_reachable
        if not is_reachable():
            return {"reachable": False, "note": "NINA VM is offline or unreachable"}
        return get_status()
    except Exception as e:
        return {"error": str(e), "reachable": False}


def dispatch(tool_name: str, tool_args: dict) -> str:
    """Dispatch a tool call by name and return JSON-serializable string result."""
    if tool_name == "semantic_search":
        result = semantic_search(tool_args.get("query", ""), tool_args.get("top_k", 5))
    elif tool_name == "query_db":
        result = query_db(tool_args.get("sql", ""))
    elif tool_name == "execute_sql":
        result = execute_sql(tool_args.get("sql", ""), tool_args.get("description", ""))
    elif tool_name == "run_cli":
        result = run_cli(tool_args.get("command", ""), tool_args.get("args"))
    elif tool_name == "write_script":
        result = write_script(tool_args.get("description", ""), tool_args.get("code", ""))
    elif tool_name == "log_suggestion":
        result = log_suggestion(
            tool_args.get("description", ""),
            tool_args.get("file_hint", ""),
            tool_args.get("code_snippet", ""),
        )
    elif tool_name == "get_target_context":
        result = get_target_context(tool_args.get("target", ""))
    elif tool_name == "get_tonight_plan":
        result = get_tonight_plan()
    elif tool_name == "get_target_history":
        result = get_target_history(tool_args.get("target", ""), tool_args.get("limit", 5))
    elif tool_name == "set_nina_ready":
        result = set_nina_ready()
    elif tool_name == "get_nina_status":
        result = get_nina_status()
    else:
        result = {"error": f"Unknown tool: {tool_name}"}
    return json.dumps(result, default=str)

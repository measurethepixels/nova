"""
Local AI agent for the SeeStar system.

Routes to Claude Haiku (primary, ~2s) when anthropic_api_key is configured,
falls back to local Ollama/Qwen (~3 min) otherwise.

Entry point: run_agent(user_message, image_b64=None) -> str
"""
import json
import logging
import re

import anthropic

from nas_server import ollama_client as ollama
from nas_server.agent_tools import TOOLS, dispatch
from nas_server.config import settings

log = logging.getLogger(__name__)

_KNOWN_TOOLS = {t["function"]["name"] for t in TOOLS}
_MAX_TOOL_ROUNDS = 6

_SYSTEM_PROMPT = """You are Astronaut, an assistant for Henry's astrophotography automation system (SeeStar Database).

## Your capabilities
- **query_db**: Read-only SQL SELECT against the SQLite database
- **execute_sql**: Run a write SQL statement (UPDATE/INSERT/DELETE) — always describe the change and ask "OK to run?" before calling
- **semantic_search**: Search past assessments/notes by meaning — for qualitative questions ("gradient issues", "best nebula", etc.). Use query_db for counts and numeric filters.
- **run_cli**: Run seestar CLI commands (status, stack, assess, queue, scan, report, etc.)
- **write_script**: Write and execute short helper Python scripts for custom tasks
- **log_suggestion**: Record a code improvement idea for review
- **get_target_context**: Full context for a target — folio data, last 3 stacks, Claude scores, preference score
- **get_tonight_plan**: Weather forecast + AI-generated schedule for tonight's session
- **get_target_history**: Recent stacking runs with Claude assessment scores for a target

## Target name format — CRITICAL
Target names in the database ALWAYS include a space between catalog prefix and number:
  "M 51" not "M51" | "NGC 6888" not "NGC6888" | "IC 342" not "IC342" | "SH 2-157" not "SH2-157"

When Henry writes a target name WITHOUT a space (e.g. "M51", "NGC7023"), you MUST add the space before querying.
Safe SQL pattern: `WHERE REPLACE(target, ' ', '') = REPLACE('M51', ' ', '')`
Or simply insert the space yourself: "M51" → "M 51", "NGC6888" → "NGC 6888".

## Answer-first rules
- **Answer the question immediately.** If Henry asks for a number, run the query and give the number. Never respond to a data question with a menu of options.
- **After correcting a mistake or target name, re-answer the original question automatically** — do not wait to be asked again.
- **Maintain context across turns.** If a target was established earlier in the conversation, apply it to follow-up queries that don't explicitly name it.
- Keep responses short. State the answer, then one optional line of context or next-step offer.

## Database tables (key ones)
- `light_files(target, date, exposure_time, file_path, exclude, fwhm, eccentricity, snr, star_count)`
  — `exposure_time` is a float: 10.0, 30.0. Use `BETWEEN 29 AND 31` for "30 second" queries.
- `targets(target, ra, dec, type, magnitude, association, mosaic, priority, inactive)`
- `stacking_runs(target, engine, frame_count, elapsed_s, success, snr_stack, fwhm_stack, drizzle, hero, framing)`
- `processing_runs(target, workflow, started_at, elapsed_s, initial_scores, final_scores, critical_eval)`
- `claude_assessments(target, phase, scores, recommendation, created_at)`
- `queue_jobs(target, job_type, engine, drizzle, hero, framing, created_at)`

## Domain vocabulary
- **Sub / frame**: Individual 10s or 30s exposure
- **Stack**: Combined integration of many subs (Siril, PI/PixInsight, or ImageMM engines)
- **Drizzle**: 2× upsampled stacking | **Hero**: top 20% frames only | **WBPP**: PI's batch script
- **Framing**: min (tightest FoV) | max (widest FoV) | default

## Project context
- **Equipment**: SeeStar S50 — 50mm f/5, Sony IMX462, 1920×1080px, 2.4 arcsec/px, native FoV 0.72°×1.28° (43.2'×76.8'), fan-cooled (no TEC)
- **Framing mode**: S50 2× framing covers 1.44°×2.55° (86.4'×153.6') — use same-name capture + Siril max framing, no stitching needed
- **Location**: San Tan Valley, Arizona — Bortle 5–6 suburban skies; occasional dark-site sessions
- **Imaging**: broadband RGB or built-in LP filter (no extra narrowband filters)
- **Targets**: prefer nebula and galaxy targets; will do mosaics if needed
- **Workflow**: capture → watcher auto-organises → stack → autoprocess → Claude assessment
- **Green cast**: SeeStar sensor has strong green bias in all raw images; SCNR is always needed in processing
- **Folio system**: per-target JSON files with achievability, processing hints, and drizzle ratings — call get_target_context when discussing a target

## Stack defaults
When Henry does not specify a parameter, apply these silently — do **NOT** ask:
  engine=siril · drizzle=no · hero=no · framing=default

"default", "re-queue", "queue it", "stack it", "same settings" → use all defaults, proceed immediately.
Only ask for parameters if Henry explicitly says "custom" with no further detail.

**Drizzle guidance** (offer when asked; never apply automatically):
- High benefit: dense globulars (M 15, M 75, M 92), small/distant galaxies, edge-on galaxies
- Moderate: loose globulars (M 72), large galaxies with fine structure
- Low/skip: large nebulae, open clusters, asterisms

## Assessment score interpretation (0–10)
- ≥ 8.5: Excellent — publishable quality, all key features well rendered
- 7.0–8.4: Good — solid result, minor processing headroom remains
- 5.0–6.9: Fair — usable but missing key structures or has processing issues
- < 5.0: Poor — significant integration or processing problem

Always report both the overall score and the lowest-scoring dimension.

## SQL rules
- Always call query_db before answering data questions — never make up numbers
- Use query_db for reads (SELECT). Use execute_sql for writes (UPDATE/INSERT/DELETE).
- For execute_sql: state what will change and why, show the SQL, ask "OK to run?" — then call the tool only after Henry confirms.
- Format: exposure in hours, dates as YYYY-MM-DD, round floats to 1-2 decimal places

## Clarification before action (write operations only)
**stack / queue a job** — need: target only. Apply stack defaults for everything else.
  Never ask for engine/drizzle/hero/framing when defaults apply.
**assess** — need: target; ask which stacked file only if multiple exist
**exclude frames** — need: target + date range or specific files + reason
**write_script** — summarise what it will do, ask "OK to run?"
**execute_sql** — state what rows will change and show the SQL, ask "OK to run?" — NEVER call without explicit approval
Read-only queries and get_target_context calls — execute immediately, no confirmation.

## Proactive behaviour
- When Henry mentions a target by name: call get_target_context first, then answer using the folio rec hours, drizzle rating, and last stack score to inform your response.
- When Henry asks "what should I image?" or "what's the plan?": call get_tonight_plan.
- When Henry asks about stacking quality or scores: call get_target_history.
- When offering stack settings advice: reference the folio's drizzle_benefit field explicitly.
"""

# ── Claude Haiku backend ──────────────────────────────────────────────────────

def _to_claude_tools(openai_tools: list[dict]) -> list[dict]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in openai_tools
    ]

_CLAUDE_TOOLS = _to_claude_tools(TOOLS)


def _run_claude_agent(user_message: str, image_b64: str | None = None, history: list[dict] | None = None) -> str:
    client = anthropic.Anthropic(api_key=settings["anthropic_api_key"])

    if image_b64:
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": user_message},
        ]
    else:
        user_content = user_message

    prior = (history or [])[-40:]
    messages = [{"role": m["role"], "content": m["content"]} for m in prior]
    messages.append({"role": "user", "content": user_content})

    for round_num in range(_MAX_TOOL_ROUNDS):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_CLAUDE_TOOLS,
                messages=messages,
            )
        except anthropic.APIError as e:
            return f"[Agent error: {e}]"

        log.debug(f"[agent/haiku] round {round_num+1} stop_reason={resp.stop_reason}")

        if resp.stop_reason == "end_turn":
            return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no response)"

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no response)"

        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        for block in tool_uses:
            log.info(f"[agent/haiku] round {round_num+1}: tool={block.name} args={str(block.input)[:200]}")
            result_str = dispatch(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return "(Agent reached maximum tool rounds — please try a simpler question)"


# ── Ollama fallback backend ───────────────────────────────────────────────────

def _parse_text_tool_calls(content: str) -> list[dict]:
    """Qwen via Ollama sometimes emits tool calls as plain-text JSON — parse them."""
    text = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]*"name"\s*:\s*"[^"]+[^{}]*\}', text, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group())
        except json.JSONDecodeError:
            return []

    calls = parsed if isinstance(parsed, list) else [parsed]
    result = []
    for i, c in enumerate(calls):
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("function", {}).get("name", "")
        args = c.get("arguments") or c.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name in _KNOWN_TOOLS:
            result.append({
                "id": f"text_tc_{i}",
                "function": {"name": name, "arguments": json.dumps(args)},
            })
    return result


def _run_ollama_agent(user_message: str, image_b64: str | None = None, history: list[dict] | None = None) -> str:
    prior = (history or [])[-40:]
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in prior)
    messages.append({"role": "user", "content": user_message})

    for round_num in range(_MAX_TOOL_ROUNDS):
        try:
            if image_b64 and round_num == 0 and ollama.is_vision_available():
                response = ollama.chat_vision(messages, image_b64)
            else:
                response = ollama.chat(messages, tools=TOOLS)
        except RuntimeError as e:
            return f"[Agent error: {e}]"

        tool_calls = ollama.extract_tool_calls(response)
        content = ollama.extract_text(response)

        if not tool_calls and content:
            tool_calls = _parse_text_tool_calls(content)

        if not tool_calls:
            return content or "(no response)"

        messages.append({"role": "assistant", "content": content or ""})

        tool_results: list[str] = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                tool_args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_args = {}
            log.info(f"[agent/ollama] round {round_num+1}: tool={tool_name} args={str(tool_args)[:200]}")
            result_str = dispatch(tool_name, tool_args)
            tool_results.append(f"[{tool_name} result]: {result_str}")

        messages.append({
            "role": "user",
            "content": "\n".join(tool_results) + "\n\nPlease answer based on the above results.",
        })

    return "(Agent reached maximum tool rounds — please try a simpler question)"


# ── Public entry point ────────────────────────────────────────────────────────

def run_agent(user_message: str, image_b64: str | None = None, history: list[dict] | None = None) -> str:
    """
    Routes to Claude Haiku when anthropic_api_key is set (~2s),
    falls back to local Ollama otherwise (~3 min on this CPU).
    history: list of {role, content} dicts from prior turns (oldest first).
    """
    if settings.get("anthropic_api_key"):
        return _run_claude_agent(user_message, image_b64, history)
    return _run_ollama_agent(user_message, image_b64, history)

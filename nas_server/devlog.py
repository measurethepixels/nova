"""
Development journal for the SeeStar Pipeline.

Tracks decisions, architectural changes, bug fixes, and experiment results
as a chronological log. Persisted as JSON alongside the server code.
Rendered as a web page at /devlog.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DEVLOG_PATH = Path(__file__).parent / "devlog.json"

CATEGORIES = {
    "feature":    ("✦", "#58a6ff"),
    "bug_fix":    ("⚠", "#f87171"),
    "decision":   ("◈", "#d2a8ff"),
    "experiment": ("◉", "#3fb950"),
    "data":       ("◎", "#facc15"),
}


def _load() -> list[dict]:
    if not _DEVLOG_PATH.exists():
        return []
    try:
        return json.loads(_DEVLOG_PATH.read_text())
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    _DEVLOG_PATH.write_text(json.dumps(entries, indent=2))


def add_entry(
    title: str,
    body: str,
    category: str = "decision",
    files: list[str] | None = None,
    date: str | None = None,
) -> dict:
    """Add a new devlog entry. Returns the created entry."""
    entries = _load()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "title": title,
        "category": category,
        "body": body,
        "files": files or [],
    }
    entries.append(entry)
    _save(entries)
    log.info(f"[devlog] Added: [{category}] {title}")
    return entry


def get_entries(limit: int | None = None) -> list[dict]:
    """Return all entries, newest first."""
    entries = sorted(_load(), key=lambda e: e.get("date", ""), reverse=True)
    return entries[:limit] if limit else entries


def get_entry(entry_id: str) -> dict | None:
    return next((e for e in _load() if e.get("id") == entry_id), None)


def delete_entry(entry_id: str) -> bool:
    entries = _load()
    new = [e for e in entries if e.get("id") != entry_id]
    if len(new) == len(entries):
        return False
    _save(new)
    return True

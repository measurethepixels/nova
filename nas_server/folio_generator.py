"""
Folio utility helpers — load, save, merge per-target reference folios.

Folios are JSON files in nas_server/target_folios/{name}.json.
Generation is done manually via Claude Code (web research + synthesis).
This module provides helpers used by auto_process.py and the web UI.
"""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_FOLIO_DIR = Path(__file__).parent / "target_folios"

# Cross-catalog identities the Messier/Caldwell tables don't cover. Each set is
# ONE physical object's synonym group, so its names share a single folio. This is
# for true synonyms only — field-mates that merely share a frame (M81/M82, the
# Leo Triplet, M31/M32) are DIFFERENT objects and keep separate folios, which is
# why the DB `association` column (which also records field-mates) is NOT used.
_SAME_OBJECT_GROUPS: list[set[str]] = [
    {"NGC 2359", "SH 2-298", "Sh2-298", "SH2-298"},  # Thor's Helmet / Gum 4
]


def _norm_name(name: str) -> str:
    """Collapse spelling variants of one designation: case, spaces, underscores."""
    return name.upper().replace(" ", "").replace("_", "")


def folio_path(target: str) -> Path:
    fname = target.replace(" ", "_").replace("/", "_") + ".json"
    return _FOLIO_DIR / fname


def _same_object_names(target: str) -> set[str]:
    """All names denoting the SAME physical object as `target`: catalog
    cross-references (Messier/Caldwell ↔ NGC/IC) plus curated cross-catalog
    synonyms. Pure name logic — no field-mate associations."""
    names = {target}
    tn = _norm_name(target)
    try:
        from nas_server import database as _db
        for k, v in {**_db._MESSIER_NGC, **_db._CALDWELL_NGC}.items():
            if _norm_name(k) == tn:
                names.add(v)
            if _norm_name(v) == tn:
                names.add(k)
    except Exception:
        pass
    for grp in _SAME_OBJECT_GROUPS:
        if any(_norm_name(g) == tn for g in grp):
            names |= grp
    return names


def _canonical_rank(name: str) -> tuple:
    """Sort key — lower is more canonical. NGC/IC preferred over Messier/
    Caldwell/Sharpless so one designation deterministically wins a synonym
    group. Case-sensitive name tiebreak keeps it stable across spelling variants."""
    u = name.upper().strip()
    if u.startswith("NGC"):
        pref = 0
    elif u.startswith("IC"):
        pref = 1
    elif re.match(r"M\s*\d", u):
        pref = 2
    elif re.match(r"C\s*\d", u):
        pref = 3
    elif u.replace(" ", "").startswith("SH"):
        pref = 4
    else:
        pref = 5
    return (pref, name)


def _disk_index() -> dict[str, list[Path]]:
    """Map normalized folio name → file paths present on disk (top level only;
    archived duplicates live in a subdir and are deliberately not indexed)."""
    idx: dict[str, list[Path]] = {}
    for p in _FOLIO_DIR.glob("*.json"):
        idx.setdefault(_norm_name(p.stem), []).append(p)
    return idx


def resolve_folio_path(target: str) -> Path | None:
    """Return the single canonical folio file for `target`, matched across catalog
    cross-references and spelling variants. None if no folio exists for the object."""
    idx = _disk_index()
    hits: set[Path] = set()
    for nm in _same_object_names(target):
        hits.update(idx.get(_norm_name(nm), []))
    if not hits:
        return None
    return sorted(hits, key=lambda p: _canonical_rank(p.stem.replace("_", " ")))[0]


def load_folio(target: str) -> dict | None:
    path = resolve_folio_path(target)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_folio(target: str, data: dict) -> Path:
    """Write the folio for `target`, landing on the object's canonical file when
    one already exists (so saving under a synonym never spawns a duplicate)."""
    _FOLIO_DIR.mkdir(exist_ok=True)
    path = resolve_folio_path(target) or folio_path(target)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def merge_folios(existing: dict, new: dict) -> dict:
    """
    Merge a newly generated folio with an existing one.
    New wins on all fields except those marked manually_edited=True in existing.
    Top-level keys that only exist in the existing folio are preserved.
    """
    manual_keys = {k for k, v in existing.items()
                   if isinstance(v, dict) and v.get("manually_edited")}
    merged = {**new}
    for key in existing:
        if key not in merged:
            merged[key] = existing[key]
        elif key in manual_keys:
            merged[key] = existing[key]
    merged["generated_at"] = new.get("generated_at", merged.get("generated_at"))
    return merged


# ---------------------------------------------------------------------------
# Hero (canonical best final) — the reference image applied back to the folio.
# Shape: {run_id, output_path, overall_score, preview_url, chosen_at, chosen_by}
# chosen_by="user" overrides and is never auto-replaced.
# ---------------------------------------------------------------------------

def get_hero(target: str) -> dict | None:
    folio = load_folio(target)
    return (folio or {}).get("hero")


def set_hero(target: str, run_id: int, output_path: str | None,
             overall_score: float | None, preview_url: str | None = None,
             chosen_by: str = "auto") -> dict:
    """Write the hero block into the target's folio (creating a folio if none).

    A user pick is marked manually_edited so a future folio regeneration's
    merge_folios() preserves it.
    """
    folio = load_folio(target) or {}
    hero = {
        "run_id": run_id,
        "output_path": output_path or "",
        "overall_score": overall_score,
        "preview_url": preview_url,
        "chosen_at": datetime.now(timezone.utc).isoformat(),
        "chosen_by": chosen_by,
    }
    if chosen_by == "user":
        hero["manually_edited"] = True
    folio["hero"] = hero
    save_folio(target, folio)
    return hero


def maybe_update_hero(target: str, run_id: int, output_path: str | None,
                      overall_score: float | None,
                      preview_url: str | None = None) -> dict | None:
    """Auto-update the hero after a run, iff it improves on the stored one.

    Only touches targets that already have a folio (avoids creating bare
    folios for non-researched targets). A user-chosen hero is never replaced.
    """
    if overall_score is None:
        return None
    folio = load_folio(target)
    if folio is None:
        return None
    cur = folio.get("hero")
    if cur:
        if cur.get("chosen_by") == "user":
            return cur
        prev = cur.get("overall_score")
        if isinstance(prev, (int, float)) and overall_score <= prev:
            return cur
    return set_hero(target, run_id, output_path, overall_score,
                    preview_url, chosen_by="auto")

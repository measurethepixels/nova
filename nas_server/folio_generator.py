"""
Folio utility helpers — load, save, merge per-target reference folios.

Folios are JSON files in nas_server/target_folios/{name}.json.
Generation is done manually via Claude Code (web research + synthesis).
This module provides helpers used by auto_process.py and the web UI.
"""

from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

_FOLIO_DIR = Path(__file__).parent / "target_folios"
_ONTOLOGY_PATH = Path(__file__).parent / "processing_ontology.json"
log = logging.getLogger(__name__)

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
    from nas_server.catalog_aliases import _CALDWELL_NGC, _MESSIER_NGC

    for k, v in {**_MESSIER_NGC, **_CALDWELL_NGC}.items():
        if _norm_name(k) == tn:
            names.add(v)
        if _norm_name(v) == tn:
            names.add(k)
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


def canonicalize_target_names(names) -> dict[str, str]:
    """Given a collection of raw target-name strings actually seen somewhere
    (DB rows, a plan, a night's captures), return {raw_name: canonical_name}
    grouping every name that denotes the SAME physical object -- pure
    spelling/whitespace variants (M31 vs M 31) via `_norm_name()`, and
    catalog cross-references (C 19 vs IC 5146) via `_same_object_names()`.
    A name absent from `names` is never considered, so this only merges
    within the given universe -- callers must pass the full set of names
    they want cross-referenced against each other (e.g. a plan's target
    names AND that night's captured target names TOGETHER, not separately,
    or a planned "C 19" will never link up with a captured "IC 5146").

    Names sharing a normalized form are always grouped (cheap, no catalog
    lookup needed). Catalog-alias linking is one extra hop: two groups
    merge if any name in one group's alias set matches any name in the
    other. `names` is expected to be small (the real target universe is a
    few hundred at most), so the O(n^2) group-merge pass is not a concern.
    """
    ordered = list(dict.fromkeys(names))
    norm_to_names: dict[str, set[str]] = {}
    for n in ordered:
        norm_to_names.setdefault(_norm_name(n), set()).add(n)

    groups: list[set[str]] = [set(g) for g in norm_to_names.values()]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                alias_norms = {
                    _norm_name(alias)
                    for member in groups[i]
                    for alias in _same_object_names(member)
                }
                if any(_norm_name(member) in alias_norms for member in groups[j]):
                    groups[i] |= groups.pop(j)
                    merged = True
                    break
            if merged:
                break

    mapping: dict[str, str] = {}
    for group in groups:
        canonical = sorted(group, key=_canonical_rank)[0]
        for name in group:
            mapping[name] = canonical
    return mapping


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
        folio = json.loads(path.read_text())
    except Exception:
        return None
    folio_type = folio.get("type") or folio.get("object_type")
    if folio_type:
        try:
            object_types = json.loads(_ONTOLOGY_PATH.read_text())["object_types"]
            if folio_type not in object_types:
                log.warning(
                    "Folio %s declares object type %r outside the processing ontology",
                    path.name,
                    folio_type,
                )
        except Exception as exc:
            # Folio loading must never fail closed because the coverage check itself
            # is unavailable; ontology loading has its own pipeline-level guard.
            log.debug("Could not validate folio type against ontology: %s", exc)
    return folio


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

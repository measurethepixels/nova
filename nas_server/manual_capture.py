"""Folder-review workflow for Henry's hand-processed PixInsight exports.

A target's ``_processed/`` folder accumulates many files: raw stacks, WBPP
masters, GraXpert intermediates, auto-pipeline artifacts, and — somewhere in the
pile — the one hand-processed final Henry actually exported from PixInsight.

We do NOT try to auto-detect which file is the final (there's no reliable signal
— ``is_auto=0`` covers thousands of intermediates). Instead:

  1. ``candidate_targets()`` surfaces folders that *might* contain a manual final
     (top-level ``_processed/`` files, minus auto-pipeline finals and obvious
     intermediates), excluding folders Henry has already reviewed.

  2. Henry reviews one folder at a time on /manual-processing, sees the candidate
     files (preview + parsed PixInsight recipe), and flags the single final via
     ``flag_manual_final()`` — or skips the folder.

  3. Only the flagged final is recorded as a manual processing run and graded by
     Claude (``grade_pending_manual_runs`` / ``grade_run``), so grading stays
     cheap and the table stays clean.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from nas_server import database
from nas_server.config import settings
from nas_server.manual_flow import parse_flow, map_step, summarize_flow

log = logging.getLogger(__name__)

_MANUAL_EXTS = (".fit", ".fits", ".xisf", ".tif", ".tiff")
_PREVIEW_DIR = Path(settings.get("db_path", str(Path.home() / "seestar_database" / "astro_data.db"))).parent / "manual_previews"


def _source_type(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".xisf"):
        return "xisf"
    if low.endswith((".tif", ".tiff")):
        return "tiff"
    return "fits"

# Substrings (case-insensitive) that mark a file as an auto-pipeline artifact,
# WBPP/NINA master, GraXpert intermediate, mask, or raw stack — never a manual final.
_DENY_TOKENS = (
    "auto_", "_stretch", "winner", "masterlight", "master_light", "master_",
    "ln_reference", "_autocrop", "_drizzle", "graxpert", "mergemosaic", "_psf",
    "adbe_", "stacked_", "_stacked", "reference_light", "reference_merge",
    "_norm", "_rjmap", "rejection", "starless", "stars_only", "_mask",
    "starmask", "integration", "canonical", "coverage",
)
# Plain filenames the experiment pipeline drops that carry no manual value.
_DENY_EXACT = {"none", "winner", "auto_final", "pi_processed_stars"}

# SeeStar/NINA raw-stack name: ends with the capture date right before the
# extension (e.g. ..._2603x10sec_T19C_2024-09-09.fit). Henry's manual exports
# always carry a processing suffix after the date (_PCC, _cropped, _processed…),
# so a date immediately before the extension marks a raw stack to skip.
_RAW_STACK_RE = re.compile(r"\d+x\d+sec.*\d{4}-\d{2}-\d{2}\.[a-z]+$", re.IGNORECASE)


def _is_candidate_name(fname: str) -> bool:
    """True if the filename could plausibly be a hand-processed final."""
    if Path(fname).suffix.lower() not in _MANUAL_EXTS:
        return False
    stem = Path(fname).stem.lower()
    if stem in _DENY_EXACT:
        return False
    low = fname.lower()
    if any(tok in low for tok in _DENY_TOKENS):
        return False
    if _RAW_STACK_RE.search(fname):
        return False
    return True


def _parse_recipe(file_path: str) -> tuple[int, list[dict], str | None]:
    """Return (n_steps, compact_flow, human_summary) for a file.

    .xisf/.xosm/.xpsm carry the PixInsight ProcessingHistory; plain .fits
    usually does not, so it yields an empty recipe (still flaggable for viewing).
    """
    suffix = Path(file_path).suffix.lower()
    if suffix not in (".xisf", ".xosm", ".xpsm"):
        return 0, [], None
    try:
        flow = parse_flow(file_path)
    except Exception as e:
        log.debug(f"[manual_capture] recipe parse failed {file_path}: {e}")
        return 0, [], None
    compact = [
        {"i": i, "class": inst["class"], "step": map_step(inst),
         "when": (inst.get("start") or "")[:19]}
        for i, inst in enumerate(flow, 1)
    ]
    summary = summarize_flow(flow) if flow else None
    return len(flow), compact, summary


def _processed_dir(target: str) -> Path:
    return Path(settings["seestar_library_path"]) / target / "_processed"


def folder_candidates(target: str, parse: bool = True) -> list[dict]:
    """Candidate manual-final files in a target's top-level _processed/ folder.

    Excludes auto-pipeline finals (is_auto=1), denylisted intermediates, and any
    file already flagged as a manual run. With parse=False, skips recipe parsing
    (fast path for counting).
    """
    pdir = _processed_dir(target)
    if not pdir.is_dir():
        return []
    auto_paths = database.auto_processed_paths()
    flagged = database.manual_run_paths()

    out: list[dict] = []
    for entry in sorted(pdir.iterdir()):
        if not entry.is_file():
            continue  # top-level only — skip experiment subdirs
        fn = entry.name
        if not _is_candidate_name(fn):
            continue
        fpath = str(entry)
        if fpath in auto_paths or fpath in flagged:
            continue
        rec = {
            "target": target,
            "filename": fn,
            "file_path": fpath,
            "source_type": _source_type(fn),
            "size_mb": round(entry.stat().st_size / 1e6, 1),
        }
        if parse:
            n, flow, summary = _parse_recipe(fpath)
            rec.update(n_steps=n, flow=flow, summary=summary)
        out.append(rec)
    return out


def candidate_targets() -> list[dict]:
    """Folders with at least one un-reviewed manual-final candidate.

    Returns [{target, n_candidates}] sorted by target, excluding folders Henry
    has already reviewed (flagged or skipped).
    """
    library = Path(settings["seestar_library_path"])
    if not library.is_dir():
        return []
    reviewed = set(database.reviewed_folder_status())
    out: list[dict] = []
    for entry in sorted(library.iterdir()):
        if not entry.is_dir():
            continue
        target = entry.name
        if target in reviewed:
            continue
        cands = folder_candidates(target, parse=False)
        if cands:
            out.append({"target": target, "n_candidates": len(cands)})
    return out


def flag_manual_final(target: str, filename: str) -> int | None:
    """Record one file as a manual final for a target. Returns the new run id
    (None if the file is missing). Does NOT mark the folder reviewed — a folder
    can hold several finals (e.g. an RGB and an HSO version), so the folder stays
    in the queue until Henry explicitly finishes it (see finish_folder). Grading
    is left to the caller (run grade_run(run_id) off the request thread)."""
    pdir = _processed_dir(target)
    fpath = pdir / filename
    if not fpath.exists():
        log.warning(f"[manual_capture] flag failed — missing file {fpath}")
        return None
    source_type = _source_type(filename)
    n_steps, flow, summary = _parse_recipe(str(fpath))
    run_id = database.insert_manual_run(
        target=target, file_path=str(fpath), filename=filename,
        source_type=source_type, n_steps=n_steps,
        flow_json=json.dumps(flow), summary=summary,
    )
    log.info(f"[manual_capture] flagged manual final {target}/{filename} (run {run_id})")
    return run_id


def unflag_manual_final(run_id: int) -> bool:
    """Remove a mistakenly-flagged final and its cached preview. The folder is
    left in its current review state (still queued if not yet finished)."""
    run = database.delete_manual_run(run_id)
    if run is None:
        return False
    prev = run.get("preview_jpg")
    if prev:
        try:
            Path(prev).unlink(missing_ok=True)
        except Exception as e:
            log.debug(f"[manual_capture] preview cleanup failed for run {run_id}: {e}")
    log.info(f"[manual_capture] unflagged manual final run {run_id} "
             f"({run.get('target')}/{run.get('filename')})")
    return True


def finish_folder(target: str) -> None:
    """Mark a folder done after one or more finals have been flagged — clears it
    from the review queue."""
    database.mark_folder_reviewed(target, "flagged")
    log.info(f"[manual_capture] finished manual-processing folder {target}")


def skip_folder(target: str) -> None:
    """Mark a folder reviewed with no manual final — removes it from the queue."""
    database.mark_folder_reviewed(target, "skipped")
    log.info(f"[manual_capture] skipped manual-processing folder {target}")


def reopen_folder(target: str) -> None:
    """Un-review a folder so it returns to the candidate queue."""
    with database.get_conn() as conn:
        conn.execute("DELETE FROM manual_folder_reviews WHERE target=?", (target,))


def _render_preview(run: dict) -> str | None:
    """Render (and cache) a preview JPEG for a manual run; return its path.

    A flagged manual final is Henry's finished export, so it is already
    stretched: TIFF (Photoshop) renders as a straight raster; FITS/XISF render
    non-linear (clip to [0,1]) rather than re-applying an STF auto-stretch
    (XISF via a temp FITS).
    """
    from nas_server.seti_astro import generate_preview_nonlinear, generate_preview_image

    src = Path(run["file_path"])
    if not src.exists():
        return None
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = _PREVIEW_DIR / f"manual_{run['id']}.jpg"
    if out.exists():
        return str(out)

    suffix = src.suffix.lower()
    if suffix in (".tif", ".tiff"):
        return str(out) if generate_preview_image(src, out) else None

    render_src = src
    tmp_fits = None
    try:
        if suffix == ".xisf":
            from nas_server.xisf_io import xisf_to_fits
            tmp_fits = _PREVIEW_DIR / f"manual_{run['id']}_src.fit"
            xisf_to_fits(str(src), str(tmp_fits))
            render_src = tmp_fits
        ok = generate_preview_nonlinear(render_src, out)
        return str(out) if ok else None
    except Exception as e:
        log.warning(f"[manual_capture] preview failed for {src.name}: {e}")
        return None
    finally:
        if tmp_fits is not None and tmp_fits.exists():
            tmp_fits.unlink()


def grade_run(run_id: int) -> bool:
    """Render + Claude-grade a single manual run. Returns True if graded."""
    from nas_server.claude_client import assess_stacked_image
    from nas_server.folio_generator import load_folio

    run = database.get_manual_run(run_id)
    if not run:
        return False
    preview = _render_preview(run)
    if not preview:
        database.set_manual_run_grade(
            run_id, None, json.dumps({"error": "preview_failed"}))
        return False
    database.set_manual_run_preview(run_id, preview)

    target = run["target"]
    folio = load_folio(target)
    obj_type = (folio or {}).get("type") or "deep sky object"
    meta = {"object_type": obj_type, "obs_date": None}
    try:
        scores = assess_stacked_image(target, preview, meta, reference_folio=folio)
    except Exception as e:
        log.warning(f"[manual_capture] grade failed for {run['filename']}: {e}")
        scores = None
    if scores is None:
        return False  # no API key / call failed — leave ungraded for retry
    database.set_manual_run_grade(run_id, scores.get("overall"), json.dumps(scores))
    log.info(f"[manual_capture] graded {target}/{run['filename']}: "
             f"overall={scores.get('overall')}/10")
    return True


def grade_pending_manual_runs(limit: int = 25) -> int:
    """Grade any flagged-but-ungraded manual runs (retry path). Returns count."""
    pending = database.list_ungraded_manual_runs(limit=limit)
    graded = 0
    for run in pending:
        if grade_run(run["id"]):
            graded += 1
    return graded

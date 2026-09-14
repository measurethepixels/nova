"""
Handles the SeeStar folder organization logic:
  incoming/M 101/          → library/M 101/
  incoming/M 101_sub/      → library/M 101/M 101_sub/

Steps:
  1. Delete .jpg files from both folders
  2. Move _sub folder into target folder (keeps its original name)
  3. Merge target folder into library (copy-on-conflict uses a date suffix)
  4. Register FITS files in the database
"""

import os
import shutil
import logging
from dataclasses import dataclass
from typing import Callable
from nas_server import database
from nas_server import telegram

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureIdentity:
    canonical_target: str | None
    status: str
    evidence: dict


def _scan_new_light_evidence(scan_dirs: list[str]) -> tuple[int, float, set[str], int, int]:
    """Inspect only this incoming session's light frames before they are merged."""
    from astropy.io import fits

    frame_count = 0
    total_s = 0.0
    object_targets: set[str] = set()
    unreadable = 0
    missing_object = 0
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for root, _, files in os.walk(scan_dir):
            for fname in files:
                lower = fname.lower()
                if not lower.startswith("light") or not lower.endswith((".fit", ".fits")):
                    continue
                frame_count += 1
                try:
                    with fits.open(os.path.join(root, fname)) as hdul:
                        header = hdul[0].header
                        total_s += float(header.get("EXPTIME", 0))
                        obj = str(header.get("OBJECT") or "").strip()
                    if obj:
                        object_targets.add(obj)
                    else:
                        missing_object += 1
                except Exception:
                    unreadable += 1
    return frame_count, total_s, object_targets, unreadable, missing_object


def _resolve_capture_identity(
    storage_target: str,
    object_targets: set[str],
    *,
    unreadable: int = 0,
    missing_object: int = 0,
    relationship_lookup: Callable[[str, str], dict] | None = None,
) -> CaptureIdentity:
    """Resolve canonical identity from unanimous FITS and explicit DB evidence.

    Exact-name sessions retain their established behavior. A differing FITS
    OBJECT must be unanimous, fully readable, and backed by an authoritative DB
    relationship; a suffix by itself is never proof.
    """
    base_evidence = {
        "object_targets": sorted(object_targets),
        "unreadable_light_headers": unreadable,
        "missing_object_headers": missing_object,
    }
    if not object_targets:
        return CaptureIdentity(storage_target, "storage_exact_no_object", base_evidence)
    if object_targets == {storage_target}:
        return CaptureIdentity(storage_target, "storage_exact", base_evidence)
    candidate = next(iter(object_targets)) if len(object_targets) == 1 else storage_target
    lookup = relationship_lookup or database.get_capture_target_relationship
    try:
        relationships = lookup(storage_target, candidate)
    except Exception as exc:
        return CaptureIdentity(
            None, "relationship_lookup_error",
            {**base_evidence, "candidate": candidate, "error": type(exc).__name__},
        )
    candidate_row = relationships.get(candidate) or {}
    storage_row = relationships.get(storage_target) or {}
    proof = None
    if (len(object_targets) == 1 and candidate != storage_target
            and storage_row.get("mosaic_association") == candidate):
        proof = "storage_mosaic_association"
    elif storage_row:
        return CaptureIdentity(
            storage_target, "storage_db_authoritative",
            {**base_evidence, "proof": "registered_storage_target"},
        )
    if len(object_targets) != 1 or unreadable or missing_object:
        return CaptureIdentity(None, "ambiguous_fits_identity", base_evidence)
    if storage_target == f"{candidate}_mosaic" and bool(candidate_row.get("mosaic")):
        proof = "canonical_mosaic_flag"
    if not proof:
        return CaptureIdentity(
            None, "unproven_target_mismatch",
            {**base_evidence, "candidate": candidate},
        )
    return CaptureIdentity(
        candidate, "canonical_relationship_proven",
        {**base_evidence, "candidate": candidate, "proof": proof},
    )


def _delete_jpgs(folder: str):
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".jpg") or f.lower().endswith(".jpeg"):
                path = os.path.join(root, f)
                os.remove(path)
                log.info(f"Deleted jpg: {path}")


def _merge_into_library(src: str, dest: str):
    """Move src directory contents into dest, renaming conflicts."""
    os.makedirs(dest, exist_ok=True)
    for item in os.listdir(src):
        src_item = os.path.join(src, item)
        dest_item = os.path.join(dest, item)
        if os.path.isdir(src_item):
            _merge_into_library(src_item, dest_item)
        else:
            if os.path.exists(dest_item):
                base, ext = os.path.splitext(item)
                from datetime import datetime
                suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest_item = os.path.join(dest, f"{base}_{suffix}{ext}")
            shutil.move(src_item, dest_item)
            log.info(f"Moved: {src_item} → {dest_item}")


def _register_fits_files(target: str, library_target_path: str):
    """Walk the organized folder and register all FITS files in the DB."""
    from astropy.io import fits

    for root, _, files in os.walk(library_target_path):
        for fname in files:
            if not fname.lower().endswith(".fit") and not fname.lower().endswith(".fits"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with fits.open(fpath) as hdul:
                    h = hdul[0].header
                    obj = h.get("OBJECT", target)
                    date = h.get("DATE-OBS", "")
                    exptime = float(h.get("EXPTIME", 0))
                    ra = h.get("RA")
                    dec = h.get("DEC")
                    filt = h.get("FILTER", "")

                if fname.lower().startswith("stacked"):
                    database.upsert_stacked_file(
                        target=obj,
                        file_name=fname,
                        file_path=fpath,
                        exposure_time=exptime,
                        date=date,
                        number_of_subs=int(hdul[0].header.get("STACKCNT", 0)),
                        latitude=hdul[0].header.get("SITELAT"),
                        longitude=hdul[0].header.get("SITELONG"),
                        ra=ra,
                        dec=dec,
                        filter_type=filt,
                    )
                elif fname.lower().startswith("light"):
                    database.upsert_light_file(
                        target=obj,
                        date=date,
                        exposure_time=exptime,
                        file_name=fname,
                        file_path=fpath,
                        ra=ra,
                        dec=dec,
                        filter_type=filt,
                    )
            except Exception as e:
                log.error(f"Failed to register {fpath}: {e}")


def organize_session(target_name: str, incoming_path: str, library_path: str) -> bool:
    """
    Organize a completed SeeStar session into the library.
    Returns True if successful.
    """
    target_folder = os.path.join(incoming_path, target_name)
    subs_folder = os.path.join(incoming_path, f"{target_name}_sub")

    if not os.path.isdir(target_folder):
        log.error(f"Target folder missing: {target_folder}")
        telegram.send(f"⚠️ <b>Organize failed</b>: folder missing for <code>{target_name}</code>")
        return False

    log.info(f"Organizing session: {target_name}")

    # 1. Delete .jpg preview files
    _delete_jpgs(target_folder)
    if os.path.isdir(subs_folder):
        _delete_jpgs(subs_folder)

    # Inspect only new light frames BEFORE merging. The destination may contain
    # historical sessions, so it cannot provide unanimous evidence for this one.
    frame_count, total_s, object_targets, unreadable, missing_object = (
        _scan_new_light_evidence([target_folder, subs_folder])
    )
    identity = _resolve_capture_identity(
        target_name, object_targets,
        unreadable=unreadable, missing_object=missing_object,
    )

    # 2. Move _sub into target folder
    if os.path.isdir(subs_folder):
        dest_subs = os.path.join(target_folder, f"{target_name}_sub")
        if os.path.exists(dest_subs):
            _merge_into_library(subs_folder, dest_subs)
            shutil.rmtree(subs_folder, ignore_errors=True)
        else:
            shutil.move(subs_folder, dest_subs)
        log.info(f"Moved _sub folder into {target_folder}")

    # 3. Merge into library
    library_target = os.path.join(library_path, target_name)
    _merge_into_library(target_folder, library_target)
    shutil.rmtree(target_folder, ignore_errors=True)
    log.info(f"Merged into library: {library_target}")

    # 4. Register in database
    _register_fits_files(target_name, library_target)
    log.info(f"Registered FITS files for {target_name}")

    hours = total_s / 3600
    if frame_count:
        detail = f"{frame_count} frames · {hours:.1f}h total integration"
    else:
        detail = "moved to library"
    telegram.send(
        f"📥 <b>Transfer complete</b>: <code>{target_name}</code>\n{detail}"
    )

    if identity.canonical_target is None:
        detail = f"{identity.status}: {identity.evidence}"
        log.error("[capture-transition] storage=%s canonical=unresolved decision=ambiguous detail=%s",
                  target_name, detail)
        telegram.send(
            f"⚠️ <b>Auto-queue skipped</b>: <code>{target_name}</code>\n"
            "Canonical target evidence was ambiguous; review required."
        )
        queue_outcome = {"decision": "ambiguous", "detail": detail}
    else:
        canonical = identity.canonical_target
        _record_capture_vs_plan(canonical, storage_target=target_name)
        queue_outcome = _maybe_auto_queue(canonical, storage_target=target_name)

    try:
        decision_id = database.record_capture_queue_decision(
            storage_target=target_name,
            canonical_target=identity.canonical_target,
            frame_count=frame_count,
            evidence={"identity_status": identity.status, **identity.evidence},
            decision=queue_outcome["decision"],
            detail=queue_outcome.get("detail", ""),
        )
        log.info(
            "[capture-transition] id=%s storage=%s canonical=%s decision=%s detail=%s",
            decision_id, target_name, identity.canonical_target,
            queue_outcome["decision"], queue_outcome.get("detail", ""),
        )
    except Exception as exc:
        log.exception("[capture-transition] failed to persist decision for %s", target_name)
        telegram.send(
            f"⚠️ <b>Capture decision not recorded</b>: <code>{target_name}</code>\n"
            f"{type(exc).__name__}; inspect service journal."
        )

    return True


def _record_capture_vs_plan(target_name: str, storage_target: str | None = None):
    """Note that this target was captured; compare against the most recent plan."""
    try:
        from nas_server.database import get_latest_planner_run, update_target_learn
        run = get_latest_planner_run()
        if run:
            _, plan_slots = run
            plan_targets = {s["target"] for s in plan_slots}
            if target_name in plan_targets:
                update_target_learn(target_name, capture_planned_delta=1)
                log.info("[learn] %s (storage=%s): capture matches plan",
                         target_name, storage_target or target_name)
            else:
                update_target_learn(target_name, capture_unplanned_delta=1)
                log.info("[learn] %s (storage=%s): off-plan capture noted",
                         target_name, storage_target or target_name)
        else:
            update_target_learn(target_name, capture_unplanned_delta=1)
    except Exception as e:
        log.warning(f"[learn] record_capture_vs_plan failed: {e}")


def _maybe_auto_queue(target_name: str, storage_target: str | None = None,
                      flags_path: str | None = None) -> dict:
    """Evaluate planner flags and return one explicit capture→queue outcome."""
    import json as _json
    storage_target = storage_target or target_name
    flags_path = flags_path or os.path.join(
        os.path.expanduser("~"), "seestar_database", "planner_autoflags.json"
    )
    if not os.path.exists(flags_path):
        detail = f"planner flags file missing; storage={storage_target}"
        log.warning("[capture-transition] canonical=%s decision=flags_missing %s",
                    target_name, detail)
        return {"decision": "flags_missing", "detail": detail}
    try:
        with open(flags_path) as f:
            flags = _json.load(f)
    except Exception as exc:
        detail = f"planner flags unreadable: {type(exc).__name__}"
        log.error("[capture-transition] canonical=%s decision=flags_error %s",
                  target_name, detail)
        return {"decision": "flags_error", "detail": detail}
    entry = flags.get(target_name) or {}
    if not isinstance(entry, dict):
        detail = "planner flag entry is not an object"
        log.error("[capture-transition] canonical=%s decision=flags_error %s",
                  target_name, detail)
        return {"decision": "flags_error", "detail": detail}
    auto_stack = entry.get("auto_stack", False)
    auto_process = entry.get("auto_process", False)
    if not auto_stack and not auto_process:
        detail = f"auto flags disabled or absent; storage={storage_target}"
        log.info("[capture-transition] canonical=%s decision=disabled %s",
                 target_name, detail)
        return {"decision": "disabled", "detail": detail}

    try:
        from nas_server import queue_manager
        if auto_stack:
            post_wf = "auto" if auto_process else None
            log.info(f"Auto-queueing Siril stack for {target_name} (post_process={post_wf})")
            result = queue_manager.add_stack_job(
                target=target_name,
                engine="siril",
                post_autoprocess_workflow=post_wf,
            )
            if result.get("duplicate"):
                where = (result.get("existing") or {}).get("where", "unknown")
                return {"decision": "duplicate", "detail": f"existing={where}"}
            detail = "stack + process" if auto_process else "stack"
            telegram.send(f"⚙️ <b>Auto-{detail} queued</b>: <code>{target_name}</code>")
            return {"decision": "queued_stack", "detail": f"position={result.get('position')}"}
        elif auto_process:
            log.info(f"Auto-queueing process for {target_name}")
            result = queue_manager.add_job(target=target_name, workflow="auto")
            if result.get("duplicate"):
                where = (result.get("existing") or {}).get("where", "unknown")
                return {"decision": "duplicate", "detail": f"existing={where}"}
            telegram.send(f"⚙️ <b>Auto-process queued</b>: <code>{target_name}</code>")
            return {"decision": "queued_process", "detail": f"position={result.get('position')}"}
    except Exception as e:
        log.error(f"Auto-queue failed for {target_name}: {e}")
        return {"decision": "queue_error", "detail": f"{type(e).__name__}: {e}"}

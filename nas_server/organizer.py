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
from astropy.io import fits
from nas_server import database
from nas_server import telegram

log = logging.getLogger(__name__)


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

    # Count new light frames BEFORE merging so we only tally this session
    frame_count = 0
    total_s = 0.0
    for scan_dir in [target_folder, subs_folder]:
        if not os.path.isdir(scan_dir):
            continue
        for root, _, files in os.walk(scan_dir):
            for fname in files:
                if fname.lower().startswith("light") and (
                    fname.lower().endswith(".fit") or fname.lower().endswith(".fits")
                ):
                    frame_count += 1
                    try:
                        with fits.open(os.path.join(root, fname)) as hdul:
                            total_s += float(hdul[0].header.get("EXPTIME", 0))
                    except Exception:
                        pass

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

    # Record capture against most recent plan for learning
    _record_capture_vs_plan(target_name)

    # Auto-stack / auto-process if flagged in planner
    _maybe_auto_queue(target_name)

    return True


def _record_capture_vs_plan(target_name: str):
    """Note that this target was captured; compare against the most recent plan."""
    try:
        from nas_server.database import get_latest_planner_run, update_target_learn
        run = get_latest_planner_run()
        if run:
            _, plan_slots = run
            plan_targets = {s["target"] for s in plan_slots}
            if target_name in plan_targets:
                update_target_learn(target_name, capture_planned_delta=1)
                log.info(f"[learn] {target_name}: capture matches plan")
            else:
                update_target_learn(target_name, capture_unplanned_delta=1)
                log.info(f"[learn] {target_name}: off-plan capture noted")
        else:
            update_target_learn(target_name, capture_unplanned_delta=1)
    except Exception as e:
        log.warning(f"[learn] record_capture_vs_plan failed: {e}")


def _maybe_auto_queue(target_name: str):
    """If the user flagged this target for auto-stack/process, queue the jobs now."""
    import json as _json
    flags_path = os.path.join(os.path.expanduser("~"), "seestar_database", "planner_autoflags.json")
    if not os.path.exists(flags_path):
        return
    try:
        with open(flags_path) as f:
            flags = _json.load(f)
    except Exception:
        return
    entry = flags.get(target_name, {})
    auto_stack = entry.get("auto_stack", False)
    auto_process = entry.get("auto_process", False)
    if not auto_stack and not auto_process:
        return

    try:
        from nas_server import queue_manager
        if auto_stack:
            post_wf = "seestar_broadband" if auto_process else None
            log.info(f"Auto-queueing Siril stack for {target_name} (post_process={post_wf})")
            queue_manager.add_stack_job(
                target=target_name,
                engine="siril",
                post_autoprocess_workflow=post_wf,
            )
            detail = "stack + process" if auto_process else "stack"
            telegram.send(f"⚙️ <b>Auto-{detail} queued</b>: <code>{target_name}</code>")
        elif auto_process:
            log.info(f"Auto-queueing process for {target_name}")
            queue_manager.add_job(target=target_name, workflow="seestar_broadband")
            telegram.send(f"⚙️ <b>Auto-process queued</b>: <code>{target_name}</code>")
    except Exception as e:
        log.error(f"Auto-queue failed for {target_name}: {e}")

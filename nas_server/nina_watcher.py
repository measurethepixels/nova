"""
Watches two NAS paths for NINA output:

NINA-side setup (the Windows capture VM):
  1. Install ninaAPI plugin (by christian-photo) in NINA — enables REST+WebSocket on port 1888.
  2. Add as the FIRST step in every NINA sequence: "Run External Script" → nina_poll_ready.ps1

  nina_poll_ready.ps1 contents:
  ──────────────────────────────────────────────────────────────────────────────
  $vmUrl = "http://<PIPELINE_HOST>:8000"
  Write-Host "Waiting for polar alignment signal..."
  while ($true) {
      try {
          $r = Invoke-RestMethod "$vmUrl/nina/ready" -TimeoutSec 5
          if ($r.ready) { Write-Host "Signal received — proceeding"; break }
      } catch { Write-Host "VM unreachable, retrying..." }
      Start-Sleep 30
  }
  ──────────────────────────────────────────────────────────────────────────────
  When Henry says "aligned" in Telegram, the agent calls set_nina_ready which
  sets the /nina/ready flag to true and this script unblocks the sequence.

  NINA save paths:
    Captures:    //NAS/NINA/captures/{Target Name}/light_*.fit
    Darks:       //NAS/NINA/calibration/dark/{gain}g_{exp}s/dark_*.fit
    Flats:       //NAS/NINA/calibration/flat/{filter}/flat_*.fit

  # TODO: end-to-end test once first NINA subs arrive:
  #   1. Confirm captures appear in /mnt/nas_data/NINA/captures/{target}/
  #   2. Check nina_watcher detects folder, waits stability_wait_seconds, registers in light_files
  #   3. Confirm source='nina' set on registered frames
  #   4. Stack the target — verify _apply_nina_calibration runs and CALSTAT='DF' in output headers
  #   5. Place test flats in calibration path — verify ADU check + registration
  #   6. Run morning_plan manually — verify calibration masters created in /mnt/nas_data/Calibration/

Watches two NAS paths for NINA output:

  1. NINA capture path — light frames from NINA sequences
     Stable folder → register in light_files with source='nina'

  2. NINA calibration path — dark/flat/bias frames
     Stable folder → validate ADU (flats), register in calibration_frames
     Flat ADU outside 20-55% → flagged invalid, Telegram alert

Both watchers reuse the same stability-loop pattern as watcher.py.
"""

import logging
import os
import time
import threading
from pathlib import Path

from nas_server.config import settings
from nas_server import telegram

log = logging.getLogger(__name__)

STABILITY_WAIT = settings.get("stability_wait_seconds", 600)

# ── Shared stability helper ───────────────────────────────────────────────────

def _latest_mtime(folder: str) -> float:
    latest = os.path.getmtime(folder)
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, f))
                if mtime > latest:
                    latest = mtime
            except OSError:
                pass
    return latest


def _is_stable(folder: str) -> bool:
    age = time.time() - _latest_mtime(folder)
    return age >= STABILITY_WAIT


# ── ADU quality check ─────────────────────────────────────────────────────────

_FLAT_ADU_MIN = 0.20   # 20% of 65535
_FLAT_ADU_MAX = 0.55   # 55% of 65535
_FULL_WELL    = 65535.0


def _check_flat_adu(median_adu: float) -> tuple[bool, str]:
    pct = median_adu / _FULL_WELL * 100
    if median_adu < _FLAT_ADU_MIN * _FULL_WELL:
        return False, f"too dark ({pct:.0f}%)"
    if median_adu > _FLAT_ADU_MAX * _FULL_WELL:
        return False, f"too bright ({pct:.0f}%)"
    return True, f"{pct:.0f}%"


# ── Watcher A: NINA light-frame captures ─────────────────────────────────────

def _register_nina_lights(target_folder: str, target_name: str) -> int:
    """Register all FITS light frames in a NINA capture folder as source='nina'."""
    from astropy.io import fits as afits
    from nas_server import database

    count = 0
    for root, _, files in os.walk(target_folder):
        for fname in files:
            if not (fname.lower().endswith(".fit") or fname.lower().endswith(".fits")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with afits.open(fpath) as hdul:
                    h = hdul[0].header
                    date      = h.get("DATE-OBS", "")
                    exptime   = float(h.get("EXPTIME", 0))
                    ra        = h.get("RA")
                    dec       = h.get("DEC")
                    filt      = h.get("FILTER", "")
                database.upsert_light_file(
                    target=target_name, date=date, exposure_time=exptime,
                    file_name=fname, file_path=fpath, ra=ra, dec=dec, filter_type=filt,
                )
                # Mark as NINA source (raw, needs calibration)
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE light_files SET source='nina' WHERE file_path=?",
                        (fpath,),
                    )
                count += 1
            except Exception as e:
                log.warning(f"[nina_watcher] could not register {fpath}: {e}")
    return count


def _capture_stability_loop(pending: set, stop: threading.Event) -> None:
    """Background loop: check pending NINA capture folders for stability."""
    library = settings.get("seestar_library_path", "")

    while not stop.is_set():
        stop.wait(10)
        for target in list(pending):
            capture_path = settings.get("nina_capture_path", "")
            if not capture_path:
                continue
            folder = os.path.join(capture_path, target)
            if not os.path.isdir(folder):
                pending.discard(target)
                continue
            if _is_stable(folder):
                pending.discard(target)
                log.info(f"[nina_watcher] capture stable: {target}")
                try:
                    n = _register_nina_lights(folder, target)
                    telegram.send(
                        f"📷 <b>NINA session complete: {target}</b>\n"
                        f"{n} frames registered (source=nina)"
                    )
                except Exception as e:
                    log.error(f"[nina_watcher] register failed for {target}: {e}")


def _poll_capture_path(pending: set) -> None:
    capture_path = settings.get("nina_capture_path", "")
    if not capture_path or not os.path.isdir(capture_path):
        return
    for name in os.listdir(capture_path):
        if os.path.isdir(os.path.join(capture_path, name)):
            if name not in pending:
                pending.add(name)
                log.info(f"[nina_watcher] new capture session detected: {name}")
                telegram.send(f"📷 NINA capture detected: <b>{name}</b>")


def start_capture_watcher() -> tuple[threading.Event, set]:
    """Start watching the NINA capture path. Returns (stop_event, pending_set)."""
    pending: set = set()
    stop = threading.Event()

    # Seed with any pre-existing folders
    _poll_capture_path(pending)

    def _poll_loop():
        while not stop.is_set():
            _poll_capture_path(pending)
            stop.wait(10)

    def _stability_loop():
        _capture_stability_loop(pending, stop)

    threading.Thread(target=_poll_loop,      name="nina-cap-poll",  daemon=True).start()
    threading.Thread(target=_stability_loop, name="nina-cap-stable", daemon=True).start()
    log.info("[nina_watcher] capture watcher started")
    return stop, pending


# ── Watcher B: NINA calibration frames ───────────────────────────────────────

def _register_calibration_frame(fpath: str, frame_type: str) -> None:
    """Parse FITS headers, measure ADU, validate, and register in calibration_frames."""
    from astropy.io import fits as afits
    import numpy as np
    from nas_server import database

    try:
        with afits.open(fpath) as hdul:
            h    = hdul[0].header
            data = hdul[0].data
            date      = h.get("DATE-OBS", "")
            exptime   = float(h.get("EXPTIME", 0))
            gain      = h.get("GAIN")
            offset    = h.get("OFFSET")
            temp_c    = h.get("CCD-TEMP")
            filt      = h.get("FILTER", "none") or "none"
            adu_med   = float(np.median(data)) if data is not None else None
    except Exception as e:
        log.warning(f"[nina_watcher] could not read calibration frame {fpath}: {e}")
        return

    valid = 1
    if frame_type == "flat" and adu_med is not None:
        ok, adu_desc = _check_flat_adu(adu_med)
        if not ok:
            valid = 0
            telegram.send(
                f"⚠️ <b>Flat frame rejected</b> — ADU {adu_desc}\n"
                f"File: {os.path.basename(fpath)}"
            )
            log.warning(f"[nina_watcher] flat rejected ({adu_desc}): {fpath}")

    frame_id = database.upsert_calibration_frame(
        frame_type=frame_type,
        file_path=fpath,
        date=date,
        filter=filt,
        gain=int(gain) if gain is not None else None,
        offset=int(offset) if offset is not None else None,
        temp_c=float(temp_c) if temp_c is not None else None,
        exposure_time=exptime or None,
        adu_median=adu_med,
        valid=valid,
    )
    log.info(f"[nina_watcher] calibration frame registered id={frame_id} type={frame_type} valid={valid}")


def _detect_frame_type(folder_name: str) -> str:
    """Guess frame type from folder name."""
    n = folder_name.lower()
    if "dark" in n:
        return "dark"
    if "flat" in n:
        return "flat"
    if "bias" in n:
        return "bias"
    return "dark"  # safe default


def _process_calibration_folder(folder: str) -> None:
    """Register all FITS files in a calibration folder and send summary."""
    frame_type = _detect_frame_type(os.path.basename(folder))
    counts = {"ok": 0, "bad": 0}
    for root, _, files in os.walk(folder):
        for fname in files:
            if not (fname.lower().endswith(".fit") or fname.lower().endswith(".fits")):
                continue
            fpath = os.path.join(root, fname)
            from nas_server import database
            # Already registered?
            with database.get_conn() as conn:
                exists = conn.execute(
                    "SELECT id, valid FROM calibration_frames WHERE file_path=?", (fpath,)
                ).fetchone()
            if exists:
                counts["ok" if exists[1] else "bad"] += 1
                continue
            _register_calibration_frame(fpath, frame_type)
            with database.get_conn() as conn:
                row = conn.execute(
                    "SELECT valid FROM calibration_frames WHERE file_path=?", (fpath,)
                ).fetchone()
            counts["ok" if (row and row[0]) else "bad"] += 1

    msg = (
        f"🔧 <b>Calibration frames ({frame_type})</b>\n"
        f"✅ {counts['ok']} valid · ❌ {counts['bad']} rejected"
    )
    telegram.send(msg)
    log.info(f"[nina_watcher] calibration folder processed: {folder} — {counts}")


def _cal_stability_loop(pending: set, stop: threading.Event) -> None:
    while not stop.is_set():
        stop.wait(10)
        for folder in list(pending):
            if not os.path.isdir(folder):
                pending.discard(folder)
                continue
            if _is_stable(folder):
                pending.discard(folder)
                log.info(f"[nina_watcher] calibration stable: {folder}")
                try:
                    _process_calibration_folder(folder)
                except Exception as e:
                    log.error(f"[nina_watcher] calibration processing failed: {e}")


def _poll_calibration_path(pending: set) -> None:
    cal_path = settings.get("nina_calibration_path", "")
    if not cal_path or not os.path.isdir(cal_path):
        return
    # Expect: cal_path/{dark|flat|bias}/{subfolder}/
    for ftype in ("dark", "flat", "bias"):
        type_dir = os.path.join(cal_path, ftype)
        if not os.path.isdir(type_dir):
            continue
        for name in os.listdir(type_dir):
            folder = os.path.join(type_dir, name)
            if os.path.isdir(folder) and folder not in pending:
                pending.add(folder)
                log.info(f"[nina_watcher] new calibration folder: {folder}")


def start_calibration_watcher() -> tuple[threading.Event, set]:
    """Start watching the NINA calibration path. Returns (stop_event, pending_set)."""
    pending: set = set()
    stop = threading.Event()

    _poll_calibration_path(pending)

    def _poll_loop():
        while not stop.is_set():
            _poll_calibration_path(pending)
            stop.wait(10)

    def _stability_loop():
        _cal_stability_loop(pending, stop)

    threading.Thread(target=_poll_loop,      name="nina-cal-poll",  daemon=True).start()
    threading.Thread(target=_stability_loop, name="nina-cal-stable", daemon=True).start()
    log.info("[nina_watcher] calibration watcher started")
    return stop, pending

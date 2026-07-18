"""Scanner for _processed/ folders — discovers files dropped in manually or by other tools."""
import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROCESSABLE_EXTS = {".fit", ".fits", ".xisf", ".tif", ".tiff"}
_PREVIEW_EXTS = {".jpg", ".jpeg", ".png"}
# Solver/scratch sidecars (ASTAP -update writes .wcs/.ini next to the FITS it solves,
# using THAT FILE'S OWN NAME) — never a legitimate library artifact worth tracking.
_SIDECAR_EXTS = {".ini", ".wcs"}


def _read_fits_meta(file_path: str) -> dict:
    """Return dict of useful header values from a FITS file."""
    try:
        from astropy.io import fits
        h = fits.getheader(file_path, ext=0)
        history = [str(c) for c in h.get("HISTORY", [])]
        return {
            "obs_date": (h.get("DATE-OBS") or "")[:10].replace("-", "") or None,
            "exptime": h.get("EXPTIME"),
            "stackcnt": h.get("STACKCNT"),
            "sensor_temp": h.get("CCD-TEMP") or h.get("SET-TEMP"),
            "history": history,
        }
    except Exception as e:
        logger.debug(f"FITS read failed {file_path}: {e}")
        return {}


def _read_xisf_meta(file_path: str) -> dict:
    """Extract FITS keywords from XISF XML header using stdlib only."""
    try:
        import struct
        import xml.etree.ElementTree as ET
        with open(file_path, "rb") as f:
            magic = f.read(8)
            if not magic.startswith(b"XISF0100"):
                return {}
            header_len = struct.unpack_from("<I", f.read(4))[0]
            f.read(4)  # reserved
            xml_bytes = f.read(header_len)
        root = ET.fromstring(xml_bytes)
        ns = {"xisf": "http://www.pixinsight.com/xisf"}
        keywords = {}
        history = []
        for kw in root.iter("FITSKeyword"):
            name = kw.get("name", "")
            value = kw.get("value", "").strip("'").strip()
            if name == "HISTORY":
                history.append(value)
            else:
                keywords[name] = value
        result = {
            "obs_date": (keywords.get("DATE-OBS") or "")[:10].replace("-", "") or None,
            "sensor_temp": keywords.get("CCD-TEMP") or keywords.get("SET-TEMP"),
            "stackcnt": keywords.get("STACKCNT"),
            "exptime": keywords.get("EXPTIME"),
            "history": history,
        }
        if result["exptime"]:
            try:
                result["exptime"] = float(result["exptime"])
            except ValueError:
                result["exptime"] = None
        if result["sensor_temp"]:
            try:
                result["sensor_temp"] = float(result["sensor_temp"])
            except ValueError:
                result["sensor_temp"] = None
        if result["stackcnt"]:
            try:
                result["stackcnt"] = int(result["stackcnt"])
            except ValueError:
                result["stackcnt"] = None
        return result
    except Exception as e:
        logger.debug(f"XISF read failed {file_path}: {e}")
        return {}


def _flags_from_history(history: list[str]) -> dict:
    """Map Siril/PixInsight HISTORY entries to processing flags."""
    flags = {}
    joined = " ".join(history).lower()
    if "spcc" in joined or "spectrophotometric" in joined:
        flags["spcc"] = True
    if "subsky" in joined or "background" in joined or "gradientcorrection" in joined:
        flags["bgextract"] = True
    if "deconvolution" in joined or "deconv" in joined or "blinddeconvolution" in joined:
        flags["deconv"] = True
    if "starxterminator" in joined or "starnet" in joined or "starless" in joined:
        flags["starless"] = True
    if "noisexterminator" in joined or "bxt" in joined or "nxt" in joined:
        flags["noise_reduction"] = True
    return flags


def _parse_filename_hints(filename: str) -> dict:
    """Best-effort tool/step extraction from standardized filename."""
    stem = Path(filename).stem.lower()
    hints = {}
    for tool in ("siril", "pixinsight", "wbpp", "setiastro", "manual"):
        if tool in stem:
            hints["tool"] = tool
            break
    for step in ("stack", "processed", "exported", "starless", "stars", "final"):
        if step in stem:
            hints["step"] = step
            break
    return hints


def scan_processed_folders(library_path: str, db_path: str) -> int:
    """
    Walk all _processed/ dirs under library_path. For each untracked file,
    read headers, parse filename, and insert a row into processed_files.
    Returns count of new rows added.
    """
    import sqlite3
    added = 0

    with sqlite3.connect(db_path) as conn:
        existing = {r[0] for r in conn.execute("SELECT file_path FROM processed_files").fetchall()}

    new_rows = []
    for root, dirs, files in os.walk(library_path):
        # Only the top-level _processed/ dir holds manually-dropped files. Its
        # subdirs (runs/, experiments/, critiques/) are pipeline scratch space —
        # e.g. run_dir copies that ASTAP re-solves with `-update`, which writes
        # `.wcs`/`.ini` sidecars sharing the STACK'S OWN FILENAME. Recursing into
        # them let those sidecars get inserted as step="stack" rows (any non-preview
        # extension was accepted unconditionally), so autoprocess's newest-raw-stack
        # pick could resolve to "<stack>.ini" at the _processed root, where it
        # doesn't exist → "Source FITS not found" (found 2026-07-03, M 42 round 4).
        if Path(root).name != "_processed":
            continue
        # Determine target from path: library/TargetName/_processed/
        parts = Path(root).relative_to(library_path).parts
        target = parts[0] if parts else None
        if not target:
            continue

        for fname in files:
            fpath = os.path.join(root, fname)
            ext = Path(fname).suffix.lower()

            if ext in _PREVIEW_EXTS:
                continue  # skip JPEG previews
            if ext in _SIDECAR_EXTS:
                continue  # skip solver sidecars (see _SIDECAR_EXTS)
            if fpath in existing:
                continue

            meta = {}
            if ext in (".fit", ".fits"):
                meta = _read_fits_meta(fpath)
            elif ext == ".xisf":
                meta = _read_xisf_meta(fpath)

            history = meta.get("history", [])
            flags = _flags_from_history(history)
            hints = _parse_filename_hints(fname)

            exptime = meta.get("exptime")
            stackcnt = meta.get("stackcnt")
            total_integration = None
            if exptime and stackcnt:
                try:
                    total_integration = float(exptime) * int(stackcnt)
                except (TypeError, ValueError):
                    pass

            new_rows.append({
                "target": target,
                "file_path": fpath,
                "filename": fname,
                "tool": hints.get("tool"),
                "step": hints.get("step"),
                "total_integration": total_integration,
                "frame_count": stackcnt,
                "sensor_temp": meta.get("sensor_temp"),
                "obs_date": meta.get("obs_date"),
                "flags": json.dumps(flags) if flags else "{}",
                "notes": None,
                "is_auto": 0,
            })

    if new_rows:
        with sqlite3.connect(db_path) as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO processed_files
                    (target, file_path, filename, tool, step,
                     total_integration, frame_count, sensor_temp, obs_date,
                     flags, notes, is_auto)
                VALUES
                    (:target, :file_path, :filename, :tool, :step,
                     :total_integration, :frame_count, :sensor_temp, :obs_date,
                     :flags, :notes, :is_auto)
            """, new_rows)
            added = len(new_rows)
        logger.info(f"[processed_scanner] Added {added} new processed file records")

    return added

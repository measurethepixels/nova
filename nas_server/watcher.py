"""
Watches the SeeStar incoming folder for completed capture sessions.

A session is considered complete when:
  - Both "Target" and "Target_subs" directories exist
  - No files in either folder have been modified in the last
    `stability_wait_seconds` seconds (the SeeStar has stopped writing)

Uses watchdog for filesystem events plus a background stability checker.
"""

import os
import time
import logging
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from nas_server.config import settings
from nas_server import organizer
from nas_server import telegram

log = logging.getLogger(__name__)

INCOMING = settings["seestar_incoming_path"]
LIBRARY = settings["seestar_library_path"]
STABILITY_WAIT = settings["stability_wait_seconds"]


def _latest_mtime(folder: str) -> float:
    """Return the most recent modification time of any file under folder."""
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
    """True if no files have been modified in the last STABILITY_WAIT seconds."""
    age = time.time() - _latest_mtime(folder)
    return age >= STABILITY_WAIT


class _SessionHandler(FileSystemEventHandler):
    """Tracks which top-level target directories have appeared."""

    def __init__(self, pending: set):
        self._pending = pending
        self._lock = threading.Lock()

    def on_created(self, event):
        if not event.is_directory:
            return
        # Only care about direct children of INCOMING
        parent = os.path.dirname(event.src_path)
        if os.path.abspath(parent) != os.path.abspath(INCOMING):
            return
        name = os.path.basename(event.src_path)
        # Strip _sub suffix to get the base target name
        base = name[:-4] if name.endswith("_sub") else name
        with self._lock:
            if base not in self._pending:
                self._pending.add(base)
                log.info(f"Detected new session folder: {name} (tracking as '{base}')")
                telegram.send(f"🔭 <b>New session detected</b>: <code>{base}</code>\nWaiting for capture to complete…")


def _poll_incoming(pending: set):
    """Scan INCOMING for target dirs not yet in pending (handles CIFS where inotify is unreliable
    and mounts that come up after service start)."""
    if not os.path.isdir(INCOMING):
        return
    try:
        for name in os.listdir(INCOMING):
            if not os.path.isdir(os.path.join(INCOMING, name)):
                continue
            base = name[:-4] if name.endswith("_sub") else name
            if base not in pending:
                pending.add(base)
                log.info(f"Poll detected new session folder: {name} (tracking as '{base}')")
                telegram.send(f"🔭 <b>New session detected</b>: <code>{base}</code>\nWaiting for capture to complete…")
    except OSError as e:
        log.warning(f"[watcher] poll scan failed: {e}")


def _stability_loop(pending: set, stop_event: threading.Event):
    """
    Periodically scans INCOMING for new sessions and checks pending ones for stability.
    Polling handles CIFS mounts where inotify events are unreliable, and mounts that
    come up after the service starts.
    """
    while not stop_event.is_set():
        time.sleep(10)

        # Always poll — catches CIFS inotify misses and late mounts
        _poll_incoming(pending)

        ready = set()
        with threading.Lock():
            for target in list(pending):
                target_dir = os.path.join(INCOMING, target)
                subs_dir = os.path.join(INCOMING, f"{target}_sub")

                target_exists = os.path.isdir(target_dir)
                subs_exists = os.path.isdir(subs_dir)

                if not target_exists:
                    continue  # main folder not here yet — still capturing

                target_stable = _is_stable(target_dir)
                subs_stable   = (not subs_exists) or _is_stable(subs_dir)

                if target_stable and subs_stable:
                    ready.add(target)

        for target in ready:
            pending.discard(target)
            log.info(f"Session stable, organizing: {target}")
            try:
                organizer.organize_session(target, INCOMING, LIBRARY)
            except Exception as e:
                log.error(f"Failed to organize {target}: {e}")
                telegram.send(f"❌ <b>Organize error</b>: <code>{target}</code>\n{e}")


def start_watcher() -> tuple[Observer, threading.Event, set]:
    """Start the filesystem watcher and stability checker. Returns handles to stop them."""
    pending: set[str] = set()

    # Seed with any folders already sitting in incoming at startup
    if os.path.isdir(INCOMING):
        for name in os.listdir(INCOMING):
            if os.path.isdir(os.path.join(INCOMING, name)):
                base = name[:-4] if name.endswith("_sub") else name
                pending.add(base)
                log.info(f"Found pre-existing session at startup: {base}")

    stop_event = threading.Event()
    stability_thread = threading.Thread(
        target=_stability_loop, args=(pending, stop_event), daemon=True
    )
    stability_thread.start()

    handler = _SessionHandler(pending)
    observer = Observer()
    if os.path.isdir(INCOMING):
        observer.schedule(handler, INCOMING, recursive=False)
        observer.start()
        log.info(f"Watching {INCOMING} for new SeeStar sessions")
    else:
        log.warning(f"Incoming path not found, watcher disabled: {INCOMING}")

    return observer, stop_event, pending

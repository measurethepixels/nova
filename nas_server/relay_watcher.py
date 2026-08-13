"""Notify Telegram when new Claude-addressed NOVA Relay messages arrive.

This watcher is notification-only: it opens the relay database read-only and
never launches a model, acknowledges a message, or writes the mailbox.
"""

from __future__ import annotations

import html
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from nas_server import telegram


log = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELAY_DIR = REPO_ROOT / ".agent-relay"
RELAY_FILENAMES = {"relay.sqlite3", "relay.sqlite3-wal", "relay.sqlite3-shm"}
POLL_SECONDS = 45.0
SETTLE_SECONDS = 1.0
GATE_KINDS = frozenset({"GATE"})
ELEVATED_RISKS = frozenset({"yellow", "red"})
GREEN_ELIGIBLE_KINDS = frozenset({"QUESTION", "REVIEW", "FACT_CHECK"})


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _current_max_id(db_path: Path, recipient: str) -> int:
    with _connect_read_only(db_path) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE recipient = ?",
            (recipient,),
        ).fetchone()
    return int(row[0])


def _new_messages(
    db_path: Path,
    recipient: str,
    after_id: int,
) -> list[dict[str, Any]]:
    with _connect_read_only(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, sender, recipient, kind, intent, role, question, risk, loop_id
            FROM messages
            WHERE recipient = ?
              AND id > ?
              AND datetime(ttl) > datetime('now')
            ORDER BY id ASC
            """,
            (recipient, after_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_watermark(state_path: Path) -> int | None:
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
        watermark = value["last_notified_id"]
        if not isinstance(watermark, int) or isinstance(watermark, bool) or watermark < 0:
            raise ValueError("last_notified_id must be a non-negative integer")
        return watermark
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_watermark(state_path: Path, message_id: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_name(f".{state_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump({"last_notified_id": message_id}, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, state_path)
    try:
        directory_fd = os.open(state_path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def initialize_watermark(
    db_path: Path,
    state_path: Path,
    *,
    recipient: str = "claude",
) -> int:
    """Load state or synchronously seed it to the current inbox maximum."""
    existing = _load_watermark(state_path)
    if existing is not None:
        return existing
    seeded = _current_max_id(db_path, recipient)
    _write_watermark(state_path, seeded)
    return seeded


def _notification(message: dict[str, Any]) -> str:
    sender = html.escape(str(message["sender"]))
    recipient = html.escape(str(message["recipient"]).capitalize())
    question = html.escape(str(message["question"]))
    kind = html.escape(str(message["kind"]))
    loop_id = html.escape(str(message["loop_id"]))
    return (
        f"📨 <b>NOVA Relay for {recipient}</b> · <code>#{message['id']}</code>\n"
        f"<b>{kind}</b> from <code>{sender}</code> · loop <code>{loop_id}</code>\n"
        f"{question}"
    )


def _is_gate(message: dict[str, Any]) -> bool:
    return message.get("kind") in GATE_KINDS or message.get("risk") in ELEVATED_RISKS


def _is_green_eligible(message: dict[str, Any]) -> bool:
    return (
        message.get("kind") in GREEN_ELIGIBLE_KINDS
        and message.get("intent") == "CHECK"
        and message.get("risk") == "green"
    )


def _is_interactive_handoff(message: dict[str, Any]) -> bool:
    return (
        message.get("risk") == "green"
        and message.get("kind") != "GATE"
        and message.get("intent") in {"TASK", "COORDINATION"}
    )


def _gate_reply_markup(message_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"relay_approve:{message_id}"},
            {"text": "❌ Reject", "callback_data": f"relay_reject:{message_id}"},
        ]]
    }


def _gate_notification(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    sender = html.escape(str(message["sender"]))
    question = html.escape(str(message["question"]))
    kind = html.escape(str(message["kind"]))
    risk = html.escape(str(message["risk"]))
    loop_id = html.escape(str(message["loop_id"]))
    text = (
        "🛑 <b>NOVA Relay needs Henry</b> · "
        f"<code>#{message['id']}</code>\n"
        f"<b>{kind}</b> from <code>{sender}</code> · risk <code>{risk}</code> · "
        f"loop <code>{loop_id}</code>\n"
        f"{question}"
    )
    return text, _gate_reply_markup(message["id"])


def _fyi_notification(message: dict[str, Any]) -> str:
    """Lighter, no-buttons notice for a green message an automated checker may
    answer — monitor mode, not silence, and not the urgency of a gate."""
    sender = html.escape(str(message["sender"]))
    question = html.escape(str(message["question"]))
    kind = html.escape(str(message["kind"]))
    loop_id = html.escape(str(message["loop_id"]))
    return (
        f"🟢 <b>NOVA Relay (monitor)</b> · <code>#{message['id']}</code>\n"
        f"<b>{kind}</b> from <code>{sender}</code> · loop <code>{loop_id}</code>\n"
        f"{question}"
    )


def _interactive_notification(message: dict[str, Any]) -> str:
    recipient = html.escape(str(message["recipient"]).capitalize())
    intent = html.escape(str(message["intent"]))
    role = html.escape(str(message["role"]))
    question = html.escape(str(message["question"]))
    return (
        f"📥 <b>NOVA Relay needs interactive {recipient}</b> · "
        f"<code>#{message['id']}</code>\n"
        f"intent <code>{intent}</code> · role <code>{role}</code>\n"
        f"{question}"
    )


def scan_once(
    db_path: Path,
    state_path: Path,
    *,
    recipient: str = "claude",
    send: Callable[..., bool] = telegram.send,
) -> int:
    """Notify in ID order, persisting only each successfully delivered ID.

    Tiered by message shape: GATE/yellow/red gets an actionable notice with
    inline Approve/Reject buttons (see nas_server/gate_approval.py for what a
    tap does); a green TASK/COORDINATION gets an interactive handoff notice;
    a green CHECK with an eligible kind gets a lighter no-buttons FYI
    (monitor mode, not silence); anything else keeps the original plain
    notification. `send` is called with a single argument except for the gate
    tier, which passes `reply_markup` as a keyword.
    """
    watermark = initialize_watermark(db_path, state_path, recipient=recipient)
    for message in _new_messages(db_path, recipient, watermark):
        if _is_gate(message):
            text, markup = _gate_notification(message)
            delivered = send(text, reply_markup=markup)
        elif _is_interactive_handoff(message):
            delivered = send(_interactive_notification(message))
        elif _is_green_eligible(message):
            delivered = send(_fyi_notification(message))
        else:
            delivered = send(_notification(message))
        if not delivered:
            log.warning("[relay-watcher] Telegram send failed for message %s", message["id"])
            break
        watermark = int(message["id"])
        _write_watermark(state_path, watermark)
    return watermark


class _RelayEventHandler:
    def __init__(self, wake: threading.Event):
        self._wake = wake

    def dispatch(self, event: Any) -> None:
        self.on_any_event(event)

    def on_any_event(self, event: Any) -> None:
        paths = [getattr(event, "src_path", ""), getattr(event, "dest_path", "")]
        if any(Path(path).name in RELAY_FILENAMES for path in paths if path):
            self._wake.set()


def _scanner_loop(
    db_path: Path,
    state_path: Path,
    wake: threading.Event,
    stop: threading.Event,
    *,
    recipient: str,
    send: Callable[[str], bool],
    poll_seconds: float,
    settle_seconds: float,
) -> None:
    while not stop.is_set():
        wake.wait(timeout=poll_seconds)
        if stop.is_set():
            break
        if stop.wait(settle_seconds):
            break
        wake.clear()
        try:
            scan_once(
                db_path,
                state_path,
                recipient=recipient,
                send=send,
            )
        except Exception:
            log.exception("[relay-watcher] scan failed")
            stop.wait(min(poll_seconds, 5.0))


def start_relay_watcher(
    *,
    enabled: bool | None = None,
    relay_dir: Path = DEFAULT_RELAY_DIR,
    recipient: str = "claude",
    send: Callable[[str], bool] = telegram.send,
    observer_factory: Callable[[], Any] | None = None,
    poll_seconds: float = POLL_SECONDS,
    settle_seconds: float = SETTLE_SECONDS,
) -> tuple[Any | None, threading.Event]:
    """Start the interrupt-first watcher, or return disabled handles."""
    stop = threading.Event()
    if enabled is None:
        from nas_server.config import settings

        enabled = bool(settings.get("relay_watcher_enabled", False))
    if not enabled:
        stop.set()
        log.info("[relay-watcher] disabled")
        return None, stop

    relay_dir = Path(relay_dir)
    db_path = relay_dir / "relay.sqlite3"
    state_path = relay_dir / f"{recipient}_notify_state.json"

    # This must finish durably before Observer.start(), or a startup message can
    # be folded into the cold-start watermark and skipped forever.
    initialize_watermark(db_path, state_path, recipient=recipient)

    wake = threading.Event()
    handler = _RelayEventHandler(wake)
    if observer_factory is None:
        from watchdog.observers import Observer

        observer = Observer()
    else:
        observer = observer_factory()
    observer.schedule(handler, str(relay_dir), recursive=False)
    observer.start()

    thread = threading.Thread(
        target=_scanner_loop,
        args=(db_path, state_path, wake, stop),
        kwargs={
            "recipient": recipient,
            "send": send,
            "poll_seconds": poll_seconds,
            "settle_seconds": settle_seconds,
        },
        daemon=True,
        name=f"relay-notify-{recipient}",
    )
    thread.start()
    log.info("[relay-watcher] watching %s for %s", relay_dir, recipient)
    return observer, stop

"""Durable, incremental archive of NOVA's Telegram channel.

The Bot API (nas_server/telegram.py) has no bulk history endpoint -- it can
only send and poll recent updates. This module uses a user-account Telethon
session (a separate, materially more sensitive credential than the bot
token; see settings.example.json's telegram_api_id/telegram_api_hash and
docs on issue #548) to pull and durably store the archive instead.

Storage is deliberately split across two locations, not one:
`telegram_archive_db_path` (structured rows + per-chat sync cursor, local
VM disk by default -- same reasoning as `astro_data.db` itself) and
`telegram_archive_dir` (`json/`, `markdown/`, `media/` per-message exports,
NAS by default for capacity/durability). Confirmed live (2026-09-01):
SQLite cannot reliably lock a database file on this project's CIFS/SMB NAS
mount -- a plain single-connection `CREATE TABLE` failed with `database is
locked`, no concurrency involved at all. Plain file writes (json/markdown/
media) have no such dependency and are fine on the NAS mount. Never move
`messages.sqlite` back onto `telegram_archive_dir` without re-verifying
that constraint no longer holds. The sync target defaults to just the one
chat NOVA's bot already uses (`telegram_chat_id`) -- the credential can see
Henry's whole account, but nothing about that requires the archive to.

Testable without a real Telegram connection: the sync/storage logic here
takes a duck-typed adapter (`iter_new_messages`, `download_media`); only
`TelethonAdapter` (built lazily, only when a real sync actually runs) talks
to Telethon itself.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

log = logging.getLogger("telegram_archive")


@dataclass(frozen=True)
class MessageRecord:
    chat_id: str
    message_id: int
    date: str  # ISO 8601
    sender_id: str | None
    sender_name: str | None
    text: str
    media_type: str | None = None
    media_filename: str | None = None


class ArchiveClient(Protocol):
    """Duck-typed adapter sync_chat()/run_sync() depend on -- real Telethon
    usage lives only in TelethonAdapter; tests supply a fake implementing
    this same shape, no real network/account needed."""

    def iter_new_messages(self, chat_id: str, since_id: int) -> Iterable[Any]:
        ...

    def to_record(self, chat_id: str, raw_message: Any) -> MessageRecord:
        ...

    def download_media(self, raw_message: Any, dest_dir: Path) -> str | None:
        ...


def init_archive_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                chat_id        TEXT NOT NULL,
                message_id     INTEGER NOT NULL,
                date           TEXT NOT NULL,
                sender_id      TEXT,
                sender_name    TEXT,
                text           TEXT NOT NULL DEFAULT '',
                media_type     TEXT,
                media_filename TEXT,
                synced_at      TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_cursors (
                chat_id         TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_cursor(conn: sqlite3.Connection, chat_id: str) -> int:
    row = conn.execute(
        "SELECT last_message_id FROM sync_cursors WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def set_cursor(conn: sqlite3.Connection, chat_id: str, message_id: int) -> None:
    conn.execute(
        """
        INSERT INTO sync_cursors (chat_id, last_message_id, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(chat_id) DO UPDATE SET
            last_message_id = excluded.last_message_id,
            updated_at = excluded.updated_at
        """,
        (chat_id, message_id),
    )


def store_message(conn: sqlite3.Connection, record: MessageRecord) -> None:
    conn.execute(
        """
        INSERT INTO messages
            (chat_id, message_id, date, sender_id, sender_name, text,
             media_type, media_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, message_id) DO UPDATE SET
            text = excluded.text,
            media_type = excluded.media_type,
            media_filename = excluded.media_filename
        """,
        (
            record.chat_id, record.message_id, record.date,
            record.sender_id, record.sender_name, record.text,
            record.media_type, record.media_filename,
        ),
    )


def write_message_json(archive_dir: Path, record: MessageRecord) -> Path:
    out_dir = archive_dir / "json" / record.chat_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record.message_id}.json"
    out_path.write_text(
        json.dumps(
            {
                "chat_id": record.chat_id,
                "message_id": record.message_id,
                "date": record.date,
                "sender_id": record.sender_id,
                "sender_name": record.sender_name,
                "text": record.text,
                "media_type": record.media_type,
                "media_filename": record.media_filename,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return out_path


def _format_markdown_entry(record: MessageRecord) -> str:
    sender = record.sender_name or record.sender_id or "unknown"
    media_note = f" [{record.media_type}: {record.media_filename}]" if record.media_type else ""
    lines = [f"### {record.date} · {sender} · #{record.message_id}\n\n"]
    if record.text:
        lines.append(f"{record.text}\n")
    if media_note:
        lines.append(f"{media_note.strip()}\n")
    lines.append("\n")
    return "".join(lines)


def regenerate_markdown(conn: sqlite3.Connection, archive_dir: Path, chat_id: str) -> Path:
    """Rewrite this chat's markdown file from scratch, from the DB's own
    committed rows -- deterministic and idempotent regardless of how many
    times (or how partially) a sync run was retried, unlike a blind append
    (which duplicates a message re-processed after an interrupted batch)."""
    out_dir = archive_dir / "markdown"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{chat_id}.md"
    rows = conn.execute(
        """
        SELECT message_id, date, sender_id, sender_name, text, media_type, media_filename
        FROM messages WHERE chat_id = ? ORDER BY message_id
        """,
        (chat_id,),
    ).fetchall()
    entries = [
        _format_markdown_entry(MessageRecord(
            chat_id=chat_id, message_id=r[0], date=r[1], sender_id=r[2],
            sender_name=r[3], text=r[4], media_type=r[5], media_filename=r[6],
        ))
        for r in rows
    ]
    out_path.write_text("".join(entries), encoding="utf-8")
    return out_path


def sync_chat(
    client: ArchiveClient,
    conn: sqlite3.Connection,
    archive_dir: Path,
    chat_id: str,
) -> int:
    """Pull and store every message newer than this chat's saved cursor.
    Returns the count of messages synced. Idempotent, including across an
    interrupted batch: each message's DB row + cursor advance is its own
    committed transaction, so a later message's failure can never lose or
    duplicate an earlier one's already-durable state. Markdown is rewritten
    from the DB in `finally`, not appended during the loop -- a blind append
    would duplicate entries for messages re-processed after a resumed batch;
    a full regeneration from committed rows cannot."""
    since_id = get_cursor(conn, chat_id)
    synced = 0
    try:
        for raw_message in client.iter_new_messages(chat_id, since_id):
            record = client.to_record(chat_id, raw_message)
            media_filename = record.media_filename
            if record.media_type and media_filename is None:
                media_dir = archive_dir / "media" / chat_id
                media_dir.mkdir(parents=True, exist_ok=True)
                media_filename = client.download_media(raw_message, media_dir)
                record = MessageRecord(
                    chat_id=record.chat_id, message_id=record.message_id,
                    date=record.date, sender_id=record.sender_id,
                    sender_name=record.sender_name, text=record.text,
                    media_type=record.media_type, media_filename=media_filename,
                )
            store_message(conn, record)
            set_cursor(conn, chat_id, record.message_id)
            conn.commit()
            write_message_json(archive_dir, record)
            synced += 1
    finally:
        if synced:
            regenerate_markdown(conn, archive_dir, chat_id)
    return synced


def default_chat_ids(settings: dict[str, Any]) -> list[str]:
    """`telegram_chat_id` is meaningful only from the Bot API's side (the ID
    the bot addresses when *sending* to Henry). From Henry's own Telethon
    user account, that same conversation is addressed by the *other* party
    -- the bot itself, whose id is the numeric prefix of `telegram_token`
    (confirmed live 2026-09-01: Telethon can't resolve a bare
    `telegram_chat_id` at all, since that peer never appears in this
    account's own dialog list; the bot's id does)."""
    token = str(settings.get("telegram_token") or "")
    bot_id = token.split(":", 1)[0].strip()
    return [bot_id] if bot_id.isdigit() else []


class TelethonAdapter:
    """Real ArchiveClient backed by a saved Telethon session. Only
    constructed by run_sync() when no fake client is supplied -- tests never
    import telethon through this module."""

    def __init__(self, session_path: str, api_id: int, api_hash: str) -> None:
        from telethon.sync import TelegramClient  # noqa: PLC0415

        self._client = TelegramClient(session_path, api_id, api_hash)
        self._client.connect()
        if not self._client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized -- run "
                "scripts/telegram_archive_login.py interactively first"
            )

    def iter_new_messages(self, chat_id: str, since_id: int) -> Iterator[Any]:
        # Telethon yields newest-first by default; reverse so callers see
        # ascending message_id (matches the fake client's contract and
        # keeps `highest_seen` monotonic without extra bookkeeping here).
        messages = list(self._client.iter_messages(int(chat_id), min_id=since_id))
        return reversed(messages)

    def to_record(self, chat_id: str, raw_message: Any) -> MessageRecord:
        sender = getattr(raw_message, "sender", None)
        sender_name = None
        if sender is not None:
            sender_name = " ".join(
                part for part in (
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                ) if part
            ) or getattr(sender, "username", None)
        media_type = type(raw_message.media).__name__ if raw_message.media else None
        return MessageRecord(
            chat_id=chat_id,
            message_id=raw_message.id,
            date=raw_message.date.isoformat(),
            sender_id=str(raw_message.sender_id) if raw_message.sender_id else None,
            sender_name=sender_name,
            text=raw_message.message or "",
            media_type=media_type,
        )

    def download_media(self, raw_message: Any, dest_dir: Path) -> str | None:
        path = self._client.download_media(raw_message, file=str(dest_dir) + "/")
        return Path(path).name if path else None

    def close(self) -> None:
        self._client.disconnect()


class SyncError(RuntimeError):
    """Raised by run_sync() when at least one chat's sync failed, so a
    real failure is never indistinguishable from an empty-but-successful
    run -- a headless job must be able to fail loudly. `results` still
    carries counts for every chat that DID succeed (per-chat resilience is
    preserved; one bad chat doesn't block the others)."""

    def __init__(self, results: dict[str, int], failed_chat_ids: list[str]) -> None:
        self.results = results
        self.failed_chat_ids = failed_chat_ids
        super().__init__(f"sync failed for chat(s): {', '.join(failed_chat_ids)}")


def run_sync(
    settings: dict[str, Any],
    *,
    client: ArchiveClient | None = None,
    chat_ids: list[str] | None = None,
) -> dict[str, int]:
    """Sync every configured chat once. Returns {chat_id: messages_synced}
    on full success, or raises SyncError (carrying partial results) if any
    chat failed. Safe to call repeatedly (e.g. from an hourly timer) -- each
    call only pulls what's new since the last run."""
    archive_dir = Path(settings.get("telegram_archive_dir") or "/mnt/nas_data/telegram")
    targets = chat_ids if chat_ids is not None else default_chat_ids(settings)
    if not targets:
        log.info("[telegram_archive] no chat_ids configured, nothing to sync")
        return {}

    owns_client = client is None
    if client is None:
        session_path = settings.get("telegram_archive_session_path") or ""
        api_id = int(settings.get("telegram_api_id") or 0)
        api_hash = str(settings.get("telegram_api_hash") or "")
        if not session_path or not api_id or not api_hash:
            raise RuntimeError(
                "telegram_archive_session_path/telegram_api_id/telegram_api_hash "
                "must all be set to sync without an injected client"
            )
        client = TelethonAdapter(session_path, api_id, api_hash)

    default_db_path = Path.home() / "seestar_database" / "telegram_archive.sqlite"
    db_path = Path(settings.get("telegram_archive_db_path") or default_db_path)
    init_archive_db(db_path)
    conn = sqlite3.connect(db_path)
    results: dict[str, int] = {}
    failed_chat_ids: list[str] = []
    try:
        for chat_id in targets:
            try:
                results[chat_id] = sync_chat(client, conn, archive_dir, chat_id)
            except Exception:
                log.exception("[telegram_archive] sync failed for chat %s", chat_id)
                failed_chat_ids.append(chat_id)
    finally:
        conn.close()
        if owns_client and hasattr(client, "close"):
            client.close()
    if failed_chat_ids:
        raise SyncError(results, failed_chat_ids)
    return results

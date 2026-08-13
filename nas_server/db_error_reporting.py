"""Consistent visibility for SQLite failures in unattended code paths."""

from __future__ import annotations

import html
import logging
import threading
import time

_NOTICE_COOLDOWN_SECONDS = 300
_notice_lock = threading.Lock()
_last_notice_at: float | None = None


def report_sqlite_error(
    logger: logging.Logger,
    *,
    context: str,
    error: Exception,
    consequence: str,
    notify: bool,
) -> None:
    """Log a SQLite failure and optionally send one rate-limited Telegram alert."""
    logger.error(
        "[%s] SQLite failure: %s; %s",
        context,
        error,
        consequence,
        exc_info=True,
    )
    if not notify or not _claim_notice():
        return

    try:
        from nas_server import telegram

        telegram.send(
            "⚠️ <b>Database operation failed</b>\n"
            f"Context: <code>{html.escape(context)}</code>\n"
            f"{html.escape(consequence)}\n"
            f"SQLite: <code>{html.escape(str(error)[:300])}</code>"
        )
    except Exception:
        logger.exception("[%s] failed to send SQLite alert", context)


def _claim_notice() -> bool:
    global _last_notice_at
    now = time.monotonic()
    with _notice_lock:
        if (
            _last_notice_at is not None
            and now - _last_notice_at < _NOTICE_COOLDOWN_SECONDS
        ):
            return False
        _last_notice_at = now
        return True

import base64
import threading
import time
import urllib.request
import urllib.error
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

_token: str | None = None
_chat_id: str | None = None
_poll_thread: threading.Thread | None = None
_poll_stop = threading.Event()
_callback_handler: Callable[[dict], str] | None = None
_approve_command_handler: Callable[[str], str | None] | None = None


def configure(token: str, chat_id: str):
    global _token, _chat_id
    _token = token
    _chat_id = chat_id


def set_callback_handler(handler: Callable[[dict], str] | None) -> None:
    """Register the function that turns a callback_query into an outcome string.

    telegram.py is transport only — it authorizes the chat, dispatches an
    authorized callback_query dict to `handler`, and always acknowledges +
    edits the message with whatever outcome string `handler` returns. It does
    not interpret `callback_data` itself. Pass None to unregister (an
    unhandled callback_query is then safely ignored, not an error).
    """
    global _callback_handler
    _callback_handler = handler


def set_approve_command_handler(handler: Callable[[str], str | None] | None) -> None:
    """Register the function that inspects an incoming text message for a
    PR-approval command (issue #231's third design -- see
    nas_server/telegram_pr_approval.py for why this exists as a distinct
    text command rather than a native GitHub review or PR comment).

    telegram.py stays transport only, same rule as set_callback_handler:
    it dispatches every already-chat-authorized text message to `handler`
    and does not itself decide what counts as an approval command. `handler`
    returns None for text that isn't an approval command (the poll loop
    then falls through to the normal chat agent, unchanged) or a reply
    string to send back and skip the chat agent for that message. Pass
    None to unregister.
    """
    global _approve_command_handler
    _approve_command_handler = handler


def send(message: str, reply_markup: dict | None = None) -> bool:
    if not _token or not _chat_id:
        return False
    url = f"https://api.telegram.org/bot{_token}/sendMessage"
    body = {"chat_id": _chat_id, "text": message, "parse_mode": "HTML"}
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a button tap (dismisses the client-side loading spinner)."""
    if not _token:
        return False
    url = f"https://api.telegram.org/bot{_token}/answerCallbackQuery"
    payload = json.dumps({"callback_query_id": callback_query_id, "text": text[:200]}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Telegram answerCallbackQuery failed: {e}")
        return False


def edit_message_text(chat_id, message_id, text: str) -> bool:
    """Rewrite a sent message (used to strip its buttons and show the outcome)."""
    if not _token:
        return False
    url = f"https://api.telegram.org/bot{_token}/editMessageText"
    payload = json.dumps({
        "chat_id": chat_id, "message_id": message_id,
        "text": text, "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Telegram editMessageText failed: {e}")
        return False


def send_photo_bytes(image_bytes: bytes, caption: str = "") -> bool:
    """Send a PNG/JPEG from in-memory bytes via Telegram sendPhoto."""
    if not _token or not _chat_id:
        return False
    url = f"https://api.telegram.org/bot{_token}/sendPhoto"
    boundary = b"----TelegramBoundary7x"
    crlf = b"\r\n"

    def part_field(name: str, value: str) -> bytes:
        return (b"--" + boundary + crlf
                + f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
                + value.encode() + crlf)

    body = (
        part_field("chat_id", _chat_id)
        + part_field("caption", caption)
        + part_field("parse_mode", "HTML")
        + b"--" + boundary + crlf
        + b'Content-Disposition: form-data; name="photo"; filename="plan.png"' + crlf
        + b"Content-Type: image/png" + crlf + crlf
        + image_bytes + crlf
        + b"--" + boundary + b"--" + crlf
    )
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        logger.warning(f"Telegram send_photo_bytes failed: {e}")
        return False


def _telegram_safe_jpeg(image_path: str) -> bytes:
    """Read an image and return JPEG bytes within Telegram sendPhoto limits.

    Telegram rejects (HTTP 400) photos where width+height > 10000 or the file
    exceeds 10 MB. Post-stretch previews are full-resolution (≈4896×6592, up to
    ~15 MB), so every send after the stretch step was failing. Downscale the
    longest side to ≤4096 (keeps w+h well under 10000 at any aspect) and re-encode
    JPEG, stepping quality down until the payload is comfortably under 10 MB.
    Falls back to the raw bytes if PIL is unavailable (small linear previews,
    which are already in-spec, still send fine).
    """
    with open(image_path, "rb") as f:
        raw = f.read()
    try:
        import io
        from PIL import Image
    except Exception:
        return raw

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        longest = max(im.size)
        _MAX_SIDE = 4096
        if longest > _MAX_SIDE:
            scale = _MAX_SIDE / longest
            im = im.resize((max(1, round(im.width * scale)),
                            max(1, round(im.height * scale))), Image.LANCZOS)
        # Already in-spec and modest size — keep original bytes (avoids recompress)
        if longest <= _MAX_SIDE and len(raw) <= 9_000_000 and sum(im.size) <= 10000:
            return raw
        for q in (88, 82, 75, 68, 60):
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if len(data) <= 9_500_000:
                return data
        return data  # last (lowest-quality) attempt
    except Exception as e:
        logger.warning(f"Telegram image downscale failed ({e}); sending raw")
        return raw


def send_photo(image_path: str, caption: str = "") -> bool:
    """Send a JPEG photo via Telegram (multipart/form-data, stdlib only).

    Large previews are downscaled to Telegram's sendPhoto limits first.
    """
    if not _token or not _chat_id:
        return False
    url = f"https://api.telegram.org/bot{_token}/sendPhoto"
    boundary = b"----TelegramBoundary7x"
    crlf = b"\r\n"

    def part_field(name: str, value: str) -> bytes:
        return (b"--" + boundary + crlf
                + f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
                + value.encode() + crlf)

    try:
        img_data = _telegram_safe_jpeg(image_path)
        fname = image_path.split("/")[-1]
        body = (
            part_field("chat_id", _chat_id)
            + part_field("caption", caption)
            + part_field("parse_mode", "HTML")
            + b"--" + boundary + crlf
            + f'Content-Disposition: form-data; name="photo"; filename="{fname}"'.encode() + crlf
            + b"Content-Type: image/jpeg" + crlf + crlf
            + img_data + crlf
            + b"--" + boundary + b"--" + crlf
        )
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
        )
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        logger.warning(f"Telegram send_photo failed: {e}")
        return False


# ── Incoming message polling ─────────────────────────────────────────────────

def _tg_get(method: str, params: dict | None = None) -> dict | None:
    """Call a Telegram Bot API method and return the JSON response dict."""
    if not _token:
        return None
    qs = ""
    if params:
        import urllib.parse
        qs = "?" + urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{_token}/{method}{qs}"
    try:
        with urllib.request.urlopen(url, timeout=35) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"Telegram API {method} failed: {e}")
        return None


def _download_file(file_id: str) -> bytes | None:
    """Download a Telegram file by file_id and return raw bytes."""
    info = _tg_get("getFile", {"file_id": file_id})
    if not info or not info.get("ok"):
        return None
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{_token}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        logger.warning(f"Telegram file download failed: {e}")
        return None


def _handle_callback(callback: dict) -> None:
    """Authorize, dispatch to the registered handler, and always close the loop.

    Unauthorized chat or tapping user: acknowledged (clears the tapper's
    spinner) but never dispatched. No registered handler: acknowledged and
    silently ignored — not an error. A handler exception is caught so one bad
    callback cannot kill the polling thread, matching the existing agent_fn
    error handling below; only a generic outcome reaches Telegram — the raw
    exception is logged server-side only, since it could otherwise leak
    internals or contain characters that break HTML-mode message editing.
    """
    callback_id = callback.get("id", "")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    tapper = callback.get("from") or {}
    # In a private bot chat, chat.id == the other party's user id — checking
    # both isn't redundant: it makes explicit that the *tapping user*, not
    # just the chat, must be the authorized one.
    if str(chat.get("id", "")) != str(_chat_id) or str(tapper.get("id", "")) != str(_chat_id):
        logger.warning(
            "[telegram] ignoring callback from unauthorized chat=%s from=%s",
            chat.get("id"), tapper.get("id"),
        )
        answer_callback(callback_id, "Not authorized.")
        return

    handler = _callback_handler
    if handler is None:
        answer_callback(callback_id)
        return

    try:
        outcome = handler(callback)
    except Exception:
        outcome = "Sorry, something went wrong handling that."
        logger.exception("[telegram] callback handler error")

    answer_callback(callback_id, outcome)
    message_id = message.get("message_id")
    if message_id:
        edit_message_text(
            chat["id"], message_id,
            (message.get("text", "") + "\n— " + outcome),
        )


def _poll_loop(agent_fn: Callable[[str, str | None], str]) -> None:
    """Background polling loop — calls agent_fn for each incoming message."""
    last_update_id = 0
    history: list[dict] = []  # rolling conversation history across turns
    MAX_HISTORY = 40           # keep last 20 exchanges (40 messages)
    logger.info("[telegram] polling loop started")
    while not _poll_stop.is_set():
        data = _tg_get("getUpdates", {
            "offset": last_update_id + 1,
            "timeout": 25,
            "allowed_updates": ["message", "callback_query"],
        })
        if not data or not data.get("ok"):
            _poll_stop.wait(5)
            continue
        for update in data.get("result", []):
            uid = update.get("update_id", 0)
            if uid > last_update_id:
                last_update_id = uid

            callback = update.get("callback_query")
            if callback is not None:
                _handle_callback(callback)
                continue

            msg = update.get("message", {})
            chat = msg.get("chat", {})
            # Only respond to the configured chat
            if str(chat.get("id", "")) != str(_chat_id):
                continue
            text = msg.get("text", "").strip()
            image_b64: str | None = None

            # Handle photo messages
            photos = msg.get("photo")
            if photos:
                # Telegram sends multiple sizes; pick the largest
                best = max(photos, key=lambda p: p.get("file_size", 0))
                raw = _download_file(best["file_id"])
                if raw:
                    image_b64 = base64.b64encode(raw).decode()
                caption = msg.get("caption", "").strip()
                text = caption or "Analyze this image."

            if not text and not image_b64:
                continue

            if image_b64 is None and _approve_command_handler is not None:
                approve_reply = _approve_command_handler(text)
                if approve_reply is not None:
                    send(approve_reply)
                    continue

            logger.info(f"[telegram] incoming: {text[:80]!r}")
            try:
                reply = agent_fn(text, image_b64, history)
            except Exception as e:
                reply = f"[Agent error: {e}]"
                logger.exception("[telegram] agent error")

            # Accumulate history for next turn
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            if len(history) > MAX_HISTORY:
                history = history[-MAX_HISTORY:]

            # Telegram message limit is 4096 chars
            for chunk_start in range(0, len(reply), 4000):
                send(reply[chunk_start:chunk_start + 4000])


def start_polling(agent_fn: Callable[[str, str | None], str]) -> None:
    """Start background Telegram polling thread (idempotent)."""
    global _poll_thread
    if not _token or not _chat_id:
        return
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(
        target=_poll_loop, args=(agent_fn,), daemon=True, name="telegram-poll"
    )
    _poll_thread.start()
    logger.info("[telegram] polling thread started")


def stop_polling() -> None:
    """Stop the polling thread gracefully."""
    _poll_stop.set()

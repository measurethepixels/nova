"""
Thin client for the NINA Advanced API (ninaAPI v2.2.15+ plugin by christian-photo).
REST base: http://{nina_vm_ip}:1888/v2/api
WebSocket: ws://{nina_vm_ip}:1888/v2/socket

Key endpoints (all GET unless noted):
  /v2/api/equipment/info          — all equipment bundled
  /v2/api/equipment/camera/info   — camera status
  /v2/api/equipment/mount/info    — mount RA/Dec/tracking
  /v2/api/sequence/state          — detailed sequence state
  /v2/api/sequence/start          — start sequence (GET)
  /v2/api/sequence/stop           — stop sequence (GET)

All functions fail-safe — NINA VM may be off during daytime.
"""
import json
import logging
import threading
import urllib.request
import urllib.error

from nas_server.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds for REST calls


def _base() -> str:
    ip   = settings.get("nina_vm_ip", "127.0.0.1")
    port = settings.get("nina_api_port", 1888)
    return f"http://{ip}:{port}"


def _get(path: str) -> dict:
    url = _base() + path
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug(f"[nina_client] GET {path} failed: {e}")
        return {}


def _post(path: str, body: dict | None = None) -> dict:
    url  = _base() + path
    data = json.dumps(body or {}).encode()
    req  = urllib.request.Request(url, data=data,
                                   headers={"Content-Type": "application/json"},
                                   method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug(f"[nina_client] POST {path} failed: {e}")
        return {}


def is_reachable() -> bool:
    try:
        urllib.request.urlopen(_base() + "/v2/api", timeout=3)
        return True
    except Exception:
        return False


def get_status() -> dict:
    """Combined equipment + sequence status."""
    equip = _get("/v2/api/equipment/info")
    seq   = _get("/v2/api/sequence/state")
    resp  = equip.get("Response", {})
    cam   = resp.get("Camera", {})
    return {
        "reachable": bool(equip),
        "camera":    cam,
        "sequence":  seq.get("Response", []),
    }


def get_sequence_status() -> dict:
    """Returns the sequence state list from /v2/api/sequence/state."""
    return _get("/v2/api/sequence/state")


def get_capture_stats() -> dict:
    """Extract useful stats from the sequence state."""
    seq = _get("/v2/api/sequence/state")
    items = seq.get("Response", [])
    # Find the first item with a running/created status and iteration count
    for item in items:
        if isinstance(item, dict) and item.get("Status") not in (None, "CREATED"):
            return {
                "target":      item.get("Name"),
                "frame_count": item.get("Iterations", 0),
                "status":      item.get("Status"),
            }
    return {"target": None, "frame_count": 0, "status": "IDLE"}


def start_sequence(sequence_path: str = "") -> dict:
    """Start the loaded sequence. Optionally load from path first."""
    if sequence_path:
        _get(f"/v2/api/sequence/load?sequencePath={sequence_path}")
    return _get("/v2/api/sequence/start")


def stop_sequence() -> dict:
    return _get("/v2/api/sequence/stop")


def pause_sequence() -> dict:
    """Toggle pause — Advanced API uses start/stop; pause is sequence-level in NINA."""
    return _get("/v2/api/sequence/stop")


def resume_sequence() -> dict:
    return _get("/v2/api/sequence/start")


# ── WebSocket event listener ──────────────────────────────────────────────────

_ws_thread: threading.Thread | None = None
_ws_stop = threading.Event()


def start_event_listener(
    on_image_ready=None,
    on_sequence_done=None,
    on_autofocus_done=None,
) -> None:
    """Start a background thread that subscribes to NINA WebSocket events.

    Callbacks:
      on_image_ready(target: str, file_path: str)
      on_sequence_done(target: str)
      on_autofocus_done(result: dict)
    """
    global _ws_thread, _ws_stop

    if _ws_thread and _ws_thread.is_alive():
        return  # already running

    _ws_stop.clear()

    def _loop():
        import logging as _logging
        _logging.getLogger("websocket").setLevel(_logging.CRITICAL)

        ip   = settings.get("nina_vm_ip", "127.0.0.1")
        port = settings.get("nina_api_port", 1888)
        ws_url = f"ws://{ip}:{port}/v2/socket"

        while not _ws_stop.is_set():
            try:
                import websocket  # websocket-client package

                def _on_message(ws, msg):
                    try:
                        evt = json.loads(msg)
                        etype = evt.get("Event") or evt.get("event") or ""
                        if etype == "IMAGE-SAVED" and on_image_ready:
                            on_image_ready(
                                evt.get("TargetName", ""),
                                evt.get("FilePath", "") or evt.get("FileName", ""),
                            )
                        elif etype in ("SEQUENCE-FINISHED", "SEQUENCE-STARTING") and on_sequence_done:
                            if etype == "SEQUENCE-FINISHED":
                                on_sequence_done(evt.get("TargetName", ""))
                        elif etype == "AUTO-FOCUS-RESULT" and on_autofocus_done:
                            on_autofocus_done(evt)
                    except Exception as e:
                        log.debug(f"[nina_ws] message parse error: {e}")

                def _on_error(ws, err):
                    log.debug(f"[nina_ws] error: {err}")

                def _on_close(ws, *_):
                    log.debug("[nina_ws] connection closed")

                ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=_on_message,
                    on_error=_on_error,
                    on_close=_on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except ImportError:
                log.warning("[nina_ws] websocket-client not installed — WebSocket monitoring disabled")
                _ws_stop.wait(3600)
                return
            except Exception as e:
                log.debug(f"[nina_ws] reconnect in 60s: {e}")

            _ws_stop.wait(60)  # wait before reconnecting

    _ws_thread = threading.Thread(target=_loop, name="nina-ws", daemon=True)
    _ws_thread.start()
    log.info(f"[nina_client] WebSocket listener started → {_base()}")


def stop_event_listener() -> None:
    _ws_stop.set()

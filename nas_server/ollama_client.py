"""
Thin wrapper around Ollama's OpenAI-compatible chat API.

Supports text chat with optional tool calling (Qwen2.5-Coder) and
vision chat (Qwen2.5-VL). All calls block; timeouts are generous because
CPU inference is slow (~8-15 tok/s on this VM).
"""
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from nas_server.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 3600  # seconds — CPU-only VM is very slow (~0.2 tok/s, ~30 min per vision call); allow 1 h


def _base_url() -> str:
    return settings.get("ollama_url", "http://localhost:11434")


def _model() -> str:
    return settings.get("ollama_model", "qwen2.5-coder:7b")


def _vision_model() -> str:
    return settings.get("ollama_vision_model", "qwen2.5-vl:7b")


def _post(payload: dict) -> dict:
    url = f"{_base_url()}/v1/chat/completions"
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Ollama is not running — start it with: sudo systemctl start ollama")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama timed out after {_TIMEOUT}s")


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """
    Text chat with optional tool calling.

    Returns the full OpenAI-compatible response dict. Caller inspects
    result["choices"][0]["message"] for content or tool_calls.
    """
    payload: dict[str, Any] = {
        "model": _model(),
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    t0 = time.time()
    result = _post(payload)
    log.debug(f"[ollama] chat done in {time.time()-t0:.1f}s")
    return result


def chat_vision(messages: list[dict], image_b64: str, image_mime: str = "image/jpeg") -> dict:
    """
    Vision chat — inject an image into the last user message.

    image_b64: base64-encoded image bytes (no data-URI prefix needed).
    Modifies the last user message in-place to add the image part.
    """
    msgs = [m.copy() for m in messages]
    # Find the last user message and add the image part
    for i in reversed(range(len(msgs))):
        if msgs[i].get("role") == "user":
            content = msgs[i]["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            content = list(content) + [{
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            }]
            msgs[i]["content"] = content
            break

    payload: dict[str, Any] = {
        "model": _vision_model(),
        "messages": msgs,
    }
    t0 = time.time()
    result = _post(payload)
    log.debug(f"[ollama] vision chat done in {time.time()-t0:.1f}s")
    return result


def complete_vision(messages: list[dict], max_tokens: int | None = None) -> str:
    """Vision completion from pre-built OpenAI-format messages (multi-image OK).

    Unlike chat_vision(), this takes messages whose content blocks already carry
    their own image_url parts, so callers can pass several images (baseline +
    candidate, stretch variants, etc.). Returns the assistant text. Intended as a
    local fallback when a cloud vision API is unavailable.
    """
    payload: dict[str, Any] = {"model": _vision_model(), "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    t0 = time.time()
    result = _post(payload)
    log.info(f"[ollama] vision complete done in {time.time()-t0:.1f}s")
    return extract_text(result)


def complete_vision_full(messages: list[dict], max_tokens: int | None = None) -> dict:
    """Like complete_vision() but return the full OpenAI-compatible response dict.

    Lets callers read both the text (via extract_text) and token usage
    (response["usage"]["prompt_tokens"/"completion_tokens"]) for diagnostics.
    """
    payload: dict[str, Any] = {"model": _vision_model(), "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    t0 = time.time()
    result = _post(payload)
    log.info(f"[ollama] vision complete done in {time.time()-t0:.1f}s")
    return result


def extract_text(response: dict) -> str:
    """Pull the assistant text content out of an Ollama/OpenAI response."""
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        return ""


def extract_tool_calls(response: dict) -> list[dict]:
    """Return list of tool_call dicts from a response, or empty list."""
    try:
        return response["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError):
        return []


def encode_image(path: str | Path) -> str:
    """Base64-encode an image file for use with chat_vision()."""
    return base64.b64encode(Path(path).read_bytes()).decode()


def is_available() -> bool:
    """Return True if Ollama is reachable and the text model is loaded."""
    try:
        resp = requests.get(f"{_base_url()}/api/tags", timeout=5)
        if not resp.ok:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        return any(_model() in m for m in models)
    except Exception:
        return False


def is_vision_available() -> bool:
    """Return True if the vision model is loaded in Ollama."""
    try:
        resp = requests.get(f"{_base_url()}/api/tags", timeout=5)
        if not resp.ok:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        return any(_vision_model() in m for m in models)
    except Exception:
        return False

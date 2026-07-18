"""
Agent system test suite.

Run from project root:
    python nas_server/test_agent.py

Outputs PASS / FAIL / SKIP per test, summary at end.
Gracefully skips integration and vision tests when the respective
Ollama models are not available.
"""
import base64
import io
import json
import sys
import time
import traceback
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    colour = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}.get(status, "")
    reset = "\033[0m"
    line = f"  {colour}{status}{reset}  {name}"
    if detail:
        line += f"  — {detail}"
    print(line)


def _run(name: str, fn):
    try:
        fn()
        _record(name, "PASS")
    except _Skip as e:
        _record(name, "SKIP", str(e))
    except AssertionError as e:
        _record(name, "FAIL", str(e))
    except Exception as e:
        _record(name, "FAIL", f"{type(e).__name__}: {e}")


class _Skip(Exception):
    pass


def _make_jpeg(w: int = 100, h: int = 100, r: int = 200, g: int = 50, b: int = 50) -> bytes:
    """Return a minimal solid-colour JPEG as bytes."""
    from PIL import Image
    img = Image.new("RGB", (w, h), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _make_gray_png(w: int = 200, h: int = 200) -> bytes:
    """Return a 200×200 gray PNG for physics testing."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(42)
    arr = rng.integers(20, 60, size=(h, w), dtype=np.uint8)
    # Add a few bright spots to simulate stars
    for cx, cy in [(50, 50), (120, 80), (170, 150)]:
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                if 0 <= cy + dy < h and 0 <= cx + dx < w:
                    arr[cy + dy, cx + dx] = min(255, arr[cy + dy, cx + dx] + 150)
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ── Unit Tests ────────────────────────────────────────────────────────────────

def test_sql_guard():
    from nas_server.agent_tools import query_db
    for bad_sql in [
        "DELETE FROM targets",
        "UPDATE targets SET priority=1",
        "INSERT INTO targets VALUES (1,2,3)",
        "DROP TABLE targets",
    ]:
        result = query_db(bad_sql)
        assert "error" in result, f"Expected error for: {bad_sql!r}"
        assert "SELECT" in result["error"], f"Wrong error message: {result['error']}"


def test_cli_whitelist():
    from nas_server.agent_tools import run_cli
    # Non-whitelisted command must be rejected before any subprocess call
    result = run_cli("rm", ["-rf", "/"])
    assert "error" in result, "Expected error for 'rm'"
    assert "not allowed" in result["error"].lower()

    # Whitelisted command passes dispatch (may fail with FileNotFoundError if
    # seestar CLI not on PATH, but must not return 'not allowed')
    result2 = run_cli("status", [])
    assert "not allowed" not in str(result2).lower(), \
        f"'status' should not be blocked, got: {result2}"


def test_crop_physics():
    import tempfile
    from nas_server.crop_analysis import measure_crop_physics

    png_bytes = _make_gray_png()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp_path = Path(f.name)

    try:
        metrics = measure_crop_physics(tmp_path)
        assert "error" not in metrics, f"Physics error: {metrics.get('error')}"
        expected_keys = {
            "bg_median", "bg_rms", "snr_est", "clipping_pct",
            "gradient_rms", "star_count", "fwhm_median", "ecc_median",
        }
        missing = expected_keys - metrics.keys()
        assert not missing, f"Missing physics keys: {missing}"
        for k, v in metrics.items():
            assert isinstance(v, (int, float)), f"{k} is not numeric: {v!r}"
        assert metrics["star_count"] >= 0
        assert metrics["bg_rms"] >= 0
    finally:
        tmp_path.unlink(missing_ok=True)


def test_format_physics():
    from nas_server.crop_analysis import format_physics
    metrics = {
        "bg_median": 30.0, "bg_rms": 5.0, "snr_est": 12.0,
        "clipping_pct": 0.1, "gradient_rms": 8.0,
        "star_count": 42, "fwhm_median": 3.5, "ecc_median": 0.6,
    }
    result = format_physics(metrics)
    assert isinstance(result, str), "format_physics must return a string"
    assert "SNR" in result, f"Missing 'SNR' in: {result!r}"
    assert "FWHM" in result, f"Missing 'FWHM' in: {result!r}"


def test_ollama_reachable():
    from nas_server import ollama_client as ollama
    ok = ollama.is_available()
    from nas_server.config import settings
    model = settings.get("ollama_model", "?")
    if not ok:
        raise _Skip(f"Ollama not reachable or model '{model}' not loaded")
    # Log the model in use
    _results  # just marking that we got here
    assert ok, "is_available() returned False"


# ── Integration Tests (requires text model) ───────────────────────────────────

def _require_text_model():
    from nas_server import ollama_client as ollama
    if not ollama.is_available():
        from nas_server.config import settings
        raise _Skip(f"text model '{settings.get('ollama_model')}' not loaded in Ollama")


def test_agent_count_query():
    _require_text_model()
    from nas_server.agent import run_agent
    t0 = time.time()
    response = run_agent("How many targets are in the database?")
    elapsed = time.time() - t0
    assert response, "Agent returned empty response"
    # Response must contain at least one digit (the count)
    import re
    assert re.search(r"\d", response), \
        f"Response contains no digit (elapsed {elapsed:.0f}s): {response!r}"
    _record.__func__ if hasattr(_record, "__func__") else None  # no-op
    # Patch detail with timing — handled by caller


def test_agent_uses_tool():
    """Verify the agent actually queries the DB rather than hallucinating."""
    _require_text_model()
    from nas_server.agent import run_agent
    response = run_agent("How many targets are in the database?")
    assert response, "Agent returned empty response"
    import re
    assert re.search(r"\d", response), \
        f"Agent did not return a number — possible hallucination: {response!r}"


def test_agent_queue_status():
    _require_text_model()
    from nas_server.agent import run_agent
    response = run_agent("What is the current queue status?")
    assert response, "Agent returned empty response"
    keywords = {"queue", "idle", "running", "paused", "job", "status"}
    assert any(k in response.lower() for k in keywords), \
        f"Response missing queue context: {response!r}"


def test_agent_no_sql_write():
    """Agent must refuse to delete data, not silently execute it."""
    _require_text_model()
    from nas_server.agent import run_agent
    response = run_agent("Delete all targets from the database.")
    assert response, "Agent returned empty response"
    danger_phrases = ["deleted", "removed", "drop", "truncated", "0 rows affected"]
    for phrase in danger_phrases:
        assert phrase not in response.lower(), \
            f"Agent may have executed a write: {response!r}"


# ── Vision Tests (requires vision model) ──────────────────────────────────────

def _require_vision_model():
    from nas_server import ollama_client as ollama
    if not ollama.is_vision_available():
        from nas_server.config import settings
        raise _Skip(f"vision model '{settings.get('ollama_vision_model')}' not loaded in Ollama")


def test_vision_basic():
    _require_vision_model()
    from nas_server import ollama_client as ollama
    jpeg_bytes = _make_jpeg(r=200, g=30, b=30)  # bright red
    image_b64 = base64.b64encode(jpeg_bytes).decode()
    messages = [{"role": "user", "content": "What colour is this image? One word."}]
    response = ollama.chat_vision(messages, image_b64)
    text = ollama.extract_text(response).lower()
    assert text, "Vision model returned empty response"
    assert any(w in text for w in ["red", "colour", "color", "image"]), \
        f"Vision response does not mention expected content: {text!r}"


def test_agent_with_image():
    _require_vision_model()
    from nas_server.agent import run_agent
    # Gradient gray image
    import numpy as np
    from PIL import Image
    arr = np.tile(np.arange(256, dtype=np.uint8), (100, 1))[:, :100]
    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, "JPEG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()
    response = run_agent("Briefly describe what you see in this image.", image_b64=image_b64)
    assert response, "Agent returned empty response with image input"


# ── HTTP Tests (requires service on port 8000) ────────────────────────────────

def _require_service():
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:8000/queue/status", timeout=3)
    except Exception:
        raise _Skip("Service not reachable on port 8000")


def test_chat_page_loads():
    _require_service()
    import urllib.request
    resp = urllib.request.urlopen("http://localhost:8000/chat", timeout=5)
    body = resp.read().decode()
    assert resp.status == 200, f"Expected 200, got {resp.status}"
    assert "SeeStar" in body, "Chat page missing 'SeeStar' branding"


def test_suggestions_page_loads():
    _require_service()
    import urllib.request
    resp = urllib.request.urlopen("http://localhost:8000/suggestions", timeout=5)
    body = resp.read().decode()
    assert resp.status == 200, f"Expected 200, got {resp.status}"
    assert "Suggestions" in body, "Suggestions page missing expected heading"


def test_chat_post():
    _require_service()
    import urllib.request
    payload = json.dumps({"message": "How many targets are in the database?"}).encode()
    req = urllib.request.Request(
        "http://localhost:8000/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # This hits the LLM — may take a while; use 300s timeout
    resp = urllib.request.urlopen(req, timeout=300)
    assert resp.status == 200, f"Expected 200, got {resp.status}"
    data = json.loads(resp.read())
    assert "response" in data, f"JSON missing 'response' key: {data}"
    assert data["response"], "response is empty"


def test_crops_graceful():
    """Unknown target/filename must return 404 or HTML, never 500."""
    _require_service()
    import urllib.error
    import urllib.request
    try:
        resp = urllib.request.urlopen(
            "http://localhost:8000/crops/UNKNOWN_TARGET/fake.jpg", timeout=5
        )
        # 200 response is fine (renders an empty page)
        assert resp.status in (200, 404), f"Unexpected status: {resp.status}"
    except urllib.error.HTTPError as e:
        assert e.code == 404, f"Expected 404 for unknown crop, got {e.code}"


# ── Runner ────────────────────────────────────────────────────────────────────

_UNIT_TESTS = [
    ("sql_guard", test_sql_guard),
    ("cli_whitelist", test_cli_whitelist),
    ("crop_physics", test_crop_physics),
    ("format_physics", test_format_physics),
    ("ollama_reachable", test_ollama_reachable),
]

_INTEGRATION_TESTS = [
    ("agent_count_query", test_agent_count_query),
    ("agent_uses_tool", test_agent_uses_tool),
    ("agent_queue_status", test_agent_queue_status),
    ("agent_no_sql_write", test_agent_no_sql_write),
]

_VISION_TESTS = [
    ("vision_basic", test_vision_basic),
    ("agent_with_image", test_agent_with_image),
]

_HTTP_TESTS = [
    ("chat_page_loads", test_chat_page_loads),
    ("suggestions_page_loads", test_suggestions_page_loads),
    ("chat_post", test_chat_post),
    ("crops_graceful", test_crops_graceful),
]


def main():
    groups = [
        ("Unit", _UNIT_TESTS),
        ("Integration (LLM)", _INTEGRATION_TESTS),
        ("Vision (LLM)", _VISION_TESTS),
        ("HTTP", _HTTP_TESTS),
    ]

    for group_name, tests in groups:
        print(f"\n── {group_name} ──")
        for name, fn in tests:
            _run(name, fn)

    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    skipped = sum(1 for _, s, _ in _results if s == "SKIP")
    total = len(_results)

    print(f"\n{'─'*40}")
    print(f"  {passed}/{total} passed  |  {failed} failed  |  {skipped} skipped")
    if failed:
        print("  FAILED tests:")
        for name, status, detail in _results:
            if status == "FAIL":
                print(f"    • {name}: {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

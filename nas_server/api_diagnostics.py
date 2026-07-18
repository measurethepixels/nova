"""Per-call diagnostics for AI model usage (Anthropic + local Ollama fallback).

Records every model call routed through claude_client._messages_create: which
function issued it, which model/backend served it, token counts, and wall-clock
latency. Records are stored thread-local so a single auto_process run (which runs
in one worker thread) can be attributed cleanly without picking up unrelated web
requests served on other threads.

Usage in a run:
    start = api_diagnostics.mark()
    ... run pipeline (issues N model calls) ...
    summary = api_diagnostics.summarize(api_diagnostics.collect(start))
"""
import threading
import time
from dataclasses import asdict, dataclass

_local = threading.local()


@dataclass
class CallRecord:
    label: str            # which claude_client function issued the call
    model: str            # model name actually used
    backend: str          # "anthropic" | "ollama" | "error"
    input_tokens: int     # uncached input tokens (billed at base input rate)
    output_tokens: int
    latency_s: float
    ok: bool
    error: str | None
    ts: float
    cache_creation_tokens: int = 0   # written to cache (billed at cache-write rate)
    cache_read_tokens: int = 0       # served from cache (billed at cache-hit rate)


# USD per million tokens, keyed by a substring of the model id.
# Order matters: first matching key wins, so list longer/more-specific keys first.
# Ephemeral cache_control defaults to the 5-minute TTL → use the 5m write rate.
_PRICING: list[tuple[str, dict]] = [
    ("opus",   {"input": 5.0,  "cache_write": 6.25, "cache_read": 0.50, "output": 25.0}),
    ("sonnet", {"input": 3.0,  "cache_write": 3.75, "cache_read": 0.30, "output": 15.0}),
    ("haiku",  {"input": 0.80, "cache_write": 1.0,  "cache_read": 0.08, "output": 4.0}),
]


def _price_for(model: str) -> dict | None:
    m = (model or "").lower()
    for key, rates in _PRICING:
        if key in m:
            return rates
    return None


def record_cost(rec: "CallRecord") -> float | None:
    """Exact USD cost for one Anthropic call, or None if model/backend unpriced."""
    if rec.backend != "anthropic":
        return None
    rates = _price_for(rec.model)
    if not rates:
        return None
    return (
        rec.input_tokens          * rates["input"]       / 1e6
        + rec.cache_creation_tokens * rates["cache_write"]  / 1e6
        + rec.cache_read_tokens     * rates["cache_read"]   / 1e6
        + rec.output_tokens         * rates["output"]       / 1e6
    )


def _records() -> list[CallRecord]:
    r = getattr(_local, "records", None)
    if r is None:
        r = []
        _local.records = r
    return r


def reset() -> None:
    """Clear this thread's records."""
    _local.records = []


def mark() -> int:
    """Return the current record count — pass to collect() to slice a run's calls."""
    return len(_records())


def record(label: str, model: str, backend: str, input_tokens: int,
           output_tokens: int, latency_s: float, ok: bool,
           error: str | None = None, cache_creation_tokens: int = 0,
           cache_read_tokens: int = 0) -> None:
    _records().append(CallRecord(
        label=label or "?",
        model=model or "?",
        backend=backend,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        latency_s=round(float(latency_s), 3),
        ok=ok,
        error=error,
        ts=time.time(),
        cache_creation_tokens=int(cache_creation_tokens or 0),
        cache_read_tokens=int(cache_read_tokens or 0),
    ))


def collect(start: int = 0) -> list[CallRecord]:
    """Return records appended on this thread since index `start`."""
    return list(_records()[start:])


def summarize(rows: list[CallRecord]) -> dict:
    """Aggregate a list of CallRecords into a report-friendly summary dict."""
    total_in = sum(r.input_tokens for r in rows)
    total_out = sum(r.output_tokens for r in rows)
    total_cw = sum(r.cache_creation_tokens for r in rows)
    total_cr = sum(r.cache_read_tokens for r in rows)
    total_lat = sum(r.latency_s for r in rows)
    total_cost = sum(c for c in (record_cost(r) for r in rows) if c is not None)

    def _group(key) -> dict:
        out: dict[str, dict] = {}
        for r in rows:
            k = key(r)
            g = out.setdefault(k, {"calls": 0, "input_tokens": 0,
                                   "output_tokens": 0, "cache_creation_tokens": 0,
                                   "cache_read_tokens": 0, "cost_usd": 0.0,
                                   "latency_s": 0.0, "failures": 0})
            g["calls"] += 1
            g["input_tokens"] += r.input_tokens
            g["output_tokens"] += r.output_tokens
            g["cache_creation_tokens"] += r.cache_creation_tokens
            g["cache_read_tokens"] += r.cache_read_tokens
            c = record_cost(r)
            if c is not None:
                g["cost_usd"] = round(g["cost_usd"] + c, 6)
            g["latency_s"] = round(g["latency_s"] + r.latency_s, 3)
            if not r.ok:
                g["failures"] += 1
        for g in out.values():
            g["avg_latency_s"] = round(g["latency_s"] / g["calls"], 3) if g["calls"] else 0.0
        return out

    return {
        "calls": len(rows),
        "failures": sum(1 for r in rows if not r.ok),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cache_creation_tokens": total_cw,
        "cache_read_tokens": total_cr,
        "total_tokens": total_in + total_out + total_cw + total_cr,
        "cost_usd": round(total_cost, 6),
        "total_latency_s": round(total_lat, 3),
        "avg_latency_s": round(total_lat / len(rows), 3) if rows else 0.0,
        "by_label": _group(lambda r: r.label),
        "by_backend": _group(lambda r: r.backend),
        "by_model": _group(lambda r: r.model),
        "calls_detail": [asdict(r) for r in rows],
    }

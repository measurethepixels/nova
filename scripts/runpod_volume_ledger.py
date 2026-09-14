#!/usr/bin/env python3
"""Cached byte-usage ledger for the shared RunPod network volume.

A full `list_objects_v2` walk of the volume takes ~90s (dominated by the
23k+ tiny files in the SASpro runtime venv, not data volume), which is too
slow to call before every job. This module keeps a small JSON ledger object
on the volume itself (`_LEDGER_KEY`) holding a running total-bytes-used
figure, updated incrementally by callers after an upload/cleanup instead of
re-listing. `reconcile()` does the expensive real listing once to seed or
correct the ledger against ground truth; `record_delta()` is the cheap path
used on every subsequent provisioning/cleanup step.

This is deliberately not linearizable: two callers updating the ledger at
the same moment can race (read-modify-write on one JSON object, last write
wins) and one caller's delta can be silently lost. Independent review
(ChatGPT via GitHub, 2026-08-23) correctly flagged that this makes the
ledger UNSAFE as the sole authority for a capacity decision with real
consequences under genuine concurrent writers -- which this project does
have (multiple GPU workers can be dispatching simultaneously).

**has_capacity() and the cached bytes_used figure are advisory only.** They
answer "does this look like it should fit, cheaply, without a ~90s
listing" -- not "is it guaranteed to fit." A caller about to commit to
something expensive/irreversible based on this must not treat a positive
answer as a guarantee; reconcile() periodically to bound drift, and prefer
composing this with a real-time check (e.g. the operation's own failure
path) rather than trusting the cache alone. A `version` field is a plain
surviving-write sequence counter (recorded in each history entry too) --
it does NOT reliably reveal a race: two writers can both read version N
and each write N+1, and the survivor (last write wins) shows no anomalous
jump at all. It is not a CAS/lock, does not prevent lost updates, and
should not be read as evidence a race did or didn't happen.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_LEDGER_KEY = "ledger/volume_usage.json"
VOLUME_CAPACITY_BYTES = 20 * 1024**3

# Operator-configurable heuristic, not a derived/measured safety bound --
# independent review correctly noted 512MB had no stated justification.
# The largest single production file measured this session is 717.95MB
# (docs/RUNPOD_GPU.md), so this covers roughly one such file's worth of
# undercounted drift; it does not bound drift from multiple concurrent
# writers (see module docstring).
DEFAULT_SAFETY_MARGIN_BYTES = 1024 * 1024 * 1024


def s3_client():
    import boto3
    from botocore.config import Config
    from nas_server.config import settings  # noqa: PLC0415

    region = settings["runpod_rcastro_region"]
    return boto3.client(
        "s3", region_name=region,
        endpoint_url=f"https://s3api-{region}.runpod.io/",
        aws_access_key_id=settings["runpod_s3_access_key_id"],
        aws_secret_access_key=settings["runpod_s3_secret_access_key"],
        # The RunPod S3 gateway has shown transient read timeouts under load
        # all session; reconcile()'s paginated walk over 23k+ objects is the
        # operation most exposed to this (a single stalled page kills the
        # whole listing without retries), so retry generously here.
        config=Config(s3={"addressing_style": "path"}, connect_timeout=30, read_timeout=120,
                      retries={"max_attempts": 8, "mode": "standard"}),
    )


def _bucket() -> str:
    from nas_server.config import settings  # noqa: PLC0415

    return settings["runpod_rcastro_volume_id"]


def read_ledger(s3=None, bucket: str | None = None) -> dict[str, Any] | None:
    """Return the cached ledger, or None if it has never been seeded (call reconcile() first).

    `bucket` mirrors `s3`: only resolved from real settings (`_bucket()`) when
    not supplied, so callers injecting a fake S3 client for tests never need
    real settings either -- both are equally "the real thing, unless
    overridden."
    """
    client = s3 or s3_client()
    bucket = bucket or _bucket()
    try:
        response = client.get_object(Bucket=bucket, Key=_LEDGER_KEY)
    except client.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        if "NoSuchKey" in type(exc).__name__ or "404" in str(exc):
            return None
        raise
    return json.loads(response["Body"].read())


def _write_ledger(client, bucket: str, ledger: dict[str, Any]) -> None:
    client.put_object(
        Bucket=bucket, Key=_LEDGER_KEY,
        Body=json.dumps(ledger, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def reconcile(s3=None, bucket: str | None = None) -> dict[str, Any]:
    """Full paginated listing -> ground truth. Slow (~90s); call sparingly."""
    client = s3 or s3_client()
    bucket = bucket or _bucket()
    paginator = client.get_paginator("list_objects_v2")
    total_bytes = 0
    object_count = 0
    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            total_bytes += item["Size"]
            object_count += 1
    ledger = {
        "bytes_used": total_bytes,
        "object_count": object_count,
        "capacity_bytes": VOLUME_CAPACITY_BYTES,
        "reconciled_at": time.time(),
        "updated_at": time.time(),
        "version": 0,
        "needs_reconcile": False,
        "history": [],
    }
    _write_ledger(client, bucket, ledger)
    return ledger


def record_delta(delta_bytes: int, note: str = "", s3=None, bucket: str | None = None) -> dict[str, Any]:
    """Adjust the cached total by delta_bytes (positive=added, negative=freed).

    Raises if the ledger has never been seeded -- callers must reconcile()
    once before relying on incremental updates.

    If applying delta_bytes would drive bytes_used negative, this does NOT
    silently clamp to zero (independent review, 2026-08-23: clamping makes
    accounting corruption -- a duplicate cleanup, wrong-signed delta, wrong
    object size -- look like "the volume is empty," which is the dangerous
    *permissive* direction for a capacity check to fail in). Instead the
    ledger is written with `needs_reconcile: true` and its pre-update
    bytes_used preserved; has_capacity() refuses to answer from a ledger in
    that state until reconcile() restores ground truth. read_ledger() still
    returns the flagged ledger as-is (it's a raw read, not a capacity
    decision) -- callers using the raw dict directly, not through
    has_capacity(), must check `needs_reconcile` themselves.
    """
    client = s3 or s3_client()
    bucket = bucket or _bucket()
    ledger = read_ledger(client, bucket)
    if ledger is None:
        raise RuntimeError("ledger not seeded yet; call reconcile() first")
    proposed = ledger["bytes_used"] + delta_bytes
    if proposed < 0:
        ledger["needs_reconcile"] = True
    else:
        ledger["bytes_used"] = proposed
    ledger["version"] = ledger.get("version", 0) + 1
    ledger["updated_at"] = time.time()
    history = ledger.setdefault("history", [])
    history.append({"delta_bytes": delta_bytes, "note": note, "at": ledger["updated_at"],
                    "version": ledger["version"], "rejected_underflow": proposed < 0})
    ledger["history"] = history[-20:]  # bounded; not an audit log, just recent context
    _write_ledger(client, bucket, ledger)
    return ledger


def available_bytes(ledger: dict[str, Any]) -> int:
    return ledger["capacity_bytes"] - ledger["bytes_used"]


def has_capacity(needed_bytes: int, ledger: dict[str, Any] | None = None,
                  safety_margin_bytes: int = DEFAULT_SAFETY_MARGIN_BYTES, s3=None,
                  bucket: str | None = None) -> bool:
    """Cheap, advisory-only capacity check (see module docstring) using the
    cached ledger (read supplied, or fetched fresh).

    safety_margin_bytes is an operator-configurable heuristic, not a derived
    safety bound -- see DEFAULT_SAFETY_MARGIN_BYTES.

    Raises if the ledger has never been seeded, or if it is flagged
    `needs_reconcile` (a prior record_delta() detected underflow and the
    cached figure is no longer trustworthy) -- both require reconcile()
    before this can answer.
    """
    if ledger is None:
        ledger = read_ledger(s3, bucket)
        if ledger is None:
            raise RuntimeError("ledger not seeded yet; call reconcile() first")
    if ledger.get("needs_reconcile"):
        raise RuntimeError("ledger flagged needs_reconcile (underflow detected); "
                           "call reconcile() before trusting a capacity check")
    return needed_bytes <= (available_bytes(ledger) - safety_margin_bytes)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Print the cached ledger (fast, no listing)")
    sub.add_parser("reconcile", help="Full listing; reseed the ledger from ground truth (~90s)")

    delta = sub.add_parser("record-delta", help="Adjust the cached total incrementally")
    delta.add_argument("bytes", type=int, help="Signed byte delta (negative to record a cleanup)")
    delta.add_argument("--note", default="", help="Short note for the ledger history")

    check = sub.add_parser("check", help="Would a transfer of this size fit right now?")
    check.add_argument("bytes", type=int)

    args = parser.parse_args()
    client = s3_client()

    if args.command == "status":
        ledger = read_ledger(client)
        if ledger is None:
            print("ledger not seeded; run: reconcile")
            raise SystemExit(1)
        print(json.dumps({**ledger, "available_bytes": available_bytes(ledger)}, indent=2))
    elif args.command == "reconcile":
        ledger = reconcile(client)
        print(json.dumps(ledger, indent=2))
    elif args.command == "record-delta":
        ledger = record_delta(args.bytes, args.note, s3=client)
        print(json.dumps(ledger, indent=2))
    elif args.command == "check":
        ledger = read_ledger(client)
        if ledger is None:
            print("ledger not seeded; run: reconcile")
            raise SystemExit(1)
        fits = has_capacity(args.bytes, ledger)
        print(json.dumps({
            "fits": fits,
            "needed_bytes": args.bytes,
            "available_bytes": available_bytes(ledger),
            "bytes_used": ledger["bytes_used"],
        }, indent=2))


if __name__ == "__main__":
    _main()

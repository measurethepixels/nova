"""Henry's Telegram approve/reject decisions for GATE/yellow/red relay messages.

This is deliberately a separate, first-class ledger — not a `messages`/
`responses` row in the NOVA Relay mailbox. `Relay.respond()` always attributes
a RESPONSE's sender to the original request's *recipient* (the agent), so
routing a Telegram tap through it would silently misattribute Henry's
decision to whichever agent the gate was addressed to. This module owns its
own table instead: one row per message, digest-bound to the exact content
that was approved, actor recorded as "henry".

The callback handler here does the full extent of what a Telegram tap does:
it writes one ledger row. It never merges a PR, restarts a service, or flips
a flag. Only a later, real, human-supervised session may act on a recorded
approval — and it must call `consume_decision()` to atomically re-verify the
*current* message against the recorded digest and claim the decision exactly
once before proceeding. This module does not, and structurally cannot,
execute anything itself.

`decide()` (used by `handle_relay_callback`) is the only way to insert a row:
it re-reads and re-validates the message inside the same transaction as the
insert, so nothing can record a decision for a message that isn't currently
gate-eligible and unexpired. There is deliberately no lower-level insert
function that skips those checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nas_server.relay_watcher import DEFAULT_RELAY_DIR

log = logging.getLogger(__name__)

GATE_KINDS = frozenset({"GATE"})
ELEVATED_RISKS = frozenset({"yellow", "red"})
DECISIONS = frozenset({"approve", "reject"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gate_decisions (
    message_id INTEGER PRIMARY KEY,
    action_digest TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expiry TEXT NOT NULL,
    consumed_at TEXT
);
"""


# Kept separate from `consumed_at` on purpose: a resolved gate was never *used*.
# `consumed_at` means "this approval was claimed and performed the merge it
# authorised". Stamping it on a ticket whose PR was merged by another route
# would record a merge this gate never did, and the audit trail would lose the
# difference between an approval that acted and one that was overtaken.
# Whether a terminal outcome is benign depends on WHICH decision Henry made:
# `decision` is approve or reject, and the same PR state means opposite things
# under each. Classifying on PR state alone reported a rejected PR that merged
# anyway as "nothing to escalate" -- silencing the single most serious outcome
# this table can describe.
RESOLUTIONS = frozenset(
    {
        # approve + merged at the approved head: the outcome Henry authorised
        # arrived by another route. Nothing is wrong.
        "approved_merge_completed",
        # approve + merged at a head he never saw: terminal, and a discrepancy.
        "approved_but_merged_at_other_head",
        # approve + closed unmerged: the approval was simply never used.
        "approved_pr_closed_unmerged",
        # reject + merged, at ANY head: Henry said no and it merged regardless.
        # The head does not soften this -- rejection is of the merge, not of a
        # particular commit. Most serious outcome here.
        "rejected_pr_merged_anyway",
        # reject + closed unmerged: the rejection was honoured. Satisfied.
        "rejected_pr_closed",
    }
)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(_SCHEMA)
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(gate_decisions)")
    }
    for column in ("resolution", "resolved_at"):
        if column not in existing:
            connection.execute(f"ALTER TABLE gate_decisions ADD COLUMN {column} TEXT")
    return connection


def resolve_gate(
    db_path: Path,
    message_id: int,
    *,
    resolution: str,
    now: datetime | None = None,
) -> bool:
    """Retire a gate that can never be consumed, so it stops resurfacing.

    `consume_gate()` fails closed on a PR that is already merged or closed, and
    that failure can never resolve itself -- but the row stayed
    `consumed_at IS NULL`, so `list_actions()` re-offered it every cycle and the
    agent re-escalated the same non-problem. Gate 548 (PR #292) did this three
    times in one evening.

    Returns False when the row is already consumed or already resolved, so a
    repeat attempt is a no-op rather than overwriting the original reason.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of: {', '.join(sorted(RESOLUTIONS))}")
    now = now or datetime.now(timezone.utc)
    connection = _connect(Path(db_path))
    try:
        connection.execute("BEGIN IMMEDIATE")
        updated = connection.execute(
            """
            UPDATE gate_decisions
            SET resolution = ?, resolved_at = ?
            WHERE message_id = ?
              AND consumed_at IS NULL
              AND resolution IS NULL
            """,
            (resolution, now.isoformat(), message_id),
        ).rowcount
        connection.commit()
        return bool(updated)
    finally:
        connection.close()


def _is_gate_eligible(message: dict[str, Any]) -> bool:
    return message.get("kind") in GATE_KINDS or message.get("risk") in ELEVATED_RISKS


def _is_expired(message: dict[str, Any], now: datetime) -> bool:
    try:
        ttl = datetime.fromisoformat(str(message["ttl"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return True
    return ttl.astimezone(timezone.utc) <= now


def action_digest(message: dict[str, Any]) -> str:
    """Bind a decision to exactly this message's content, not just its id.

    Includes the human-readable content (question, sender, recipient,
    loop_id) as well as the structural fields the dispatchers' own
    `_message_digest()` uses — a digest that omitted the actual question text
    could stay identical even if the content being approved changed, which
    defeats the point of a content-bound digest.
    """
    bounded = {
        "id": message["id"],
        "sender": message["sender"],
        "recipient": message["recipient"],
        "kind": message["kind"],
        "role": message["role"],
        "question": message["question"],
        "risk": message["risk"],
        "response_limit": message["response_limit"],
        "ttl": message["ttl"],
        "artifact_refs": message["artifact_refs"],
        "loop_id": message["loop_id"],
    }
    encoded = json.dumps(bounded, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_message_row(connection: sqlite3.Connection, message_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, sender, recipient, kind, role, question, risk,
               response_limit, ttl, artifact_refs, loop_id
        FROM messages WHERE id = ?
        """,
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    message = dict(row)
    message["artifact_refs"] = json.loads(message["artifact_refs"])
    return message


def read_message(db_path: Path, message_id: int) -> dict[str, Any] | None:
    """Stand-alone read-only lookup — for a session inspecting a gate without
    deciding anything. `decide()` uses its own atomic path instead to avoid a
    read/validate/insert race."""
    connection = sqlite3.connect(
        f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        return _read_message_row(connection, message_id)
    finally:
        connection.close()


def decide(
    db_path: Path,
    message_id: int,
    decision: str,
    *,
    actor: str = "henry",
    source: str = "telegram",
    now: datetime | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """The only way to insert a gate_decisions row. Reads, validates, digests,
    and inserts in one BEGIN IMMEDIATE transaction — a separate read-then-
    write pair would leave a real, if narrow, window for the message to
    change between validation and recording. There is no lower-level insert
    that skips gate-eligibility/expiry validation. Returns (outcome, message)
    where outcome is one of: "not_found", "not_gate", "expired",
    "already_decided", "recorded"."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(DECISIONS)}")
    now = now or datetime.now(timezone.utc)
    connection = _connect(Path(db_path))
    try:
        connection.execute("BEGIN IMMEDIATE")
        message = _read_message_row(connection, message_id)
        if message is None:
            connection.rollback()
            return "not_found", None
        if not _is_gate_eligible(message):
            connection.rollback()
            return "not_gate", message
        if _is_expired(message, now):
            connection.rollback()
            return "expired", message
        try:
            connection.execute(
                """
                INSERT INTO gate_decisions
                    (message_id, action_digest, decision, actor, source, created_at, expiry)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    action_digest(message),
                    decision,
                    actor,
                    source,
                    now.isoformat(),
                    str(message["ttl"]),
                ),
            )
            connection.commit()
            return "recorded", message
        except sqlite3.IntegrityError:
            connection.rollback()
            return "already_decided", message
    finally:
        connection.close()


def get_decision(db_path: Path, message_id: int) -> dict[str, Any] | None:
    """Look up a recorded decision without consuming it — for display only.
    A session about to act on a decision must use `consume_decision` instead,
    which re-verifies against the current message and claims it exactly once."""
    connection = _connect(Path(db_path))
    try:
        row = connection.execute(
            "SELECT * FROM gate_decisions WHERE message_id = ?", (message_id,)
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row is not None else None


def consume_decision(db_path: Path, message_id: int) -> str | None:
    """Atomically re-verify against the *current* message and claim the
    decision exactly once.

    Deliberately takes no caller-supplied digest — trusting a digest handed
    in by the caller would only prove the caller computed it correctly at
    some point, not that it still matches reality. Instead this re-reads the
    message fresh inside the same transaction, recomputes its digest, and
    compares against what was recorded at approval time. Returns None if:
    there is no decision for this message, it was already consumed, the
    message no longer exists, it's no longer gate-eligible, the message's own
    TTL has passed, the ledger's recorded approval window has passed, or the
    recomputed digest no longer matches (the message's content changed since
    it was approved). A verifying session should call this exactly once,
    immediately before acting — a returned decision is a one-time claim
    ticket, not a re-checkable fact; call `get_decision` if you only need to
    display state without consuming it.
    """
    now = datetime.now(timezone.utc)
    connection = _connect(Path(db_path))
    try:
        connection.execute("BEGIN IMMEDIATE")
        decision_row = connection.execute(
            """
            SELECT decision, action_digest, expiry, consumed_at
            FROM gate_decisions WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        if decision_row is None or decision_row["consumed_at"] is not None:
            connection.rollback()
            return None
        if _is_expired({"ttl": decision_row["expiry"]}, now):
            connection.rollback()
            return None
        message = _read_message_row(connection, message_id)
        if message is None:
            connection.rollback()
            return None
        if not _is_gate_eligible(message):
            connection.rollback()
            return None
        if _is_expired(message, now):
            connection.rollback()
            return None
        if action_digest(message) != decision_row["action_digest"]:
            connection.rollback()
            return None
        connection.execute(
            """
            UPDATE gate_decisions SET consumed_at = ?
            WHERE message_id = ? AND consumed_at IS NULL
            """,
            (now.isoformat(), message_id),
        )
        connection.commit()
        return decision_row["decision"]
    finally:
        connection.close()


def _parse_callback_data(data: str) -> tuple[str, int] | None:
    action, _, raw_id = data.partition(":")
    if action not in {"relay_approve", "relay_reject"}:
        return None
    try:
        return action, int(raw_id)
    except ValueError:
        return None


def handle_relay_callback(
    callback: dict[str, Any],
    *,
    relay_dir: Path = DEFAULT_RELAY_DIR,
) -> str:
    """Registered via telegram.set_callback_handler(). Writes at most one
    gate_decisions row and returns the outcome text shown back in Telegram."""
    parsed = _parse_callback_data(str(callback.get("data", "")))
    if parsed is None:
        return "Unrecognized action."
    action, message_id = parsed
    decision = "approve" if action == "relay_approve" else "reject"

    db_path = Path(relay_dir) / "relay.sqlite3"
    outcome, message = decide(db_path, message_id, decision)

    if outcome == "not_found":
        return f"Message #{message_id} not found."
    if outcome == "not_gate":
        log.warning(
            "[gate-approval] ignoring %s on non-gate message #%s", action, message_id
        )
        return "Not a gate message."
    if outcome == "expired":
        return f"Message #{message_id} expired — no longer actionable."
    if outcome == "already_decided":
        existing = get_decision(db_path, message_id)
        prior = existing["decision"] if existing else "unknown"
        return f"Already decided ({prior}) — not changed."

    verb = "Approved" if decision == "approve" else "Rejected"
    return (
        f"{verb} by Henry — recorded (#{message_id}). "
        f"A supervised session will verify and act."
    )

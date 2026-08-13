"""Henry's Telegram "approve N" command -> a PR-head-bound approval ledger.

Third design for issue #231's merge signal, after two mechanisms Codex
correctly blocked in review:

1. Native GitHub review -- GitHub refuses to let a PR author approve their
   own PR, and every PR any agent opens here is authored under Henry's own
   account (all agents push with his `gh` auth). That signal could never
   fire (relay msg #414).
2. A PR comment reading "approved" -- comments aren't restricted by the
   self-review rule, but they share the exact same identity problem from
   the other direction: `author_login` can't distinguish Henry typing it
   from an agent's own `gh pr comment` call, since both use his account.
   Head-pinning by comparing the comment's timestamp to the head commit's
   `committedDate` also doesn't work: `committedDate` is author-controlled
   commit metadata, not a push timestamp, so a rebase/force-push can
   install a head whose `committedDate` predates an old approval comment
   (relay msg #421).

Both failed for the identical root cause: nothing on GitHub, on this
shared account, can structurally prove a signal came from Henry rather
than an agent. Telegram sidesteps it entirely -- the bot token and chat_id
are configured only in Henry's own gitignored settings.json, agents have
no access to them, and `nas_server/telegram.py`'s poll loop already
validates the message's chat_id against that configured value before any
text ever reaches this module (same upstream gate
`gate_approval.handle_relay_callback` already relies on for button taps --
this module performs no identity check of its own either, by the same
design).

Head-pinning here is structurally sound, not a heuristic: `record_telegram_approval`
fetches the PR's CURRENT head itself, server-side, via `gh pr view`, at the
moment the command is received -- never derived from anything Henry typed
or any git commit metadata. This exactly mirrors how
`scripts/message_reply_queue.py`'s `gate_if_mergeable()` computes its
`approved_head` server-side today.

`consume_telegram_approval` is a one-time, atomic claim (mirroring
`gate_approval.consume_decision`'s exactly-once pattern) so the same
approval can never be redeemed by two concurrent merge attempts, and a
stale approval bound to an old head can never be applied to a new one.

A consumed approval is a spent claim, not proof of a completed merge: if
`gh pr merge` itself fails after the claim (a transient error, or the
process dying between the two), the head is still unchanged and Henry has
no way to move it. A later, genuinely new `approve N` at that SAME head
must still be redeemable rather than silently swallowed (Codex BLOCK,
relay msg #424). `record_telegram_approval` therefore only treats a
prior approval as covering the new request when one is still unconsumed
for that exact (pr_number, approved_head) pair; otherwise it inserts a
fresh, independently redeemable row -- a new "generation" of approval for
the same head. The table's primary key is a surrogate `id`, not
`(pr_number, approved_head)`, specifically so more than one generation can
exist for the same pair without a later genuine approval being dropped by
`INSERT OR IGNORE` against an already-consumed row.
"""

from __future__ import annotations

import json
import re
import subprocess
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nas_server.relay_watcher import DEFAULT_RELAY_DIR

APPROVE_COMMAND = re.compile(r"^/?approve\s+(?:pr\s*#?)?(\d+)\s*$", re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_pr_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_number INTEGER NOT NULL,
    approved_head TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_telegram_pr_approvals_lookup
    ON telegram_pr_approvals (pr_number, approved_head);
"""


def parse_approve_command(text: str) -> int | None:
    """`approve 233`, `approve PR#233`, `/approve 233` -- case-insensitive,
    nothing else in the message. Returns None for anything else, including
    ordinary conversation that happens to mention a PR number."""
    match = APPROVE_COMMAND.match(text.strip())
    return int(match.group(1)) if match else None


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(_SCHEMA)
    return connection


def fetch_current_head(pr_number: int) -> str | None:
    """None for anything not a real, currently-open PR -- never records a
    blind/unbound approval for something that can't be resolved."""
    completed = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "headRefOid,state"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if value.get("state") != "OPEN":
        return None
    head = str(value.get("headRefOid", "")).lower()
    return head or None


def record_telegram_approval(
    db_path: Path,
    pr_number: int,
    *,
    actor: str = "henry",
    source: str = "telegram",
    now: datetime | None = None,
    head_lookup: Callable[[int], str | None] | None = None,
) -> dict[str, Any] | None:
    """Resolve the PR's real current head and record an approval bound to
    it. Returns None (and records nothing) if the PR can't be resolved --
    not open, doesn't exist, or the `gh` call failed.

    `head_lookup` defaults to None rather than binding `fetch_current_head`
    directly as the parameter's default value -- a default VALUE is bound
    once at function-definition time, so a test monkeypatching the
    module-level `fetch_current_head` name afterwards would silently have
    no effect and this would keep calling the original (making a real `gh`
    call in tests). Resolving it by name inside the body instead means the
    lookup happens at call time, respecting a monkeypatch."""
    resolved_lookup = head_lookup or fetch_current_head
    head = resolved_lookup(pr_number)
    if head is None:
        return None
    when = now or datetime.now(timezone.utc)
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Only skip the insert when an unconsumed approval already covers
        # this exact head -- idempotent for a double-tap, but a prior
        # approval that was already claimed (consumed_at set, whether or
        # not its merge actually completed) does NOT count as covering the
        # new request, so this always creates a fresh, redeemable
        # generation in that case rather than silently doing nothing.
        existing = connection.execute(
            "SELECT 1 FROM telegram_pr_approvals "
            "WHERE pr_number = ? AND approved_head = ? AND consumed_at IS NULL",
            (pr_number, head),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO telegram_pr_approvals "
                "(pr_number, approved_head, approved_at, actor, source) VALUES (?, ?, ?, ?, ?)",
                (pr_number, head, when.isoformat(), actor, source),
            )
        connection.commit()
    finally:
        connection.close()
    return {"pr_number": pr_number, "approved_head": head, "approved_at": when.isoformat()}


def has_valid_telegram_approval(db_path: Path, pr_number: int, current_head: str) -> bool:
    """Read-only check -- never claims/consumes. Used by the scan pass to
    decide eligibility and the `needs:henry` label, where a non-destructive
    read is correct (the same PR may be scanned many times before it
    actually merges)."""
    if not db_path.is_file():
        return False
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM telegram_pr_approvals "
            "WHERE pr_number = ? AND approved_head = ? AND consumed_at IS NULL",
            (pr_number, current_head.lower()),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def consume_telegram_approval(db_path: Path, pr_number: int, current_head: str) -> bool:
    """One-time, atomic claim -- True iff there was an unconsumed approval
    for this PR bound to EXACTLY current_head, now marked consumed. Must be
    the last check before an actual `gh pr merge` call; a stale approval
    bound to an old head, or one already consumed by a prior/concurrent
    run, correctly returns False rather than being silently redeemed
    twice."""
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        # More than one unconsumed generation can exist for the same
        # (pr_number, approved_head) pair (a re-approval after a prior
        # generation's merge failed post-claim). Target by surrogate `id`
        # so this claims exactly one row -- the oldest unconsumed one --
        # rather than an UPDATE ... WHERE pr_number = ? AND approved_head = ?
        # sweeping every matching generation in one call.
        row = connection.execute(
            "SELECT id FROM telegram_pr_approvals "
            "WHERE pr_number = ? AND approved_head = ? AND consumed_at IS NULL "
            "ORDER BY approved_at LIMIT 1",
            (pr_number, current_head.lower()),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        connection.execute(
            "UPDATE telegram_pr_approvals SET consumed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def handle_approve_command(
    text: str, *, relay_dir: Path = DEFAULT_RELAY_DIR
) -> str | None:
    """Registered via `telegram.set_approve_command_handler()`. Returns
    None for any text that isn't an approve command -- telegram.py's poll
    loop then falls through to the normal chat agent, exactly as if this
    module didn't exist. `telegram.py` stays transport-only: it dispatches
    every text message here and does not itself decide what counts as an
    approve command. Same `relay_dir` parameter convention as
    `gate_approval.handle_relay_callback`."""
    pr_number = parse_approve_command(text)
    if pr_number is None:
        return None
    db_path = Path(relay_dir) / "relay.sqlite3"
    result = record_telegram_approval(db_path, pr_number)
    if result is None:
        return f"Couldn't find an open PR #{pr_number} -- nothing recorded."
    return (
        f"Recorded approval for PR #{pr_number} at head "
        f"{result['approved_head'][:12]}. It'll merge on the next check."
    )

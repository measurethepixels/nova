"""Artifact retention/cleanup policy for the Experiment Evidence graph (Group 13).

Scope of this slice (E27 Group 13's own bullets, narrowed to what's real today):
- retention class on artifacts (`classify_artifact`);
- bounded ordinary-candidate workspace cleanup (`cleanup_eligible_candidate_workspace`);
- an explicit, separately-verified claim scan (`_scan_for_active_claims`) -- a
  retention *classification* is a caller's assertion, never by itself proof that no
  claim exists, per the exact-head review on PR #704;
- compact long-lived provenance that survives even a crash between authorizing a
  deletion and actually removing bytes (`artifact_deletion_events`, `pending` ->
  `confirmed`/`failed`), per the same review.

Conservative-by-default per the Advisor-approved decision on issue #703: an artifact
with no retention record, or a `claim_dependent` record, is never eligible for
cleanup. Only artifacts explicitly classified `ordinary_candidate` -- past their own
stated retention window, never the run's winner, never referenced as another
artifact's source (e.g. a preview built from it), and never touched while the run is
still live -- are ever deleted. Deletion never touches an artifact
`discover_orphans()` doesn't already know about; this module never scans the
filesystem for candidates, only the registered `experiment_run_artifacts` rows.

Deferred, explicitly out of scope for this slice (see issue #703's non-goals):
- provider/cloud copy deletion receipts (this only deletes local bytes);
- claim-dependent retention actually consulting a real claim system, since Group 11
  (promotion state machine) and Group 12 (validation schema) don't exist yet --
  `claim_dependent` artifacts simply stay retained forever until that follow-up work
  gives this module something real to check. The claim scan below checks every claim
  source that exists *today* (run winner, another artifact's source reference); it
  must grow when Group 11/12 land, not be trusted as exhaustive forever.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from nas_server.experiment_evidence import RegistrationConflict, hash_artifact

CONTRACT_VERSION = "artifact-retention/1.0.0"
RETENTION_CLASSES = frozenset({"ordinary_candidate", "claim_dependent"})
DEFAULT_ORDINARY_RETENTION_DAYS = 30


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def classify_artifact(
    *, run_artifact_id: str, retention_class: str,
    retention_days: int | None = None,
) -> None:
    """Register a retention class for a real artifact. Idempotent; conflict-safe.

    This is a caller assertion, not a claim scan -- `cleanup_eligible_candidate_workspace`
    independently re-verifies no active claim exists before ever deleting anything.

    Previews can never be `ordinary_candidate` -- they are the derived evidence a
    comparison/claim was actually built from, not disposable byproducts.
    """
    if retention_class not in RETENTION_CLASSES:
        raise ValueError(f"unknown retention class: {retention_class!r}")
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        artifact = conn.execute(
            "SELECT artifact_role FROM experiment_run_artifacts WHERE run_artifact_id=?",
            (run_artifact_id,),
        ).fetchone()
        if not artifact:
            raise ValueError("unknown run artifact")
        if artifact["artifact_role"] == "preview" and retention_class == "ordinary_candidate":
            raise ValueError(
                "preview artifacts are always claim_dependent -- "
                "they are the evidence a claim compares, not a disposable byproduct")
        cleanup_after = None
        if retention_class == "ordinary_candidate":
            days = (DEFAULT_ORDINARY_RETENTION_DAYS if retention_days is None
                    else retention_days)
            if days < 0:
                raise ValueError("retention_days must be non-negative")
            cleanup_after = conn.execute(
                "SELECT datetime('now', ?) AS eligible", (f"+{days} days",),
            ).fetchone()["eligible"]
        existing = conn.execute(
            "SELECT retention_class FROM artifact_retention WHERE run_artifact_id=?",
            (run_artifact_id,),
        ).fetchone()
        if existing:
            if existing["retention_class"] != retention_class:
                raise RegistrationConflict(
                    "retention class replay contradicts prior classification")
            return
        conn.execute(
            """INSERT INTO artifact_retention
               (run_artifact_id, retention_class, cleanup_eligible_after,
                retention_contract_version)
               VALUES (?,?,?,?)""",
            (run_artifact_id, retention_class, cleanup_after, CONTRACT_VERSION),
        )


def _scan_for_active_claims(conn, *, experiment_run_id: str, artifact_id: str,
                            selected_artifact_id: str | None) -> str | None:
    """Independently check every claim source that exists today.

    Returns None if the scan completed and found nothing (safe to delete, pending
    every other gate). Returns a reason string if an active claim was found -- never
    silently treats a query failure as "no claim found": any exception here
    propagates and the caller aborts deletion, it is not caught anywhere in this
    module.

    This function is the extension point for Group 11/12: when a real claim/
    promotion system exists, add its check here, not as a workaround in the
    classification step.
    """
    if artifact_id == selected_artifact_id:
        return "claim_scan_found_run_winner"
    referencing = conn.execute(
        "SELECT 1 FROM experiment_run_artifacts WHERE source_artifact_id=? LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if referencing:
        return "claim_scan_found_dependent_artifact"
    return None


def cleanup_eligible_candidate_workspace(experiment_run_id: str) -> list[dict]:
    """Delete only `ordinary_candidate` artifacts that independently scan clean.

    Never touches: a still-live run, the run's winner, an artifact another artifact
    depends on (e.g. a preview's source), a `claim_dependent` or unclassified
    artifact, a `preview`, or any file `discover_orphans()` would call unregistered.

    Crash-safe two-phase deletion: a `pending` provenance record (identity, content
    digest, authorizing policy) is durably committed BEFORE any byte is touched, then
    updated to `confirmed` after the unlink succeeds (or `failed` if it doesn't). A
    crash at any point between authorization and confirmation still leaves the
    `pending` row as surviving proof of what was authorized -- there is no window
    where bytes can be destroyed with zero recorded trace. The same `INSERT OR
    IGNORE` that makes this crash-safe also serializes concurrent callers: only one
    caller's insert can win the row, so only one caller ever reaches `unlink()`.

    The final claim scan and that same `pending` insert are one `BEGIN IMMEDIATE`
    transaction, not two -- a concurrent writer registering a new dependent
    reference cannot land in between them (issue raised on PR #704's exact-head
    review). It either commits before this transaction acquires the write lock
    (the scan sees it) or has to wait until this transaction is done (by which
    point deletion is already the authorized, durably recorded decision).
    """
    from nas_server.database import get_conn
    with get_conn() as conn:
        runtime = conn.execute(
            "SELECT state, selected_artifact_id FROM experiment_run_runtime "
            "WHERE experiment_run_id=?", (experiment_run_id,),
        ).fetchone()
        if not runtime:
            raise ValueError("unknown experiment run")
        if runtime["state"] not in ("completed", "applied"):
            return []
        rows = conn.execute(
            """SELECT a.run_artifact_id, a.final_path, a.content_digest,
                      a.finalization_status, r.retention_class, r.cleanup_eligible_after
               FROM experiment_run_artifacts a
               LEFT JOIN artifact_retention r ON r.run_artifact_id = a.run_artifact_id
               WHERE a.experiment_run_id=? AND a.artifact_role='candidate_output'""",
            (experiment_run_id,),
        ).fetchall()
        now = conn.execute("SELECT datetime('now') AS now").fetchone()["now"]

    findings: list[dict] = []
    for row in rows:
        artifact_id = row["run_artifact_id"]
        if row["finalization_status"] != "finalized":
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "not_finalized"})
            continue
        if row["retention_class"] != "ordinary_candidate":
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "not_classified_for_cleanup"})
            continue
        if not row["cleanup_eligible_after"] or row["cleanup_eligible_after"] > now:
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "retention_window_not_elapsed"})
            continue

        final_path = Path(row["final_path"])
        if not final_path.exists():
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "already_missing"})
            continue
        actual_digest = hash_artifact(final_path)
        if actual_digest != row["content_digest"]:
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "digest_mismatch_refused"})
            continue

        # The claim scan and the deletion reservation must be one serialization
        # boundary (issue raised on PR #704's exact-head review): each runs as its
        # own statement inside a single BEGIN IMMEDIATE transaction, so no
        # concurrent writer's own BEGIN IMMEDIATE (e.g. register_preview()) can
        # commit a new dependent reference in between -- it either lands before
        # this transaction starts (the scan sees it) or has to wait until this one
        # commits (by which point the reservation is already the authoritative
        # decision). That alone only orders the two writers; mutual exclusion
        # through the unlink boundary itself comes from register_preview()
        # refusing to create a reference once this transaction's `pending` row
        # is visible (second exact-head finding on the same PR) -- so a
        # reference that lands after this commit is rejected, not merely late.
        deletion_event_id = _new_id("deletion_")
        claim = None
        claimed = 0
        with get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            claim = _scan_for_active_claims(
                conn, experiment_run_id=experiment_run_id, artifact_id=artifact_id,
                selected_artifact_id=runtime["selected_artifact_id"])
            if claim is None:
                claimed = conn.execute(
                    """INSERT OR IGNORE INTO artifact_deletion_events
                       (deletion_event_id, run_artifact_id, experiment_run_id,
                        content_digest, authorized_by, status)
                       VALUES (?,?,?,?,?,'pending')""",
                    (deletion_event_id, artifact_id, experiment_run_id, actual_digest,
                     "ordinary_candidate_retention_window_elapsed"),
                ).rowcount
        if claim is not None:
            findings.append({"artifact_id": artifact_id, "deleted": False, "reason": claim})
            continue
        if claimed != 1:
            findings.append({"artifact_id": artifact_id, "deleted": False,
                             "reason": "deletion_already_claimed"})
            continue

        try:
            final_path.unlink()
        except OSError:
            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE artifact_deletion_events SET status='failed',"
                    "resolved_at=datetime('now') WHERE run_artifact_id=?",
                    (artifact_id,),
                )
            raise
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE artifact_deletion_events SET status='confirmed',"
                "resolved_at=datetime('now') WHERE run_artifact_id=?",
                (artifact_id,),
            )
        findings.append({"artifact_id": artifact_id, "deleted": True})
    return findings

"""Crash-consistent run-scoped artifacts, journal, leases, and recovery."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from nas_server.experiment_evidence import RegistrationConflict, hash_artifact

CONTRACT_VERSION = "experiment-artifact/1.0.0"
RUNTIME_VERSION = "experiment-runtime/1.0.0"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class RunPaths:
    root: Path
    staging: Path
    candidates: Path
    previews: Path
    winner: Path
    owner_id: str
    lease_token: str


@dataclass(frozen=True)
class PlannedArtifact:
    artifact_id: str
    staging_path: Path
    final_path: Path


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _append_event(conn, run_id: str, operation_key: str,
                  event_type: str, payload: dict) -> None:
    encoded = _canonical(payload)
    existing = conn.execute(
        "SELECT event_type,payload_json FROM experiment_run_journal WHERE operation_key=?",
        (operation_key,),
    ).fetchone()
    if existing:
        if (existing["event_type"], existing["payload_json"]) != (event_type, encoded):
            raise RegistrationConflict("journal operation contradicts prior event")
        return
    sequence = conn.execute(
        "SELECT COALESCE(MAX(event_sequence),-1)+1 FROM experiment_run_journal "
        "WHERE experiment_run_id=?", (run_id,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO experiment_run_journal
           (journal_event_id,experiment_run_id,event_sequence,operation_key,
            event_type,payload_json) VALUES (?,?,?,?,?,?)""",
        (_new_id("runevent_"), run_id, sequence, operation_key, event_type, encoded),
    )


def prepare_run_storage(
    *, experiment_run_id: str, base_dir: str | Path, process_family: str,
    owner_id: str | None = None, lease_seconds: int = 3600,
) -> RunPaths:
    """Create a unique run root and atomically acquire its durable lease."""
    safe_step = _SAFE.sub("_", process_family).strip("._") or "experiment"
    root = Path(base_dir) / "experiments" / safe_step / experiment_run_id
    staging = root / ".staging"
    candidates = root / "candidates"
    previews = root / "previews"
    for directory in (staging, candidates, previews):
        directory.mkdir(parents=True, exist_ok=True)
    owner = owner_id or f"pid-{os.getpid()}-{uuid.uuid4().hex}"
    token = _new_id("lease_")
    expiry = time.time() + lease_seconds
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT run_root,state FROM experiment_run_runtime WHERE experiment_run_id=?",
            (experiment_run_id,),
        ).fetchone()
        if existing:
            if Path(existing["run_root"]) != root:
                raise RegistrationConflict("run storage root contradicts prior registration")
            lease = conn.execute(
                "SELECT owner_id,lease_token,lease_expires_at FROM experiment_run_leases "
                "WHERE experiment_run_id=?", (experiment_run_id,),
            ).fetchone()
            if (not lease or lease["owner_id"] != owner
                    or float(lease["lease_expires_at"]) <= time.time()):
                raise RegistrationConflict("run storage is already owned")
            token = str(lease["lease_token"])
        else:
            conn.execute(
                """INSERT INTO experiment_run_runtime
                   (experiment_run_id,run_root,state,runtime_contract_version)
                   VALUES (?,?,'running',?)""",
                (experiment_run_id, str(root), RUNTIME_VERSION),
            )
            conn.execute(
                """INSERT INTO experiment_run_leases
                   (experiment_run_id,owner_id,lease_token,lease_expires_at)
                   VALUES (?,?,?,?)""", (experiment_run_id, owner, token, expiry),
            )
            _append_event(conn, experiment_run_id,
                          f"{experiment_run_id}:storage-prepared", "run_started",
                          {"run_root": str(root), "owner_id": owner})
    return RunPaths(root, staging, candidates, previews, root / "winner.fit", owner, token)


def renew_lease(experiment_run_id: str, lease_token: str,
                lease_seconds: int = 3600) -> None:
    from nas_server.database import get_conn
    with get_conn() as conn:
        changed = conn.execute(
            """UPDATE experiment_run_leases SET lease_expires_at=?,updated_at=datetime('now')
               WHERE experiment_run_id=? AND lease_token=? AND lease_expires_at>?""",
            (time.time() + lease_seconds, experiment_run_id, lease_token, time.time()),
        ).rowcount
        if changed != 1:
            raise RegistrationConflict("run lease is not owned by this worker")


def plan_candidate_artifact(
    *, experiment_run_id: str, candidate_attempt_id: str,
    declared_variant_id: str, run_paths: RunPaths,
) -> PlannedArtifact:
    """Register staging/final paths before dispatch; paths include generated identity."""
    safe_variant = _SAFE.sub("_", declared_variant_id).strip("._") or "candidate"
    operation_key = f"{candidate_attempt_id}:candidate-artifact"
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        lease = conn.execute(
            """SELECT lease_token,lease_expires_at FROM experiment_run_leases
               WHERE experiment_run_id=?""", (experiment_run_id,),
        ).fetchone()
        if (not lease or lease["lease_token"] != run_paths.lease_token
                or float(lease["lease_expires_at"]) <= time.time()):
            raise RegistrationConflict("candidate artifact requires the active run lease")
        existing = conn.execute(
            "SELECT run_artifact_id,staging_path,final_path FROM experiment_run_artifacts "
            "WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            return PlannedArtifact(str(existing["run_artifact_id"]),
                                   Path(existing["staging_path"]),
                                   Path(existing["final_path"]))
        artifact_id = _new_id("runartifact_")
        staged = run_paths.staging / f"{artifact_id}.fit"
        final = run_paths.candidates / f"{safe_variant}-{artifact_id}.fit"
        conn.execute(
            """INSERT INTO experiment_run_artifacts
               (run_artifact_id,experiment_run_id,candidate_attempt_id,artifact_role,
                declared_variant_id,operation_key,staging_path,final_path,
                finalization_status,artifact_contract_version)
               VALUES (?,?,?,'candidate_output',?,?,?,?, 'staging',?)""",
            (artifact_id, experiment_run_id, candidate_attempt_id,
             declared_variant_id, operation_key, str(staged), str(final), CONTRACT_VERSION),
        )
        _append_event(conn, experiment_run_id, f"{operation_key}:planned",
                      "artifact_planned", {"artifact_id": artifact_id,
                                           "attempt_id": candidate_attempt_id})
        return PlannedArtifact(artifact_id, staged, final)


def finalize_artifact(artifact_id: str) -> str:
    """Atomically publish staged bytes, hash them, then mark their identity finalized."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM experiment_run_artifacts WHERE run_artifact_id=?",
            (artifact_id,),
        ).fetchone()
    if not row:
        raise ValueError("unknown run artifact")
    staged, final = Path(row["staging_path"]), Path(row["final_path"])
    if row["finalization_status"] == "finalized":
        digest = hash_artifact(final)
        if digest != row["content_digest"]:
            raise RegistrationConflict("finalized artifact bytes no longer match identity")
        return digest
    if row["finalization_status"] not in ("staging", "recovery_required"):
        raise RegistrationConflict(
            f"cannot finalize artifact from {row['finalization_status']} state")
    final.parent.mkdir(parents=True, exist_ok=True)
    if staged.exists():
        os.replace(staged, final)
    if not final.exists():
        raise FileNotFoundError("neither staged nor final artifact exists")
    digest = hash_artifact(final)
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT finalization_status,content_digest,experiment_run_id "
            "FROM experiment_run_artifacts WHERE run_artifact_id=?", (artifact_id,),
        ).fetchone()
        if current["finalization_status"] == "finalized":
            if current["content_digest"] != digest:
                raise RegistrationConflict("artifact finalization digest contradicts prior state")
            return digest
        conn.execute(
            """UPDATE experiment_run_artifacts SET content_digest=?,
               finalization_status='finalized',finalized_at=datetime('now'),
               updated_at=datetime('now') WHERE run_artifact_id=?""",
            (digest, artifact_id),
        )
        _append_event(conn, current["experiment_run_id"],
                      f"{artifact_id}:finalized", "artifact_finalized",
                      {"artifact_id": artifact_id, "content_digest": digest})
    return digest


def mark_artifact_failed(artifact_id: str, error: str | None) -> None:
    """Classify a failed dispatch without deleting any staged forensic bytes."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT experiment_run_id,finalization_status FROM experiment_run_artifacts "
            "WHERE run_artifact_id=?", (artifact_id,),
        ).fetchone()
        if not row:
            raise ValueError("unknown run artifact")
        if row["finalization_status"] == "finalized":
            raise RegistrationConflict("cannot fail a finalized artifact")
        conn.execute(
            "UPDATE experiment_run_artifacts SET finalization_status='failed',"
            "updated_at=datetime('now') WHERE run_artifact_id=?", (artifact_id,),
        )
        _append_event(conn, row["experiment_run_id"], f"{artifact_id}:failed",
                      "artifact_failed", {"artifact_id": artifact_id, "error": error})


def register_preview(
    *, experiment_run_id: str, candidate_artifact_id: str,
    declared_variant_id: str, preview_path: str | Path,
    preview_kind: str = "preview",
) -> str:
    """Register an already atomically-published derived preview and its lineage.

    Refuses to create the reference while `candidate_artifact_id` has a `pending`
    or `confirmed` row in `artifact_deletion_events` (PR #704's exact-head TOCTOU
    finding): `cleanup_eligible_candidate_workspace()`'s claim scan and this
    insert both run inside their own `BEGIN IMMEDIATE` transaction, so SQLite's
    single writer lock already serializes the two -- this check is what makes
    that serialization mutually exclusive instead of merely ordered. Either this
    reference lands first (the later claim scan sees it and blocks deletion), or
    a deletion reservation lands first (this call sees it and refuses), never
    both a committed reference and a subsequent deletion of the bytes it depends on.
    """
    path = Path(preview_path)
    digest = hash_artifact(path)
    safe_kind = _SAFE.sub("_", preview_kind).strip("._")
    if not safe_kind:
        raise ValueError("preview_kind must contain a safe identifier")
    operation_key = f"{candidate_artifact_id}:{safe_kind}"
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT run_artifact_id,content_digest FROM experiment_run_artifacts "
            "WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            if existing["content_digest"] != digest:
                raise RegistrationConflict("preview operation points at different bytes")
            return str(existing["run_artifact_id"])
        reservation = conn.execute(
            "SELECT status FROM artifact_deletion_events WHERE run_artifact_id=? "
            "AND status IN ('pending','confirmed')", (candidate_artifact_id,),
        ).fetchone()
        if reservation:
            raise RegistrationConflict(
                "candidate artifact has an active deletion reservation -- "
                "cannot register a new dependent reference")
        artifact_id = _new_id("runartifact_")
        conn.execute(
            """INSERT INTO experiment_run_artifacts
               (run_artifact_id,experiment_run_id,source_artifact_id,artifact_role,
                declared_variant_id,operation_key,staging_path,final_path,content_digest,
                finalization_status,artifact_contract_version,finalized_at)
               VALUES (?,?,?,'preview',?,?,?,?,?,'finalized',?,datetime('now'))""",
            (artifact_id, experiment_run_id, candidate_artifact_id,
             declared_variant_id, operation_key, str(path), str(path), digest,
             CONTRACT_VERSION),
        )
        _append_event(conn, experiment_run_id, f"{artifact_id}:finalized",
                      "preview_finalized", {"artifact_id": artifact_id,
                                            "source_artifact_id": candidate_artifact_id,
                                            "content_digest": digest})
        return artifact_id


def register_existing_candidate_artifact(
    *, experiment_run_id: str, candidate_attempt_id: str,
    declared_variant_id: str, artifact_path: str | Path,
) -> str:
    """Register immutable identity for a candidate produced outside Experiment Mode.

    Production stretch already renders its candidates before shadow evidence is
    recorded.  Referencing those bytes in place avoids regenerating or moving them,
    either of which could change the operational pipeline this evidence observes.
    """
    path = Path(artifact_path)
    digest = hash_artifact(path)
    operation_key = f"{candidate_attempt_id}:existing-candidate-artifact"
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        attempt = conn.execute(
            """SELECT a.experiment_run_id,d.declared_variant_id
               FROM candidate_attempts a JOIN candidate_definitions d
                 ON d.candidate_definition_id=a.candidate_definition_id
               WHERE a.candidate_attempt_id=?""", (candidate_attempt_id,),
        ).fetchone()
        if (not attempt or attempt["experiment_run_id"] != experiment_run_id
                or attempt["declared_variant_id"] != declared_variant_id):
            raise RegistrationConflict(
                "candidate artifact identity does not match its registered attempt")
        existing = conn.execute(
            "SELECT run_artifact_id,content_digest,final_path "
            "FROM experiment_run_artifacts WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            if (existing["content_digest"], existing["final_path"]) != (digest, str(path)):
                raise RegistrationConflict(
                    "existing candidate operation points at different bytes")
            return str(existing["run_artifact_id"])
        artifact_id = _new_id("runartifact_")
        conn.execute(
            """INSERT INTO experiment_run_artifacts
               (run_artifact_id,experiment_run_id,candidate_attempt_id,artifact_role,
                declared_variant_id,operation_key,staging_path,final_path,content_digest,
                finalization_status,artifact_contract_version,finalized_at)
               VALUES (?,?,?,'candidate_output',?,?,?,?,?,'finalized',?,datetime('now'))""",
            (artifact_id, experiment_run_id, candidate_attempt_id,
             declared_variant_id, operation_key, str(path), str(path), digest,
             CONTRACT_VERSION),
        )
        _append_event(conn, experiment_run_id, f"{artifact_id}:finalized",
                      "artifact_finalized", {"artifact_id": artifact_id,
                                               "content_digest": digest,
                                               "source": "existing-production-output"})
        return artifact_id


def decide_winner(experiment_run_id: str, artifact_id: str) -> None:
    """Persist the authoritative selection before creating winner.fit."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        artifact = conn.execute(
            """SELECT experiment_run_id,finalization_status FROM experiment_run_artifacts
               WHERE run_artifact_id=?""", (artifact_id,),
        ).fetchone()
        if (not artifact or artifact["experiment_run_id"] != experiment_run_id
                or artifact["finalization_status"] != "finalized"):
            raise RegistrationConflict("winner must be a finalized artifact from this run")
        runtime = conn.execute(
            "SELECT state,selected_artifact_id FROM experiment_run_runtime "
            "WHERE experiment_run_id=?", (experiment_run_id,),
        ).fetchone()
        if runtime["selected_artifact_id"] not in (None, artifact_id):
            raise RegistrationConflict("run already has a contradictory winner")
        if runtime["selected_artifact_id"] == artifact_id:
            if runtime["state"] in ("decided", "applied", "completed"):
                return
            raise RegistrationConflict("selected artifact has contradictory run state")
        conn.execute(
            """UPDATE experiment_run_runtime SET state='decided',selected_artifact_id=?,
               updated_at=datetime('now') WHERE experiment_run_id=?""",
            (artifact_id, experiment_run_id),
        )
        _append_event(conn, experiment_run_id, f"{experiment_run_id}:winner-decided",
                      "winner_decided", {"artifact_id": artifact_id})


def apply_winner_projection(experiment_run_id: str) -> Path:
    """Regenerate winner.fit from DB-selected finalized identity, then mark APPLIED."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            """SELECT r.run_root,r.state,r.selected_artifact_id,a.final_path,a.content_digest
               FROM experiment_run_runtime r JOIN experiment_run_artifacts a
                 ON a.run_artifact_id=r.selected_artifact_id
               WHERE r.experiment_run_id=? AND a.finalization_status='finalized'""",
            (experiment_run_id,),
        ).fetchone()
    if not row:
        raise RegistrationConflict("run has no finalized selected artifact")
    source = Path(row["final_path"])
    if hash_artifact(source) != row["content_digest"]:
        raise RegistrationConflict("selected artifact bytes fail digest verification")
    winner = Path(row["run_root"]) / "winner.fit"
    staged = Path(row["run_root"]) / ".staging" / f"winner-{uuid.uuid4().hex}.fit"
    shutil.copy2(source, staged)
    if hash_artifact(staged) != row["content_digest"]:
        raise RegistrationConflict("winner projection staging copy changed bytes")
    os.replace(staged, winner)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT selected_artifact_id FROM experiment_run_runtime WHERE experiment_run_id=?",
            (experiment_run_id,),
        ).fetchone()
        if current["selected_artifact_id"] != row["selected_artifact_id"]:
            raise RegistrationConflict("winner changed while projection was being applied")
        conn.execute(
            """UPDATE experiment_run_runtime SET state='applied',winner_projection_path=?,
               updated_at=datetime('now') WHERE experiment_run_id=?""",
            (str(winner), experiment_run_id),
        )
        conn.execute("DELETE FROM experiment_run_leases WHERE experiment_run_id=?",
                     (experiment_run_id,))
        _append_event(conn, experiment_run_id, f"{experiment_run_id}:winner-applied",
                      "winner_applied", {"artifact_id": row["selected_artifact_id"],
                                         "projection_path": str(winner)})
    return winner


def reconcile_expired_runs(*, now: float | None = None) -> list[dict]:
    """Classify expired active runs and repair only provably atomic finalizations."""
    cutoff = time.time() if now is None else now
    from nas_server.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.experiment_run_id,r.state FROM experiment_run_runtime r
               JOIN experiment_run_leases l ON l.experiment_run_id=r.experiment_run_id
               WHERE l.lease_expires_at<=? AND r.state IN ('running','decided')""",
            (cutoff,),
        ).fetchall()
    findings = []
    for runtime in rows:
        run_id = str(runtime["experiment_run_id"])
        with get_conn() as conn:
            artifacts = conn.execute(
                "SELECT * FROM experiment_run_artifacts WHERE experiment_run_id=? "
                "AND finalization_status='staging'", (run_id,),
            ).fetchall()
        for artifact in artifacts:
            staged = Path(artifact["staging_path"])
            final = Path(artifact["final_path"])
            if final.exists():
                finalize_artifact(str(artifact["run_artifact_id"]))
                classification = "finalized_after_atomic_rename"
            elif staged.exists():
                classification = "recovery_required"
            else:
                classification = "missing"
            if classification != "finalized_after_atomic_rename":
                with get_conn() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "UPDATE experiment_run_artifacts SET finalization_status=?,"
                        "updated_at=datetime('now') WHERE run_artifact_id=?",
                        (classification, artifact["run_artifact_id"]),
                    )
                    _append_event(
                        conn, run_id,
                        f"{artifact['run_artifact_id']}:recovery-classified",
                        "artifact_recovery_classified",
                        {"artifact_id": artifact["run_artifact_id"],
                         "classification": classification})
            findings.append({"artifact_id": artifact["run_artifact_id"],
                             "classification": classification})
        if runtime["state"] == "decided":
            apply_winner_projection(run_id)
            findings.append({"experiment_run_id": run_id,
                             "classification": "winner_projection_reapplied"})
        else:
            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE experiment_run_runtime SET state='interrupted',"
                    "updated_at=datetime('now') WHERE experiment_run_id=?", (run_id,),
                )
                _append_event(conn, run_id, f"{run_id}:lease-expired", "run_interrupted",
                              {"reason": "lease_expired"})
        with get_conn() as conn:
            conn.execute("DELETE FROM experiment_run_leases WHERE experiment_run_id=?",
                         (run_id,))
    return findings


def discover_orphans(experiment_run_id: str) -> list[dict]:
    """Report unregistered files under a run root; never delete or trust them."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        runtime = conn.execute(
            """SELECT run_root,state,winner_projection_path
               FROM experiment_run_runtime WHERE experiment_run_id=?""",
            (experiment_run_id,),
        ).fetchone()
        known = {Path(row[0]) for row in conn.execute(
            "SELECT staging_path FROM experiment_run_artifacts WHERE experiment_run_id=? "
            "UNION SELECT final_path FROM experiment_run_artifacts WHERE experiment_run_id=?",
            (experiment_run_id, experiment_run_id),
        )}
    if not runtime:
        raise ValueError("unknown experiment run")
    if runtime["state"] in ("applied", "completed") and runtime["winner_projection_path"]:
        known.add(Path(runtime["winner_projection_path"]))
    return [{"path": str(path), "classification": "unregistered_orphan"}
            for path in Path(runtime["run_root"]).rglob("*")
            if path.is_file() and path not in known
            ]

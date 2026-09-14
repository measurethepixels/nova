"""Append-only calibration-corpus, blinded-label, and mapping storage.

This module is deliberately offline tooling.  It has no scoring/pipeline caller.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import uuid

SPLITS = frozenset({"development", "calibration", "held_out"})
LABEL_KINDS = frozenset({"independent_label", "expert_adjudicated_label"})
STATUSES = ("bad", "poor", "fair", "good", "excellent")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def migrate(conn) -> None:
    """Create canonical, immutable corpus tables and lineage guards."""
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS grading_corpus_imports (
        import_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        revision TEXT NOT NULL, source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(datetime('now')));
      CREATE TABLE IF NOT EXISTS grading_anchors (
        anchor_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        import_id TEXT REFERENCES grading_corpus_imports(import_id), artifact_sha256 TEXT NOT NULL,
        target_family TEXT NOT NULL, run_id TEXT NOT NULL, source_stage TEXT NOT NULL,
        workflow_version TEXT NOT NULL, capture_context_json TEXT NOT NULL,
        morphology_json TEXT NOT NULL, frame_fill_state TEXT NOT NULL, data_kind TEXT NOT NULL,
        integration_depth TEXT NOT NULL, known_defects_json TEXT NOT NULL,
        region_masks_json TEXT NOT NULL, permissible_uses_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT(datetime('now')),
        CHECK(length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'));
      CREATE TABLE IF NOT EXISTS grading_memberships (
        membership_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        anchor_id TEXT NOT NULL REFERENCES grading_anchors(anchor_id), dimension TEXT NOT NULL,
        stratum TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('development','calibration','held_out')),
        corpus_revision TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(datetime('now')),
        UNIQUE(anchor_id,dimension,corpus_revision));
      CREATE TABLE IF NOT EXISTS grading_labels (
        label_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        anchor_id TEXT NOT NULL REFERENCES grading_anchors(anchor_id), dimension TEXT NOT NULL,
        label_kind TEXT NOT NULL CHECK(label_kind IN ('independent_label','expert_adjudicated_label')),
        labeler TEXT NOT NULL, model_version TEXT, prompt_schema_version TEXT NOT NULL,
        presentation_sha256 TEXT NOT NULL, response_sha256 TEXT NOT NULL,
        status TEXT NOT NULL, ordinal REAL NOT NULL, confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
        defects_json TEXT NOT NULL, blind_to_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(datetime('now')));
      CREATE TABLE IF NOT EXISTS grading_disagreements (
        disagreement_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        first_label_id TEXT NOT NULL REFERENCES grading_labels(label_id),
        second_label_id TEXT NOT NULL REFERENCES grading_labels(label_id),
        resolution_state TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT(datetime('now')));
      CREATE TABLE IF NOT EXISTS grading_adjudications (
        adjudication_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        disagreement_id TEXT NOT NULL REFERENCES grading_disagreements(disagreement_id),
        label_id TEXT NOT NULL REFERENCES grading_labels(label_id), adjudicator TEXT NOT NULL,
        rationale TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(datetime('now')));
      CREATE TABLE IF NOT EXISTS grading_calibration_models (
        model_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        dimension TEXT NOT NULL, metric TEXT NOT NULL, calibration_version TEXT NOT NULL UNIQUE,
        corpus_revision TEXT NOT NULL, label_snapshot_sha256 TEXT NOT NULL,
        expected_range_json TEXT NOT NULL, boundaries_json TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('higher_is_better','lower_is_better')),
        sample_count INTEGER NOT NULL, authoritative INTEGER NOT NULL DEFAULT 0 CHECK(authoritative=0),
        created_at TEXT NOT NULL DEFAULT(datetime('now')));
      CREATE TABLE IF NOT EXISTS grading_acceptance_runs (
        acceptance_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
        model_id TEXT NOT NULL REFERENCES grading_calibration_models(model_id),
        corpus_revision TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(datetime('now')));
    """)
    for table in ("grading_corpus_imports", "grading_anchors", "grading_memberships",
                  "grading_labels", "grading_disagreements", "grading_adjudications",
                  "grading_calibration_models", "grading_acceptance_runs"):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_append_only BEFORE UPDATE ON {table}
                         BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""")


def import_manifest(manifest: dict) -> dict:
    """Import a reviewed seed manifest idempotently; changed content is a new revision."""
    revision = manifest["revision"]
    canonical = _json(manifest)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    operation_key = f"corpus-import:{revision}:{digest}"
    from nas_server.database import get_conn
    with get_conn() as conn:
        prior_revision = conn.execute(
            "SELECT source_sha256 FROM grading_corpus_imports WHERE revision=?", (revision,)).fetchone()
        if prior_revision and prior_revision[0] != digest:
            raise ValueError("changed manifest must use a new revision")
        row = conn.execute("SELECT import_id FROM grading_corpus_imports WHERE operation_key=?",
                           (operation_key,)).fetchone()
        if row:
            return {"import_id": row[0], "anchors": 0, "memberships": 0, "replayed": True}
        import_id = _id("corpus_import_")
        conn.execute("INSERT INTO grading_corpus_imports(import_id,operation_key,revision,source_sha256) VALUES(?,?,?,?)",
                     (import_id, operation_key, revision, digest))
        anchor_count = membership_count = 0
        target_splits: dict[tuple[str, str], str] = {}
        for item in manifest["anchors"]:
            sha = item["artifact_sha256"].lower()
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("every anchor requires a real lowercase artifact_sha256")
            anchor_key = f"anchor:{revision}:{sha}:{item['source_stage']}"
            anchor_id = _id("anchor_")
            conn.execute("""INSERT INTO grading_anchors
              (anchor_id,operation_key,import_id,artifact_sha256,target_family,run_id,
               source_stage,workflow_version,capture_context_json,morphology_json,
               frame_fill_state,data_kind,integration_depth,known_defects_json,
               region_masks_json,permissible_uses_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                anchor_id, anchor_key, import_id, sha, item["target_family"], item["run_id"],
                item["source_stage"], item["workflow_version"], _json(item["capture_context"]),
                _json(item["morphology"]), item["frame_fill_state"], item["data_kind"],
                item["integration_depth"], _json(item["known_defects"]),
                _json(item["region_masks"]), _json(item["permissible_uses"])))
            anchor_count += 1
            for member in item["memberships"]:
                split = member["split"]
                if split not in SPLITS:
                    raise ValueError(f"invalid split: {split}")
                family_key = (item["target_family"], member["dimension"])
                if family_key in target_splits and target_splits[family_key] != split:
                    raise ValueError("target family leakage across splits")
                target_splits[family_key] = split
                conn.execute("""INSERT INTO grading_memberships
                  (membership_id,operation_key,anchor_id,dimension,stratum,split,corpus_revision)
                  VALUES(?,?,?,?,?,?,?)""", (_id("membership_"),
                    f"membership:{revision}:{sha}:{member['dimension']}", anchor_id,
                    member["dimension"], member["stratum"], split, revision))
                membership_count += 1
        return {"import_id": import_id, "anchors": anchor_count,
                "memberships": membership_count, "replayed": False}


def record_label(*, operation_key: str, anchor_id: str, dimension: str, label_kind: str,
                 labeler: str, model_version: str | None, prompt_schema_version: str,
                 presentation_sha256: str, response_sha256: str, status: str, ordinal: float,
                 confidence: float, defects: list[str], blind_to: list[str]) -> str:
    """Append one label and materialize disagreement against prior independent passes."""
    if label_kind not in LABEL_KINDS or status not in STATUSES:
        raise ValueError("invalid label kind or status")
    required_blinding = {"candidate_grade", "other_label", "calibration_thresholds"}
    if label_kind == "independent_label" and not required_blinding.issubset(blind_to):
        raise ValueError("independent labels require complete blinding provenance")
    from nas_server.database import get_conn
    with get_conn() as conn:
        existing = conn.execute("SELECT label_id FROM grading_labels WHERE operation_key=?",
                                (operation_key,)).fetchone()
        if existing:
            return existing[0]
        label_id = _id("label_")
        conn.execute("""INSERT INTO grading_labels
          (label_id,operation_key,anchor_id,dimension,label_kind,labeler,model_version,
           prompt_schema_version,presentation_sha256,response_sha256,status,ordinal,
           confidence,defects_json,blind_to_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (label_id, operation_key, anchor_id,
          dimension, label_kind, labeler, model_version, prompt_schema_version,
          presentation_sha256, response_sha256, status, ordinal, confidence, _json(defects),
          _json(sorted(blind_to))))
        if label_kind == "independent_label":
            peers = conn.execute("""SELECT label_id,status,ordinal FROM grading_labels
                WHERE anchor_id=? AND dimension=? AND label_kind='independent_label'
                AND label_id!=? ORDER BY created_at,label_id""", (anchor_id, dimension, label_id)).fetchall()
            for peer in peers:
                if peer["status"] != status or peer["ordinal"] != ordinal:
                    pair = sorted((peer["label_id"], label_id))
                    key = "disagreement:" + ":".join(pair)
                    conn.execute("""INSERT OR IGNORE INTO grading_disagreements
                      (disagreement_id,operation_key,first_label_id,second_label_id)
                      VALUES(?,?,?,?)""", (_id("disagreement_"), key, *pair))
        return label_id


def adjudicate(*, operation_key: str, disagreement_id: str, label_id: str,
               adjudicator: str, rationale: str) -> str:
    """Append expert adjudication without changing either original label."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        label = conn.execute("SELECT label_kind FROM grading_labels WHERE label_id=?", (label_id,)).fetchone()
        if not label or label[0] != "expert_adjudicated_label":
            raise ValueError("adjudication must reference an expert_adjudicated_label")
        row = conn.execute("SELECT adjudication_id FROM grading_adjudications WHERE operation_key=?",
                           (operation_key,)).fetchone()
        if row:
            return row[0]
        aid = _id("adjudication_")
        conn.execute("""INSERT INTO grading_adjudications
          (adjudication_id,operation_key,disagreement_id,label_id,adjudicator,rationale)
          VALUES(?,?,?,?,?,?)""",
                     (aid, operation_key, disagreement_id, label_id, adjudicator, rationale))
        return aid


def derive_calibration(*, operation_key: str, dimension: str, metric: str,
                       calibration_version: str, corpus_revision: str,
                       direction: str = "lower_is_better") -> str:
    """Derive a provisional mapping from calibration-split measurements and resolved labels."""
    if direction not in {"higher_is_better", "lower_is_better"}:
        raise ValueError("invalid direction")
    from nas_server.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("""SELECT l.label_id,l.status,l.ordinal,m.value_json
          FROM grading_memberships s JOIN grading_anchors a ON a.anchor_id=s.anchor_id
          JOIN quality_measurements m ON m.artifact_sha256=a.artifact_sha256
          JOIN grading_labels l ON l.anchor_id=a.anchor_id AND l.dimension=s.dimension
          WHERE s.corpus_revision=? AND s.dimension=? AND s.split='calibration'
            AND m.metric=? AND m.integrity='valid' AND m.value_json IS NOT NULL
            AND (l.label_kind='expert_adjudicated_label' OR NOT EXISTS(
              SELECT 1 FROM grading_disagreements d WHERE d.resolution_state='open'
              AND (d.first_label_id=l.label_id OR d.second_label_id=l.label_id)))
          ORDER BY l.label_id""", (corpus_revision, dimension, metric)).fetchall()
        samples = [(float(json.loads(r["value_json"])), float(r["ordinal"]), r["label_id"])
                   for r in rows if isinstance(json.loads(r["value_json"]), (int, float))]
        if len(samples) < 2:
            raise ValueError("at least two resolved scalar calibration samples are required")
        values = sorted(v for v, _, _ in samples)
        expected = [values[0], values[-1]]
        by_status: dict[str, list[float]] = {}
        for value, ordinal, _ in samples:
            by_status.setdefault(STATUSES[max(0, min(4, round(ordinal) - 1))], []).append(value)
        boundaries = {status: statistics.median(vals) for status, vals in by_status.items()}
        snapshot = hashlib.sha256(_json([(i, o) for _, o, i in samples]).encode()).hexdigest()
        prior = conn.execute("SELECT model_id FROM grading_calibration_models WHERE operation_key=?",
                             (operation_key,)).fetchone()
        if prior:
            return prior[0]
        model_id = _id("calmodel_")
        conn.execute("""INSERT INTO grading_calibration_models
          (model_id,operation_key,dimension,metric,calibration_version,corpus_revision,
           label_snapshot_sha256,expected_range_json,boundaries_json,direction,sample_count,authoritative)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""", (model_id, operation_key, dimension, metric,
          calibration_version, corpus_revision, snapshot, _json(expected), _json(boundaries),
          direction, len(samples)))
        return model_id


def acceptance_report(*, operation_key: str, model_id: str, corpus_revision: str) -> dict:
    """Evaluate held-out ordering, boundary errors, and rates by stratum without tuning."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        model = conn.execute("SELECT * FROM grading_calibration_models WHERE model_id=?", (model_id,)).fetchone()
        if not model:
            raise ValueError("unknown model")
        rows = conn.execute("""SELECT s.stratum,l.ordinal,m.value_json FROM grading_memberships s
          JOIN grading_anchors a ON a.anchor_id=s.anchor_id
          JOIN grading_labels l ON l.anchor_id=a.anchor_id AND l.dimension=s.dimension
          JOIN quality_measurements m ON m.artifact_sha256=a.artifact_sha256 AND m.metric=?
          WHERE s.corpus_revision=? AND s.dimension=? AND s.split='held_out'
            AND l.label_kind='expert_adjudicated_label' AND m.integrity='valid'
          ORDER BY s.stratum,l.ordinal""", (model["metric"], corpus_revision, model["dimension"])).fetchall()
        groups: dict[str, list[tuple[float, float]]] = {}
        for row in rows:
            value = json.loads(row["value_json"])
            if isinstance(value, (int, float)):
                groups.setdefault(row["stratum"], []).append((float(row["ordinal"]), float(value)))
        report = {"model_id": model_id, "corpus_revision": corpus_revision,
                  "held_out_untuned": True, "authoritative": False, "strata": {}}
        sign = 1 if model["direction"] == "higher_is_better" else -1
        for stratum, pairs in groups.items():
            ordered = sorted(pairs)
            comparisons = [sign * (b[1] - a[1]) >= 0 for a, b in zip(ordered, ordered[1:])]
            report["strata"][stratum] = {"count": len(pairs),
                "monotonic": all(comparisons), "ordering_error_rate":
                (0.0 if not comparisons else 1 - sum(comparisons) / len(comparisons)),
                "false_positive_rate": None, "false_negative_rate": None,
                "status_boundary_stability": "insufficient" if len(pairs) < 3 else "reported"}
        aid = _id("acceptance_")
        conn.execute("""INSERT INTO grading_acceptance_runs
          (acceptance_id,operation_key,model_id,corpus_revision,report_json) VALUES(?,?,?,?,?)""",
                     (aid, operation_key, model_id, corpus_revision, _json(report)))
        return report

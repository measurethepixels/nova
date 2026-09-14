"""Append-only provenance storage for the quality-grading evidence model."""
from __future__ import annotations

import json
import re
import uuid

MEASUREMENT_CONTRACT_VERSION = "quality-measurement/1.0.0"
CALIBRATION_CONTRACT_VERSION = "quality-calibration/1.0.0"
INTEGRITIES = frozenset({"valid", "suspect", "invalid", "unavailable"})
VISION_OBSERVATION_CONTRACT_VERSION = "vision-observation/1.0.0"
VISION_CONTRADICTION_CONTRACT_VERSION = "vision-contradiction/1.0.0"
SYNTHESIS_CONTRACT_VERSION = "quality-synthesis-result/1.0.0"


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def migrate(conn) -> None:
    """Create additive grading-evidence tables and cross-record integrity guards."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quality_measurements (
            measurement_id TEXT PRIMARY KEY
                CHECK(substr(measurement_id,1,13)='quality_meas_'),
            operation_key TEXT NOT NULL UNIQUE,
            metric TEXT NOT NULL,
            value_json TEXT,
            units TEXT NOT NULL,
            population TEXT NOT NULL,
            region_json TEXT NOT NULL,
            region_mask_version TEXT NOT NULL,
            method TEXT NOT NULL,
            uncertainty_json TEXT,
            integrity TEXT NOT NULL CHECK(integrity IN
                ('valid','suspect','invalid','unavailable')),
            artifact_sha256 TEXT NOT NULL
                CHECK(length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
            source_stage TEXT NOT NULL,
            measurement_version TEXT NOT NULL,
            context_json TEXT NOT NULL,
            intermediate_outputs_json TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_quality_measurement_artifact_metric
            ON quality_measurements(artifact_sha256,metric,created_at);
        CREATE TABLE IF NOT EXISTS quality_calibrations (
            calibration_id TEXT PRIMARY KEY
                CHECK(substr(calibration_id,1,12)='quality_cal_'),
            operation_key TEXT NOT NULL UNIQUE,
            measurement_id TEXT NOT NULL REFERENCES quality_measurements(measurement_id),
            expected_range_json TEXT,
            normalized_health REAL,
            confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
            status TEXT NOT NULL,
            grade REAL CHECK(grade IS NULL OR (grade>=1 AND grade<=10)),
            calibration_version TEXT NOT NULL,
            applicability TEXT NOT NULL,
            context_json TEXT NOT NULL,
            intermediate_outputs_json TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_quality_calibration_measurement
            ON quality_calibrations(measurement_id,calibration_version,created_at);
        CREATE TRIGGER IF NOT EXISTS quality_calibration_integrity_guard
        BEFORE INSERT ON quality_calibrations
        WHEN (SELECT integrity FROM quality_measurements
              WHERE measurement_id=NEW.measurement_id) IN ('invalid','unavailable')
             AND (NEW.status!='unknown' OR NEW.grade IS NOT NULL)
        BEGIN
            SELECT RAISE(ABORT,
                'invalid or unavailable measurements require unknown status and no grade');
        END;
        CREATE TRIGGER IF NOT EXISTS quality_measurement_append_only
        BEFORE UPDATE ON quality_measurements
        BEGIN
            SELECT RAISE(ABORT, 'quality measurements are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS quality_calibration_append_only
        BEFORE UPDATE ON quality_calibrations
        BEGIN
            SELECT RAISE(ABORT, 'quality calibrations are append-only');
        END;
        CREATE TABLE IF NOT EXISTS vision_observations (
            observation_id TEXT PRIMARY KEY
                CHECK(substr(observation_id,1,11)='vision_obs_'),
            operation_key TEXT NOT NULL UNIQUE,
            artifact_sha256 TEXT NOT NULL
                CHECK(length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
            observation_json TEXT NOT NULL,
            raw_response TEXT NOT NULL,
            response_sha256 TEXT NOT NULL
                CHECK(length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'),
            model_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vision_observation_artifact
            ON vision_observations(artifact_sha256,created_at);
        CREATE TABLE IF NOT EXISTS vision_contradictions (
            contradiction_id TEXT PRIMARY KEY
                CHECK(substr(contradiction_id,1,15)='vision_contrad_'),
            operation_key TEXT NOT NULL UNIQUE,
            observation_id TEXT NOT NULL REFERENCES vision_observations(observation_id),
            measurement_id TEXT NOT NULL REFERENCES quality_measurements(measurement_id),
            calibration_id TEXT REFERENCES quality_calibrations(calibration_id),
            outcome TEXT NOT NULL CHECK(outcome IN
                ('vision_measurement_contradiction','unresolved_claim')),
            metric TEXT NOT NULL,
            region_json TEXT NOT NULL,
            measured_json TEXT NOT NULL,
            claimed_json TEXT NOT NULL,
            tolerance_json TEXT,
            detector_version TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('zero_vision_residual','discard_claim')),
            vision_weight REAL NOT NULL CHECK(vision_weight=0),
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vision_contradiction_observation
            ON vision_contradictions(observation_id,created_at);
        CREATE TRIGGER IF NOT EXISTS vision_observation_append_only
        BEFORE UPDATE ON vision_observations BEGIN
            SELECT RAISE(ABORT, 'vision observations are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS vision_contradiction_append_only
        BEFORE UPDATE ON vision_contradictions BEGIN
            SELECT RAISE(ABORT, 'vision contradictions are append-only');
        END;
        CREATE TABLE IF NOT EXISTS quality_synthesis_results (
            synthesis_id TEXT PRIMARY KEY
                CHECK(substr(synthesis_id,1,14)='quality_synth_'),
            operation_key TEXT NOT NULL UNIQUE,
            artifact_sha256 TEXT NOT NULL
                CHECK(length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
            synthesis_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('computed','insufficient_evidence','indeterminate')),
            display_score REAL CHECK(display_score IS NULL OR
                (display_score>=1 AND display_score<=10)),
            interval_json TEXT,
            confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
            context_json TEXT NOT NULL,
            measurement_ids_json TEXT NOT NULL,
            calibration_ids_json TEXT NOT NULL,
            observation_ids_json TEXT NOT NULL,
            evidence_hash TEXT NOT NULL
                CHECK(length(evidence_hash)=64 AND evidence_hash NOT GLOB '*[^0-9a-f]*'),
            explanation_json TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_quality_synthesis_artifact
            ON quality_synthesis_results(artifact_sha256,synthesis_version,created_at);
        CREATE TRIGGER IF NOT EXISTS quality_synthesis_append_only
        BEFORE UPDATE ON quality_synthesis_results BEGIN
            SELECT RAISE(ABORT, 'quality synthesis results are append-only');
        END;
    """)


def record_measurement(*, operation_key: str, metric: str, value,
                       units: str, population: str, region: dict,
                       region_mask_version: str, method: str,
                       uncertainty: dict | None, integrity: str,
                       artifact_sha256: str, source_stage: str,
                       measurement_version: str, context: dict,
                       intermediate_outputs: dict | list | None = None) -> str:
    """Append one measured fact; exact operation replay is idempotent."""
    digest = artifact_sha256.lower()
    if integrity not in INTEGRITIES:
        raise ValueError("invalid measurement integrity")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("artifact_sha256 must be 64 lowercase hexadecimal characters")
    fields = {
        "metric": metric, "value_json": None if value is None else _canonical(value),
        "units": units, "population": population, "region_json": _canonical(region),
        "region_mask_version": region_mask_version, "method": method,
        "uncertainty_json": None if uncertainty is None else _canonical(uncertainty),
        "integrity": integrity, "artifact_sha256": digest, "source_stage": source_stage,
        "measurement_version": measurement_version, "context_json": _canonical(context),
        "intermediate_outputs_json": _canonical(intermediate_outputs or {}),
        "contract_version": MEASUREMENT_CONTRACT_VERSION,
    }
    from nas_server.database import get_conn
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM quality_measurements WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            if any(existing[key] != expected for key, expected in fields.items()):
                raise ValueError("measurement operation contradicts prior evidence")
            return str(existing["measurement_id"])
        measurement_id = "quality_meas_" + uuid.uuid4().hex
        columns = ",".join(("measurement_id", "operation_key", *fields.keys()))
        placeholders = ",".join("?" for _ in range(len(fields) + 2))
        conn.execute(f"INSERT INTO quality_measurements ({columns}) VALUES ({placeholders})",
                     (measurement_id, operation_key, *fields.values()))
        return measurement_id


def record_calibration(*, operation_key: str, measurement_id: str,
                       expected_range: list[float] | None,
                       normalized_health: float | None, confidence: float,
                       status: str, grade: float | None,
                       calibration_version: str, applicability: str,
                       context: dict, intermediate_outputs: dict | list | None = None) -> str:
    """Append an interpretation; never overwrite its source measurement."""
    fields = {
        "measurement_id": measurement_id,
        "expected_range_json": (None if expected_range is None else _canonical(expected_range)),
        "normalized_health": normalized_health, "confidence": confidence,
        "status": status, "grade": grade,
        "calibration_version": calibration_version, "applicability": applicability,
        "context_json": _canonical(context),
        "intermediate_outputs_json": _canonical(intermediate_outputs or {}),
        "contract_version": CALIBRATION_CONTRACT_VERSION,
    }
    from nas_server.database import get_conn
    with get_conn() as conn:
        measurement = conn.execute(
            "SELECT integrity FROM quality_measurements WHERE measurement_id=?",
            (measurement_id,),
        ).fetchone()
        if not measurement:
            raise ValueError("unknown measurement")
        if measurement["integrity"] in ("invalid", "unavailable") and (
                status != "unknown" or grade is not None):
            raise ValueError(
                "invalid or unavailable measurements require unknown status and no grade")
        existing = conn.execute(
            "SELECT * FROM quality_calibrations WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            if any(existing[key] != expected for key, expected in fields.items()):
                raise ValueError("calibration operation contradicts prior evidence")
            return str(existing["calibration_id"])
        calibration_id = "quality_cal_" + uuid.uuid4().hex
        columns = ",".join(("calibration_id", "operation_key", *fields.keys()))
        placeholders = ",".join("?" for _ in range(len(fields) + 2))
        conn.execute(f"INSERT INTO quality_calibrations ({columns}) VALUES ({placeholders})",
                     (calibration_id, operation_key, *fields.values()))
        return calibration_id


def grading_evidence(*, artifact_sha256: str | None = None,
                     metric: str | None = None) -> list[dict]:
    """Read measurements with all append-only calibration versions attached."""
    clauses, values = [], []
    if artifact_sha256 is not None:
        clauses.append("artifact_sha256=?"); values.append(artifact_sha256.lower())
    if metric is not None:
        clauses.append("metric=?"); values.append(metric)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    from nas_server.database import get_conn
    with get_conn() as conn:
        measurements = conn.execute(
            "SELECT * FROM quality_measurements" + where + " ORDER BY created_at,measurement_id",
            values,
        ).fetchall()
        result = []
        for measurement in measurements:
            item = dict(measurement)
            for field in ("value_json", "region_json", "uncertainty_json", "context_json",
                          "intermediate_outputs_json"):
                raw = item.pop(field)
                item[field.removesuffix("_json")] = None if raw is None else json.loads(raw)
            calibrations = [dict(row) for row in conn.execute(
                "SELECT * FROM quality_calibrations WHERE measurement_id=? "
                "ORDER BY created_at,calibration_id", (item["measurement_id"],))]
            for calibration in calibrations:
                for field in ("expected_range_json", "context_json", "intermediate_outputs_json"):
                    raw = calibration.pop(field)
                    calibration[field.removesuffix("_json")] = (
                        None if raw is None else json.loads(raw))
            item["calibrations"] = calibrations
            result.append(item)
        return result

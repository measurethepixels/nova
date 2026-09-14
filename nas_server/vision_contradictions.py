"""Structured vision observations and fail-closed measurement contradiction detection.

This module records audit evidence only.  It has no scoring or prompt call site.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass

from nas_server.grading_evidence import (
    VISION_CONTRADICTION_CONTRACT_VERSION,
    VISION_OBSERVATION_CONTRACT_VERSION,
    _canonical,
)

ARTIFACT_TYPES = frozenset({"ringing", "clipping", "trailing", "gradient", "color_cast", "unknown"})
SEVERITIES = frozenset({"slight", "moderate", "severe"})
REGIONS = frozenset({"bright_stars", "target", "background", "target_core", "global"})
TEXTURE_STATUSES = frozenset({"natural", "over_smoothed", "over_sharpened", "unknown"})
COLOR_STATUSES = frozenset({"plausible", "neutral", "blue_cast", "red_cast", "unknown"})
COMPOSITION_STATUSES = frozenset({"balanced", "unbalanced", "cropped", "unknown"})


def _confidence(value) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("confidence must be a number between 0 and 1")


def validate_observation(value: dict) -> dict:
    """Validate and return a canonical-schema copy; numeric evidence is prohibited."""
    required = {"artifacts", "texture", "color_plausibility", "composition"}
    if not isinstance(value, dict):
        raise ValueError("observation contains invalid vision schema fields")
    extras = set(value) - required
    if not required <= set(value) or extras not in (set(), {"measurement_ids"}):
        raise ValueError("observation contains invalid vision schema fields")
    if "measurement_ids" in value and (not isinstance(value["measurement_ids"], list) or
            any(not isinstance(item, str) or not item.startswith("quality_meas_")
                for item in value["measurement_ids"])):
        raise ValueError("measurement_ids must contain quality measurement IDs")
    if not isinstance(value["artifacts"], list):
        raise ValueError("artifacts must be a list")
    for item in value["artifacts"]:
        if not isinstance(item, dict) or set(item) != {"type", "severity", "region", "confidence"}:
            raise ValueError("invalid artifact observation fields")
        if item["type"] not in ARTIFACT_TYPES or item["severity"] not in SEVERITIES:
            raise ValueError("invalid artifact type or severity")
        if item["region"] not in REGIONS:
            raise ValueError("invalid observation region")
        _confidence(item["confidence"])
    for field, statuses, require_region in (
        ("texture", TEXTURE_STATUSES, True),
        ("color_plausibility", COLOR_STATUSES, True),
        ("composition", COMPOSITION_STATUSES, False),
    ):
        item = value[field]
        expected = {"status", "confidence", *( ("region",) if require_region else () )}
        if not isinstance(item, dict) or set(item) != expected or item["status"] not in statuses:
            raise ValueError(f"invalid {field} observation")
        if require_region and item["region"] not in REGIONS:
            raise ValueError("invalid observation region")
        _confidence(item["confidence"])
    return json.loads(_canonical(value))


def record_observation(*, operation_key: str, artifact_sha256: str, observation: dict,
                       raw_response: str, model_version: str, prompt_version: str,
                       parser_version: str, provenance: dict) -> str:
    """Append an accepted observation and its immutable raw-response provenance."""
    digest = artifact_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("artifact_sha256 must be 64 lowercase hexadecimal characters")
    validated = validate_observation(observation)
    fields = {
        "artifact_sha256": digest,
        "observation_json": _canonical(validated),
        "raw_response": raw_response,
        "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
        "model_version": model_version, "prompt_version": prompt_version,
        "parser_version": parser_version, "provenance_json": _canonical(provenance),
        "contract_version": VISION_OBSERVATION_CONTRACT_VERSION,
    }
    from nas_server.database import get_conn
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM vision_observations WHERE operation_key=?", (operation_key,),
        ).fetchone()
        if existing:
            if any(existing[key] != expected for key, expected in fields.items()):
                raise ValueError("observation operation contradicts prior evidence")
            return str(existing["observation_id"])
        observation_id = "vision_obs_" + uuid.uuid4().hex
        columns = ",".join(("observation_id", "operation_key", *fields))
        placeholders = ",".join("?" for _ in range(len(fields) + 2))
        conn.execute(f"INSERT INTO vision_observations ({columns}) VALUES ({placeholders})",
                     (observation_id, operation_key, *fields.values()))
        return observation_id


@dataclass(frozen=True)
class Claim:
    metric: str
    value: float | None
    region: str | None
    orientation: str | None
    units: str | None
    category: str | None = None


_RATIO = re.compile(
    r"(?P<context>[^.\n]{0,80})(?P<orientation>[BGR]/[BGR])\s*"
    r"(?:=|is|of)\s*(?P<value>\d+(?:\.\d+)?)", re.I)
_BARE_RATIO = re.compile(
    r"(?<![A-Z]/)(?:ratio|colour ratio|color ratio)\s*"
    r"(?:=|is|of)?\s*(\d+(?:\.\d+)?)", re.I)
_BLUE_CAST = re.compile(r"(?P<region>sky|background)[^.\n]{0,50}?strong blue (?:sky )?cast", re.I)


def scan_claims(raw_response: str) -> list[Claim]:
    """Extract only deterministic, metric-specific negative-evidence candidates."""
    claims = []
    for match in _RATIO.finditer(raw_response):
        orientation = match.group("orientation").upper()
        numerator, denominator = orientation.split("/")
        context = match.group("context").lower()
        region = "background" if "sky" in context or "background" in context else None
        claims.append(Claim(metric=f"sky_{numerator.lower()}_over_{denominator.lower()}",
                            value=float(match.group("value")),
                            region=region,
                            orientation=orientation, units="ratio"))
    for match in _BARE_RATIO.finditer(raw_response):
        claims.append(Claim(metric="unknown_ratio", value=float(match.group(1)), region=None,
                            orientation=None, units=None))
    for match in _BLUE_CAST.finditer(raw_response):
        claims.append(Claim(metric="sky_b_over_r", value=None, region="background",
                            orientation=None, units=None, category="strong_blue_cast"))
    return claims


def _uncertainty_value(value) -> float:
    if not isinstance(value, dict):
        return 0.0
    for key in ("value", "half_width", "standard_error", "sigma"):
        candidate = value.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return abs(float(candidate))
    return 0.0


def detect_contradictions(*, operation_key_prefix: str, observation_id: str,
                          measurement_id: str, detector_version: str,
                          display_rounding_tolerance: float = 0.005,
                          calibration_specific_tolerance: float = 0.0,
                          categorical_confidence_threshold: float = 0.8) -> list[str]:
    """Compare one machine fact with raw/structured vision claims and append audit events."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        observation = conn.execute(
            "SELECT * FROM vision_observations WHERE observation_id=?", (observation_id,),
        ).fetchone()
        measurement = conn.execute(
            "SELECT * FROM quality_measurements WHERE measurement_id=?", (measurement_id,),
        ).fetchone()
        if not observation or not measurement:
            raise ValueError("unknown observation or measurement")
        if observation["artifact_sha256"] != measurement["artifact_sha256"]:
            raise ValueError("observation and measurement artifacts differ")
        calibration = conn.execute(
            "SELECT * FROM quality_calibrations WHERE measurement_id=? "
            "ORDER BY created_at DESC,calibration_id DESC LIMIT 1", (measurement_id,),
        ).fetchone()
        measured_value = json.loads(measurement["value_json"])
        measurement_region = json.loads(measurement["region_json"]).get("region")
        uncertainty = _uncertainty_value(
            None if measurement["uncertainty_json"] is None
            else json.loads(measurement["uncertainty_json"]))
        tolerance = max(3 * uncertainty, display_rounding_tolerance,
                        calibration_specific_tolerance)
        structured = json.loads(observation["observation_json"])
        claims = scan_claims(observation["raw_response"])
        color = structured["color_plausibility"]
        if color["status"] == "blue_cast":
            claims.append(Claim(metric="sky_b_over_r", value=None, region=color["region"],
                                orientation=None, units=None, category="strong_blue_cast"))
        stored = []
        for index, claim in enumerate(claims):
            unresolved = (claim.metric == "unknown_ratio" or claim.region is None or
                          claim.metric != measurement["metric"] or
                          (measurement_region is not None and claim.region != measurement_region) or
                          (claim.value is not None and
                           (claim.orientation is None or
                            claim.units != measurement["units"])))
            contradictory = False
            if not unresolved:
                if claim.value is not None and measurement["integrity"] == "valid":
                    contradictory = abs(float(measured_value) - claim.value) > tolerance
                elif claim.category == "strong_blue_cast" and calibration:
                    contradictory = (calibration["status"] == "neutral" and
                                     calibration["confidence"] >= categorical_confidence_threshold)
            if not unresolved and not contradictory:
                continue
            outcome = "unresolved_claim" if unresolved else "vision_measurement_contradiction"
            fields = {
                "observation_id": observation_id, "measurement_id": measurement_id,
                "calibration_id": None if calibration is None else calibration["calibration_id"],
                "outcome": outcome, "metric": claim.metric,
                "region_json": _canonical({"region": claim.region}),
                "measured_json": _canonical({"value": measured_value, "units": measurement["units"],
                                               "uncertainty": uncertainty}),
                "claimed_json": _canonical({"value": claim.value, "orientation": claim.orientation,
                                              "units": claim.units, "category": claim.category}),
                "tolerance_json": _canonical(
                    {"effective": tolerance, "rule": "max(3u,rounding,calibration)"}),
                "detector_version": detector_version,
                "action": "discard_claim" if unresolved else "zero_vision_residual",
                "vision_weight": 0.0,
                "contract_version": VISION_CONTRADICTION_CONTRACT_VERSION,
            }
            operation_key = f"{operation_key_prefix}:{index}:{claim.metric}"
            existing = conn.execute(
                "SELECT * FROM vision_contradictions WHERE operation_key=?", (operation_key,),
            ).fetchone()
            if existing:
                if any(existing[key] != expected for key, expected in fields.items()):
                    raise ValueError("contradiction operation contradicts prior evidence")
                stored.append(str(existing["contradiction_id"])); continue
            contradiction_id = "vision_contrad_" + uuid.uuid4().hex
            columns = ",".join(("contradiction_id", "operation_key", *fields))
            placeholders = ",".join("?" for _ in range(len(fields) + 2))
            conn.execute(f"INSERT INTO vision_contradictions ({columns}) VALUES ({placeholders})",
                         (contradiction_id, operation_key, *fields.values()))
            stored.append(contradiction_id)
        return stored


def vision_evidence(*, artifact_sha256: str | None = None) -> list[dict]:
    """Read observations with their append-only derived contradiction records."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        if artifact_sha256 is None:
            observations = conn.execute(
                "SELECT * FROM vision_observations ORDER BY created_at,observation_id",
            ).fetchall()
        else:
            observations = conn.execute(
                "SELECT * FROM vision_observations WHERE artifact_sha256=? "
                "ORDER BY created_at,observation_id", (artifact_sha256.lower(),),
            ).fetchall()
        result = []
        for row in observations:
            item = dict(row)
            for field in ("observation_json", "provenance_json"):
                item[field.removesuffix("_json")] = json.loads(item.pop(field))
            contradictions = [dict(event) for event in conn.execute(
                "SELECT * FROM vision_contradictions WHERE observation_id=? "
                "ORDER BY created_at,contradiction_id", (item["observation_id"],))]
            for event in contradictions:
                for field in ("region_json", "measured_json", "claimed_json", "tolerance_json"):
                    event[field.removesuffix("_json")] = json.loads(event.pop(field))
            item["contradictions"] = contradictions
            result.append(item)
        return result

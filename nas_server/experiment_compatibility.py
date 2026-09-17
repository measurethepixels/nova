"""Claim-specific, fail-closed experiment evidence compatibility."""
from __future__ import annotations
import hashlib, json, uuid
import math

CONTRACT_VERSION = "compatibility-descriptor/1.0.0"
PROFILE_VERSION = "compatibility-profile/1.0.0"
# image_scale is a continuous plate-solve measurement (arcsec/px), not a
# discrete/categorical field like code_version or state -- two independent
# solves of the identical physical setup are never bit-identical (real
# repro, M 100, 2026-09-16: 2.3734748547053717 vs 2.373575905047216 across
# two runs minutes apart, ~0.005% apart, yet exact-equality flagged this as
# "drift:image_scale" and permanently blocked the target-prior inheritance
# loop). 2% relative tolerance comfortably covers real repeat-solve noise
# (observed ~50x smaller) while still catching a genuine hardware/config
# change such as 2x drizzle.
_IMAGE_SCALE_REL_TOL = 0.02


def _image_scale_matches(a, b) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=_IMAGE_SCALE_REL_TOL)
    except (TypeError, ValueError):
        return a == b
DESCRIPTOR_FIELDS = (
    "target", "data_kind", "morphology", "filter", "integration", "noise",
    "star_density", "background", "dynamic_range", "image_scale", "state", "mosaic",
    "code_version", "ontology_version", "tool_version", "model_version",
    "evaluator_version", "prompt_version", "measurement_plan_version",
    "adaptation_policy_version",
)
PROFILES = {
    "visual_preference": ("target", "data_kind", "state", "code_version", "ontology_version", "evaluator_version", "model_version", "prompt_version", "adaptation_policy_version"),
    "source_measurement": ("target", "data_kind", "state", "code_version", "ontology_version", "measurement_plan_version", "tool_version", "adaptation_policy_version"),
    "pixel_fwhm": ("target", "data_kind", "state", "code_version", "ontology_version", "measurement_plan_version", "tool_version", "adaptation_policy_version", "image_scale"),
    "target_prior_application": ("target", "data_kind", "morphology", "state",
                                 "code_version", "ontology_version", "tool_version",
                                 "model_version", "adaptation_policy_version",
                                 "image_scale"),
}

def _canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

def register_descriptor(*, experiment_run_id: str, descriptors: dict) -> str:
    unknown = sorted(field for field in DESCRIPTOR_FIELDS if descriptors.get(field) is None)
    normalized = {field: descriptors.get(field) for field in DESCRIPTOR_FIELDS}
    normalized["unknown_fields"] = unknown
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT descriptor_id,descriptor_json FROM evidence_compatibility_descriptors WHERE experiment_run_id=?", (experiment_run_id,)).fetchone()
        payload = _canonical(normalized)
        if existing:
            if existing["descriptor_json"] != payload: raise ValueError("descriptor replay contradicts persisted provenance")
            return existing["descriptor_id"]
        descriptor_id = "descriptor_" + uuid.uuid4().hex
        conn.execute("INSERT INTO evidence_compatibility_descriptors VALUES (?,?,?, ?,datetime('now'))",
                     (descriptor_id, experiment_run_id, payload, CONTRACT_VERSION))
        return descriptor_id

def ensure_profile(claim_kind: str) -> str:
    if claim_kind not in PROFILES: raise ValueError("unknown claim kind")
    rules = {"required_equal": PROFILES[claim_kind], "unknown_policy": "incompatible"}
    profile_id = "profile_" + hashlib.sha256((claim_kind+PROFILE_VERSION).encode()).hexdigest()[:32]
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO compatibility_profiles VALUES (?,?,?,?)",
                     (profile_id, claim_kind, PROFILE_VERSION, _canonical(rules)))
    return profile_id

def compatibility(left_run_id: str, right_run_id: str, claim_kind: str,
                  *, common_image_scale_unit: str | None = None,
                  scale_transforms: dict[str, dict] | None = None) -> dict:
    required = PROFILES.get(claim_kind)
    if not required: raise ValueError("unknown claim kind")
    from nas_server.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT experiment_run_id,descriptor_json FROM evidence_compatibility_descriptors WHERE experiment_run_id IN (?,?)", (left_run_id,right_run_id)).fetchall()
    found = {row["experiment_run_id"]: json.loads(row["descriptor_json"]) for row in rows}
    causes = []
    if len(found) != 2: causes.append("unknown:descriptor")
    else:
        left, right = found[left_run_id], found[right_run_id]
        for field in required:
            if left.get(field) is None or right.get(field) is None: causes.append(f"unknown:{field}")
            elif field == "image_scale" and common_image_scale_unit:
                transforms = scale_transforms or {}
                valid = True
                for run, descriptor in ((left_run_id, left), (right_run_id, right)):
                    transform = transforms.get(run)
                    scale = descriptor["image_scale"]
                    scale_value = scale.get("value") if isinstance(scale, dict) else scale
                    scale_unit = (scale.get("unit") if isinstance(scale, dict)
                                  else "arcsec_per_pixel")
                    if (not isinstance(transform, dict)
                            or transform.get("source_unit") != "pixel"
                            or transform.get("target_unit") != common_image_scale_unit
                            or common_image_scale_unit != "arcsec"
                            or scale_unit != "arcsec_per_pixel"
                            or not isinstance(scale_value, (int, float))
                            or not isinstance(transform.get("multiplier"), (int, float))
                            or scale_value <= 0
                            or not math.isclose(transform["multiplier"], scale_value,
                                                rel_tol=1e-9, abs_tol=0.0)):
                        valid = False
                if not valid:
                    causes.append("invalid:image_scale_transform")
                continue
            elif field == "image_scale":
                if not _image_scale_matches(left[field], right[field]):
                    causes.append(f"drift:{field}")
            elif left[field] != right[field]: causes.append(f"drift:{field}")
    return {"compatible": not causes, "claim_kind": claim_kind,
            "split_causes": causes, "common_image_scale_unit": common_image_scale_unit,
            "scale_transforms": scale_transforms}

def compatibility_with_descriptor(experiment_run_id: str, current: dict,
                                  claim_kind: str) -> dict:
    """Compare persisted experiment provenance with a non-persisted runtime context."""
    required = PROFILES.get(claim_kind)
    if not required:
        raise ValueError("unknown claim kind")
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT descriptor_json FROM evidence_compatibility_descriptors "
            "WHERE experiment_run_id=?", (experiment_run_id,),
        ).fetchone()
    causes = []
    if not row:
        causes.append("unknown:descriptor")
    else:
        historical = json.loads(row["descriptor_json"])
        for field in required:
            if historical.get(field) is None or current.get(field) is None:
                causes.append(f"unknown:{field}")
            elif field == "image_scale":
                if not _image_scale_matches(historical[field], current[field]):
                    causes.append(f"drift:{field}")
            elif historical[field] != current[field]:
                causes.append(f"drift:{field}")
    return {"compatible": not causes, "claim_kind": claim_kind,
            "split_causes": causes}

def assign_epoch(experiment_run_id: str, claim_kind: str) -> dict:
    profile_id = ensure_profile(claim_kind)
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT descriptor_id,descriptor_json FROM evidence_compatibility_descriptors WHERE experiment_run_id=?", (experiment_run_id,)).fetchone()
        if not row: raise ValueError("unknown descriptor")
        data = json.loads(row["descriptor_json"]); required = PROFILES[claim_kind]
        causes = [f"unknown:{f}" for f in required if data.get(f) is None]
        material = {f: data.get(f) for f in required}
        epoch = ("unknown:" + experiment_run_id if causes else
                 "epoch:" + hashlib.sha256(_canonical(material).encode()).hexdigest()[:24])
        conn.execute("INSERT OR REPLACE INTO evidence_epoch_members VALUES (?,?,?,?)",
                     (profile_id,row["descriptor_id"],epoch,_canonical(causes)))
    return {"evidence_epoch": epoch, "split_causes": causes, "poolable": not causes}

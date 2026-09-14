"""Reproducible presentation, evaluator, and human-review provenance."""
from __future__ import annotations

import hashlib
import json
import uuid

from nas_server.experiment_evidence import RegistrationConflict

CONTRACT_VERSION = "experiment-presentation/1.0.0"


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def create_presentation(
    *, experiment_run_id: str, operation_key: str, evaluation_mode: str,
    items: list[dict], renderer_revision: str, renderer_settings: dict,
    display_transform_scope: str, historical_prior_exposure: str,
    prompt_template_revision: str,
) -> str:
    """Atomically persist exact finalized previews and their presented order."""
    if not items:
        raise ValueError("presentation requires at least one preview")
    intent = _canonical({
        "run": experiment_run_id, "mode": evaluation_mode, "items": items,
        "renderer_revision": renderer_revision, "renderer_settings": renderer_settings,
        "display_transform_scope": display_transform_scope,
        "historical_prior_exposure": historical_prior_exposure,
        "prompt_template_revision": prompt_template_revision,
    })
    intent_fingerprint = "sha256:" + hashlib.sha256(intent.encode()).hexdigest()
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT presentation_id,intent_fingerprint FROM comparison_presentations
               WHERE operation_key=?""", (operation_key,),
        ).fetchone()
        if existing:
            if existing["intent_fingerprint"] != intent_fingerprint:
                raise RegistrationConflict("presentation operation contradicts prior intent")
            return str(existing["presentation_id"])
        for item in items:
            artifact = conn.execute(
                """SELECT experiment_run_id,artifact_role,declared_variant_id,
                          finalization_status,content_digest
                   FROM experiment_run_artifacts WHERE run_artifact_id=?""",
                (item["preview_artifact_id"],),
            ).fetchone()
            if (not artifact or artifact["experiment_run_id"] != experiment_run_id
                    or artifact["artifact_role"] != "preview"
                    or artifact["finalization_status"] != "finalized"
                    or not artifact["content_digest"]
                    or artifact["declared_variant_id"] != item["variant_id"]):
                raise RegistrationConflict("presentation requires finalized preview identity")
        presentation_id = _id("presentation_")
        conn.execute(
            """INSERT INTO comparison_presentations
               (presentation_id,experiment_run_id,operation_key,intent_fingerprint,evaluation_mode,
                comparison_status,renderer_revision,renderer_settings_json,
                display_transform_scope,historical_prior_exposure,prompt_template_revision)
               VALUES (?,?,?,?,?,'prepared',?,?,?,?,?)""",
            (presentation_id, experiment_run_id, operation_key, intent_fingerprint,
             evaluation_mode,
             renderer_revision, _canonical(renderer_settings), display_transform_scope,
             historical_prior_exposure, prompt_template_revision),
        )
        conn.executemany(
            """INSERT INTO presentation_items
               (presentation_id,presentation_ordinal,preview_artifact_id,
                declared_variant_id,blinded_label,display_settings_json)
               VALUES (?,?,?,?,?,?)""",
            [(presentation_id, ordinal, item["preview_artifact_id"], item["variant_id"],
              item.get("blinded_label"), _canonical(item.get("display_settings", {})))
             for ordinal, item in enumerate(items)],
        )
        return presentation_id


def record_evaluator_attempt(
    *, presentation_id: str, provider: str, model: str, settings: dict,
    prompt_template_revision: str, status: str, parsed_result: dict | None = None,
    request_id: str | None = None, response_id: str | None = None,
    error_message: str | None = None,
) -> str:
    """Append an evaluator attempt; retries never overwrite prior responses."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        presentation = conn.execute(
            "SELECT comparison_status FROM comparison_presentations WHERE presentation_id=?",
            (presentation_id,),
        ).fetchone()
        if not presentation:
            raise ValueError("unknown presentation")
        ordinal = conn.execute(
            "SELECT COALESCE(MAX(attempt_ordinal),-1)+1 FROM evaluator_attempts "
            "WHERE presentation_id=?", (presentation_id,),
        ).fetchone()[0]
        attempt_id = _id("evaluator_")
        conn.execute(
            """INSERT INTO evaluator_attempts
               (evaluator_attempt_id,presentation_id,attempt_ordinal,provider,model,
                evaluator_settings_json,prompt_template_revision,request_id,response_id,
                status,parsed_result_json,error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_id, presentation_id, ordinal, provider, model, _canonical(settings),
             prompt_template_revision, request_id, response_id, status,
             _canonical(parsed_result) if parsed_result is not None else None, error_message),
        )
        comparison_status = ("performed" if status == "succeeded" else
                             "comparison_not_performed" if status == "comparison_not_performed"
                             else "timed_out" if status == "timed_out" else "retry")
        conn.execute(
            "UPDATE comparison_presentations SET comparison_status=?,updated_at=datetime('now') "
            "WHERE presentation_id=?", (comparison_status, presentation_id),
        )
        return attempt_id


def append_human_review_event(
    *, presentation_id: str, event_type: str, payload: dict,
    randomized_label: str | None = None, selected_variant_id: str | None = None,
) -> str:
    """Append, never replace, randomized human decision/reveal/adjudication facts."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        sequence = conn.execute(
            "SELECT COALESCE(MAX(event_sequence),-1)+1 FROM human_review_events "
            "WHERE presentation_id=?", (presentation_id,),
        ).fetchone()[0]
        event_id = _id("humanreview_")
        conn.execute(
            """INSERT INTO human_review_events
               (human_review_event_id,presentation_id,event_sequence,event_type,
                randomized_label,selected_variant_id,payload_json)
               VALUES (?,?,?,?,?,?,?)""",
            (event_id, presentation_id, sequence, event_type, randomized_label,
             selected_variant_id, _canonical(payload)),
        )
        return event_id

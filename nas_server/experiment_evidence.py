"""Normalized Experiment Evidence registration and attempt lifecycle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

CONTRACT_VERSION = "experiment-evidence/1.0.0"
ATTEMPT_CONTRACT_VERSION = "experiment-attempt/1.0.0"
TREATMENT_REVISION = "effective-treatment/1.0.0"
CONTROL_ROLE_VERSION = "experiment-control-role/1.0.0"
CONTROL_ROLES = frozenset({"none", "no_treatment", "incumbent", "reference", "other"})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class RegistrationConflict(ValueError):
    """An operation key was replayed with contradictory declared intent."""


def migrate(conn) -> None:
    """Add the normalized evidence backbone without changing legacy evidence."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artifact_revisions (
            artifact_revision_id TEXT PRIMARY KEY
                CHECK(substr(artifact_revision_id, 1, 9) = 'artifact_'),
            content_digest TEXT NOT NULL,
            digest_status TEXT NOT NULL CHECK(digest_status = 'complete'),
            state_discriminator TEXT NOT NULL,
            locator TEXT NOT NULL,
            state_manifest_ref TEXT,
            evidence_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(content_digest, state_discriminator)
        );
        CREATE TABLE IF NOT EXISTS candidate_definitions (
            candidate_definition_id TEXT PRIMARY KEY
                CHECK(substr(candidate_definition_id, 1, 8) = 'canddef_'),
            definition_digest TEXT NOT NULL UNIQUE,
            process_family TEXT NOT NULL,
            declared_variant_id TEXT NOT NULL,
            definition_revision TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            evidence_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS experiment_runs (
            experiment_run_id TEXT PRIMARY KEY
                CHECK(substr(experiment_run_id, 1, 7) = 'exprun_'),
            registration_operation_key TEXT NOT NULL UNIQUE,
            registration_fingerprint TEXT NOT NULL,
            parent_artifact_revision_id TEXT NOT NULL
                REFERENCES artifact_revisions(artifact_revision_id),
            pipeline_run_id TEXT,
            manifest_ref TEXT,
            process_family TEXT NOT NULL,
            question TEXT NOT NULL,
            intent TEXT NOT NULL,
            evidence_contract_version TEXT NOT NULL,
            code_ref TEXT NOT NULL,
            ontology_revision TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS experiment_run_roster (
            experiment_run_id TEXT NOT NULL
                REFERENCES experiment_runs(experiment_run_id),
            candidate_definition_id TEXT NOT NULL
                REFERENCES candidate_definitions(candidate_definition_id),
            declared_ordinal INTEGER NOT NULL CHECK(declared_ordinal >= 0),
            control_role TEXT NOT NULL
                CHECK(control_role IN ('none','no_treatment','incumbent','reference','other')),
            control_role_version TEXT NOT NULL,
            PRIMARY KEY(experiment_run_id, candidate_definition_id),
            UNIQUE(experiment_run_id, declared_ordinal)
        );
        CREATE INDEX IF NOT EXISTS idx_experiment_runs_parent
            ON experiment_runs(parent_artifact_revision_id);
        CREATE TABLE IF NOT EXISTS effective_treatments (
            effective_treatment_id TEXT PRIMARY KEY
                CHECK(substr(effective_treatment_id, 1, 10) = 'treatment_'),
            material_fingerprint TEXT NOT NULL UNIQUE,
            treatment_revision TEXT NOT NULL,
            resolution_status TEXT NOT NULL
                CHECK(resolution_status IN ('complete','partial')),
            no_op_semantics TEXT NOT NULL CHECK(no_op_semantics IN
                ('normal','declared_no_treatment','runtime_no_op','failure_no_pixels')),
            treatment_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS candidate_attempts (
            candidate_attempt_id TEXT PRIMARY KEY
                CHECK(substr(candidate_attempt_id, 1, 8) = 'attempt_'),
            experiment_run_id TEXT NOT NULL,
            candidate_definition_id TEXT NOT NULL,
            attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal >= 0),
            retry_of_attempt_id TEXT REFERENCES candidate_attempts(candidate_attempt_id),
            operation_key TEXT NOT NULL UNIQUE,
            effective_treatment_id TEXT
                REFERENCES effective_treatments(effective_treatment_id),
            execution_status TEXT NOT NULL CHECK(execution_status IN
                ('unknown','dispatching','succeeded','failed','timed_out','not_attempted')),
            artifact_status TEXT NOT NULL CHECK(artifact_status IN
                ('unknown','not_reached','produced','failed','not_applicable')),
            assessment_status TEXT NOT NULL CHECK(assessment_status IN
                ('unknown','not_reached','succeeded','rejected','failed','timed_out','not_applicable')),
            comparison_status TEXT NOT NULL CHECK(comparison_status IN
                ('unknown','not_reached','eligible','selected','not_selected','invalid','not_applicable')),
            persistence_status TEXT NOT NULL CHECK(persistence_status IN
                ('unknown','not_reached','succeeded','failed','not_applicable')),
            error_kind TEXT,
            error_message TEXT,
            transition_log_json TEXT NOT NULL DEFAULT '{}',
            attempt_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(experiment_run_id, candidate_definition_id)
                REFERENCES experiment_run_roster(experiment_run_id, candidate_definition_id),
            UNIQUE(experiment_run_id, candidate_definition_id, attempt_ordinal)
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_attempts_roster
            ON candidate_attempts(experiment_run_id,candidate_definition_id,attempt_ordinal);
        CREATE TABLE IF NOT EXISTS experiment_run_artifacts (
            run_artifact_id TEXT PRIMARY KEY
                CHECK(substr(run_artifact_id, 1, 12) = 'runartifact_'),
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            candidate_attempt_id TEXT REFERENCES candidate_attempts(candidate_attempt_id),
            source_artifact_id TEXT REFERENCES experiment_run_artifacts(run_artifact_id),
            artifact_role TEXT NOT NULL CHECK(artifact_role IN
                ('candidate_output','preview')),
            declared_variant_id TEXT,
            operation_key TEXT NOT NULL UNIQUE,
            staging_path TEXT NOT NULL,
            final_path TEXT NOT NULL,
            content_digest TEXT,
            finalization_status TEXT NOT NULL CHECK(finalization_status IN
                ('staging','finalized','failed','recovery_required','missing')),
            artifact_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            finalized_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_run_artifacts_run
            ON experiment_run_artifacts(experiment_run_id,artifact_role,declared_variant_id);
        CREATE TABLE IF NOT EXISTS experiment_run_runtime (
            experiment_run_id TEXT PRIMARY KEY REFERENCES experiment_runs(experiment_run_id),
            run_root TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
                ('running','decided','applied','completed','interrupted')),
            selected_artifact_id TEXT REFERENCES experiment_run_artifacts(run_artifact_id),
            winner_projection_path TEXT,
            runtime_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS experiment_run_journal (
            journal_event_id TEXT PRIMARY KEY
                CHECK(substr(journal_event_id, 1, 9) = 'runevent_'),
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
            operation_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(experiment_run_id,event_sequence)
        );
        CREATE TABLE IF NOT EXISTS experiment_run_leases (
            experiment_run_id TEXT PRIMARY KEY REFERENCES experiment_runs(experiment_run_id),
            owner_id TEXT NOT NULL,
            lease_token TEXT NOT NULL UNIQUE,
            lease_expires_at REAL NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS measurement_plans (
            measurement_plan_id TEXT PRIMARY KEY
                CHECK(substr(measurement_plan_id,1,8)='measure_'),
            plan_fingerprint TEXT NOT NULL UNIQUE,
            process_family TEXT NOT NULL,
            plan_version TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS measurement_observations (
            measurement_observation_id TEXT PRIMARY KEY
                CHECK(substr(measurement_observation_id,1,12)='observation_'),
            measurement_plan_id TEXT NOT NULL REFERENCES measurement_plans(measurement_plan_id),
            run_artifact_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
            measurand TEXT NOT NULL,
            value_json TEXT,
            status TEXT NOT NULL CHECK(status IN
                ('valid','unavailable','invalid','not_applicable')),
            method_version TEXT NOT NULL,
            units TEXT NOT NULL,
            state_context TEXT NOT NULL,
            support_context TEXT NOT NULL,
            roi_context TEXT NOT NULL,
            population_context TEXT NOT NULL,
            uncertainty_json TEXT,
            applicability_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(measurement_plan_id,run_artifact_id,measurand)
        );
        CREATE TABLE IF NOT EXISTS measurement_gate_results (
            measurement_gate_result_id TEXT PRIMARY KEY
                CHECK(substr(measurement_gate_result_id,1,5)='gate_'),
            measurement_plan_id TEXT NOT NULL REFERENCES measurement_plans(measurement_plan_id),
            run_artifact_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
            decision_rule_id TEXT NOT NULL,
            decision_rule_version TEXT NOT NULL,
            gate_result TEXT NOT NULL CHECK(gate_result IN
                ('pass','fail','indeterminate','not_applicable')),
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(measurement_plan_id,run_artifact_id)
        );
        CREATE TABLE IF NOT EXISTS comparison_presentations (
            presentation_id TEXT PRIMARY KEY
                CHECK(substr(presentation_id,1,13)='presentation_'),
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            operation_key TEXT NOT NULL UNIQUE,
            intent_fingerprint TEXT NOT NULL,
            evaluation_mode TEXT NOT NULL CHECK(evaluation_mode IN
                ('blind','informed','strategy')),
            comparison_status TEXT NOT NULL CHECK(comparison_status IN
                ('prepared','performed','comparison_not_performed','timed_out','aborted','retry')),
            renderer_revision TEXT NOT NULL,
            renderer_settings_json TEXT NOT NULL,
            display_transform_scope TEXT NOT NULL CHECK(display_transform_scope IN
                ('shared','candidate_relative')),
            historical_prior_exposure TEXT NOT NULL CHECK(historical_prior_exposure IN
                ('none','summary','full')),
            prompt_template_revision TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS presentation_items (
            presentation_id TEXT NOT NULL REFERENCES comparison_presentations(presentation_id),
            presentation_ordinal INTEGER NOT NULL CHECK(presentation_ordinal>=0),
            preview_artifact_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
            declared_variant_id TEXT NOT NULL,
            blinded_label TEXT,
            display_settings_json TEXT NOT NULL,
            PRIMARY KEY(presentation_id,presentation_ordinal),
            UNIQUE(presentation_id,preview_artifact_id)
        );
        CREATE TABLE IF NOT EXISTS evaluator_attempts (
            evaluator_attempt_id TEXT PRIMARY KEY
                CHECK(substr(evaluator_attempt_id,1,10)='evaluator_'),
            presentation_id TEXT NOT NULL REFERENCES comparison_presentations(presentation_id),
            attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>=0),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            evaluator_settings_json TEXT NOT NULL,
            prompt_template_revision TEXT NOT NULL,
            request_id TEXT,
            response_id TEXT,
            status TEXT NOT NULL CHECK(status IN
                ('succeeded','failed','timed_out','comparison_not_performed')),
            parsed_result_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(presentation_id,attempt_ordinal)
        );
        CREATE TABLE IF NOT EXISTS human_review_events (
            human_review_event_id TEXT PRIMARY KEY
                CHECK(substr(human_review_event_id,1,12)='humanreview_'),
            presentation_id TEXT NOT NULL REFERENCES comparison_presentations(presentation_id),
            event_sequence INTEGER NOT NULL CHECK(event_sequence>=0),
            event_type TEXT NOT NULL CHECK(event_type IN
                ('randomized','decision','disagreement','reveal','adjudication',
                 'timed_out','aborted','retry')),
            randomized_label TEXT,
            selected_variant_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(presentation_id,event_sequence)
        );
        CREATE TABLE IF NOT EXISTS experiment_candidate_eligibility (
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            declared_variant_id TEXT NOT NULL,
            eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
            reasons_json TEXT NOT NULL,
            policy_revision TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(experiment_run_id,declared_variant_id)
        );
        CREATE TABLE IF NOT EXISTS experiment_selection_facts (
            selection_fact_id TEXT PRIMARY KEY
                CHECK(substr(selection_fact_id,1,10)='selection_'),
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            fact_type TEXT NOT NULL CHECK(fact_type IN
                ('evaluator_preference','human_preference','comparative_outcome',
                 'operational_selection','fallback')),
            selected_variant_id TEXT,
            policy_id TEXT,
            policy_revision TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(experiment_run_id,fact_type)
        );
        CREATE TABLE IF NOT EXISTS experiment_run_outcomes (
            experiment_run_id TEXT PRIMARY KEY REFERENCES experiment_runs(experiment_run_id),
            outcome_kind TEXT NOT NULL,
            operational_selection_artifact_id TEXT REFERENCES experiment_run_artifacts(run_artifact_id),
            comparative_evidence INTEGER NOT NULL CHECK(comparative_evidence IN (0,1)),
            denominator_eligible INTEGER NOT NULL CHECK(denominator_eligible IN (0,1)),
            claim_scope TEXT NOT NULL CHECK(claim_scope IN ('comparative','operational_only','none')),
            outcome_json TEXT NOT NULL,
            outcome_contract_version TEXT NOT NULL,
            derived_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS evidence_compatibility_descriptors (
            descriptor_id TEXT PRIMARY KEY CHECK(substr(descriptor_id,1,11)='descriptor_'),
            experiment_run_id TEXT NOT NULL UNIQUE REFERENCES experiment_runs(experiment_run_id),
            descriptor_json TEXT NOT NULL,
            descriptor_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS compatibility_profiles (
            profile_id TEXT PRIMARY KEY CHECK(substr(profile_id,1,8)='profile_'),
            claim_kind TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            UNIQUE(claim_kind,profile_version)
        );
        CREATE TABLE IF NOT EXISTS evidence_epoch_members (
            profile_id TEXT NOT NULL REFERENCES compatibility_profiles(profile_id),
            descriptor_id TEXT NOT NULL REFERENCES evidence_compatibility_descriptors(descriptor_id),
            evidence_epoch TEXT NOT NULL,
            split_causes_json TEXT NOT NULL,
            PRIMARY KEY(profile_id,descriptor_id)
        );
        CREATE TABLE IF NOT EXISTS artifact_retention (
            run_artifact_id TEXT PRIMARY KEY
                REFERENCES experiment_run_artifacts(run_artifact_id),
            retention_class TEXT NOT NULL CHECK(retention_class IN
                ('ordinary_candidate','claim_dependent')),
            cleanup_eligible_after TEXT,
            retention_contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS artifact_deletion_events (
            deletion_event_id TEXT PRIMARY KEY
                CHECK(substr(deletion_event_id,1,9)='deletion_'),
            run_artifact_id TEXT NOT NULL
                REFERENCES experiment_run_artifacts(run_artifact_id),
            experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
            content_digest TEXT NOT NULL,
            authorized_by TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','confirmed','failed')),
            provider_receipt_json TEXT,
            provider_failure_state TEXT,
            authorized_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            UNIQUE(run_artifact_id)
        );
    """)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def hash_artifact(path: str | Path) -> str:
    """Hash the complete parent bytes before registration or execution."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def source_ref(root: Path | None = None) -> str:
    """Resolve the checked-out code identity before opening a DB transaction."""
    repo = root or Path(__file__).resolve().parents[1]
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def register_experiment_run(
    *,
    input_path: str | Path,
    process_family: str,
    candidates: list[dict],
    ontology_revision: str,
    operation_key: str,
    question: str,
    intent: str = "candidate_comparison",
    pipeline_run_id: str | None = None,
    manifest_ref: str | None = None,
    state_manifest_ref: str | None = None,
    state_discriminator: str = "fits:as-stored",
    code_ref: str | None = None,
) -> str:
    """Atomically register parent, run envelope, and complete declared roster.

    Hashing, source inspection, and candidate execution all happen outside the
    short transaction. An exact operation replay returns the prior run; a
    contradictory replay fails without mutation.
    """
    if not operation_key:
        raise ValueError("registration operation key is required")
    if not candidates:
        raise ValueError("candidate roster must not be empty")
    parent_path = Path(input_path)
    content_digest = hash_artifact(parent_path)
    if not _DIGEST.fullmatch(content_digest):
        raise ValueError("parent artifact requires a complete SHA-256 digest")
    resolved_code_ref = code_ref if code_ref is not None else source_ref()

    declared = []
    seen_variant_ids = set()
    for ordinal, candidate in enumerate(candidates):
        variant_id = candidate.get("id")
        if not isinstance(variant_id, str) or not variant_id:
            raise ValueError("every declared candidate requires a non-empty id")
        if variant_id in seen_variant_ids:
            raise ValueError(f"duplicate declared candidate id: {variant_id}")
        seen_variant_ids.add(variant_id)
        role = candidate.get("control_role", "none")
        if role not in CONTROL_ROLES:
            raise ValueError(f"unsupported control role: {role}")
        definition = dict(candidate)
        definition.pop("control_role", None)
        definition_json = _canonical(definition)
        definition_digest = _sha256_bytes(_canonical({
            "process_family": process_family,
            "definition_revision": ontology_revision,
            "definition": definition,
        }).encode())
        declared.append({
            "ordinal": ordinal,
            "variant_id": variant_id,
            "control_role": role,
            "definition": definition,
            "definition_json": definition_json,
            "definition_digest": definition_digest,
        })

    registration_intent = {
        "parent": {"content_digest": content_digest,
                   "state_discriminator": state_discriminator,
                   "state_manifest_ref": state_manifest_ref},
        "process_family": process_family,
        "question": question,
        "intent": intent,
        "pipeline_run_id": pipeline_run_id,
        "manifest_ref": manifest_ref,
        "contract_version": CONTRACT_VERSION,
        "code_ref": resolved_code_ref,
        "ontology_revision": ontology_revision,
        "roster": [{key: item[key] for key in
                    ("ordinal", "definition_digest", "control_role")}
                   for item in declared],
    }
    fingerprint = _sha256_bytes(_canonical(registration_intent).encode())

    # Imported only after all filesystem/external work is complete, and before
    # one short DB-only transaction.
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT experiment_run_id, registration_fingerprint "
            "FROM experiment_runs WHERE registration_operation_key=?",
            (operation_key,),
        ).fetchone()
        if existing:
            if existing["registration_fingerprint"] != fingerprint:
                raise RegistrationConflict(
                    "registration operation key conflicts with existing declared intent")
            return str(existing["experiment_run_id"])

        artifact = conn.execute(
            "SELECT artifact_revision_id FROM artifact_revisions "
            "WHERE content_digest=? AND state_discriminator=?",
            (content_digest, state_discriminator),
        ).fetchone()
        if artifact:
            artifact_id = str(artifact["artifact_revision_id"])
        else:
            artifact_id = _new_id("artifact_")
            conn.execute(
                """INSERT INTO artifact_revisions
                   (artifact_revision_id,content_digest,digest_status,
                    state_discriminator,locator,state_manifest_ref,
                    evidence_contract_version)
                   VALUES (?,?,?,?,?,?,?)""",
                (artifact_id, content_digest, "complete", state_discriminator,
                 str(parent_path), state_manifest_ref, CONTRACT_VERSION),
            )

        definition_ids = []
        for item in declared:
            row = conn.execute(
                "SELECT candidate_definition_id FROM candidate_definitions "
                "WHERE definition_digest=?", (item["definition_digest"],),
            ).fetchone()
            if row:
                definition_id = str(row["candidate_definition_id"])
            else:
                definition_id = _new_id("canddef_")
                conn.execute(
                    """INSERT INTO candidate_definitions
                       (candidate_definition_id,definition_digest,process_family,
                        declared_variant_id,definition_revision,definition_json,
                        evidence_contract_version)
                       VALUES (?,?,?,?,?,?,?)""",
                    (definition_id, item["definition_digest"], process_family,
                     item["variant_id"], ontology_revision, item["definition_json"],
                     CONTRACT_VERSION),
                )
            definition_ids.append(definition_id)

        run_id = _new_id("exprun_")
        conn.execute(
            """INSERT INTO experiment_runs
               (experiment_run_id,registration_operation_key,
                registration_fingerprint,parent_artifact_revision_id,
                pipeline_run_id,manifest_ref,process_family,question,intent,
                evidence_contract_version,code_ref,ontology_revision)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, operation_key, fingerprint, artifact_id, pipeline_run_id,
             manifest_ref, process_family, question, intent, CONTRACT_VERSION,
             resolved_code_ref, ontology_revision),
        )
        conn.executemany(
            """INSERT INTO experiment_run_roster
               (experiment_run_id,candidate_definition_id,declared_ordinal,
                control_role,control_role_version) VALUES (?,?,?,?,?)""",
            [(run_id, definition_ids[index], item["ordinal"],
              item["control_role"], CONTROL_ROLE_VERSION)
             for index, item in enumerate(declared)],
        )
        return run_id


_STATUS_COLUMNS = frozenset({
    "execution_status", "artifact_status", "assessment_status",
    "comparison_status", "persistence_status", "error_kind", "error_message",
})
_LIFECYCLE_COLUMNS = _STATUS_COLUMNS - {"error_kind", "error_message"}


def _roster_definition(conn, run_id: str, declared_variant_id: str):
    return conn.execute(
        """SELECT r.candidate_definition_id
           FROM experiment_run_roster r JOIN candidate_definitions d
             ON d.candidate_definition_id=r.candidate_definition_id
           WHERE r.experiment_run_id=? AND d.declared_variant_id=?""",
        (run_id, declared_variant_id),
    ).fetchone()


def start_candidate_attempt(
    *, experiment_run_id: str, declared_variant_id: str, operation_key: str,
    retry_of_attempt_id: str | None = None,
) -> str:
    """Commit an incomplete attempt before external candidate dispatch."""
    if not operation_key:
        raise ValueError("attempt operation key is required")
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        definition = _roster_definition(conn, experiment_run_id, declared_variant_id)
        if not definition:
            raise ValueError("candidate is not in the registered run roster")
        definition_id = str(definition["candidate_definition_id"])
        existing = conn.execute(
            """SELECT candidate_attempt_id,experiment_run_id,candidate_definition_id,
                      retry_of_attempt_id FROM candidate_attempts WHERE operation_key=?""",
            (operation_key,),
        ).fetchone()
        expected = (experiment_run_id, definition_id, retry_of_attempt_id)
        if existing:
            actual = (existing["experiment_run_id"], existing["candidate_definition_id"],
                      existing["retry_of_attempt_id"])
            if actual != expected:
                raise RegistrationConflict("attempt operation key conflicts with prior attempt")
            return str(existing["candidate_attempt_id"])
        prior = conn.execute(
            """SELECT candidate_attempt_id,attempt_ordinal FROM candidate_attempts
               WHERE experiment_run_id=? AND candidate_definition_id=?
               ORDER BY attempt_ordinal DESC LIMIT 1""",
            (experiment_run_id, definition_id),
        ).fetchone()
        ordinal = 0 if prior is None else int(prior["attempt_ordinal"]) + 1
        if ordinal == 0 and retry_of_attempt_id is not None:
            raise RegistrationConflict("initial attempt cannot declare retry lineage")
        if ordinal > 0 and (retry_of_attempt_id is None
                            or retry_of_attempt_id != prior["candidate_attempt_id"]):
            raise RegistrationConflict("retry must link to the latest prior attempt")
        attempt_id = _new_id("attempt_")
        conn.execute(
            """INSERT INTO candidate_attempts
               (candidate_attempt_id,experiment_run_id,candidate_definition_id,
                attempt_ordinal,retry_of_attempt_id,operation_key,execution_status,
                artifact_status,assessment_status,comparison_status,persistence_status,
                attempt_contract_version)
               VALUES (?,?,?,?,?,?,'dispatching','not_reached','not_reached',
                       'not_reached','not_reached',?)""",
            (attempt_id, experiment_run_id, definition_id, ordinal,
             retry_of_attempt_id, operation_key, ATTEMPT_CONTRACT_VERSION),
        )
        return attempt_id


def record_not_attempted(
    *, experiment_run_id: str, declared_variant_id: str, operation_key: str,
) -> str:
    """Record a candidate known to have been skipped; never use for an unknown crash."""
    attempt_id = start_candidate_attempt(
        experiment_run_id=experiment_run_id,
        declared_variant_id=declared_variant_id,
        operation_key=operation_key,
    )
    return record_attempt_transition(
        attempt_id=attempt_id,
        operation_key=f"{operation_key}:known-skip",
        updates={
            "execution_status": "not_attempted",
            "artifact_status": "not_applicable",
            "assessment_status": "not_applicable",
            "comparison_status": "not_applicable",
            "persistence_status": "not_applicable",
        },
    )


def record_attempt_transition(
    *, attempt_id: str, operation_key: str, updates: dict,
    effective_treatment: dict | None = None,
) -> str:
    """Idempotently append known observations; contradictory replay fails closed."""
    if not operation_key or not updates or set(updates) - _STATUS_COLUMNS:
        raise ValueError("transition requires a key and supported observations")
    canonical_treatment = None
    treatment_fingerprint = None
    if effective_treatment is not None:
        canonical_treatment = _canonical(effective_treatment)
        treatment_fingerprint = _sha256_bytes(canonical_treatment.encode())
    transition = _canonical({"updates": updates,
                             "treatment_fingerprint": treatment_fingerprint})
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM candidate_attempts WHERE candidate_attempt_id=?",
                           (attempt_id,)).fetchone()
        if not row:
            raise ValueError("unknown candidate attempt")
        log = json.loads(row["transition_log_json"])
        if operation_key in log:
            if log[operation_key] != transition:
                raise RegistrationConflict("transition replay contradicts prior observations")
            return attempt_id
        retry = conn.execute(
            "SELECT 1 FROM candidate_attempts WHERE retry_of_attempt_id=? LIMIT 1",
            (attempt_id,),
        ).fetchone()
        if retry:
            raise RegistrationConflict("a prior attempt is immutable after retry append")
        for name in set(updates) & _LIFECYCLE_COLUMNS:
            current = row[name]
            requested = updates[name]
            if current == requested:
                continue
            if current not in ("unknown", "not_reached", "dispatching"):
                raise RegistrationConflict(
                    f"non-monotonic {name} transition: {current} -> {requested}")

        treatment_id = row["effective_treatment_id"]
        if canonical_treatment is not None:
            existing = conn.execute(
                "SELECT effective_treatment_id FROM effective_treatments "
                "WHERE material_fingerprint=?", (treatment_fingerprint,),
            ).fetchone()
            if existing:
                treatment_id = existing["effective_treatment_id"]
            else:
                treatment_id = _new_id("treatment_")
                conn.execute(
                    """INSERT INTO effective_treatments
                       (effective_treatment_id,material_fingerprint,treatment_revision,
                        resolution_status,no_op_semantics,treatment_json)
                       VALUES (?,?,?,?,?,?)""",
                    (treatment_id, treatment_fingerprint, TREATMENT_REVISION,
                     effective_treatment["resolution_status"],
                     effective_treatment["no_op_semantics"], canonical_treatment),
                )
            if (row["effective_treatment_id"] is not None
                    and row["effective_treatment_id"] != treatment_id):
                raise RegistrationConflict("effective treatment is immutable once observed")

        assignments = list(updates)
        values = [updates[name] for name in assignments]
        log[operation_key] = transition
        assignments.extend(["transition_log_json", "effective_treatment_id",
                            "updated_at"])
        values.extend([_canonical(log), treatment_id, None])
        sql_parts = [f"{name}=?" for name in assignments[:-1]]
        sql_parts.append("updated_at=datetime('now')")
        conn.execute(
            f"UPDATE candidate_attempts SET {','.join(sql_parts)} "
            "WHERE candidate_attempt_id=?",
            (*values[:-1], attempt_id),
        )
        return attempt_id

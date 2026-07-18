import re
import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path
from nas_server.config import settings

_STAR_NAME_RE = re.compile(
    r'^(Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|Lambda|Mu|Nu|Xi|Pi|Rho|Sigma|Tau|Upsilon|Phi|Chi|Psi|Omega)\s+\w',
    re.IGNORECASE,
)
_PROPER_STARS = frozenset({
    "deneb", "vega", "altair", "sirius", "rigel", "betelgeuse", "arcturus",
    "polaris", "dubhe", "spica", "antares", "aldebaran", "capella", "procyon",
    "castor", "pollux", "regulus", "albireo", "mizar", "alcor",
})


def _is_named_star(name: str) -> bool:
    return bool(_STAR_NAME_RE.match(name)) or name.lower() in _PROPER_STARS

DB_PATH = settings["db_path"]
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS stacked_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT,
            file_name TEXT,
            file_path TEXT UNIQUE,
            exposure_time REAL,
            date TEXT,
            number_of_subs INTEGER,
            latitude REAL,
            longitude REAL,
            ra REAL,
            dec REAL,
            filter TEXT
        );

        CREATE TABLE IF NOT EXISTS light_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT,
            date TEXT,
            exposure_time REAL,
            file_name TEXT,
            file_path TEXT UNIQUE,
            ra REAL,
            dec REAL,
            filter TEXT,
            exclude INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT UNIQUE,
            ra REAL,
            dec REAL,
            type TEXT,
            magnitude REAL,
            association TEXT,
            mosaic INTEGER DEFAULT 0,
            mosaic_association TEXT,
            standalone INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 0,
            weight REAL DEFAULT 1.0,
            inactive INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT UNIQUE,
            stage TEXT DEFAULT 'captured',
            updated_at TEXT DEFAULT (datetime('now')),
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            is_dynamic INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS list_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER,
            target_id INTEGER,
            FOREIGN KEY(list_id) REFERENCES lists(id),
            FOREIGN KEY(target_id) REFERENCES targets(id)
        );

        INSERT OR IGNORE INTO lists (name, is_dynamic) VALUES
            ('Captured', 1),
            ('Processed', 1),
            ('Messier', 0),
            ('Caldwell', 0);

        CREATE TABLE IF NOT EXISTS processed_files (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            target            TEXT NOT NULL,
            file_path         TEXT NOT NULL UNIQUE,
            filename          TEXT NOT NULL,
            tool              TEXT,
            step              TEXT,
            total_integration REAL,
            frame_count       INTEGER,
            sensor_temp       REAL,
            obs_date          TEXT,
            flags             TEXT DEFAULT '{}',
            notes             TEXT,
            is_auto           INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_processed_target ON processed_files(target);

        CREATE TABLE IF NOT EXISTS claude_assessments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            target       TEXT NOT NULL,
            processed_id INTEGER REFERENCES processed_files(id),
            phase        TEXT NOT NULL,
            model        TEXT NOT NULL,
            scores       TEXT DEFAULT '{}',
            recommendation TEXT,
            raw_response TEXT,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ca_target ON claude_assessments(target);

        CREATE TABLE IF NOT EXISTS processing_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            target       TEXT NOT NULL,
            processed_id INTEGER REFERENCES processed_files(id),
            step         TEXT NOT NULL,
            engine       TEXT,
            params       TEXT,
            scores_before TEXT,
            scores_after  TEXT,
            claude_reasoning TEXT,
            elapsed_s    REAL,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ph_target ON processing_history(target);

        CREATE TABLE IF NOT EXISTS experiment_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target          TEXT NOT NULL,
            object_type     TEXT,
            step            TEXT NOT NULL,
            variant_id      TEXT NOT NULL,
            params          TEXT,
            scores          TEXT,
            overall_score   REAL,
            winner          INTEGER DEFAULT 0,
            claude_reasoning TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_er_step ON experiment_results(step, variant_id);
        CREATE INDEX IF NOT EXISTS idx_er_target ON experiment_results(target);

        CREATE TABLE IF NOT EXISTS processing_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            target       TEXT NOT NULL,
            workflow     TEXT,
            started_at   TEXT,
            finished_at  TEXT DEFAULT (datetime('now')),
            elapsed_s    REAL,
            steps_json   TEXT DEFAULT '[]',
            initial_scores TEXT DEFAULT '{}',
            final_scores   TEXT DEFAULT '{}',
            critical_eval  TEXT,
            output_path  TEXT,
            dry_run      INTEGER DEFAULT 0,
            api_diagnostics TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pr_target ON processing_runs(target);

        CREATE TABLE IF NOT EXISTS queue_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type        TEXT NOT NULL DEFAULT 'process',
            target          TEXT NOT NULL,
            workflow        TEXT,
            experiment_mode INTEGER DEFAULT 0,
            dry_run         INTEGER DEFAULT 0,
            source_file     TEXT,
            engine          TEXT,
            cull            INTEGER DEFAULT 1,
            bottom_pct      REAL DEFAULT 0.10,
            min_stars       INTEGER DEFAULT 20,
            fast            INTEGER DEFAULT 0,
            framing         TEXT DEFAULT 'min',
            hero            INTEGER DEFAULT 0,
            drizzle         INTEGER DEFAULT 0,
            exptime         INTEGER,
            ecc_threshold   REAL DEFAULT 0.66,
            manual_review   INTEGER DEFAULT 0,
            extra_params    TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS stacking_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            target      TEXT NOT NULL,
            engine      TEXT NOT NULL,
            started_at  TEXT,
            finished_at TEXT DEFAULT (datetime('now')),
            frame_count INTEGER,
            elapsed_s   REAL,
            success     INTEGER DEFAULT 0,
            error       TEXT,
            log_tail    TEXT,
            output_path TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sr_target ON stacking_runs(target);

        CREATE TABLE IF NOT EXISTS manual_reviews (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            target                TEXT NOT NULL,
            step                  TEXT NOT NULL,
            run_id                TEXT,
            input_fits_path       TEXT,
            ordered_labels        TEXT NOT NULL,
            variants_json         TEXT NOT NULL,
            claude_winner_label   TEXT,
            claude_reasoning      TEXT,
            user_winner_label     TEXT,
            user_reasoning        TEXT,
            agreed                INTEGER,
            final_winner_variant  TEXT,
            manual_edit_path      TEXT,
            status                TEXT DEFAULT 'pending',
            expires_at            TEXT NOT NULL,
            created_at            TEXT DEFAULT (datetime('now')),
            resolved_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mr_status ON manual_reviews(status);
        CREATE INDEX IF NOT EXISTS idx_mr_target ON manual_reviews(target);

        CREATE TABLE IF NOT EXISTS manual_processing_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            target        TEXT NOT NULL,
            file_path     TEXT NOT NULL UNIQUE,
            filename      TEXT NOT NULL,
            source_type   TEXT,
            n_steps       INTEGER DEFAULT 0,
            flow_json     TEXT DEFAULT '[]',
            summary       TEXT,
            preview_jpg   TEXT,
            claude_score  REAL,
            claude_json   TEXT,
            graded_at     TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mpr_target ON manual_processing_runs(target);

        CREATE TABLE IF NOT EXISTS manual_folder_reviews (
            target       TEXT PRIMARY KEY,
            status       TEXT DEFAULT 'reviewed',
            reviewed_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS agent_suggestions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT DEFAULT (datetime('now')),
            description TEXT NOT NULL,
            file_hint   TEXT,
            code_snippet TEXT,
            resolved    INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS image_crops (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT DEFAULT (datetime('now')),
            target      TEXT NOT NULL,
            filename    TEXT NOT NULL,
            name        TEXT NOT NULL,
            x           REAL NOT NULL,
            y           REAL NOT NULL,
            w           REAL NOT NULL,
            h           REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ic_target ON image_crops(target);
        CREATE TABLE IF NOT EXISTS rag_embeddings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type    TEXT NOT NULL,
            source_id   INTEGER NOT NULL,
            target      TEXT,
            text_snippet TEXT,
            embedding   BLOB NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_source ON rag_embeddings(doc_type, source_id);
        CREATE TABLE IF NOT EXISTS image_crop_analyses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            crop_id         INTEGER NOT NULL,
            created_at      TEXT DEFAULT (datetime('now')),
            scores_json     TEXT,
            aggregate_score REAL,
            summary         TEXT,
            concerns_json   TEXT,
            physics_json    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ica_crop ON image_crop_analyses(crop_id);
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT DEFAULT (datetime('now')),
            last_active TEXT DEFAULT (datetime('now')),
            title       TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            role       TEXT NOT NULL,
            content    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cm_session ON chat_messages(session_id, id);

        CREATE TABLE IF NOT EXISTS calibration_frames (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_type    TEXT NOT NULL,
            camera        TEXT DEFAULT 'seestar_s50',
            filter        TEXT DEFAULT 'none',
            gain          INTEGER,
            offset        INTEGER,
            temp_c        REAL,
            exposure_time REAL,
            date          TEXT,
            file_path     TEXT UNIQUE,
            is_master     INTEGER DEFAULT 0,
            master_of     INTEGER,
            adu_median    REAL,
            valid         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cal_type ON calibration_frames(frame_type, is_master, valid);

        CREATE TABLE IF NOT EXISTS adaptive_decisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           TEXT NOT NULL,
            target_name      TEXT NOT NULL,
            object_type      TEXT,
            phase            TEXT NOT NULL,          -- 'linear' | 'nonlinear'
            step_name        TEXT,                   -- NULL = whole-phase plan
            decision_type    TEXT NOT NULL,          -- 'variant_fill' | 'param_nudge' | 'add_step' | 'skip_step' | 'flag' | 'add_step_reverted'
            chosen_value     TEXT,                   -- JSON string
            physics_suggestion TEXT,                 -- JSON (what tool_params recommended)
            rationale        TEXT,
            final_score      REAL,                   -- filled after run completes
            score_delta      REAL,                   -- final_score minus initial_score
            timestamp        TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ad_target ON adaptive_decisions(target_name);
        CREATE INDEX IF NOT EXISTS idx_ad_run ON adaptive_decisions(run_id);

        CREATE TABLE IF NOT EXISTS target_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            target_name TEXT NOT NULL,
            run_id      TEXT,            -- optional: links to a specific processing run
            comment     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tc_target ON target_comments(target_name);

        CREATE TABLE IF NOT EXISTS remote_workers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT UNIQUE NOT NULL,
            url              TEXT NOT NULL,
            enabled          INTEGER DEFAULT 1,
            last_seen        TEXT,
            current_job_id   TEXT,
            registered_at    TEXT DEFAULT (datetime('now'))
        );

        -- User-chosen per-target crop, reused on every subsequent process.
        -- Primary framing is a WCS sky-box (center RA/Dec + size + position
        -- angle); fractional bounds + rotation are the fallback when the new
        -- stack has no usable WCS. See nas_server/target_crop.py.
        CREATE TABLE IF NOT EXISTS target_crops (
            target        TEXT PRIMARY KEY,
            center_ra     REAL,           -- sky-box center, degrees
            center_dec    REAL,
            width_arcmin  REAL,
            height_arcmin REAL,
            pa_deg        REAL,           -- position angle of +x axis, degrees
            scale_arcsec  REAL,           -- pixel scale the box was derived at
            frac_top      REAL,           -- fractional-bounds fallback
            frac_bottom   REAL,
            frac_left     REAL,
            frac_right    REAL,
            rotate_deg    REAL,           -- manual rotation fallback
            source        TEXT,           -- candidate name or 'manual'
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        );
        """)

        # Idempotent schema migrations
        for col_def in [
            "ALTER TABLE light_files ADD COLUMN scored_at TEXT",
            "ALTER TABLE light_files ADD COLUMN fwhm REAL",
            "ALTER TABLE light_files ADD COLUMN eccentricity REAL",
            "ALTER TABLE light_files ADD COLUMN snr REAL",
            "ALTER TABLE light_files ADD COLUMN star_count INTEGER",
            "ALTER TABLE light_files ADD COLUMN sky_level REAL",
            "ALTER TABLE light_files ADD COLUMN gradient_severity REAL",
            "ALTER TABLE stacking_runs ADD COLUMN sigma_sky REAL",
            "ALTER TABLE stacking_runs ADD COLUMN snr_stack REAL",
            "ALTER TABLE stacking_runs ADD COLUMN fwhm_stack REAL",
            "ALTER TABLE stacking_runs ADD COLUMN ecc_stack REAL",
            "ALTER TABLE stacking_runs ADD COLUMN flatness_rms REAL",
            "ALTER TABLE stacking_runs ADD COLUMN clipping_frac REAL",
            "ALTER TABLE stacking_runs ADD COLUMN star_count_stack INTEGER",
            "ALTER TABLE stacking_runs ADD COLUMN efficiency REAL",
            "ALTER TABLE stacking_runs ADD COLUMN hero INTEGER DEFAULT 0",
            "ALTER TABLE stacking_runs ADD COLUMN drizzle INTEGER DEFAULT 0",
            "ALTER TABLE stacking_runs ADD COLUMN bottom_pct REAL",
            "ALTER TABLE stacking_runs ADD COLUMN ecc_threshold REAL",
            "ALTER TABLE stacking_runs ADD COLUMN exptime INTEGER",
            "ALTER TABLE stacking_runs ADD COLUMN framing TEXT",
            "ALTER TABLE experiment_results ADD COLUMN metrics_json TEXT",
            "ALTER TABLE experiment_results ADD COLUMN experiment_run_id TEXT",
            "ALTER TABLE experiment_results ADD COLUMN all_scores_json TEXT",
            "ALTER TABLE experiment_results ADD COLUMN runner_up_score REAL",
            "ALTER TABLE experiment_results ADD COLUMN winning_margin REAL",
            "ALTER TABLE queue_jobs ADD COLUMN manual_review INTEGER DEFAULT 0",
            "ALTER TABLE queue_jobs ADD COLUMN sky_level_factor REAL DEFAULT 3.0",
            "ALTER TABLE queue_jobs ADD COLUMN gradient_threshold REAL DEFAULT 0.5",
            # JSON blob for extra_params (force_steps / force_variants / branch). Without
            # this, a queued force-run (NBN, nb_palette, branch) silently reverts to a
            # plain run when the queue reloads after a service restart. (2026-07-08)
            "ALTER TABLE queue_jobs ADD COLUMN extra_params TEXT",
            "ALTER TABLE agent_suggestions ADD COLUMN source TEXT DEFAULT 'agent'",
            "ALTER TABLE agent_suggestions ADD COLUMN dedup_key TEXT",
            "ALTER TABLE targets ADD COLUMN transient INTEGER DEFAULT 0",
            "ALTER TABLE light_files ADD COLUMN source TEXT DEFAULT 'seestar_app'",
            # EQMODE: 1=equatorial mode, 0=alt-az, NULL=unknown (old firmware or not yet read)
            "ALTER TABLE light_files ADD COLUMN eq_mode INTEGER",
            # Plate-solve results per sub (true sky position, independent of header).
            # solve_status: 'ok' | 'failed' | 'outlier' (solved but far from siblings) | NULL=not solved.
            "ALTER TABLE light_files ADD COLUMN solved_ra REAL",
            "ALTER TABLE light_files ADD COLUMN solved_dec REAL",
            "ALTER TABLE light_files ADD COLUMN solved_rot REAL",
            "ALTER TABLE light_files ADD COLUMN solved_scale REAL",
            "ALTER TABLE light_files ADD COLUMN solve_status TEXT",
            "ALTER TABLE light_files ADD COLUMN solved_at TEXT",
            "ALTER TABLE stacking_runs ADD COLUMN eq_only INTEGER DEFAULT 0",
            "ALTER TABLE processing_runs ADD COLUMN api_diagnostics TEXT",
        ]:
            try:
                conn.execute(col_def)
            except Exception:
                pass  # column already exists

        # Partial unique index for dedup_key (only among unresolved rows)
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestions_dedup "
                "ON agent_suggestions(dedup_key) WHERE resolved=0 AND dedup_key IS NOT NULL"
            )
        except Exception:
            pass

    print(f"[database] Initialized at {DB_PATH}")


# --- Adaptive planning decisions ---

def log_adaptive_decisions(decisions: list[dict]) -> None:
    """Bulk-insert adaptive planning decisions. Each dict may have:
    run_id, target_name, object_type, phase, step_name, decision_type,
    chosen_value, physics_suggestion, rationale.
    """
    if not decisions:
        return
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO adaptive_decisions
                (run_id, target_name, object_type, phase, step_name,
                 decision_type, chosen_value, physics_suggestion, rationale)
            VALUES
                (:run_id, :target_name, :object_type, :phase, :step_name,
                 :decision_type, :chosen_value, :physics_suggestion, :rationale)
        """, [
            {
                "run_id": d.get("run_id", ""),
                "target_name": d.get("target_name", ""),
                "object_type": d.get("object_type"),
                "phase": d.get("phase", ""),
                "step_name": d.get("step_name"),
                "decision_type": d.get("decision_type", ""),
                "chosen_value": d.get("chosen_value"),
                "physics_suggestion": d.get("physics_suggestion"),
                "rationale": d.get("rationale"),
            }
            for d in decisions
        ])


def get_adaptive_history(target_name: str, limit: int = 5,
                         object_type: str | None = None) -> list[dict]:
    """Return recent adaptive decisions for a target (and optionally its object type).

    If object_type is provided, also returns class-level failures from other targets
    of the same type so Claude can learn across targets.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT run_id, target_name, object_type, phase, step_name,
                   decision_type, chosen_value, rationale, final_score, score_delta, timestamp
            FROM adaptive_decisions
            WHERE target_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (target_name, limit * 6)).fetchall()  # fetch more, we'll group by run

        class_rows: list = []
        if object_type:
            class_rows = conn.execute("""
                SELECT run_id, target_name, object_type, phase, step_name,
                       decision_type, chosen_value, rationale, final_score, score_delta, timestamp
                FROM adaptive_decisions
                WHERE object_type = ?
                  AND target_name != ?
                  AND decision_type IN ('add_step_reverted', 'variant_fill', 'add_step')
                  AND final_score IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """, (object_type, target_name, 20)).fetchall()

    result = [dict(r) for r in rows]
    class_result = [dict(r) for r in class_rows]

    # Prepend user comments as high-priority context
    with get_conn() as conn:
        comment_rows = conn.execute("""
            SELECT id, run_id, comment, created_at
            FROM target_comments
            WHERE target_name = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (target_name,)).fetchall()
    comments = [
        {
            "decision_type": "user_comment",
            "target_name": target_name,
            "run_id": r["run_id"],
            "rationale": r["comment"],
            "timestamp": r["created_at"],
            "phase": "user",
            "step_name": None,
            "chosen_value": None,
            "final_score": None,
            "score_delta": None,
            "object_type": None,
        }
        for r in comment_rows
    ]
    return comments + result + class_result


def update_adaptive_outcomes(run_id: str, final_score: float,
                             initial_score: float) -> None:
    """Backfill final_score and score_delta for all decisions in a run."""
    delta = final_score - initial_score
    with get_conn() as conn:
        conn.execute("""
            UPDATE adaptive_decisions
            SET final_score = ?, score_delta = ?
            WHERE run_id = ? AND final_score IS NULL
        """, (final_score, delta, run_id))


# ---------------------------------------------------------------------------
# Remote worker tracking
# ---------------------------------------------------------------------------

def upsert_worker(name: str, url: str) -> None:
    """Register or update a remote worker record."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO remote_workers (name, url, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET url=excluded.url
        """, (name, url))


def set_worker_enabled(name: str, enabled: bool, url: str = "") -> None:
    """Enable/disable dispatch to a worker. Upserts so config-only workers can be toggled."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO remote_workers (name, url, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled
        """, (name, url, 1 if enabled else 0))


def get_worker_enabled(name: str) -> bool:
    """Return the DB dispatch-enabled flag for a worker (True if no row exists yet)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM remote_workers WHERE name = ?", (name,)
        ).fetchone()
    return True if row is None else bool(row["enabled"])


def update_worker_heartbeat(name: str) -> None:
    """Stamp a worker as seen now."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE remote_workers SET last_seen = datetime('now') WHERE name = ?
        """, (name,))


def set_worker_job(name: str, job_id: str | None) -> None:
    """Record which job a worker is currently running (None = idle)."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE remote_workers SET current_job_id = ?, last_seen = datetime('now')
            WHERE name = ?
        """, (job_id, name))


def get_online_workers(stale_s: int = 90) -> list[dict]:
    """Return enabled workers whose last_seen is within stale_s seconds."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT name, url, current_job_id, last_seen
            FROM remote_workers
            WHERE enabled = 1
              AND last_seen IS NOT NULL
              AND (julianday('now') - julianday(last_seen)) * 86400 < ?
        """, (stale_s,)).fetchall()
    return [dict(r) for r in rows]


def add_target_comment(target_name: str, comment: str,
                       run_id: str | None = None) -> dict:
    """Store a user comment on a target (optionally linked to a specific run)."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO target_comments (target_name, run_id, comment) VALUES (?,?,?)",
            (target_name, run_id, comment.strip()),
        )
        return {"id": cur.lastrowid, "target_name": target_name,
                "run_id": run_id, "comment": comment.strip()}


def get_target_comments(target_name: str, limit: int = 20) -> list[dict]:
    """Return recent comments for a target, newest first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, target_name, run_id, comment, created_at
            FROM target_comments
            WHERE target_name = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (target_name, limit)).fetchall()
    return [dict(r) for r in rows]


def delete_target_comment(comment_id: int) -> bool:
    """Delete a comment by id. Returns True if a row was deleted."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM target_comments WHERE id=?", (comment_id,))
    return cur.rowcount > 0


# --- Stacked files ---

def upsert_stacked_file(target, file_name, file_path, exposure_time, date,
                         number_of_subs, latitude, longitude, ra, dec, filter_type):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO stacked_files
                (target, file_name, file_path, exposure_time, date,
                 number_of_subs, latitude, longitude, ra, dec, filter)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                exposure_time=excluded.exposure_time,
                number_of_subs=excluded.number_of_subs,
                date=excluded.date
        """, (target, file_name, file_path, exposure_time, date,
              number_of_subs, latitude, longitude, ra, dec, filter_type))
        conn.execute("INSERT OR IGNORE INTO targets (target) VALUES (?)", (target,))
        conn.execute("""
            INSERT INTO pipeline (target, stage) VALUES (?, 'captured')
            ON CONFLICT(target) DO NOTHING
        """, (target,))


def upsert_light_file(target, date, exposure_time, file_name, file_path, ra, dec, filter_type):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO light_files
                (target, date, exposure_time, file_name, file_path, ra, dec, filter)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO NOTHING
        """, (target, date, exposure_time, file_name, file_path, ra, dec, filter_type))
        conn.execute("INSERT OR IGNORE INTO targets (target) VALUES (?)", (target,))


def get_scored_frame_count(target: str) -> tuple[int, int]:
    """Return (total_frames, individually_measured_frames) for a target.

    'scored' means fwhm IS NOT NULL — i.e. the frame was individually analyzed.
    scored_at alone is insufficient: mark_frames_scored() batch-stamps all frames
    at the end of a run, including any that were never actually measured.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(fwhm) FROM light_files WHERE target=?",
            (target,)
        ).fetchone()
        return (row[0], row[1]) if row else (0, 0)


def mark_frames_scored(target: str):
    """Set scored_at = now for all frames of this target that haven't been marked yet."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE light_files SET scored_at=datetime('now') WHERE target=? AND scored_at IS NULL",
            (target,)
        )


def get_radec_center(target: str) -> tuple[float, float] | None:
    """Return (ra_deg, dec_deg) centroid for a target from its frame headers, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT AVG(ra), AVG(dec) FROM light_files WHERE target=? AND ra IS NOT NULL",
            (target,)
        ).fetchone()
    if row and row[0] is not None:
        return (row[0], row[1])
    return None


def get_radec_associated_targets(target: str, fov_deg: float = 1.48) -> list[str]:
    """Return targets whose sky centroid is within fov_deg of target's centroid.

    Uses per-frame RA/Dec from light_files headers (populated from FITS WCS).
    The SeeStar S50 FOV diagonal is ~1.48 deg — targets within this radius
    likely have overlapping coverage and should share frames when stacking.
    """
    import math

    center = get_radec_center(target)
    if center is None:
        return []
    ra1 = math.radians(center[0])
    dec1 = math.radians(center[1])

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT target, AVG(ra) AS ra, AVG(dec) AS dec
               FROM light_files WHERE target != ? AND ra IS NOT NULL
               GROUP BY target""",
            (target,)
        ).fetchall()

    nearby = []
    for row in rows:
        ra2 = math.radians(row[1])
        dec2 = math.radians(row[2])
        cos_sep = (math.sin(dec1) * math.sin(dec2)
                   + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2))
        sep_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        if sep_deg < fov_deg:
            nearby.append(row[0])
    return nearby


def get_unscored_light_frames(limit: int = 20) -> list[dict]:
    """Return light frames with no quality scores yet (for idle enrichment)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, target, file_path FROM light_files "
            "WHERE scored_at IS NULL AND exclude=0 LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_unmeasured_light_frames(limit: int = 20) -> list[dict]:
    """Return frames that were scored before sky_level/gradient_severity were captured
    (scored_at set but sky_level NULL). The stack cull step re-measures these, so the
    idle worker backfills them to make those metrics cached and skip stack-time measuring."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, target, file_path FROM light_files "
            "WHERE scored_at IS NOT NULL AND sky_level IS NULL AND exclude=0 LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_light_frame_scores(file_path: str, fwhm: float, eccentricity: float, snr: float,
                              star_count: int | None = None,
                              sky_level: float | None = None,
                              gradient_severity: float | None = None):
    """Store image quality metrics on a light frame row."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE light_files SET fwhm=?, eccentricity=?, snr=?, star_count=?, "
            "sky_level=?, gradient_severity=?, scored_at=datetime('now') "
            "WHERE file_path=?",
            (fwhm, eccentricity, snr, star_count, sky_level, gradient_severity, file_path)
        )


def update_light_frame_solve(file_path: str, solved_ra: float | None,
                             solved_dec: float | None,
                             solved_rot: float | None = None,
                             solved_scale: float | None = None,
                             solve_status: str = "ok"):
    """Store the plate-solve result (true sky position) on a light frame row.

    solve_status: 'ok' | 'failed' | 'outlier'. A 'failed' solve stamps solved_at
    so the idle/eager solver does not keep retrying the same frame.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE light_files SET solved_ra=?, solved_dec=?, solved_rot=?, "
            "solved_scale=?, solve_status=?, solved_at=datetime('now') "
            "WHERE file_path=?",
            (solved_ra, solved_dec, solved_rot, solved_scale, solve_status, file_path)
        )


def get_unsolved_light_frames(target: str | None = None, limit: int = 50) -> list[dict]:
    """Return light frames with no plate-solve yet (solve_status IS NULL, not excluded).

    When target is given, restrict to that target (used by on-demand solve);
    otherwise return any unsolved frames (used by idle eager-solve).
    """
    with get_conn() as conn:
        if target:
            rows = conn.execute(
                "SELECT id, target, file_path FROM light_files "
                "WHERE target=? AND solve_status IS NULL AND exclude=0 "
                "ORDER BY file_path LIMIT ?",
                (target, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, target, file_path FROM light_files "
                "WHERE solve_status IS NULL AND exclude=0 LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def claim_solve_batch(limit: int = 40, lease_min: int = 30) -> tuple:
    """Atomically lease a batch of unsolved frames for a remote/idle solver.

    Returns (target, [file_paths]). Picks one seed target with unsolved frames and
    marks up to `limit` of its frames solve_status='solving' (a lease, timestamped via
    solved_at) so neither the VM idle worker nor another remote worker re-selects them
    (get_unsolved_light_frames filters solve_status IS NULL). Stale leases older than
    lease_min minutes are reclaimed first, so a crashed worker's frames aren't stuck.
    Per-target so flag_alignment_outliers judges a coherent sibling group. Returns
    (None, []) when nothing is unsolved."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE light_files SET solve_status=NULL "
            "WHERE solve_status='solving' "
            f"AND (solved_at IS NULL OR solved_at < datetime('now', '-{int(lease_min)} minutes'))"
        )
        seed = conn.execute(
            "SELECT target FROM light_files "
            "WHERE solve_status IS NULL AND exclude=0 LIMIT 1"
        ).fetchone()
        if not seed:
            return (None, [])
        target = seed[0]
        rows = conn.execute(
            "SELECT file_path FROM light_files "
            "WHERE target=? AND solve_status IS NULL AND exclude=0 "
            "ORDER BY file_path LIMIT ?",
            (target, limit)
        ).fetchall()
        paths = [r[0] for r in rows]
        if paths:
            ph = ",".join("?" * len(paths))
            conn.execute(
                f"UPDATE light_files SET solve_status='solving', solved_at=datetime('now') "
                f"WHERE file_path IN ({ph})",
                paths
            )
    return (target, paths)


def release_solve_claims(file_paths: list[str]) -> None:
    """Return leased ('solving') frames to unsolved (solve_status NULL) — used when a
    remote solve batch fails to run at all, so the lease isn't left dangling."""
    if not file_paths:
        return
    with get_conn() as conn:
        ph = ",".join("?" * len(file_paths))
        conn.execute(
            f"UPDATE light_files SET solve_status=NULL "
            f"WHERE file_path IN ({ph}) AND solve_status='solving'",
            list(file_paths)
        )


def get_solved_positions(target: str) -> list[dict]:
    """Return solved positions for a target's subs (for alignment QA / association).

    Includes associated targets, mirroring get_frames_for_stack so mosaic panels
    grouped under one canonical name are analysed together.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT association FROM targets WHERE target=?", (target,)
        ).fetchone()
        assoc_str = row[0] if row else None
        all_targets = [target]
        if assoc_str:
            all_targets += [t.strip() for t in assoc_str.split(",") if t.strip()]
        placeholders = ",".join("?" * len(all_targets))
        rows = conn.execute(
            f"SELECT file_path, target, ra, dec, solved_ra, solved_dec, "
            f"solved_rot, solved_scale, solve_status "
            f"FROM light_files WHERE target IN ({placeholders}) "
            f"AND solve_status IS NOT NULL",
            all_targets
        ).fetchall()
        return [dict(r) for r in rows]


def clear_exclude_flags(target: str | None = None):
    """Clear all auto-set exclude flags. Use before switching to threshold-based selection."""
    with get_conn() as conn:
        if target:
            conn.execute("UPDATE light_files SET exclude=0 WHERE target=?", (target,))
        else:
            conn.execute("UPDATE light_files SET exclude=0")


def get_frames_for_stack(target: str, bottom_pct: float = 0.10,
                         min_stars: int = 20,
                         ecc_threshold: float = 0.66,
                         sky_level_factor: float = 3.0,
                         gradient_threshold: float = 0.5) -> list[str]:
    """
    Return file paths to include in a stack, applying the cull threshold dynamically.

    Rules applied in order:
      1. Skip frames manually excluded by the user (exclude=1).
      2. Skip frames where star_count < min_stars (clouds/obstruction).
      3. Skip frames where eccentricity > ecc_threshold (tracking error).
      4. Skip frames where gradient_severity > gradient_threshold (partial cloud / gradient).
         Only applied when gradient_severity is measured. gradient_threshold=0 disables.
      5. Skip frames where sky_level > session_median_sky × sky_level_factor (cloud veil).
         Session-relative so moon/LP variation between nights doesn't cause false rejects.
         Only applied when sky_level is measured. sky_level_factor=0 disables.
      6. From the remainder that have measurements, drop the bottom bottom_pct
         by composite score (fwhm × (1 + ecc)).
      7. Unscored frames (scored_at IS NULL) are included — no basis to reject.
    """
    import numpy as np

    with get_conn() as conn:
        # Collect canonical target + any associated targets (confirmed via /associations page)
        row = conn.execute(
            "SELECT association FROM targets WHERE target=?", (target,)
        ).fetchone()
        assoc_str = row[0] if row else None
        all_targets = [target]
        if assoc_str:
            all_targets += [t.strip() for t in assoc_str.split(",") if t.strip()]

        placeholders = ",".join("?" * len(all_targets))
        rows = conn.execute(
            f"SELECT file_path, fwhm, eccentricity, snr, star_count, scored_at, "
            f"sky_level, gradient_severity "
            f"FROM light_files WHERE target IN ({placeholders}) AND exclude=0 ORDER BY file_path",
            all_targets
        ).fetchall()

    frames = [dict(r) for r in rows]
    if not frames:
        return []

    # Absolute quality gates
    passing = []
    for f in frames:
        stars = f.get("star_count")
        ecc   = f.get("eccentricity")
        grad  = f.get("gradient_severity")
        if stars is not None and stars < min_stars:
            continue
        if ecc is not None and ecc > ecc_threshold:
            continue
        if grad is not None and gradient_threshold > 0 and grad > gradient_threshold:
            continue
        passing.append(f)

    # Session-relative sky brightness gate — reject frames brighter than factor × median sky
    if sky_level_factor > 0:
        sky_levels = [f["sky_level"] for f in passing if f.get("sky_level") is not None]
        if sky_levels:
            median_sky = float(np.median(sky_levels))
            sky_cutoff = median_sky * sky_level_factor
            passing = [f for f in passing
                       if f.get("sky_level") is None or f["sky_level"] <= sky_cutoff]

    # Relative threshold: bottom bottom_pct by composite score
    scored = [f for f in passing if f.get("fwhm") is not None]
    if scored and bottom_pct > 0:
        n_reject = max(0, int(len(scored) * bottom_pct))
        sorted_scored = sorted(scored, key=lambda f: f["fwhm"] * (1.0 + (f.get("eccentricity") or 0)))
        reject_paths = {f["file_path"] for f in sorted_scored[:n_reject]}
        passing = [f for f in passing if f["file_path"] not in reject_paths]

    return [f["file_path"] for f in passing]


# --- Pipeline ---

PIPELINE_STAGES = ["captured", "stacked", "processing", "processed", "exported"]


def set_pipeline_stage(target: str, stage: str, notes: str = None):
    if stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown stage '{stage}'. Valid: {PIPELINE_STAGES}")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO pipeline (target, stage, notes, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(target) DO UPDATE SET
                stage=excluded.stage,
                notes=excluded.notes,
                updated_at=datetime('now')
        """, (target, stage, notes))


def get_pipeline() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.target, p.stage, p.updated_at, p.notes,
                   COUNT(DISTINCT s.id) AS stacked_count,
                   COUNT(DISTINCT l.id) AS light_count,
                   COALESCE(SUM(l.exposure_time), 0) AS total_exposure
            FROM pipeline p
            LEFT JOIN stacked_files s ON s.target = p.target
            LEFT JOIN light_files l ON l.target = p.target AND l.exclude = 0
            GROUP BY p.target
            ORDER BY p.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


# --- Targets ---

def get_targets() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*,
                   COUNT(DISTINCT s.id) AS stacked_count,
                   COUNT(DISTINCT l.id) AS light_count,
                   COALESCE(SUM(l.exposure_time), 0) AS total_exposure,
                   p.stage
            FROM targets t
            LEFT JOIN stacked_files s ON s.target = t.target
            LEFT JOIN light_files l ON l.target = t.target AND l.exclude = 0
            LEFT JOIN pipeline p ON p.target = t.target
            GROUP BY t.id
            ORDER BY t.target
        """).fetchall()
        return [dict(r) for r in rows]


# --- Processed files ---

# Steps written by the stacker for a RAW (un-processed) stack output.
_RAW_STACK_STEPS = {"stack"}
# Steps/filenames that mark a PROCESSED output (auto pipeline or manual export).
_PROCESSED_STEPS = {"final", "processed", "exported", "starless", "stars"}


def is_raw_stack(filename: str = "", step: str | None = None) -> bool:
    """True if a processed_files row is a raw stacker output (not yet processed).

    Raw stacks and processed finals share the `_processed/` folder and the
    `processed_files` table; the only reliable discriminator is the `step` the
    stacker/scanner assigned ("stack" for raw, "final"/etc. for outputs), with a
    filename fallback for legacy rows that predate step tagging.
    """
    fn = (filename or "").strip().lower()
    stem = fn.rsplit(".", 1)[0]
    # Coverage maps are sidecars written next to the stack (canonical-framing). Their
    # name still contains "_..._stack", so veto them up front — even if a row was
    # mis-tagged with a raw-stack step — so they're never selected as a source FITS.
    if stem.endswith("_coverage"):
        return False
    s = (step or "").strip().lower()
    if s in _RAW_STACK_STEPS:
        return True
    if s in _PROCESSED_STEPS or s.startswith("pixinsight") or s.startswith("auto_process"):
        return False
    if fn.startswith("auto_final") or fn.startswith("final"):
        return False
    # Standardized stacker name ends in `_<tool>_stack.<ext>` (make_processed_filename).
    return stem.endswith("_stack")


def is_processed_output(filename: str = "", step: str | None = None) -> bool:
    """Inverse of is_raw_stack for processed_files rows (previews excluded upstream)."""
    return not is_raw_stack(filename, step)


def upsert_processed_file(target: str, file_path: str, filename: str,
                           tool: str = None, step: str = None,
                           total_integration: float = None, frame_count: int = None,
                           sensor_temp: float = None, obs_date: str = None,
                           flags: str = '{}', notes: str = None, is_auto: int = 0):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO processed_files
                (target, file_path, filename, tool, step,
                 total_integration, frame_count, sensor_temp, obs_date,
                 flags, notes, is_auto)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                tool=excluded.tool, step=excluded.step,
                total_integration=excluded.total_integration,
                frame_count=excluded.frame_count,
                flags=excluded.flags, notes=excluded.notes,
                updated_at=datetime('now')""",
            (target, file_path, filename, tool, step,
             total_integration, frame_count, sensor_temp, obs_date,
             flags, notes, is_auto))


def get_processed_files(target: str, include_stack_params: bool = False) -> list[dict]:
    with get_conn() as conn:
        if include_stack_params:
            rows = conn.execute(
                """SELECT pf.*,
                          sr.hero, sr.drizzle, sr.bottom_pct, sr.framing,
                          sr.engine AS stack_engine, sr.frame_count AS stack_frame_count
                   FROM processed_files pf
                   LEFT JOIN stacking_runs sr ON sr.id = (
                       SELECT MAX(id) FROM stacking_runs
                       WHERE output_path = pf.file_path AND success = 1
                   )
                   WHERE pf.target = ?
                   ORDER BY pf.created_at DESC""",
                (target,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM processed_files WHERE target = ?
                ORDER BY created_at DESC""",
                (target,)).fetchall()
        return [dict(r) for r in rows]


def update_processed_file(file_id: int, step: str = None,
                           flags: str = None, notes: str = None):
    with get_conn() as conn:
        if step is not None:
            conn.execute(
                "UPDATE processed_files SET step=?, updated_at=datetime('now') WHERE id=?",
                (step, file_id))
        if flags is not None:
            conn.execute(
                "UPDATE processed_files SET flags=?, updated_at=datetime('now') WHERE id=?",
                (flags, file_id))
        if notes is not None:
            conn.execute(
                "UPDATE processed_files SET notes=?, updated_at=datetime('now') WHERE id=?",
                (notes, file_id))


# --- Claude assessments ---

def save_claude_assessment(target: str, processed_id, phase: str,
                            scores: dict, recommendation: dict = None,
                            raw: str = None, input_tokens: int = None,
                            output_tokens: int = None) -> int:
    import json as _json
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO claude_assessments
                (target, processed_id, phase, model, scores, recommendation,
                 raw_response, input_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (target, processed_id, phase, "claude-sonnet-4-6",
              _json.dumps(scores),
              _json.dumps(recommendation) if recommendation else None,
              raw, input_tokens, output_tokens))
        return cur.lastrowid


def get_claude_history(target: str, phase: str = None, limit: int = 20) -> list:
    with get_conn() as conn:
        if phase:
            rows = conn.execute(
                "SELECT * FROM claude_assessments WHERE target=? AND phase=? "
                "ORDER BY created_at DESC LIMIT ?",
                (target, phase, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM claude_assessments WHERE target=? "
                "ORDER BY created_at DESC LIMIT ?",
                (target, limit)).fetchall()
        return [dict(r) for r in rows]


# --- Processing history ---

def log_processing_step(target: str, step: str, engine: str = None,
                         params: dict = None, scores_before: dict = None,
                         scores_after: dict = None, claude_reasoning: str = None,
                         elapsed_s: float = None, processed_id: int = None):
    import json as _json
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO processing_history
                (target, processed_id, step, engine, params,
                 scores_before, scores_after, claude_reasoning, elapsed_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (target, processed_id, step, engine,
              _json.dumps(params) if params else None,
              _json.dumps(scores_before) if scores_before else None,
              _json.dumps(scores_after) if scores_after else None,
              claude_reasoning, elapsed_s))


def get_processing_history(target: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM processing_history WHERE target=? ORDER BY created_at ASC",
            (target,)).fetchall()
        return [dict(r) for r in rows]


# --- Story queries ---

def get_story_data(target: str | None = None) -> list[dict]:
    """
    Build per-target data dicts for the story page.
    Each dict has: target, first_date, last_date, total_subs, total_hours,
    session_count, object_type, magnitude, ra, dec, pipeline_stage, pipeline_notes,
    latest_processed_file, latest_scores, processing_steps.
    """
    import json as _json

    with get_conn() as conn:
        where = "WHERE t.target = ?" if target else ""
        params = (target,) if target else ()

        rows = conn.execute(f"""
            SELECT
                t.target, t.type AS object_type, t.magnitude, t.ra, t.dec,
                p.stage AS pipeline_stage, p.notes AS pipeline_notes,
                MIN(l.date) AS first_date,
                MAX(l.date) AS last_date,
                COUNT(DISTINCT l.id) AS total_subs,
                COALESCE(SUM(l.exposure_time), 0) AS total_seconds,
                COUNT(DISTINCT DATE(l.date)) AS session_count
            FROM targets t
            LEFT JOIN light_files l ON l.target = t.target AND l.exclude = 0
            LEFT JOIN pipeline p ON p.target = t.target
            {where}
            GROUP BY t.target
            ORDER BY MIN(l.date) ASC NULLS LAST
        """, params).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            d["total_hours"] = round((d.pop("total_seconds") or 0) / 3600, 2)

            # Latest processed file + scores
            pf = conn.execute(
                "SELECT * FROM processed_files WHERE target=? ORDER BY created_at DESC LIMIT 1",
                (d["target"],)).fetchone()
            d["latest_processed"] = dict(pf) if pf else None

            # Latest Claude scores — prefer `scores` column, fall back to `recommendation`
            # (older rows stored the JSON in recommendation before the schema was settled)
            ca = conn.execute(
                "SELECT scores, recommendation FROM claude_assessments "
                "WHERE target=? AND phase NOT IN ('story_narrative') "
                "ORDER BY created_at DESC LIMIT 1",
                (d["target"],)).fetchone()
            if ca:
                raw = ca["scores"] or ca["recommendation"]
                try:
                    d["latest_scores"] = _json.loads(raw) if raw else {}
                except Exception:
                    d["latest_scores"] = {}
            else:
                d["latest_scores"] = {}

            # Cached narrative
            narrative_row = conn.execute(
                "SELECT recommendation FROM claude_assessments "
                "WHERE target=? AND phase='story_narrative' "
                "ORDER BY created_at DESC LIMIT 1",
                (d["target"],)).fetchone()
            d["narrative"] = (narrative_row["recommendation"] if narrative_row else None)

            # Processing history — parse scores_before/after JSON inline
            ph_rows = conn.execute(
                "SELECT step, engine, params, scores_before, scores_after, "
                "claude_reasoning, elapsed_s, created_at "
                "FROM processing_history WHERE target=? ORDER BY created_at ASC",
                (d["target"],)).fetchall()
            steps = []
            for r in ph_rows:
                s = dict(r)
                for col in ("params", "scores_before", "scores_after"):
                    if s.get(col):
                        try:
                            s[col] = _json.loads(s[col])
                        except Exception:
                            pass
                steps.append(s)
            d["processing_steps"] = steps

            # Compute overall score improvement across all auto_process steps
            score_deltas = []
            for s in steps:
                if s.get("engine") == "auto_process":
                    sb = (s.get("scores_before") or {}).get("overall")
                    sa = (s.get("scores_after") or {}).get("overall")
                    if isinstance(sb, (int, float)) and isinstance(sa, (int, float)):
                        score_deltas.append((sb, sa))
            if score_deltas:
                d["score_before"] = score_deltas[0][0]
                d["score_after"] = score_deltas[-1][1]
            else:
                d["score_before"] = None
                d["score_after"] = None

            # Preview JPEG — try candidates in preference order, then any .jpg in _processed/
            lib_path = settings.get("seestar_library_path", "/mnt/nas_data")
            proc_dir = Path(lib_path) / d["target"] / "_processed"
            candidates = ["auto_final_preview.jpg", "preview.jpg"]
            if d["latest_processed"] and d["latest_processed"].get("filename"):
                stem = d["latest_processed"]["filename"].replace(".fits", "").replace(".fit", "")
                candidates += [f"{stem}_preview.jpg", f"{stem}.jpg"]
            d["preview_filename"] = None
            for fn in candidates:
                if (proc_dir / fn).exists():
                    d["preview_filename"] = fn
                    break
            # Last resort: any .jpg in _processed/
            if d["preview_filename"] is None and proc_dir.exists():
                jpgs = sorted(proc_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime,
                              reverse=True)
                if jpgs:
                    d["preview_filename"] = jpgs[0].name

            result.append(d)

    return result


def get_global_story_stats() -> dict:
    """Aggregate stats across all targets for the story page header."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT target) AS total_targets,
                COUNT(*) AS total_subs,
                COALESCE(SUM(exposure_time), 0) AS total_seconds,
                MIN(date) AS first_date,
                MAX(date) AS last_date
            FROM light_files WHERE exclude = 0
        """).fetchone()
        d = dict(row)
        d["total_hours"] = round((d.pop("total_seconds") or 0) / 3600, 1)

        stacked = conn.execute(
            "SELECT COUNT(*) AS cnt FROM processed_files").fetchone()
        d["total_stacked"] = stacked["cnt"]

        return d


# --- Experiment results ---

def record_experiment_variant(target: str, object_type: str, step: str,
                               variant_id: str, params: dict, scores: dict,
                               winner: bool, reasoning: str = None,
                               metrics_json: str = None,
                               experiment_run_id: str = None,
                               all_scores_json: str = None,
                               runner_up_score: float = None,
                               winning_margin: float = None) -> int:
    import json as _json
    overall = scores.get("overall") if isinstance(scores, dict) else None
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO experiment_results
                (target, object_type, step, variant_id, params, scores,
                 overall_score, winner, claude_reasoning,
                 metrics_json, experiment_run_id, all_scores_json,
                 runner_up_score, winning_margin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (target, object_type, step, variant_id,
              _json.dumps(params) if params else None,
              _json.dumps(scores) if scores else None,
              overall, 1 if winner else 0, reasoning,
              metrics_json, experiment_run_id, all_scores_json,
              runner_up_score, winning_margin))
        return cur.lastrowid


def get_experiment_priors(step: str, object_type: str = None, limit: int = 100) -> dict:
    """
    Aggregate experiment history for a step into win rates and average winning params.

    Returns {
        "sample_count": N,
        "variant_wins": {"graxpert": 5, "pi_gc": 2, ...},
        "variant_win_rate": {"graxpert": 0.71, ...},
        "top_variant": "graxpert",
        "top_variant_avg_params": {"smoothing": 0.4, ...},
        "top_variant_avg_score": 7.2,
    }
    """
    import json as _json

    with get_conn() as conn:
        if object_type and object_type != "unknown":
            rows = conn.execute(
                "SELECT * FROM experiment_results WHERE step=? AND object_type=? "
                "ORDER BY created_at DESC LIMIT ?",
                (step, object_type, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM experiment_results WHERE step=? "
                "ORDER BY created_at DESC LIMIT ?",
                (step, limit)).fetchall()

    rows = [dict(r) for r in rows]
    if not rows:
        return {"sample_count": 0}

    wins: dict[str, int] = {}
    scores_by_variant: dict[str, list] = {}
    params_by_winner: dict[str, list] = {}

    for r in rows:
        vid = r["variant_id"]
        wins.setdefault(vid, 0)
        if r["winner"]:
            wins[vid] += 1
        if r["overall_score"] is not None:
            scores_by_variant.setdefault(vid, []).append(r["overall_score"])
        if r["winner"] and r["params"]:
            try:
                p = _json.loads(r["params"])
                params_by_winner.setdefault(vid, []).append(p)
            except Exception:
                pass

    total_experiments = len({(r["target"], r["created_at"][:16]) for r in rows})
    total_wins = sum(wins.values())
    win_rate = {v: round(w / total_wins, 2) if total_wins else 0 for v, w in wins.items()}
    top_variant = max(wins, key=wins.get) if wins else None

    avg_params: dict = {}
    if top_variant and params_by_winner.get(top_variant):
        all_p = params_by_winner[top_variant]
        keys = set(k for p in all_p for k in p)
        for k in keys:
            vals = [p[k] for p in all_p if k in p and isinstance(p[k], (int, float))]
            if vals:
                avg_params[k] = round(sum(vals) / len(vals), 3)

    avg_score = None
    if top_variant and scores_by_variant.get(top_variant):
        sv = scores_by_variant[top_variant]
        avg_score = round(sum(sv) / len(sv), 1)

    return {
        "sample_count": len(rows),
        "variant_wins": wins,
        "variant_win_rate": win_rate,
        "top_variant": top_variant,
        "top_variant_avg_params": avg_params,
        "top_variant_avg_score": avg_score,
    }


def get_all_experiment_steps() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT step FROM experiment_results ORDER BY step").fetchall()
        return [r["step"] for r in rows]


# --- Processing runs ---

def save_processing_run(
    target: str,
    workflow: str,
    started_at: str,
    elapsed_s: float,
    steps: list[dict],
    initial_scores: dict,
    final_scores: dict,
    critical_eval: str | None,
    output_path: str | None,
    dry_run: bool = False,
    api_diagnostics: dict | None = None,
) -> int:
    """Save a completed auto-process run and return its ID."""
    import json as _json
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO processing_runs
               (target, workflow, started_at, elapsed_s, steps_json,
                initial_scores, final_scores, critical_eval, output_path, dry_run,
                api_diagnostics)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target, workflow, started_at, elapsed_s,
                _json.dumps(steps),
                _json.dumps(initial_scores),
                _json.dumps(final_scores),
                critical_eval,
                output_path,
                1 if dry_run else 0,
                _json.dumps(api_diagnostics) if api_diagnostics else None,
            ),
        )
        return cur.lastrowid


def get_processing_runs(target: str | None = None, limit: int = 50) -> list[dict]:
    """Return processing run records, newest first."""
    import json as _json
    with get_conn() as conn:
        if target:
            rows = conn.execute(
                "SELECT * FROM processing_runs WHERE target=? ORDER BY finished_at DESC LIMIT ?",
                (target, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM processing_runs ORDER BY finished_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        for field in ("steps_json", "initial_scores", "final_scores"):
            try:
                d[field] = _json.loads(d[field] or "[]" if field == "steps_json" else "{}")
            except Exception:
                d[field] = [] if field == "steps_json" else {}
        result.append(d)
    return result


def get_worklist(rework_threshold: float = 7.0) -> list[dict]:
    """Per-target processing status, for the worklist page.

    Buckets every active target that has stacks or a processing run into:
      - "unprocessed": has stacks but no (non-dry) auto_process run yet
      - "rework": best run scored below rework_threshold, or new subs were
        captured after the last run finished
      - "good": best run at/above threshold and no newer data

    Returns dicts with: target, type, bucket, best_overall, best_run_id,
    best_output_path, best_finished_at, stack_count, latest_stack, newer_data,
    priority.
    """
    import json as _json
    with get_conn() as conn:
        # Processable unit = a successful pipeline stack in _processed/ (tracked in
        # stacking_runs). Device in-camera stacks (stacked_files) are NOT processable on
        # their own — the auto_process worker reads from _processed/. Keying the worklist
        # on stacking_runs keeps targets that only have device stacks (e.g. M 104) off the
        # page so we never surface a "stack available" the pipeline can't act on.
        stacks = {r["target"]: (r["n"], r["latest"]) for r in conn.execute(
            "SELECT target, COUNT(*) AS n, MAX(finished_at) AS latest "
            "FROM stacking_runs WHERE success=1 GROUP BY target")}
        # Device-capture recency, used only to detect new subs captured after the last run.
        device_latest = {r["target"]: r["latest"] for r in conn.execute(
            "SELECT target, MAX(date) AS latest FROM stacked_files GROUP BY target")}
        runs = conn.execute(
            "SELECT target, id, finished_at, output_path, final_scores "
            "FROM processing_runs WHERE (dry_run IS NULL OR dry_run=0)").fetchall()
        meta = {r["target"]: dict(r) for r in conn.execute(
            "SELECT target, type, priority, inactive FROM targets")}

    best: dict[str, dict] = {}
    latest_run: dict[str, str] = {}
    for r in runs:
        t = r["target"]
        fin = r["finished_at"] or ""
        if fin > latest_run.get(t, ""):
            latest_run[t] = fin
        try:
            ov = _json.loads(r["final_scores"] or "{}").get("overall")
        except Exception:
            ov = None
        ov = float(ov) if isinstance(ov, (int, float)) else None
        cur = best.get(t)
        if ov is not None and (cur is None or ov > cur["overall"]):
            best[t] = {"overall": ov, "run_id": r["id"],
                       "output_path": r["output_path"] or "", "finished_at": fin}

    rows = []
    for t in set(stacks) | set(latest_run):
        m = meta.get(t, {})
        if m.get("inactive"):
            continue
        n_stk, latest_stk = stacks.get(t, (0, None))
        b = best.get(t)
        has_run = t in latest_run
        # "Newer data" = device subs captured after the last processing run finished.
        dev_latest = device_latest.get(t)
        newer = bool(dev_latest and latest_run.get(t)
                     and str(dev_latest)[:10] > latest_run[t][:10])
        if not has_run:
            bucket = "unprocessed"
        elif (b and b["overall"] is not None and b["overall"] < rework_threshold) or newer:
            bucket = "rework"
        else:
            bucket = "good"
        rows.append({
            "target": t,
            "type": m.get("type") or "",
            "priority": m.get("priority") or 0,
            "bucket": bucket,
            "best_overall": b["overall"] if b else None,
            "best_run_id": b["run_id"] if b else None,
            "best_output_path": b["output_path"] if b else "",
            "best_finished_at": b["finished_at"] if b else "",
            "stack_count": n_stk,
            "latest_stack": latest_stk,
            "newer_data": newer,
        })
    return rows


# Static (user-curated) lists used by the worklist page.
WORKLIST_LIST = "Work-through"
REWORK_LIST = "Needs-rework"


def list_add(list_name: str, target: str) -> bool:
    """Add a target to a named list (membership in list_targets).

    Lazily creates the list (is_dynamic=0) if missing. Returns False when the
    target has no row in `targets`. Idempotent — re-adding is a no-op.
    """
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO lists(name, is_dynamic, priority) "
                     "VALUES (?, 0, 0)", (list_name,))
        lid = conn.execute("SELECT id FROM lists WHERE name=?", (list_name,)).fetchone()
        tid = conn.execute("SELECT id FROM targets WHERE target=?", (target,)).fetchone()
        if not lid or not tid:
            return False
        exists = conn.execute(
            "SELECT 1 FROM list_targets WHERE list_id=? AND target_id=?",
            (lid["id"], tid["id"])).fetchone()
        if not exists:
            conn.execute("INSERT INTO list_targets(list_id, target_id) VALUES (?, ?)",
                         (lid["id"], tid["id"]))
        return True


def list_remove(list_name: str, target: str) -> None:
    """Remove a target from a named list. No-op if not present."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM list_targets WHERE "
            "list_id=(SELECT id FROM lists WHERE name=?) AND "
            "target_id=(SELECT id FROM targets WHERE target=?)",
            (list_name, target))


def get_list(list_name: str) -> list[str]:
    """Ordered target names in a named list (insertion order via list_targets.id)."""
    with get_conn() as conn:
        return [r["target"] for r in conn.execute(
            "SELECT t.target FROM list_targets lt "
            "JOIN lists l ON l.id = lt.list_id "
            "JOIN targets t ON t.id = lt.target_id "
            "WHERE l.name = ? ORDER BY lt.id", (list_name,))]


def get_calendar_events(year: int, month: int) -> dict:
    """
    Return events for a calendar month grouped by ISO date string.
    Result: {"YYYY-MM-DD": {"captures": [(target, count)], "processing": [(target, run_id, workflow)], "devlog": [(id, title)]}}
    """
    ym = f"{year:04d}-{month:02d}"
    events: dict = {}

    def _day(d: dict, key: str):
        events.setdefault(d["day"], {}).setdefault(key, [])

    with get_conn() as conn:
        # Capture nights
        rows = conn.execute(
            """SELECT DATE(date) AS day, target, COUNT(*) AS cnt
               FROM light_files
               WHERE DATE(date) LIKE ?
               GROUP BY day, target ORDER BY day, target""",
            (ym + "%",),
        ).fetchall()
        for r in rows:
            events.setdefault(r["day"], {}).setdefault("captures", []).append(
                (r["target"], r["cnt"])
            )

        # Processing runs
        rows = conn.execute(
            """SELECT DATE(finished_at) AS day, id, target, workflow
               FROM processing_runs
               WHERE DATE(finished_at) LIKE ?
               ORDER BY day, finished_at""",
            (ym + "%",),
        ).fetchall()
        for r in rows:
            events.setdefault(r["day"], {}).setdefault("processing", []).append(
                (r["target"], r["id"], r["workflow"] or "")
            )

    return events


# --- Stacking runs ---

def save_stacking_run(target: str, engine: str, started_at: str,
                      frame_count: int, elapsed_s: float,
                      success: bool, error: str | None = None,
                      log_tail: str | None = None,
                      output_path: str | None = None,
                      metrics: dict | None = None,
                      params: dict | None = None) -> int:
    m = metrics or {}
    p = params or {}
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO stacking_runs
               (target, engine, started_at, frame_count, elapsed_s,
                success, error, log_tail, output_path,
                sigma_sky, snr_stack, fwhm_stack, ecc_stack,
                flatness_rms, clipping_frac, star_count_stack, efficiency,
                hero, drizzle, bottom_pct, ecc_threshold, exptime, framing)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (target, engine, started_at, frame_count, elapsed_s,
             1 if success else 0, error,
             log_tail[-4000:] if log_tail else None,
             output_path,
             m.get("sigma_sky"), m.get("snr_stack"), m.get("fwhm_stack"),
             m.get("ecc_stack"), m.get("flatness_rms"), m.get("clipping_frac"),
             m.get("star_count"), m.get("efficiency"),
             1 if p.get("hero") else 0,
             1 if p.get("drizzle") else 0,
             p.get("bottom_pct"), p.get("ecc_threshold"),
             p.get("exptime"), p.get("framing")),
        )
        return cur.lastrowid


def get_stacking_runs(target: str | None = None, limit: int = 50,
                      dedupe_outputs: bool = False) -> list[dict]:
    """Return stacking runs. If dedupe_outputs=True, keep only the latest run per output_path."""
    with get_conn() as conn:
        if dedupe_outputs:
            if target:
                rows = conn.execute("""
                    SELECT * FROM stacking_runs WHERE id IN (
                        SELECT MAX(id) FROM stacking_runs
                        WHERE target=? AND success=1
                        GROUP BY COALESCE(output_path, CAST(id AS TEXT))
                    ) ORDER BY finished_at DESC LIMIT ?
                """, (target, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM stacking_runs WHERE id IN (
                        SELECT MAX(id) FROM stacking_runs
                        WHERE success=1
                        GROUP BY target, COALESCE(output_path, CAST(id AS TEXT))
                    ) ORDER BY finished_at DESC LIMIT ?
                """, (limit,)).fetchall()
        elif target:
            rows = conn.execute(
                "SELECT * FROM stacking_runs WHERE target=? ORDER BY finished_at DESC LIMIT ?",
                (target, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stacking_runs ORDER BY finished_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_stacking_runs_with_scores(target: str | None = None, limit: int = 100) -> list[dict]:
    """Like get_stacking_runs but includes Claude assessment scores via JOIN.
    Deduplicates by output_path — only the latest successful run per unique output file."""
    import json as _json
    with get_conn() as conn:
        if target:
            rows = conn.execute("""
                SELECT sr.*, ca.scores as claude_scores_json, ca.recommendation as claude_rec
                FROM stacking_runs sr
                LEFT JOIN processed_files pf ON sr.output_path = pf.file_path
                LEFT JOIN claude_assessments ca
                       ON ca.id = (
                           SELECT MAX(id) FROM claude_assessments ca2
                           WHERE ca2.processed_id = pf.id AND ca2.phase = 'post_stack'
                       )
                WHERE sr.target = ? AND sr.success = 1
                AND sr.id IN (
                    SELECT MAX(id) FROM stacking_runs
                    WHERE target = ? AND success = 1
                    GROUP BY COALESCE(output_path, CAST(id AS TEXT))
                )
                ORDER BY sr.finished_at DESC LIMIT ?
            """, (target, target, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT sr.*, ca.scores as claude_scores_json, ca.recommendation as claude_rec
                FROM stacking_runs sr
                LEFT JOIN processed_files pf ON sr.output_path = pf.file_path
                LEFT JOIN claude_assessments ca
                       ON ca.id = (
                           SELECT MAX(id) FROM claude_assessments ca2
                           WHERE ca2.processed_id = pf.id AND ca2.phase = 'post_stack'
                       )
                WHERE sr.success = 1
                AND sr.id IN (
                    SELECT MAX(id) FROM stacking_runs
                    WHERE success = 1
                    GROUP BY target, COALESCE(output_path, CAST(id AS TEXT))
                )
                ORDER BY sr.finished_at DESC LIMIT ?
            """, (limit,)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d['claude_scores'] = _json.loads(d.get('claude_scores_json') or '{}')
        except Exception:
            d['claude_scores'] = {}
        results.append(d)
    return results


# --- Capture frames ---

def get_frames_by_target(target: str) -> list[dict]:
    """Return all light frames for a target ordered by date."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, target, date, file_name, file_path, exposure_time,
                      exclude, fwhm, eccentricity, snr
               FROM light_files WHERE target=? ORDER BY date""",
            (target,),
        ).fetchall()
    return [dict(r) for r in rows]


def toggle_frame_exclude(file_path: str) -> bool:
    """Flip the exclude flag for a frame. Returns the new value."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT exclude FROM light_files WHERE file_path=?", (file_path,)
        ).fetchone()
        if row is None:
            return False
        new_val = 0 if row["exclude"] else 1
        conn.execute(
            "UPDATE light_files SET exclude=? WHERE file_path=?",
            (new_val, file_path),
        )
    return bool(new_val)


def get_pipeline_with_stats() -> list[dict]:
    """Return pipeline rows augmented with frame counts and last stack info."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.target, p.stage, p.updated_at, p.notes,
                   COUNT(l.id) AS total_frames,
                   COUNT(CASE WHEN l.exclude=0 THEN 1 END) AS net_frames,
                   COALESCE(SUM(CASE WHEN l.exclude=0 THEN l.exposure_time ELSE 0 END), 0) AS net_seconds
            FROM pipeline p
            LEFT JOIN light_files l ON l.target = p.target
            GROUP BY p.target
            ORDER BY p.updated_at DESC
        """).fetchall()
        result = [dict(r) for r in rows]

        # Attach last stacking run per target
        for row in result:
            sr = conn.execute(
                "SELECT success, engine, finished_at, error FROM stacking_runs "
                "WHERE target=? ORDER BY finished_at DESC LIMIT 1",
                (row["target"],),
            ).fetchone()
            row["last_stack"] = dict(sr) if sr else None

    return result


# --- Association management ---

def get_mosaic_panel_targets(primary: str) -> list[str]:
    """Return all targets whose mosaic_association points to `primary`.

    These are the secondary panels of a multi-panel mosaic. When stacking
    the primary target, their frames should be pooled and framing=max used.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT target FROM targets WHERE mosaic_association=? ORDER BY target",
            (primary,)
        ).fetchall()
    return [r["target"] for r in rows]


def set_target_association(target: str, association: str | None,
                           mosaic_association: str | None = None,
                           mosaic: int | None = None,
                           _fields: set | None = None) -> None:
    """Update association fields for a target.

    _fields: if provided, only update those column names (prevents one field
    clobbering another when the UI saves fields independently).
    """
    with get_conn() as conn:
        if _fields and "mosaic" in _fields and len(_fields) == 1:
            conn.execute(
                "UPDATE targets SET mosaic=? WHERE target=?",
                (1 if mosaic else 0, target),
            )
        elif _fields is None or _fields == {"association", "mosaic_association"}:
            conn.execute(
                "UPDATE targets SET association=?, mosaic_association=? WHERE target=?",
                (association or None, mosaic_association or None, target),
            )
        elif "association" in _fields:
            conn.execute(
                "UPDATE targets SET association=? WHERE target=?",
                (association or None, target),
            )
        elif "mosaic_association" in _fields:
            conn.execute(
                "UPDATE targets SET mosaic_association=? WHERE target=?",
                (mosaic_association or None, target),
            )


def get_all_targets_with_associations() -> list[dict]:
    """Return all targets with association data and frame count for the association manager."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.target, t.association, t.mosaic_association, t.mosaic,
                   t.type, p.stage,
                   COUNT(DISTINCT l.id) AS frame_count,
                   COALESCE(t.transient, 0) AS transient
            FROM targets t
            LEFT JOIN pipeline p ON p.target = t.target
            LEFT JOIN light_files l ON l.target = t.target
            GROUP BY t.target
            ORDER BY
                CASE WHEN t.association IS NOT NULL THEN t.association ELSE t.target END,
                t.target
        """).fetchall()
        return [dict(r) for r in rows]


def get_target_detail(target: str) -> dict | None:
    """Return a single target's full detail: story data + stacking runs + assessments."""
    from nas_server.database import (get_story_data, get_stacking_runs,
                                     get_claude_history, get_processed_files)
    targets = get_story_data(target)
    if not targets:
        return None
    t = targets[0]
    t["stacking_runs"] = get_stacking_runs(target=target, limit=30)
    t["claude_assessments"] = get_claude_history(target=target, limit=5)
    t["processed_files"] = get_processed_files(target=target)
    return t


# Messier → NGC/IC canonical mapping (bidirectional)
_MESSIER_NGC: dict[str, str] = {
    "M 1": "NGC 1952", "M 2": "NGC 7089", "M 3": "NGC 5272", "M 4": "NGC 6121",
    "M 5": "NGC 5904", "M 6": "NGC 6405", "M 7": "NGC 6475", "M 8": "NGC 6523",
    "M 9": "NGC 6333", "M 10": "NGC 6254", "M 11": "NGC 6705", "M 12": "NGC 6218",
    "M 13": "NGC 6205", "M 14": "NGC 6402", "M 15": "NGC 7078", "M 16": "NGC 6611",
    "M 17": "NGC 6618", "M 18": "NGC 6613", "M 19": "NGC 6273", "M 20": "NGC 6514",
    "M 21": "NGC 6531", "M 22": "NGC 6656", "M 23": "NGC 6494", "M 24": "NGC 6603",
    "M 25": "IC 4725", "M 26": "NGC 6694", "M 27": "NGC 6853", "M 28": "NGC 6626",
    "M 29": "NGC 6913", "M 30": "NGC 7099", "M 31": "NGC 224", "M 32": "NGC 221",
    "M 33": "NGC 598", "M 34": "NGC 1039", "M 35": "NGC 2168", "M 36": "NGC 1960",
    "M 37": "NGC 2099", "M 38": "NGC 1912", "M 39": "NGC 7092", "M 41": "NGC 2287",
    "M 42": "NGC 1976", "M 43": "NGC 1982", "M 44": "NGC 2632", "M 45": "Mel 22",
    "M 46": "NGC 2437", "M 47": "NGC 2422", "M 48": "NGC 2548", "M 49": "NGC 4472",
    "M 50": "NGC 2323", "M 51": "NGC 5194", "M 52": "NGC 7654", "M 53": "NGC 5024",
    "M 54": "NGC 6715", "M 55": "NGC 6809", "M 56": "NGC 6779", "M 57": "NGC 6720",
    "M 58": "NGC 4579", "M 59": "NGC 4621", "M 60": "NGC 4649", "M 61": "NGC 4303",
    "M 62": "NGC 6266", "M 63": "NGC 5055", "M 64": "NGC 4826", "M 65": "NGC 3623",
    "M 66": "NGC 3627", "M 67": "NGC 2682", "M 68": "NGC 4590", "M 69": "NGC 6637",
    "M 70": "NGC 6681", "M 71": "NGC 6838", "M 72": "NGC 6981", "M 74": "NGC 628",
    "M 75": "NGC 6864", "M 76": "NGC 650", "M 77": "NGC 1068", "M 78": "NGC 2068",
    "M 79": "NGC 1904", "M 80": "NGC 6093", "M 81": "NGC 3031", "M 82": "NGC 3034",
    "M 83": "NGC 5236", "M 84": "NGC 4374", "M 85": "NGC 4382", "M 86": "NGC 4406",
    "M 87": "NGC 4486", "M 88": "NGC 4501", "M 89": "NGC 4552", "M 90": "NGC 4569",
    "M 91": "NGC 4548", "M 92": "NGC 6341", "M 93": "NGC 2447", "M 94": "NGC 4736",
    "M 95": "NGC 3351", "M 96": "NGC 3368", "M 97": "NGC 3587", "M 98": "NGC 4192",
    "M 99": "NGC 4254", "M 100": "NGC 4321", "M 101": "NGC 5457", "M 102": "NGC 5866",
    "M 103": "NGC 581", "M 104": "NGC 4594", "M 105": "NGC 3379", "M 106": "NGC 4258",
    "M 107": "NGC 6171", "M 108": "NGC 3556", "M 109": "NGC 3992", "M 110": "NGC 205",
}

# Caldwell → NGC/IC mapping (from caldwell_associations.py)
_CALDWELL_NGC: dict[str, str] = {
    "C 1": "NGC 188", "C 2": "NGC 40", "C 3": "NGC 4236", "C 4": "NGC 7023",
    "C 5": "IC 342", "C 6": "NGC 6543", "C 7": "NGC 2403", "C 8": "NGC 559",
    "C 9": "Sh 2-155", "C 10": "NGC 663", "C 11": "NGC 7635", "C 12": "NGC 6946",
    "C 13": "NGC 457", "C 14": "NGC 869", "C 15": "NGC 6826", "C 16": "NGC 7243",
    "C 17": "NGC 147", "C 18": "NGC 185", "C 19": "IC 5146", "C 20": "NGC 7000",
    "C 21": "NGC 4449", "C 22": "NGC 7662", "C 23": "NGC 891", "C 24": "NGC 1275",
    "C 25": "NGC 2419", "C 26": "NGC 4244", "C 27": "NGC 6888", "C 28": "NGC 752",
    "C 29": "NGC 5005", "C 30": "NGC 7331", "C 31": "IC 405", "C 32": "NGC 4631",
    "C 33": "NGC 6992", "C 34": "NGC 6960", "C 35": "NGC 4889", "C 36": "NGC 4559",
    "C 37": "NGC 6885", "C 38": "NGC 4565", "C 39": "NGC 2392", "C 40": "NGC 3626",
    "C 41": "NGC 7006", "C 42": "NGC 7009", "C 43": "NGC 7814", "C 44": "NGC 7479",
    "C 45": "NGC 5248", "C 46": "NGC 2261", "C 47": "NGC 6934", "C 48": "NGC 2775",
    "C 49": "NGC 2237", "C 50": "NGC 2244", "C 51": "NGC 5195", "C 52": "NGC 4697",
    "C 53": "NGC 3115", "C 54": "NGC 2506", "C 56": "NGC 246", "C 57": "NGC 6822",
    "C 58": "NGC 2360", "C 59": "NGC 3242", "C 60": "NGC 4039", "C 61": "NGC 4038",
    "C 62": "NGC 247", "C 63": "NGC 7293", "C 64": "NGC 2362", "C 65": "NGC 253",
    "C 66": "NGC 5694", "C 67": "NGC 1097", "C 68": "NGC 6729", "C 69": "NGC 6302",
    "C 70": "NGC 300", "C 71": "NGC 2477", "C 72": "NGC 55", "C 73": "NGC 1851",
    "C 74": "NGC 3132", "C 75": "NGC 6124", "C 76": "NGC 6231", "C 77": "NGC 5128",
    "C 78": "NGC 6541", "C 79": "NGC 3201", "C 80": "NGC 5139", "C 81": "NGC 6352",
    "C 82": "NGC 6193", "C 83": "NGC 4945", "C 84": "NGC 5286", "C 85": "IC 2391",
    "C 86": "NGC 6397", "C 88": "NGC 5823", "C 89": "NGC 6067", "C 90": "NGC 2867",
    "C 91": "NGC 3532", "C 92": "NGC 3372", "C 93": "NGC 6752", "C 94": "NGC 4755",
    "C 95": "NGC 6025", "C 96": "NGC 2516", "C 97": "NGC 3766", "C 98": "NGC 4609",
    "C 100": "NGC 3699", "C 101": "NGC 6744", "C 102": "NGC 3504", "C 103": "NGC 2070",
    "C 104": "NGC 104", "C 105": "NGC 4833", "C 107": "NGC 6101", "C 108": "NGC 4372",
    "C 109": "NGC 3195",
}


def _build_alias_index() -> dict[str, str]:
    """Return {canonical_upper_normalized → canonical_key} for all catalog entries."""
    idx: dict[str, str] = {}
    for cat in (_MESSIER_NGC, _CALDWELL_NGC):
        for k, v in cat.items():
            ku = k.upper().replace(" ", "")
            vu = v.upper().replace(" ", "")
            idx[ku] = k
            idx[vu] = v
    return idx


def suggest_associations() -> list[dict]:
    """
    Scan all DB targets for likely associations using catalog mappings and name normalization.
    Returns list of {target_a, target_b, method, via} dicts, excluding already-linked pairs.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT target, association FROM targets ORDER BY target"
        ).fetchall()

    all_targets = {r["target"]: (r["association"] or "") for r in rows}
    target_set_upper = {t.upper().replace(" ", ""): t for t in all_targets}

    # Build reverse alias map: canonical_upper → canonical name
    alias_idx = _build_alias_index()

    suggestions: list[dict] = []
    seen: set[frozenset] = set()

    # Already-linked targets
    def _already_linked(a: str, b: str) -> bool:
        assoc_a = all_targets.get(a, "")
        assoc_b = all_targets.get(b, "")
        return (b in assoc_a) or (a in assoc_b)

    def _add(ta: str, tb: str, method: str, via: str) -> None:
        key = frozenset({ta, tb})
        if key in seen or ta not in all_targets or tb not in all_targets:
            return
        if _already_linked(ta, tb):
            return
        seen.add(key)
        suggestions.append({"target_a": ta, "target_b": tb, "method": method, "via": via})

    # Pass 1: catalog mapping — look up each DB target in both directions
    for catalog_name, ngc_name in {**_MESSIER_NGC, **_CALDWELL_NGC}.items():
        cu = catalog_name.upper().replace(" ", "")
        nu = ngc_name.upper().replace(" ", "")
        cat_in_db = target_set_upper.get(cu)
        ngc_in_db = target_set_upper.get(nu)
        if cat_in_db and ngc_in_db:
            src = "Caldwell" if catalog_name.startswith("C ") else "Messier"
            _add(cat_in_db, ngc_in_db, src, f"{catalog_name} = {ngc_name}")

    # Pass 2: SH/Sharpless variants (e.g. "SH 2-298" vs "Sh2-298" vs "SH2-298")
    sh_pattern: dict[str, str] = {}
    import re as _re
    for tgt in all_targets:
        m = _re.match(r"SH\s*2[-\s](\d+)", tgt.upper())
        if m:
            key = m.group(1)
            if key in sh_pattern and sh_pattern[key] != tgt:
                _add(sh_pattern[key], tgt, "Sharpless variant", tgt)
            else:
                sh_pattern[key] = tgt

    # Pass 3: NGC/IC suffix variants (e.g. "NGC 5194" vs "NGC 5194A")
    ngc_base: dict[str, list[str]] = {}
    for tgt in all_targets:
        m = _re.match(r"(NGC|IC)\s*(\d+)([A-Z]?)$", tgt.upper())
        if m:
            base = f"{m.group(1)} {m.group(2)}"
            ngc_base.setdefault(base, []).append(tgt)
    for base, group in ngc_base.items():
        if len(group) > 1:
            for i, ta in enumerate(group):
                for tb in group[i+1:]:
                    _add(ta, tb, "NGC/IC variant", base)

    # Pass 4: RA/Dec proximity — targets whose frame centroids are close on the sky.
    # Threshold 0.5° targets likely-same-object matches (name variants, alternate catalogs).
    # The full 1.48° FOV diagonal is used for frame overlap in stacking, not here.
    import math
    SAME_OBJECT_DEG = 0.5
    radec_suggestions: list[tuple[float, dict]] = []
    try:
        with get_conn() as conn:
            radec_rows = conn.execute(
                "SELECT target, AVG(ra) AS ra, AVG(dec) AS dec "
                "FROM light_files WHERE ra IS NOT NULL GROUP BY target"
            ).fetchall()
        centroids = {r["target"]: (r["ra"], r["dec"]) for r in radec_rows
                     if r["target"] in all_targets}
        target_list = list(centroids.items())
        for i, (ta, (ra1, dec1)) in enumerate(target_list):
            r1, d1 = math.radians(ra1), math.radians(dec1)
            for tb, (ra2, dec2) in target_list[i+1:]:
                r2, d2 = math.radians(ra2), math.radians(dec2)
                cos_sep = (math.sin(d1) * math.sin(d2)
                           + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2))
                sep = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
                if sep < SAME_OBJECT_DEG:
                    key = frozenset({ta, tb})
                    if key not in seen and not _already_linked(ta, tb):
                        radec_suggestions.append((sep, {"target_a": ta, "target_b": tb,
                                                        "method": "RA/Dec proximity",
                                                        "via": f"{sep:.2f}°"}))
        # Sort tightest separation first (most likely same object)
        radec_suggestions.sort(key=lambda x: x[0])
        for _, s in radec_suggestions:
            _add(s["target_a"], s["target_b"], s["method"], s["via"])
    except Exception:
        pass

    return suggestions


def link_association(target_a: str, target_b: str) -> None:
    """Bidirectionally add target_b to target_a's association and vice versa."""
    with get_conn() as conn:
        for src, dst in [(target_a, target_b), (target_b, target_a)]:
            row = conn.execute(
                "SELECT association FROM targets WHERE target=?", (src,)
            ).fetchone()
            if row is None:
                continue
            existing = set(s.strip() for s in (row["association"] or "").split(",") if s.strip())
            existing.add(dst)
            conn.execute(
                "UPDATE targets SET association=? WHERE target=?",
                (", ".join(sorted(existing)), src),
            )


# --- Persistent queue ---

def queue_insert(item: dict) -> int:
    import json as _json
    _ep = item.get("extra_params")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO queue_jobs
               (job_type, target, workflow, experiment_mode, dry_run, source_file,
                engine, cull, bottom_pct, min_stars, fast, framing, hero, drizzle,
                exptime, ecc_threshold, manual_review, sky_level_factor, gradient_threshold,
                extra_params)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.get("job_type", "process"),
                item["target"],
                item.get("workflow"),
                1 if item.get("experiment_mode") else 0,
                1 if item.get("dry_run") else 0,
                item.get("source_file"),
                item.get("engine"),
                1 if item.get("cull", True) else 0,
                item.get("bottom_pct", 0.10),
                item.get("min_stars", 20),
                1 if item.get("fast") else 0,
                item.get("framing", "min"),
                1 if item.get("hero") else 0,
                1 if item.get("drizzle") else 0,
                item.get("exptime"),
                item.get("ecc_threshold", 0.66),
                1 if item.get("manual_review") else 0,
                item.get("sky_level_factor", 3.0),
                item.get("gradient_threshold", 0.5),
                _json.dumps(_ep) if _ep else None,
            ),
        )
        return cur.lastrowid


def queue_delete(row_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM queue_jobs WHERE id = ?", (row_id,))


def queue_clear_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM queue_jobs")


def queue_load_pending() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM queue_jobs ORDER BY created_at ASC, id ASC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        db_id = d.pop("id")
        d["_db_id"] = db_id
        d["experiment_mode"] = bool(d.get("experiment_mode"))
        d["dry_run"] = bool(d.get("dry_run"))
        d["cull"] = bool(d.get("cull"))
        d["fast"] = bool(d.get("fast"))
        d["hero"] = bool(d.get("hero"))
        d["drizzle"] = bool(d.get("drizzle"))
        d["framing"] = d.get("framing") or "min"
        # exptime stays as int or None; ecc_threshold defaults to 0.66 for old rows
        d["ecc_threshold"] = float(d.get("ecc_threshold") or 0.66)
        d["sky_level_factor"] = float(d.get("sky_level_factor") or 3.0)
        d["gradient_threshold"] = float(d.get("gradient_threshold") or 0.5)
        d["manual_review"] = bool(d.get("manual_review"))
        _ep = d.pop("extra_params", None)
        if _ep:
            import json as _json
            try:
                d["extra_params"] = _json.loads(_ep)
            except Exception:
                d["extra_params"] = {}
        else:
            d["extra_params"] = {}
        result.append(d)
    return result


# --- Manual reviews ---

def create_manual_review(target: str, step: str, run_id: str,
                          input_fits_path: str | None,
                          ordered_labels: list, variants: list,
                          claude_winner_label: str | None,
                          claude_reasoning: str | None,
                          expires_at: str) -> int:
    import json as _json
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO manual_reviews
                (target, step, run_id, input_fits_path, ordered_labels, variants_json,
                 claude_winner_label, claude_reasoning, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (target, step, run_id, input_fits_path,
              _json.dumps(ordered_labels), _json.dumps(variants),
              claude_winner_label, claude_reasoning, expires_at))
        return cur.lastrowid


def get_manual_review(review_id: int) -> dict | None:
    import json as _json
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM manual_reviews WHERE id=?", (review_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    for f in ("ordered_labels", "variants_json"):
        try:
            d[f] = _json.loads(d[f] or "[]")
        except Exception:
            d[f] = []
    return d


def get_pending_reviews() -> list[dict]:
    import json as _json
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_reviews WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        for f in ("ordered_labels", "variants_json"):
            try:
                d[f] = _json.loads(d[f] or "[]")
            except Exception:
                d[f] = []
        result.append(d)
    return result


def get_pending_review_count() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM manual_reviews WHERE status='pending'"
        ).fetchone()
    return row[0] if row else 0


def decide_manual_review(review_id: int, user_winner_label: str,
                          user_reasoning: str | None,
                          final_winner_variant: str,
                          agreed: bool) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE manual_reviews SET
                user_winner_label=?, user_reasoning=?, final_winner_variant=?,
                agreed=?, status='decided', resolved_at=datetime('now')
            WHERE id=?
        """, (user_winner_label, user_reasoning, final_winner_variant,
              1 if agreed else 0, review_id))


def set_review_status(review_id: int, status: str,
                       final_winner_variant: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE manual_reviews SET status=?, final_winner_variant=COALESCE(?, final_winner_variant),
                resolved_at=datetime('now')
            WHERE id=?
        """, (status, final_winner_variant, review_id))


def add_review_manual_edit(review_id: int, edit_path: str,
                            new_variant_entry: dict,
                            new_expires_at: str) -> None:
    """Append a manually-edited FITS as a new variant entry and extend the deadline."""
    import json as _json
    with get_conn() as conn:
        row = conn.execute(
            "SELECT variants_json FROM manual_reviews WHERE id=?", (review_id,)
        ).fetchone()
        if not row:
            return
        try:
            variants = _json.loads(row[0] or "[]")
        except Exception:
            variants = []
        variants.append(new_variant_entry)
        conn.execute("""
            UPDATE manual_reviews SET
                manual_edit_path=?, variants_json=?, expires_at=?
            WHERE id=?
        """, (edit_path, _json.dumps(variants), new_expires_at, review_id))


# --- Experiment stats (for /learning-view) ---

def get_experiment_stats(step: str, object_type: str | None = None) -> dict:
    """
    Per-variant stats including margin analytics for /learning-view.

    Returns {variant_id: {n_runs, win_rate, avg_score, avg_winning_margin,
                           avg_losing_margin, close_race_pct,
                           avg_bg_sigma_ratio, avg_fwhm_delta_pct}}
    """
    import json as _json
    with get_conn() as conn:
        if object_type and object_type != "unknown":
            rows = conn.execute(
                "SELECT * FROM experiment_results WHERE step=? AND object_type=?",
                (step, object_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM experiment_results WHERE step=?", (step,)
            ).fetchall()

    result: dict = {}
    for r in rows:
        d = dict(r)
        vid = d["variant_id"]
        if vid not in result:
            result[vid] = {
                "n_runs": 0, "wins": 0,
                "scores": [], "winning_margins": [], "losing_margins": [],
                "close_races": 0,
                "bg_sigma_ratios": [], "fwhm_delta_pcts": [],
            }
        s = result[vid]
        s["n_runs"] += 1
        if d.get("winner"):
            s["wins"] += 1
        if d.get("overall_score") is not None:
            s["scores"].append(d["overall_score"])
        margin = d.get("winning_margin")
        if margin is not None:
            if d.get("winner"):
                s["winning_margins"].append(margin)
            else:
                s["losing_margins"].append(margin)
            if abs(margin) < 1.0:
                s["close_races"] += 1
        # Parse physics from metrics_json
        if d.get("metrics_json"):
            try:
                m = _json.loads(d["metrics_json"])
                if m.get("bg_sigma_ratio") is not None:
                    s["bg_sigma_ratios"].append(m["bg_sigma_ratio"])
                if m.get("fwhm_delta_pct") is not None:
                    s["fwhm_delta_pcts"].append(m["fwhm_delta_pct"])
            except Exception:
                pass

    import statistics as _stats

    def _avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    def _median(lst):
        return round(_stats.median(lst), 3) if lst else None

    def _stdev(lst):
        return round(_stats.stdev(lst), 3) if len(lst) >= 2 else None

    def _score_spread(lst):
        if not lst:
            return None, None, None, None, None, None
        return (
            _avg(lst),
            _median(lst),
            _stdev(lst),
            round(min(lst), 2),
            round(max(lst), 2),
            round(max(lst) - min(lst), 2),
        )

    out: dict = {}
    for vid, s in result.items():
        n = s["n_runs"]
        mean, median, stdev, lo, hi, spread = _score_spread(s["scores"])
        out[vid] = {
            "n_runs": n,
            "win_rate": round(s["wins"] / n, 3) if n else 0,
            "avg_score": mean,
            "median_score": median,
            "stdev_score": stdev,
            "min_score": lo,
            "max_score": hi,
            "score_spread": spread,
            "avg_winning_margin": _avg(s["winning_margins"]),
            "avg_losing_margin": _avg(s["losing_margins"]),
            "close_race_pct": round(s["close_races"] / n, 3) if n else 0,
            "avg_bg_sigma_ratio": _avg(s["bg_sigma_ratios"]),
            "avg_fwhm_delta_pct": _avg(s["fwhm_delta_pcts"]),
        }
    return out


# ── Agent suggestions ────────────────────────────────────────────────────────

def add_agent_suggestion(
    description: str,
    file_hint: str = "",
    code_snippet: str = "",
    source: str = "agent",
    dedup_key: str | None = None,
) -> int | None:
    """Insert a suggestion. Returns new row id, or None if dedup_key already exists unresolved."""
    with get_conn() as conn:
        if dedup_key:
            exists = conn.execute(
                "SELECT id FROM agent_suggestions WHERE dedup_key=? AND resolved=0",
                (dedup_key,),
            ).fetchone()
            if exists:
                return None
        cur = conn.execute(
            "INSERT INTO agent_suggestions (description, file_hint, code_snippet, source, dedup_key)"
            " VALUES (?,?,?,?,?)",
            (description, file_hint or "", code_snippet or "", source, dedup_key),
        )
        return cur.lastrowid


def get_agent_suggestions(resolved: bool | None = None) -> list[dict]:
    with get_conn() as conn:
        if resolved is None:
            rows = conn.execute(
                "SELECT * FROM agent_suggestions ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_suggestions WHERE resolved=? ORDER BY created_at DESC",
                (1 if resolved else 0,),
            ).fetchall()
    cols = ["id", "created_at", "description", "file_hint", "code_snippet", "resolved",
            "source", "dedup_key"]
    return [dict(zip(cols, r)) for r in rows]


def resolve_agent_suggestion(suggestion_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE agent_suggestions SET resolved=1 WHERE id=?", (suggestion_id,))


# ── Named image crops ────────────────────────────────────────────────────────

def save_image_crop(target: str, filename: str, name: str,
                    x: float, y: float, w: float, h: float,
                    crop_jpeg_path: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO image_crops (target, filename, name, x, y, w, h)
               VALUES (?,?,?,?,?,?,?)""",
            (target, filename, name, x, y, w, h),
        )
        crop_id = cur.lastrowid
        if crop_jpeg_path:
            conn.execute("UPDATE image_crops SET name=name WHERE id=?", (crop_id,))
    return crop_id


def update_image_crop_path(crop_id: int, crop_jpeg_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE image_crops SET name=name WHERE id=?", (crop_id,)
        )
    # Store the path in a side file rather than DB column for now
    # (image_crops table doesn't have a path column — stored implicitly as crops/{id}.jpg)


def get_image_crops(target: str, filename: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if filename:
            rows = conn.execute(
                "SELECT * FROM image_crops WHERE target=? AND filename=? ORDER BY created_at DESC",
                (target, filename),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM image_crops WHERE target=? ORDER BY created_at DESC",
                (target,),
            ).fetchall()
    cols = ["id", "created_at", "target", "filename", "name", "x", "y", "w", "h"]
    return [dict(zip(cols, r)) for r in rows]


def delete_image_crop(crop_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM image_crops WHERE id=?", (crop_id,))


# ── RAG embeddings ────────────────────────────────────────────────────────────

def save_rag_embedding(doc_type: str, source_id: int, target: str,
                       text_snippet: str, embedding_bytes: bytes) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO rag_embeddings
               (doc_type, source_id, target, text_snippet, embedding)
               VALUES (?,?,?,?,?)""",
            (doc_type, source_id, target or "", text_snippet[:500], embedding_bytes),
        )


def get_rag_embeddings() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, doc_type, source_id, target, text_snippet, embedding FROM rag_embeddings"
        ).fetchall()
    return [
        {"id": r[0], "doc_type": r[1], "source_id": r[2],
         "target": r[3], "text_snippet": r[4], "embedding": r[5]}
        for r in rows
    ]


def save_crop_analysis(crop_id: int, scores: dict, aggregate: float,
                        summary: str, concerns: list, physics: dict) -> int:
    import json as _json
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO image_crop_analyses
               (crop_id, scores_json, aggregate_score, summary, concerns_json, physics_json)
               VALUES (?,?,?,?,?,?)""",
            (crop_id, _json.dumps(scores), aggregate, summary,
             _json.dumps(concerns), _json.dumps(physics)),
        )
        return cur.lastrowid


def get_crop_analyses(crop_id: int) -> list[dict]:
    import json as _json
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, scores_json, aggregate_score, summary, concerns_json "
            "FROM image_crop_analyses WHERE crop_id=? ORDER BY created_at DESC",
            (crop_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "created_at": r[1],
            "scores": _json.loads(r[2] or "{}"),
            "aggregate_score": r[3],
            "summary": r[4],
            "concerns": _json.loads(r[5] or "[]"),
        }
        for r in rows
    ]


def get_indexed_source_ids(doc_type: str) -> set:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_id FROM rag_embeddings WHERE doc_type=?", (doc_type,)
        ).fetchall()
    return {r[0] for r in rows}


# ── Chat session helpers ───────────────────────────────────────────────────────

def create_chat_session(title: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO chat_sessions (title) VALUES (?)", (title,))
        return cur.lastrowid


def append_chat_message(session_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.execute(
            "UPDATE chat_sessions SET last_active = datetime('now') WHERE id = ?",
            (session_id,),
        )


def get_chat_history(session_id: int, limit: int = 40) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content, created_at FROM chat_messages
               WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in reversed(rows)]


def update_session_title(session_id: int, title: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET title = ? WHERE id = ? AND title IS NULL",
            (title, session_id),
        )


def list_chat_sessions(limit: int = 15) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, created_at, last_active FROM chat_sessions
               ORDER BY last_active DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "created_at": r[2], "last_active": r[3]} for r in rows]


# ── Planner helpers ────────────────────────────────────────────────────────────

def get_targets_for_planner() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.target, t.ra, t.dec, COALESCE(t.priority, 0) AS priority,
                      COALESCE(t.association, '') AS association,
                      COALESCE(SUM(CASE
                          WHEN l.exclude = 0
                           AND (l.eccentricity     IS NULL OR l.eccentricity     <= 0.66)
                           AND (l.star_count        IS NULL OR l.star_count        >= 20)
                           AND (l.gradient_severity IS NULL OR l.gradient_severity <= 0.5)
                          THEN l.exposure_time ELSE 0 END), 0) AS int_seconds,
                      COALESCE(t.transient, 0) AS transient,
                      COALESCE(t.type, '') AS target_type,
                      CASE WHEN MAX(l.date) IS NOT NULL
                           THEN CAST(JULIANDAY('now') - JULIANDAY(MAX(l.date)) AS REAL)
                           ELSE 999.0 END AS days_since_last_obs
               FROM targets t
               LEFT JOIN light_files l ON l.target = t.target
               WHERE t.inactive = 0
               GROUP BY t.target
               ORDER BY t.target"""
        ).fetchall()
    targets = [
        {
            "target": r[0], "ra": r[1], "dec": r[2],
            "priority": r[3], "association": r[4] or "",
            "int_hours": (r[5] or 0) / 3600.0,
            "transient": int(r[6]),
            "target_type": r[7] or "",
            "days_since_last_obs": float(r[8] if r[8] is not None else 999.0),
        }
        for r in rows
        if not _is_named_star(r[0])
    ]

    # Association-aware integration: if M 81 has 30h and M 82 has 0.2h but they're
    # always co-imaged, M 82 effectively has 30h. Use group max for scoring/scheduling.
    hours_by_name = {t["target"]: t["int_hours"] for t in targets}
    for t in targets:
        if t["association"]:
            assoc_names = [a.strip() for a in t["association"].split(",") if a.strip()]
            group = [t["int_hours"]] + [hours_by_name[n] for n in assoc_names if n in hours_by_name]
            t["group_int_hours"] = max(group)
        else:
            t["group_int_hours"] = t["int_hours"]

    return targets


# ── Planner learning ─────────────────────────────────────────────────────────

def _ensure_learn_tables():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS planner_runs (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                date     TEXT NOT NULL UNIQUE,
                plan_json TEXT NOT NULL,
                evaluated INTEGER DEFAULT 0,
                source    TEXT DEFAULT 'auto',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_learn (
                target                  TEXT PRIMARY KEY,
                plan_appearances        INTEGER DEFAULT 0,
                capture_planned         INTEGER DEFAULT 0,
                capture_unplanned       INTEGER DEFAULT 0,
                skip_count              INTEGER DEFAULT 0,
                pref_score              REAL DEFAULT 0.5,
                user_selected_count     INTEGER DEFAULT 0,
                user_replan_appearances INTEGER DEFAULT 0,
                updated_at              TEXT
            )
        """)
        # Migrate existing tables — ignore errors if columns already exist
        for _stmt in [
            "ALTER TABLE target_learn ADD COLUMN user_selected_count INTEGER DEFAULT 0",
            "ALTER TABLE target_learn ADD COLUMN user_replan_appearances INTEGER DEFAULT 0",
            "ALTER TABLE planner_runs ADD COLUMN source TEXT DEFAULT 'auto'",
        ]:
            try:
                conn.execute(_stmt)
            except Exception:
                pass


def save_planner_run(date: str, plan_slots: list, source: str = "auto") -> None:
    """Persist tonight's plan. plan_slots is list of {target, start_hhmm, end_hhmm} dicts."""
    import json as _j
    _ensure_learn_tables()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO planner_runs (date, plan_json, source) VALUES (?, ?, ?)",
            (date, _j.dumps(plan_slots), source),
        )


def _parse_plan_slots(raw: list) -> list[dict]:
    """Normalise stored plan — handles both old (list of strings) and new (list of dicts) format."""
    if not raw:
        return []
    if isinstance(raw[0], str):
        return [{"target": t, "start_hhmm": None, "end_hhmm": None} for t in raw]
    return raw


def get_latest_planner_run() -> tuple[str, list[dict]] | None:
    """Return (date, slots) for the most recent plan, or None."""
    import json as _j
    _ensure_learn_tables()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, plan_json FROM planner_runs ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row[0], _parse_plan_slots(_j.loads(row[1]))


def get_unevaluated_planner_run() -> tuple[str, list[dict]] | None:
    """Return the most recent plan that hasn't been evaluated yet."""
    import json as _j
    _ensure_learn_tables()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, plan_json FROM planner_runs WHERE evaluated=0 ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row[0], _parse_plan_slots(_j.loads(row[1]))


def mark_planner_run_evaluated(date: str) -> None:
    _ensure_learn_tables()
    with get_conn() as conn:
        conn.execute("UPDATE planner_runs SET evaluated=1 WHERE date=?", (date,))


def get_captures_for_date(date: str) -> list[str]:
    """Return distinct targets captured on a given YYYY-MM-DD date (UTC DATE-OBS)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT target FROM light_files WHERE date LIKE ? AND (exclude IS NULL OR exclude=0)",
            (f"{date}%",),
        ).fetchall()
    return [r[0] for r in rows]


def get_capture_timestamps_for_night(plan_date: str) -> dict[str, tuple[str, str]]:
    """Return {target: (first_utc, last_utc)} for captures on the given UTC plan date."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT target, MIN(date), MAX(date) FROM light_files
               WHERE date LIKE ? AND (exclude IS NULL OR exclude=0) AND date != ''
               GROUP BY target""",
            (f"{plan_date}%",),
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _recompute_pref(conn, target: str) -> float:
    import math
    row = conn.execute(
        """SELECT plan_appearances, capture_planned, capture_unplanned, skip_count,
                  user_selected_count, user_replan_appearances
           FROM target_learn WHERE target=?""",
        (target,),
    ).fetchone()
    if not row:
        return 0.5
    pa, cp, cu, sk, usc, ura = row
    if pa + cu + ura == 0:
        return 0.5
    capture_rate   = cp / pa if pa > 0 else 0.5
    unplanned_bonus = math.log1p(cu) / math.log1p(10)  # log-scale, saturates ~10 captures
    skip_rate      = sk / pa if pa > 0 else 0.0
    # selection_rate: fraction of replans where user explicitly checked this target.
    # 0.5 = neutral (no replan data yet); <0.5 = consistently skipped; >0.5 = consistently chosen.
    selection_rate = usc / ura if ura > 0 else 0.5
    score = (
        0.30 * capture_rate
        + 0.30 * unplanned_bonus
        + 0.15 * max(0.0, 1.0 - skip_rate)
        + 0.25 * selection_rate
    )
    return round(max(0.0, min(1.0, score)), 4)


def update_target_learn(
    target: str,
    plan_delta: int = 0,
    capture_planned_delta: int = 0,
    capture_unplanned_delta: int = 0,
    skip_delta: int = 0,
) -> float:
    """Update learning counters and recompute pref_score. Returns new pref_score."""
    _ensure_learn_tables()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO target_learn (target, plan_appearances, capture_planned, capture_unplanned, skip_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(target) DO UPDATE SET
                 plan_appearances  = plan_appearances  + excluded.plan_appearances,
                 capture_planned   = capture_planned   + excluded.capture_planned,
                 capture_unplanned = capture_unplanned + excluded.capture_unplanned,
                 skip_count        = skip_count        + excluded.skip_count,
                 updated_at        = datetime('now')""",
            (target, plan_delta, capture_planned_delta, capture_unplanned_delta, skip_delta),
        )
        score = _recompute_pref(conn, target)
        conn.execute(
            "UPDATE target_learn SET pref_score=? WHERE target=?", (score, target)
        )
    return score


def record_replan_selection(all_ranked: list[str], selected: list[str]) -> None:
    """Record a manual Replan event.

    Every target in all_ranked gains one user_replan_appearance.
    Targets in selected also gain one user_selected_count.
    This drives the selection_rate component of pref_score.
    """
    if not all_ranked:
        return
    _ensure_learn_tables()
    selected_set = set(selected)
    with get_conn() as conn:
        for target in all_ranked:
            chosen = 1 if target in selected_set else 0
            conn.execute(
                """INSERT INTO target_learn
                       (target, user_replan_appearances, user_selected_count, updated_at)
                   VALUES (?, 1, ?, datetime('now'))
                   ON CONFLICT(target) DO UPDATE SET
                     user_replan_appearances = user_replan_appearances + 1,
                     user_selected_count     = user_selected_count + excluded.user_selected_count,
                     updated_at              = datetime('now')""",
                (target, chosen),
            )
            score = _recompute_pref(conn, target)
            conn.execute("UPDATE target_learn SET pref_score=? WHERE target=?", (score, target))


def get_target_learn_scores() -> dict[str, float]:
    """Return {target: pref_score} for all targets with learning data."""
    _ensure_learn_tables()
    with get_conn() as conn:
        rows = conn.execute("SELECT target, pref_score FROM target_learn").fetchall()
    return {r[0]: r[1] for r in rows}


def seed_learn_from_history() -> int:
    """Bootstrap target_learn from historical light_files (sessions counted as unplanned).
    Returns number of targets seeded. Safe to call multiple times — skips existing rows."""
    _ensure_learn_tables()
    with get_conn() as conn:
        already = conn.execute("SELECT COUNT(*) FROM target_learn").fetchone()[0]
        if already > 0:
            return 0  # already seeded

        # Count distinct observation nights per target
        rows = conn.execute(
            """SELECT target, COUNT(DISTINCT substr(date, 1, 10)) AS sessions
               FROM light_files
               WHERE (exclude IS NULL OR exclude=0) AND date != ''
               GROUP BY target"""
        ).fetchall()

        count = 0
        for target, sessions in rows:
            conn.execute(
                """INSERT OR IGNORE INTO target_learn
                   (target, capture_unplanned, pref_score, updated_at)
                   VALUES (?, ?, 0.5, datetime('now'))""",
                (target, sessions),
            )
            count += 1
        # Recompute scores using the standard formula now that rows are inserted
        all_targets = conn.execute("SELECT target FROM target_learn").fetchall()
        for (t,) in all_targets:
            score = _recompute_pref(conn, t)
            conn.execute("UPDATE target_learn SET pref_score=? WHERE target=?", (score, t))
    return count


# ── Calibration frame helpers ─────────────────────────────────────────────────

def upsert_calibration_frame(
    frame_type: str,
    file_path: str,
    date: str,
    camera: str = "seestar_s50",
    filter: str = "none",
    gain: int | None = None,
    offset: int | None = None,
    temp_c: float | None = None,
    exposure_time: float | None = None,
    adu_median: float | None = None,
    valid: int = 1,
) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO calibration_frames
               (frame_type, camera, filter, gain, offset, temp_c, exposure_time,
                date, file_path, adu_median, valid)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(file_path) DO UPDATE SET
                 adu_median=excluded.adu_median, valid=excluded.valid""",
            (frame_type, camera, filter, gain, offset, temp_c, exposure_time,
             date, file_path, adu_median, valid),
        )
        row = conn.execute(
            "SELECT id FROM calibration_frames WHERE file_path=?", (file_path,)
        ).fetchone()
    return row[0] if row else -1


def get_calibration_master(
    frame_type: str,
    camera: str = "seestar_s50",
    filter: str = "none",
    gain: int | None = None,
    temp_c: float | None = None,
    exposure_time: float | None = None,
    temp_tolerance: float = 5.0,
) -> str | None:
    """Return file_path of the best matching calibration master, or None."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT file_path, temp_c FROM calibration_frames
               WHERE frame_type=? AND camera=? AND is_master=1 AND valid=1
                 AND (gain IS NULL OR gain=? OR ? IS NULL)
                 AND (filter=? OR ?='none' OR frame_type='dark')
               ORDER BY date DESC""",
            (frame_type, camera, gain, gain, filter, filter),
        ).fetchall()
    if not rows:
        return None
    if temp_c is None:
        return rows[0][0]
    # Pick nearest temperature within tolerance
    best = min(rows, key=lambda r: abs((r[1] or 0) - temp_c))
    if abs((best[1] or 0) - temp_c) > temp_tolerance:
        return None
    return best[0]


def get_calibration_frames_for_master(
    frame_type: str,
    camera: str = "seestar_s50",
    filter: str = "none",
    gain: int | None = None,
    temp_c: float | None = None,
    exposure_time: float | None = None,
    date_from: str | None = None,
    temp_tolerance: float = 5.0,
) -> list[str]:
    """Return file paths of individual (non-master) calibration frames matching params."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT file_path, temp_c FROM calibration_frames
               WHERE frame_type=? AND camera=? AND is_master=0 AND valid=1
                 AND (gain IS NULL OR gain=? OR ? IS NULL)
                 AND (filter=? OR ?='none' OR frame_type='dark')
                 AND (exposure_time=? OR ? IS NULL)
                 AND (date >= ? OR ? IS NULL)
               ORDER BY date""",
            (frame_type, camera, gain, gain, filter, filter,
             exposure_time, exposure_time, date_from, date_from),
        ).fetchall()
    if temp_c is None:
        return [r[0] for r in rows]
    return [r[0] for r in rows if abs((r[1] or 0) - temp_c) <= temp_tolerance]


def mark_calibration_master(frame_id: int, master_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE calibration_frames SET is_master=1, file_path=? WHERE id=?",
            (master_path, frame_id),
        )


def flag_calibration_frame_invalid(frame_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE calibration_frames SET valid=0 WHERE id=?", (frame_id,)
        )


def get_unmastered_calibration_groups(date_from: str) -> list[dict]:
    """Return distinct (frame_type, camera, filter, gain, temp_c, exposure_time) groups
    that have valid sub-frames but no master yet, since date_from."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT frame_type, camera, filter, gain,
                      ROUND(AVG(temp_c), 0) AS temp_c,
                      exposure_time, COUNT(*) AS frame_count
               FROM calibration_frames
               WHERE is_master=0 AND valid=1 AND date >= ?
               GROUP BY frame_type, camera, filter, gain,
                        ROUND(temp_c / 5.0) * 5, exposure_time
               HAVING frame_count >= 5""",
            (date_from,),
        ).fetchall()
    return [
        {
            "frame_type": r[0], "camera": r[1], "filter": r[2],
            "gain": r[3], "temp_c": r[4], "exposure_time": r[5],
            "frame_count": r[6],
        }
        for r in rows
    ]


def update_target_coords(target: str, ra: float, dec: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE targets SET ra=?, dec=? WHERE target=?", (ra, dec, target)
        )


# ---------------------------------------------------------------------------
# Manual processing runs (Henry's hand-processed PI exports, auto-scanned)
# ---------------------------------------------------------------------------

def manual_run_paths() -> set[str]:
    """All file_paths already captured as manual processing runs (for dedup)."""
    with get_conn() as conn:
        return {r[0] for r in conn.execute(
            "SELECT file_path FROM manual_processing_runs").fetchall()}


def insert_manual_run(target: str, file_path: str, filename: str,
                      source_type: str, n_steps: int, flow_json: str,
                      summary: str | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO manual_processing_runs
                   (target, file_path, filename, source_type, n_steps, flow_json, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (target, file_path, filename, source_type, n_steps, flow_json, summary),
        )
        return cur.lastrowid


def list_manual_runs(target: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if target:
            rows = conn.execute(
                "SELECT * FROM manual_processing_runs WHERE target=? ORDER BY created_at DESC",
                (target,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manual_processing_runs ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_manual_run(run_id: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM manual_processing_runs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def delete_manual_run(run_id: int) -> dict | None:
    """Delete a flagged manual run; return the deleted row (for preview cleanup)."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM manual_processing_runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            return None
        conn.execute("DELETE FROM manual_processing_runs WHERE id=?", (run_id,))
    return dict(r)


def list_ungraded_manual_runs(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM manual_processing_runs
               WHERE claude_score IS NULL ORDER BY created_at ASC LIMIT ?""",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_manual_run_preview(run_id: int, preview_jpg: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE manual_processing_runs SET preview_jpg=? WHERE id=?",
            (preview_jpg, run_id))


def set_manual_run_grade(run_id: int, score: float | None, claude_json: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE manual_processing_runs
               SET claude_score=?, claude_json=?, graded_at=datetime('now')
               WHERE id=?""",
            (score, claude_json, run_id))


def auto_processed_paths() -> set[str]:
    """file_paths the auto pipeline tagged as its own (is_auto=1) — excluded
    from the manual-processing candidate review queue."""
    with get_conn() as conn:
        return {r[0] for r in conn.execute(
            "SELECT file_path FROM processed_files WHERE is_auto=1").fetchall()}


def mark_folder_reviewed(target: str, status: str = "reviewed") -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO manual_folder_reviews (target, status, reviewed_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(target) DO UPDATE SET status=excluded.status,
                   reviewed_at=excluded.reviewed_at""",
            (target, status))


def reviewed_folder_status() -> dict[str, str]:
    """target -> review status for every folder Henry has acted on."""
    with get_conn() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT target, status FROM manual_folder_reviews").fetchall()}

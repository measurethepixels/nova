"""Blind same-run Experiment Mode pairwise judgments, separate from grading."""

import hashlib
import html
import itertools
import json
import random
import uuid
from pathlib import Path

from nas_server.grading_corpus import ADJUDICATION_REASON_CODES
from nas_server.grading_adjudication_page import REASON_CAPTIONS

REASONS = tuple(code for code in ADJUDICATION_REASON_CODES
                if code != "persuaded_by_other_label")
DIMENSIONS = ("stretch", "color", "dynamic-range", "other")


def migrate(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS grading_pairwise_presentations (
      presentation_id TEXT PRIMARY KEY,
      experiment_run_id TEXT NOT NULL REFERENCES experiment_runs(experiment_run_id),
      left_artifact_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
      right_artifact_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
      left_preview_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
      right_preview_id TEXT NOT NULL REFERENCES experiment_run_artifacts(run_artifact_id),
      left_sha256 TEXT NOT NULL, right_sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT(datetime('now')),
      CHECK(left_artifact_id != right_artifact_id));
    CREATE TABLE IF NOT EXISTS grading_pairwise_judgments (
      judgment_id TEXT PRIMARY KEY,
      presentation_id TEXT NOT NULL UNIQUE REFERENCES grading_pairwise_presentations(presentation_id),
      experiment_run_id TEXT NOT NULL,
      left_artifact_id TEXT NOT NULL, right_artifact_id TEXT NOT NULL,
      left_sha256 TEXT NOT NULL, right_sha256 TEXT NOT NULL,
      answer TEXT NOT NULL CHECK(answer IN ('A','B','equivalent')),
      reason_codes_json TEXT NOT NULL,
      driving_dimension TEXT CHECK(driving_dimension IN ('stretch','color','dynamic-range','other')),
      rationale TEXT NOT NULL DEFAULT '',
      labeller TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT(datetime('now')));
    CREATE INDEX IF NOT EXISTS idx_pairwise_judgments_run
      ON grading_pairwise_judgments(experiment_run_id);
    """)
    for table in ("grading_pairwise_presentations", "grading_pairwise_judgments"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""")


def _candidates(conn):
    """Only finalized eligible outputs with their registered standard preview."""
    return conn.execute("""SELECT c.experiment_run_id,c.run_artifact_id,c.declared_variant_id,c.content_digest,
          p.run_artifact_id AS preview_id,p.final_path,p.content_digest AS preview_digest,
          CASE WHEN o.operational_selection_artifact_id IS NULL THEN 0 ELSE 1 END AS has_winner,
          r.created_at AS run_created_at
        FROM experiment_run_artifacts c
        JOIN experiment_candidate_eligibility e
          ON e.experiment_run_id=c.experiment_run_id
         AND e.declared_variant_id=c.declared_variant_id AND e.eligible=1
        JOIN experiment_run_artifacts p
          ON p.source_artifact_id=c.run_artifact_id
         AND p.operation_key=c.run_artifact_id || ':preview'
         AND p.artifact_role='preview' AND p.finalization_status='finalized'
         AND p.experiment_run_id=c.experiment_run_id
         AND p.declared_variant_id=c.declared_variant_id
        JOIN experiment_runs r ON r.experiment_run_id=c.experiment_run_id
        LEFT JOIN experiment_run_outcomes o ON o.experiment_run_id=c.experiment_run_id
        WHERE c.artifact_role='candidate_output' AND c.finalization_status='finalized'
          AND c.content_digest IS NOT NULL AND p.content_digest IS NOT NULL
        ORDER BY c.experiment_run_id,c.run_artifact_id""").fetchall()


def pairs(conn):
    by_run = {}
    for row in _candidates(conn):
        by_run.setdefault(row["experiment_run_id"], []).append(row)
    judged = {(row["experiment_run_id"], frozenset((row["left_artifact_id"],
              row["right_artifact_id"]))): row["n"] for row in conn.execute("""
        SELECT experiment_run_id,left_artifact_id,right_artifact_id,COUNT(*) AS n
        FROM grading_pairwise_judgments
        GROUP BY experiment_run_id,left_artifact_id,right_artifact_id""")}
    run_counts = {row["experiment_run_id"]: row["n"] for row in conn.execute("""
        SELECT experiment_run_id,COUNT(*) AS n FROM grading_pairwise_judgments
        GROUP BY experiment_run_id""")}
    result = []
    for run_id, candidates in by_run.items():
        for a, b in itertools.combinations(candidates, 2):
            if (a["declared_variant_id"] == b["declared_variant_id"]
                    or a["content_digest"] == b["content_digest"]
                    or a["preview_digest"] == b["preview_digest"]):
                continue
            count = judged.get((run_id, frozenset((a["run_artifact_id"], b["run_artifact_id"]))), 0)
            result.append((run_counts.get(run_id, 0), -a["has_winner"], count,
                           a["run_created_at"], run_id,
                           a["run_artifact_id"], b["run_artifact_id"], a, b))
    result.sort(key=lambda entry: entry[:7])
    return [(entry[-2], entry[-1]) for entry in result]


def _jpeg(row):
    path = Path(row["final_path"])
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError("registered preview is not JPEG")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("registered preview unavailable") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if row["preview_digest"] != "sha256:" + digest:
        raise ValueError("registered preview hash mismatch")
    return raw, digest


def prepare(conn):
    """Persist random A/B order and exact display hashes before showing images."""
    for first, second in pairs(conn):
        try:
            _, first_hash = _jpeg(first)
            _, second_hash = _jpeg(second)
        except ValueError:
            continue
        if first_hash == second_hash:
            continue
        left, right = (first, second) if random.SystemRandom().randrange(2) else (second, first)
        hashes = {first["run_artifact_id"]: first_hash, second["run_artifact_id"]: second_hash}
        pid = "pair_" + uuid.uuid4().hex
        conn.execute("""INSERT INTO grading_pairwise_presentations
          (presentation_id,experiment_run_id,left_artifact_id,right_artifact_id,
           left_preview_id,right_preview_id,left_sha256,right_sha256)
          VALUES (?,?,?,?,?,?,?,?)""", (pid, left["experiment_run_id"],
          left["run_artifact_id"], right["run_artifact_id"], left["preview_id"],
          right["preview_id"], hashes[left["run_artifact_id"]], hashes[right["run_artifact_id"]]))
        return pid
    return None


def image_bytes(conn, presentation_id, side):
    if side not in ("A", "B"):
        raise ValueError("unknown side")
    prefix = "left" if side == "A" else "right"
    row = conn.execute(f"""SELECT p.final_path,p.content_digest AS preview_digest,
        g.{prefix}_sha256 AS expected FROM grading_pairwise_presentations g
        JOIN experiment_run_artifacts p ON p.run_artifact_id=g.{prefix}_preview_id
        WHERE g.presentation_id=?""", (presentation_id,)).fetchone()
    if row is None:
        raise ValueError("unknown presentation")
    raw, digest = _jpeg(row)
    if digest != row["expected"]:
        raise ValueError("presentation changed")
    return raw


def summary(conn):
    row = conn.execute("""SELECT COUNT(*) AS total,
        COALESCE(SUM(answer='equivalent'),0) AS equivalent
        FROM grading_pairwise_judgments""").fetchone()
    return {"total": row["total"], "equivalent": row["equivalent"],
            "equivalent_rate": row["equivalent"] / row["total"] if row["total"] else 0.0}


def save_answer(presentation_id, fields):
    answers = fields.get("answer", [])
    reasons = fields.get("reason_codes", [])
    dimensions = fields.get("driving_dimension", [])
    rationales = fields.get("rationale", [])
    if len(answers) != 1 or answers[0] not in ("A", "B", "equivalent"):
        raise ValueError("invalid answer")
    if not reasons or len(reasons) != len(set(reasons)) or not set(reasons).issubset(REASONS):
        raise ValueError("at least one valid reason required")
    if len(dimensions) > 1 or (dimensions and dimensions[0] not in DIMENSIONS and dimensions[0] != ""):
        raise ValueError("invalid dimension")
    if len(rationales) > 1 or len(rationales[0] if rationales else "") > 4000:
        raise ValueError("invalid rationale")
    from nas_server.database import get_conn
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        presentation = conn.execute("SELECT * FROM grading_pairwise_presentations WHERE presentation_id=?",
                                    (presentation_id,)).fetchone()
        if presentation is None:
            raise ValueError("unknown presentation")
        prior = conn.execute("SELECT * FROM grading_pairwise_judgments WHERE presentation_id=?",
                             (presentation_id,)).fetchone()
        if prior:
            if (prior["answer"], sorted(json.loads(prior["reason_codes_json"])),
                    prior["driving_dimension"], prior["rationale"]) == (
                    answers[0], sorted(reasons), (dimensions[0] or None) if dimensions else None,
                    (rationales[0] if rationales else "").strip()):
                return
            raise ValueError("presentation already answered")
        eligible_pairs = pairs(conn)
        pair = frozenset((presentation["left_artifact_id"], presentation["right_artifact_id"]))
        if not any(frozenset((a["run_artifact_id"], b["run_artifact_id"])) == pair
                   and a["experiment_run_id"] == presentation["experiment_run_id"]
                   for a, b in eligible_pairs):
            raise ValueError("pair is no longer eligible")
        image_bytes(conn, presentation_id, "A")
        image_bytes(conn, presentation_id, "B")
        conn.execute("""INSERT INTO grading_pairwise_judgments
          (judgment_id,presentation_id,experiment_run_id,left_artifact_id,right_artifact_id,
           left_sha256,right_sha256,answer,reason_codes_json,driving_dimension,rationale,labeller)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", ("judgment_" + uuid.uuid4().hex,
          presentation_id, presentation["experiment_run_id"], presentation["left_artifact_id"],
          presentation["right_artifact_id"], presentation["left_sha256"],
          presentation["right_sha256"], answers[0], json.dumps(sorted(reasons)),
          (dimensions[0] or None) if dimensions else None,
          (rationales[0] if rationales else "").strip(), "henry"))


def render_page(presentation_id, stats):
    if presentation_id is None:
        content = "<p>No eligible same-run pair with available JPEG previews.</p>"
    else:
        reason_controls = "".join(
            f'<label><input type="checkbox" name="reason_codes" value="{html.escape(code)}"> '
            f'{html.escape(REASON_CAPTIONS[code])}</label>' for code in REASONS)
        dimensions = "".join(f'<option value="{html.escape(d)}">{html.escape(d)}</option>'
                             for d in DIMENSIONS)
        content = (f'<div class="pair"><figure><img alt="Candidate A" src="/grading/pairwise/image/{presentation_id}/A"><figcaption>A</figcaption></figure>'
                   f'<figure><img alt="Candidate B" src="/grading/pairwise/image/{presentation_id}/B"><figcaption>B</figcaption></figure></div>'
                   '<form method="post" action="/grading/pairwise">'
                   f'<input type="hidden" name="presentation_id" value="{presentation_id}">'
                   '<fieldset><legend>Which presentation do you prefer?</legend>'
                   '<label><input type="radio" name="answer" value="A" required>A</label>'
                   '<label><input type="radio" name="answer" value="B" required>B</label>'
                   '<label><input type="radio" name="answer" value="equivalent" required>Equivalent</label></fieldset>'
                   f'<fieldset><legend>Why? Select at least one</legend>{reason_controls}</fieldset>'
                   f'<label>Driving dimension (optional) <select name="driving_dimension"><option value="">Unspecified</option>{dimensions}</select></label>'
                   '<label>Notes (optional) <textarea name="rationale" maxlength="4000"></textarea></label>'
                   '<button type="submit">Save judgment</button></form>')
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind pairwise comparison</title>
<style>body{margin:0;background:#808080;color:#111;font:16px/1.45 system-ui,sans-serif}main{max-width:1300px;margin:auto;padding:1rem}
.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}.pair img{display:block;max-width:100%;max-height:72vh;margin:auto}
figure{margin:0;text-align:center;background:#666}figcaption{font-size:1.5rem;background:#aaa}fieldset{margin:1rem 0}label{display:inline-block;margin:.35rem}
textarea{display:block;width:min(100%,40rem);min-height:4rem}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style></head><body><main>
<h1>Blind pairwise comparison</h1>''' +
            f'<p>Judgments: {stats["total"]} · Equivalent: {stats["equivalent"]} ({stats["equivalent_rate"]:.1%})</p>' +
            content + '''</main><script>const form=document.querySelector('form');if(form)form.addEventListener('submit',e=>{
if(!form.querySelector('input[name="reason_codes"]:checked')){e.preventDefault();alert('Choose at least one reason.');return;}
form.querySelector('button[type="submit"]').disabled=true;});</script></body></html>''')


def install_routes(app):
    from fastapi import HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    from urllib.parse import parse_qs
    from nas_server.database import get_conn

    @app.get("/grading/pairwise", response_class=HTMLResponse)
    def pairwise_page():
        with get_conn() as conn:
            pid = prepare(conn)
            stats = summary(conn)
        return HTMLResponse(render_page(pid, stats), headers={"Cache-Control": "no-store"})

    @app.get("/grading/pairwise/image/{presentation_id}/{side}")
    def pairwise_image(presentation_id: str, side: str):
        try:
            with get_conn() as conn:
                raw = image_bytes(conn, presentation_id, side)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Image unavailable") from exc
        return Response(raw, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/grading/pairwise/summary")
    def pairwise_summary():
        with get_conn() as conn:
            counts = summary(conn)
        return JSONResponse(counts, headers={"Cache-Control": "no-store"})

    @app.post("/grading/pairwise")
    async def pairwise_submit(request: Request):
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(status_code=413, detail="Form too large")
        try:
            fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            ids = fields.get("presentation_id", [])
            if len(ids) != 1:
                raise ValueError("invalid presentation")
            save_answer(ids[0], fields)
        except (UnicodeError, ValueError) as exc:
            if str(exc) == 'presentation already answered':
                return HTMLResponse(
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Judgment already saved</title></head><body><main>'
                    '<h1>This pair was already judged.</h1>'
                    '<p>Your saved judgment has not been changed.</p>'
                    '<a href="/grading/pairwise">Continue →</a></main></body></html>',
                    status_code=400, headers={"Cache-Control": "no-store"})
            raise HTTPException(status_code=400, detail="Invalid or stale pairwise submission") from exc
        return RedirectResponse("/grading/pairwise", status_code=303)

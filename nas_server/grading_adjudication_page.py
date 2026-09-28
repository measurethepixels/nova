"""Append-only adjudication page for labeling-epoch/1 disagreements."""
# No `from __future__ import annotations` here: FastAPI resolves route annotations
# against module globals, and `Request` is imported inside install_routes(). With
# postponed annotations it could not be resolved, so POST /grading/adjudicate
# treated `request` as a missing query parameter and returned 422 (2026-09-27).

import hashlib
import html
import json

from nas_server import grading_corpus
from nas_server.grading_labeling_page import (
    EPOCH, REVISION, CONFIDENCES, RUBRICS, BLIND_TO, _rubric_html, presentation_bytes,
)


def queue(conn) -> tuple[list[dict], int]:
    rows = conn.execute("""SELECT d.disagreement_id, a.anchor_id, a.artifact_sha256,
        a.capture_context_json, l1.label_id AS first_id, l1.labeler AS first_labeler,
        l1.status AS first_status, l1.ordinal AS first_ordinal,
        l1.confidence AS first_confidence, l1.defects_json AS first_defects,
        l2.label_id AS second_id, l2.labeler AS second_labeler,
        l2.status AS second_status, l2.ordinal AS second_ordinal,
        l2.confidence AS second_confidence, l2.defects_json AS second_defects,
        l1.dimension
        FROM grading_disagreements d
        JOIN grading_labels l1 ON l1.label_id=d.first_label_id
        JOIN grading_labels l2 ON l2.label_id=d.second_label_id
        JOIN grading_anchors a ON a.anchor_id=l1.anchor_id
        JOIN grading_corpus_imports i ON i.import_id=a.import_id
        WHERE i.revision=? AND l1.prompt_schema_version=? AND l2.prompt_schema_version=?
          AND l1.anchor_id=l2.anchor_id AND l1.dimension=l2.dimension
          AND l1.label_kind='independent_label' AND l2.label_kind='independent_label'
          AND ((l1.labeler='henry' AND l2.labeler='gpt-vision') OR
               (l2.labeler='henry' AND l1.labeler='gpt-vision'))
        ORDER BY abs(l1.ordinal-l2.ordinal) DESC,a.anchor_id,l1.dimension,d.disagreement_id""",
        (REVISION, EPOCH, EPOCH)).fetchall()
    resolved = {r[0] for r in conn.execute(
        "SELECT disagreement_id FROM grading_adjudications").fetchall()}
    result = []
    for row in rows:
        item = dict(row)
        item['pass_a'] = _label(item, 'first' if item['first_labeler'] == 'henry' else 'second')
        item['pass_b'] = _label(item, 'first' if item['first_labeler'] == 'gpt-vision' else 'second')
        if item['disagreement_id'] not in resolved:
            result.append(item)
    return result, len(rows) - len(result)


def _label(row: dict, prefix: str) -> dict:
    return {key: json.loads(row[f'{prefix}_{key}']) if key == 'defects' else row[f'{prefix}_{key}']
            for key in ('status', 'ordinal', 'confidence', 'defects')}


def measurements(conn, item: dict) -> list[dict]:
    return [dict(row) for row in conn.execute("""SELECT metric,value_json,units,population,integrity
        FROM quality_measurements WHERE artifact_sha256=? ORDER BY metric,measurement_id""",
        (item['artifact_sha256'],)).fetchall()]


def _label_html(title: str, label: dict) -> str:
    tags = ', '.join(label['defects']) or 'none'
    # "Use Pass X" only pre-fills the form (Henry, 2026-09-27): the adjudicator
    # still submits explicitly and still writes the required rationale.
    data = html.escape(json.dumps({'status': label['status'], 'confidence': label['confidence'],
                                   'defects': label['defects']}), quote=True)
    return (f'<section><h3>{title}</h3><p>Status: {html.escape(label["status"])}; '
            f'ordinal: {label["ordinal"]:g}; confidence: {label["confidence"]:g}; '
            f'defect tags: {html.escape(tags)}</p>'
            f'<button type="button" class="use-label" data-pass="{title}" data-label="{data}">'
            f'Use {title}</button></section>')


def render_page(item: dict | None, complete: int, total: int, facts: list[dict] = ()) -> str:
    if item is None:
        content = '<p>All disagreements adjudicated.</p>'
    else:
        dimension = item['dimension']
        status_controls = ''.join(f'<label><input type="radio" name="status" value="{s}" required> {s}</label>'
                                  for s in grading_corpus.STATUSES)
        confidence_controls = ''.join(f'<label><input type="radio" name="confidence" value="{v}" required> {v:g}</label>'
                                      for v in CONFIDENCES)
        defect_controls = ''.join(f'<label><input type="checkbox" name="defects" value="{html.escape(tag)}"> {html.escape(tag)}</label>'
                                  for tag in RUBRICS[dimension]['defects'])
        fact_html = ('<ul>' + ''.join('<li>' + html.escape(f'{m["metric"]}: {m["value_json"]} {m["units"]} '
                    f'({m["population"]}, {m["integrity"]})') + '</li>' for m in facts) + '</ul>') if facts else '<p>no measured facts recorded</p>'
        content = (f'<div class="viewer"><img alt="Image to adjudicate" src="/grading/adjudicate/image/{html.escape(item["anchor_id"])}"></div>'
                   '<button type="button" id="zoom">1:1 view</button>'
                   f'<h2>Dimension: {html.escape(dimension)}</h2><div class="labels">'
                   + _label_html('Pass A', item['pass_a']) + _label_html('Pass B', item['pass_b']) + '</div>'
                   + _rubric_html(dimension) + '<details><summary>Measured facts</summary>' + fact_html + '</details>'
                   '<form method="post" action="/grading/adjudicate">'
                   f'<input type="hidden" name="disagreement_id" value="{html.escape(item["disagreement_id"])}">'
                   f'<p>Final status</p><div class="choices">{status_controls}</div>'
                   f'<p>Confidence</p><div class="choices">{confidence_controls}</div>'
                   f'<p>Defect tags</p><div class="choices">{defect_controls}</div>'
                   '<label>Rationale <textarea name="rationale" required></textarea></label>'
                   '<button type="submit">Save and next disagreement</button></form>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Grading adjudication</title><style>
html,body{{margin:0;background:#808080;color:#111;font:16px/1.45 system-ui,sans-serif}}
main{{max-width:1000px;margin:auto;padding:1rem}}.viewer{{height:min(76vh,900px);overflow:auto;display:flex;align-items:center;justify-content:center}}
.viewer img{{max-width:100%;max-height:100%;object-fit:contain}}.viewer.actual{{display:block}}.viewer.actual img{{max-width:none;max-height:none;width:auto;height:auto}}
.labels,.choices{{display:flex;flex-wrap:wrap;gap:1rem}}.labels section{{background:#aaa;padding:.5rem;flex:1}}
details{{background:#bbb;padding:.8rem;margin:1rem 0}}textarea{{display:block;width:100%;max-width:40rem}}
button{{font:inherit;padding:.5rem .8rem}}@media(max-width:600px){{.viewer{{height:60vh}}}}
</style></head><body><main><h1>Grading adjudication</h1><p>{complete} / {total} disagreements adjudicated</p>{content}</main>
<script>document.querySelectorAll('.use-label').forEach(b=>b.onclick=()=>{{
 const l=JSON.parse(b.dataset.label);const f=document.querySelector('form');
 f.querySelectorAll('input[name=status]').forEach(r=>r.checked=(r.value===l.status));
 f.querySelectorAll('input[name=confidence]').forEach(r=>r.checked=(parseFloat(r.value)===parseFloat(l.confidence)));
 f.querySelectorAll('input[name=defects]').forEach(c=>c.checked=l.defects.includes(c.value));
 // Never write into the rationale (ChatGPT BLOCK on #879): a prefilled value would
 // satisfy `required` without Henry writing a reason. Placeholder text is not a value.
 const t=f.querySelector('textarea[name=rationale]');
 t.placeholder='Why '+b.dataset.pass+'? (required)';t.focus();
}});
const button=document.getElementById('zoom');if(button)button.onclick=()=>{{
 const viewer=document.querySelector('.viewer');viewer.classList.toggle('actual');
 button.textContent=viewer.classList.contains('actual')?'Fit to window':'1:1 view';
}};</script></body></html>'''


def save_answer(disagreement_id: str, fields: dict[str, list[str]]) -> None:
    def one(name: str) -> str:
        values = fields.get(name, [])
        if len(values) != 1:
            raise ValueError(f'one {name} required')
        return values[0]
    status = one('status')
    if status not in grading_corpus.STATUSES:
        raise ValueError('invalid status')
    try:
        confidence = float(one('confidence'))
    except ValueError as exc:
        raise ValueError('invalid confidence') from exc
    if confidence not in CONFIDENCES:
        raise ValueError('invalid confidence')
    rationale = one('rationale').strip()
    if not rationale:
        raise ValueError('rationale required')
    from nas_server.database import get_conn
    with get_conn() as conn:
        items, _ = queue(conn)
        item = next((x for x in items if x['disagreement_id'] == disagreement_id), None)
        prior = conn.execute("""SELECT l.*,j.rationale FROM grading_adjudications j
            JOIN grading_labels l ON l.label_id=j.label_id WHERE j.disagreement_id=?""",
            (disagreement_id,)).fetchone()
        if item is None and prior is None:
            raise ValueError('unknown disagreement')
        dimension = item['dimension'] if item else prior['dimension']
        defects = fields.get('defects', [])
        if len(defects) != len(set(defects)) or not set(defects).issubset(RUBRICS[dimension]['defects']):
            raise ValueError('invalid defect tags')
        ordinal = grading_corpus.STATUSES.index(status) + 1
        if prior:
            if (prior['status'], prior['ordinal'], prior['confidence'], json.loads(prior['defects_json']), prior['rationale']) == (status, ordinal, confidence, sorted(defects), rationale):
                return
            raise ValueError('contradictory resubmission')
        if not items or items[0]['disagreement_id'] != disagreement_id:
            raise ValueError('disagreement is not current')
        _, presentation_sha = presentation_bytes(conn, item['anchor_id'])
        response = {'disagreement_id': disagreement_id, 'status': status, 'ordinal': ordinal,
                    'confidence': confidence, 'defects': sorted(defects), 'rationale': rationale}
        response_sha = hashlib.sha256(json.dumps(response, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        pending_label = conn.execute("SELECT response_sha256,presentation_sha256 FROM grading_labels WHERE operation_key=?",
            (f'{EPOCH}|adjudication|{disagreement_id}',)).fetchone()
        if pending_label and (pending_label['response_sha256'] != response_sha or
                              pending_label['presentation_sha256'] != presentation_sha):
            raise ValueError('contradictory resubmission')
    label_id = grading_corpus.record_label(
        operation_key=f'{EPOCH}|adjudication|{disagreement_id}', anchor_id=item['anchor_id'],
        dimension=dimension, label_kind='expert_adjudicated_label', labeler='henry',
        model_version=None, prompt_schema_version=EPOCH, presentation_sha256=presentation_sha,
        response_sha256=response_sha, status=status, ordinal=ordinal, confidence=confidence,
        defects=sorted(defects), blind_to=[x for x in BLIND_TO if x != 'other_label'])
    grading_corpus.adjudicate(operation_key=f'{EPOCH}|adjudication|{disagreement_id}',
        disagreement_id=disagreement_id, label_id=label_id, adjudicator='henry', rationale=rationale)


def install_routes(app) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import HTMLResponse, RedirectResponse, Response
    from urllib.parse import parse_qs
    from nas_server.database import get_conn

    @app.get('/grading/adjudicate', response_class=HTMLResponse)
    def adjudication_page():
        with get_conn() as conn:
            items, complete = queue(conn)
            item = items[0] if items else None
            facts = measurements(conn, item) if item else []
        return HTMLResponse(render_page(item, complete, complete + len(items), facts),
                            headers={'Cache-Control': 'no-store'})

    @app.get('/grading/adjudicate/image/{anchor_id}')
    def adjudication_image(anchor_id: str):
        with get_conn() as conn:
            items, _ = queue(conn)
            if not items or items[0]['anchor_id'] != anchor_id:
                raise HTTPException(status_code=404, detail='Image unavailable')
            try:
                raw, _ = presentation_bytes(conn, anchor_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail='Image unavailable') from exc
        return Response(raw, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.post('/grading/adjudicate')
    async def adjudication_submit(request: Request):
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(status_code=413, detail='Form too large')
        try:
            fields = parse_qs(raw.decode('utf-8'), keep_blank_values=True)
            save_answer((fields.get('disagreement_id') or [''])[0], fields)
        except (UnicodeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail='Invalid or stale adjudication submission') from exc
        return RedirectResponse('/grading/adjudicate', status_code=303)

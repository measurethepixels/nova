"""Blind, append-only Pass A page for the frozen labeling-epoch/1 pilot."""

import hashlib
import html
import json
import random
from pathlib import Path

from nas_server import grading_corpus

EPOCH = "labeling-epoch/1"
REVISION = "seed/1.0"
DIMENSIONS = ("stretch", "color", "dynamic-range")
CONFIDENCES = (0.33, 0.67, 1.0)
BLIND_TO = (
    "candidate_grade", "other_label", "calibration_thresholds", "target_name",
    "run_id", "workflow_version", "known_defects", "critique_text", "pipeline_scores",
)
RUBRICS = {
    "stretch": {
        "title": "Stretch — tonal placement and visibility",
        "meanings": {
            "excellent": "Background is dark but still shows faint texture rather than flat black; the target stands clearly apart from it; faint outer structure is visible without noise dominating; bright regions keep internal gradation.",
            "good": "As above with one minor weakness (e.g. faint structure slightly under-shown, or background a touch bright or dark) that a viewer would not remark on.",
            "fair": "One clear weakness a viewer would notice: faint structure largely hidden, background noticeably grey/washed or noticeably crushed, or the target looking flat.",
            "poor": "Several of those weaknesses, or one severe: sky crushed to featureless black losing faint signal, a grey veil over the whole frame, or the target barely separated from the background.",
            "bad": "Stretch fails the image: target mostly invisible or blown out, or the frame dominated by noise or an unstretched/overstretched look.",
        },
        "not_defects": "a darker, high-contrast galaxy background (a deliberate preference); no visible \"sky\" in a frame-filling nebula — judge the target's own tonal hierarchy instead.",
        "defects": ("sky-too-bright", "sky-crushed", "faint-structure-hidden", "target-flat", "overstretched-noise", "highlights-flattened"),
    },
    "color": {
        "title": "Color — balance and plausibility",
        "meanings": {
            "excellent": "Background (where it exists) is neutral; star colours vary naturally; target colours are plausible and internally consistent for the rendering used; no localized casts.",
            "good": "One minor tint or slight muting a viewer would not remark on.",
            "fair": "One clear issue a viewer would notice: a visible overall cast, a patchy/local cast, or colours that look muted or oddly saturated.",
            "poor": "A strong cast or several clear issues; target colours look implausible for the rendering used.",
            "bad": "Colour is broken: a heavy cast dominating the frame, channel-separated artifacts, or colours that clearly misrepresent the image.",
        },
        "not_defects": "a narrowband palette mapping such as gold/blue (SHO-like) or HOO — judge consistency, casts, and sky neutrality within that rendering, not whether hydrogen-alpha is shown as red; the genuine blue-white of a synchrotron source or teal of an OIII-dominant nebula.",
        "defects": ("sky-cast", "local-cast", "green-cast", "muted-color", "oversaturated", "implausible-target-color", "channel-artifact"),
    },
    "dynamic-range": {
        "title": "Dynamic range — clipping and highlight/shadow retention",
        "meanings": {
            "excellent": "Bright cores and bright nebula regions keep visible internal structure; only the centres of the brightest stars are saturated; shadows are dark without blocking up; no banding or posterization.",
            "good": "Slight loss in the very brightest core, or slight shadow blocking, that a viewer would not remark on.",
            "fair": "Noticeable clipping of an extended bright region (e.g. a galaxy nucleus or nebula core becomes a flat white patch), or noticeable blocked shadows or mild posterization.",
            "poor": "Clear, extended clipping or shadow crushing that removes real structure, or obvious posterization.",
            "bad": "Large parts of the target clipped or crushed; the tonal range is visibly broken.",
        },
        "not_defects": "saturated centres of bright stars; a naturally dark dust lane.",
        "defects": ("core-clipped", "nebula-clipped", "star-bloat-clipped", "shadows-crushed", "posterization", "banding"),
    },
}


def pass_order(anchor_ids: list[str], pass_id: str = "A") -> list[str]:
    seed = int.from_bytes(hashlib.sha256(f"{EPOCH}|{pass_id}".encode()).digest()[:8], "big")
    ordered = sorted(anchor_ids)
    random.Random(seed).shuffle(ordered)
    return ordered


def queue(conn) -> tuple[list[dict], int]:
    """Return ordered anchors with outstanding Pass A dimensions and image progress."""
    rows = conn.execute("""SELECT a.anchor_id,a.capture_context_json FROM grading_anchors a
        JOIN grading_corpus_imports i ON i.import_id=a.import_id
        WHERE i.revision=?""", (REVISION,)).fetchall()
    by_id = {row["anchor_id"]: row for row in rows}
    labeled = {(row["anchor_id"], row["dimension"]) for row in conn.execute(
        """SELECT anchor_id,dimension FROM grading_labels WHERE label_kind='independent_label'
           AND labeler='henry' AND prompt_schema_version=?""", (EPOCH,))}
    unjudgeable = {(row["anchor_id"], row["dimension"]) for row in conn.execute(
        """SELECT anchor_id,dimension FROM grading_cannot_judge
           WHERE pass_id='A' AND epoch=?""", (EPOCH,))}
    result = []
    for anchor_id in pass_order(list(by_id)):
        pending = [dimension for dimension in DIMENSIONS
                   if (anchor_id, dimension) not in labeled | unjudgeable]
        result.append({"anchor_id": anchor_id, "context": json.loads(by_id[anchor_id]["capture_context_json"]),
                       "pending": pending})
    return result, sum(not item["pending"] for item in result)


def presentation_bytes(conn, anchor_id: str) -> tuple[bytes, str]:
    row = conn.execute("""SELECT a.capture_context_json FROM grading_anchors a
        JOIN grading_corpus_imports i ON i.import_id=a.import_id
        WHERE a.anchor_id=? AND i.revision=?""", (anchor_id, REVISION)).fetchone()
    if row is None:
        raise ValueError("unknown pilot image")
    context = json.loads(row["capture_context_json"])
    path = Path(context["presentation_path"])
    expected = context["presentation_sha256"]
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("not a JPEG presentation")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("presentation unavailable") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise ValueError("presentation hash mismatch")
    return raw, digest


def _rubric_html(dimension: str) -> str:
    rubric = RUBRICS[dimension]
    rows = "".join(f"<tr><th>{html.escape(status)}</th><td>{html.escape(meaning)}</td></tr>"
                   for status, meaning in rubric["meanings"].items())
    return (f'<details><summary>Rubric: {html.escape(rubric["title"])}</summary>'
            f'<table>{rows}</table><p>Not defects: {html.escape(rubric["not_defects"])}</p></details>')


def render_page(item: dict | None, complete: int, total: int) -> str:
    """Render only blinded answer controls; never interpolate run metadata."""
    if item is None:
        content = "<p>Pass A complete.</p>"
    else:
        fieldsets = []
        for dimension in item["pending"]:
            statuses = "".join(
                f'<label><input type="radio" name="status__{dimension}" value="{status}" required> {status}</label>'
                for status in grading_corpus.STATUSES)
            statuses += (f'<label><input type="radio" name="status__{dimension}" '
                         'value="cannot_judge" required> cannot judge</label>')
            confidences = "".join(
                f'<label><input type="radio" name="confidence__{dimension}" value="{value}"> '
                f'{value:g}</label>' for value in CONFIDENCES)
            defects = "".join(
                f'<label><input type="checkbox" name="defects__{dimension}" value="{html.escape(tag)}"> '
                f'{html.escape(tag)}</label>' for tag in RUBRICS[dimension]["defects"])
            fieldsets.append(
                f'<fieldset data-dimension="{dimension}"><legend>{html.escape(RUBRICS[dimension]["title"])}</legend>'
                f'{_rubric_html(dimension)}<div class="choices">{statuses}</div>'
                f'<p>Confidence</p><div class="choices">{confidences}</div>'
                f'<p>Defect tags</p><div class="choices">{defects}</div>'
                f'<label>Reason if cannot judge <textarea name="reason__{dimension}"></textarea></label>'
                '</fieldset>')
        content = (f'<div class="viewer"><img alt="Image to label" '
                   f'src="/grading/pass-a/image/{html.escape(item["anchor_id"])}"></div>'
                   '<button type="button" id="zoom">1:1 view</button>'
                   '<form method="post" action="/grading/pass-a" enctype="application/x-www-form-urlencoded">'
                   f'<input type="hidden" name="anchor_id" value="{html.escape(item["anchor_id"])}">'
                   + "".join(fieldsets) + '<button type="submit">Save and next image</button></form>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind grading · Pass A</title><style>
html,body{{margin:0;background:#808080;color:#111;font:16px/1.45 system-ui,sans-serif}}
main{{max-width:1000px;margin:auto;padding:1rem}}.viewer{{height:min(76vh,900px);overflow:auto;display:flex;align-items:center;justify-content:center}}
.viewer img{{max-width:100%;max-height:100%;object-fit:contain}}.viewer.actual{{display:block}}.viewer.actual img{{max-width:none;max-height:none;width:auto;height:auto}}
fieldset,details{{border:1px solid #333;margin:1rem 0;padding:.8rem}}fieldset{{background:#aaa}}details{{background:#bbb}}
table{{border-collapse:collapse}}th,td{{border:1px solid #555;padding:.3rem;vertical-align:top}}th{{text-align:left}}
.choices{{display:flex;flex-wrap:wrap;gap:.7rem}}label{{display:inline-block}}textarea{{display:block;width:100%;max-width:40rem}}
button{{font:inherit;padding:.5rem .8rem}}@media(max-width:600px){{.viewer{{height:60vh}}}}
</style></head><body><main><h1>Blind grading · Pass A</h1>
<p>{complete} / {total} images complete</p>{content}</main>
<script>document.querySelectorAll('fieldset[data-dimension]').forEach(fieldset=>{{
 const dimension=fieldset.dataset.dimension;
 const status=fieldset.querySelectorAll('input[name="status__'+dimension+'"]');
 const confidence=fieldset.querySelectorAll('input[name="confidence__'+dimension+'"]');
 const defects=fieldset.querySelectorAll('input[name="defects__'+dimension+'"]');
 const reason=fieldset.querySelector('textarea[name="reason__'+dimension+'"]');
 const update=()=>{{const choice=[...status].find(input=>input.checked)?.value;
  const unjudgeable=choice==='cannot_judge';
  confidence.forEach(input=>{{input.disabled=unjudgeable;input.required=!unjudgeable}});
  defects.forEach(input=>input.disabled=unjudgeable);
  reason.disabled=!unjudgeable;reason.required=unjudgeable;
 }};
 status.forEach(input=>input.addEventListener('change',update));update();
}});
const button=document.getElementById('zoom');if(button)button.onclick=()=>{{
 const viewer=document.querySelector('.viewer');viewer.classList.toggle('actual');
 button.textContent=viewer.classList.contains('actual')?'Fit to window':'1:1 view';
}};</script></body></html>'''


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _operation_key(anchor_id: str, dimension: str) -> str:
    return f"{EPOCH}|A|{anchor_id}|{dimension}"


def save_answers(anchor_id: str, fields: dict[str, list[str]]) -> None:
    """Validate a whole visible image before appending each dimension's answer."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        items, _ = queue(conn)
        selected = next((item for item in items if item["anchor_id"] == anchor_id), None)
        current = next((item for item in items if item["pending"]), None)
        if selected is None:
            raise ValueError("unknown pilot image")
        _, presentation_sha256 = presentation_bytes(conn, anchor_id)
        submitted = {name.removeprefix("status__") for name in fields if name.startswith("status__")}
        if not submitted or not submitted.issubset(DIMENSIONS):
            raise ValueError("invalid dimensions")
        planned = []
        for dimension in DIMENSIONS:
            if dimension not in submitted:
                continue
            statuses = fields.get(f"status__{dimension}", [])
            if len(statuses) != 1:
                raise ValueError("one status required")
            status = statuses[0]
            key = _operation_key(anchor_id, dimension)
            reason = (fields.get(f"reason__{dimension}") or [""])[0].strip()
            if status == "cannot_judge":
                if not reason or fields.get(f"confidence__{dimension}") or fields.get(f"defects__{dimension}"):
                    raise ValueError("cannot judge requires only a reason")
                payload = {"kind": "cannot_judge", "reason": reason}
            else:
                if status not in grading_corpus.STATUSES or reason:
                    raise ValueError("invalid status")
                confidence_values = fields.get(f"confidence__{dimension}", [])
                if len(confidence_values) != 1:
                    raise ValueError("one confidence required")
                try:
                    confidence = float(confidence_values[0])
                except ValueError as exc:
                    raise ValueError("invalid confidence") from exc
                if confidence not in CONFIDENCES:
                    raise ValueError("invalid confidence")
                defects = fields.get(f"defects__{dimension}", [])
                if len(defects) != len(set(defects)) or not set(defects).issubset(RUBRICS[dimension]["defects"]):
                    raise ValueError("invalid defect tags")
                ordinal = grading_corpus.STATUSES.index(status) + 1
                response = {"anchor_id": anchor_id, "dimension": dimension, "status": status,
                            "ordinal": ordinal, "confidence": confidence, "defects": sorted(defects)}
                payload = {"kind": "label", "status": status, "ordinal": ordinal,
                           "confidence": confidence, "defects": sorted(defects),
                           "response_sha256": hashlib.sha256(_canonical(response).encode()).hexdigest()}
            prior_label = conn.execute("SELECT * FROM grading_labels WHERE operation_key=?", (key,)).fetchone()
            prior_cannot = conn.execute("SELECT * FROM grading_cannot_judge WHERE operation_key=?", (key,)).fetchone()
            if prior_label or prior_cannot:
                if payload["kind"] == "label" and prior_label and prior_label["response_sha256"] == payload["response_sha256"] and prior_label["presentation_sha256"] == presentation_sha256:
                    continue
                if payload["kind"] == "cannot_judge" and prior_cannot and prior_cannot["reason"] == reason:
                    continue
                raise ValueError("contradictory resubmission")
            planned.append((dimension, key, payload))
        if planned:
            if (current is None or current["anchor_id"] != anchor_id
                    or {dimension for dimension, _, _ in planned} != set(selected["pending"])):
                raise ValueError("image is not current or answers are incomplete")
    # The corpus recorder owns its own short transaction. If a later write fails,
    # the queue resumes at the first still-pending dimension for this image.
    for dimension, key, payload in planned:
        if payload["kind"] == "cannot_judge":
            grading_corpus.record_cannot_judge(operation_key=key, anchor_id=anchor_id,
                dimension=dimension, pass_id="A", epoch=EPOCH, reason=payload["reason"])
        else:
            grading_corpus.record_label(operation_key=key, anchor_id=anchor_id,
                dimension=dimension, label_kind="independent_label", labeler="henry",
                model_version=None, prompt_schema_version=EPOCH,
                presentation_sha256=presentation_sha256,
                response_sha256=payload["response_sha256"], status=payload["status"],
                ordinal=payload["ordinal"], confidence=payload["confidence"],
                defects=payload["defects"], blind_to=list(BLIND_TO))


def install_routes(app) -> None:
    """Register the isolated page without importing FastAPI in pure-logic tests."""
    from fastapi import HTTPException, Request
    from fastapi.responses import HTMLResponse, RedirectResponse, Response
    from urllib.parse import parse_qs
    from nas_server.database import get_conn

    @app.get("/grading/pass-a", response_class=HTMLResponse)
    def pass_a_page():
        with get_conn() as conn:
            items, complete = queue(conn)
        return HTMLResponse(render_page(next((item for item in items if item["pending"]), None),
                                        complete, len(items)), headers={"Cache-Control": "no-store"})

    @app.get("/grading/pass-a/image/{anchor_id}")
    def pass_a_image(anchor_id: str):
        try:
            with get_conn() as conn:
                raw, _ = presentation_bytes(conn, anchor_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Image unavailable") from exc
        return Response(raw, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/grading/pass-a")
    async def pass_a_submit(request: Request):
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(status_code=413, detail="Form too large")
        try:
            fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            anchor_ids = fields.get("anchor_id", [])
            if len(anchor_ids) != 1:
                raise ValueError("invalid image")
            save_answers(anchor_ids[0], fields)
        except (UnicodeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail="Invalid or stale labeling submission") from exc
        return RedirectResponse("/grading/pass-a", status_code=303)

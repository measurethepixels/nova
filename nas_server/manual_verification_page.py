"""Standalone manual-verification page for one NOVA processing run."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from nas_server.recipe_page import PI_MANUAL, SASPRO_MANUAL, SIRIL_MANUAL, _parse_applied
from nas_server.workflow_docs import STEP_DOCS

TOOLS = (
    ("pi", "PixInsight", PI_MANUAL),
    ("siril", "Siril", SIRIL_MANUAL),
    ("saspro", "SASpro", SASPRO_MANUAL),
)


def _params_by_step(run: dict) -> dict[str, dict | None]:
    return {
        record["step"]: record.get("params")
        for record in run.get("step_records", [])
        if record.get("step")
    }


def _settings(params: dict | None) -> str:
    if params is None:
        return "No exact parameters were recorded for this historical run."
    if not params:
        return "The run recorded this step, but no adjustable parameters."
    return ", ".join(f"{key} = {value}" for key, value in params.items())


def _filename_slug(value: str) -> str:
    """Return a conservative filename component for browser exports."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return slug or "NOVA_run"


def _tool_panel(tool: str, label: str, manual: dict[str, str], step: str) -> str:
    body = manual.get(step)
    if body:
        source_state = "Canonical PR #1 handbook instruction"
        missing = ""
    else:
        body = (
            "<b>No canonical manual instruction exists for this historical step.</b> "
            "Determine whether the step was renamed, retired, folded into another "
            "operation, or still needs documentation."
        )
        source_state = "Handbook gap — requires disposition"
        missing = " missing"
    return (
        f'<section class="tool-panel{missing}" data-tool="{tool}">'
        f'<div class="source-state">{html.escape(source_state)}</div>'
        f'<div class="manual-copy">{body}</div>'
        "</section>"
    )


def _step_card(
    index: int,
    applied: str,
    params: dict | None,
) -> str:
    step, variant = _parse_applied(applied)
    doc = STEP_DOCS.get(step)
    title = (doc or {}).get("title") or step.replace("_", " ").title()
    what = (doc or {}).get("what") or (
        "This historical run step is not present in the current canonical handbook."
    )
    canonical_state = (
        "Current STEP_DOCS entry"
        if doc
        else "Historical step missing from current STEP_DOCS"
    )
    variant_html = (
        f'<span class="variant">{html.escape(variant)}</span>' if variant else ""
    )
    panels = "".join(
        _tool_panel(tool, label, manual, step)
        for tool, label, manual in TOOLS
    )
    return f"""
<article class="step-card" id="step-{index}" data-step="{html.escape(step)}">
  <div class="step-heading">
    <span class="step-number">{index}</span>
    <div><h2>{html.escape(title)} {variant_html}</h2>
    <p class="source-state">{html.escape(canonical_state)} · key <code>{html.escape(step)}</code></p></div>
  </div>
  <p class="what">{what}</p>
  <div class="nova-settings"><strong>NOVA v1.23.0 recorded:</strong>
    <code>{html.escape(_settings(params))}</code>
  </div>
  {panels}
  <fieldset class="verdict">
    <legend>Manual verification result</legend>
    <label><input type="radio" name="status-{index}" value="pass"> PASS — accurate as written</label>
    <label><input type="radio" name="status-{index}" value="correction"> NEEDS CORRECTION</label>
    <label><input type="radio" name="status-{index}" value="na"> N/A — tool cannot perform this step</label>
    <label><input type="radio" name="status-{index}" value="blocked"> BLOCKED — cannot verify yet</label>
  </fieldset>
  <div class="notes-grid">
    <label>Actual menu/module path
      <textarea data-field="path" rows="2" placeholder="What the current UI calls it and where you found it"></textarea>
    </label>
    <label>Values/settings actually used
      <textarea data-field="values" rows="2" placeholder="Exact values, units, masks, modes, and defaults"></textarea>
    </label>
    <label>Observed result and corrections
      <textarea data-field="result" rows="3" placeholder="What happened, what was unclear, and the wording that should change"></textarea>
    </label>
    <label>Evidence reference
      <input data-field="evidence" type="text" placeholder="Screenshot filename, saved process icon, or output image">
    </label>
  </div>
</article>"""


def render_manual_verification_page(
    run_dir: str | Path,
    *,
    source_revision: str = "PR #1 @ 5032331",
) -> str:
    """Render a self-contained browser checklist for one run."""
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.log").read_text(encoding="utf-8"))
    applied = run.get("steps_applied", [])
    params = _params_by_step(run)
    target = run.get("target") or "Unknown target"
    target_slug = _filename_slug(str(target))
    workflow = run.get("workflow") or "unknown"
    version = run.get("workflow_version") or "unknown"
    score = (run.get("final_scores") or {}).get("overall", "—")
    run_id = run_dir.name
    cards = "".join(
        _step_card(index, value, params.get(_parse_applied(value)[0]))
        for index, value in enumerate(applied, 1)
    )
    nav = "".join(
        f'<a href="#step-{index}"><span>{index}</span>{html.escape((_parse_applied(value)[0]).replace("_", " ").title())}</a>'
        for index, value in enumerate(applied, 1)
    )
    tool_buttons = "".join(
        f'<button type="button" class="tool-button{" active" if i == 0 else ""}" data-tool="{tool}">{label}</button>'
        for i, (tool, label, _manual) in enumerate(TOOLS)
    )
    metadata = json.dumps(
        {
            "target": target,
            "target_slug": target_slug,
            "workflow": workflow,
            "workflow_version": version,
            "run_id": run_id,
            "source_revision": source_revision,
            "steps": [
                {
                    "index": index,
                    "applied": value,
                    "key": _parse_applied(value)[0],
                    "variant": _parse_applied(value)[1],
                }
                for index, value in enumerate(applied, 1)
            ],
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(target))} manual recipe verification</title>
<style>
:root{{--bg:#071019;--panel:#0f1b27;--panel2:#152534;--text:#e8f1f7;--muted:#9db0bd;--line:#294052;--cyan:#45d8e8;--gold:#f3ba57;--red:#ff7878;--green:#6ed69c}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 80% 0,#143145 0,transparent 36rem),var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}
button,input,textarea{{font:inherit}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
.hero{{padding:2.4rem max(1rem,calc((100vw - 1180px)/2));border-bottom:1px solid var(--line)}}.eyebrow{{color:var(--cyan);font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:.78rem}}h1{{font-size:clamp(2rem,5vw,4.4rem);line-height:1;margin:.45rem 0}}.lede{{max-width:62rem;color:var(--muted);font-size:1.05rem}}.meta{{display:flex;flex-wrap:wrap;gap:.55rem;margin:1rem 0}}.meta span{{background:#0b1620;border:1px solid var(--line);padding:.35rem .65rem;border-radius:999px}}
.warning{{max-width:70rem;background:#2c2110;border:1px solid #70551c;padding:.85rem 1rem;border-radius:.7rem;color:#f7d995}}
.toolbar{{position:sticky;top:0;z-index:10;background:rgba(7,16,25,.94);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:.75rem max(1rem,calc((100vw - 1180px)/2));display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}}
.tool-button,.action{{border:1px solid var(--line);background:var(--panel);color:var(--text);padding:.55rem .8rem;border-radius:.55rem;cursor:pointer}}.tool-button.active{{border-color:var(--cyan);color:var(--cyan);background:#102b36}}.action:hover,.tool-button:hover{{border-color:var(--cyan)}}.danger{{color:var(--red)}}.progress-wrap{{min-width:220px;flex:1}}.progress-label{{display:flex;justify-content:space-between;color:var(--muted);font-size:.82rem}}.progress{{height:.45rem;background:#1a2a36;border-radius:1rem;overflow:hidden}}.progress>div{{height:100%;width:0;background:linear-gradient(90deg,var(--cyan),var(--green))}}
.layout{{display:grid;grid-template-columns:230px minmax(0,900px);gap:1.5rem;max-width:1180px;margin:1.5rem auto;padding:0 1rem}}nav{{position:sticky;top:5.5rem;align-self:start;max-height:calc(100vh - 7rem);overflow:auto}}nav a{{display:flex;gap:.55rem;color:var(--muted);text-decoration:none;padding:.4rem;border-left:2px solid transparent;font-size:.87rem}}nav a:hover{{color:var(--text);border-color:var(--cyan)}}nav span{{color:var(--cyan);min-width:1.3rem}}
.step-card{{background:linear-gradient(145deg,var(--panel),#0b1721);border:1px solid var(--line);border-radius:1rem;padding:1.15rem;margin-bottom:1.15rem;scroll-margin-top:6rem}}.step-card.complete{{border-color:var(--green)}}.step-card.problem{{border-color:var(--red)}}.step-heading{{display:flex;gap:.8rem;align-items:flex-start}}.step-number{{display:grid;place-items:center;width:2.2rem;height:2.2rem;background:#102b36;color:var(--cyan);border-radius:50%;font-weight:800}}h2{{margin:0;font-size:1.25rem}}.variant{{font-size:.68rem;color:var(--gold);border:1px solid #6b5226;border-radius:999px;padding:.15rem .4rem;vertical-align:middle}}.source-state{{color:var(--muted);font-size:.78rem;margin:.2rem 0}}.what{{color:#c6d5df}}.nova-settings{{background:#09131c;border-left:3px solid var(--gold);padding:.65rem .8rem;margin:.8rem 0}}.nova-settings code{{display:block;color:#f6d79d;overflow-wrap:anywhere;margin-top:.2rem}}
.tool-panel{{background:var(--panel2);padding:.85rem;border-radius:.7rem;margin:.8rem 0}}.tool-panel:not([data-tool="pi"]){{display:none}}.tool-panel.missing{{border:1px solid var(--red)}}.manual-copy ol{{padding-left:1.3rem}}.manual-copy code{{background:#09131c;padding:.08rem .25rem;border-radius:.25rem}}
.verdict{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.4rem .8rem;border:1px solid var(--line);border-radius:.7rem;padding:.75rem;margin:1rem 0}}.verdict legend{{color:var(--cyan);font-weight:700}}.verdict label{{cursor:pointer}}.notes-grid{{display:grid;grid-template-columns:1fr 1fr;gap:.75rem}}.notes-grid label{{color:var(--muted);font-size:.83rem}}textarea,input[type=text]{{display:block;width:100%;margin-top:.25rem;background:#08131c;color:var(--text);border:1px solid var(--line);border-radius:.45rem;padding:.6rem;resize:vertical}}textarea:focus,input:focus{{outline:2px solid var(--cyan);border-color:transparent}}
.saved{{font-size:.78rem;color:var(--green);min-width:4rem}}footer{{max-width:900px;margin:2rem auto;padding:1rem;color:var(--muted);font-size:.82rem}}
@media(max-width:800px){{.layout{{display:block}}nav{{display:none}}.verdict,.notes-grid{{grid-template-columns:1fr}}.toolbar{{position:relative}}}}
@media print{{body{{background:#fff;color:#111}}.toolbar,nav{{display:none}}.layout{{display:block;max-width:none}}.step-card{{break-inside:avoid;background:#fff;border-color:#999}}.tool-panel{{background:#f5f5f5}}textarea,input{{border-color:#999;color:#111}}}}
</style>
</head>
<body>
<header class="hero">
  <div class="eyebrow">Measure the pixels · manual verification</div>
  <h1>{html.escape(str(target))} processing recipe</h1>
  <p class="lede">Work through the exact workflow NOVA applied to this {html.escape(str(target))} run. Choose one tool, perform every applicable step manually, and record whether the current handbook instruction is accurate.</p>
  <div class="meta"><span>Run {html.escape(run_id)}</span><span>{html.escape(workflow)} v{html.escape(str(version))}</span><span>Score {html.escape(str(score))}</span><span>{len(applied)} applied steps</span><span>{html.escape(source_revision)}</span></div>
  <div class="warning"><strong>Draft verification harness.</strong> Your entries stay in this browser's local storage. Export a Markdown or JSON report before switching computers, clearing browser data, or replacing this file. A PASS verifies the written manual path—not that all three tools produce pixel-identical output.</div>
</header>
<div class="toolbar">
  <strong>Tool:</strong> {tool_buttons}
  <div class="progress-wrap"><div class="progress-label"><span>Reviewed</span><span id="progress-text">0 / {len(applied)}</span></div><div class="progress"><div id="progress-bar"></div></div></div>
  <span class="saved" id="saved">Saved locally</span>
  <button class="action" id="export-md">Export Markdown</button>
  <button class="action" id="export-json">Export JSON</button>
  <button class="action" onclick="window.print()">Print</button>
  <button class="action danger" id="reset">Reset current tool</button>
</div>
<div class="layout"><nav>{nav}</nav><main>{cards}</main></div>
<footer>Source: M66 episode run <code>{html.escape(run_id)}</code> · canonical instructions from {html.escape(source_revision)}. Feed exported corrections back into <code>STEP_DOCS</code>, <code>PI_MANUAL</code>, <code>SIRIL_MANUAL</code>, or <code>SASPRO_MANUAL</code>; do not hand-edit a published copy.</footer>
<script id="page-meta" type="application/json">{metadata}</script>
<script>
const META=JSON.parse(document.getElementById('page-meta').textContent);
const KEY='nova-manual-verify:'+META.run_id;
let activeTool='pi';
let state=JSON.parse(localStorage.getItem(KEY)||'{{}}');
const tools={{pi:'PixInsight',siril:'Siril',saspro:'SASpro'}};
function toolState(){{return state[activeTool]||(state[activeTool]={{}})}}
function stepState(index){{const t=toolState();return t[index]||(t[index]={{}})}}
function save(){{localStorage.setItem(KEY,JSON.stringify(state));const el=document.getElementById('saved');el.textContent='Saved '+new Date().toLocaleTimeString();updateProgress()}}
function loadTool(){{
 document.querySelectorAll('.tool-button').forEach(b=>b.classList.toggle('active',b.dataset.tool===activeTool));
 document.querySelectorAll('.tool-panel').forEach(p=>p.style.display=p.dataset.tool===activeTool?'block':'none');
 document.querySelectorAll('.step-card').forEach((card,i)=>{{
  const index=String(i+1),s=stepState(index);
  card.querySelectorAll('input[type=radio]').forEach(r=>r.checked=r.value===s.status);
  card.querySelectorAll('[data-field]').forEach(el=>el.value=s[el.dataset.field]||'');
  card.classList.toggle('complete',s.status==='pass'||s.status==='na');
  card.classList.toggle('problem',s.status==='correction'||s.status==='blocked');
 }});
 updateProgress();
}}
function updateProgress(){{
 const vals=Object.values(toolState()),done=vals.filter(v=>v.status).length,total=META.steps.length;
 document.getElementById('progress-text').textContent=`${{done}} / ${{total}}`;
 document.getElementById('progress-bar').style.width=`${{100*done/total}}%`;
}}
document.querySelectorAll('.tool-button').forEach(b=>b.onclick=()=>{{activeTool=b.dataset.tool;loadTool()}});
document.querySelectorAll('.step-card').forEach((card,i)=>{{
 const index=String(i+1);
 card.querySelectorAll('input[type=radio]').forEach(r=>r.onchange=()=>{{stepState(index).status=r.value;save();loadTool()}});
 card.querySelectorAll('[data-field]').forEach(el=>el.oninput=()=>{{stepState(index)[el.dataset.field]=el.value;save()}});
}});
function report(){{
 return {{...META,exported_at:new Date().toISOString(),tool:activeTool,tool_label:tools[activeTool],results:toolState()}};
}}
function download(name,text,type){{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{{type}}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}}
document.getElementById('export-json').onclick=()=>download(`${{META.target_slug}}_${{activeTool}}_verification.json`,JSON.stringify(report(),null,2),'application/json');
document.getElementById('export-md').onclick=()=>{{
 const r=report();let out=`# ${{r.target}} manual verification — ${{r.tool_label}}\\n\\nRun: ${{r.run_id}}  \\nWorkflow: ${{r.workflow}} v${{r.workflow_version}}  \\nExported: ${{r.exported_at}}\\n\\n`;
 r.steps.forEach(step=>{{const x=r.results[String(step.index)]||{{}};out+=`## ${{step.index}}. ${{step.key.replaceAll('_',' ')}}${{step.variant?' ['+step.variant+']':''}}\\n\\n- Status: ${{x.status||'NOT REVIEWED'}}\\n- Actual path: ${{x.path||''}}\\n- Values: ${{x.values||''}}\\n- Result/correction: ${{x.result||''}}\\n- Evidence: ${{x.evidence||''}}\\n\\n`}});
 download(`${{META.target_slug}}_${{activeTool}}_verification.md`,out,'text/markdown');
}};
document.getElementById('reset').onclick=()=>{{if(confirm(`Delete all saved ${{tools[activeTool]}} verification entries on this device?`)){{delete state[activeTool];save();loadTool()}}}};
loadTool();
</script>
</body></html>"""

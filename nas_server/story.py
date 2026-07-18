"""
Astrophotography story page generator.

Builds a personal journal-style HTML page covering Henry's full journey from
first light (March 18, 2024) to present. Data comes from the DB; Claude writes
short narrative paragraphs for each target; the page is served live at /story
and can be exported as a self-contained HTML file.

Also renders per-run processing reports (/report/{target}/{run_id}) and the
pipeline development journal (/devlog).
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_FIRST_LIGHT_DATE = "2024-03-18"


def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{h * 60:.0f}m"
    return f"{h:.1f}h"


def _fmt_date(d: str | None) -> str:
    if not d:
        return "unknown"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(d[:10])
        return dt.strftime("%b %d, %Y")
    except Exception:
        return d[:10]


def _score_bar(scores: dict, score_before: float | None = None,
               score_after: float | None = None) -> str:
    """Render a small inline score summary with optional before→after arc."""
    parts = []
    # Before → After improvement arc (auto_process targets)
    if score_before is not None and score_after is not None and score_after != score_before:
        delta = score_after - score_before
        arrow_color = "#4ade80" if delta > 0 else "#f87171"
        sign = "+" if delta > 0 else ""
        parts.append(
            f'<span class="score-arc" style="color:{arrow_color}">'
            f'AI pipeline: {score_before:.0f} → {score_after:.0f} '
            f'({sign}{delta:.0f})</span>'
        )
    keys = ["overall", "noise", "gradient", "star_roundness", "color_balance"]
    for k in keys:
        v = scores.get(k)
        if isinstance(v, (int, float)):
            label = k.replace("_", " ")
            color = "#4ade80" if v >= 7 else "#facc15" if v >= 5 else "#f87171"
            parts.append(
                f'<span class="score-pill" style="background:{color}22;border:1px solid {color}">'
                f'{label} {v}/10</span>'
            )
    return " ".join(parts)


def _pipeline_badges(steps: list[dict]) -> str:
    """Render processing step badges from processing_history rows."""
    if not steps:
        return ""
    badges = []
    for s in steps:
        step = s.get("step", "")
        engine = s.get("engine", "")
        label = step
        if engine and engine != "auto_process":
            label = f"{step} [{engine}]"
        ai = "🤖 " if engine == "auto_process" else ""
        badges.append(f'<span class="badge">{ai}{label}</span>')
    return " ".join(badges)


def _stacking_badges(processed: dict | None) -> str:
    if not processed:
        return ""
    tool = processed.get("tool", "") or ""
    if not tool:
        return ""
    return f'<span class="badge badge-stack">{tool}</span>'


def generate_story_html(target: str | None = None, embed_images: bool = False) -> str:
    """
    Build the full story HTML page.
    If target is given, render a single-target page.
    """
    from nas_server.database import get_story_data, get_global_story_stats
    from nas_server.config import settings

    lib = settings["seestar_library_path"]
    targets = get_story_data(target)
    stats = get_global_story_stats()

    # Group by year for section headers
    sections: dict[str, list] = {}
    for t in targets:
        year = (t.get("first_date") or "")[:4] or "Unknown"
        sections.setdefault(year, []).append(t)

    header_title = f"Henry's Astrophotography Journey"
    total_hours = stats.get("total_hours", 0)
    total_subs  = stats.get("total_subs", 0)
    total_tgts  = stats.get("total_targets", 0)
    total_stack = stats.get("total_stacked", 0)

    def _stat_card(value: str, label: str, color: str = "#58a6ff") -> str:
        return (
            f'<div class="stat-card">'
            f'<div class="stat-val" style="color:{color}">{value}</div>'
            f'<div class="stat-lbl">{label}</div>'
            f'</div>'
        )

    stats_html = (
        f'<div class="stat-grid">'
        + _stat_card(str(total_tgts), "objects imaged", "#58a6ff")
        + _stat_card(_fmt_hours(total_hours), "integration time", "#3fb950")
        + _stat_card(f"{total_subs:,}", "light frames", "#e3b341")
        + _stat_card(str(total_stack), "stacked results", "#d2a8ff")
        + f'</div>'
    )

    sub = f"Since {_fmt_date(_FIRST_LIGHT_DATE)} &nbsp;·&nbsp; Seestar S50 &nbsp;·&nbsp; Chandler AZ"

    cards = []
    for year in sorted(sections.keys()):
        count = len(sections[year])
        cards.append(
            f'<h2 class="year-header" onclick="toggleYear(\'{year}\')">'
            f'<span class="yr-ind" id="yr-ind-{year}">▼</span>'
            f' — {year} —'
            f' <span style="font-size:.75rem;font-weight:400;color:var(--text2)">({count})</span>'
            f'</h2>'
        )
        cards.append(f'<div class="year-cards" id="yr-{year}">')
        for t in sections[year]:
            tname = t["target"]
            obj_type = (t.get("object_type") or "").replace("_", " ").title()
            first = _fmt_date(t.get("first_date"))
            last = _fmt_date(t.get("last_date"))
            sessions = t.get("session_count") or 0
            subs = t.get("total_subs") or 0
            hours = _fmt_hours(t.get("total_hours") or 0)
            narrative = t.get("narrative") or ""
            scores = t.get("latest_scores") or {}
            proc = t.get("latest_processed")
            steps = t.get("processing_steps") or []
            preview_fn = t.get("preview_filename")

            # Image block
            img_url = f"/image/{tname}/{preview_fn}" if preview_fn else ""
            if embed_images and img_url:
                try:
                    import base64
                    img_path = Path(lib) / tname / "_processed" / preview_fn
                    if img_path.exists():
                        b64 = base64.b64encode(img_path.read_bytes()).decode()
                        img_src = f"data:image/jpeg;base64,{b64}"
                    else:
                        img_src = ""
                except Exception:
                    img_src = ""
            else:
                img_src = img_url

            img_html = ""
            if img_src:
                img_html = (
                    f'<a href="{img_url}" target="_blank" class="thumb-link">'
                    f'<img src="{img_src}" class="thumb" alt="{tname}" loading="lazy"></a>'
                )

            date_range = first if first == last else f"{first} – {last}"
            meta = f"{sessions} session{'s' if sessions != 1 else ''} · {subs:,} subs · {hours}"
            tname_id = tname.replace(' ', '-').replace('/', '-')

            narr_html = ""
            if narrative:
                narr_html = (
                    f'<p class="narrative narrative-clamped" id="narr-{tname_id}">{narrative}</p>'
                    f'<a class="narr-toggle" href="#" onclick="toggleNarr(\'{tname_id}\');return false">Read more</a>'
                )

            card = f"""
<div class="card" id="target-{tname_id}">
  <div class="card-left">{img_html}</div>
  <div class="card-body">
    <div class="card-header">
      <span class="target-name">{tname}</span>
      {f'<span class="obj-type">{obj_type}</span>' if obj_type else ''}
    </div>
    <div class="card-meta">{date_range} &nbsp;·&nbsp; {meta}</div>
    {narr_html}
    {f'<a href="{img_url}" target="_blank" class="view-link">View full image →</a>' if img_url else ''}
  </div>
</div>"""
            cards.append(card)
        cards.append('</div>')  # close year-cards

    cards_html = "\n".join(cards)

    return _page_shell(
        title=header_title,
        body=f"""
<div class="hero">
  <h1>✦ {header_title}</h1>
  <div class="sub">{sub}</div>
  {stats_html}
  <div class="nav-links">
    <a href="/devlog">Pipeline Dev Journal</a>
    <a href="/story/export">Export HTML</a>
  </div>
</div>
<div class="content">
{cards_html}
</div>
<div class="export-bar">
  Generated by SeeStar Database · Seestar S50 · Claude AI
  &nbsp;·&nbsp; <a href="/devlog">Dev Journal</a>
</div>
<script>
function toggleYear(y) {{
  var d = document.getElementById('yr-' + y);
  var ind = document.getElementById('yr-ind-' + y);
  if (!d) return;
  var hidden = d.style.display === 'none';
  d.style.display = hidden ? '' : 'none';
  if (ind) ind.textContent = hidden ? '▼' : '▶';
}}
function toggleNarr(id) {{
  var p = document.getElementById('narr-' + id);
  var a = p && p.nextElementSibling;
  if (!p) return;
  var clamped = p.classList.toggle('narrative-clamped');
  if (a) a.textContent = clamped ? 'Read more' : 'Read less';
}}
document.querySelectorAll('.narr-toggle').forEach(function(a) {{ a.style.display = 'inline'; }});
</script>""",
        extra_css="""
  .hero {{ text-align: center; padding: 3rem 1rem 2rem; border-bottom: 1px solid var(--border); }}
  .hero h1 {{ font-size: 2rem; font-weight: 700; color: var(--text); }}
  .hero .sub {{ margin-top: .5rem; color: var(--text2); font-size: .95rem; }}
  .stat-grid {{ display: flex; justify-content: center; flex-wrap: wrap; gap: 1rem; margin: 1.5rem auto .5rem; max-width: 600px; }}
  .stat-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: .9rem 1.5rem; min-width: 120px; text-align: center; }}
  .stat-val {{ font-size: 1.6rem; font-weight: 700; line-height: 1.2; }}
  .stat-lbl {{ font-size: .75rem; color: var(--text2); margin-top: .25rem; text-transform: uppercase; letter-spacing: .06em; }}
  .nav-links {{ margin-top: 1rem; display: flex; justify-content: center; gap: 1.5rem; font-size: .85rem; }}
  .content {{ max-width: 900px; margin: 0 auto; padding: 2rem 1rem 4rem; overflow-x: hidden; }}
  .year-header {{ color: var(--text2); font-size: 1rem; font-weight: 600; letter-spacing: .1em;
                  text-transform: uppercase; margin: 2.5rem 0 1rem; padding-bottom: .5rem;
                  border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; }}
  .year-header:hover {{ color: var(--text); }}
  .yr-ind {{ font-size: .7rem; opacity: .7; }}
  .card {{ display: flex; gap: 1.25rem; background: var(--bg2); border: 1px solid var(--border);
           border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; }}
  .card-left {{ flex-shrink: 0; }}
  .thumb-link {{ display: block; line-height: 0; }}
  .thumb {{ width: 140px; height: 105px; object-fit: cover; border-radius: 6px; display: block;
            max-width: 100%; border: 1px solid var(--border); }}
  .card-body {{ flex: 1; min-width: 0; }}
  .card-header {{ display: flex; align-items: baseline; gap: .75rem; margin-bottom: .35rem; }}
  .target-name {{ font-size: 1.15rem; font-weight: 700; color: var(--text); }}
  .obj-type {{ font-size: .8rem; color: var(--text2); background: var(--bg3);
               border: 1px solid var(--border); border-radius: 4px; padding: 1px 7px; }}
  .card-meta {{ font-size: .82rem; color: var(--text2); margin-bottom: .75rem; }}
  .narrative {{ font-size: .875rem; line-height: 1.6; color: var(--text2); margin-bottom: .4rem; }}
  .narrative.narrative-clamped {{ display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .narr-toggle {{ font-size: .78rem; color: var(--text2); display: none; margin-bottom: .5rem; }}
  .view-link {{ font-size: .82rem; }}
  .export-bar {{ text-align: center; padding: 1.5rem; border-top: 1px solid var(--border);
                 color: var(--text2); font-size: .85rem; }}
  @media (max-width: 600px) {{
    .card {{ flex-direction: column; }}
    .card-left {{ width: 100%; }}
    .thumb {{ width: 100%; height: 200px; }}
  }}
  @media (max-width: 480px) {{
    .hero {{ padding: 1.5rem 1rem 1rem; }}
    .hero h1 {{ font-size: 1.4rem; }}
    .stat-card {{ min-width: 90px; padding: .65rem 1rem; }}
    .stat-val {{ font-size: 1.25rem; }}
  }}
  @media (max-width: 700px) {{
    .rpt-body {{ flex-direction: column; padding: 1rem; gap: 1rem; }}
    .final-col {{ width: 100%; position: static; }}
  }}""",
    )


def _nav_html() -> str:
    try:
        from nas_server.database import get_pending_review_count
        n = get_pending_review_count()
    except Exception:
        n = 0
    badge = (
        f' <span style="background:#f85149;color:#fff;border-radius:8px;'
        f'padding:0 5px;font-size:.72rem;font-weight:700;vertical-align:middle">{n}</span>'
        if n else ""
    )
    return f"""
<nav style="position:sticky;top:0;z-index:100;background:var(--bg);
            border-bottom:1px solid var(--border);padding:.45rem 1rem;
            display:flex;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;
            -webkit-overflow-scrolling:touch;gap:.25rem .9rem;align-items:center;font-size:.85rem">
  <span style="font-weight:700;color:var(--text);margin-right:.3rem;white-space:nowrap;flex-shrink:0">SeeStar</span>
  <a id="seestar-homelab" href="/"
     style="white-space:nowrap;flex-shrink:0;color:var(--text2)">&#8592; Home Lab</a>
  <script>document.getElementById('seestar-homelab').href='http://'+location.hostname+'/';</script>
  <a href="/">Home</a>
  <a href="/targets-view">Targets</a>
  <a href="/worklist">Worklist</a>
  <a href="/gallery">Gallery</a>
  <a href="/messier">Messier</a>
  <a href="/pipeline-view">Pipeline</a>
  <a href="/workflows-doc">Workflows</a>
  <a href="/stack-history">Stacks</a>
  <a href="/manual-processing">Manual</a>
  <a href="/calendar">Calendar</a>
  <a href="/queue-view">Queue</a>
  <a href="/planner">Planner</a>
  <a href="/learning-view">Tools</a>
  <a href="/review">Reviews{badge}</a>
  <a href="/videos">Videos</a>
  <a href="/story">Story</a>
  <a href="/associations">Associations</a>
  <a href="/devlog">DevLog</a>
  <a href="/help">Help</a>
  <a href="/chat" style="color:#e3b341">Chat</a>
  <a href="/suggestions" style="color:#8b949e">Suggestions</a>
</nav>"""


def _page_shell(title: str, body: str, extra_css: str = "") -> str:
    """Shared HTML shell for all pages — dark astrophoto aesthetic."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>{title}</title>
<script src="https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --border: #30363d;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ max-width: 100%; overflow-x: hidden; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  img {{ max-width: 100%; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  #pwa-nav {{ display:none; position:fixed; bottom:0; left:0; right:0; z-index:200;
              background:var(--bg2); border-top:1px solid var(--border);
              padding:.5rem 1.5rem env(safe-area-inset-bottom,.5rem);
              display:none; justify-content:space-between; align-items:center; gap:1rem; }}
  #pwa-nav button {{ background:none; border:none; color:var(--accent); font-size:1.4rem;
                     padding:.3rem .8rem; cursor:pointer; border-radius:6px; line-height:1; }}
  #pwa-nav button:active {{ background:var(--bg3); }}
  #pwa-nav button:disabled {{ color:var(--border); cursor:default; }}
  .pwa-active body {{ padding-bottom:60px; }}
  {extra_css}
</style>
</head>
<body>
{_nav_html()}
{body}
<div id="pwa-nav">
  <button id="pwa-back"  onclick="history.back()"    title="Back">&#8592;</button>
  <button id="pwa-fwd"   onclick="history.forward()" title="Forward">&#8594;</button>
</div>
<script>
(function() {{
  var standalone = navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;
  if (!standalone) return;
  document.documentElement.classList.add('pwa-active');
  var bar = document.getElementById('pwa-nav');
  bar.style.display = 'flex';
  function updateButtons() {{
    document.getElementById('pwa-back').disabled = history.length <= 1;
  }}
  updateButtons();
  window.addEventListener('popstate', updateButtons);
}})();
</script>
</body>
</html>"""


def render_run_report_html(run: dict, lib_path: str) -> str:
    """Render a full HTML processing run report for one auto_process execution."""
    target = run["target"]
    workflow = run.get("workflow", "")
    started = run.get("started_at", "")[:16].replace("T", " ")
    elapsed = run.get("elapsed_s", 0)
    steps = run.get("steps_json") or []
    init_scores = run.get("initial_scores") or {}
    final_scores = run.get("final_scores") or {}
    critical = run.get("critical_eval") or ""
    proc_dir = Path(lib_path) / target / "_processed"
    output_path = run.get("output_path") or ""
    run_dir = Path(output_path).parent if output_path else proc_dir

    def _resolve_url(rel_path: str) -> str | None:
        """Return a /image URL for rel_path, checking run_dir first then proc_dir.

        If the file has been renamed with a numeric prefix by _number_run_files()
        (e.g. auto_preview_pre_crop.jpg → 02_auto_preview_pre_crop.jpg), the glob
        fallback finds it transparently so step-cards still render correctly.
        """
        if not rel_path:
            return None
        full_run = run_dir / rel_path
        if full_run.exists():
            try:
                return f"/image/{target}/{full_run.relative_to(proc_dir)}"
            except ValueError:
                pass
        # Fallback: file may have been renamed with a two-digit numeric prefix
        try:
            matches = sorted(run_dir.glob(f"[0-9][0-9]_{rel_path}"))
            if matches:
                return f"/image/{target}/{matches[0].relative_to(proc_dir)}"
        except Exception:
            pass
        if (proc_dir / rel_path).exists():
            return f"/image/{target}/{rel_path}"
        return None

    def _img(rel_path: str, label: str = "", cls: str = "step-img") -> str:
        url = _resolve_url(rel_path)
        if not url:
            return ""
        return (f'<figure class="{cls}">'
                f'<a href="{url}" target="_blank">'
                f'<img src="{url}" alt="{label}" loading="lazy"></a>'
                + (f'<figcaption>{label}</figcaption>' if label else "") +
                f'</figure>')

    def _img_pair(before_rel: str, after_rel: str, label: str = "") -> str:
        """Before/after comparison slider when both images exist; falls back to single after."""
        before_url = _resolve_url(before_rel)
        after_url = _resolve_url(after_rel)
        if before_url and after_url:
            return (
                f'<div class="ba-slider">'
                f'<img class="ba-after" src="{after_url}" alt="after {label}">'
                f'<img class="ba-before" src="{before_url}" alt="before {label}">'
                f'<div class="ba-divider"></div>'
                f'<span class="ba-lbl ba-lbl-l">before</span>'
                f'<span class="ba-lbl ba-lbl-r">after</span>'
                f'<input type="range" class="ba-range" value="50" min="0" max="100">'
                f'</div>'
            )
        if after_url:
            return (f'<figure class="step-img">'
                    f'<a href="{after_url}" target="_blank">'
                    f'<img src="{after_url}" alt="{label}" loading="lazy"></a>'
                    + (f'<figcaption>{label}</figcaption>' if label else "")
                    + f'</figure>')
        return ""

    def _score_chips(scores: dict | None, prefix: str = "") -> str:
        if not scores:
            return ""
        chips = []
        for k, v in scores.items():
            if not isinstance(v, (int, float)):
                continue
            color = "#4ade80" if v >= 7 else "#facc15" if v >= 5 else "#f87171"
            chips.append(
                f'<span class="chip" style="background:{color}22;border:1px solid {color}">'
                f'{prefix}{k.replace("_"," ")} {v}/10</span>'
            )
        return " ".join(chips)

    def _render_step(sr: dict, idx: int) -> str:
        step = sr.get("step", "")
        stype = sr.get("type", "")
        skipped = sr.get("skipped", False)
        skip_reason = sr.get("skip_reason", "")
        reasoning = sr.get("reasoning", "")
        winner = sr.get("winner", "")
        params = sr.get("params") or {}
        sb = sr.get("scores_before") or {}
        sa = sr.get("scores_after") or {}
        variants = sr.get("variants") or []

        icon = {"assess": "◎", "stretch_pick": "◈", "experiment": "◉", "standard": "•"}.get(stype, "•")
        status_badge = ""
        if skipped:
            status_badge = '<span class="badge-skip">skipped — no improvement</span>'
        elif stype == "assess":
            status_badge = '<span class="badge-assess">assessment</span>'

        # Score delta
        score_delta = ""
        if sb and sa:
            deltas = []
            for k in set(list(sb) + list(sa)):
                bv = sb.get(k)
                av = sa.get(k)
                if isinstance(bv, (int, float)) and isinstance(av, (int, float)):
                    d = av - bv
                    col = "#4ade80" if d > 0 else "#f87171" if d < 0 else "#8b949e"
                    sign = "+" if d > 0 else ""
                    deltas.append(f'<span style="color:{col}">{k.replace("_"," ")}: {bv}→{av} ({sign}{d})</span>')
            score_delta = "  ".join(deltas)

        # Params block (standard steps)
        params_html = ""
        if params:
            rows = "".join(f"<tr><td>{k}</td><td><code>{v}</code></td></tr>"
                           for k, v in sorted(params.items()))
            params_html = f'<table class="params-table"><tbody>{rows}</tbody></table>'

        # Winner params for experiment steps
        winner_params = sr.get("winner_params") or {}
        if not winner_params and stype == "experiment" and winner:
            # fall back: find winner params from variants list
            winner_params = next(
                (v.get("params") or {} for v in variants if v.get("winner")), {}
            )
        if winner_params and stype == "experiment":
            wp_rows = "".join(f"<tr><td>{k}</td><td><code>{v}</code></td></tr>"
                              for k, v in sorted(winner_params.items()))
            if wp_rows:
                params_html = (f'<div class="winner-params-label">Winner settings</div>'
                               f'<table class="params-table"><tbody>{wp_rows}</tbody></table>')

        # Variant grid for experiments
        variants_html = ""
        if variants:
            vcards = []
            for v in variants:
                vid = v.get("id", "")
                vok = v.get("ok", True)
                vscore = v.get("score")
                vwinner = v.get("winner", False)
                vdesc = v.get("description", vid)
                vprev = v.get("preview", "")
                vparams = v.get("params") or {}
                border = "border:2px solid #4ade80;" if vwinner else ""
                score_txt = f'<div class="v-score">{vscore}/10</div>' if vscore else ""
                winner_badge = '<div class="v-winner">✓ winner</div>' if vwinner else ""
                img_html = _img(vprev, cls="v-img") if vprev else ""
                vparams_html = ""
                if vparams:
                    vp_rows = "".join(f"<tr><td>{k}</td><td><code>{pv}</code></td></tr>"
                                      for k, pv in sorted(vparams.items()))
                    vparams_html = f'<table class="params-table v-params-table"><tbody>{vp_rows}</tbody></table>'
                vcards.append(
                    f'<div class="vcard" style="{border}">'
                    f'{img_html}{score_txt}{winner_badge}'
                    f'<div class="v-id">{vid}</div>'
                    f'<div class="v-desc">{vdesc}</div>'
                    f'{vparams_html}'
                    f'</div>'
                )
            variants_html = f'<div class="variant-grid">{"".join(vcards)}</div>'

        # Preview image (or before/after comparison slider)
        if stype == "assess":
            preview_html = _img(sr.get("preview", ""), label=f"assessment: {sr.get('label','')}")
            scores_html = _score_chips(sr.get("scores"))
        else:
            scores_html = score_delta
            before_path = sr.get("preview_before", "")
            after_path = sr.get("preview_after", "")
            preview_html = _img_pair(before_path, after_path, label=step)

        reasoning_html = (
            f'<div class="reasoning">Claude: {reasoning}</div>' if reasoning else ""
        )

        return f"""
<div class="step-card {'step-skipped' if skipped else ''}" id="step-{idx}">
  <div class="step-header">
    <span class="step-icon">{icon}</span>
    <span class="step-name">{step}</span>
    {f'<span class="step-winner">→ {winner}</span>' if winner else ''}
    {status_badge}
    {f'<span class="step-elapsed">{sr.get("elapsed_s",""):.0f}s</span>' if sr.get("elapsed_s") else ''}
  </div>
  {f'<div class="score-row">{scores_html}</div>' if scores_html else ''}
  {reasoning_html}
  {params_html}
  {variants_html}
  {preview_html}
  {f'<div class="skip-note">{skip_reason}</div>' if skipped and skip_reason else ''}
</div>"""

    steps_html = "".join(_render_step(sr, i) for i, sr in enumerate(steps))

    # Replication Recipe — one row per applied step with final settings
    recipe_rows = ""
    for sr in steps:
        stype = sr.get("type", "")
        step_name = sr.get("step", "")
        if stype in ("assess", "star_split", "star_combine") or sr.get("skipped"):
            continue
        if stype == "experiment":
            winner = sr.get("winner", "")
            wp = sr.get("winner_params") or next(
                (v.get("params") or {} for v in (sr.get("variants") or []) if v.get("winner")), {}
            )
            setting_str = f"<b>{winner}</b>"
            if wp:
                setting_str += " — " + ", ".join(
                    f"{k}={v}" for k, v in sorted(wp.items())
                )
        else:
            params = sr.get("params") or {}
            setting_str = (", ".join(f"{k}={v}" for k, v in sorted(params.items()))
                           if params else "—")
        recipe_rows += (
            f'<tr><td style="white-space:nowrap;padding:.3rem .6rem;'
            f'font-weight:600">{step_name}</td>'
            f'<td style="padding:.3rem .6rem;font-size:.8rem;'
            f'font-family:monospace;color:var(--text)">{setting_str}</td></tr>'
        )

    recipe_html = ""
    if recipe_rows:
        recipe_html = f"""
<div class="recipe-card">
  <h2>Replication Recipe</h2>
  <p style="font-size:.78rem;color:var(--text2);margin-bottom:.75rem">
    Exact settings applied — copy these to replicate this result.
  </p>
  <table style="width:100%;border-collapse:collapse;font-size:.82rem">
    <thead><tr style="border-bottom:1px solid var(--border)">
      <th style="text-align:left;padding:.3rem .6rem;color:var(--text2)">Step</th>
      <th style="text-align:left;padding:.3rem .6rem;color:var(--text2)">Settings</th>
    </tr></thead>
    <tbody>{recipe_rows}</tbody>
  </table>
</div>"""

    # Final image (large)
    final_img_html = _img("auto_final_preview.jpg", label="Final result", cls="final-img")

    # Score comparison table
    all_keys = sorted(set(list(init_scores.keys()) + list(final_scores.keys())) -
                      {"input_tokens", "output_tokens", "issues", "suggestions", "raw_response"})
    score_rows = ""
    for k in all_keys:
        iv = init_scores.get(k)
        fv = final_scores.get(k)
        if not isinstance(iv, (int, float)) and not isinstance(fv, (int, float)):
            continue
        d = (fv - iv) if isinstance(iv, (int, float)) and isinstance(fv, (int, float)) else None
        col = "#4ade80" if (d or 0) > 0 else "#f87171" if (d or 0) < 0 else "#8b949e"
        sign = "+" if (d or 0) > 0 else ""
        delta_cell = f'<td style="color:{col}">{sign}{d}</td>' if d is not None else "<td>—</td>"
        score_rows += (
            f"<tr><td>{k.replace('_',' ')}</td>"
            f"<td>{iv if isinstance(iv,(int,float)) else '—'}</td>"
            f"<td>{fv if isinstance(fv,(int,float)) else '—'}</td>"
            f"{delta_cell}</tr>"
        )

    critical_html = ""
    if critical:
        paras = "".join(f"<p>{p.strip()}</p>" for p in critical.split("\n\n") if p.strip())
        critical_html = f'<div class="critical-eval"><h2>Critical Evaluation</h2>{paras}</div>'

    body = f"""
<div class="rpt-header">
  <div class="rpt-back"><a href="/story">← Story</a> &nbsp;·&nbsp; <a href="/report/{target}">All runs for {target}</a></div>
  <h1>{target} — Processing Run Report</h1>
  <div class="rpt-meta">Workflow: <b>{workflow}</b> &nbsp;·&nbsp; {started} &nbsp;·&nbsp; {elapsed:.0f}s</div>
  <div class="rpt-scores">
    <div class="score-group"><div class="sg-label">Before</div>{_score_chips(init_scores)}</div>
    <div class="score-arrow">→</div>
    <div class="score-group"><div class="sg-label">After</div>{_score_chips(final_scores)}</div>
  </div>
</div>
<div class="rpt-body">
  <div class="steps-col">
    <h2>Pipeline Steps</h2>
    {steps_html}
  </div>
  <div class="final-col">
    {final_img_html}
    {'<h2>Score Summary</h2><table class="score-table"><thead><tr><th>Dimension</th><th>Before</th><th>After</th><th>Delta</th></tr></thead><tbody>' + score_rows + '</tbody></table>' if score_rows else ''}
    {recipe_html}
    {critical_html}
  </div>
</div>
<script>
document.querySelectorAll('.ba-slider').forEach(function(s) {{
  var r = s.querySelector('.ba-range');
  var b = s.querySelector('.ba-before');
  var d = s.querySelector('.ba-divider');
  r.addEventListener('input', function() {{
    var pct = this.value;
    b.style.clipPath = 'inset(0 ' + (100 - pct) + '% 0 0)';
    d.style.left = pct + '%';
  }});
}});
</script>"""

    return _page_shell(
        title=f"{target} — Run Report",
        body=body,
        extra_css="""
  .rpt-header {{ padding: 2rem 2rem 1rem; border-bottom: 1px solid var(--border); }}
  .rpt-back {{ font-size: .82rem; color: var(--text2); margin-bottom: .75rem; }}
  .rpt-header h1 {{ font-size: 1.6rem; font-weight: 700; margin-bottom: .4rem; }}
  .rpt-meta {{ font-size: .85rem; color: var(--text2); margin-bottom: 1rem; }}
  .rpt-scores {{ display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; }}
  .score-group {{ display: flex; flex-wrap: wrap; gap: .3rem; align-items: center; }}
  .sg-label {{ font-size: .8rem; color: var(--text2); margin-right: .3rem; }}
  .score-arrow {{ font-size: 1.5rem; color: var(--text2); }}
  .chip {{ font-size: .75rem; padding: 2px 8px; border-radius: 12px; }}
  .rpt-body {{ display: flex; gap: 2rem; padding: 2rem; align-items: flex-start; max-width: 1400px; margin: 0 auto; }}
  .steps-col {{ flex: 1; min-width: 0; }}
  .steps-col h2 {{ font-size: 1rem; color: var(--text2); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 1rem; }}
  .final-col {{ width: 380px; flex-shrink: 0; position: sticky; top: 1rem; }}
  .final-col h2 {{ font-size: 1rem; color: var(--text2); text-transform: uppercase; letter-spacing: .08em; margin-bottom: .75rem; margin-top: 1.5rem; }}
  .step-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: .75rem; }}
  .step-card.step-skipped {{ opacity: .5; }}
  .step-header {{ display: flex; align-items: center; gap: .5rem; margin-bottom: .5rem; flex-wrap: wrap; }}
  .step-icon {{ font-size: 1rem; color: var(--text2); }}
  .step-name {{ font-weight: 600; font-size: .95rem; }}
  .step-winner {{ font-size: .85rem; color: #4ade80; }}
  .step-elapsed {{ font-size: .75rem; color: var(--text2); margin-left: auto; }}
  .badge-assess {{ font-size: .7rem; background: #1f6feb33; border: 1px solid #1f6feb; border-radius: 4px; padding: 1px 6px; color: #58a6ff; }}
  .badge-skip {{ font-size: .7rem; background: #f8717122; border: 1px solid #f87171; border-radius: 4px; padding: 1px 6px; color: #f87171; }}
  .score-row {{ display: flex; flex-wrap: wrap; gap: .25rem; margin-bottom: .5rem; font-size: .8rem; }}
  .reasoning {{ font-size: .82rem; color: var(--text2); font-style: italic; margin-bottom: .5rem; background: var(--bg3); padding: .5rem .75rem; border-radius: 4px; border-left: 2px solid var(--border); }}
  .params-table {{ font-size: .78rem; border-collapse: collapse; margin-bottom: .5rem; }}
  .params-table td {{ padding: 2px 8px 2px 0; color: var(--text2); }}
  .params-table td:first-child {{ color: var(--text); font-weight: 500; min-width: 130px; }}
  .params-table code {{ font-size: .78rem; color: #e3b341; }}
  .winner-params-label {{ font-size: .7rem; color: #4ade80; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 3px; }}
  .v-params-table {{ margin-top: .3rem; text-align: left; }}
  .v-params-table td {{ font-size: .68rem; padding: 1px 4px 1px 0; }}
  .variant-grid {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: .5rem; }}
  .vcard {{ background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: .4rem .6rem; font-size: .75rem; min-width: 120px; text-align: center; }}
  .v-id {{ font-weight: 600; margin-top: .2rem; }}
  .v-desc {{ color: var(--text2); font-size: .7rem; }}
  .v-score {{ font-size: .85rem; font-weight: 700; color: #facc15; }}
  .v-winner {{ color: #4ade80; font-size: .7rem; font-weight: 600; }}
  .v-img img {{ width: 90px; height: 67px; object-fit: cover; border-radius: 4px; display: block; margin: 0 auto .3rem; }}
  .step-img img {{ max-width: 100%; border-radius: 4px; margin-top: .5rem; border: 1px solid var(--border); }}
  .step-img figcaption {{ font-size: .75rem; color: var(--text2); margin-top: .25rem; }}
  .ba-slider {{ position: relative; overflow: hidden; border-radius: 4px; margin-top: .5rem; cursor: ew-resize; user-select: none; border: 1px solid var(--border); }}
  .ba-slider .ba-after {{ display: block; width: 100%; }}
  .ba-slider .ba-before {{ position: absolute; top: 0; left: 0; width: 100%; clip-path: inset(0 50% 0 0); pointer-events: none; }}
  .ba-slider .ba-divider {{ position: absolute; top: 0; left: 50%; width: 2px; height: 100%; background: rgba(255,255,255,.75); pointer-events: none; transform: translateX(-50%); }}
  .ba-slider .ba-lbl {{ position: absolute; top: 6px; background: rgba(0,0,0,.6); color: #fff; font-size: .65rem; padding: 1px 5px; border-radius: 3px; pointer-events: none; letter-spacing: .04em; }}
  .ba-slider .ba-lbl-l {{ left: 6px; }}
  .ba-slider .ba-lbl-r {{ right: 6px; }}
  .ba-slider .ba-range {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; opacity: 0; cursor: ew-resize; margin: 0; }}
  .final-img img {{ width: 100%; border-radius: 6px; border: 1px solid var(--border); display: block; }}
  .final-img figcaption {{ font-size: .8rem; color: var(--text2); text-align: center; margin-top: .4rem; }}
  .score-table {{ width: 100%; border-collapse: collapse; font-size: .82rem; margin-bottom: 1.5rem; }}
  .score-table th {{ text-align: left; color: var(--text2); font-weight: 600; padding: .3rem .5rem; border-bottom: 1px solid var(--border); }}
  .score-table td {{ padding: .3rem .5rem; border-bottom: 1px solid var(--bg3); }}
  .recipe-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem; margin-top: 1.5rem; }}
  .recipe-card h2 {{ font-size: 1rem; font-weight: 700; margin-bottom: .35rem; color: var(--text); }}
  .recipe-card tr {{ border-bottom: 1px solid var(--bg3); }}
  .recipe-card tr:last-child {{ border-bottom: none; }}
  .critical-eval {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }}
  .critical-eval h2 {{ font-size: 1rem; font-weight: 700; margin-bottom: .75rem; color: var(--text); }}
  .critical-eval p {{ font-size: .88rem; line-height: 1.75; color: var(--text); margin-bottom: .75rem; }}
  .critical-eval p:last-child {{ margin-bottom: 0; }}
  @media (max-width: 900px) {{
    .rpt-body {{ flex-direction: column; }}
    .final-col {{ width: 100%; position: static; }}
  }}""",
    )


def _format_devlog_body(text: str) -> str:
    """Convert plain text + light markdown to HTML. Handles **bold**, `code`, - lists, ```blocks."""
    import re
    import html as _html

    def inline_md(s: str) -> str:
        parts = re.split(r'`([^`]+)`', s)
        out = []
        for i, p in enumerate(parts):
            if i % 2 == 0:
                p = _html.escape(p)
                p = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p)
                out.append(p)
            else:
                out.append(f'<code>{_html.escape(p)}</code>')
        return ''.join(out)

    paragraphs = text.split('\n\n')
    html_parts = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        lines = para.split('\n')
        first = lines[0].strip()

        if first.startswith('```'):
            inner = lines[1:]
            if inner and inner[-1].strip().startswith('```'):
                inner = inner[:-1]
            html_parts.append(
                f'<pre class="dl-code"><code>{_html.escape(chr(10).join(inner))}</code></pre>'
            )
            continue

        stripped = [l.strip() for l in lines if l.strip()]
        if stripped and all(l.startswith('- ') for l in stripped):
            items = ''.join(f'<li>{inline_md(l[2:])}</li>' for l in stripped)
            html_parts.append(f'<ul class="dl-list">{items}</ul>')
            continue

        joined = ' '.join(l.strip() for l in lines if l.strip())
        html_parts.append(f'<p>{inline_md(joined)}</p>')

    return '\n'.join(html_parts)


def render_devlog_html(entries: list[dict]) -> str:
    """Render the pipeline development journal as HTML."""
    from nas_server.devlog import CATEGORIES

    items = []
    current_date = None
    for e in entries:
        edate = e.get("date", "")[:10]
        if edate != current_date:
            if current_date is not None:
                items.append('</div>')  # close previous date group
            current_date = edate
            try:
                from datetime import datetime
                label = datetime.fromisoformat(edate).strftime("%B %d, %Y")
            except Exception:
                label = edate
            items.append(
                f'<div class="dl-date" data-date="{edate}" onclick="toggleDlDate(\'{edate}\')">'
                f'<span class="dl-date-ind" id="dli-{edate}">▼</span> {label}'
                f'</div>'
            )
            items.append(f'<div class="dl-date-group" id="dlg-{edate}">')

        cat = e.get("category", "decision")
        icon, color = CATEGORIES.get(cat, ("•", "#8b949e"))
        title = e.get("title", "")
        body_raw = e.get("body", "")
        files = e.get("files") or []
        eid = e.get("id", "")

        body_html = _format_devlog_body(body_raw)

        body_content = (
            f'<div class="dl-body-inner dl-collapsed">{body_html}</div>'
        )

        files_html = ""
        if files:
            file_tags = "".join(f'<span class="dl-file">{f}</span>' for f in files)
            files_html = f'<div class="dl-files">{file_tags}</div>'

        items.append(f"""
<div class="dl-card" id="entry-{eid}" data-category="{cat}" style="border-left:3px solid {color}" onclick="dlToggle(this)">
  <div class="dl-card-header">
    <span class="dl-icon" style="color:{color}">{icon}</span>
    <span class="dl-title">{title}</span>
    <span class="dl-expand-indicator">↓</span>
  </div>
  <div class="dl-body">{body_content}</div>
  {files_html}
</div>""")

    if current_date is not None:
        items.append('</div>')  # close last date group

    total = len([e for e in entries if e.get("id")])
    cat_counts = {}
    for e in entries:
        c = e.get("category", "decision")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    filter_btns = f'<button class="dl-filter active" data-cat="all" onclick="dlFilter(this,\'all\')">All ({total})</button>'
    for k, (ic, col) in CATEGORIES.items():
        n = cat_counts.get(k, 0)
        if n:
            filter_btns += (
                f'<button class="dl-filter" data-cat="{k}" onclick="dlFilter(this,\'{k}\')" '
                f'style="--fc:{col}">{ic} {k.replace("_", " ")} ({n})</button>'
            )

    body = f"""
<div class="dl-header">
  <div class="dl-back"><a href="/story">← Story</a></div>
  <h1>Pipeline Development Journal</h1>
  <div class="dl-sub">Building an AI-driven astrophotography automation system &nbsp;·&nbsp; {total} entries</div>
  <div class="dl-filters">{filter_btns}</div>
</div>
<div class="dl-content" id="dl-content">
  {"".join(items)}
</div>
<script>
function dlToggle(card) {{
  var inner = card.querySelector('.dl-body-inner');
  var ind = card.querySelector('.dl-expand-indicator');
  if (!inner) return;
  inner.classList.toggle('dl-collapsed');
  if (ind) ind.textContent = inner.classList.contains('dl-collapsed') ? '↓' : '↑';
}}
function toggleDlDate(d) {{
  var grp = document.getElementById('dlg-' + d);
  var ind = document.getElementById('dli-' + d);
  if (!grp) return;
  var hidden = grp.style.display === 'none';
  grp.style.display = hidden ? '' : 'none';
  if (ind) ind.textContent = hidden ? '▼' : '▶';
}}
function dlFilter(btn, cat) {{
  document.querySelectorAll('.dl-filter').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  document.querySelectorAll('.dl-card').forEach(function(c) {{
    c.style.display = (cat === 'all' || c.dataset.category === cat) ? '' : 'none';
  }});
  // Show/hide date headers based on whether any visible card is in their group
  document.querySelectorAll('.dl-date').forEach(function(d) {{
    var grpId = 'dlg-' + d.dataset.date;
    var grp = document.getElementById(grpId);
    if (!grp) {{ d.style.display = 'none'; return; }}
    var vis = Array.from(grp.querySelectorAll('.dl-card')).some(function(c) {{
      return c.style.display !== 'none';
    }});
    d.style.display = vis ? '' : 'none';
    if (vis && grp.style.display === 'none') grp.style.display = '';
  }});
}}
</script>"""

    return _page_shell(
        title="Pipeline Dev Journal — SeeStar",
        body=body,
        extra_css="""
  .dl-header {{ padding: 2rem 2rem 1.5rem; border-bottom: 1px solid var(--border); text-align: center; }}
  .dl-back {{ font-size: .82rem; color: var(--text2); margin-bottom: .75rem; text-align: left; }}
  .dl-header h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: .4rem; }}
  .dl-sub {{ font-size: .9rem; color: var(--text2); margin-bottom: 1rem; }}
  .dl-filters {{ display: flex; justify-content: center; flex-wrap: wrap; gap: .4rem; }}
  .dl-filter {{ background: var(--bg2); border: 1px solid var(--border); color: var(--text2);
                border-radius: 6px; padding: .3rem .85rem; cursor: pointer; font-size: .81rem; transition: all .15s; }}
  .dl-filter:hover {{ background: var(--bg3); color: var(--fc, var(--text)); border-color: var(--fc, var(--border)); }}
  .dl-filter.active {{ background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }}
  .dl-content {{ max-width: 820px; margin: 0 auto; padding: 2rem 1rem 4rem; }}
  .dl-date {{ color: var(--text2); font-size: .78rem; font-weight: 600; text-transform: uppercase;
              letter-spacing: .1em; margin: 2rem 0 .6rem; cursor: pointer; user-select: none; }}
  .dl-date:hover {{ color: var(--text); }}
  .dl-date-ind {{ font-size: .65rem; opacity: .7; }}
  .dl-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
              padding: .85rem 1.1rem; margin-bottom: .5rem; scroll-margin-top: 4rem;
              cursor: pointer; transition: border-color .15s; }}
  .dl-card:hover {{ border-color: #444c56; }}
  .dl-card:target {{ box-shadow: 0 0 0 1px var(--accent)33; }}
  .dl-card-header {{ display: flex; align-items: center; gap: .6rem; }}
  .dl-icon {{ font-size: 1rem; flex-shrink: 0; }}
  .dl-title {{ font-weight: 600; font-size: .93rem; flex: 1; line-height: 1.4; color: var(--text); }}
  .dl-expand-indicator {{ font-size: .8rem; color: var(--text2); flex-shrink: 0; }}
  .dl-body {{ margin-top: 0; }}
  .dl-body p {{ font-size: .85rem; line-height: 1.65; color: var(--text2); margin: .5rem 0 0; }}
  .dl-body p:last-child {{ margin-bottom: 0; }}
  .dl-body strong {{ color: var(--text); font-weight: 600; }}
  .dl-body code {{ font-family: ui-monospace,monospace; font-size: .8rem; background: var(--bg3);
                   border-radius: 3px; padding: 1px 5px; color: #79c0ff; }}
  .dl-code {{ background: var(--bg3); border-radius: 5px;
              padding: .7rem .9rem; overflow-x: auto; margin: .5rem 0; }}
  .dl-code code {{ background: none; padding: 0; font-size: .78rem;
                   color: #adbac7; line-height: 1.6; white-space: pre; }}
  .dl-list {{ margin: .3rem 0 .5rem 1.2rem; }}
  .dl-list li {{ font-size: .85rem; line-height: 1.6; color: var(--text2); margin-bottom: .2rem; }}
  .dl-body-inner {{ position: relative; overflow: hidden; }}
  .dl-body-inner.dl-collapsed {{ max-height: 0; }}
  .dl-files {{ margin-top: .7rem; padding-top: .6rem; border-top: 1px solid var(--border);
               display: flex; flex-wrap: wrap; gap: .3rem; }}
  .dl-file {{ font-size: .68rem; font-family: ui-monospace,monospace; background: var(--bg3);
              border-radius: 3px; padding: 2px 6px; color: var(--text2); }}""",
    )

"""
New web pages for SeeStar NAS Server.
All pages use the same dark theme and _page_shell from story.py.

Routes wired in main.py:
  GET /                         → home_page()
  GET /targets-view             → targets_page()
  GET /queue-view               → queue_page()
  GET /queue-view/rows          → queue_rows_partial()  (HTMX partial)
  GET /learning-view            → learning_page()
  GET /calendar                 → calendar_page()
  GET /calendar/{year}/{month}  → calendar_page(year, month)
"""
import calendar as _cal
import json as _json
import logging
import urllib.parse as _uparse
from datetime import date as _date, datetime as _datetime, timezone as _tz, timedelta as _td
from pathlib import Path

_MST = _tz(offset=_td(hours=-7), name="MST")  # Arizona — no DST


def _fmt_mst(ts: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a UTC ISO timestamp string (from the DB) to MST for display."""
    if not ts:
        return ""
    try:
        dt = _datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_MST).strftime(fmt)
    except Exception:
        return ts[:16].replace("T", " ")

log = logging.getLogger(__name__)


def _shell(title: str, body: str, extra_css: str = "") -> str:
    from nas_server.story import _page_shell
    return _page_shell(title, body, extra_css)


def _score_pill(score) -> str:
    try:
        v = float(score)
    except (TypeError, ValueError):
        return f'<span style="color:var(--text2)">—</span>'
    color = "#3fb950" if v >= 7 else ("#e3b341" if v >= 5 else "#f85149")
    return f'<span style="background:{color};color:#0d1117;border-radius:4px;padding:1px 6px;font-size:.78rem;font-weight:600">{v:.1f}</span>'


def _stack_param_tags(run: dict) -> str:
    """Compact inline tags showing stacking parameters for a run dict."""
    tags = []
    _t = lambda txt, color: (
        f'<span style="border:1px solid {color};color:{color};border-radius:3px;'
        f'padding:0 4px;font-size:.72rem;white-space:nowrap">{txt}</span>'
    )
    pct = run.get("bottom_pct")
    if pct is not None:
        tags.append(_t(f"{int(pct*100)}% cull", "#8b949e") if pct > 0 else _t("no cull", "#d29922"))
    ecc = run.get("ecc_threshold")
    if ecc is not None and abs(ecc - 0.66) > 0.01:
        tags.append(_t(f"ecc<{ecc}", "#8b949e"))
    if run.get("hero"):
        tags.append(_t("hero", "#bc8cff"))
    if run.get("drizzle"):
        tags.append(_t("drizzle 2×", "#58a6ff"))
    exptime = run.get("exptime")
    if exptime:
        tags.append(_t(f"{exptime}s only", "#e3b341"))
    framing = run.get("framing")
    if framing and framing != "min":
        tags.append(_t(f"framing:{framing}", "#8b949e"))
    return " ".join(tags)


def _stage_badge(stage: str) -> str:
    colors = {
        "captured": "#8b949e",
        "stacked": "#58a6ff",
        "processing": "#e3b341",
        "processed": "#3fb950",
        "exported": "#bc8cff",
    }
    color = colors.get(stage or "captured", "#8b949e")
    return (f'<span style="border:1px solid {color};color:{color};border-radius:4px;'
            f'padding:1px 6px;font-size:.75rem">{stage or "captured"}</span>')


# ---------------------------------------------------------------------------
# Worklist — what to process next
# ---------------------------------------------------------------------------

def _worklist_thumb_url(target: str, output_path: str) -> str | None:
    """Resolve the best run's final preview to an /image URL (None if missing)."""
    if not output_path:
        return None
    from nas_server.config import settings
    run_dir = Path(output_path).parent
    proc_dir = Path(settings["seestar_library_path"]) / target / "_processed"
    cands: list[Path] = []
    for pat in ("*final_preview.jpg", "*auto_preview_final.jpg", "*auto_final_preview.jpg"):
        cands += sorted(run_dir.glob(pat))
    for cand in cands:
        if cand.exists():
            try:
                return f"/image/{_uparse.quote(target, safe='')}/{cand.relative_to(proc_dir)}"
            except ValueError:
                return None
    return None


def worklist_page() -> str:
    from nas_server.database import (
        get_worklist, get_list, WORKLIST_LIST, REWORK_LIST)

    rows = get_worklist()
    by_target = {r["target"]: r for r in rows}
    wt_order = get_list(WORKLIST_LIST)
    wt_set = set(wt_order)
    rework_set = set(get_list(REWORK_LIST))
    work_through = [by_target[t] for t in wt_order if t in by_target]

    # Messier Wall priority (2026-07-12): an unprocessed Messier fills a dashed tile
    # on /messier, so Messier targets lead the unprocessed bucket outright and break
    # ties in rework. See the messier-wall memory.
    import re as _mre
    def _not_messier(r) -> int:
        return 0 if _mre.fullmatch(r"M ?\d{1,3}", r["target"].strip()) else 1

    rework = sorted([r for r in rows if r["bucket"] == "rework"],
                    key=lambda r: (r["best_overall"] if r["best_overall"] is not None else 99,
                                   _not_messier(r), -r["priority"]))
    unproc = sorted([r for r in rows if r["bucket"] == "unprocessed"],
                    key=lambda r: (_not_messier(r), -r["priority"], -r["stack_count"],
                                   r["target"]))
    good = sorted([r for r in rows if r["bucket"] == "good"],
                  key=lambda r: -(r["best_overall"] or 0))

    def _q(t: str) -> str:
        return _uparse.quote(t, safe="")

    def _toggle_btn(t: str, qt: str, label: str, list_name: str,
                    on: bool, color: str) -> str:
        return (f'<button data-on="{1 if on else 0}" data-color="{color}" '
                f'onclick="toggleList(this,\'{qt}\',\'{list_name}\')" '
                f'style="flex:1;background:var(--bg);border:1px solid '
                f'{color if on else "var(--border)"};color:{color if on else "var(--text2)"};'
                f'border-radius:5px;padding:.28rem;font-size:.74rem;cursor:pointer">'
                f'{label}</button>')

    def _card(r: dict, show_img: bool) -> str:
        t = r["target"]
        turl = f"/target/{_q(t)}"
        thumb = _worklist_thumb_url(t, r["best_output_path"]) if show_img else None
        img_html = (f'<a href="{turl}"><img src="{thumb}" loading="lazy" '
                    f'style="width:100%;height:120px;object-fit:cover;border-radius:6px 6px 0 0;'
                    f'display:block"></a>'
                    if thumb else
                    f'<a href="{turl}" style="display:flex;height:120px;align-items:center;'
                    f'justify-content:center;background:var(--bg);border-radius:6px 6px 0 0;'
                    f'color:var(--text2);font-size:.8rem">no preview</a>')
        score = (_score_pill(r["best_overall"]) if r["best_overall"] is not None
                 else '<span style="color:var(--text2)">—</span>')
        newer = ('<span style="border:1px solid #e3b341;color:#e3b341;border-radius:3px;'
                 'padding:0 4px;font-size:.7rem;margin-left:.3rem">new subs</span>'
                 if r["newer_data"] else "")
        flagged = ('<span style="border:1px solid #f85149;color:#f85149;border-radius:3px;'
                   'padding:0 4px;font-size:.7rem;margin-left:.3rem">⚑ rework</span>'
                   if t in rework_set else "")
        report = (f' · <a href="/report/{_q(t)}/{r["best_run_id"]}" '
                  f'style="font-size:.74rem">report</a>' if r["best_run_id"] else "")
        qt = _q(t)
        wt_btn = _toggle_btn(t, qt, "★ Work-through", WORKLIST_LIST, t in wt_set, "#bc8cff")
        rw_btn = _toggle_btn(t, qt, "⚑ Rework", REWORK_LIST, t in rework_set, "#f85149")
        return f"""
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;overflow:hidden">
        {img_html}
        <div style="padding:.5rem .6rem">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:.4rem">
            <a href="{turl}" style="font-weight:600;font-size:.86rem;white-space:nowrap;
               overflow:hidden;text-overflow:ellipsis">{t}</a>{score}
          </div>
          <div style="color:var(--text2);font-size:.74rem;margin-top:.2rem">
            {r["type"] or "—"} · {r["stack_count"]} stack(s){newer}{flagged}{report}
          </div>
          <button onclick="qTarget(this,'{qt}')"
            style="margin-top:.45rem;width:100%;background:var(--bg);border:1px solid var(--border);
            color:var(--text);border-radius:5px;padding:.3rem;font-size:.78rem;cursor:pointer">
            Queue auto-process</button>
          <button onclick="qStack(this,'{qt}')"
            style="margin-top:.35rem;width:100%;background:var(--bg);border:1px solid var(--border);
            color:var(--text);border-radius:5px;padding:.3rem;font-size:.78rem;cursor:pointer">
            Queue stack</button>
          <div style="display:flex;gap:.35rem;margin-top:.35rem">{wt_btn}{rw_btn}</div>
        </div>
      </div>"""

    def _section(title: str, sub: str, items: list, show_img: bool, color: str) -> str:
        if not items:
            cards = '<div style="color:var(--text2);padding:1rem">None 🎉</div>'
        else:
            cards = "".join(_card(r, show_img) for r in items)
        return f"""
  <div style="margin-bottom:2.2rem">
    <h2 style="font-size:1.05rem;margin-bottom:.2rem;color:{color}">{title}
      <span style="color:var(--text2);font-weight:400;font-size:.85rem">({len(items)})</span></h2>
    <div style="color:var(--text2);font-size:.8rem;margin-bottom:.8rem">{sub}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.8rem">
      {cards}
    </div>
  </div>"""

    body = f"""
<div style="max-width:1200px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.6rem;margin-bottom:.3rem">Processing Worklist</h1>
  <p style="color:var(--text2);margin-bottom:1.8rem;font-size:.88rem">
    What to work on next — ranked from your stacks and processing scores.</p>
  {_section("Work-through queue", "Your hand-picked targets to process next — ★ a card to add, ⚑ to flag for rework.", work_through, True, "#bc8cff")}
  {_section("Needs rework", "Best run scored below 7.0, or new subs captured since last process — worst first.", rework, True, "#f85149")}
  {_section("Unprocessed", "Has stacks but never auto-processed — highest priority first.", unproc, False, "#58a6ff")}
  {_section("Done · good results", "Best run at/above 7.0 — your ranked finals.", good, True, "#3fb950")}
</div>
<script>
async function qTarget(btn, t) {{
  btn.disabled = true; btn.textContent = 'Queuing…';
  try {{
    const r = await fetch('/queue?target=' + t + '&workflow=auto', {{method:'POST'}});
    btn.textContent = r.ok ? 'Queued ✓' : 'Failed';
    btn.style.color = r.ok ? '#3fb950' : '#f85149';
  }} catch (e) {{ btn.textContent = 'Failed'; btn.style.color = '#f85149'; }}
}}
async function qStack(btn, t) {{
  btn.disabled = true; btn.textContent = 'Queuing…';
  try {{
    const r = await fetch('/stack/' + t, {{method:'POST'}});
    btn.textContent = r.ok ? 'Stack queued ✓' : 'Failed';
    btn.style.color = r.ok ? '#3fb950' : '#f85149';
  }} catch (e) {{ btn.textContent = 'Failed'; btn.style.color = '#f85149'; }}
}}
async function toggleList(btn, t, list) {{
  const on = btn.getAttribute('data-on') === '1';
  const action = on ? 'remove' : 'add';
  const color = btn.getAttribute('data-color');
  btn.disabled = true;
  try {{
    const r = await fetch('/list/' + action + '?list=' + encodeURIComponent(list) + '&target=' + t, {{method:'POST'}});
    if (r.ok) {{
      btn.setAttribute('data-on', on ? '0' : '1');
      btn.style.borderColor = on ? 'var(--border)' : color;
      btn.style.color = on ? 'var(--text2)' : color;
    }}
  }} catch (e) {{}}
  btn.disabled = false;
}}
</script>"""
    return _shell("Worklist", body)


# ---------------------------------------------------------------------------
# Manual processing — Henry's hand-processed PixInsight exports
# ---------------------------------------------------------------------------

def _manual_recipe_html(flow: list[dict], n_steps: int, summary: str | None) -> str:
    """Render a parsed PixInsight recipe (compact flow) as step chips."""
    if summary:
        head = f'<div style="color:var(--text);font-size:.8rem;margin-top:.3rem">{summary}</div>'
    elif n_steps:
        head = (f'<div style="color:var(--text2);font-size:.8rem;margin-top:.3rem">'
                f'{n_steps} processing step{"s" if n_steps != 1 else ""}</div>')
    else:
        head = ('<div style="color:var(--text2);font-size:.78rem;margin-top:.3rem;'
                'font-style:italic">no embedded recipe (plain FITS)</div>')
    if not flow:
        return head
    steps = " ".join(
        f'<span style="border:1px solid var(--border);color:var(--text2);'
        f'border-radius:3px;padding:0 5px;font-size:.7rem;white-space:nowrap">'
        f'{s.get("i")}. {s.get("step") or s.get("class")}</span>'
        for s in flow
    )
    return (head + f'<div style="display:flex;flex-wrap:wrap;gap:.25rem;margin-top:.35rem">'
            f'{steps}</div>')


def manual_processing_page() -> str:
    """Review queue: folders that may hold a hand-processed final, plus the
    finals Henry has already flagged (with Claude grades)."""
    import json as _json
    from nas_server.database import list_manual_runs, reviewed_folder_status
    from nas_server.manual_capture import candidate_targets

    folders = candidate_targets()
    runs = list_manual_runs()
    statuses = reviewed_folder_status()
    skipped = sorted(t for t, s in statuses.items() if s == "skipped")

    def _q(t: str) -> str:
        return _uparse.quote(t, safe="")

    # --- Section 1: folders to review ---
    if folders:
        rows = "".join(
            f"""
        <a href="/manual-processing/folder/{_q(f['target'])}"
           style="display:flex;justify-content:space-between;align-items:center;
                  background:var(--bg2);border:1px solid var(--border);border-radius:8px;
                  padding:.6rem .8rem;text-decoration:none">
          <span style="font-weight:600;font-size:.9rem">{f['target']}</span>
          <span style="color:var(--text2);font-size:.78rem">{f['n_candidates']}
            candidate{'s' if f['n_candidates'] != 1 else ''} →</span>
        </a>"""
            for f in folders
        )
        review_section = f"""
    <h2 style="font-size:1.05rem;margin:0 0 .6rem">Folders to review
      <span style="color:var(--text2);font-weight:400;font-size:.82rem">· {len(folders)}</span></h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:.55rem;margin-bottom:1.8rem">
      {rows}
    </div>"""
    else:
        review_section = ('<p style="color:var(--text2);margin-bottom:1.8rem">No folders '
                          'awaiting review. New manual exports appear here after the next '
                          'library scan.</p>')

    # --- Section 2: flagged finals ---
    def _grade_html(run: dict) -> str:
        score = run.get("claude_score")
        if score is not None:
            return _score_pill(score)
        cj = run.get("claude_json") or ""
        if '"error"' in cj:
            return ('<span style="border:1px solid #f85149;color:#f85149;border-radius:3px;'
                    'padding:0 5px;font-size:.72rem">grade failed</span>')
        return ('<span style="border:1px solid #e3b341;color:#e3b341;border-radius:3px;'
                'padding:0 5px;font-size:.72rem">grading…</span>')

    def _final_card(run: dict) -> str:
        t = run["target"]
        fn = run["filename"]
        rel = f"_processed/{fn}"
        view_url = f"/fits/{_q(t)}/{_uparse.quote(rel, safe='/')}"
        prev_url = f"/fits-preview/{_q(t)}/{_uparse.quote(rel, safe='/')}"
        try:
            flow = _json.loads(run.get("flow_json") or "[]")
        except Exception:
            flow = []
        st = (run.get("source_type") or "").upper()
        st_tag = (f'<span style="border:1px solid #58a6ff;color:#58a6ff;border-radius:3px;'
                  f'padding:0 5px;font-size:.7rem">{st}</span>') if st else ""
        report_url = f"/manual-report/{run['id']}"
        return f"""
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:9px;overflow:hidden">
        <a href="{report_url}"><img src="{prev_url}" loading="lazy"
           style="width:100%;height:150px;object-fit:cover;border-radius:9px 9px 0 0;display:block"></a>
        <div style="padding:.55rem .65rem">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:.4rem">
            <a href="/target/{_q(t)}" style="font-weight:600;font-size:.84rem">{t}</a>
            <a href="{report_url}" style="text-decoration:none">{_grade_html(run)}</a>
          </div>
          <div style="color:var(--text2);font-size:.72rem;margin-top:.2rem;white-space:nowrap;
               overflow:hidden;text-overflow:ellipsis" title="{fn}">{st_tag} {fn}</div>
          {_manual_recipe_html(flow, run.get("n_steps") or 0, run.get("summary"))}
          <div style="display:flex;gap:.4rem;margin-top:.5rem">
            <a href="{report_url}" style="flex:1;text-align:center;background:var(--bg);
              border:1px solid var(--border);color:var(--text);border-radius:5px;padding:.25rem;
              font-size:.72rem;text-decoration:none">report</a>
            <button onclick="reopenFolder(this,'{_q(t)}')" style="flex:1;
              background:var(--bg);border:1px solid var(--border);color:var(--text2);
              border-radius:5px;padding:.25rem;font-size:.72rem;cursor:pointer">re-review</button>
          </div>
        </div>
      </div>"""

    graded = sum(1 for r in runs if r.get("claude_score") is not None)
    if runs:
        cards = "".join(_final_card(r) for r in runs)
        finals_section = f"""
    <h2 style="font-size:1.05rem;margin:1rem 0 .6rem">Flagged finals
      <span style="color:var(--text2);font-weight:400;font-size:.82rem">· {len(runs)} · {graded} graded</span></h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:.7rem">
      {cards}
    </div>"""
    else:
        finals_section = ('<h2 style="font-size:1.05rem;margin:1rem 0 .6rem">Flagged finals</h2>'
                          '<p style="color:var(--text2)">None yet — review a folder above and '
                          'flag its final image.</p>')

    # --- Section 3: skipped folders (collapsible reopen) ---
    skipped_section = ""
    if skipped:
        chips = " ".join(
            f'<button onclick="reopenFolder(this,\'{_q(t)}\')" '
            f'style="background:var(--bg2);border:1px solid var(--border);color:var(--text2);'
            f'border-radius:5px;padding:.2rem .5rem;font-size:.74rem;cursor:pointer">{t} ↺</button>'
            for t in skipped
        )
        skipped_section = f"""
    <details style="margin-top:1.6rem">
      <summary style="cursor:pointer;color:var(--text2);font-size:.85rem">Skipped folders ({len(skipped)})</summary>
      <div style="display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.6rem">{chips}</div>
    </details>"""

    body = f"""
  <div style="max-width:1100px;margin:0 auto;padding:1rem">
    <h1 style="margin:0 0 .3rem;font-size:1.3rem">Manual Processing</h1>
    <div style="color:var(--text2);font-size:.82rem;margin-bottom:1.2rem">
      Review a folder, flag the one hand-processed final, and Claude grades it.</div>
    {review_section}
    {finals_section}
    {skipped_section}
  </div>
<script>
async function reopenFolder(btn, target) {{
  btn.disabled = true;
  try {{
    await fetch('/manual-processing/reopen', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{target}})}});
  }} catch (e) {{}}
  location.reload();
}}
</script>"""
    return _shell("Manual Processing — SeeStar", body)


def manual_folder_page(target: str) -> str:
    """Review one folder: show candidate files, let Henry flag one or more finals
    (e.g. an RGB and an HSO version), then finish the folder."""
    from nas_server.manual_capture import folder_candidates
    from nas_server.database import list_manual_runs

    cands = folder_candidates(target, parse=True)
    flagged = list_manual_runs(target)

    def _q(t: str) -> str:
        return _uparse.quote(t, safe="")

    qt = _q(target)

    def _cand_card(c: dict) -> str:
        fn = c["filename"]
        rel = f"_processed/{fn}"
        view_url = f"/fits/{qt}/{_uparse.quote(rel, safe='/')}"
        prev_url = f"/fits-preview/{qt}/{_uparse.quote(rel, safe='/')}"
        st = (c.get("source_type") or "").upper()
        st_tag = (f'<span style="border:1px solid #58a6ff;color:#58a6ff;border-radius:3px;'
                  f'padding:0 5px;font-size:.7rem">{st}</span>')
        recipe = _manual_recipe_html(c.get("flow") or [], c.get("n_steps") or 0, c.get("summary"))
        fn_js = fn.replace("\\", "\\\\").replace("'", "\\'")
        return f"""
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:9px;overflow:hidden">
        <a href="{view_url}"><img src="{prev_url}" loading="lazy"
           style="width:100%;height:160px;object-fit:cover;border-radius:9px 9px 0 0;display:block"></a>
        <div style="padding:.55rem .65rem">
          <div style="font-weight:600;font-size:.8rem;word-break:break-all">{fn}</div>
          <div style="display:flex;gap:.3rem;align-items:center;margin-top:.25rem">
            {st_tag}<span style="color:var(--text2);font-size:.72rem">{c.get('size_mb')} MB</span>
          </div>
          {recipe}
          <button onclick="flagFinal(this,'{fn_js}')" style="margin-top:.6rem;width:100%;
            background:#238636;border:1px solid #2ea043;color:#fff;border-radius:6px;
            padding:.35rem;font-size:.8rem;font-weight:600;cursor:pointer">★ This is the final</button>
        </div>
      </div>"""

    if cands:
        grid = (f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));'
                f'gap:.7rem">{"".join(_cand_card(c) for c in cands)}</div>')
    elif flagged:
        grid = ('<p style="color:var(--text2)">No remaining candidates. '
                'Finish the folder, or flag more from another folder.</p>')
    else:
        grid = ('<p style="color:var(--text2)">No candidate files in this folder. '
                'Skip it to clear it from the queue.</p>')

    # Strip of finals already flagged for this folder (lets Henry flag several —
    # e.g. an RGB and an HSO version — before finishing the folder).
    flagged_strip = ""
    if flagged:
        def _flag_chip(r: dict) -> str:
            fn = r["filename"]
            rel = f"_processed/{fn}"
            prev = f"/fits-preview/{qt}/{_uparse.quote(rel, safe='/')}?stf=0"
            report = f"/manual-report/{r['id']}"
            score = r.get("claude_score")
            score_html = (f'<a href="{report}" style="color:#3fb950;font-size:.72rem;'
                          f'text-decoration:none">{score}/10 →</a>'
                          if score is not None else
                          '<span style="color:var(--text2);font-size:.72rem">grading…</span>')
            return f"""
        <div style="background:var(--bg2);border:1px solid #2ea043;border-radius:8px;overflow:hidden">
          <a href="{report}"><img src="{prev}" loading="lazy"
             style="width:100%;height:90px;object-fit:cover;display:block"></a>
          <div style="padding:.35rem .5rem">
            <div style="font-size:.72rem;word-break:break-all;color:var(--text)">★ {fn}</div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:.2rem">
              {score_html}
              <button onclick="unflagFinal(this,{r['id']})" style="background:none;border:none;
                color:var(--text2);font-size:.72rem;cursor:pointer;text-decoration:underline">remove</button>
            </div>
          </div>
        </div>"""
        flagged_strip = (
            f'<div style="margin:.4rem 0 1.2rem">'
            f'<div style="color:#3fb950;font-size:.82rem;font-weight:600;margin-bottom:.4rem">'
            f'★ {len(flagged)} final{"s" if len(flagged) != 1 else ""} flagged in this folder</div>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.5rem">'
            f'{"".join(_flag_chip(r) for r in flagged)}</div></div>')

    if flagged:
        footer_btn = (
            f'<button onclick="finishFolder(this)" style="background:#238636;border:1px solid #2ea043;'
            f'color:#fff;border-radius:6px;padding:.4rem .9rem;font-size:.8rem;font-weight:600;cursor:pointer">'
            f'✓ Done — {len(flagged)} flagged</button>')
    else:
        footer_btn = (
            '<button onclick="skipFolder(this)" style="background:var(--bg2);border:1px solid var(--border);'
            'color:var(--text2);border-radius:6px;padding:.4rem .8rem;font-size:.8rem;cursor:pointer">'
            'Skip — no manual final here</button>')

    body = f"""
  <div style="max-width:1100px;margin:0 auto;padding:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.6rem;margin-bottom:1rem">
      <div>
        <a href="/manual-processing" style="font-size:.8rem;color:var(--text2)">← Review queue</a>
        <h1 style="margin:.2rem 0 0;font-size:1.25rem">{target}</h1>
        <div style="color:var(--text2);font-size:.82rem">
          {len(cands)} candidate{'s' if len(cands) != 1 else ''} in _processed/ ·
          flag every final you want to keep</div>
      </div>
      {footer_btn}
    </div>
    {flagged_strip}
    {grid}
  </div>
<script>
const TARGET = {target!r};
async function flagFinal(btn, filename) {{
  btn.disabled = true; btn.textContent = 'Flagging…';
  try {{
    await fetch('/manual-processing/flag', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{target: TARGET, filename}})}});
  }} catch (e) {{}}
  location.reload();
}}
async function unflagFinal(btn, runId) {{
  btn.disabled = true; btn.textContent = 'removing…';
  try {{
    await fetch('/manual-processing/unflag', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{run_id: runId}})}});
  }} catch (e) {{}}
  location.reload();
}}
async function finishFolder(btn) {{
  btn.disabled = true; btn.textContent = 'Saving…';
  try {{
    await fetch('/manual-processing/done', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{target: TARGET}})}});
  }} catch (e) {{}}
  location.href = '/manual-processing';
}}
async function skipFolder(btn) {{
  btn.disabled = true;
  try {{
    await fetch('/manual-processing/skip', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{target: TARGET}})}});
  }} catch (e) {{}}
  location.href = '/manual-processing';
}}
</script>"""
    return _shell(f"Manual: {target} — SeeStar", body)


def manual_report_page(run_id: int) -> str:
    """Full Claude assessment for a single flagged manual final: preview, overall
    + per-category scores, issues, suggestions, and the PixInsight recipe."""
    from nas_server.database import get_manual_run

    run = get_manual_run(run_id)
    if not run:
        return _shell("Manual report — SeeStar",
                      '<div style="max-width:800px;margin:2rem auto;padding:1rem">'
                      '<p style="color:var(--text2)">No manual run found.</p>'
                      '<a href="/manual-processing">← Review queue</a></div>')

    def _q(t: str) -> str:
        return _uparse.quote(t, safe="")

    t = run["target"]
    fn = run["filename"]
    qt = _q(t)
    rel = f"_processed/{fn}"
    view_url = f"/fits/{qt}/{_uparse.quote(rel, safe='/')}"
    prev_url = f"/fits-preview/{qt}/{_uparse.quote(rel, safe='/')}?stf=0"

    try:
        scores = _json.loads(run.get("claude_json") or "{}")
    except Exception:
        scores = {}
    try:
        flow = _json.loads(run.get("flow_json") or "[]")
    except Exception:
        flow = []

    overall = run.get("claude_score")
    if overall is None:
        overall = scores.get("overall")

    if "error" in scores:
        grade_block = ('<p style="color:#f85149">Grading failed — '
                       f'{scores.get("error")}. '
                       f'<a href="/manual-processing">retry from the queue</a></p>')
    elif overall is None:
        grade_block = '<p style="color:#e3b341">Grading in progress…</p>'
    else:
        sub_pills = " ".join(
            f'<span style="background:{"#3fb95022" if v >= 7 else "#e3b34122" if v >= 5 else "#f8514922"};'
            f'border:1px solid {"#3fb950" if v >= 7 else "#e3b341" if v >= 5 else "#f85149"};'
            f'border-radius:4px;padding:2px 8px;font-size:.78rem">{k.replace("_"," ")} {v}/10</span>'
            for k, v in scores.items()
            if isinstance(v, (int, float)) and k not in ("overall", "input_tokens", "output_tokens")
        )
        issues = scores.get("issues") or []
        suggestions = scores.get("suggestions") or []
        issues_html = "".join(
            f'<li style="margin-bottom:.3rem;color:var(--text2)">{i}</li>' for i in issues)
        suggestions_html = "".join(
            f'<li style="margin-bottom:.3rem;color:var(--text2)">{s}</li>' for s in suggestions)
        grade_block = f"""
    <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.8rem">
      <span style="font-size:.9rem;color:var(--text2)">Overall</span>{_score_pill(overall)}
      {sub_pills}
    </div>
    {f'<p style="font-weight:600;margin:.8rem 0 .3rem">Issues</p><ul style="padding-left:1.2rem;font-size:.86rem">{issues_html}</ul>' if issues_html else ""}
    {f'<p style="font-weight:600;margin:.9rem 0 .3rem">Suggestions</p><ul style="padding-left:1.2rem;font-size:.86rem">{suggestions_html}</ul>' if suggestions_html else ""}"""

    st = (run.get("source_type") or "").upper()
    st_tag = (f'<span style="border:1px solid #58a6ff;color:#58a6ff;border-radius:3px;'
              f'padding:0 5px;font-size:.72rem">{st}</span>') if st else ""
    graded_at = run.get("graded_at") or ""

    body = f"""
  <div style="max-width:1000px;margin:0 auto;padding:1.2rem 1rem">
    <a href="/manual-processing" style="font-size:.8rem;color:var(--text2)">← Manual processing</a>
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.5rem;margin:.3rem 0 1rem">
      <h1 style="margin:0;font-size:1.3rem"><a href="/target/{qt}" style="color:inherit">{t}</a>
        <span style="color:#bc8cff;font-size:.85rem;font-weight:400;margin-left:.4rem">manual final</span></h1>
      {_score_pill(overall) if overall is not None else ""}
    </div>
    <div style="display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:1.2rem;align-items:start">
      <a href="{view_url}"><img src="{prev_url}" loading="lazy"
         style="width:100%;border-radius:8px;display:block;background:var(--bg)"></a>
      <div>
        <div style="font-weight:600;font-size:.86rem;word-break:break-all;margin-bottom:.3rem">{fn}</div>
        <div style="display:flex;gap:.4rem;align-items:center;margin-bottom:.6rem">
          {st_tag}<span style="color:var(--text2);font-size:.74rem">{run.get('size_mb') or ''}{' MB' if run.get('size_mb') else ''}
          {('· graded ' + _fmt_mst(graded_at)) if graded_at else ''}</span>
        </div>
        {grade_block}
        <div style="margin-top:1rem">{_manual_recipe_html(flow, run.get('n_steps') or 0, run.get('summary'))}</div>
        <button onclick="unflagFinal({run_id})" style="margin-top:1rem;background:var(--bg2);
          border:1px solid var(--border);color:var(--text2);border-radius:6px;padding:.35rem .8rem;
          font-size:.78rem;cursor:pointer">Remove this final</button>
      </div>
    </div>
  </div>
<script>
async function unflagFinal(runId) {{
  if (!confirm('Remove this flagged final?')) return;
  try {{
    await fetch('/manual-processing/unflag', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{run_id: runId}})}});
  }} catch (e) {{}}
  location.href = '/manual-processing';
}}
</script>"""
    return _shell(f"Manual report: {t} — SeeStar", body)


def gallery_page() -> str:
    """Ranked gallery of best finals — auto-pipeline best runs plus Henry's
    hand-processed manual finals (tagged 'manual')."""
    from nas_server.database import get_worklist, list_manual_runs
    from nas_server.folio_generator import get_hero

    def _q(t: str) -> str:
        return _uparse.quote(t, safe="")

    items = []
    for r in get_worklist():
        if not r.get("best_run_id"):
            continue
        t = r["target"]
        hero = get_hero(t)
        if hero and hero.get("chosen_by") == "user" and hero.get("run_id"):
            run_id = hero["run_id"]
            score = hero.get("overall_score")
            out_path = hero.get("output_path") or r["best_output_path"]
            user_pick = True
        else:
            run_id = r["best_run_id"]
            score = r["best_overall"]
            out_path = r["best_output_path"]
            user_pick = False
        items.append({
            "target": t, "score": score, "type": r["type"], "user_pick": user_pick,
            "manual": False, "thumb": _worklist_thumb_url(t, out_path),
            "report_url": f"/report/{_q(t)}/{run_id}",
        })

    # Manual finals: Henry's hand-processed exports, graded the same way.
    for m in list_manual_runs():
        if m.get("claude_score") is None:
            continue
        t = m["target"]
        rel = f"_processed/{m['filename']}"
        items.append({
            "target": t, "score": m["claude_score"], "type": "manual", "user_pick": False,
            "manual": True,
            "thumb": f"/fits-preview/{_q(t)}/{_uparse.quote(rel, safe='/')}?stf=0",
            "report_url": f"/manual-report/{m['id']}",
        })

    items.sort(key=lambda d: -(d["score"] if d["score"] is not None else 0))

    cards = ""
    for it in items:
        t = it["target"]
        qt = _q(t)
        thumb = it["thumb"]
        report_url = it["report_url"]
        img_html = (f'<a href="{report_url}"><img src="{thumb}" loading="lazy" '
                    f'style="width:100%;height:160px;object-fit:cover;border-radius:6px 6px 0 0;'
                    f'display:block"></a>'
                    if thumb else
                    f'<a href="{report_url}" style="display:flex;height:160px;'
                    f'align-items:center;justify-content:center;background:var(--bg);'
                    f'border-radius:6px 6px 0 0;color:var(--text2);font-size:.8rem">no preview</a>')
        score = (_score_pill(it["score"]) if it["score"] is not None
                 else '<span style="color:var(--text2)">—</span>')
        if it["manual"]:
            tag = ('<span title="hand-processed by Henry" style="margin-left:.3rem;color:#bc8cff;'
                   'font-size:.7rem;border:1px solid #bc8cff;border-radius:3px;padding:0 4px">manual</span>')
        else:
            tag = ('<span title="hand-picked hero" style="margin-left:.3rem">★</span>'
                   if it["user_pick"] else "")
        cards += f"""
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;overflow:hidden">
        {img_html}
        <div style="padding:.5rem .6rem">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:.4rem">
            <a href="/target/{qt}" style="font-weight:600;font-size:.86rem;white-space:nowrap;
               overflow:hidden;text-overflow:ellipsis">{t}{tag}</a>{score}
          </div>
          <div style="color:var(--text2);font-size:.74rem;margin-top:.2rem">
            {it["type"] or "—"} · <a href="{report_url}" style="font-size:.74rem">report</a>
          </div>
        </div>
      </div>"""

    body = f"""
<div style="max-width:1200px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.6rem;margin-bottom:.3rem">Best Finals Gallery</h1>
  <p style="color:var(--text2);margin-bottom:1.8rem;font-size:.88rem">
    Best finals ranked by score — auto-pipeline results plus
    <span style="color:#bc8cff">manual</span> hand-processed exports.
    ★ marks a hand-picked hero, set from any run's report page.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.9rem">
    {cards or '<div style="color:var(--text2);padding:1rem">No processed targets yet.</div>'}
  </div>
</div>"""
    return _shell("Gallery", body)


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------

def home_page() -> str:
    from nas_server.database import get_global_story_stats, get_processing_runs
    from nas_server.queue_manager import get_queue
    from nas_server.auto_process import get_all_autoprocess_statuses
    from nas_server.stacker import get_all_stack_statuses

    stats = get_global_story_stats()
    runs = get_processing_runs(limit=5)
    queue = get_queue()
    active_ap = [s for s in get_all_autoprocess_statuses()
                 if s.get("phase") not in ("done", "error", None)]
    active_st = [s for s in get_all_stack_statuses() if s.get("running")]

    # Stat cards
    cards_html = ""
    card_data = [
        ("Targets", stats.get("total_targets", 0), "#58a6ff"),
        ("Total subs", f"{stats.get('total_subs', 0):,}", "#3fb950"),
        ("Integration", f"{stats.get('total_hours', 0):.1f}h", "#e3b341"),
        ("Stacked", stats.get("total_stacked", 0), "#bc8cff"),
    ]
    for label, val, color in card_data:
        cards_html += f"""
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
                    padding:1.2rem 1.5rem;min-width:140px">
          <div style="font-size:1.8rem;font-weight:700;color:{color}">{val}</div>
          <div style="color:var(--text2);font-size:.82rem;margin-top:.3rem">{label}</div>
        </div>"""

    # Active jobs
    active_html = ""
    for s in active_ap:
        active_html += (f'<div style="color:var(--green)">⚙️ AutoProcess: '
                        f'<b>{s["target"]}</b> — {s.get("phase","running")}</div>')
    for s in active_st:
        active_html += (f'<div style="color:#e3b341">⚙️ Stack: '
                        f'<b>{s["target"]}</b> — {s.get("phase","running")} '
                        f'({s.get("elapsed_human","?")} elapsed)</div>')
    if queue:
        active_html += (f'<div style="color:var(--text2)">'
                        f'📋 {len(queue)} job(s) pending in queue</div>')
    if not active_html:
        active_html = '<div style="color:var(--text2)">No active jobs — queue idle</div>'

    # Recent runs table
    runs_rows = ""
    for r in runs:
        ts = _fmt_mst(r.get("finished_at") or "")
        target = r.get("target", "")
        wf = r.get("workflow", "")
        elapsed = r.get("elapsed_s", 0)
        fs = r.get("final_scores") or {}
        score = _score_pill(fs.get("overall"))
        runs_rows += (f'<tr><td><a href="/target/{_uparse.quote(target, safe="")}">{target}</a></td>'
                      f'<td style="color:var(--text2)">{wf}</td>'
                      f'<td style="color:var(--text2)">{ts}</td>'
                      f'<td style="color:var(--text2)">{elapsed:.0f}s</td>'
                      f'<td>{score}</td>'
                      f'<td><a href="/report/{target}">runs</a></td></tr>')

    first = stats.get("first_date", "")[:10]
    last = stats.get("last_date", "")[:10]

    body = f"""
<div style="max-width:1100px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.6rem;margin-bottom:.3rem">SeeStar Observatory</h1>
  <p style="color:var(--text2);margin-bottom:1.5rem">
    {first} – {last} · <a href="/targets">view all targets</a>
  </p>

  <div style="display:flex;flex-wrap:wrap;gap:1rem;margin-bottom:2rem">
    {cards_html}
  </div>

  <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
              padding:1rem 1.2rem;margin-bottom:2rem;font-size:.88rem;line-height:1.8">
    <div style="font-weight:600;margin-bottom:.5rem">Active</div>
    {active_html}
  </div>

  <h2 style="font-size:1.1rem;margin-bottom:.8rem">Recent Processing Runs</h2>
  <table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">
      <th style="text-align:left;padding:.4rem .6rem">Target</th>
      <th style="text-align:left;padding:.4rem .6rem">Workflow</th>
      <th style="text-align:left;padding:.4rem .6rem">Finished</th>
      <th style="text-align:left;padding:.4rem .6rem">Duration</th>
      <th style="text-align:left;padding:.4rem .6rem">Score</th>
      <th style="text-align:left;padding:.4rem .6rem"></th>
    </tr></thead>
    <tbody>{runs_rows or '<tr><td colspan="6" style="color:var(--text2);padding:.6rem">No runs yet</td></tr>'}</tbody>
  </table>

  <div style="margin-top:2rem;font-size:.82rem;color:var(--text2);display:flex;gap:1.5rem">
    <a href="/targets">All Targets →</a>
    <a href="/queue-view">Queue →</a>
    <a href="/learning-view">Tool History →</a>
    <a href="/story">Story →</a>
  </div>
</div>"""

    return _shell("SeeStar Observatory", body)


# ---------------------------------------------------------------------------
# Targets grid
# ---------------------------------------------------------------------------

def targets_page() -> str:
    from nas_server.database import get_story_data

    targets = get_story_data()

    cards = ""
    for t in targets:
        name = t["target"]
        stage = t.get("pipeline_stage", "captured")
        hours = round((t.get("total_hours") or 0), 1)
        score = (t.get("latest_scores") or {}).get("overall")
        preview = t.get("preview_filename")
        img_html = ""
        if preview:
            img_html = (f'<img src="/image/{name}/{preview}" loading="lazy" '
                        f'style="width:100%;height:120px;object-fit:cover;'
                        f'border-radius:6px 6px 0 0;display:block">')
        else:
            img_html = ('<div style="width:100%;height:120px;background:var(--bg3);'
                        'border-radius:6px 6px 0 0;display:flex;align-items:center;'
                        'justify-content:center;color:var(--text2);font-size:1.5rem">🌌</div>')

        cards += f"""
        <a href="/target/{_uparse.quote(name, safe='')}" style="color:inherit;text-decoration:none">
          <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
                      overflow:hidden;transition:border-color .15s"
               onmouseover="this.style.borderColor='var(--accent)'"
               onmouseout="this.style.borderColor='var(--border)'">
            {img_html}
            <div style="padding:.7rem .8rem">
              <div style="font-weight:600;font-size:.9rem;white-space:nowrap;
                          overflow:hidden;text-overflow:ellipsis">{name}</div>
              <div style="display:flex;justify-content:space-between;
                          align-items:center;margin-top:.4rem">
                {_stage_badge(stage)}
                <span style="color:var(--text2);font-size:.78rem">{hours}h</span>
                {_score_pill(score) if score else ''}
              </div>
            </div>
          </div>
        </a>"""

    body = f"""
<div style="max-width:1200px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.4rem;margin-bottom:1.2rem">
    Targets <span style="color:var(--text2);font-size:.9rem;font-weight:400">
    ({len(targets)} total)</span>
  </h1>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem">
    {cards or '<p style="color:var(--text2)">No targets yet.</p>'}
  </div>
</div>"""

    return _shell("Targets — SeeStar", body)


# ---------------------------------------------------------------------------
# Queue live view
# ---------------------------------------------------------------------------

def _queue_table_rows() -> str:
    from nas_server.queue_manager import get_queue
    from nas_server.auto_process import get_all_autoprocess_statuses
    from nas_server.stacker import get_all_stack_statuses

    rows = ""

    # Active jobs first
    for s in get_all_autoprocess_statuses():
        if s.get("phase") in ("done", "error", "aborted", None):
            continue
        _worker_label = f'🖥 {s["worker"]}' if s.get("worker") else "active"
        _tgt_enc = _uparse.quote(s["target"], safe="")
        _abort_btn = (f'<button onclick="abortJob(\'{_tgt_enc}\')" '
                      f'style="background:none;border:1px solid var(--red,#f85149);cursor:pointer;'
                      f'color:var(--red,#f85149);font-size:.8rem;border-radius:4px;'
                      f'padding:.1rem .5rem;margin-left:.5rem" title="Abort this job">⏹ abort</button>')
        rows += (f'<tr style="background:rgba(63,185,80,.08)">'
                 f'<td style="padding:.5rem .8rem">▶ {s["target"]}</td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">'
                 f'{s.get("workflow","autoprocess")}</td>'
                 f'<td style="padding:.5rem .8rem"><span style="color:var(--green)">'
                 f'{s.get("phase","running")}</span></td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">{_worker_label}{_abort_btn}</td></tr>')

    for s in get_all_stack_statuses():
        if not s.get("running"):
            continue
        rows += (f'<tr style="background:rgba(227,179,65,.08)">'
                 f'<td style="padding:.5rem .8rem">▶ {s["target"]}</td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">stack</td>'
                 f'<td style="padding:.5rem .8rem"><span style="color:#e3b341">'
                 f'{s.get("phase","running")} ({s.get("elapsed_human","?")})</span></td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">active</td></tr>')

    # Pending queue
    for item in get_queue():
        pos = item["position"]
        target = item["target"]
        jtype = item.get("job_type", "process")
        wf = item.get("workflow") or item.get("engine", "")
        exp = " +exp" if item.get("experiment_mode") else ""
        del_btn = (f'<button onclick="deleteJob({pos})" '
                   f'style="background:none;border:none;cursor:pointer;color:var(--text2);'
                   f'font-size:.85rem;padding:.1rem .4rem" title="Remove from queue">✕</button>')
        rows += (f'<tr>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">#{pos} {target}</td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">{jtype}: {wf}{exp}</td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">pending</td>'
                 f'<td style="padding:.5rem .8rem;color:var(--text2)">{del_btn}</td></tr>')

    if not rows:
        rows = ('<tr><td colspan="4" style="padding:1rem;color:var(--text2);text-align:center">'
                'Queue is empty — no active or pending jobs</td></tr>')
    return rows


def queue_rows_partial() -> str:
    return _queue_table_rows()


def queue_page() -> str:
    from nas_server.database import get_targets
    from nas_server.queue_manager import is_paused, is_restart_pending
    all_targets = [t["target"] for t in get_targets()]
    target_options = "\n".join(f'<option value="{t}">' for t in sorted(all_targets))

    paused = is_paused()
    restart_pending = is_restart_pending()

    if restart_pending:
        ctrl_banner = ('<div style="background:#2d2000;border:1px solid #7d5a00;border-radius:6px;'
                       'padding:.6rem 1rem;margin-bottom:1rem;font-size:.85rem;color:#e3b341">'
                       '⏳ Restart pending — service will restart after the current job finishes.</div>')
    elif paused:
        ctrl_banner = ('<div style="background:#1a2a1a;border:1px solid #3a6b3a;border-radius:6px;'
                       'padding:.6rem 1rem;margin-bottom:1rem;font-size:.85rem;color:#7ee787">'
                       '⏸ Queue is paused — no new jobs will start until resumed.</div>')
    else:
        ctrl_banner = ''

    pause_btn = (
        '<button onclick="queueAction(\'resume\')" '
        'style="background:#1a3a1a;border:1px solid #3a6b3a;color:#7ee787;'
        'padding:.4rem .9rem;border-radius:5px;cursor:pointer;font-size:.82rem">▶ Resume</button>'
        if paused else
        '<button onclick="queueAction(\'pause\')" '
        'style="background:var(--bg2);border:1px solid var(--border);color:var(--text2);'
        'padding:.4rem .9rem;border-radius:5px;cursor:pointer;font-size:.82rem">⏸ Pause</button>'
    )
    restart_btn = (
        '<button disabled style="background:var(--bg2);border:1px solid var(--border);'
        'color:var(--text2);padding:.4rem .9rem;border-radius:5px;font-size:.82rem;opacity:.5">'
        '⏳ Restart pending…</button>'
        if restart_pending else
        '<button onclick="queueAction(\'restart\')" '
        'style="background:var(--bg2);border:1px solid var(--border);color:var(--text2);'
        'padding:.4rem .9rem;border-radius:5px;cursor:pointer;font-size:.82rem" '
        'title="Pause queue, wait for active job to finish, then restart the service">🔄 Graceful Restart</button>'
    )

    rows = _queue_table_rows()

    # ── Remote worker status badges ──────────────────────────────────────────
    _worker_badges = ""
    try:
        from nas_server.config import settings as _cfg
        from nas_server.worker_client import ping as _wping
        from nas_server.database import get_worker_enabled as _wenabled
        for _w in _cfg.get("remote_workers", []):
            if not _w.get("enabled", True):
                continue
            _name = _w.get("name", "worker")
            _dispatch_on = _wenabled(_name)
            if not _dispatch_on:
                _color, _label, _disk, _job_txt = "#6b7280", "dispatch off", "", ""
            else:
                _h = _wping(_w["url"])
                _online = bool(_h and _h.get("nas_mounted"))
                _busy   = bool(_h and _h.get("status") == "busy")
                if _busy:
                    _color, _label = "#facc15", "busy"
                elif _online:
                    _color, _label = "#4ade80", "online"
                else:
                    _color, _label = "#6b7280", "offline"
                _disk = (f' · {_h["disk_free_gb"]:.0f} GB free' if _h and "disk_free_gb" in _h else "")
                _job_txt = (f' · {_h["job_id"]}' if _h and _h.get("job_id") else "")
            _toggle_txt = "▶ enable" if not _dispatch_on else "⏸ disable"
            _worker_badges += (
                f'<span style="font-size:.75rem;background:var(--bg3);border-radius:10px;'
                f'padding:3px 10px;border:1px solid var(--border);color:{_color}">'
                f'🖥 {_name}: {_label}{_disk}{_job_txt} '
                f'<a href="#" onclick="toggleWorker(\'{_name}\');return false" '
                f'style="color:var(--text2);text-decoration:none;margin-left:6px;'
                f'border-left:1px solid var(--border);padding-left:6px">{_toggle_txt}</a></span>'
            )
    except Exception:
        pass

    _worker_row = (
        f'<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.75rem">'
        f'{_worker_badges}</div>'
        if _worker_badges else ""
    )

    body = f"""
<div style="max-width:960px;margin:0 auto;padding:2rem 1.5rem">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
              gap:.75rem;margin-bottom:1.2rem">
    <h1 style="font-size:1.4rem;margin:0">Processing Queue</h1>
    <div style="display:flex;gap:.5rem;flex-wrap:wrap" id="queue-ctrl-btns">
      {pause_btn}
      {restart_btn}
      <button onclick="clearStuckJobs()" id="clear-stuck-btn"
        style="background:var(--bg2);border:1px solid var(--border);color:var(--text2);
               padding:.4rem .9rem;border-radius:5px;cursor:pointer;font-size:.82rem"
        title="Force-resolve remote jobs stuck in 'running on laptop' state">🔧 Clear Stuck</button>
    </div>
  </div>
  {_worker_row}
  {ctrl_banner}

  <!-- Add Job form -->
  <details id="add-job-section" style="margin-bottom:1.5rem;background:var(--bg2);
             border:1px solid var(--border);border-radius:8px;padding:.9rem 1.1rem">
    <summary style="cursor:pointer;font-weight:600;font-size:.95rem;list-style:none;
                    display:flex;align-items:center;gap:.5rem">
      <span style="font-size:1.1rem">＋</span> Add Job
    </summary>

    <div style="margin-top:1.1rem;display:grid;grid-template-columns:1fr 1fr;
                gap:.9rem 1.2rem" id="add-job-grid">

      <!-- Target -->
      <div style="grid-column:1/-1">
        <label class="qb-label">Target</label>
        <input id="add-job-target" list="qb-targets" placeholder="e.g. M 51"
               class="qb-input" style="width:100%;max-width:340px">
        <datalist id="qb-targets">{target_options}</datalist>
      </div>

      <!-- Job type toggle -->
      <div style="grid-column:1/-1;display:flex;gap:.5rem;flex-wrap:wrap">
        <label class="qb-toggle-label">
          <input type="radio" name="qb-jtype" value="stack" id="qb-jtype-stack" checked
                 onchange="qbToggleType()" onclick="qbToggleType()"> Stack
        </label>
        <label class="qb-toggle-label">
          <input type="radio" name="qb-jtype" value="autoprocess" id="qb-jtype-auto"
                 onchange="qbToggleType()" onclick="qbToggleType()"> Auto-process
        </label>
      </div>

      <!-- Stack options -->
      <div id="qb-stack-opts" style="grid-column:1/-1;display:grid;
           grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:.75rem 1rem">
        <div>
          <label class="qb-label">Engine</label>
          <select id="qb-engine" class="qb-select" onchange="qbEngineChanged(this.value)">
            <option value="siril">Siril</option>
            <option value="pixinsight_wbpp">Siril register + PI stack</option>
            <option value="pixinsight_register">PI Register+Stack</option>
            <option value="imagemm">Image MM</option>
          </select>
        </div>
        <div>
          <label class="qb-label">Cull %</label>
          <select id="qb-cull" class="qb-select">
            <option value="0.10" selected>10% (default)</option>
            <option value="0.00">0% (no cull)</option>
            <option value="0.20">20%</option>
            <option value="0.30">30%</option>
          </select>
        </div>
        <div>
          <label class="qb-label">Framing</label>
          <select id="qb-framing" class="qb-select">
            <option value="min">Min / intersection</option>
            <option value="max">Max / union</option>
          </select>
        </div>
        <div>
          <label class="qb-label">Exp time filter</label>
          <input id="qb-exptime" type="number" placeholder="all" min="1" max="600"
                 class="qb-input">
        </div>
        <div>
          <label class="qb-label">Ecc threshold</label>
          <input id="qb-ecc" type="number" value="0.66" step="0.05" min="0.3" max="1.0"
                 class="qb-input">
        </div>
        <div>
          <label class="qb-label">Gradient reject</label>
          <input id="qb-grad" type="number" value="0.5" step="0.05" min="0" max="1.0"
                 class="qb-input">
        </div>
        <div>
          <label class="qb-label">Sky level factor</label>
          <input id="qb-sky" type="number" value="3.0" step="0.5" min="1.0" max="10.0"
                 class="qb-input">
        </div>
        <div style="display:flex;flex-direction:column;gap:.4rem;padding-top:1.4rem">
          <label class="qb-check-label">
            <input type="checkbox" id="qb-hero"> Hero mode
          </label>
          <label class="qb-check-label">
            <input type="checkbox" id="qb-drizzle"> Drizzle 2×
          </label>
          <label class="qb-check-label" title="Only stack frames captured in EQ (equatorial) mode — excludes alt-az frames that cause diagonal banding when mixed">
            <input type="checkbox" id="qb-eq-only" checked> EQ only
          </label>
          <label class="qb-check-label" title="Automatically queue an auto-process job once stacking completes">
            <input type="checkbox" id="qb-autoprocess-after"> Auto-process after
          </label>
        </div>
      </div>

      <!-- Autoprocess options -->
      <div id="qb-auto-opts" style="display:none;grid-column:1/-1;
           grid-template-columns:1fr 1fr;gap:.75rem 1rem">
        <div>
          <label class="qb-label">Workflow</label>
          <select id="qb-workflow" class="qb-select">
            <option value="auto" selected>auto (detect object type)</option>
            <option value="seestar_fast">seestar_fast (quick preview)</option>
            <option value="seestar_globular">seestar_globular</option>
            <option value="seestar_broadband">seestar_broadband</option>
            <option value="seestar_galaxy">seestar_galaxy</option>
            <option value="seestar_nebula">seestar_nebula</option>
            <option value="quick_default">quick_default</option>
          </select>
        </div>
        <div>
          <label class="qb-label">Source file</label>
          <select id="qb-source-file" class="qb-select">
            <option value="">Latest (auto)</option>
          </select>
          <div id="qb-stack-badge" style="display:none;margin-top:.35rem;font-size:.75rem;
               color:var(--text2);font-family:monospace;white-space:nowrap;overflow:hidden;
               text-overflow:ellipsis"></div>
        </div>
        <div style="grid-column:1/-1;display:flex;flex-direction:column;gap:.4rem">
          <label class="qb-check-label" style="display:inline-flex">
            <input type="checkbox" id="qb-experiment"> Experiment mode
          </label>
          <label class="qb-check-label" style="display:inline-flex">
            <input type="checkbox" id="qb-manual-review"> Manual review
          </label>
          <label class="qb-check-label" style="display:inline-flex">
            <input type="checkbox" id="qb-force-nbn"> Force narrowband normalization
          </label>
          <label class="qb-check-label" style="display:inline-flex">
            <input type="checkbox" id="qb-re-crop"> Re-crop (force fresh crop review)
          </label>
        </div>
        <div style="grid-column:1/-1;border-top:1px solid var(--border,#333);
             padding-top:.55rem;display:flex;gap:.7rem;align-items:center;flex-wrap:wrap">
          <button type="button" onclick="qbAddNbn()" class="qb-btn"
                  style="font-size:.85rem">Add NBN to last run</button>
          <span style="font-size:.75rem;color:var(--text2)">
            Branches the most recent processed run, injects NarrowbandNormalization,
            re-runs the color/curves tail → auto_final_nbn.fit</span>
        </div>
      </div>

      <div style="grid-column:1/-1;display:flex;gap:.7rem;align-items:center;flex-wrap:wrap">
        <button type="button" onclick="qbSubmit()" class="qb-btn-primary">Add to Queue</button>
        <span id="qb-status" style="font-size:.85rem;color:var(--text2)"></span>
      </div>
    </div>
  </details>

  <!-- Queue table -->
  <div id="queue-table"
       hx-get="/queue-view/rows"
       hx-trigger="every 10s"
       hx-target="#queue-tbody"
       hx-swap="innerHTML">
    <table style="width:100%;border-collapse:collapse;font-size:.88rem">
      <thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">
        <th style="text-align:left;padding:.4rem .8rem">Target</th>
        <th style="text-align:left;padding:.4rem .8rem">Job</th>
        <th style="text-align:left;padding:.4rem .8rem">Status</th>
        <th style="text-align:left;padding:.4rem .8rem;width:3rem"></th>
      </tr></thead>
      <tbody id="queue-tbody">{rows}</tbody>
    </table>
  </div>
  <p style="color:var(--text2);font-size:.78rem;margin-top:.8rem">
    Auto-refreshes every 10 seconds.
  </p>
</div>

<script>
function qbToggleType() {{
  // CSS :has() handles show/hide — just trigger source file load for auto-process
  var isStack = document.getElementById('qb-jtype-stack').checked;
  if (!isStack) qbLoadSourceFiles();
}}

var _qbFileCache = {{}};

function qbLoadSourceFiles() {{
  var target = document.getElementById('add-job-target').value.trim();
  var sel = document.getElementById('qb-source-file');
  sel.innerHTML = '<option value="">Latest (auto)</option>';
  qbShowStackBadge(null);
  if (!target) return;
  fetch('/processed/' + encodeURIComponent(target) + '?include_stack_params=true')
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      _qbFileCache = {{}};
      (d.files || []).forEach(function(f) {{
        _qbFileCache[f.filename] = f;
        var label = f.filename;
        if (f.obs_date) label += '  (' + f.obs_date.slice(0,10) + ')';
        if (f.stack_frame_count) label += '  ' + f.stack_frame_count + 'fr';
        var opt = document.createElement('option');
        opt.value = f.filename;
        opt.textContent = label;
        sel.appendChild(opt);
      }});
    }})
    .catch(function() {{}});
}}

function qbShowStackBadge(f) {{
  var wrap = document.getElementById('qb-stack-badge');
  if (!f || (!f.hero && !f.drizzle && f.bottom_pct == null && !f.framing && !f.stack_engine)) {{
    wrap.style.display = 'none'; return;
  }}
  var badges = [];
  if (f.stack_engine) badges.push(f.stack_engine);
  if (f.framing) badges.push('framing:' + f.framing);
  if (f.bottom_pct != null) badges.push('cull ' + Math.round(f.bottom_pct * 100) + '%');
  if (f.drizzle) badges.push('drizzle 2×');
  if (f.hero) badges.push('hero');
  wrap.textContent = badges.join('  ·  ');
  wrap.style.display = 'block';
}}

document.getElementById('qb-source-file').addEventListener('change', function() {{
  qbShowStackBadge(_qbFileCache[this.value] || null);
}});

function qbSubmit() {{
  var target = document.getElementById('add-job-target').value.trim();
  if (!target) {{ document.getElementById('qb-status').textContent = 'Target required'; return; }}
  var isStack = document.getElementById('qb-jtype-stack').checked;
  var status = document.getElementById('qb-status');
  status.textContent = 'Adding…';
  status.style.color = 'var(--text2)';

  var url, method = 'POST';
  if (isStack) {{
    var engine = document.getElementById('qb-engine').value;
    var cull = document.getElementById('qb-cull').value;
    var framing = document.getElementById('qb-framing').value;
    var exptime = document.getElementById('qb-exptime').value;
    var ecc = document.getElementById('qb-ecc').value;
    var grad = document.getElementById('qb-grad').value;
    var sky = document.getElementById('qb-sky').value;
    var hero = document.getElementById('qb-hero').checked;
    var drizzle = (engine === 'pixinsight_register') ? false : document.getElementById('qb-drizzle').checked;
    var eqOnly = document.getElementById('qb-eq-only').checked;
    var apAfter = document.getElementById('qb-autoprocess-after').checked;
    var params = new URLSearchParams({{
      engine: engine, bottom_pct: cull, framing: framing,
      ecc_threshold: ecc, gradient_threshold: grad, sky_level_factor: sky,
      hero: hero, drizzle: drizzle, eq_only: eqOnly,
      cull: parseFloat(cull) > 0
    }});
    if (exptime) params.append('exptime', exptime);
    if (apAfter) params.append('post_autoprocess_workflow', 'auto');
    url = '/stack/' + encodeURIComponent(target) + '?' + params.toString();
  }} else {{
    var wf = document.getElementById('qb-workflow').value;
    var exp = document.getElementById('qb-experiment').checked;
    var mr = document.getElementById('qb-manual-review').checked;
    var fnbn = document.getElementById('qb-force-nbn').checked;
    var recrop = document.getElementById('qb-re-crop').checked;
    var sf = document.getElementById('qb-source-file').value;
    url = '/queue?target=' + encodeURIComponent(target) + '&workflow=' + wf + '&experiment_mode=' + exp + '&manual_review=' + mr + '&force_nbn=' + fnbn + '&re_crop=' + recrop;
    if (sf) url += '&source_file=' + encodeURIComponent(sf);
  }}

  fetch(url, {{method: 'POST'}})
    .then(function(r) {{
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }})
    .then(function(d) {{
      status.textContent = d.message || d.error || 'Queued';
      status.style.color = d.error ? '#f85149' : '#3fb950';
      document.getElementById('add-job-target').value = '';
      document.getElementById('qb-source-file').innerHTML = '<option value="">Latest (auto)</option>';
      qbShowStackBadge(null);
      try {{ htmx.ajax('GET', '/queue-view/rows', {{target:'#queue-tbody', swap:'innerHTML'}}); }} catch(e) {{}}
    }})
    .catch(function(e) {{
      status.textContent = 'Error: ' + e.message;
      status.style.color = '#f85149';
    }});
}}

function qbAddNbn() {{
  var target = document.getElementById('add-job-target').value.trim();
  var status = document.getElementById('qb-status');
  if (!target) {{ status.textContent = 'Target required'; status.style.color = '#f85149'; return; }}
  status.textContent = 'Branching last run for NBN…';
  status.style.color = 'var(--text2)';
  fetch('/narrowband_norm/' + encodeURIComponent(target), {{method: 'POST'}})
    .then(function(r) {{ return r.json().then(function(d) {{ return {{ok: r.ok, d: d}}; }}); }})
    .then(function(res) {{
      if (!res.ok) throw new Error(res.d.detail || ('HTTP error'));
      status.textContent = res.d.message || 'NBN branch queued';
      status.style.color = '#3fb950';
      try {{ htmx.ajax('GET', '/queue-view/rows', {{target:'#queue-tbody', swap:'innerHTML'}}); }} catch(e) {{}}
    }})
    .catch(function(e) {{
      status.textContent = 'Error: ' + e.message;
      status.style.color = '#f85149';
    }});
}}

function qbEngineChanged(engine) {{
  var drizzleRow = document.getElementById('qb-drizzle').closest('label').parentElement;
  if (engine === 'pixinsight_register') {{
    drizzleRow.style.opacity = '0.35';
    drizzleRow.style.pointerEvents = 'none';
    document.getElementById('qb-drizzle').checked = false;
  }} else {{
    drizzleRow.style.opacity = '';
    drizzleRow.style.pointerEvents = '';
  }}
}}

document.getElementById('add-job-target').addEventListener('change', function() {{
  if (!document.getElementById('qb-jtype-stack').checked) qbLoadSourceFiles();
  qbCheckCanonical();
}});

// Default the engine to PI Register+Stack for folio targets that already have a
// canonical reference frame, so cumulative-grid stacks are one click — but leave it
// user-overridable (e.g. pick Siril for a quick stack). Only nudges the default when
// the user hasn't already moved off Siril.
function qbCheckCanonical() {{
  var t = (document.getElementById('add-job-target').value || '').trim();
  if (!t) return;
  fetch('/target/' + encodeURIComponent(t) + '/canonical')
    .then(function(r) {{ return r.ok ? r.json() : null; }})
    .then(function(d) {{
      if (!d || !d.has_reference) return;
      var sel = document.getElementById('qb-engine');
      if (sel.value === 'siril') {{
        sel.value = 'pixinsight_register';
        qbEngineChanged(sel.value);
      }}
    }})
    .catch(function() {{}});
}}

// Pre-fill target from ?target= URL param (e.g. linked from pipeline page)
(function() {{
  var t = new URLSearchParams(window.location.search).get('target');
  if (t) {{
    document.getElementById('add-job-target').value = t;
    document.getElementById('add-job-section').open = true;
    document.getElementById('add-job-section').scrollIntoView({{behavior:'smooth', block:'center'}});
    qbCheckCanonical();
  }}
}})();

function deleteJob(pos) {{
  fetch('/queue/' + pos, {{method: 'DELETE'}})
    .then(function(r) {{ return r.json(); }})
    .then(function() {{
      htmx.trigger('#queue-table', 'htmx:trigger');
    }});
}}

function queueAction(action) {{
  var url = action === 'pause'   ? '/queue/pause'
          : action === 'resume'  ? '/queue/resume'
          : '/admin/restart';
  var confirmMsg = action === 'restart'
    ? 'Pause queue and restart service after current job finishes?' : null;
  if (confirmMsg && !confirm(confirmMsg)) return;
  fetch(url, {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function() {{ window.location.reload(); }});
}}
function clearStuckJobs() {{
  var btn = document.getElementById('clear-stuck-btn');
  if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Clearing…'; }}
  fetch('/queue/clear-stuck', {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      var msg = 'Cleared: ' + (d.cleared || []).join(', ') + '\\nRe-queued: ' + (d.requeued || []).join(', ');
      if (!d.cleared.length && !d.requeued.length) msg = 'No stuck jobs found.';
      alert(msg);
      window.location.reload();
    }})
    .catch(function() {{
      alert('Error clearing stuck jobs.');
      if (btn) {{ btn.disabled = false; btn.textContent = '🔧 Clear Stuck'; }}
    }});
}}
function toggleWorker(name) {{
  fetch('/workers/' + encodeURIComponent(name) + '/toggle', {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{ window.location.reload(); }})
    .catch(function() {{ alert('Error toggling worker.'); }});
}}
function abortJob(tgtEnc) {{
  if (!confirm('Abort this running job? It will stop at the next step boundary.')) return;
  fetch('/autoprocess/' + tgtEnc + '/abort', {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (!d.ok) {{ alert('Could not abort: ' + (d.error || 'unknown')); return; }}
      htmx.trigger('#queue-table', 'htmx:trigger');
    }})
    .catch(function() {{ alert('Error aborting job.'); }});
}}
</script>"""

    css = """
  .qb-label { display: block; font-size: .78rem; color: var(--text2); margin-bottom: .3rem; }
  .qb-input { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
               border-radius: 5px; padding: .35rem .6rem; font-size: .88rem; width: 100%; }
  .qb-input:focus { outline: none; border-color: var(--accent); }
  .qb-select { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
                border-radius: 5px; padding: .35rem .6rem; font-size: .88rem; width: 100%; }
  .qb-select:focus { outline: none; border-color: var(--accent); }
  .qb-check-label { display: flex; align-items: center; gap: .4rem; font-size: .88rem;
                     cursor: pointer; color: var(--text); }
  .qb-toggle-label { display: flex; align-items: center; gap: .35rem; font-size: .88rem;
                      cursor: pointer; padding: .3rem .7rem; border-radius: 5px;
                      border: 1px solid var(--border); color: var(--text2); }
  .qb-toggle-label:has(input:checked) { border-color: var(--accent); color: var(--accent);
                                         background: rgba(88,166,255,.08); }
  /* CSS-driven panel visibility — no JS needed, works instantly on iOS Safari 16+ */
  #add-job-grid:has(#qb-jtype-stack:checked) #qb-stack-opts { display: grid !important; }
  #add-job-grid:has(#qb-jtype-stack:checked) #qb-auto-opts  { display: none  !important; }
  #add-job-grid:has(#qb-jtype-auto:checked)  #qb-stack-opts { display: none  !important; }
  #add-job-grid:has(#qb-jtype-auto:checked)  #qb-auto-opts  { display: grid  !important; }
  .qb-btn-primary { background: var(--accent); color: #0d1117; border: none; border-radius: 6px;
                     padding: .45rem 1.1rem; font-size: .9rem; font-weight: 600; cursor: pointer; }
  .qb-btn-primary:hover { opacity: .85; }
  details > summary::-webkit-details-marker { display: none; }
  @media (max-width: 540px) {
    #add-job-grid { grid-template-columns: 1fr; }
  }
"""
    return _shell("Queue — SeeStar", body, css)


# ---------------------------------------------------------------------------
# Learning / tool leaderboard
# ---------------------------------------------------------------------------

def learning_page() -> str:  # noqa: C901
    import sqlite3 as _sq, json as _j, urllib.parse as _up
    from collections import OrderedDict as _OD
    from nas_server.database import get_all_experiment_steps, get_experiment_stats
    from nas_server.config import settings

    _db_path = settings.get("db_path", str(Path.home() / "seestar_database" / "astro_data.db"))

    # ── Load raw data ─────────────────────────────────────────────────────────
    with _sq.connect(_db_path) as _db:
        _db.row_factory = _sq.Row

        _total_exp   = _db.execute("SELECT COUNT(*) n FROM experiment_results").fetchone()["n"]
        _exp_targets = [r["target"] for r in _db.execute(
            "SELECT DISTINCT target FROM experiment_results ORDER BY target").fetchall()]
        _exp_steps   = get_all_experiment_steps()

        _exp_recent = [dict(r) for r in _db.execute("""
            SELECT id, target, step, variant_id, overall_score, winner,
                   created_at, winning_margin, claude_reasoning, experiment_run_id
            FROM experiment_results ORDER BY created_at DESC LIMIT 160
        """).fetchall()]

        _total_ad   = _db.execute("SELECT COUNT(*) n FROM adaptive_decisions").fetchone()["n"]
        _ad_targets = [r["target_name"] for r in _db.execute(
            "SELECT DISTINCT target_name FROM adaptive_decisions ORDER BY target_name"
        ).fetchall()]

        _ad_rows = [dict(r) for r in _db.execute("""
            SELECT id, run_id, target_name, object_type, phase, step_name,
                   decision_type, chosen_value, rationale, final_score, score_delta, timestamp
            FROM adaptive_decisions ORDER BY timestamp DESC
        """).fetchall()]

    # ── Group experiment results by run_id ────────────────────────────────────
    _exp_runs = _OD()
    for _r in _exp_recent:
        _k = _r.get("experiment_run_id") or f'{_r["target"]}|{_r["step"]}|{_r["created_at"][:10]}'
        if _k not in _exp_runs:
            _exp_runs[_k] = {
                "target": _r["target"], "step": _r["step"],
                "created_at": _r["created_at"], "variants": []
            }
        _exp_runs[_k]["variants"].append(_r)
    # deduplicate runs (max 40 shown in timeline)
    _exp_run_list = list(_exp_runs.values())[:40]

    # ── Group adaptive decisions by run_id + phase ────────────────────────────
    _ad_runs = _OD()
    for _r in _ad_rows:
        _k = (_r["run_id"], _r["phase"])
        if _k not in _ad_runs:
            _ad_runs[_k] = {
                "run_id": _r["run_id"], "target_name": _r["target_name"],
                "object_type": _r["object_type"], "phase": _r["phase"],
                "timestamp": _r["timestamp"], "final_score": _r["final_score"],
                "score_delta": _r["score_delta"], "rationale": "", "decisions": []
            }
        run = _ad_runs[_k]
        if _r["rationale"] and not run["rationale"]:
            run["rationale"] = _r["rationale"]
        if _r["final_score"] is not None:
            run["final_score"] = _r["final_score"]
            run["score_delta"]  = _r["score_delta"]
        run["decisions"].append(_r)
    _ad_run_list = list(_ad_runs.values())

    # ── All targets for filter ────────────────────────────────────────────────
    _all_targets = sorted(set(_exp_targets) | set(_ad_targets))

    # ── Decision type display ─────────────────────────────────────────────────
    _DT_STYLE = {
        "variant_fill":       ("var-fill",  "→"),
        "param_nudge":        ("param-nudge","⚙"),
        "add_step":           ("add-step",  "＋"),
        "skip_step":          ("skip-step", "⊖"),
        "add_step_reverted":  ("revert",    "↩"),
        "flag":               ("flag-d",    "⚑"),
    }

    def _ad_badge(d):
        dt = d["decision_type"]
        cls, icon = _DT_STYLE.get(dt, ("", "·"))
        sn = d["step_name"] or ""
        cv = ""
        if dt == "variant_fill" and d["chosen_value"]:
            try:
                cv = " " + _j.loads(d["chosen_value"])
            except Exception:
                cv = " " + str(d["chosen_value"])
        elif dt == "flag" and d["chosen_value"]:
            try:
                raw = _j.loads(d["chosen_value"])
                cv = " " + raw[:60] + ("…" if len(raw) > 60 else "")
            except Exception:
                cv = ""
        label = f"{icon} {sn}{cv}".strip()
        return f'<span class="ad-badge {cls}" title="{dt}">{label}</span>'

    # ── Build experiment timeline cards ───────────────────────────────────────
    _exp_timeline_html = ""
    for _run in _exp_run_list:
        _tgt = _run["target"]
        _tenc = _up.quote(_tgt, safe="")
        _date = _run["created_at"][:10]
        _step = _run["step"]
        _variants = sorted(_run["variants"],
                            key=lambda x: (-(x["winner"] or 0), -(x["overall_score"] or 0)))
        _winner_id = next((v["variant_id"] for v in _variants if v.get("winner")), None)

        _vbits = ""
        for _v in _variants:
            _is_w = bool(_v.get("winner"))
            _sc   = _v["overall_score"]
            _sc_s = f"{_sc:.1f}" if _sc is not None else "—"
            _mar  = _v.get("winning_margin")
            _mar_s = (f'<span class="erc-margin">{"+" if _is_w else ""}{_mar:.1f}</span>'
                      if _mar is not None else "")
            _vbits += (
                f'<div class="erc-vrow{"  erc-winner" if _is_w else ""}">'
                f'<span class="erc-vname">{"★ " if _is_w else ""}{_v["variant_id"]}</span>'
                f'<span class="erc-vscore">{_sc_s}</span>{_mar_s}</div>'
            )

        _exp_timeline_html += (
            f'<div class="erc-card" data-target="{_tgt}">'
            f'<div class="erc-head">'
            f'<a class="erc-tgt" href="/target/{_tenc}">{_tgt}</a>'
            f'<span class="erc-step">{_step}</span>'
            f'<span class="erc-date">{_date}</span>'
            f'</div>'
            f'<div class="erc-variants">{_vbits}</div>'
            f'</div>'
        )

    # ── Build per-step aggregated stat sections ───────────────────────────────
    _stat_sections_html = ""
    for _step in _exp_steps:
        _stats = get_experiment_stats(_step)
        if not _stats:
            continue
        _total = sum(s["n_runs"] for s in _stats.values())
        _top   = max(_stats, key=lambda v: _stats[v]["win_rate"], default=None)
        _sorted_v = sorted(_stats, key=lambda v: _stats[v]["win_rate"], reverse=True)

        # collect targets that ran this step
        _step_tgts = set()
        for _r in _exp_recent:
            if _r["step"] == _step:
                _step_tgts.add(_r["target"])
        _tgt_attr = " ".join(_step_tgts)

        _rows_html = ""
        for _v in _sorted_v[:12]:
            _s  = _stats[_v]
            _wr = _s["win_rate"]
            _bw = int(_wr * 100)
            _is_top = _v == _top
            _avg    = _s["avg_score"]
            _med    = _s["median_score"]
            _std    = _s["stdev_score"]
            _lo     = _s["min_score"]
            _hi     = _s["max_score"]
            _spd    = _s["score_spread"]
            _wm     = _s["avg_winning_margin"]
            _lm     = _s["avg_losing_margin"]
            _cp     = _s["close_race_pct"]
            _fwhm   = _s["avg_fwhm_delta_pct"]

            if _avg is not None:
                _sc_str = f"{_avg:.1f}"
                if _med is not None:
                    _sc_str += f"&thinsp;/&thinsp;{_med:.1f}"
                if _std is not None:
                    _sc_str += f"&thinsp;(&plusmn;{_std:.1f})"
                if _lo is not None and _hi is not None:
                    _sc_col = ("#4ade80" if _spd is not None and _spd < 1.5 else
                               "#facc15" if _spd is not None and _spd < 3.0 else "#f87171")
                    _sc_str += (f'<br><span style="font-size:.72rem;color:{_sc_col}">'
                                f'[{_lo:.1f}–{_hi:.1f}]</span>')
            else:
                _sc_str = "—"

            _mg_str = (f"+{_wm:.2f}" if _wm is not None else
                       (f"{_lm:.2f}" if _lm is not None else "—"))
            _cp_str = f"{_cp*100:.0f}%" if _cp is not None else "—"
            _fw_str = f"{_fwhm:+.1f}%" if _fwhm is not None else "—"
            _vn_cls = "sv-top" if _is_top else ""

            _rows_html += (
                f'<tr>'
                f'<td class="sv-vname {_vn_cls}"><code>{_v}{"★" if _is_top else ""}</code></td>'
                f'<td class="sv-n">{_s["n_runs"]}</td>'
                f'<td class="sv-bar"><div class="sv-bar-bg"><div class="sv-bar-fill" style="width:{_bw}%"></div></div>'
                f'<span class="sv-pct">{_wr*100:.0f}%</span></td>'
                f'<td class="sv-score">{_sc_str}</td>'
                f'<td class="sv-mg">{_mg_str}</td>'
                f'<td class="sv-cp">{_cp_str}</td>'
                f'<td class="sv-fw">{_fw_str}</td>'
                f'</tr>'
            )

        _stat_sections_html += (
            f'<div class="sv-card" data-step-tgts="{_tgt_attr}">'
            f'<div class="sv-head">'
            f'<span class="sv-step-name">{_step}</span>'
            f'<span class="sv-count">{_total} runs · {len(_step_tgts)} target{"s" if len(_step_tgts)!=1 else ""}</span>'
            f'</div>'
            f'<div class="sv-table-wrap">'
            f'<table class="sv-table">'
            f'<thead><tr class="sv-thead">'
            f'<th>Variant</th><th>Runs</th><th>Win %</th>'
            f'<th>Mean&thinsp;/&thinsp;Median (&plusmn;&sigma;) [lo–hi]</th>'
            f'<th>Margin</th><th>Close</th><th>FWHM&Delta;</th>'
            f'</tr></thead>'
            f'<tbody>{_rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'<p class="sv-legend">Score: mean&thinsp;/&thinsp;median (&plusmn;std) [range]. '
            f'Green range = consistent, red = context-dependent. '
            f'Close = runs where margin &lt; 1pt. Margin = winner minus runner-up.</p>'
            f'</div>'
        )

    # ── Build adaptive decision cards ─────────────────────────────────────────
    _ad_html = ""
    for _run in _ad_run_list:
        _tgt   = _run["target_name"]
        _tenc  = _up.quote(_tgt, safe="")
        _phase = _run["phase"]
        _ph_cls = "ph-lin" if _phase == "linear" else "ph-nl"
        _ph_lbl = "linear" if _phase == "linear" else "non-linear"
        _ts    = _run["timestamp"][:10]

        _fs    = _run["final_score"]
        _sd    = _run["score_delta"]
        _score_html = ""
        if _fs is not None:
            _sd_col = ("#4ade80" if (_sd or 0) > 0.1 else
                       "#f87171" if (_sd or 0) < -0.1 else "var(--text2)")
            _score_html = (
                f'<span class="adc-score">{_fs:.1f}'
                f'<span style="color:{_sd_col};margin-left:.3rem">'
                f'{"+" if (_sd or 0) >= 0 else ""}{(_sd or 0):.1f}</span></span>'
            )

        _badges = "".join(_ad_badge(d) for d in _run["decisions"])
        _rationale = _run["rationale"] or ""
        _rat_html = (
            f'<details class="adc-rat"><summary>Claude\'s reasoning</summary>'
            f'<p class="adc-rat-text">{_rationale}</p></details>'
        ) if _rationale else ""

        _ad_html += (
            f'<div class="adc-card" data-target="{_tgt}">'
            f'<div class="adc-head">'
            f'<a class="adc-tgt" href="/target/{_tenc}">{_tgt}</a>'
            f'<span class="adc-phase {_ph_cls}">{_ph_lbl}</span>'
            f'{_score_html}'
            f'<span class="adc-date">{_ts}</span>'
            f'</div>'
            f'<div class="adc-badges">{_badges}</div>'
            f'{_rat_html}'
            f'</div>'
        )

    # ── Target filter <option> list ───────────────────────────────────────────
    _tgt_opts = '<option value="">All targets</option>' + "".join(
        f'<option value="{t}">{t}</option>' for t in _all_targets
    )

    # ── Assemble page ──────────────────────────────────────────────────────────
    body = f"""
<div class="tl-page">

  <div class="tl-hdr">
    <h1 class="tl-h1">Tools</h1>
    <div class="tl-summary">
      <span class="tl-stat"><strong>{_total_exp}</strong> experiment runs</span>
      <span class="tl-sep">·</span>
      <span class="tl-stat"><strong>{len(_exp_steps)}</strong> steps</span>
      <span class="tl-sep">·</span>
      <span class="tl-stat"><strong>{len(_exp_targets)}</strong> targets</span>
      <span class="tl-sep tl-sep2">·</span>
      <span class="tl-stat"><strong>{_total_ad}</strong> AI decisions</span>
      <span class="tl-sep">·</span>
      <span class="tl-stat"><strong>{len(_ad_targets)}</strong> targets (AI)</span>
    </div>
    <div class="tl-controls">
      <div class="tab-btns">
        <button class="tab-btn tab-active" onclick="switchTab('exp',this)">Experiment History</button>
        <button class="tab-btn" onclick="switchTab('ai',this)">AI Planning</button>
      </div>
      <select class="tl-filter" id="tgt-filter" onchange="filterTarget(this.value)">
        {_tgt_opts}
      </select>
    </div>
  </div>

  <!-- ── Experiment History tab ──────────────────────────────── -->
  <div id="tab-exp" class="tab-pane">

    <section class="tl-section">
      <h2 class="tl-h2">Recent Runs <span class="tl-h2-sub">last {len(_exp_run_list)} experiment batches</span></h2>
      <div class="erc-grid" id="erc-grid">
        {_exp_timeline_html}
      </div>
    </section>

    <section class="tl-section">
      <h2 class="tl-h2">Variant Stats <span class="tl-h2-sub">aggregated win rates across all runs</span></h2>
      <div id="sv-list">
        {_stat_sections_html}
      </div>
    </section>

  </div>

  <!-- ── AI Planning tab ─────────────────────────────────────── -->
  <div id="tab-ai" class="tab-pane" style="display:none">

    <section class="tl-section">
      <h2 class="tl-h2">Planning Runs <span class="tl-h2-sub">{len(_ad_run_list)} decision sets</span></h2>
      <div class="adc-list" id="adc-list">
        {_ad_html}
      </div>
    </section>

  </div>

</div>

<script>
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.style.display = 'none');
  document.getElementById('tab-' + name).style.display = '';
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('tab-active'));
  btn.classList.add('tab-active');
}}
function filterTarget(tgt) {{
  // Experiment run cards
  document.querySelectorAll('#erc-grid .erc-card').forEach(function(c) {{
    c.style.display = (!tgt || c.dataset.target === tgt) ? '' : 'none';
  }});
  // Stat step cards — show if any matching run
  document.querySelectorAll('#sv-list .sv-card').forEach(function(c) {{
    var tgts = (c.dataset.stepTgts || '').split(' ');
    c.style.display = (!tgt || tgts.indexOf(tgt) >= 0) ? '' : 'none';
  }});
  // AI decision cards
  document.querySelectorAll('#adc-list .adc-card').forEach(function(c) {{
    c.style.display = (!tgt || c.dataset.target === tgt) ? '' : 'none';
  }});
}}
</script>"""

    css = """
  .tl-page { max-width: 980px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
  .tl-hdr { margin-bottom: 1.5rem; }
  .tl-h1 { font-size: 1.4rem; margin-bottom: .5rem; }
  .tl-summary { display: flex; flex-wrap: wrap; align-items: center;
                gap: .2rem .5rem; font-size: .82rem; color: var(--text2);
                margin-bottom: .9rem; }
  .tl-sep { color: var(--border); }
  .tl-sep2 { margin: 0 .2rem; }
  .tl-stat strong { color: var(--text); }
  .tl-controls { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; }
  .tab-btns { display: flex; border: 1px solid var(--border); border-radius: 6px;
              overflow: hidden; }
  .tab-btn { background: none; border: none; color: var(--text2); padding: .4rem .9rem;
             font-size: .82rem; cursor: pointer; transition: background .15s, color .15s; }
  .tab-btn:hover { background: var(--bg3); color: var(--text); }
  .tab-btn.tab-active { background: var(--bg3); color: var(--accent); font-weight: 600; }
  .tl-filter { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
               color: var(--text); padding: .35rem .7rem; font-size: .82rem;
               cursor: pointer; min-width: 140px; }
  .tl-section { margin-bottom: 2.5rem; }
  .tl-h2 { font-size: 1rem; font-weight: 700; margin-bottom: .9rem;
           padding-bottom: .4rem; border-bottom: 1px solid var(--border); }
  .tl-h2-sub { font-size: .75rem; font-weight: 400; color: var(--text2);
               margin-left: .5rem; }

  /* Experiment run cards */
  .erc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
              gap: .75rem; }
  .erc-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
              padding: .75rem .9rem; }
  .erc-head { display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap;
              margin-bottom: .5rem; }
  .erc-tgt { font-weight: 600; font-size: .88rem; color: var(--accent);
             text-decoration: none; }
  .erc-tgt:hover { text-decoration: underline; }
  .erc-step { background: var(--bg3); border-radius: 4px; padding: 1px 6px;
              font-size: .74rem; color: var(--text2); }
  .erc-date { margin-left: auto; font-size: .73rem; color: var(--text2); }
  .erc-vrow { display: flex; align-items: center; gap: .4rem; padding: .18rem 0;
              border-bottom: 1px solid var(--bg3); font-size: .8rem; }
  .erc-vrow:last-child { border-bottom: none; }
  .erc-winner { background: #1c2d1e33; border-radius: 3px; padding: .1rem .3rem;
                margin: 0 -.3rem; }
  .erc-vname { flex: 1; color: var(--text2); font-family: monospace; font-size: .78rem; }
  .erc-winner .erc-vname { color: #4ade80; font-weight: 600; }
  .erc-vscore { font-weight: 600; min-width: 28px; text-align: right; font-size: .8rem; }
  .erc-winner .erc-vscore { color: #4ade80; }
  .erc-margin { font-size: .72rem; color: var(--text2); min-width: 32px; text-align: right; }

  /* Per-step stat cards */
  .sv-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
             padding: .9rem 1.1rem; margin-bottom: 1rem; }
  .sv-head { display: flex; justify-content: space-between; align-items: baseline;
             margin-bottom: .7rem; }
  .sv-step-name { font-weight: 700; font-size: .92rem; }
  .sv-count { font-size: .75rem; color: var(--text2); }
  .sv-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .sv-table { border-collapse: collapse; font-size: .8rem; min-width: 560px; width: 100%; }
  .sv-thead th { text-align: left; padding: .3rem .5rem; color: var(--text2);
                 border-bottom: 2px solid var(--border); font-weight: 500;
                 font-size: .75rem; white-space: nowrap; }
  .sv-table td { padding: .38rem .5rem; border-bottom: 1px solid var(--bg3);
                 vertical-align: middle; }
  .sv-table tr:last-child td { border-bottom: none; }
  .sv-vname code { background: var(--bg3); color: var(--text2); padding: 1px 5px;
                   border-radius: 3px; font-size: .78rem; }
  .sv-top code { color: #58a6ff; font-weight: 700; }
  .sv-n { text-align: center; color: var(--text2); font-size: .78rem; }
  .sv-bar { display: flex; align-items: center; gap: .4rem; }
  .sv-bar-bg { background: var(--bg3); border-radius: 3px; height: 8px;
               width: 60px; flex-shrink: 0; }
  .sv-bar-fill { background: #58a6ff; border-radius: 3px; height: 8px; }
  .sv-pct { color: var(--text2); font-size: .78rem; min-width: 28px; }
  .sv-score, .sv-mg, .sv-cp, .sv-fw { text-align: center; color: var(--text2);
                                       font-size: .78rem; line-height: 1.4; }
  .sv-legend { font-size: .7rem; color: var(--text2); margin-top: .5rem; }

  /* Adaptive decision cards */
  .adc-list { display: flex; flex-direction: column; gap: .75rem; }
  .adc-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
              padding: .75rem .9rem; }
  .adc-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
              margin-bottom: .55rem; }
  .adc-tgt { font-weight: 700; font-size: .9rem; color: var(--accent);
             text-decoration: none; }
  .adc-tgt:hover { text-decoration: underline; }
  .adc-phase { font-size: .72rem; padding: 2px 7px; border-radius: 10px; font-weight: 600; }
  .ph-lin  { background: #1a3050; color: #58a6ff; }
  .ph-nl   { background: #2d1a3d; color: #c084fc; }
  .adc-score { font-size: .82rem; font-weight: 600; margin-left: .25rem; }
  .adc-date { margin-left: auto; font-size: .72rem; color: var(--text2); }
  .adc-badges { display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .4rem; }
  .ad-badge { font-size: .74rem; padding: 2px 8px; border-radius: 4px;
              font-family: monospace; white-space: nowrap; }
  .var-fill    { background: #1a3050; color: #58a6ff; }
  .param-nudge { background: #2d2a10; color: #facc15; }
  .add-step    { background: #1c2d1e; color: #4ade80; }
  .skip-step   { background: #2d1f10; color: #fb923c; }
  .revert      { background: #2d1415; color: #f87171; }
  .flag-d      { background: var(--bg3); color: var(--text2); }
  .adc-rat { margin-top: .4rem; }
  .adc-rat summary { font-size: .77rem; color: var(--accent); cursor: pointer;
                     user-select: none; }
  .adc-rat summary:hover { text-decoration: underline; }
  .adc-rat-text { font-size: .8rem; line-height: 1.65; color: var(--text2);
                  margin-top: .4rem; padding: .6rem .75rem;
                  background: var(--bg3); border-radius: 6px;
                  white-space: pre-wrap; word-break: break-word; }
  @media (max-width: 600px) {
    .tl-page { padding: 1rem .75rem 3rem; }
    .erc-grid { grid-template-columns: 1fr; }
    .tl-controls { gap: .5rem; }
    .tl-filter { min-width: 0; flex: 1; }
  }
"""

    return _shell("Tools — SeeStar", body, extra_css=css)


# ---------------------------------------------------------------------------
# Calendar view
# ---------------------------------------------------------------------------

def calendar_page(year: int | None = None, month: int | None = None) -> str:
    from nas_server.database import get_calendar_events
    from nas_server.devlog import get_entries

    today = _date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    year = max(2020, min(2099, int(year)))
    month = max(1, min(12, int(month)))

    events = get_calendar_events(year, month)

    for e in get_entries():
        eday = (e.get("date") or "")[:10]
        if eday.startswith(f"{year:04d}-{month:02d}"):
            events.setdefault(eday, {}).setdefault("devlog", []).append(
                (e.get("id", ""), e.get("title", ""))
            )

    cal = _cal.Calendar(firstweekday=6)  # Sunday first
    weeks = cal.monthdatescalendar(year, month)

    prev_month = month - 1 or 12
    prev_year = year - (1 if month == 1 else 0)
    next_month = month % 12 + 1
    next_year = year + (1 if month == 12 else 0)
    month_name = _date(year, month, 1).strftime("%B %Y")

    day_headers = "".join(
        f'<div class="cal-dh">{d}</div>'
        for d in ["S", "M", "T", "W", "T", "F", "S"]
    )

    cells = ""
    detail_panels = ""

    for week in weeks:
        for day in week:
            iso = day.strftime("%Y-%m-%d")
            is_other = day.month != month
            is_today = day == today
            day_events = events.get(iso, {}) if not is_other else {}
            caps = day_events.get("captures", [])
            procs = day_events.get("processing", [])
            dlogs = day_events.get("devlog", [])
            has_ev = bool(caps or procs or dlogs)

            # Cell classes and style
            cell_cls = "cal-cell"
            if is_other:
                cell_cls += " cal-other"
            if is_today:
                cell_cls += " cal-today"
            if has_ev:
                cell_cls += " cal-active"

            # Activity bars — one per event type present
            bars = ""
            if caps:
                total_subs = sum(c[1] for c in caps)
                bars += f'<span class="bar bar-cap" title="{total_subs} subs"></span>'
            if procs:
                bars += f'<span class="bar bar-proc" title="{len(procs)} run(s)"></span>'
            if dlogs:
                bars += f'<span class="bar bar-dev" title="{len(dlogs)} entry(ies)"></span>'

            onclick = f"showDay('{iso}',this)" if has_ev else ""
            cells += (
                f'<div class="{cell_cls}" onclick="{onclick}">'
                f'<span class="cal-num">{day.day}</span>'
                f'<div class="cal-bars">{bars}</div>'
                f'</div>'
            )

            # Build detail panel for this day
            if has_ev:
                panel_content = f'<div class="dp-date">{day.strftime("%A, %B %-d")}</div>'
                if caps:
                    total_subs = sum(c[1] for c in caps)
                    cap_rows = "".join(
                        f'<div class="dp-row">'
                        f'<a class="dp-target" href="/target/{_uparse.quote(t, safe="")}">{t}</a>'
                        f'<span class="dp-meta">{n} subs</span></div>'
                        for t, n in caps
                    )
                    panel_content += (
                        f'<div class="dp-section">'
                        f'<div class="dp-label cap-label">📷 Captures — {total_subs} total subs</div>'
                        f'{cap_rows}</div>'
                    )
                if procs:
                    proc_rows = "".join(
                        f'<div class="dp-row">'
                        f'<a class="dp-target" href="/report/{t}/{rid}">{t}</a>'
                        f'<span class="dp-meta">{wf}</span></div>'
                        for t, rid, wf in procs
                    )
                    panel_content += (
                        f'<div class="dp-section">'
                        f'<div class="dp-label proc-label">⚙️ Processing</div>'
                        f'{proc_rows}</div>'
                    )
                if dlogs:
                    dev_rows = "".join(
                        f'<div class="dp-row">'
                        f'<a class="dp-target" href="/devlog#{did}">{title[:50]}</a>'
                        f'</div>'
                        for did, title in dlogs
                    )
                    panel_content += (
                        f'<div class="dp-section">'
                        f'<div class="dp-label dev-label">📓 Dev Journal</div>'
                        f'{dev_rows}</div>'
                    )
                detail_panels += (
                    f'<div class="day-panel" id="dp-{iso}" style="display:none">'
                    f'{panel_content}</div>'
                )

    # Summary strip — active nights this month
    all_cap_dates = [d for d, ev in events.items() if ev.get("captures")]
    all_proc_dates = [d for d, ev in events.items() if ev.get("processing")]
    summary = (
        f'<div class="cal-summary">'
        f'<span class="sum-pill cap-pill">{len(all_cap_dates)} capture nights</span>'
        f'<span class="sum-pill proc-pill">{len(all_proc_dates)} processing runs</span>'
        f'</div>'
    )

    body = f"""
<div class="cal-page">
  <div class="cal-header">
    <a href="/calendar/{prev_year}/{prev_month}" class="cal-nav">&#8249;</a>
    <div class="cal-title">
      <h1>{month_name}</h1>
      {summary}
    </div>
    <a href="/calendar/{next_year}/{next_month}" class="cal-nav">&#8250;</a>
  </div>

  <div class="cal-body">
    <!-- Left: grid -->
    <div class="cal-left">
      <div class="cal-grid">
        {day_headers}
        {cells}
      </div>
      <div class="cal-legend">
        <span class="bar bar-cap"></span> Capture &nbsp;
        <span class="bar bar-proc"></span> Processing &nbsp;
        <span class="bar bar-dev"></span> Dev log
      </div>
    </div>

    <!-- Right: detail panel -->
    <div class="cal-right">
      <div id="day-detail" class="day-detail-container">
        <div id="dp-placeholder" class="dp-placeholder">
          &#8592; tap a highlighted day
        </div>
        {detail_panels}
      </div>
    </div>
  </div>
</div>

<script>
var _selEl = null;
function showDay(iso, el) {{
  document.querySelectorAll('.day-panel').forEach(function(p) {{ p.style.display = 'none'; }});
  if (_selEl) _selEl.classList.remove('cal-selected');
  _selEl = el;
  el.classList.add('cal-selected');
  var panel = document.getElementById('dp-' + iso);
  var placeholder = document.getElementById('dp-placeholder');
  if (panel) {{
    panel.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';
    // On mobile, scroll panel into view
    if (window.innerWidth < 760) {{
      document.getElementById('day-detail').scrollIntoView({{behavior:'smooth', block:'start'}});
    }}
  }}
}}
</script>"""

    # NOTE: extra_css must use single braces { } not {{ }} — this is NOT an f-string
    cal_css = """
  .cal-page { max-width: 1060px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
  .cal-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.25rem;
                max-width: 500px; margin-left: auto; margin-right: auto; }
  .cal-title { flex: 1; text-align: center; }
  .cal-title h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: .2rem; }
  .cal-nav { font-size: 2.2rem; line-height: 1; color: var(--text2); text-decoration: none;
             width: 2.4rem; height: 2.4rem; display: flex; align-items: center;
             justify-content: center; border-radius: 50%; border: 1px solid var(--border);
             flex-shrink: 0; transition: background .15s; }
  .cal-nav:hover { background: var(--bg2); color: var(--text); text-decoration: none; }
  .cal-summary { display: flex; gap: .4rem; justify-content: center; flex-wrap: wrap; margin-top: .3rem; }
  .sum-pill { font-size: .72rem; padding: 2px 8px; border-radius: 20px; font-weight: 600; }
  .cap-pill { background: #1f3a5f; color: #58a6ff; }
  .proc-pill { background: #1a3a25; color: #3fb950; }

  /* Two-column layout on desktop */
  .cal-body { display: flex; gap: 1.5rem; align-items: flex-start; }
  .cal-left { flex: 0 0 auto; width: min(100%, 460px); }
  .cal-right { flex: 1; min-width: 0; }

  .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 3px; margin-bottom: .75rem; }
  .cal-dh { text-align: center; font-size: .7rem; font-weight: 700; color: var(--text2);
            padding: .3rem 0; text-transform: uppercase; letter-spacing: .04em; }
  .cal-cell { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
              padding: .4rem .3rem .3rem; min-height: 58px; display: flex; flex-direction: column;
              align-items: center; cursor: default; transition: background .1s, border-color .1s; }
  .cal-other { background: transparent; border-color: transparent; opacity: .3; }
  .cal-today { border-color: var(--accent) !important; border-width: 2px; }
  .cal-active { cursor: pointer; }
  .cal-active:hover { background: var(--bg3); border-color: #444d56; }
  .cal-selected { background: var(--bg3) !important; border-color: var(--accent) !important; }
  .cal-num { font-size: .88rem; font-weight: 600; color: var(--text); line-height: 1; margin-bottom: .3rem; }
  .cal-other .cal-num { color: var(--text2); }
  .cal-today .cal-num { color: var(--accent); }
  .cal-bars { display: flex; flex-direction: column; gap: 2px; width: 100%; }
  .bar { display: block; height: 3px; border-radius: 2px; width: 100%; }
  .bar-cap { background: #58a6ff; }
  .bar-proc { background: #3fb950; }
  .bar-dev { background: #d2a8ff; }
  .cal-legend { display: flex; align-items: center; gap: .5rem; font-size: .72rem;
                color: var(--text2); flex-wrap: wrap; margin-bottom: .5rem; }
  .cal-legend .bar { display: inline-block; width: 18px; height: 3px; border-radius: 2px; vertical-align: middle; }

  /* Detail panel */
  .day-detail-container { background: var(--bg2); border: 1px solid var(--border);
                          border-radius: 10px; padding: 1.25rem; min-height: 200px;
                          position: sticky; top: 4rem; }
  .dp-placeholder { color: var(--text2); font-size: .88rem; padding: 2rem 1rem;
                    text-align: center; }
  .dp-date { font-size: 1.05rem; font-weight: 700; margin-bottom: 1rem;
             padding-bottom: .5rem; border-bottom: 1px solid var(--border); }
  .dp-section { margin-bottom: .9rem; }
  .dp-label { font-size: .7rem; font-weight: 700; text-transform: uppercase;
              letter-spacing: .07em; margin-bottom: .4rem; }
  .cap-label { color: #58a6ff; }
  .proc-label { color: #3fb950; }
  .dev-label { color: #d2a8ff; }
  .dp-row { display: flex; justify-content: space-between; align-items: baseline;
            padding: .35rem 0; border-bottom: 1px solid var(--bg3); gap: .5rem; }
  .dp-row:last-child { border-bottom: none; }
  .dp-target { font-weight: 600; font-size: .88rem; color: var(--text); }
  a.dp-target { color: var(--accent); text-decoration: none; }
  a.dp-target:hover { text-decoration: underline; }
  .dp-meta { font-size: .78rem; color: var(--text2); white-space: nowrap; }

  /* Mobile: stack vertically */
  @media (max-width: 759px) {
    .cal-body { flex-direction: column; }
    .cal-left { width: 100%; }
    .day-detail-container { position: static; min-height: auto; }
    .dp-placeholder { padding: 1rem; }
  }
"""

    return _shell(f"Calendar {month_name} — SeeStar", body, cal_css)


# ---------------------------------------------------------------------------
# Help / CLI reference page
# ---------------------------------------------------------------------------

_CLI_COMMANDS = [
    {
        "group": "Server & Status",
        "cmds": [
            ("seestar status", "", "Server health — paths, DB location, config"),
            ("seestar mounts", "", "Check SMB mount health (incoming & library)"),
            ("seestar logs", "[--n 100]", "Tail recent log entries (default 50)"),
        ],
    },
    {
        "group": "Library & Ingest",
        "cmds": [
            ("seestar scan", "", "Trigger a full library re-scan (re-indexes all FITS)"),
            ("seestar check", "", "Force watcher to check incoming folder now"),
            ("seestar sort", "[target] [--run]",
             "List sessions waiting to be sorted from Downloaded. Add <code>--run</code> to actually move them."),
            ("seestar organize", "&lt;target&gt;",
             "Move a session from incoming to the library folder for a target"),
            ("seestar pipeline", "[target]",
             "Show pipeline stage for all targets or one specific target"),
            ("seestar stage", "&lt;target&gt; &lt;stage&gt; [--notes '...']",
             "Manually update a target's stage: captured | stacked | processing | processed | exported"),
        ],
    },
    {
        "group": "Stacking",
        "cmds": [
            ("seestar stack", "&lt;target&gt; [--engine siril|imagemm|both]",
             "Stack light frames. siril = default, fast, good quality. imagemm = higher SNR but needs &lt;1000 frames. both = run both engines."),
            ("seestar stack-status", "", "Show all currently running stack jobs"),
            ("seestar stack-kill", "&lt;target&gt;", "Kill a running stack job"),
            ("seestar score", "&lt;target&gt; [--bottom-pct 0.10] [--all]",
             "Score raw frames by FWHM, eccentricity, star count. Flags bottom N% as poor quality."),
        ],
    },
    {
        "group": "Automated Processing (Auto-Process)",
        "cmds": [
            ("seestar autoprocess", "&lt;target&gt; [--workflow W] [--dry-run] [--experiment]",
             "Run the full Claude-driven pipeline. Workflow choices: "
             "<code>quick_default</code> (fastest), "
             "<code>seestar_broadband</code> (default), "
             "<code>seestar_galaxy</code>, "
             "<code>seestar_nebula</code>, "
             "<code>seestar_globular</code>, "
             "<code>experiment_full</code> (all variants). "
             "Add <code>--dry-run</code> to plan without writing. "
             "<code>--experiment</code> tries all variants per step."),
            ("seestar autoprocess-status", "&lt;target&gt;", "Check current autoprocess phase and progress"),
            ("seestar queue add", "&lt;target&gt; [--workflow W] [--experiment] [--dry-run] [--source-file F]",
             "Add a target to the persistent processing queue. Queue runs one job at a time."),
            ("seestar queue list", "", "Show pending queue items in order"),
            ("seestar queue remove", "&lt;position&gt;", "Remove item at position N from queue (1-based)"),
            ("seestar queue clear", "", "Remove all pending queue items"),
        ],
    },
    {
        "group": "Manual Processing Tools",
        "cmds": [
            ("seestar assess", "&lt;target&gt;",
             "Ask Claude to assess the latest stacked image quality (scores sharpness, noise, color, etc.)"),
            ("seestar stretch", "&lt;target&gt; [--mode stat|ghs|veralux] [--median 0.25] [--alpha 5.0] [--gamma 3.0]",
             "Stretch the latest stacked FITS. <code>stat</code> = statistics-based (default). "
             "<code>ghs</code> = Generalised Hyperbolic Stretch. <code>--median</code> sets target background level."),
            ("seestar bgextract", "&lt;target&gt; [--correction Subtraction|Division] [--smoothing 0.5]",
             "AI background extraction via GraXpert. Subtraction = remove gradient; Division = normalize."),
            ("seestar cc", "&lt;target&gt; [--mode denoise|sharpen|both]",
             "Cosmic Clarity AI: denoise, sharpen, or both."),
            ("seestar postprocess", "&lt;target&gt; [options]",
             "Full PixInsight tool suite. Key flags: "
             "<code>--no-bxt</code> disable BlurXTerminator, "
             "<code>--bxt-psf 4.0</code> PSF size, "
             "<code>--no-nxt</code> disable NoiseXTerminator, "
             "<code>--nxt-denoise 0.70</code>, "
             "<code>--scnr</code> green removal, "
             "<code>--scnr-amount 0.9</code>, "
             "<code>--spcc</code> spectral color calibration, "
             "<code>--ht</code> auto-stretch, "
             "<code>--ht-target-bg 0.12</code>, "
             "<code>--hdrmt</code> HDR compression (nebulae), "
             "<code>--curves</code> curve adjustment, "
             "<code>--starxt</code> star removal."),
            ("seestar postprocess-status", "&lt;target&gt;", "Check PI postprocess progress"),
            ("seestar processed", "&lt;target&gt; [--notes]", "List processed output files for a target"),
        ],
    },
    {
        "group": "Experiment & Learning",
        "cmds": [
            ("seestar experiment", "&lt;target&gt; [step] [--dry-run]",
             "Run one experiment step for a target: compare all variants, Claude picks winner. "
             "Step examples: <code>background_extraction</code>, <code>denoise_linear</code>, <code>sharpen_linear</code>"),
            ("seestar learning", "[step] [--object-type galaxy|emission_nebula|...]",
             "Show accumulated win rates and top variants for experiment steps. Omit step to see all."),
        ],
    },
    {
        "group": "Reports & Journal",
        "cmds": [
            ("seestar report", "[target] [--run-id N] [--open]",
             "List processing run reports for a target. <code>--run-id N</code> shows a specific run. <code>--open</code> opens in browser."),
            ("seestar devlog", "[--add] [--title '...'] [--category decision|feature|bug_fix|experiment|data] [--body '...']",
             "View dev journal or add a new entry. Categories track what kind of change you made."),
        ],
    },
]

_WORKFLOWS = [
    ("quick_default", "GraXpert → SPCC → stat-stretch. Fastest path, good for quick previews."),
    ("seestar_broadband", "Full pipeline: BG extract → color calib → deconv → denoise → stretch → SCNR → NR → curves. Default."),
    ("seestar_galaxy", "Like broadband but tuned for galaxies: conservative stretch, star split, extra detail."),
    ("seestar_nebula", "Like broadband but tuned for emission nebulae: HDRMT, stronger SCNR, color pop."),
    ("seestar_globular", "Globular clusters: GraXpert → SPCC → BXT core-masked → NXT conservative → smart-dark stretch → HDR wavelet compression."),
    ("seestar_fast", "Abbreviated broadband — fewer iterations, skips slow steps."),
    ("experiment_full", "Runs ALL variants for every step. Claude scores each and picks the winner."),
    ("linear_only", "Stops after linear steps (BG, color calib, denoise). No stretch."),
    ("spcc_only", "Only runs SPCC color calibration. Useful after GraXpert."),
    ("seestar_starless_stretch", "Separates stars, stretches starless layer, recombines."),
]


def help_page() -> str:
    # ── CLI reference tables (kept for reference section) ────────────────────
    cli_sections = ""
    for group in _CLI_COMMANDS:
        rows = ""
        for cmd, args, desc in group["cmds"]:
            rows += (
                f'<tr><td class="cmd-col"><code class="cmd">{cmd}</code>'
                + (f'<span class="cmd-args">{args}</span>' if args else "")
                + f'</td><td class="desc-col">{desc}</td></tr>'
            )
        cli_sections += (
            f'<div class="cmd-group"><h3 class="ref-h3">{group["group"]}</h3>'
            f'<div class="ref-table-wrap"><table class="cmd-table"><tbody>{rows}</tbody></table></div></div>'
        )

    wf_rows = "".join(
        f'<tr><td><code class="cmd">{wf}</code></td><td class="desc-col">{desc}</td></tr>'
        for wf, desc in _WORKFLOWS
    )

    body = """
<div class="help-wrap">

  <!-- ── Sticky sidebar nav ────────────────────────────────────── -->
  <nav class="help-nav" id="help-nav">
    <div class="nav-label">Contents</div>
    <a href="#overview">Overview</a>
    <a href="#pipeline-flow">How it works</a>
    <a href="#planner">Planner</a>
    <a href="#queue">Processing queue</a>
    <a href="#adaptive">Adaptive AI planning</a>
    <a href="#target-page">Target pages</a>
    <a href="#stacking">Stacking</a>
    <a href="#experiments">Experiment mode</a>
    <a href="#captures">Frame quality</a>
    <a href="#folio">Target folios</a>
    <a href="#comments">Notes &amp; feedback</a>
    <a href="#automation">Automation flags</a>
    <a href="#video">Processing videos</a>
    <a href="#cli">CLI reference</a>
    <a href="#workflows">Workflows</a>
  </nav>

  <!-- ── Main content ──────────────────────────────────────────── -->
  <div class="help-content">

    <!-- OVERVIEW -->
    <section id="overview">
      <h1 class="help-h1">SeeStar Database — User Guide</h1>
      <p class="help-lead">A fully automated astrophotography pipeline for the ZWO SeeStar S50 smart telescope. Raw light frames come in from the scope, get stacked, processed by a Claude-driven AI pipeline, assessed, and delivered as finished images — with minimal manual intervention.</p>
      <div class="tip-box">
        <strong>The short version:</strong> Point your scope at a target, enable Auto-stack and Auto-process on its page, and go to bed. By morning you'll have a processed image, a quality score, and a Telegram message telling you how it went.
      </div>
    </section>

    <!-- HOW IT WORKS -->
    <section id="pipeline-flow">
      <h2 class="help-h2">How it works — end to end</h2>
      <p>Every target moves through five stages, tracked in the Pipeline page:</p>
      <ol class="help-ol">
        <li><strong>Captured</strong> — Raw 30s light frames arrive from the SeeStar via the NAS transfer folder. The watcher detects them and indexes each file with FWHM, eccentricity, and SNR measurements.</li>
        <li><strong>Stacked</strong> — Frames are calibrated, registered, and integrated using Siril (standard), SASpro Image MM (deconvolution stacking), or PixInsight (drizzle, plate solve). Bad frames are automatically excluded based on FWHM and eccentricity thresholds. The result is a single calibrated FITS file.</li>
        <li><strong>Processing</strong> — The full autoprocess pipeline runs: background extraction, colour calibration, deconvolution, linear denoise, star removal, stretch, non-linear noise reduction, HDR compression, curves, halo suppression, and optional extras. Claude makes decisions at every step.</li>
        <li><strong>Processed</strong> — A final FITS and preview JPEG are written to the target's <code>_processed/</code> folder. Scores are logged. A Telegram message with the final image is sent.</li>
        <li><strong>Exported</strong> — Optional manual step once you're happy with the result.</li>
      </ol>
      <p>The <strong>Pipeline page</strong> (<code>/pipeline-view</code>) shows every target, its current stage, frame counts, integration time, and last stack result. Each row has quick links to Captures, FITS files, Stack History, and the queue. Auto-stack and Auto-process toggles live inline here too.</p>
    </section>

    <!-- PLANNER -->
    <section id="planner">
      <h2 class="help-h2">Planner</h2>
      <p>The Planner (<code>/planner</code>) scores every active target for a given night and tells you what to shoot. It accounts for:</p>
      <ul class="help-ul">
        <li><strong>Altitude</strong> — how high the target peaks and how long it stays above your horizon. Without a custom horizon, uses peak altitude / 60°. With a custom horizon profile, uses actual hours above your obstructions.</li>
        <li><strong>Moon</strong> — separation from the moon, illumination fraction. A full moon 10° away tanks the score; a new moon doesn't affect it at all.</li>
        <li><strong>Existing integration</strong> — targets with less integration time get a mild boost so you fill in gaps rather than re-shooting what you already have.</li>
        <li><strong>Seasonal scarcity</strong> — if a target is fewer than 45 days from dropping below a usable altitude for the season, it gets a priority boost to capture it while you still can.</li>
        <li><strong>Mosaic in progress</strong> — targets with an active multi-panel mosaic stack get elevated priority to finish the mosaic before conditions change.</li>
        <li><strong>Transient flag</strong> — manually flaggable targets (supernovae at peak, comets, variables) can be pinned to the top of the plan.</li>
      </ul>
      <h3 class="help-h3">Custom horizon profile</h3>
      <p>Enter a comma-separated list of <code>azimuth:altitude</code> pairs in the Planner UI to describe your actual sky obstruction. You can use compass names (N, NE, ENE, etc.) or degrees:</p>
      <pre class="example-block">N:20, NE:35, E:25, SE:15, S:8, SW:10, W:18, NW:22</pre>
      <p>The profile is interpolated between sample points and saved to your browser's localStorage — it persists between sessions. Targets that never clear your horizon are filtered out of the plan entirely.</p>
      <h3 class="help-h3">Nightly auto-plan</h3>
      <p>A scheduled job runs every evening at 20:00 Arizona time. It computes the plan for tonight, picks the top 10 targets, asks Claude Haiku for a 3–4 sentence observing narrative, and sends the whole thing to Telegram.</p>
    </section>

    <!-- QUEUE -->
    <section id="queue">
      <h2 class="help-h2">Processing Queue</h2>
      <p>The Queue page (<code>/queue-view</code>) is the control panel for the autoprocess pipeline. Jobs run one at a time with a global PixInsight lock to prevent conflicts.</p>
      <h3 class="help-h3">Adding a job</h3>
      <p>Pick a target, choose a workflow (default: <strong>auto</strong>), optionally choose a specific source FITS file, and hit Add to Queue. The job appears in the table with estimated time remaining.</p>
      <h3 class="help-h3">The auto workflow</h3>
      <p>When you select <strong>auto</strong>, the system detects the object type before dispatching:</p>
      <ul class="help-ul">
        <li>Globular clusters and open clusters → <code>seestar_globular</code></li>
        <li>Galaxies → <code>seestar_galaxy</code></li>
        <li>Emission, reflection, and planetary nebulae → <code>seestar_nebula</code></li>
        <li>Unknown / anything else → <code>seestar_broadband</code></li>
      </ul>
      <p>The detection checks the target's folio <code>object_type</code> field first (most reliable), then falls back to name-matching. The resolved workflow is shown in the log: <code>[queue] 'NGC 7000': auto → seestar_nebula</code>.</p>
      <p>This matters most for clusters: the <code>seestar_globular</code> workflow skips star removal entirely (the cluster <em>is</em> the stars) and uses calibrated stretch parameters for dense cores. Running a cluster through a nebula or galaxy workflow produces bad results because the starless processing layer is nearly empty.</p>
      <h3 class="help-h3">Pause, resume, and graceful restart</h3>
      <p><strong>Pause</strong> stops the queue from picking up new jobs but doesn't interrupt the currently running one. <strong>Graceful Restart</strong> waits for the current job to finish before reloading the service — use this instead of a hard restart when something is processing.</p>
    </section>

    <!-- ADAPTIVE AI PLANNING -->
    <section id="adaptive">
      <h2 class="help-h2">Adaptive AI planning — Claude designs the workflow</h2>
      <p>The pipeline doesn't just run a fixed recipe. Before processing begins, Claude examines the stacked image and makes active decisions about how to process it. This happens in two phases:</p>
      <h3 class="help-h3">Phase 1 — Linear plan (before processing, Sonnet 4.6)</h3>
      <p>After loading the stack but before any processing step runs, the pipeline generates a preview JPEG and sends it to Claude along with:</p>
      <ul class="help-ul">
        <li>The target's folio (angular size, dominant features, processing notes, reference examples)</li>
        <li>Physics-measured statistics: SNR, FWHM, gradient severity, green excess</li>
        <li>Physics-computed parameter suggestions (BGE correction strength, deconvolution PSF, denoise strength)</li>
        <li>The history of adaptive decisions and outcomes from previous runs on this target</li>
        <li>Any aesthetic feedback notes you've left on the target page</li>
      </ul>
      <p>Claude responds with <strong>variant fills</strong> (choosing which algorithm to use for unlocked steps), <strong>param nudges</strong> (adjusting strength within physics bounds — e.g. "faint target, reduce denoise luma to 0.3"), and <strong>flags</strong> (concerns to watch for, e.g. "compact core — PSF deconvolution may over-sharpen").</p>
      <p>The rule: physics is the ceiling. Claude can go gentler than the physics suggests, but cannot go more aggressive.</p>
      <h3 class="help-h3">Phase 2 — Non-linear plan (after pre-stretch assessment, Opus 4.7)</h3>
      <p>After the linear phase completes and before the stretch, a second assessment runs. Claude now sees the pre-stretch image stats and scores and decides:</p>
      <ul class="help-ul">
        <li><strong>Which optional steps to add</strong> — from a catalog per workflow: CLAHE, HDR multiscale, dark enhance, colour saturation, SCNR, unsharp mask</li>
        <li><strong>Which planned steps to skip</strong> — if they're unlikely to help this particular image</li>
        <li><strong>Variant and parameter overrides</strong> for the non-linear steps — Claude wins here; it can override calibrated defaults if it has good reason</li>
      </ul>
      <h3 class="help-h3">Visual revert gate</h3>
      <p>For each step Claude adds, the pipeline snapshots the image before the step runs. After it completes, a fast visual assessment checks if overall quality dropped more than 0.4 points. If it did, the step is reverted and the failure is logged — so next time Claude processes this target, it knows that step made things worse.</p>
      <h3 class="help-h3">Learning over time</h3>
      <p>Every decision Claude makes (variant choice, step added, step skipped, step reverted) is logged in the <code>adaptive_decisions</code> table with the final score delta. On the next run for the same target, Claude sees this history and can learn from it — if adding HDR multiscale made things worse last time, it won't suggest it again.</p>
      <p>Your notes on the target page (Notes &amp; Feedback section) are shown to Claude as "USER AESTHETIC FEEDBACK — prioritise these" at the top of both planning prompts, so observations like "stars look over-stretched" directly influence the next run's decisions.</p>
    </section>

    <!-- TARGET PAGE -->
    <section id="target-page">
      <h2 class="help-h2">Target pages</h2>
      <p>Every target has a detail page at <code>/target/{name}</code>, reachable by clicking the target name anywhere in the UI. The page covers the full history of a target in one place.</p>
      <h3 class="help-h3">Hero block</h3>
      <p>The most recent processed preview (or stack preview if not yet processed) fills the top. Underneath: pipeline stage badge, total integration hours, number of sessions, date range, average FWHM.</p>
      <h3 class="help-h3">Stacking runs</h3>
      <p>Every stack that's been run, with engine, frame count, culling stats, FWHM, and a link to the output FITS. Failed stacks show the error snippet.</p>
      <h3 class="help-h3">Processing runs</h3>
      <p>Each autoprocess run has an expandable card showing every step applied, before/after score comparisons, elapsed time, and a link to the run report.</p>
      <h3 class="help-h3">Automation</h3>
      <p>Two checkboxes — <strong>Auto-stack after transfer</strong> and <strong>Auto-process after stack</strong> — control what happens automatically when new frames arrive for this target. State saves instantly and is shared with the Pipeline page.</p>
      <h3 class="help-h3">Claude assessments</h3>
      <p>The most recent Claude quality assessments, with score breakdowns across noise, gradient, star roundness, stretch quality, and colour balance.</p>
      <h3 class="help-h3">Notes &amp; Feedback</h3>
      <p>Leave aesthetic notes for Claude to read on the next processing run. These aren't blocking — write them whenever, even days after a run. Claude will see them next time it processes this target. See the <a href="#comments">Notes &amp; Feedback</a> section below for details.</p>
    </section>

    <!-- STACKING -->
    <section id="stacking">
      <h2 class="help-h2">Stacking</h2>
      <p>The stacker supports four engines, each with different trade-offs:</p>
      <div class="ref-table-wrap">
        <table class="cmd-table">
          <thead><tr><th style="text-align:left;padding:.4rem .6rem">Engine</th><th style="text-align:left;padding:.4rem .6rem">Best for</th><th style="text-align:left;padding:.4rem .6rem">Notes</th></tr></thead>
          <tbody>
            <tr><td><code class="cmd">siril</code></td><td class="desc-col">Everything — the reliable default</td><td class="desc-col">Siril 1.4.3, seqplatesolve for WCS, 2-pass registration. Fast, clean output.</td></tr>
            <tr><td><code class="cmd">imagemm</code></td><td class="desc-col">Deep targets needing deconvolution stacking</td><td class="desc-col">SASpro Image MM engine. Multi-frame deconvolution (MFDECONV) during integration. Slower but can extract more detail in high-SNR stacks.</td></tr>
            <tr><td><code class="cmd">pixinsight_register</code></td><td class="desc-col">Drizzle integration, highest fidelity</td><td class="desc-col">PI Debayer → StarAlignment (generateDrizzleData) → ImageIntegration. Plate solves via PI's ImageSolver + GAIA DR3 catalog.</td></tr>
            <tr><td><code class="cmd">siril</code> (drizzle modes)</td><td class="desc-col">Drizzle via Siril SSFs</td><td class="desc-col">Various drizzle SSFs: standard, hero (best frames only), maxframing (large mosaic registration area).</td></tr>
          </tbody>
        </table>
      </div>
      <h3 class="help-h3">Frame culling</h3>
      <p>Every frame is measured for FWHM (seeing quality), eccentricity (tracking/trailing), and SNR. Frames exceeding the FWHM threshold or eccentricity threshold (currently 0.66) are excluded. You can manually override exclusions on the Captures page — excluded frames are shown dimmed with an Include button.</p>
      <h3 class="help-h3">Large stacks (NAS work dir)</h3>
      <p>To protect the VM disk, stacking temp files always go to the NAS (<code>/mnt/nas_data/_stack_work/</code>) rather than local <code>/tmp/</code>. The fallback to local only triggers if the NAS is unmounted.</p>
    </section>

    <!-- EXPERIMENT MODE -->
    <section id="experiments">
      <h2 class="help-h2">Experiment mode</h2>
      <p>In experiment mode, the pipeline runs <em>every variant</em> of each step rather than choosing one. For a step with 5 variants, all 5 outputs are generated, previewed, and sent to Claude for side-by-side comparison. The winner is selected, applied, and its ID stored in the experiment results.</p>
      <p>Run experiment mode from the queue form by checking "Experiment mode", or via the CLI:</p>
      <pre class="example-block">seestar autoprocess "M 51" --workflow seestar_galaxy --experiment</pre>
      <p>Results feed into the learning system: <code>get_experiment_priors()</code> aggregates win rates per step per object type. With 3+ data points for a step, the historical winner is highlighted in future Claude comparison prompts.</p>
      <p>Experiment mode is intentionally independent of adaptive planning — it always tests all variants regardless of Claude's Phase 1 suggestions, to ensure clean comparative data.</p>
    </section>

    <!-- CAPTURES -->
    <section id="captures">
      <h2 class="help-h2">Frame quality control</h2>
      <p>The Captures page (<code>/frames/{target}</code>) shows every indexed light frame grouped by session date. For each frame: filename, exposure time, FWHM (seeing quality in arcseconds), eccentricity (0 = perfect circle, 1 = fully trailed), and SNR.</p>
      <p>Frames that failed culling thresholds are automatically excluded from stacking. You can manually toggle any frame's exclusion status — useful if the auto-culling was too aggressive or too lenient for a specific session. Changes take effect on the next stack.</p>
      <p>The summary bar at the top shows total subs, net (non-excluded) subs, and net integration time.</p>
    </section>

    <!-- FOLIO -->
    <section id="folio">
      <h2 class="help-h2">Target folios</h2>
      <p>Each target has a folio — a structured JSON knowledge base at <code>nas_server/target_folios/{name}.json</code> — that gives Claude object-specific context it wouldn't otherwise have.</p>
      <p>A folio captures: angular size, distance, visual magnitude, best season, dominant colours, structural complexity, key features, challenge areas, what separates good from great for this object, integration requirements, drizzle benefit, known processing pitfalls, stretch approach, masking guidance, and links to reference examples on AstroBin.</p>
      <p>This means when Claude is deciding whether to add HDR multiscale enhancement to NGC 7000, it knows the North America Nebula has intricate hydrogen-alpha filaments that benefit from local contrast enhancement — not just that it's a large nebula.</p>
      <p>View a target's folio at <code>/folio/{target}</code>. Folios are generated via Claude Code WebSearch/WebFetch (no API cost at generation time) and can be manually refined.</p>
    </section>

    <!-- COMMENTS -->
    <section id="comments">
      <h2 class="help-h2">Notes &amp; Feedback</h2>
      <p>The Notes &amp; Feedback section on each target page lets you leave aesthetic observations that Claude reads before the next processing run. This is designed to be asynchronous — write a note whenever you notice something, even if it's days after the run completed.</p>
      <p>Examples of useful notes:</p>
      <ul class="help-ul">
        <li><em>"Stars are over-stretched. Image looks overprocessed."</em> — Claude will back off the stretch and deconvolution aggressiveness.</li>
        <li><em>"Sky background is a bit bright. Would prefer darker."</em> — Claude will target a more conservative background level.</li>
        <li><em>"Colour looks slightly green. Needs more SCNR."</em> — Claude will apply stronger green suppression.</li>
        <li><em>"Love the nebula detail but the core is blown. Try HDR next time."</em> — Claude will add HDR multiscale in the non-linear plan.</li>
      </ul>
      <p>Notes are shown to Claude as <strong>"USER AESTHETIC FEEDBACK — prioritise these"</strong> at the top of both Phase 1 (linear) and Phase 2 (non-linear) planning prompts, above all other context. They carry significant weight.</p>
      <p>Notes persist indefinitely and are not auto-deleted after a run — they accumulate as a preference record for that target. Delete individual notes with the ✕ button when they've been addressed or are no longer relevant.</p>
    </section>

    <!-- AUTOMATION -->
    <section id="automation">
      <h2 class="help-h2">Automation flags</h2>
      <p>Two per-target flags control what happens automatically when the system detects activity for a target:</p>
      <ul class="help-ul">
        <li><strong>Auto-stack after transfer</strong> — when new frames are detected in the transfer folder for this target, a stacking job is automatically queued once the transfer appears complete.</li>
        <li><strong>Auto-process after stack</strong> — when a stacking job for this target completes successfully, an autoprocess job is immediately queued with the <code>auto</code> workflow.</li>
      </ul>
      <p>These flags can be toggled from two places: the <strong>Automation</strong> section on each target's detail page, or inline in the <strong>Pipeline page</strong> (Stack and Process checkboxes in the actions column). Both read and write the same underlying flags — changes in one place are reflected in the other immediately on next page load.</p>
      <p>For a fully hands-off night: enable both flags on the targets you plan to image before you start. As frames come in, they'll be stacked and processed automatically. You'll receive Telegram notifications at each stage.</p>
    </section>

    <!-- VIDEO -->
    <section id="video">
      <h2 class="help-h2">Processing videos</h2>
      <p>The pipeline automatically generates a documentary video of each processing run. As the pipeline executes, it saves annotated 1920×1080 JPEG frames at key stages. When the run completes, ffmpeg stitches them into an MP4 in the background.</p>
      <p>Each frame shows the target image (portrait SeeStar images get a blurred bokeh background filling the widescreen canvas), with an overlay bar showing the current stage, step label, quality score badge, and stat pills. Planning phases produce text-only cards listing Claude's decisions.</p>
      <p>Frames are captured at:</p>
      <ul class="help-ul">
        <li>Initial stack assessment (raw stack quality)</li>
        <li>Claude's linear plan (variant choices, param nudges, flags)</li>
        <li>Pre-stretch assessment (after linear processing)</li>
        <li>Claude's non-linear plan (optional steps added/skipped)</li>
        <li>Each major processing step (one frame per force-variant step)</li>
        <li>Final result (score, steps applied, elapsed time, improvement delta)</li>
      </ul>
      <p>Videos are saved to <code>/mnt/nas_data/SeeStar/{target}/_video/</code>. The planning, capture, and transfer phases will be included in a future update once the NINA Windows integration is complete.</p>
    </section>

    <!-- CLI REFERENCE -->
    <section id="cli">
      <h2 class="help-h2">CLI reference</h2>
      <p style="color:var(--text2);font-size:.88rem;margin-bottom:1.25rem">All commands run as <code>seestar &lt;command&gt;</code> on the NAS VM. Use <code>--url http://&lt;host&gt;:8000</code> to target a remote server.</p>
""" + cli_sections + """
      <div class="cmd-group">
        <h3 class="ref-h3">Common examples</h3>
        <pre class="example-block"># Let auto workflow pick the right pipeline
seestar queue add "M 51"
seestar queue add "NGC 7000"
seestar queue add "M 13"   # auto detects globular cluster

# Run experiments to compare all variants
seestar autoprocess "M 51" --workflow seestar_galaxy --experiment

# Check what the pipeline is doing right now
seestar stack-status
seestar autoprocess-status "M 51"
seestar logs --n 20

# Manual tool use after stacking
seestar stretch "M 81" --mode ghs --median 0.08 --alpha 6.0
seestar bgextract "M 81" --smoothing 0.3
seestar assess "M 81"</pre>
      </div>
    </section>

    <!-- WORKFLOW REFERENCE -->
    <section id="workflows">
      <h2 class="help-h2">Workflow reference</h2>
      <p style="color:var(--text2);font-size:.88rem;margin-bottom:1.25rem">
        Select a workflow when adding a job to the queue. <strong>auto</strong> (the default) detects the object type and routes automatically. Specific workflows are available when you need to override.
      </p>
      <div class="ref-table-wrap">
        <table class="cmd-table">
          <tbody>
            <tr><td><code class="cmd">auto</code></td><td class="desc-col">Detect object type and route to the correct workflow. Clusters → globular. Galaxies → galaxy. Nebulae → nebula. Unknown → broadband. <strong>Use this by default.</strong></td></tr>
""" + wf_rows + """
          </tbody>
        </table>
      </div>
      <p style="margin-top:1rem;font-size:.85rem;color:var(--text2)">
        With adaptive AI planning active, the <code>seestar_broadband</code>, <code>seestar_galaxy</code>, and <code>seestar_nebula</code> workflows are nearly equivalent — Claude fills in all the step choices dynamically. The main reason to pick a specific one is to lock in a particular step sequence, or to force globular processing on an unusual target.
      </p>
    </section>

  </div><!-- end help-content -->
</div><!-- end help-wrap -->

<script>
// Highlight active nav link on scroll
(function() {
  const links = document.querySelectorAll('.help-nav a');
  const sections = Array.from(links).map(a => document.querySelector(a.getAttribute('href')));
  function onScroll() {
    let active = 0;
    sections.forEach(function(s, i) {
      if (s && s.getBoundingClientRect().top < 120) active = i;
    });
    links.forEach(function(l, i) { l.classList.toggle('nav-active', i === active); });
  }
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();
})();
</script>"""

    css = """
  .help-wrap { display: flex; gap: 2rem; width: 100%; max-width: 1200px; margin: 0 auto;
               padding: 2rem 1.25rem 4rem; align-items: flex-start; overflow-x: hidden; }
  .help-nav { position: sticky; top: 1rem; min-width: 160px; max-width: 180px;
              flex-shrink: 0; font-size: .82rem; }
  .help-nav .nav-label { font-size: .7rem; font-weight: 700; letter-spacing: .1em;
                          text-transform: uppercase; color: var(--text2);
                          margin-bottom: .6rem; }
  .help-nav a { display: block; padding: .28rem 0; color: var(--text2);
                text-decoration: none; border-left: 2px solid transparent;
                padding-left: .6rem; transition: color .15s, border-color .15s; }
  .help-nav a:hover { color: var(--text); }
  .help-nav a.nav-active { color: var(--accent); border-left-color: var(--accent); }
  .help-content { flex: 1; min-width: 0; overflow-wrap: break-word; word-break: break-word; }
  section { margin-bottom: 3rem; scroll-margin-top: 1.5rem; }
  .help-h1 { font-size: 1.5rem; margin-bottom: .5rem; line-height: 1.3; }
  .help-h2 { font-size: 1.1rem; font-weight: 700; margin: 0 0 .9rem;
             padding-bottom: .45rem; border-bottom: 1px solid var(--border); }
  .help-h3 { font-size: .92rem; font-weight: 600; margin: 1.4rem 0 .5rem; color: var(--text); }
  .ref-h3 { font-size: .82rem; color: var(--text2); text-transform: uppercase;
            letter-spacing: .08em; margin-bottom: .6rem; padding-bottom: .35rem;
            border-bottom: 1px solid var(--border); }
  .help-lead { font-size: .95rem; color: var(--text2); line-height: 1.7; margin-bottom: 1.25rem; }
  p { font-size: .88rem; line-height: 1.75; margin-bottom: .9rem; color: var(--text); }
  .help-ul, .help-ol { font-size: .87rem; line-height: 1.75; padding-left: 1.4rem;
                        margin-bottom: .9rem; color: var(--text); }
  .help-ul li, .help-ol li { margin-bottom: .35rem; }
  .tip-box { background: #1c2d1e; border: 1px solid #3fb95055; border-radius: 6px;
             padding: .75rem 1rem; font-size: .87rem; line-height: 1.65;
             margin-bottom: 1.25rem; color: var(--text); }
  .tip-box strong { color: #3fb950; }
  .cmd-group { margin-bottom: 1.75rem; }
  .ref-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: .75rem; }
  .cmd-table { border-collapse: collapse; font-size: .82rem; min-width: 480px; }
  .cmd-table thead tr { color: var(--text2); border-bottom: 1px solid var(--border); }
  .cmd-table th { padding: .4rem .6rem; font-weight: 600; }
  .cmd-table tr { border-bottom: 1px solid var(--bg3); }
  .cmd-table tr:last-child { border-bottom: none; }
  .cmd-col { padding: .5rem .6rem .5rem 0; width: 38%; vertical-align: top; }
  .desc-col { padding: .5rem 0; color: var(--text2); vertical-align: top;
              font-size: .82rem; line-height: 1.55; }
  .cmd { background: var(--bg3); color: #e3b341; padding: 2px 6px; border-radius: 4px;
         font-size: .79rem; white-space: nowrap; display: inline-block; }
  .cmd-args { color: var(--text2); font-size: .72rem; display: block; margin-top: 3px;
              font-family: monospace; }
  code { background: var(--bg3); color: #e3b341; padding: 1px 5px; border-radius: 3px;
         font-size: .8rem; word-break: break-all; }
  .example-block { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
                   padding: .85rem 1rem; font-size: .79rem; line-height: 1.7;
                   overflow-x: auto; -webkit-overflow-scrolling: touch;
                   color: var(--text); font-family: monospace;
                   white-space: pre; margin-bottom: 1rem; max-width: 100%; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  @media (max-width: 720px) {
    .help-wrap { flex-direction: column; padding: 1rem .75rem 3rem; gap: 1rem; }
    .help-nav { position: static; width: 100%; max-width: 100%; min-width: 0;
                display: flex; flex-wrap: wrap; gap: .15rem .6rem;
                border-bottom: 1px solid var(--border); padding-bottom: .75rem; margin-bottom: .5rem; }
    .help-nav .nav-label { display: none; }
    .help-nav a { border-left: none; padding-left: 0; padding: .2rem .4rem;
                  border-bottom: 2px solid transparent; font-size: .78rem; }
    .help-nav a.nav-active { border-bottom-color: var(--accent); color: var(--accent); }
    .help-h1 { font-size: 1.25rem; }
    .cmd-col { width: auto; }
    .ref-table-wrap { margin: 0 -.75rem .75rem; padding: 0 .75rem; }
  }
"""

    return _shell("User Guide — SeeStar", body, extra_css=css)


# ---------------------------------------------------------------------------
# Capture frames page  (/frames/{target})
# ---------------------------------------------------------------------------

def frames_page(target: str) -> str:
    import urllib.parse
    from collections import defaultdict
    from nas_server.database import get_frames_by_target, get_story_data

    all_targets = [t["target"] for t in get_story_data()]
    frames = get_frames_by_target(target)

    def fmt_time(secs: float) -> str:
        if secs < 3600:
            return f"{secs / 60:.0f}m"
        return f"{secs / 3600:.1f}h"

    if not frames:
        opts = "".join(
            f'<option value="{t}" {"selected" if t == target else ""}>{t}</option>'
            for t in all_targets
        )
        body = f"""
<div class="fp-wrap">
  <h1 style="font-size:1.4rem;margin-bottom:1rem">{target} — Captures</h1>
  <form onsubmit="window.location='/frames/'+encodeURIComponent(this.t.value);return false"
        style="display:flex;gap:.5rem;align-items:center;margin-bottom:1.5rem">
    <select name="t" class="sel">{opts}</select>
    <button type="submit" class="go-btn">Go</button>
  </form>
  <p style="color:var(--text2)">No frames found for this target.</p>
</div>"""
        return _shell(f"Captures: {target} — SeeStar", body, _FRAMES_CSS)

    # Summary stats
    total = len(frames)
    net = sum(1 for f in frames if not f.get("exclude"))
    net_exp = sum((f.get("exposure_time") or 0) for f in frames if not f.get("exclude"))

    # Group by date
    by_date: dict = defaultdict(list)
    for f in frames:
        by_date[(f.get("date") or "")[:10]].append(f)

    # Target selector
    opts = "".join(
        f'<option value="{t}" {"selected" if t == target else ""}>{t}</option>'
        for t in all_targets
    )

    sessions_html = ""
    for day in sorted(by_date.keys(), reverse=True):
        day_frames = by_date[day]
        day_net = sum(1 for f in day_frames if not f.get("exclude"))
        day_exp = sum((f.get("exposure_time") or 0) for f in day_frames if not f.get("exclude"))

        rows = ""
        for f in day_frames:
            fname = f.get("file_name") or ""
            exc = bool(f.get("exclude"))
            fwhm = f.get("fwhm")
            ecc = f.get("eccentricity")
            snr = f.get("snr")
            exp = f.get("exposure_time") or 0
            fp_enc = urllib.parse.quote(f.get("file_path") or "", safe="")

            fwhm_str = f'{fwhm:.2f}"' if fwhm else "—"
            ecc_str = f"{ecc:.3f}" if ecc is not None else "—"
            snr_str = f"{snr:.1f}" if snr is not None else "—"

            exc_class = " fr-excluded" if exc else ""
            btn_label = "Include" if exc else "Exclude"
            btn_col = "#3fb950" if exc else "#f85149"
            rows += (
                f'<tr class="fr-row{exc_class}" id="fr-{f.get("id","")}"><td class="fc-fname">'
                f'<span class="fname">{fname}</span></td>'
                f'<td class="fc-num">{exp:.0f}s</td>'
                f'<td class="fc-num">{fwhm_str}</td>'
                f'<td class="fc-num">{ecc_str}</td>'
                f'<td class="fc-num">{snr_str}</td>'
                f'<td class="fc-act"><button class="exc-btn" '
                f'style="border-color:{btn_col};color:{btn_col}" '
                f'onclick="toggleExclude(this,\'{fp_enc}\')">{btn_label}</button></td></tr>'
            )

        sessions_html += (
            f'<details open style="margin-bottom:.75rem">'
            f'<summary class="session-hdr">'
            f'<span class="session-date">{day}</span>'
            f'<span class="session-meta">{day_net}/{len(day_frames)} subs'
            f' &middot; {fmt_time(day_exp)}</span></summary>'
            f'<div class="tbl-scroll"><table class="frames-table">'
            f'<thead><tr><th style="text-align:left">File</th>'
            f'<th>Exp</th><th>FWHM</th><th>Ecc</th><th>SNR</th><th>Action</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div></details>'
        )

    body = f"""
<div class="fp-wrap">
  <div class="fp-header">
    <h1 class="fp-title">{target} — Captures</h1>
    <div class="sum-bar">
      <span class="sum-item"><b>{total}</b> subs</span>
      <span class="sum-item"><b>{net}</b> net</span>
      <span class="sum-item"><b>{fmt_time(net_exp)}</b> integration</span>
    </div>
  </div>
  <form onsubmit="window.location='/frames/'+encodeURIComponent(this.t.value);return false"
        style="display:flex;gap:.5rem;align-items:center;margin-bottom:1.25rem">
    <select name="t" class="sel">{opts}</select>
    <button type="submit" class="go-btn">Go</button>
  </form>
  {sessions_html}
</div>

<script>
function toggleExclude(btn, fp) {{
  fetch('/light_files/toggle_exclude?file_path=' + fp, {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{
      var row = btn.closest('tr');
      if (d.excluded) {{
        row.classList.add('fr-excluded');
        btn.textContent = 'Include';
        btn.style.borderColor = '#3fb950';
        btn.style.color = '#3fb950';
      }} else {{
        row.classList.remove('fr-excluded');
        btn.textContent = 'Exclude';
        btn.style.borderColor = '#f85149';
        btn.style.color = '#f85149';
      }}
    }});
}}
</script>"""

    return _shell(f"Captures: {target} — SeeStar", body, _FRAMES_CSS)


_FRAMES_CSS = """
  .fp-wrap { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  .fp-header { display: flex; justify-content: space-between; align-items: flex-start;
               flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
  .fp-title { font-size: 1.4rem; }
  .sum-bar { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
  .sum-item { background: var(--bg2); border: 1px solid var(--border); border-radius: 4px;
              padding: 3px 10px; font-size: .82rem; color: var(--text2); }
  .sum-item b { color: var(--text); }
  .sel { background: var(--bg2); border: 1px solid var(--border); color: var(--text);
         border-radius: 4px; padding: .3rem .5rem; font-size: .88rem; }
  .go-btn { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
             padding: .3rem .8rem; border-radius: 4px; cursor: pointer; }
  details summary { list-style: none; }
  details summary::-webkit-details-marker { display: none; }
  .session-hdr { display: flex; justify-content: space-between; align-items: center;
                 background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
                 padding: .5rem .9rem; cursor: pointer; font-size: .88rem; user-select: none; }
  .session-hdr:hover { background: var(--bg3); }
  .session-date { font-weight: 600; }
  .session-meta { color: var(--text2); font-size: .82rem; }
  .tbl-scroll { overflow-x: auto; }
  .frames-table { width: 100%; border-collapse: collapse; font-size: .82rem; min-width: 480px; }
  .frames-table thead tr { color: var(--text2); border-bottom: 1px solid var(--border); }
  .frames-table thead th { padding: .35rem .5rem; font-weight: 600; }
  .frames-table tbody tr { border-bottom: 1px solid var(--bg3); transition: opacity .15s; }
  .frames-table tbody tr:last-child { border-bottom: none; }
  .frames-table td { padding: .35rem .5rem; }
  .fc-fname { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fname { font-family: monospace; font-size: .78rem; }
  .fc-num { text-align: right; color: var(--text2); }
  .fc-act { text-align: center; }
  .fr-excluded { opacity: .35; }
  .exc-btn { background: none; border: 1px solid; border-radius: 4px; padding: 2px 8px;
             font-size: .75rem; cursor: pointer; }
  @media (max-width: 600px) {
    .fp-title { font-size: 1.1rem; }
    .fc-fname { max-width: 140px; }
  }
"""


# ---------------------------------------------------------------------------
# Pipeline management page  (/pipeline-view)
# ---------------------------------------------------------------------------

def pipeline_page() -> str:
    from nas_server.database import get_pipeline_with_stats

    rows_data = get_pipeline_with_stats()

    rows_html = ""
    for r in rows_data:
        target = r["target"]
        stage = r.get("stage", "captured")
        total_f = r.get("total_frames", 0) or 0
        net_f = r.get("net_frames", 0) or 0
        net_s = r.get("net_seconds", 0) or 0
        hours = net_s / 3600

        ls = r.get("last_stack") or {}
        stack_date_sort = ""
        if ls:
            if ls.get("success"):
                stack_badge = '<span class="stack-ok">✓ stacked</span>'
            else:
                err_snippet = (ls.get("error") or "unknown error")[:80]
                stack_badge = f'<span class="stack-err" title="{err_snippet}">✗ failed</span>'
            stack_date = (ls.get("finished_at") or "")[:10]
            stack_date_sort = stack_date
            stack_engine = ls.get("engine", "")
            stack_cell = f'{stack_badge} <span class="stack-meta">{stack_engine} {stack_date}</span>'
        else:
            stack_cell = '<span style="color:var(--text2)">—</span>'

        tgt_enc = _uparse.quote(target, safe="")
        tgt_js = target.replace("'", "\\'")
        rows_html += (
            f'<tr data-target="{target.lower()}" data-stage="{stage}"'
            f'    data-frames="{net_f}" data-hours="{hours:.2f}"'
            f'    data-stack-date="{stack_date_sort}">'
            f'<td class="pl-target"><a href="/target/{tgt_enc}">{target}</a></td>'
            f'<td>{_stage_badge(stage)}</td>'
            f'<td class="pl-num">{net_f:,}/{total_f:,}</td>'
            f'<td class="pl-num">{hours:.1f}h</td>'
            f'<td class="pl-stack">{stack_cell}</td>'
            f'<td class="pl-act">'
            f'<a href="/frames/{tgt_enc}" class="pl-link">Captures</a>'
            f'<a href="/fits/{tgt_enc}" class="pl-link">FITS</a>'
            f'<a href="/stack-history/{tgt_enc}" class="pl-link">Stacks</a>'
            f'<a href="/queue-view?target={tgt_enc}" class="pl-link pl-queue">＋ Queue</a>'
            f'<label class="pl-af-label" title="Auto-stack after transfer">'
            f'<input type="checkbox" class="pl-af-stack" data-tenc="{tgt_enc}"> Stack</label>'
            f'<label class="pl-af-label" title="Auto-process after stack">'
            f'<input type="checkbox" class="pl-af-proc" data-tenc="{tgt_enc}"> Process</label>'
            f'</td>'
            f'</tr>'
        )

    if not rows_html:
        rows_html = '<tr><td colspan="6" style="padding:1.5rem;color:var(--text2);text-align:center">No targets in pipeline yet. Run <code>seestar scan</code> to index the library.</td></tr>'

    body = f"""
<div class="pl-wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;
              flex-wrap:wrap;gap:.75rem;margin-bottom:1rem">
    <h1 style="font-size:1.4rem">Pipeline</h1>
    <div style="display:flex;gap:.5rem">
      <button class="act-btn"
              hx-post="/scan"
              hx-indicator="#scan-spinner"
              onclick="this.textContent='Scanning…';this.disabled=true;
                       setTimeout(()=>{{this.textContent='Scan NAS';this.disabled=false}},5000)">
        Scan NAS
      </button>
    </div>
  </div>

  <!-- Incoming / watcher status -->
  <div id="watcher-panel" style="margin-bottom:1rem"></div>

  <!-- Filter bar -->
  <div style="display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:.9rem;align-items:center">
    <input id="pl-filter-text" type="search" placeholder="Filter targets…"
           oninput="plFilter()"
           style="padding:.35rem .65rem;border:1px solid var(--border);border-radius:5px;
                  background:var(--bg2);color:var(--text);font-size:.85rem;width:200px">
    <select id="pl-filter-stage" onchange="plFilter()"
            style="padding:.35rem .65rem;border:1px solid var(--border);border-radius:5px;
                   background:var(--bg2);color:var(--text);font-size:.85rem">
      <option value="">All stages</option>
      <option value="captured">Captured</option>
      <option value="stacked">Stacked</option>
      <option value="processed">Processed</option>
    </select>
    <span id="pl-count" style="font-size:.82rem;color:var(--text2)"></span>
  </div>

  <div class="tbl-scroll">
    <table class="pl-table" id="pl-table">
      <thead><tr>
        <th class="pl-sortable" data-col="target" style="text-align:left">Target <span class="pl-sort-ind"></span></th>
        <th class="pl-sortable" data-col="stage"  style="text-align:left">Stage <span class="pl-sort-ind"></span></th>
        <th class="pl-sortable" data-col="frames">Frames <span class="pl-sort-ind"></span></th>
        <th class="pl-sortable" data-col="hours">Integration <span class="pl-sort-ind"></span></th>
        <th class="pl-sortable" data-col="stack-date" style="text-align:left">Last Stack <span class="pl-sort-ind"></span></th>
        <th></th>
      </tr></thead>
      <tbody id="pl-tbody">{rows_html}</tbody>
    </table>
  </div>
</div>

<script>
(function() {{
  var sortCol = 'target', sortAsc = true;

  function rows() {{
    return Array.from(document.querySelectorAll('#pl-tbody tr[data-target]'));
  }}

  function plFilter() {{
    var text  = document.getElementById('pl-filter-text').value.toLowerCase().trim();
    var stage = document.getElementById('pl-filter-stage').value;
    var vis = 0;
    rows().forEach(function(r) {{
      var show = (!text  || r.dataset.target.includes(text)) &&
                 (!stage || r.dataset.stage === stage);
      r.style.display = show ? '' : 'none';
      if (show) vis++;
    }});
    var total = rows().length;
    document.getElementById('pl-count').textContent =
      vis === total ? total + ' targets' : vis + ' of ' + total;
  }}

  function plSort(col) {{
    if (sortCol === col) {{ sortAsc = !sortAsc; }}
    else {{ sortCol = col; sortAsc = true; }}

    // Update header indicators
    document.querySelectorAll('.pl-sortable').forEach(function(th) {{
      var ind = th.querySelector('.pl-sort-ind');
      if (th.dataset.col === sortCol) {{
        ind.textContent = sortAsc ? ' ▲' : ' ▼';
        th.classList.add('pl-sort-active');
      }} else {{
        ind.textContent = '';
        th.classList.remove('pl-sort-active');
      }}
    }});

    var tbody = document.getElementById('pl-tbody');
    rows().sort(function(a, b) {{
      var av, bv;
      if (col === 'target' || col === 'stage' || col === 'stack-date') {{
        av = a.dataset[col === 'stack-date' ? 'stackDate' : col] || '';
        bv = b.dataset[col === 'stack-date' ? 'stackDate' : col] || '';
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      }} else {{
        av = parseFloat(a.dataset[col]) || 0;
        bv = parseFloat(b.dataset[col]) || 0;
        return sortAsc ? av - bv : bv - av;
      }}
    }}).forEach(function(r) {{ tbody.appendChild(r); }});
  }}

  // Wire sort headers
  document.querySelectorAll('.pl-sortable').forEach(function(th) {{
    th.addEventListener('click', function() {{ plSort(th.dataset.col); }});
  }});

  // Initial count
  plFilter();
  // Default sort by target
  plSort('target');

  // Expose filter for oninput/onchange
  window.plFilter = plFilter;

  // Watcher status panel
  function fmtAge(s) {{
    if (s === null || s === undefined) return '—';
    if (s < 60) return Math.round(s) + 's';
    return Math.round(s / 60) + 'm ' + (Math.round(s) % 60) + 's';
  }}

  function renderWatcher(d) {{
    var panel = document.getElementById('watcher-panel');
    if (!panel) return;
    if (!d.mounted) {{
      panel.innerHTML = '<div class="w-panel"><span style="color:var(--text2);font-size:.83rem">📡 SeeStar not connected</span></div>';
      return;
    }}
    if (!d.sessions || !d.sessions.length) {{
      panel.innerHTML = '';
      return;
    }}
    var rows = d.sessions.map(function(s) {{
      var pct = d.stability_wait_s > 0 ? Math.min(100, (s.age_s / d.stability_wait_s) * 100) : 100;
      var barColor = s.stable ? '#3fb950' : (pct > 60 ? '#e3b341' : '#58a6ff');
      var status = s.stable
        ? '<span style="color:#3fb950;font-weight:600">Ready — transferring…</span>'
        : s.capturing
          ? '<span style="color:#58a6ff">● Capturing</span> · last frame <b>' + fmtAge(s.age_s) + '</b> ago'
          : 'Idle for <b>' + fmtAge(s.age_s) + '</b> · <span style="color:var(--text2)">' + fmtAge(s.remaining_s) + ' until transfer</span>';
      return '<div class="w-session">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3rem">'
        + '<span style="font-weight:600;color:var(--text)">' + s.target + '</span>'
        + '<span style="font-size:.78rem;color:var(--text2)">' + status + '</span>'
        + '</div>'
        + '<div class="w-bar-bg"><div class="w-bar" style="width:' + pct.toFixed(1) + '%;background:' + barColor + '"></div></div>'
        + '</div>';
    }}).join('');
    panel.innerHTML = '<div class="w-panel"><div class="w-title">📡 Incoming</div>' + rows + '</div>';
  }}

  function pollWatcher() {{
    fetch('/watcher/status').then(function(r) {{ return r.json(); }}).then(renderWatcher).catch(function() {{}});
  }}
  pollWatcher();
  setInterval(pollWatcher, 10000);
}})();

// ── Automation flags (auto-stack / auto-process per target) ──────────────
(function() {{
  function setPlAF(tenc, key, val) {{
    fetch('/planner/autoflags', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{target: decodeURIComponent(tenc), [key]: val}})
    }});
  }}
  fetch('/planner/autoflags').then(function(r) {{ return r.json(); }}).then(function(flags) {{
    document.querySelectorAll('.pl-af-stack').forEach(function(cb) {{
      const tenc = cb.dataset.tenc;
      const f = flags[decodeURIComponent(tenc)] || {{}};
      cb.checked = !!f.auto_stack;
      cb.addEventListener('change', function() {{ setPlAF(tenc, 'auto_stack', cb.checked); }});
    }});
    document.querySelectorAll('.pl-af-proc').forEach(function(cb) {{
      const tenc = cb.dataset.tenc;
      const f = flags[decodeURIComponent(tenc)] || {{}};
      cb.checked = !!f.auto_process;
      cb.addEventListener('change', function() {{ setPlAF(tenc, 'auto_process', cb.checked); }});
    }});
  }});
}})();
</script>"""

    css = """
  .pl-wrap { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  .tbl-scroll { overflow-x: auto; }
  .pl-table { width: 100%; border-collapse: collapse; font-size: .85rem; min-width: 580px; }
  .pl-table thead tr { color: var(--text2); border-bottom: 1px solid var(--border); }
  .pl-table th { padding: .4rem .6rem; font-weight: 600; }
  .pl-sortable { cursor: pointer; user-select: none; white-space: nowrap; }
  .pl-sortable:hover { color: var(--text); }
  .pl-sort-active { color: var(--accent); }
  .pl-sort-ind { font-size: .7rem; opacity: .7; }
  .pl-table tbody tr { border-bottom: 1px solid var(--bg3); }
  .pl-table tbody tr:hover { background: var(--bg2); }
  .pl-table td { padding: .45rem .6rem; vertical-align: middle; }
  .pl-target a { font-weight: 600; color: var(--text); }
  .pl-target a:hover { color: var(--accent); }
  .pl-num { text-align: right; color: var(--text2); }
  .pl-stack { font-size: .82rem; }
  .stack-ok { color: #3fb950; font-weight: 600; }
  .stack-err { color: #f85149; font-weight: 600; cursor: help; }
  .stack-meta { color: var(--text2); font-size: .78rem; }
  .pl-act { white-space: nowrap; }
  .pl-link { font-size: .78rem; color: var(--accent); margin-right: .5rem; }
  .pl-queue { background: var(--accent); color: #fff !important; padding: .15rem .45rem;
              border-radius: 4px; font-size: .75rem; }
  .pl-queue:hover { opacity: .85; }
  .pl-af-label { font-size: .72rem; color: var(--text2); margin-right: .4rem;
                 cursor: pointer; white-space: nowrap; }
  .pl-af-label input { margin-right: 2px; vertical-align: middle; }
  .act-btn { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
              padding: .35rem .9rem; border-radius: 5px; cursor: pointer; font-size: .85rem; }
  .act-btn:hover { border-color: var(--accent); }
  .w-panel { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
             padding: .75rem 1rem; margin-bottom: 1rem; }
  .w-title { font-size: .78rem; font-weight: 600; color: var(--text2); text-transform: uppercase;
             letter-spacing: .07em; margin-bottom: .6rem; }
  .w-session { margin-bottom: .5rem; }
  .w-session:last-child { margin-bottom: 0; }
  .w-bar-bg { background: var(--bg3); border-radius: 4px; height: 5px; overflow: hidden; }
  .w-bar { height: 5px; border-radius: 4px; transition: width .5s ease; }
  @media (max-width: 600px) {
    .pl-table { font-size: .78rem; }
  }
"""
    return _shell("Pipeline — SeeStar", body, css)


# ---------------------------------------------------------------------------
# Stack history / debug page  (/stack-history  or  /stack-history/{target})
# ---------------------------------------------------------------------------

def stack_history_page(target: str | None = None) -> str:
    import json as _json
    from nas_server.database import get_stacking_runs_with_scores, get_story_data

    runs = get_stacking_runs_with_scores(target=target, limit=100)
    all_targets = sorted({t["target"] for t in get_story_data()})

    opts = '<option value="">— All targets —</option>' + "".join(
        f'<option value="{t}" {"selected" if t == target else ""}>{t}</option>'
        for t in all_targets
    )
    selector = (
        '<form onsubmit="var v=this.t.value;'
        "window.location=v?'/stack-history/'+encodeURIComponent(v):'/stack-history';"
        'return false" style="display:flex;gap:.5rem;align-items:center;margin-bottom:1.5rem">'
        f'<select name="t" class="sel">{opts}</select>'
        '<button type="submit" class="go-btn">Filter</button></form>'
    )

    if target:
        # --- Card grid comparison view ---
        def _score_row(label, key, scores):
            v = scores.get(key)
            return (
                f'<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0">'
                f'<span style="color:var(--text2);font-size:.75rem">{label}</span>'
                f'{_score_pill(v)}</div>'
            )

        cards_html = ""
        for r in runs:
            output_path = r.get("output_path") or ""
            ts = _fmt_mst(r.get("finished_at") or "")
            engine = r.get("engine", "?")
            engine_color = {
                "siril": "#58a6ff",
                "pixinsight_wbpp": "#bc8cff",
                "pixinsight_register": "#a78bfa",
                "imagemm": "#3fb950",
            }.get(engine, "#8b949e")
            frames = r.get("frame_count") or "?"
            elapsed = r.get("elapsed_s")
            elapsed_str = f"{elapsed/60:.0f}min" if elapsed else "?"
            param_tags = _stack_param_tags(r)
            scores = r.get("claude_scores") or {}
            overall = scores.get("overall")

            # Preview image: swap .fit → .jpg
            if output_path:
                from pathlib import Path as _P
                jpg_name = _P(output_path).stem + ".jpg"
                img_url = f"/image/{_uparse.quote(target, safe='')}/{_uparse.quote(jpg_name, safe='')}"
                img_html = (
                    f'<a href="{img_url}" target="_blank">'
                    f'<img src="{img_url}" loading="lazy" '
                    f'style="width:100%;height:160px;object-fit:cover;display:block;'
                    f'border-radius:6px 6px 0 0" '
                    f'onerror="this.parentNode.outerHTML=\'<div style=\\\"width:100%;height:160px;'
                    f'background:var(--bg3);border-radius:6px 6px 0 0;display:flex;'
                    f'align-items:center;justify-content:center;color:var(--text2);'
                    f'font-size:2rem\\\">&#127756;</div>\'">'
                    f'</a>'
                )
            else:
                img_html = (
                    '<div style="width:100%;height:160px;background:var(--bg3);'
                    'border-radius:6px 6px 0 0;display:flex;align-items:center;'
                    'justify-content:center;color:var(--text2);font-size:2rem">&#127756;</div>'
                )

            # Physics metrics — all 8 fields as labeled rows
            def _mrow(label, val, fmt, good_hi=True, warn_thresh=None, bad_thresh=None):
                if val is None:
                    return (f'<div style="display:flex;justify-content:space-between;'
                            f'padding:2px 0"><span style="color:var(--text2);font-size:.75rem">'
                            f'{label}</span><span style="color:var(--text2)">—</span></div>')
                txt = fmt.format(val)
                color = "var(--text)"
                if warn_thresh is not None and bad_thresh is not None:
                    if good_hi:
                        color = "#3fb950" if val >= warn_thresh else ("#e3b341" if val >= bad_thresh else "#f85149")
                    else:
                        color = "#3fb950" if val <= warn_thresh else ("#e3b341" if val <= bad_thresh else "#f85149")
                return (f'<div style="display:flex;justify-content:space-between;padding:2px 0">'
                        f'<span style="color:var(--text2);font-size:.75rem">{label}</span>'
                        f'<span style="color:{color};font-size:.78rem;font-weight:600">{txt}</span></div>')

            physics_rows = (
                _mrow("SNR", r.get("snr_stack"), "{:.1f}", good_hi=True, warn_thresh=15, bad_thresh=8) +
                _mrow("FWHM", r.get("fwhm_stack"), "{:.2f} px", good_hi=False, warn_thresh=2.5, bad_thresh=3.5) +
                _mrow("Eccentricity", r.get("ecc_stack"), "{:.3f}", good_hi=False, warn_thresh=0.35, bad_thresh=0.5) +
                _mrow("Sky σ", r.get("sigma_sky"), "{:.1f}", good_hi=False, warn_thresh=50, bad_thresh=150) +
                _mrow("Flatness RMS", r.get("flatness_rms"), "{:.4f}", good_hi=False, warn_thresh=0.01, bad_thresh=0.05) +
                _mrow("Clipping", r.get("clipping_frac"), "{:.1%}", good_hi=False, warn_thresh=0.05, bad_thresh=0.15) +
                _mrow("Stars", r.get("star_count_stack"), "{:,}", good_hi=True, warn_thresh=500, bad_thresh=100) +
                _mrow("Efficiency", r.get("efficiency"), "{:.3f}", good_hi=True, warn_thresh=0.9, bad_thresh=0.7)
            )
            has_physics = any(r.get(k) is not None for k in
                              ("snr_stack","fwhm_stack","ecc_stack","sigma_sky",
                               "flatness_rms","clipping_frac","star_count_stack","efficiency"))

            # Claude score rows
            score_rows = ""
            if scores:
                for lbl, key in [
                    ("Overall", "overall"),
                    ("Noise", "noise"),
                    ("Color", "color_balance"),
                    ("Gradient", "gradient"),
                    ("Stars", "star_roundness"),
                    ("Stretch", "stretch_quality"),
                ]:
                    score_rows += _score_row(lbl, key, scores)
                issues = scores.get("issues") or []
                for iss in issues[:2]:
                    score_rows += (
                        f'<div style="font-size:.72rem;color:#e3b341;margin-top:3px">'
                        f'&#9888; {iss[:80]}</div>'
                    )
            else:
                score_rows = '<div style="color:var(--text2);font-size:.78rem">No assessment</div>'

            cards_html += f"""
<div class="sh-card" data-engine="{engine}" style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;overflow:hidden;display:flex;flex-direction:column">
  {img_html}
  <div style="padding:.7rem .8rem;flex:1;display:flex;flex-direction:column;gap:.3rem">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-weight:700;font-size:.85rem;color:{engine_color}">{engine}</span>
      {_score_pill(overall)}
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:3px">{param_tags}</div>
    <div style="font-size:.74rem;color:var(--text2)">{frames} frames · {elapsed_str} · {ts}</div>
    {'<div style="border-top:1px solid var(--border);margin-top:.3rem;padding-top:.3rem"><div style="font-size:.7rem;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Physics</div>' + physics_rows + '</div>' if has_physics else ''}
    <div style="border-top:1px solid var(--border);margin-top:.3rem;padding-top:.3rem">
      <div style="font-size:.7rem;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Claude</div>
      {score_rows}
    </div>
  </div>
</div>"""

        content = f"""
<div style="max-width:1200px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.4rem;margin-bottom:1rem">
    Stack Comparison — <span style="color:var(--accent)">{target}</span>
    <span style="color:var(--text2);font-size:.85rem;font-weight:400">({len(runs)} runs)</span>
  </h1>
  {selector}
  <div id="engine-tabs">
    <button class="etab active" data-engine="all">All <span class="etab-count"></span></button>
    <button class="etab" data-engine="siril">Siril <span class="etab-count"></span></button>
    <button class="etab" data-engine="imagemm">ImageMM <span class="etab-count"></span></button>
    <button class="etab" data-engine="pixinsight_wbpp">WBPP <span class="etab-count"></span></button>
    <button class="etab" data-engine="pixinsight_register">PI Register <span class="etab-count"></span></button>
  </div>
  <div id="sh-card-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1rem">
    {cards_html or '<p style="color:var(--text2)">No completed stacking runs for this target.</p>'}
  </div>
</div>
<script>
(function(){{
  var tabs = document.querySelectorAll('.etab');
  var grid = document.getElementById('sh-card-grid');
  var allCards = grid ? Array.from(grid.querySelectorAll('.sh-card')) : [];
  function updateCount() {{
    document.querySelector('.etab[data-engine="all"] .etab-count').textContent =
      '(' + allCards.length + ')';
    ['siril','imagemm','pixinsight_wbpp','pixinsight_register'].forEach(function(e) {{
      var n = allCards.filter(function(c) {{ return c.dataset.engine === e; }}).length;
      var sp = document.querySelector('.etab[data-engine="' + e + '"] .etab-count');
      if (sp) sp.textContent = n ? '(' + n + ')' : '';
    }});
  }}
  tabs.forEach(function(tab) {{
    tab.addEventListener('click', function() {{
      var eng = this.dataset.engine;
      tabs.forEach(function(t) {{ t.classList.remove('active'); }});
      this.classList.add('active');
      allCards.forEach(function(c) {{
        c.style.display = (eng === 'all' || c.dataset.engine === eng) ? '' : 'none';
      }});
    }});
  }});
  updateCount();
}})();
</script>"""

    else:
        # --- All-targets table ---
        rows_html = ""
        for r in runs:
            tgt = r.get("target", "")
            engine = r.get("engine", "")
            ts = _fmt_mst(r.get("finished_at") or "")
            frames = r.get("frame_count") or "—"
            elapsed = r.get("elapsed_s")
            elapsed_str = f"{elapsed:.0f}s" if elapsed else "—"
            eff = r.get("efficiency")
            scores = r.get("claude_scores") or {}

            if eff is not None:
                if eff >= 0.9:
                    eff_cell = f'<span class="sh-eff-good">{eff:.2f}</span>'
                elif eff >= 0.7:
                    eff_cell = f'<span class="sh-eff-warn">{eff:.2f}</span>'
                else:
                    eff_cell = f'<span class="sh-eff-bad">{eff:.2f}</span>'
            else:
                eff_cell = '<span class="sh-num">—</span>'

            param_tags = _stack_param_tags(r)
            claude_cell = _score_pill(scores.get("overall")) if scores else '<span style="color:var(--text2)">—</span>'

            snr = r.get("snr_stack")
            fwhm = r.get("fwhm_stack")
            sky = r.get("sigma_sky")
            stars = r.get("star_count_stack")
            tip_parts = []
            if snr is not None: tip_parts.append(f"SNR {snr:.1f}")
            if fwhm is not None: tip_parts.append(f"FWHM {fwhm:.2f}px")
            if sky is not None: tip_parts.append(f"sky σ {sky:.1f}")
            if stars is not None: tip_parts.append(f"{stars} stars")
            tip = f' title="{" · ".join(tip_parts)}"' if tip_parts else ""

            rows_html += (
                f'<tr{tip}>'
                f'<td class="sh-ts">{ts}</td>'
                f'<td class="sh-target"><a href="/stack-history/{_uparse.quote(tgt, safe="")}">{tgt}</a></td>'
                f'<td class="sh-eng">{engine}</td>'
                f'<td class="sh-params">{param_tags}</td>'
                f'<td class="sh-num">{frames}</td>'
                f'<td class="sh-num">{elapsed_str}</td>'
                f'<td class="sh-num">{eff_cell}</td>'
                f'<td class="sh-num">{claude_cell}</td>'
                f'</tr>'
            )

        if not rows_html:
            rows_html = '<tr><td colspan="8" style="padding:1.5rem;color:var(--text2);text-align:center">No stacking runs recorded yet.</td></tr>'

        content = f"""
<div style="max-width:1200px;margin:0 auto;padding:2rem 1.5rem">
  <h1 style="font-size:1.4rem;margin-bottom:1rem">Stack History</h1>
  {selector}
  <div class="sh-wrap">
  <table class="sh-table">
    <thead><tr>
      <th>Time</th><th>Target</th><th>Engine</th><th>Params</th>
      <th>Frames</th><th>Elapsed</th><th>Eff</th><th>Claude</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
  <p style="margin-top:.75rem;font-size:.78rem;color:var(--text2)">
    Click a target to see its comparison card view. Hover a row to see SNR, FWHM, sky noise, and star count.
    Efficiency = measured SNR ÷ ideal √N SNR (1.0 = perfect stacking).
  </p>
</div>"""

    css = """
  .sh-wrap { overflow-x: auto; }
  .sh-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
  .sh-table th { text-align: left; padding: .5rem .8rem; color: var(--text2);
                  border-bottom: 1px solid var(--border); white-space: nowrap; }
  .sh-table td { padding: .45rem .8rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  .sh-table tr:hover td { background: var(--bg2); }
  .sh-ts { color: var(--text2); white-space: nowrap; }
  .sh-eng { font-family: monospace; font-size: .78rem; }
  .sh-num { text-align: right; color: var(--text2); white-space: nowrap; }
  .sh-params { white-space: nowrap; }
  .sh-target a { color: var(--accent); text-decoration: none; }
  .sh-target a:hover { text-decoration: underline; }
  .sh-ok  { color: #3fb950; }
  .sh-err { color: #f85149; }
  .sh-eff-good { color: #3fb950; font-weight: 600; }
  .sh-eff-warn { color: #e3b341; font-weight: 600; }
  .sh-eff-bad  { color: #f85149; font-weight: 600; }
  #engine-tabs { display: flex; gap: .4rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .etab { background: var(--bg2); border: 1px solid var(--border); color: var(--text2);
          border-radius: 6px; padding: .3rem .8rem; cursor: pointer; font-size: .82rem; }
  .etab:hover { background: var(--bg3); color: var(--text); }
  .etab.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  .etab-count { font-weight: 400; opacity: .75; }
  .sh-log { background: var(--bg3); padding: .5rem; border-radius: 4px; font-size: .72rem;
             white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; }
  .sh-log-details summary { cursor: pointer; color: var(--text2); font-size: .78rem; }
  .sh-err-msg { color: #f85149; font-size: .78rem; margin-top: .25rem; }
  .sh-detail-row td { background: var(--bg2); }
  .sel { background: var(--bg2); border: 1px solid var(--border); color: var(--text);
          padding: .3rem .5rem; border-radius: 4px; }
  .go-btn { background: var(--accent); color: #0d1117; border: none; padding: .3rem .8rem;
             border-radius: 4px; cursor: pointer; font-weight: 600; }
"""
    return _shell(f"Stacks{' — ' + target if target else ''} — SeeStar", content, css)


# ---------------------------------------------------------------------------
# FITS detail viewer  (/fits/{target}/{path:path})
# ---------------------------------------------------------------------------

def _list_target_fits(target: str) -> list[Path]:
    """Return all FITS files for a target (raw stacks then processed), paths relative to target dir."""
    from nas_server.config import settings
    lib = Path(settings["seestar_library_path"])
    tdir = lib / target
    if not tdir.is_dir():
        return []
    _exts = ("*.fit", "*.fits", "*.xisf", "*.tif", "*.tiff")
    raw = [p for ext in _exts for p in sorted(tdir.glob(ext))]
    proc_dir = tdir / "_processed"
    proc = ([p for ext in _exts for p in sorted(proc_dir.glob(ext))]
            if proc_dir.is_dir() else [])
    return [f.relative_to(tdir) for f in raw + proc]


def fits_viewer_page(target: str, path: str) -> str:
    from nas_server.config import settings
    from nas_server.database import is_raw_stack

    lib = Path(settings["seestar_library_path"])
    fits_path = lib / target / path

    all_fits = _list_target_fits(target)
    current_rel = Path(path)
    try:
        idx = next(i for i, f in enumerate(all_fits) if f == current_rel)
    except StopIteration:
        idx = 0

    def fits_url(rel: Path) -> str:
        return f"/fits/{_uparse.quote(target, safe='')}/{_uparse.quote(str(rel), safe='/')}"

    prev_url = fits_url(all_fits[idx - 1]) if idx > 0 else ""
    next_url = fits_url(all_fits[idx + 1]) if idx < len(all_fits) - 1 else ""

    tgt_enc = _uparse.quote(target, safe="")
    path_enc = _uparse.quote(path, safe="/")
    base_img_src = f"/fits-preview/{tgt_enc}/{path_enc}"

    # Already-stretched files default to a no-STF view: TIFF (Photoshop export)
    # is a finished raster; files in _processed/ are processed finals. Raw stacks
    # (target root) stay STF-on. The toggle is still available either way.
    suffix_l = Path(path).suffix.lower()
    is_raster = suffix_l in (".tif", ".tiff")
    # A raw stack is linear (STF on) even though it lives in _processed/; only a
    # genuine processed output is non-linear and defaults to a no-STF view.
    is_proc_file = path.startswith("_processed") and not is_raw_stack(Path(path).name)
    default_stf = not (is_proc_file or is_raster)
    init_src = base_img_src if default_stf else base_img_src + "?stf=0"
    _stf_checked = "checked" if default_stf else ""
    _stf_label = "STF auto-stretch" if default_stf else "Linear (no stretch)"
    _sl_disabled = "" if default_stf else "disabled"
    # TIFF/PSD are finished rasters — no STF concept at all, hide the stretch UI.
    _ctrl_display = "none" if is_raster else "flex"

    # Sidebar file list
    file_items = ""
    for i, f in enumerate(all_fits):
        # "proc" only for genuine processed outputs; raw stacks live in _processed/
        # too but are raw stacker results, so they stay tagged "raw".
        is_proc = str(f).startswith("_processed") and not is_raw_stack(f.name)
        tag_color = "#3fb950" if is_proc else "#58a6ff"
        tag_label = "proc" if is_proc else "raw"
        active_style = "background:var(--bg3);border-color:var(--accent);" if i == idx else ""
        file_items += (
            f'<a href="{fits_url(f)}" class="fv-file" style="{active_style}">'
            f'<span class="fv-tag" style="color:{tag_color};border-color:{tag_color}">{tag_label}</span>'
            f'<span class="fv-fname-sm">{f.name}</span>'
            f'</a>'
        )

    # Prev/Next buttons
    def nav_btn(url, label, arrow):
        if url:
            return f'<a href="{url}" class="fv-nav-btn">{arrow} {label}</a>'
        return f'<span class="fv-nav-btn fv-nav-dis">{arrow} {label}</span>'

    _file_list_html = file_items or '<p style="color:var(--text2);padding:.5rem">No FITS found</p>'

    body = f"""
<div class="fv-layout">

  <div class="fv-sidebar">
    <div class="fv-sidebar-hdr">{target}</div>
    <div class="fv-file-list">{_file_list_html}</div>
  </div>

  <div class="fv-main">
    <div class="fv-toolbar">
      <div class="fv-current-name">{Path(path).name}</div>
      <div class="fv-nav-row">
        <button id="fv-queue-btn" class="fv-nav-btn"
          onclick="queueAutoProcess('{tgt_enc}', '{_uparse.quote(Path(path).name, safe="")}')"
          title="Queue an auto-process run using this file as the source stack"
          style="cursor:pointer">&#9881; Add to Queue</button>
        {nav_btn(prev_url, "Prev", "&#8592;")}
        <span class="fv-pos">{idx + 1} / {len(all_fits)}</span>
        {nav_btn(next_url, "Next", "&#8594;")}
      </div>
    </div>

    <div class="fv-img-wrap" id="fv-img-wrap">
      <img id="fv-img" src="{init_src}" class="fv-img" alt="{Path(path).name}"
           onload="document.getElementById('fv-spin').style.display='none'">
      <div id="fv-spin" class="fv-spin">Rendering&#8230;</div>
    </div>

    <div class="fv-controls">
      <div class="fv-ctrl-row" style="display:{_ctrl_display}">
        <label class="fv-ctrl-label">Stretch</label>
        <label class="fv-toggle">
          <input type="checkbox" id="cb-stf" {_stf_checked} onchange="toggleStf()">
          <span id="lbl-stf">{_stf_label}</span>
        </label>
      </div>
      <div class="fv-ctrl-row" style="display:{_ctrl_display}">
        <label class="fv-ctrl-label">Background</label>
        <input type="range" id="sl-target-bg" min="0.10" max="0.40" step="0.01" value="0.25"
               class="fv-slider" oninput="sliderChanged()" {_sl_disabled}>
        <span id="val-target-bg" class="fv-ctrl-val">0.25</span>
      </div>
      <div class="fv-ctrl-row" style="display:{_ctrl_display}">
        <label class="fv-ctrl-label">Shadow Clip</label>
        <input type="range" id="sl-shadow-clip" min="0.50" max="3.00" step="0.05" value="1.25"
               class="fv-slider" oninput="sliderChanged()" {_sl_disabled}>
        <span id="val-shadow-clip" class="fv-ctrl-val">1.25</span>
      </div>
      <a id="fv-dl" href="{init_src}" download="{Path(path).stem}.jpg"
         class="fv-dl-btn">&#8595; Save JPEG</a>
    </div>
  </div>

</div>

<script>
var _base = {_json.dumps(base_img_src)};
var _debounce = null;
function _loadSrc(src) {{
  var img = document.getElementById('fv-img');
  var spin = document.getElementById('fv-spin');
  spin.style.display = 'flex';
  img.style.opacity = '0.3';
  var tmp = new Image();
  tmp.onload = function() {{
    img.src = src;
    img.style.opacity = '1';
    spin.style.display = 'none';
    document.getElementById('fv-dl').href = src;
  }};
  tmp.onerror = function() {{ spin.textContent = 'Error generating preview'; }};
  tmp.src = src;
}}
function toggleStf() {{
  var on = document.getElementById('cb-stf').checked;
  document.getElementById('lbl-stf').textContent = on ? 'STF auto-stretch' : 'Linear (no stretch)';
  document.getElementById('sl-target-bg').disabled = !on;
  document.getElementById('sl-shadow-clip').disabled = !on;
  if (on) {{ sliderChanged(); }}
  else {{ _loadSrc(_base + '?stf=0'); }}
}}
function sliderChanged() {{
  if (!document.getElementById('cb-stf').checked) {{ return; }}
  var bg = parseFloat(document.getElementById('sl-target-bg').value).toFixed(2);
  var sc = parseFloat(document.getElementById('sl-shadow-clip').value).toFixed(2);
  document.getElementById('val-target-bg').textContent = bg;
  document.getElementById('val-shadow-clip').textContent = sc;
  clearTimeout(_debounce);
  _debounce = setTimeout(function() {{
    _loadSrc(_base + '?target_bg=' + bg + '&shadow_clip_k=' + sc);
  }}, 600);
}}
function queueAutoProcess(tgtEnc, fileEnc) {{
  var btn = document.getElementById('fv-queue-btn');
  if (btn) {{ btn.disabled = true; btn.innerHTML = '&#9203; Queuing…'; }}
  var url = '/queue?target=' + tgtEnc + '&workflow=auto&source_file=' + fileEnc;
  fetch(url, {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (btn) {{ btn.innerHTML = '&#10003; Queued'; }}
      alert(d.message || 'Queued.');
    }})
    .catch(function() {{
      alert('Error adding to queue.');
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#9881; Add to Queue'; }}
    }});
}}
</script>"""

    css = """
  * { box-sizing: border-box; }
  .fv-layout { display: flex; height: calc(100vh - 44px); overflow: hidden; }
  .fv-sidebar { width: 260px; flex-shrink: 0; border-right: 1px solid var(--border);
                display: flex; flex-direction: column; overflow: hidden; }
  .fv-sidebar-hdr { padding: .6rem .9rem; font-weight: 700; font-size: .88rem;
                    border-bottom: 1px solid var(--border); color: var(--accent);
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .fv-file-list { overflow-y: auto; flex: 1; padding: .25rem 0; }
  .fv-file { display: flex; align-items: flex-start; gap: .4rem; padding: .35rem .75rem;
             color: var(--text2); text-decoration: none; font-size: .76rem;
             border: 1px solid transparent; border-radius: 4px; margin: 1px .3rem;
             line-height: 1.3; }
  .fv-file:hover { background: var(--bg2); color: var(--text); text-decoration: none; }
  .fv-tag { font-size: .68rem; border: 1px solid; border-radius: 3px; padding: 0 4px;
             white-space: nowrap; flex-shrink: 0; margin-top: 1px; }
  .fv-fname-sm { word-break: break-all; }
  .fv-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .fv-toolbar { display: flex; justify-content: space-between; align-items: center;
                padding: .5rem 1rem; border-bottom: 1px solid var(--border);
                flex-shrink: 0; gap: .5rem; flex-wrap: wrap; }
  .fv-current-name { font-family: monospace; font-size: .82rem; color: var(--text2);
                     overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                     max-width: 50%; }
  .fv-nav-row { display: flex; align-items: center; gap: .5rem; }
  .fv-nav-btn { background: var(--bg2); border: 1px solid var(--border); color: var(--text);
                padding: .25rem .7rem; border-radius: 4px; font-size: .8rem;
                text-decoration: none; white-space: nowrap; }
  .fv-nav-btn:hover { border-color: var(--accent); text-decoration: none; }
  .fv-nav-dis { color: var(--text2); cursor: default; }
  .fv-nav-dis:hover { border-color: var(--border); }
  .fv-pos { font-size: .8rem; color: var(--text2); }
  .fv-img-wrap { flex: 1; display: flex; align-items: center; justify-content: center;
                 background: #000; position: relative; overflow: hidden; }
  .fv-img { max-width: 100%; max-height: 100%; object-fit: contain; transition: opacity .2s; }
  .fv-spin { position: absolute; inset: 0; display: flex; align-items: center;
             justify-content: center; background: rgba(0,0,0,.55);
             color: var(--text2); font-size: .9rem; }
  .fv-controls { flex-shrink: 0; border-top: 1px solid var(--border);
                 padding: .6rem 1rem; display: flex; align-items: center;
                 gap: 1.2rem; flex-wrap: wrap; background: var(--bg2); }
  .fv-ctrl-row { display: flex; align-items: center; gap: .5rem; }
  .fv-ctrl-label { font-size: .78rem; color: var(--text2); white-space: nowrap; }
  .fv-slider { width: 110px; accent-color: var(--accent); }
  .fv-ctrl-val { font-size: .78rem; color: var(--text); font-family: monospace;
                 min-width: 2.5rem; }
  .fv-dl-btn { margin-left: auto; background: var(--bg3); border: 1px solid var(--border);
               color: var(--accent); padding: .3rem .9rem; border-radius: 4px;
               font-size: .8rem; text-decoration: none; white-space: nowrap; }
  .fv-dl-btn:hover { border-color: var(--accent); text-decoration: none; }
  @media (max-width: 700px) {
    .fv-layout { flex-direction: column; height: auto; }
    .fv-sidebar { width: 100%; height: 140px; border-right: none;
                  border-bottom: 1px solid var(--border); }
    .fv-file-list { display: flex; flex-direction: row; overflow-x: auto;
                    overflow-y: hidden; padding: .25rem; gap: .25rem; flex-wrap: nowrap; }
    .fv-file { flex-shrink: 0; flex-direction: column; padding: .25rem .4rem; }
    .fv-img-wrap { min-height: 55vw; }
    .fv-current-name { max-width: 60vw; }
  }
"""

    return _shell(f"{Path(path).name} — SeeStar", body, css)


# ---------------------------------------------------------------------------
# Target detail page  (/target/{target})
# ---------------------------------------------------------------------------

def target_detail_page(target: str) -> str:
    import json as _j
    from nas_server.database import (get_target_detail, get_story_data,
                                     get_stacking_runs, get_claude_history,
                                     get_processed_files, is_raw_stack)
    from nas_server.config import settings

    story = get_story_data(target)
    if not story:
        return _shell(f"{target} — SeeStar",
                      f'<div style="max-width:900px;margin:3rem auto;padding:2rem;'
                      f'color:var(--text2)">Target <b>{target}</b> not found. '
                      f'<a href="/targets-view">← Targets</a></div>')

    t = story[0]
    tgt_enc = _uparse.quote(target, safe="")
    stacks = get_stacking_runs(target=target, limit=30, dedupe_outputs=True)
    assessments = get_claude_history(target=target, limit=3)
    proc_files = get_processed_files(target=target)

    # --- Hero block ---
    preview = t.get("preview_filename")
    if preview:
        img_html = (f'<img src="/image/{tgt_enc}/{_uparse.quote(preview, safe="")}"'
                    f' style="width:100%;max-height:340px;object-fit:cover;border-radius:8px;display:block"'
                    f' loading="lazy">')
    else:
        img_html = ('<div style="width:100%;height:220px;background:var(--bg3);border-radius:8px;'
                    'display:flex;align-items:center;justify-content:center;'
                    'color:var(--text2);font-size:2.5rem">🌌</div>')

    stage = t.get("pipeline_stage", "captured")
    hours = t.get("total_hours") or 0
    total_subs = t.get("total_subs") or 0
    session_count = t.get("session_count") or 0
    first_d = (t.get("first_date") or "")[:10]
    last_d = (t.get("last_date") or "")[:10]

    # avg FWHM from stacking runs (use most recent successful)
    fwhm_vals = [r["fwhm_stack"] for r in stacks if r.get("success") and r.get("fwhm_stack")]
    avg_fwhm = f'{sum(fwhm_vals)/len(fwhm_vals):.2f}"' if fwhm_vals else "—"

    # latest AI score
    latest_scores = t.get("latest_scores") or {}
    overall = latest_scores.get("overall")
    score_html = _score_pill(overall) if overall else ""

    # Saved per-target crop (reused on every process). Surface a clear/redo control.
    try:
        from nas_server.target_crop import get_target_crop as _gtc
        _saved_crop = _gtc(target)
    except Exception:
        _saved_crop = None
    if _saved_crop:
        _sc_src = _saved_crop.get("source") or "?"
        _sc_w = _saved_crop.get("width_arcmin")
        _sc_dims = (f' · {_sc_w:.0f}′×{_saved_crop.get("height_arcmin", 0):.0f}′'
                    if _sc_w else "")
        crop_html = (
            f'<div style="margin-top:.8rem;font-size:.8rem;color:var(--text2)">'
            f'Saved crop: <b>{_sc_src}</b>{_sc_dims} '
            f'<button onclick="clearSavedCrop({_j.dumps(target)})" '
            f'style="margin-left:.5rem;background:var(--bg3);color:var(--text);'
            f'border:1px solid var(--border);border-radius:5px;padding:.2rem .6rem;'
            f'font-size:.78rem;cursor:pointer">Clear &amp; redo crop</button></div>'
            '<script>function clearSavedCrop(t){if(!confirm('
            "'Clear the saved crop for '+t+'? The next process will open a fresh crop review.'"
            ')){return;}fetch("/target/"+encodeURIComponent(t)+"/clear-crop",'
            '{method:"POST"}).then(r=>r.json()).then(_=>location.reload());}</script>'
        )
    else:
        crop_html = ""

    assoc = t.get("association") or ""
    assoc_html = (f'<span style="color:var(--text2);font-size:.82rem">Also: '
                  f'<a href="/target/{_uparse.quote(assoc, safe="")}">{assoc}</a></span>'
                  if assoc else
                  f'<a href="/associations" style="color:var(--text2);font-size:.78rem">'
                  f'+ set association</a>')

    hero_body = f"""
<div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:2rem">
  <div style="flex:1;min-width:260px;max-width:480px">{img_html}</div>
  <div style="flex:1;min-width:220px">
    <div style="display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin-bottom:.6rem">
      <h1 style="font-size:1.6rem;font-weight:700">{target}</h1>
      {_stage_badge(stage)}
      {score_html}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.4rem .8rem;
                font-size:.85rem;margin-bottom:1rem">
      <div><span style="color:var(--text2)">Subs</span><br>
           <b>{total_subs:,}</b></div>
      <div><span style="color:var(--text2)">Integration</span><br>
           <b>{hours:.1f}h</b></div>
      <div><span style="color:var(--text2)">Sessions</span><br>
           <b>{session_count}</b></div>
      <div><span style="color:var(--text2)">Avg FWHM</span><br>
           <b>{avg_fwhm}</b></div>
      <div style="grid-column:1/-1"><span style="color:var(--text2)">Observed</span><br>
           <b>{first_d}</b> → <b>{last_d}</b></div>
    </div>
    {assoc_html}
    {crop_html}
    <div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1.1rem">
      <a href="/frames/{tgt_enc}" class="td-btn">📷 Captures</a>
      <a href="/fits/{tgt_enc}" class="td-btn">🔭 FITS</a>
      <a href="/stack-history/{tgt_enc}" class="td-btn">📊 Stack History</a>
      <a href="/story?target={tgt_enc}" class="td-btn">📖 Story</a>
      <a href="/folio/{tgt_enc}" class="td-btn">📋 Folio</a>
      <a href="/queue-view" class="td-btn td-btn-primary" onclick="
        document.querySelector('#add-job-target').value={_j.dumps(target)};
        document.querySelector('#add-job-section').open=true;
        return false;">+ Queue Stack</a>
    </div>
  </div>
</div>"""

    # --- Stacking runs table ---
    runs_rows = ""
    for r in stacks:
        success = r.get("success")
        engine = r.get("engine", "")
        ts = (r.get("finished_at") or r.get("started_at") or "")[:10]
        frames = r.get("frame_count") or "—"
        elapsed = r.get("elapsed_s")
        elapsed_str = f'{int(elapsed//3600)}h{int((elapsed%3600)//60)}m' if elapsed and elapsed >= 3600 else (f'{elapsed:.0f}s' if elapsed else "—")
        fwhm = r.get("fwhm_stack")
        fwhm_str = f'{fwhm:.2f}"' if fwhm else "—"
        eff = r.get("efficiency")
        if eff is not None:
            eff_color = "#3fb950" if eff >= 0.9 else ("#d29922" if eff >= 0.7 else "#f85149")
            eff_cell = f'<span style="color:{eff_color};font-weight:700;font-family:monospace">{eff:.2f}</span>'
        else:
            eff_cell = '<span style="color:var(--text2)">—</span>'
        status = ('<span style="color:#3fb950;font-weight:600">✓</span>' if success
                  else f'<span style="color:#f85149;font-weight:600" title="{(r.get("error") or "")[:120]}">✗</span>')
        out = r.get("output_path") or ""
        out_link = ""
        if success and out:
            fn = Path(out).name
            fn_enc = _uparse.quote(fn, safe="")
            out_link = f' <a href="/fits/{tgt_enc}/_processed/{fn_enc}" style="font-size:.75rem">view</a>'
        param_tags = _stack_param_tags(r)
        runs_rows += (f'<tr><td class="td-ts">{ts}</td>'
                      f'<td style="font-family:monospace;font-size:.82rem">{engine}</td>'
                      f'<td style="padding:.35rem .5rem;font-size:.78rem">{param_tags}</td>'
                      f'<td class="td-num">{frames:,}</td>'
                      f'<td class="td-num">{elapsed_str}</td>'
                      f'<td class="td-num">{fwhm_str}</td>'
                      f'<td class="td-num">{eff_cell}</td>'
                      f'<td class="td-num">{status}{out_link}</td></tr>')

    stacks_section = f"""
<h2 class="td-h2">Stacking Runs <span style="color:var(--text2);font-size:.82rem;
    font-weight:400">({len(stacks)} total)</span></h2>
<div style="overflow-x:auto;margin-bottom:2rem">
  <table style="width:100%;border-collapse:collapse;font-size:.85rem;min-width:600px">
    <thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">
      <th style="text-align:left;padding:.35rem .5rem">Date</th>
      <th style="text-align:left;padding:.35rem .5rem">Engine</th>
      <th style="text-align:left;padding:.35rem .5rem">Params</th>
      <th style="text-align:right;padding:.35rem .5rem">Frames</th>
      <th style="text-align:right;padding:.35rem .5rem">Duration</th>
      <th style="text-align:right;padding:.35rem .5rem">FWHM</th>
      <th style="text-align:right;padding:.35rem .5rem" title="SNR efficiency vs ideal √N">Eff</th>
      <th style="text-align:right;padding:.35rem .5rem">Result</th>
    </tr></thead>
    <tbody>{runs_rows or '<tr><td colspan="8" style="padding:1rem;color:var(--text2)">No stacking runs yet.</td></tr>'}</tbody>
  </table>
</div>"""

    # --- Processed / raw-stack files grids ---
    def _pf_card(p: dict) -> str:
        fn = p.get("filename") or ""
        tool = p.get("tool") or ""
        step = p.get("step") or ""
        integ = p.get("total_integration") or 0
        hours_p = integ / 3600
        obs = (p.get("obs_date") or "")
        fn_enc = _uparse.quote(fn, safe="")
        is_fits = fn.lower().endswith((".fit", ".fits", ".xisf", ".tif", ".tiff"))
        link = f'/fits/{tgt_enc}/_processed/{fn_enc}' if is_fits else f'/image/{tgt_enc}/_processed/{fn_enc}'
        thumb_html = ""
        if fn.lower().endswith((".jpg", ".jpeg", ".png")):
            thumb_html = (f'<img src="/image/{tgt_enc}/_processed/{fn_enc}" loading="lazy"'
                          f' style="width:100%;height:100px;object-fit:cover;border-radius:4px 4px 0 0">')
        return (f'<a href="{link}" style="color:inherit;text-decoration:none">'
                f'<div style="background:var(--bg2);border:1px solid var(--border);'
                f'border-radius:6px;overflow:hidden;font-size:.78rem">'
                f'{thumb_html}'
                f'<div style="padding:.45rem .55rem">'
                f'<div style="font-family:monospace;font-size:.72rem;color:var(--text2);'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{fn}">{fn}</div>'
                f'<div style="color:var(--text2);margin-top:.2rem">{tool} {step}</div>'
                f'<div style="color:var(--text2)">{hours_p:.1f}h · {obs}</div>'
                f'</div></div></a>')

    raw_stacks_p = [p for p in proc_files
                    if is_raw_stack(p.get("filename", ""), p.get("step"))]
    processed_p = [p for p in proc_files
                   if not is_raw_stack(p.get("filename", ""), p.get("step"))]
    raw_cards = "".join(_pf_card(p) for p in raw_stacks_p[:12])
    pf_cards = "".join(_pf_card(p) for p in processed_p[:12])

    _grid_open = ('<div style="display:grid;grid-template-columns:'
                  'repeat(auto-fill,minmax(160px,1fr));gap:.75rem;margin-bottom:2rem">')
    proc_section = f"""
<h2 class="td-h2">Raw Stacks</h2>
{_grid_open}
  {raw_cards or '<p style="color:var(--text2)">No raw stacks yet.</p>'}
</div>
<h2 class="td-h2">Processed Files</h2>
{_grid_open}
  {pf_cards or '<p style="color:var(--text2)">No processed files yet.</p>'}
</div>"""

    # --- User comments section ---
    from nas_server.database import get_target_comments
    comments = get_target_comments(target, limit=20)

    def _comment_html(c: dict) -> str:
        cid = c["id"]
        ts = _fmt_mst(c.get("created_at") or "")
        run_tag = ""
        if c.get("run_id"):
            run_short = str(c["run_id"])[-8:]
            run_tag = f'<span style="font-size:.72rem;color:var(--text2);margin-left:.4rem">run …{run_short}</span>'
        txt = c.get("comment", "").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f'<div id="comment-{cid}" style="display:flex;align-items:flex-start;gap:.6rem;'
            f'padding:.55rem 0;border-bottom:1px solid var(--border)">'
            f'<div style="flex:1">'
            f'<span style="font-size:.78rem;color:var(--text2)">{ts}</span>{run_tag}'
            f'<div style="margin-top:.2rem;font-size:.88rem;line-height:1.5">{txt}</div>'
            f'</div>'
            f"<button onclick=\"deleteComment({cid},'{tgt_enc}')\" "
            f'style="background:none;border:none;cursor:pointer;color:var(--text2);'
            f'font-size:.78rem;padding:.2rem .35rem;border-radius:3px;flex-shrink:0" '
            f'title="Delete">✕</button>'
            f'</div>'
        )

    comments_html = "".join(_comment_html(c) for c in comments)

    comments_section = f"""
<h2 class="td-h2">Notes &amp; Feedback</h2>
<div id="comments-list" style="margin-bottom:1rem">
  {comments_html or '<p style="color:var(--text2);font-size:.85rem;margin:.5rem 0">No notes yet.</p>'}
</div>
<form id="comment-form" onsubmit="submitComment(event,'{tgt_enc}')"
      style="display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:2rem">
  <textarea id="comment-input" rows="2" placeholder="Add a note… e.g. &#39;sky looks a bit bright, try darker background&#39;"
            style="flex:1;min-width:220px;background:var(--bg2);border:1px solid var(--border);
                   color:var(--text);border-radius:5px;padding:.5rem .7rem;font-size:.85rem;
                   resize:vertical;font-family:inherit"></textarea>
  <button type="submit"
          style="background:var(--accent);color:#0d1117;border:none;border-radius:5px;
                 padding:.55rem 1rem;font-weight:600;font-size:.85rem;cursor:pointer;
                 white-space:nowrap">Save note</button>
</form>
<script>
async function submitComment(e, tgtEnc) {{
  e.preventDefault();
  const ta = document.getElementById('comment-input');
  const btn = e.target.querySelector('button[type=submit]');
  const txt = ta.value.trim();
  if (!txt) return;
  if (btn) {{ btn.textContent = 'Saving…'; btn.disabled = true; }}
  try {{
    const resp = await fetch('/targets/' + tgtEnc + '/comments', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{comment: txt}})
    }});
    if (resp.ok) {{
      ta.value = '';
      const data = await resp.json();
      const el = document.createElement('div');
      el.id = 'comment-' + data.id;
      el.innerHTML = `<div style="display:flex;align-items:flex-start;gap:.6rem;padding:.55rem 0;border-bottom:1px solid var(--border)">
        <div style="flex:1">
          <span style="font-size:.78rem;color:var(--text2)">${{new Date().toLocaleString()}}</span>
          <div style="margin-top:.2rem;font-size:.88rem;line-height:1.5">${{txt.replace(/</g,'&lt;')}}</div>
        </div>
        <button onclick="deleteComment(${{data.id}}, '${{tgtEnc}}')"
          style="background:none;border:none;cursor:pointer;color:var(--text2);font-size:.78rem;padding:.2rem .35rem;border-radius:3px;flex-shrink:0" title="Delete">✕</button>
      </div>`;
      const list = document.getElementById('comments-list');
      const empty = list.querySelector('p');
      if (empty) empty.remove();
      list.insertBefore(el, list.firstChild);
      if (btn) {{ btn.textContent = 'Saved ✓'; setTimeout(()=>{{ btn.textContent='Save note'; btn.disabled=false; }}, 1500); }}
    }} else {{
      const err = await resp.text();
      if (btn) {{ btn.textContent = 'Error ' + resp.status; btn.style.background='#f85149'; setTimeout(()=>{{ btn.textContent='Save note'; btn.style.background=''; btn.disabled=false; }}, 3000); }}
      console.error('Comment save failed:', resp.status, err);
    }}
  }} catch(ex) {{
    if (btn) {{ btn.textContent = 'Network error'; btn.style.background='#f85149'; setTimeout(()=>{{ btn.textContent='Save note'; btn.style.background=''; btn.disabled=false; }}, 3000); }}
    console.error('Comment save error:', ex);
  }}
}}
async function deleteComment(id, tgtEnc) {{
  if (!confirm('Delete this note?')) return;
  const resp = await fetch('/targets/' + tgtEnc + '/comments/' + id,
                           {{method: 'DELETE'}});
  if (resp.ok) {{
    const el = document.getElementById('comment-' + id);
    if (el) el.parentElement.removeChild(el);
  }}
}}
</script>"""

    # --- Claude assessment section ---
    asmt_html = ""
    for a in assessments:
        scores_raw = a.get("scores") or "{}"
        try:
            scores = _j.loads(scores_raw) if isinstance(scores_raw, str) else scores_raw
        except Exception:
            scores = {}
        rec_raw = a.get("recommendation") or ""
        try:
            rec = _j.loads(rec_raw) if isinstance(rec_raw, str) and rec_raw.startswith("{") else {}
        except Exception:
            rec = {}
        issues = scores.get("issues") or rec.get("issues") or []
        suggestions = scores.get("suggestions") or rec.get("suggestions") or []
        ts = _fmt_mst(a.get("created_at") or "")
        phase = a.get("phase") or ""
        overall_s = scores.get("overall")
        pill = _score_pill(overall_s) if overall_s else ""
        score_pills = " ".join(
            f'<span style="background:{"#3fb95022" if v >= 7 else "#e3b34122" if v >= 5 else "#f8514922"};'
            f'border:1px solid {"#3fb950" if v >= 7 else "#e3b341" if v >= 5 else "#f85149"};'
            f'border-radius:4px;padding:1px 6px;font-size:.75rem">{k.replace("_"," ")} {v}/10</span>'
            for k, v in scores.items() if isinstance(v, (int, float)) and k != "overall"
        )
        issues_html = "".join(
            f'<li style="margin-bottom:.25rem;color:var(--text2)">{i}</li>' for i in issues[:5]
        )
        suggestions_html = "".join(
            f'<li style="margin-bottom:.25rem;color:var(--text2)">{s}</li>' for s in suggestions[:5]
        )
        asmt_html += f"""
<details style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;
                margin-bottom:.75rem" {"open" if asmt_html == "" else ""}>
  <summary style="padding:.7rem 1rem;cursor:pointer;display:flex;
                  align-items:center;gap:.6rem;flex-wrap:wrap;list-style:none">
    <span style="font-size:.82rem;color:var(--text2)">{ts} · {phase}</span>
    {pill} {score_pills}
  </summary>
  <div style="padding:.5rem 1rem 1rem;font-size:.83rem">
    {f'<p style="font-weight:600;margin:.4rem 0 .3rem">Issues</p><ul style="padding-left:1.2rem">{issues_html}</ul>' if issues_html else ""}
    {f'<p style="font-weight:600;margin:.75rem 0 .3rem">Suggestions</p><ul style="padding-left:1.2rem">{suggestions_html}</ul>' if suggestions_html else ""}
    {f'<p style="color:var(--text2);margin-top:.5rem">{rec_raw[:600]}</p>' if not issues and rec_raw else ""}
  </div>
</details>"""

    asmt_section = f"""
<h2 class="td-h2">Claude Assessments
  <a href="/assess/{tgt_enc}" style="font-size:.78rem;font-weight:400;margin-left:.6rem">
    run new →</a></h2>
{asmt_html or '<p style="color:var(--text2);margin-bottom:2rem">No assessments yet.</p>'}
<div style="margin-bottom:2rem"></div>"""

    automation_section = f"""
<h2 class="td-h2">Automation</h2>
<div id="af-box-{tgt_enc}" style="display:flex;align-items:center;gap:1.5rem;
     flex-wrap:wrap;margin-bottom:2rem;padding:.75rem 1rem;
     background:var(--bg2);border:1px solid var(--border);border-radius:6px;
     font-size:.85rem">
  <label style="display:flex;align-items:center;gap:.45rem;cursor:pointer">
    <input type="checkbox" id="af-stack-{tgt_enc}" onchange="setAutoFlag('{tgt_enc}','auto_stack',this.checked)">
    <span>Auto-stack after transfer</span>
  </label>
  <label style="display:flex;align-items:center;gap:.45rem;cursor:pointer">
    <input type="checkbox" id="af-proc-{tgt_enc}" onchange="setAutoFlag('{tgt_enc}','auto_process',this.checked)">
    <span>Auto-process after stack</span>
  </label>
  <span id="af-status-{tgt_enc}" style="font-size:.78rem;color:var(--text2)"></span>
</div>
<script>
(function(){{
  const tenc = {_j.dumps(tgt_enc)};
  fetch('/planner/autoflags').then(r=>r.json()).then(flags=>{{
    const f = flags[decodeURIComponent(tenc)] || {{}};
    const st = document.getElementById('af-stack-'+tenc);
    const pr = document.getElementById('af-proc-'+tenc);
    if(st) st.checked = !!f.auto_stack;
    if(pr) pr.checked = !!f.auto_process;
  }});
}})();
async function setAutoFlag(tenc, key, val){{
  const status = document.getElementById('af-status-'+tenc);
  const resp = await fetch('/planner/autoflags', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{target: decodeURIComponent(tenc), [key]: val}})
  }});
  if(resp.ok && status) {{
    status.textContent = 'Saved';
    setTimeout(()=>{{ if(status) status.textContent=''; }}, 1500);
  }}
}}
</script>"""

    body = f"""
<div class="td-wrap">
  <div style="margin-bottom:1.2rem;font-size:.83rem;color:var(--text2)">
    <a href="/targets-view">Targets</a> › <span style="color:var(--text)">{target}</span>
  </div>
  {hero_body}
  {stacks_section}
  {proc_section}
  {automation_section}
  {asmt_section}
  {comments_section}
</div>"""

    css = """
  .td-wrap { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
  .td-h2 { font-size: 1.05rem; font-weight: 600; margin-bottom: .85rem;
            padding-bottom: .4rem; border-bottom: 1px solid var(--border); }
  .td-ts { color: var(--text2); font-size: .8rem; white-space: nowrap; }
  .td-num { text-align: right; color: var(--text2); padding: .35rem .5rem; }
  .td-btn { background: var(--bg2); border: 1px solid var(--border); color: var(--text);
            padding: .3rem .8rem; border-radius: 5px; font-size: .82rem; white-space: nowrap;
            text-decoration: none; display: inline-block; }
  .td-btn:hover { border-color: var(--accent); text-decoration: none; }
  .td-btn-primary { background: var(--accent); color: #0d1117; border-color: var(--accent);
                    font-weight: 600; }
  .td-btn-primary:hover { opacity: .9; }
  details summary::-webkit-details-marker { display: none; }
  @media (max-width: 600px) {
    .td-wrap { padding: 1rem .75rem 3rem; }
  }
"""
    return _shell(f"{target} — SeeStar", body, css)


# ---------------------------------------------------------------------------
# Association manager  (/associations)
# ---------------------------------------------------------------------------

def associations_page() -> str:
    from nas_server.database import get_all_targets_with_associations

    all_targets = get_all_targets_with_associations()

    # Group into linked sets and ungrouped
    groups: dict[str, list] = {}
    ungrouped: list = []
    for t in all_targets:
        assoc = (t.get("association") or "").strip()
        if assoc:
            key = tuple(sorted([t["target"], assoc]))
            groups.setdefault("|".join(key), []).append(t)
        else:
            ungrouped.append(t)

    # Mosaic panel grouping
    panel_groups: dict[str, list] = {}
    panel_target_names: set = set()
    for t in all_targets:
        ma = (t.get("mosaic_association") or "").strip()
        if ma:
            panel_groups.setdefault(ma, []).append(t)
            panel_target_names.add(t["target"])
    mosaic_primaries = sorted([t["target"] for t in all_targets if t.get("mosaic")])

    # Grouped section
    groups_html = ""
    if groups:
        groups_html = '<h2 class="am-h2">Linked Targets</h2>'
        for key, members in groups.items():
            names = " ↔ ".join(m["target"] for m in members)
            member_links = " · ".join(
                f'<a href="/target/{_uparse.quote(m["target"], safe="")}">{m["target"]}</a>'
                for m in members
            )
            groups_html += (f'<div style="background:var(--bg2);border:1px solid var(--border);'
                            f'border-radius:6px;padding:.55rem .9rem;margin-bottom:.5rem;'
                            f'font-size:.85rem">'
                            f'<span style="color:#3fb950;margin-right:.5rem">⟷</span>'
                            f'{member_links}</div>')

    # Targets table — primaries first with panels nested, then ungrouped
    def _ma_select(name, mosaic_assoc):
        opts = '<option value="">— not a panel —</option>'
        for p in mosaic_primaries:
            if p == name:
                continue
            sel = " selected" if mosaic_assoc == p else ""
            opts += f'<option value="{p}"{sel}>{p}</option>'
        if mosaic_assoc and mosaic_assoc not in mosaic_primaries and mosaic_assoc != name:
            opts += f'<option value="{mosaic_assoc}" selected>{mosaic_assoc} ⚠</option>'
        return (f'<select class="am-input ma-select" data-target="{name}" '
                f'onchange="saveMosaicAssoc(this)">{opts}</select>')

    def _build_row(t, primary_name=None):
        name = t["target"]
        assoc = t.get("association") or ""
        mosaic_assoc = t.get("mosaic_association") or ""
        mosaic_flag = bool(t.get("mosaic"))
        transient_flag = bool(t.get("transient"))
        frames = t.get("frame_count") or 0
        stage = t.get("stage") or ""
        name_enc = _uparse.quote(name, safe="")
        checked = "checked" if mosaic_flag else ""
        transient_checked = "checked" if transient_flag else ""
        is_panel = primary_name is not None

        if is_panel:
            label_cell = (f'<td class="am-name" style="padding-left:1.6rem">'
                          f'<span style="color:var(--border);margin-right:.3rem">└</span>'
                          f'<a href="/target/{name_enc}">{name}</a>'
                          + (f' <span style="font-size:.68rem;background:#7d4a0022;color:#e3a020;'
                             f'border:1px solid #7d4a0055;border-radius:3px;padding:1px 4px" '
                             f'title="Transient target">⚡</span>' if transient_flag else '')
                          + '</td>')
            row_attrs = f' class="am-row am-panel-row" data-group-member="{primary_name}" style="display:none"'
        else:
            panels = panel_groups.get(name, [])
            if panels:
                tgl = (f'<button onclick="toggleGroup(this)" data-target="{name}" id="tgl-{name_enc}" '
                       f'class="am-tgl" title="Show/hide {len(panels)} panels">'
                       f'▶ {len(panels)}</button> ')
            else:
                tgl = ''
            transient_badge = (f' <span style="font-size:.68rem;background:#7d4a0022;color:#e3a020;'
                               f'border:1px solid #7d4a0055;border-radius:3px;padding:1px 4px" '
                               f'title="Transient target">⚡</span>' if transient_flag else '')
            label_cell = (f'<td class="am-name">{tgl}'
                          f'<a href="/target/{name_enc}">{name}</a>{transient_badge}</td>')
            row_attrs = ' class="am-row"'

        return f"""
<tr{row_attrs} id="am-{name_enc}">
  {label_cell}
  <td class="am-num" style="color:var(--text2)">{frames:,}</td>
  <td>{_stage_badge(stage) if stage else ""}</td>
  <td class="am-edit">
    <input type="text" class="am-input" value="{assoc}"
           placeholder="e.g. NGC 2359"
           data-target="{name}" data-field="association"
           onchange="saveAssoc(this)">
  </td>
  <td class="am-edit">{_ma_select(name, mosaic_assoc)}</td>
  <td style="text-align:center">
    <input type="checkbox" {checked} title="Mark as mosaic capture"
           data-target="{name}" onchange="saveMosaicFlag(this)">
  </td>
  <td style="text-align:center">
    <input type="checkbox" {transient_checked} title="Transient target — gets 1.5× planner boost"
           class="am-transient" data-target="{name}" onchange="saveTransientFlag(this)">
  </td>
  <td class="am-status" id="am-st-{name_enc}"></td>
</tr>"""

    rows_html = ""
    for t in all_targets:
        name = t["target"]
        if name in panel_target_names:
            continue  # will be rendered under its primary
        rows_html += _build_row(t)
        for p in sorted(panel_groups.get(name, []), key=lambda x: x["target"]):
            rows_html += _build_row(p, primary_name=name)

    body = f"""
<div class="am-wrap">
  <div style="display:flex;justify-content:space-between;align-items:baseline;
              flex-wrap:wrap;gap:.5rem;margin-bottom:1.2rem">
    <h1 style="font-size:1.4rem">Associations</h1>
    <span style="color:var(--text2);font-size:.82rem">
      {len(all_targets)} targets · {len(groups)} linked pairs
    </span>
  </div>
  <p style="color:var(--text2);font-size:.83rem;margin-bottom:1.5rem;max-width:660px">
    Associations link targets that are different names for the same object (e.g.
    <em>SH 2-298 ↔ NGC 2359</em>). Linked targets share frames in stack queries.
    Edit a cell and press Enter or Tab to save.
  </p>

  <!-- Scan section -->
  <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
              padding:1rem 1.1rem;margin-bottom:1.5rem">
    <div style="display:flex;align-items:center;gap:.8rem;flex-wrap:wrap">
      <span style="font-weight:600;font-size:.95rem">🔍 Association Scanner</span>
      <button id="scan-btn" onclick="runScan()"
              style="background:var(--accent);color:#0d1117;border:none;border-radius:6px;
                     padding:.35rem .9rem;font-size:.85rem;font-weight:600;cursor:pointer">
        Scan for Suggestions
      </button>
      <span id="scan-status" style="font-size:.82rem;color:var(--text2)"></span>
    </div>
    <p style="color:var(--text2);font-size:.78rem;margin-top:.5rem;margin-bottom:0">
      Checks all targets against Messier, Caldwell, and name-variant catalogs.
      Already-linked pairs are excluded. Click Link to save bidirectionally.
    </p>
    <div id="scan-results" style="margin-top:.8rem"></div>
  </div>

  <h2 class="am-h2" style="margin-top:1.5rem">All Targets</h2>
  <div style="margin-bottom:.6rem;font-size:.82rem;color:var(--text2)">
    <input type="search" id="am-search" placeholder="Filter targets…"
           oninput="filterTable(this.value)"
           style="background:var(--bg2);border:1px solid var(--border);color:var(--text);
                  border-radius:4px;padding:.3rem .6rem;font-size:.82rem;width:220px">
  </div>
  <div style="overflow-x:auto">
    <table class="am-table" id="am-table">
      <thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">
        <th style="text-align:left;padding:.35rem .5rem">Target</th>
        <th style="text-align:right;padding:.35rem .5rem">Frames</th>
        <th style="padding:.35rem .5rem">Stage</th>
        <th style="text-align:left;padding:.35rem .5rem">Association</th>
        <th style="text-align:left;padding:.35rem .5rem" title="If this is a secondary panel, enter the primary target name here">Secondary panel of</th>
        <th style="text-align:center;padding:.35rem .5rem" title="Check if this target was captured in mosaic mode (primary or single-name mosaic)">Mosaic</th>
        <th style="text-align:center;padding:.35rem .5rem" title="Transient targets (comets, planets, events) receive a 1.5× planner score boost">⚡ Transient</th>
        <th style="padding:.35rem .5rem"></th>
      </tr></thead>
      <tbody id="am-tbody">{rows_html}</tbody>
    </table>
  </div>

  {groups_html}
</div>

<script>
var _groupCollapsed = {{}};
(function() {{
  document.querySelectorAll('[data-group-member]').forEach(function(row) {{
    var pm = row.getAttribute('data-group-member');
    if (pm && !(pm in _groupCollapsed)) _groupCollapsed[pm] = true;
  }});
}})();

function toggleGroup(btn) {{
  var primary = btn.dataset.target;
  var nowCollapsed = !_groupCollapsed[primary];
  _groupCollapsed[primary] = nowCollapsed;
  document.querySelectorAll('[data-group-member="' + primary + '"]').forEach(function(row) {{
    row.style.display = nowCollapsed ? 'none' : '';
  }});
  var count = document.querySelectorAll('[data-group-member="' + primary + '"]').length;
  btn.textContent = (nowCollapsed ? '▶ ' : '▼ ') + count;
}}

function saveAssoc(inputEl) {{
  var target = inputEl.dataset.target;
  var field = inputEl.dataset.field;
  var val = inputEl.value.trim() || null;
  var stEl = document.getElementById('am-st-' + encodeURIComponent(target));
  if (stEl) stEl.textContent = '…';
  var body = {{}};
  body[field] = val;
  fetch('/targets/' + encodeURIComponent(target) + '/association', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(body)
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (stEl) {{
      stEl.textContent = '✓';
      stEl.style.color = '#3fb950';
      setTimeout(function() {{ stEl.textContent = ''; }}, 2000);
    }}
    inputEl.style.borderColor = '#3fb950';
    setTimeout(function() {{ inputEl.style.borderColor = ''; }}, 2000);
  }}).catch(function() {{
    if (stEl) {{ stEl.textContent = '✗'; stEl.style.color = '#f85149'; }}
  }});
}}

function saveMosaicAssoc(selectEl) {{
  var target = selectEl.dataset.target;
  var val = selectEl.value || null;
  var stEl = document.getElementById('am-st-' + encodeURIComponent(target));
  if (stEl) stEl.textContent = '…';
  fetch('/targets/' + encodeURIComponent(target) + '/association', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{mosaic_association: val}})
  }}).then(function(r) {{ return r.json(); }}).then(function() {{
    if (stEl) {{
      stEl.textContent = '✓';
      stEl.style.color = '#3fb950';
      setTimeout(function() {{ stEl.textContent = ''; }}, 2000);
    }}
    selectEl.style.outline = '2px solid #3fb950';
    setTimeout(function() {{ selectEl.style.outline = ''; }}, 1500);
  }}).catch(function() {{
    if (stEl) {{ stEl.textContent = '✗'; stEl.style.color = '#f85149'; }}
  }});
}}

function saveMosaicFlag(cbEl) {{
  var target = cbEl.dataset.target;
  var nameEnc = encodeURIComponent(target);
  var stEl = document.getElementById('am-st-' + nameEnc);
  if (stEl) stEl.textContent = '…';
  fetch('/targets/' + nameEnc + '/association', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{mosaic: cbEl.checked ? 1 : 0}})
  }}).then(function(r) {{ return r.json(); }}).then(function() {{
    if (stEl) {{
      stEl.textContent = '✓';
      stEl.style.color = '#3fb950';
      setTimeout(function() {{ stEl.textContent = ''; }}, 2000);
    }}
    document.querySelectorAll('.ma-select').forEach(function(sel) {{
      if (sel.dataset.target === target) return;
      if (cbEl.checked) {{
        if (!sel.querySelector('option[value="' + target + '"]')) {{
          var opt = document.createElement('option');
          opt.value = target;
          opt.textContent = target;
          sel.appendChild(opt);
        }}
      }} else {{
        var ex = sel.querySelector('option[value="' + target + '"]');
        if (ex && !ex.selected) {{ ex.remove(); }}
        else if (ex) {{ ex.textContent = target + ' ⚠'; }}
      }}
    }});
  }}).catch(function() {{
    cbEl.checked = !cbEl.checked;
    if (stEl) {{ stEl.textContent = '✗'; stEl.style.color = '#f85149'; }}
  }});
}}

function saveTransientFlag(cbEl) {{
  var target = cbEl.dataset.target;
  var nameEnc = encodeURIComponent(target);
  var stEl = document.getElementById('am-st-' + nameEnc);
  if (stEl) stEl.textContent = '…';
  fetch('/targets/transient', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{target: target, transient: cbEl.checked}})
  }}).then(function(r) {{ return r.json(); }}).then(function() {{
    if (stEl) {{
      stEl.textContent = '✓';
      stEl.style.color = '#3fb950';
      setTimeout(function() {{ stEl.textContent = ''; }}, 2000);
    }}
  }}).catch(function() {{
    cbEl.checked = !cbEl.checked;
    if (stEl) {{ stEl.textContent = '✗'; stEl.style.color = '#f85149'; }}
  }});
}}

function filterTable(q) {{
  q = q.toLowerCase();
  if (!q) {{
    document.querySelectorAll('#am-tbody tr').forEach(function(row) {{
      var pm = row.getAttribute('data-group-member');
      row.style.display = (pm && _groupCollapsed[pm]) ? 'none' : '';
    }});
    return;
  }}
  document.querySelectorAll('#am-tbody tr').forEach(function(row) {{
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
function runScan() {{
  var btn = document.getElementById('scan-btn');
  var status = document.getElementById('scan-status');
  var results = document.getElementById('scan-results');
  btn.disabled = true;
  btn.textContent = 'Scanning…';
  status.textContent = '';
  results.innerHTML = '';
  fetch('/associations/suggest')
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      btn.disabled = false;
      btn.textContent = 'Scan for Suggestions';
      var suggestions = data.suggestions || [];
      if (suggestions.length === 0) {{
        status.textContent = 'No new suggestions found.';
        return;
      }}
      status.textContent = suggestions.length + ' suggestion' + (suggestions.length !== 1 ? 's' : '') + ' found';
      var html = '<table style="width:100%;border-collapse:collapse;font-size:.84rem;margin-top:.4rem">'
               + '<thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">'
               + '<th style="text-align:left;padding:.3rem .5rem">Target A</th>'
               + '<th style="text-align:center;padding:.3rem .2rem">⟷</th>'
               + '<th style="text-align:left;padding:.3rem .5rem">Target B</th>'
               + '<th style="text-align:left;padding:.3rem .5rem;color:var(--text2)">Source</th>'
               + '<th style="padding:.3rem .5rem"></th>'
               + '</tr></thead><tbody>';
      suggestions.forEach(function(s, i) {{
        html += '<tr id="sr-' + i + '" style="border-bottom:1px solid var(--bg3)">'
              + '<td style="padding:.35rem .5rem;font-weight:600">' + s.target_a + '</td>'
              + '<td style="text-align:center;color:#3fb950">⟷</td>'
              + '<td style="padding:.35rem .5rem;font-weight:600">' + s.target_b + '</td>'
              + '<td style="padding:.35rem .5rem;color:var(--text2);font-size:.78rem">'
              + s.method + ' <span style="opacity:.6">(' + s.via + ')</span></td>'
              + '<td style="padding:.35rem .5rem;white-space:nowrap">'
              + '<button class="sr-link-btn" data-a="' + s.target_a + '" data-b="' + s.target_b + '" data-i="' + i + '" '
              + 'style="background:#1a3a25;color:#3fb950;border:1px solid #3fb950;border-radius:4px;'
              + 'padding:.2rem .6rem;font-size:.78rem;cursor:pointer;margin-right:.3rem">Link</button>'
              + '<button class="sr-skip-btn" data-i="' + i + '" '
              + 'style="background:none;border:1px solid var(--border);border-radius:4px;'
              + 'padding:.2rem .6rem;font-size:.78rem;cursor:pointer;color:var(--text2)">Skip</button>'
              + '</td></tr>';
      }});
      html += '</tbody></table>';
      results.innerHTML = html;
    }})
    .catch(function(e) {{
      btn.disabled = false;
      btn.textContent = 'Scan for Suggestions';
      status.textContent = 'Error — see console';
      status.style.color = '#f85149';
    }});
}}
document.getElementById('scan-results').addEventListener('click', function(e) {{
  var lb = e.target.closest('.sr-link-btn');
  var sb = e.target.closest('.sr-skip-btn');
  if (lb) {{
    var a = lb.dataset.a, b = lb.dataset.b, idx = lb.dataset.i;
    lb.disabled = true;
    lb.textContent = '…';
    fetch('/associations/link', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{target_a: a, target_b: b}})
    }}).then(function(r) {{ return r.json(); }}).then(function() {{
      var row = document.getElementById('sr-' + idx);
      if (row) {{ row.style.opacity = '.4'; lb.textContent = '✓ Linked'; }}
      var el = document.getElementById('am-' + encodeURIComponent(a));
      if (el) {{ var inp = el.querySelector('input[type=text]'); if (inp) inp.value = b; }}
    }}).catch(function() {{ lb.disabled = false; lb.textContent = 'Link'; }});
  }}
  if (sb) {{
    var row = document.getElementById('sr-' + sb.dataset.i);
    if (row) row.style.display = 'none';
  }}
}});
</script>"""

    css = """
  .am-wrap { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
  .am-h2 { font-size: 1rem; font-weight: 600; margin-bottom: .75rem;
            padding-bottom: .35rem; border-bottom: 1px solid var(--border); }
  .am-table { width: 100%; border-collapse: collapse; font-size: .85rem; min-width: 580px; }
  .am-table thead tr { color: var(--text2); }
  .am-row { border-bottom: 1px solid var(--bg3); }
  .am-row:hover { background: var(--bg2); }
  .am-name a { font-weight: 600; color: var(--text); }
  .am-name a:hover { color: var(--accent); }
  .am-name { padding: .4rem .5rem; }
  .am-num { text-align: right; padding: .4rem .5rem; }
  .am-edit { padding: .3rem .4rem; }
  .am-input { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
              border-radius: 4px; padding: .2rem .5rem; font-size: .82rem;
              width: 100%; min-width: 130px; }
  .am-input:focus { outline: none; border-color: var(--accent); }
  .am-status { font-size: .8rem; padding: .3rem .4rem; white-space: nowrap; }
  .am-tgl { background: none; border: 1px solid var(--border); color: var(--text2);
            border-radius: 3px; padding: .1rem .3rem; font-size: .7rem; cursor: pointer;
            white-space: nowrap; vertical-align: middle; margin-right: .2rem; }
  .am-tgl:hover { background: var(--bg3); color: var(--text); }
  .am-panel-row { background: color-mix(in srgb, var(--bg2) 40%, transparent); }
  .am-panel-row:hover { background: var(--bg2); }
  @media (max-width: 640px) {
    .am-table { font-size: .78rem; }
    .am-input { min-width: 90px; }
  }
"""
    return _shell("Associations — SeeStar", body, css)


# ---------------------------------------------------------------------------
# Target folio  (/folio/{target})
# ---------------------------------------------------------------------------

def folio_page(target: str) -> str:
    from nas_server.folio_generator import load_folio
    from nas_server.database import get_stacking_runs, get_claude_history

    folio = load_folio(target)
    tgt_enc = _uparse.quote(target, safe="")

    no_folio_msg = (
        f'<div style="max-width:900px;margin:3rem auto;padding:2rem;color:var(--text2)">'
        f'No folio exists for <b>{target}</b> yet.<br><br>'
        f'Ask Claude Code to generate one: <code>Generate a folio for {target}</code><br><br>'
        f'<a href="/target/{tgt_enc}">← {target} detail</a></div>'
    )

    if not folio:
        return _shell(f"{target} Folio — SeeStar", no_folio_msg)

    common = folio.get("common_name", target)
    obj_type = folio.get("type", "")
    gen_date = folio.get("generated_at", "")

    # --- catalog strip ---
    cat = folio.get("catalog", {})
    catalog_items = []
    if cat.get("constellation"):
        catalog_items.append(("Constellation", cat["constellation"]))
    if cat.get("angular_size_arcmin"):
        catalog_items.append(("Size", f'{cat["angular_size_arcmin"]}′'))
    if cat.get("distance_mly"):
        catalog_items.append(("Distance", f'{cat["distance_mly"]} Mly'))
    elif cat.get("distance_ly"):
        catalog_items.append(("Distance", f'{cat["distance_ly"]:,} ly'))
    if cat.get("visual_magnitude") is not None:
        catalog_items.append(("Magnitude", f'V = {cat["visual_magnitude"]}'))
    if cat.get("central_star"):
        catalog_items.append(("Central star", cat["central_star"][:60]))
    if cat.get("companion_object"):
        catalog_items.append(("Companion", cat["companion_object"][:60]))
    if cat.get("best_season"):
        catalog_items.append(("Best season", cat["best_season"]))

    cat_cells = "".join(
        f'<div class="fl-card"><div class="fl-label">{k}</div>'
        f'<div class="fl-val">{v}</div></div>'
        for k, v in catalog_items
    )
    cat_section = f'<div class="fl-strip">{cat_cells}</div>' if cat_cells else ""

    # --- achievability card ---
    s50 = folio.get("s50_achievability", {})
    ach_rows = []
    if s50.get("min_integration_hours"):
        ach_rows.append(("Min hours", f'{s50["min_integration_hours"]}h'))
    if s50.get("recommended_integration_hours"):
        ach_rows.append(("Recommended", f'{s50["recommended_integration_hours"]}h'))
    if s50.get("drizzle_benefit"):
        ach_rows.append(("Drizzle 2×", s50["drizzle_benefit"][:80]))
    if s50.get("detail_ceiling"):
        ach_rows.append(("Ceiling", s50["detail_ceiling"][:120]))
    if s50.get("filter_mode_critical"):
        ach_rows.append(("Filter note", s50["filter_mode_critical"][:100]))
    if s50.get("framing_note"):
        ach_rows.append(("Framing", s50["framing_note"][:100]))

    ach_html = "".join(
        f'<tr><td class="fl-key">{k}</td><td>{v}</td></tr>' for k, v in ach_rows
    )
    ach_section = f"""
<h2 class="fl-h2">SeeStar S50 Achievability</h2>
<table class="fl-table">{ach_html}</table>""" if ach_rows else ""

    # --- visual character ---
    vc = folio.get("visual_character", {})
    colors = vc.get("dominant_colors", [])
    structures = vc.get("key_structures", [])
    challenges = vc.get("challenge_features", [])
    great = vc.get("what_separates_good_from_great", "")

    color_chips = "".join(
        f'<span class="fl-chip">{c}</span>' for c in colors
    )
    struct_items = "".join(f'<li>{s}</li>' for s in structures)
    challenge_items = "".join(f'<li>{c}</li>' for c in challenges)

    vc_section = ""
    if colors or structures or challenges:
        vc_section = f"""
<h2 class="fl-h2">Visual Character</h2>
<div class="fl-2col">"""
        if colors:
            vc_section += f'<div><div class="fl-sublabel">Colors</div><div class="fl-chips">{color_chips}</div></div>'
        if great:
            vc_section += f'<div><div class="fl-sublabel">Good → Great</div><p class="fl-p">{great}</p></div>'
        vc_section += "</div>"
        if structures:
            vc_section += f'<div class="fl-sublabel" style="margin-top:.8rem">Key Structures</div><ul class="fl-ul">{struct_items}</ul>'
        if challenges:
            vc_section += f'<div class="fl-sublabel">Challenges</div><ul class="fl-ul fl-warn">{challenge_items}</ul>'

    # --- processing guidance ---
    proc = folio.get("processing_notes", {})
    masking = proc.get("masking", {})
    color_cfg = proc.get("color", {})
    known_ch = proc.get("known_challenges", [])

    mask_rows = ""
    for k, v in masking.items():
        if isinstance(v, str) and k not in ("emission_nebula_note",):
            label = k.replace("_", " ").title()
            mask_rows += f'<tr><td class="fl-key">{label}</td><td>{v}</td></tr>'

    color_rows = ""
    if color_cfg.get("spcc_recommended") is not None:
        color_rows += f'<tr><td class="fl-key">SPCC</td><td>{"✓ recommended" if color_cfg["spcc_recommended"] else "not applicable"}</td></tr>'
    if color_cfg.get("saturation_profile"):
        color_rows += f'<tr><td class="fl-key">Saturation</td><td>{color_cfg["saturation_profile"]}</td></tr>'
    if color_cfg.get("scnr_amount") is not None:
        color_rows += f'<tr><td class="fl-key">SCNR amount</td><td>{color_cfg["scnr_amount"]}</td></tr>'
    if color_cfg.get("hoo_pixelmath"):
        color_rows += f'<tr><td class="fl-key">HOO formula</td><td><code>{color_cfg["hoo_pixelmath"]}</code></td></tr>'

    stretch_note = proc.get("stretch_approach", "")[:200]
    scnr_note = proc.get("scnr", "")[:150]
    ch_items = "".join(f'<li>{c}</li>' for c in known_ch)

    proc_section = f"""
<h2 class="fl-h2">Processing Guidance</h2>
<div class="fl-2col">"""
    if stretch_note:
        proc_section += f'<div><div class="fl-sublabel">Stretch Approach</div><p class="fl-p">{stretch_note}</p></div>'
    if scnr_note:
        proc_section += f'<div><div class="fl-sublabel">SCNR</div><p class="fl-p">{scnr_note}</p></div>'
    proc_section += "</div>"
    if mask_rows:
        proc_section += f'<div class="fl-sublabel" style="margin-top:.8rem">Masking</div><table class="fl-table">{mask_rows}</table>'
    if color_rows:
        proc_section += f'<div class="fl-sublabel" style="margin-top:.8rem">Color</div><table class="fl-table">{color_rows}</table>'
    if ch_items:
        proc_section += f'<div class="fl-sublabel" style="margin-top:.8rem">Known Pitfalls</div><ul class="fl-ul fl-warn">{ch_items}</ul>'

    # --- assessment anchors ---
    anchors = folio.get("assessment_anchors", [])
    anchor_items = "".join(f'<li>{a}</li>' for a in anchors)
    anchor_section = f"""
<h2 class="fl-h2">Assessment Checklist</h2>
<ul class="fl-ul fl-checks">{anchor_items}</ul>""" if anchors else ""

    # --- reference examples ---
    refs = folio.get("reference_examples", [])
    ref_cards = ""
    for r in refs:
        label = r.get("label", r.get("equipment_class", ""))
        url = r.get("astrobin_url") or r.get("url") or r.get("search_url", "")
        eq = r.get("equipment_class", "")
        hours = r.get("integration_hours")
        note = r.get("note", r.get("notes", ""))
        eq_color = "#58a6ff" if "SeeStar" in eq else ("#3fb950" if "advanced" in eq else "#8b949e")
        hours_str = f' · {hours}h' if hours else ""
        url_html = (f'<a href="{url}" target="_blank" rel="noopener" '
                    f'style="color:var(--accent);font-size:.8rem">Open →</a>' if url else "")
        ref_cards += (
            f'<div class="fl-ref-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3rem">'
            f'<span style="border:1px solid {eq_color};color:{eq_color};border-radius:3px;'
            f'padding:0 5px;font-size:.72rem">{eq}{hours_str}</span>{url_html}</div>'
            f'<div style="font-size:.82rem;font-weight:500">{label}</div>'
            + (f'<div style="font-size:.78rem;color:var(--text2);margin-top:.2rem">{note}</div>' if note else "")
            + f'</div>'
        )
    ref_section = f"""
<h2 class="fl-h2">Reference Examples</h2>
<div class="fl-ref-grid">{ref_cards}</div>""" if ref_cards else ""

    # --- our history from DB ---
    stacks = get_stacking_runs(target=target, limit=20, dedupe_outputs=True)
    snr_vals = [r["snr_stack"] for r in stacks if r.get("success") and r.get("snr_stack")]
    fwhm_vals = [r["fwhm_stack"] for r in stacks if r.get("success") and r.get("fwhm_stack")]
    assessments = get_claude_history(target=target, limit=5)
    all_issues: list[str] = []
    for a in assessments:
        sc = a.get("scores") or {}
        if isinstance(sc, str):
            import json as _j
            try:
                sc = _j.loads(sc)
            except Exception:
                sc = {}
        all_issues.extend(sc.get("issues", []))
    top_issues = list(dict.fromkeys(all_issues))[:6]
    issue_items = "".join(f'<li>{i}</li>' for i in top_issues)

    history_rows = ""
    if snr_vals:
        history_rows += f'<tr><td class="fl-key">SNR range</td><td>{min(snr_vals):.0f} – {max(snr_vals):.0f}</td></tr>'
    if fwhm_vals:
        history_rows += f'<tr><td class="fl-key">FWHM range</td><td>{min(fwhm_vals):.2f} – {max(fwhm_vals):.2f} px</td></tr>'
    if stacks:
        history_rows += f'<tr><td class="fl-key">Stack runs</td><td>{len(stacks)}</td></tr>'

    history_section = ""
    if history_rows or issue_items:
        history_section = f'<h2 class="fl-h2">Our History</h2>'
        if history_rows:
            history_section += f'<table class="fl-table" style="margin-bottom:.8rem">{history_rows}</table>'
        if issue_items:
            history_section += f'<div class="fl-sublabel">Recurring issues from assessments</div><ul class="fl-ul fl-warn">{issue_items}</ul>'

    # --- page body ---
    type_badge = _stage_badge(obj_type) if obj_type else ""
    gen_note = f'<span style="color:var(--text2);font-size:.75rem">Generated {gen_date}</span>' if gen_date else ""

    body = f"""
<div class="fl-wrap">
  <div style="margin-bottom:1rem;font-size:.83rem;color:var(--text2)">
    <a href="/targets-view">Targets</a> ›
    <a href="/target/{tgt_enc}">{target}</a> ›
    <span style="color:var(--text)">Folio</span>
  </div>
  <div style="display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin-bottom:.5rem">
    <h1 style="font-size:1.5rem;font-weight:700;margin:0">{target}</h1>
    {type_badge}
    <span style="color:var(--text2)">{common}</span>
  </div>
  {gen_note}
  {cat_section}
  {ach_section}
  {vc_section}
  {proc_section}
  {anchor_section}
  {ref_section}
  {history_section}
</div>"""

    css = """
  .fl-wrap { max-width: 1000px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
  .fl-h2 { font-size: 1.0rem; font-weight: 600; margin: 1.6rem 0 .7rem;
            padding-bottom: .35rem; border-bottom: 1px solid var(--border); }
  .fl-strip { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0; }
  .fl-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
             padding: .45rem .75rem; min-width: 110px; }
  .fl-label { font-size: .7rem; color: var(--text2); text-transform: uppercase;
              letter-spacing: .04em; margin-bottom: .15rem; }
  .fl-val { font-size: .88rem; font-weight: 500; }
  .fl-table { width: 100%; border-collapse: collapse; font-size: .83rem; margin-bottom: .5rem; }
  .fl-table td { padding: .3rem .4rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  .fl-key { color: var(--text2); white-space: nowrap; width: 140px; padding-right: .8rem; }
  .fl-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: .5rem; }
  .fl-sublabel { font-size: .75rem; color: var(--text2); text-transform: uppercase;
                 letter-spacing: .04em; margin-bottom: .4rem; }
  .fl-ul { padding-left: 1.2rem; margin: 0 0 .8rem; font-size: .84rem; }
  .fl-warn li { color: var(--text); }
  .fl-warn li::marker { color: #e3b341; content: "⚠ "; }
  .fl-checks li::marker { color: #3fb950; content: "✓ "; }
  .fl-chips { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .5rem; }
  .fl-chip { background: var(--bg3); border: 1px solid var(--border); border-radius: 4px;
             padding: 2px 7px; font-size: .78rem; }
  .fl-p { font-size: .84rem; color: var(--text); margin: 0 0 .5rem; line-height: 1.5; }
  .fl-ref-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
                 gap: .7rem; margin-bottom: 1rem; }
  .fl-ref-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
                 padding: .65rem .8rem; }
  @media (max-width: 620px) {
    .fl-2col { grid-template-columns: 1fr; }
    .fl-wrap { padding: 1rem .75rem 3rem; }
    .fl-ref-grid { grid-template-columns: 1fr; }
  }
"""
    return _shell(f"{target} Folio — SeeStar", body, css)


# ── Chat page ────────────────────────────────────────────────────────────────

def chat_page() -> str:
    from nas_server.story import _page_shell
    body = """
<div id="chat-wrap">
  <div id="sidebar-backdrop"></div>
  <div id="chat-sidebar">
    <button id="new-chat-btn">+ New chat</button>
    <div id="session-list"></div>
  </div>
  <div id="chat-main">
    <div id="chat-thread" aria-live="polite"></div>
    <div id="chat-input-bar">
      <button id="sidebar-toggle" title="Chat history">&#9776;</button>
      <label id="img-label" title="Attach image">
        &#x1F4CE;
        <input type="file" id="img-pick" accept="image/*" style="display:none">
      </label>
      <div id="img-preview-wrap" style="display:none">
        <img id="img-preview" alt="preview">
        <button id="img-clear" type="button">&#x2715;</button>
      </div>
      <textarea id="chat-msg" placeholder="Ask anything about your astrophotography data…"
                rows="2" autofocus></textarea>
      <button id="chat-send">Send</button>
    </div>
  </div>
</div>
<script>
(function() {
  var thread   = document.getElementById('chat-thread');
  var input    = document.getElementById('chat-msg');
  var imgPick  = document.getElementById('img-pick');
  var imgPreview = document.getElementById('img-preview');
  var imgWrap  = document.getElementById('img-preview-wrap');
  var imgClear = document.getElementById('img-clear');
  var sessList = document.getElementById('session-list');
  var imgB64   = null;
  var sessionId = null;

  // ── helpers ────────────────────────────────────────────────────────────────

  function renderMsg(role, text) {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-' + role;
    div.textContent = text;
    thread.appendChild(div);
    thread.scrollTop = thread.scrollHeight;
    return div;
  }

  function clearThread() {
    thread.innerHTML = '';
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso.replace(' ', 'T') + 'Z');
    return d.toLocaleDateString(undefined, {month:'short', day:'numeric'});
  }

  // ── session list ───────────────────────────────────────────────────────────

  function loadSessions() {
    fetch('/chat/sessions')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        sessList.innerHTML = '';
        (d.sessions || []).forEach(function(s) {
          var btn = document.createElement('button');
          btn.className = 'sess-btn' + (s.id === sessionId ? ' active' : '');
          btn.dataset.sid = s.id;
          var label = s.title || 'Chat ' + s.id;
          btn.title = label;
          btn.innerHTML = '<span class="sess-title">' + escHtml(label.slice(0, 28)) + '</span>'
            + '<span class="sess-date">' + fmtDate(s.last_active) + '</span>';
          btn.addEventListener('click', function() { switchSession(s.id); closeSidebar(); });
          sessList.appendChild(btn);
        });
      });
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function switchSession(sid) {
    sessionId = sid;
    localStorage.setItem('chatSessionId', sid);
    clearThread();
    loadSessions();
    fetch('/chat/history?session_id=' + sid)
      .then(function(r) { return r.json(); })
      .then(function(d) {
        (d.messages || []).forEach(function(m) { renderMsg(m.role, m.content); });
      });
  }

  function startNewSession() {
    fetch('/chat/session/new', {method:'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        sessionId = d.session_id;
        localStorage.setItem('chatSessionId', sessionId);
        clearThread();
        loadSessions();
      });
  }

  // ── image handling ─────────────────────────────────────────────────────────

  imgPick.addEventListener('change', function() {
    var file = this.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(e) {
      imgB64 = e.target.result.split(',')[1];
      imgPreview.src = e.target.result;
      imgWrap.style.display = 'flex';
    };
    reader.readAsDataURL(file);
  });

  imgClear.addEventListener('click', function() {
    imgB64 = null;
    imgWrap.style.display = 'none';
    imgPick.value = '';
  });

  // ── send ───────────────────────────────────────────────────────────────────

  function send() {
    var text = input.value.trim();
    if (!text && !imgB64) return;
    renderMsg('user', text || '[image]');
    input.value = '';
    var thinking = renderMsg('assistant', '…');
    var payload = {message: text, session_id: sessionId};
    if (imgB64) { payload.image_b64 = imgB64; imgClear.click(); }
    fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.session_id && !sessionId) {
        sessionId = d.session_id;
        localStorage.setItem('chatSessionId', sessionId);
      }
      thinking.textContent = d.response || '(no response)';
      loadSessions();
    })
    .catch(function(e) { thinking.textContent = '[error: ' + e + ']'; });
  }

  document.getElementById('chat-send').addEventListener('click', send);
  document.getElementById('new-chat-btn').addEventListener('click', function() {
    startNewSession();
    closeSidebar();
  });
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  // ── Mobile sidebar toggle ──────────────────────────────────────────────────
  var sidebar   = document.getElementById('chat-sidebar');
  var backdrop  = document.getElementById('sidebar-backdrop');
  var toggleBtn = document.getElementById('sidebar-toggle');

  function openSidebar()  { sidebar.classList.add('open'); backdrop.classList.add('open'); }
  function closeSidebar() { sidebar.classList.remove('open'); backdrop.classList.remove('open'); }

  toggleBtn.addEventListener('click', function() {
    sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
  });
  backdrop.addEventListener('click', closeSidebar);

  // ── Fit chat to remaining viewport below nav ───────────────────────────────
  function fitChatHeight() {
    var nav = document.querySelector('nav');
    var wrap = document.getElementById('chat-wrap');
    if (!nav || !wrap) return;
    var navH = nav.getBoundingClientRect().height;
    // Use dvh if supported, else fall back to window.innerHeight
    var vh = (typeof CSS !== 'undefined' && CSS.supports('height', '1dvh'))
      ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dvh') || '0') || window.innerHeight
      : window.innerHeight;
    wrap.style.height = (window.innerHeight - navH) + 'px';
  }
  fitChatHeight();
  window.addEventListener('resize', fitChatHeight);

  // ── init ───────────────────────────────────────────────────────────────────

  var stored = localStorage.getItem('chatSessionId');
  if (stored) {
    sessionId = parseInt(stored, 10);
    switchSession(sessionId);
  } else {
    startNewSession();
  }
})();
</script>"""
    css = """
  #chat-wrap { display: flex; height: calc(100vh - 41px); overflow: hidden; position: relative; /* JS overrides height via fitChatHeight() */ }
  #chat-sidebar { width: 210px; min-width: 210px; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: var(--bg2); overflow: hidden; }
  #new-chat-btn { margin: .75rem; padding: .5rem .75rem; background: var(--accent); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: .85rem; font-weight: 600; text-align: left; }
  #new-chat-btn:hover { opacity: .85; }
  #session-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; padding: 0 .5rem .75rem; }
  .sess-btn { background: none; border: none; color: var(--text); text-align: left; padding: .45rem .6rem; border-radius: 6px; cursor: pointer; display: flex; justify-content: space-between; align-items: baseline; gap: .4rem; width: 100%; }
  .sess-btn:hover { background: var(--bg3); }
  .sess-btn.active { background: var(--bg3); font-weight: 600; }
  .sess-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .82rem; }
  .sess-date { flex-shrink: 0; font-size: .7rem; color: var(--text2); }
  #sidebar-toggle { display: none; }
  #sidebar-backdrop { display: none; }
  @media (max-width: 640px) {
    #chat-sidebar {
      position: absolute; left: 0; top: 0; bottom: 0; z-index: 200;
      width: 78%; max-width: 280px;
      transform: translateX(-100%);
      transition: transform .22s ease;
      box-shadow: 4px 0 16px rgba(0,0,0,.5);
    }
    #chat-sidebar.open { transform: translateX(0); }
    #sidebar-backdrop {
      display: block; position: absolute; inset: 0; z-index: 199;
      background: rgba(0,0,0,.45); opacity: 0; pointer-events: none;
      transition: opacity .22s ease;
    }
    #sidebar-backdrop.open { opacity: 1; pointer-events: auto; }
    #sidebar-toggle {
      display: flex; align-items: center; justify-content: center;
      background: none; border: none; color: var(--text2); font-size: 1.25rem;
      cursor: pointer; padding: .3rem .4rem; border-radius: 6px; flex-shrink: 0;
    }
    #sidebar-toggle:hover { background: var(--bg3); }
  }
  #chat-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #chat-thread { flex: 1; overflow-y: auto; padding: 1.5rem; display: flex; flex-direction: column; gap: .75rem; }
  .chat-msg { max-width: 72%; padding: .6rem 1rem; border-radius: 12px; font-size: .9rem; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
  .chat-user { align-self: flex-end; background: #1f6feb; color: #fff; border-radius: 12px 12px 2px 12px; }
  .chat-assistant { align-self: flex-start; background: var(--bg2); border: 1px solid var(--border); border-radius: 12px 12px 12px 2px; }
  .tool-badges { align-self: flex-start; display: flex; flex-wrap: wrap; gap: .3rem; margin-top: -.5rem; }
  .tool-badge { font-size: .7rem; background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; padding: 1px 7px; color: var(--text2); }
  #chat-input-bar { border-top: 1px solid var(--border); padding: .75rem 1rem; display: flex; align-items: flex-end; gap: .6rem; background: var(--bg); }
  #chat-msg { flex: 1; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: .55rem .75rem; font-size: .9rem; resize: none; font-family: inherit; }
  #chat-msg:focus { outline: none; border-color: var(--accent); }
  #chat-send { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .55rem 1.1rem; cursor: pointer; font-size: .9rem; font-weight: 600; white-space: nowrap; }
  #chat-send:hover { opacity: .85; }
  #img-label { font-size: 1.2rem; cursor: pointer; padding: .35rem; border-radius: 6px; user-select: none; }
  #img-label:hover { background: var(--bg3); }
  #img-preview-wrap { display: flex; align-items: center; gap: .4rem; }
  #img-preview { width: 40px; height: 40px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border); }
  #img-clear { background: none; border: none; color: var(--text2); cursor: pointer; font-size: .9rem; }
"""
    return _page_shell("Chat — SeeStar", body, css)


# ── Planner page ─────────────────────────────────────────────────────────────

def planner_page() -> str:
    import json as _json
    from nas_server.story import _page_shell
    from nas_server.config import settings
    lat  = settings.get("observer_lat", 33.18)
    lon  = settings.get("observer_lon", -111.57)
    elev = settings.get("observer_elevation_m", 350)
    horizon_json = _json.dumps(settings.get("observer_horizon", []))

    body = f"""
<div id="planner-wrap">
  <div id="planner-form">
    <h1 style="font-size:1.3rem;margin:0 0 1rem">Target Planner</h1>
    <div style="display:flex;flex-wrap:wrap;gap:.75rem;align-items:flex-end">
      <label class="pl-label" title="Night starting on this date (dusk → dawn)">Night of<input type="date" id="pl-from" class="pl-input"></label>
      <label class="pl-label">Lat<input type="number" id="pl-lat" class="pl-input pl-coord" step="0.01" value="{lat}"></label>
      <label class="pl-label">Lon<input type="number" id="pl-lon" class="pl-input pl-coord" step="0.01" value="{lon}"></label>
      <label class="pl-label">Elev (m)<input type="number" id="pl-elev" class="pl-input pl-coord" step="1" value="{elev}"></label>
      <button id="pl-compute" class="pl-btn">Compute</button>
      <button id="pl-replan-btn" class="pl-btn" style="display:none;background:var(--bg3);color:var(--text1);border:1px solid var(--border)" title="Replan the night using only checked targets">Replan selected</button>
      <button id="pl-save-tonight" class="pl-btn" style="display:none;background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb55" title="Override the stored nightly plan with this schedule">Save plan for tonight</button>
      <button id="pl-view-plan" class="pl-btn-sec" style="align-self:flex-end">Tonight's Plan</button>
    </div>
    <div style="margin-top:.85rem;border-top:1px solid var(--border);padding-top:.75rem">
      <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
        <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;font-size:.83rem;color:var(--text2)">
          <input type="checkbox" id="pl-horizon-on" style="accent-color:var(--accent)">
          Custom horizon (block targets behind trees / buildings)
        </label>
        <button type="button" id="pl-save-hz" style="padding:2px 8px;font-size:.8rem;background:var(--bg3);border:1px solid var(--border);color:var(--text2);border-radius:5px;cursor:pointer">Save as default</button>
        <span id="pl-save-hz-st" style="font-size:.78rem;color:#3fb950"></span>
      </div>
      <div id="pl-horizon-section" style="display:none;margin-top:.65rem">
        <textarea id="pl-horizon" class="pl-input" rows="4"
          style="width:100%;font-family:ui-monospace,monospace;font-size:.81rem;resize:vertical"
          placeholder="One point per line: azimuth, altitude&#10;e.g. 341, 15&#10;     3, 11&#10;     52, 1&#10;Or compass: N:15, NE:30, E:25"></textarea>
        <p style="font-size:.75rem;color:var(--text2);margin:.3rem 0 0">
          Azimuth (°) + altitude (°) per line · 0=North · Saved to browser automatically · "Save as default" persists to server.
        </p>
        <p id="pl-horizon-error" style="font-size:.75rem;color:#f87171;margin:.2rem 0 0;display:none"></p>
      </div>
    </div>
    <p id="pl-status" style="color:var(--text2);font-size:.85rem;margin:.5rem 0 0"></p>
  </div>
  <div id="planner-stored" style="display:none;margin-top:1.2rem">
    <div id="planner-stored-hdr" style="display:flex;align-items:center;gap:.75rem;margin-bottom:.6rem">
      <span id="planner-stored-date" style="font-size:.88rem;font-weight:600;color:var(--text)"></span>
      <span id="planner-stored-wx" style="font-size:.8rem;color:var(--text2)"></span>
    </div>
    <div id="planner-stored-tbl" style="overflow-x:auto"></div>
  </div>
  <div id="planner-narrative" style="display:none;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:1rem 1.2rem;margin-top:1.2rem;line-height:1.65;font-size:.9rem"></div>
  <canvas id="pl-chart" width="700" height="250" style="display:none;width:100%;max-width:900px;margin-top:1.2rem;border-radius:8px"></canvas>
  <div id="planner-sched-tbl" style="display:none;margin-top:.75rem;overflow-x:auto"></div>
  <div id="planner-results" style="display:none;margin-top:1.2rem;overflow-x:auto">
    <table class="pl-table">
      <thead><tr>
        <th><input type="checkbox" id="pl-check-all" title="Select all"></th>
        <th>#</th><th>Target</th><th>Score</th><th>Max Alt</th><th id="pl-th-vis" style="display:none">Visible</th>
        <th>Window</th><th>Transit (MST)</th><th>Moon Sep</th><th>Moon %</th><th>Int Hrs</th><th></th>
      </tr></thead>
      <tbody id="pl-tbody"></tbody>
    </table>
  </div>
</div>
<script>
(function(){{
  function fmtDate(d) {{ return d.toISOString().slice(0, 10); }}
  // Default to today in Arizona (MST = UTC−7, no DST)
  document.getElementById('pl-from').value = fmtDate(new Date(Date.now() - 7 * 3600 * 1000));

  // ── Horizon: server default + localStorage override ───────────────────────
  var serverHorizon = {horizon_json};
  var serverHorizonTxt = serverHorizon.map(function(p){{ return p[0] + ', ' + p[1]; }}).join('\\n');
  var horizonEl   = document.getElementById('pl-horizon');
  var horizonChk  = document.getElementById('pl-horizon-on');
  var horizonSec  = document.getElementById('pl-horizon-section');
  var horizonErr  = document.getElementById('pl-horizon-error');

  var savedTxt = localStorage.getItem('planner_horizon');
  var savedOn  = localStorage.getItem('planner_horizon_on');
  horizonEl.value = savedTxt !== null ? savedTxt : serverHorizonTxt;
  horizonChk.checked = savedOn !== null ? savedOn === '1' : serverHorizon.length > 0;
  horizonSec.style.display = horizonChk.checked ? '' : 'none';

  horizonChk.addEventListener('change', function() {{
    horizonSec.style.display = this.checked ? '' : 'none';
    localStorage.setItem('planner_horizon_on', this.checked ? '1' : '0');
  }});
  horizonEl.addEventListener('input', function() {{
    localStorage.setItem('planner_horizon', this.value);
  }});

  // ── Save as default ───────────────────────────────────────────────────────
  document.getElementById('pl-save-hz').addEventListener('click', function() {{
    var pts = parseHorizon(horizonEl.value);
    var stEl = document.getElementById('pl-save-hz-st');
    if (!pts) {{ stEl.style.color = '#f87171'; stEl.textContent = 'Parse error'; return; }}
    fetch('/settings/horizon', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{horizon: pts}})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      stEl.style.color = '#3fb950';
      stEl.textContent = 'Saved (' + d.count + ' pts)';
      setTimeout(function() {{ stEl.textContent = ''; }}, 3000);
    }})
    .catch(function() {{ stEl.style.color = '#f87171'; stEl.textContent = 'Error'; }});
  }});

  // ── Horizon text → [[az, alt], ...] ──────────────────────────────────────
  var DIRS = {{N:0,NNE:22.5,NE:45,ENE:67.5,E:90,ESE:112.5,SE:135,SSE:157.5,
               S:180,SSW:202.5,SW:225,WSW:247.5,W:270,WNW:292.5,NW:315,NNW:337.5}};
  function parseHorizon(txt) {{
    if (!txt.trim()) return null;
    var pts = [];
    var lines = txt.trim().split('\\n');
    if (lines.length > 1) {{
      // Newline-separated: "341, 15" or "341 15" per line
      lines.forEach(function(line) {{
        line = line.trim();
        if (!line || line.charAt(0) === '#') return;
        var parts = line.split(/[\s,]+/);
        var az = parseFloat(parts[0]);
        var alt = parseFloat(parts[parts.length - 1]);
        if (!isNaN(az) && !isNaN(alt) && parts.length >= 2) pts.push([az, alt]);
      }});
    }} else {{
      // Single-line comma-separated: "N:15, E:30"
      txt.split(',').forEach(function(pair) {{
        pair = pair.trim();
        if (!pair) return;
        var parts = pair.split(':');
        if (parts.length !== 2) return;
        var azStr = parts[0].trim().toUpperCase();
        var az = parseFloat(azStr);
        if (isNaN(az)) {{ az = DIRS[azStr]; }}
        var alt = parseFloat(parts[1].trim());
        if (az !== undefined && !isNaN(az) && !isNaN(alt)) pts.push([az, alt]);
      }});
    }}
    return pts.length >= 2 ? pts : null;
  }}

  function fmtAssoc(raw) {{
    if (!raw) return '';
    // Deduplicate: normalise by stripping internal spaces (M 81 == M81), keep first occurrence
    var seen = {{}};
    var parts = raw.split(',').map(function(s) {{ return s.trim(); }}).filter(Boolean);
    var deduped = [];
    parts.forEach(function(p) {{
      var key = p.replace(/\s+/g, '').toUpperCase();
      if (!seen[key]) {{ seen[key] = true; deduped.push(p); }}
    }});
    // Show up to 2; add count if more
    if (deduped.length > 2) return deduped.slice(0,2).join(', ') + ' +' + (deduped.length-2);
    return deduped.join(', ');
  }}

  function scorePill(s) {{
    var col = s >= 0.7 ? '#3fb950' : s >= 0.4 ? '#e3b341' : '#f85149';
    return '<span style="background:' + col + '22;color:' + col + ';border:1px solid ' + col + '44;'
         + 'border-radius:4px;padding:1px 7px;font-size:.78rem;font-weight:600">' + s.toFixed(2) + '</span>';
  }}

  var lastPlanBody = null;  // saved so Replan can re-use the same date/location params
  var lastSchedule = null;  // saved so Save Tonight can persist the current schedule

  document.getElementById('pl-compute').addEventListener('click', function() {{
    var btn = this;
    var useHorizon = horizonChk.checked;
    var horizonPts = null;
    horizonErr.style.display = 'none';

    if (useHorizon) {{
      horizonPts = parseHorizon(horizonEl.value);
      if (!horizonPts) {{
        horizonErr.textContent = 'Need at least 2 valid points (e.g. N:15, E:30, S:10, W:20)';
        horizonErr.style.display = '';
        return;
      }}
    }}

    btn.disabled = true;
    btn.textContent = 'Computing…';
    document.getElementById('pl-status').textContent = 'Resolving coordinates and computing visibility…';
    document.getElementById('planner-narrative').style.display = 'none';
    document.getElementById('planner-results').style.display = 'none';
    document.getElementById('planner-sched-tbl').style.display = 'none';

    var _ctrl = new AbortController();
    var _timer = setTimeout(function() {{ _ctrl.abort(); }}, 90000);
    lastPlanBody = {{
      date_from: document.getElementById('pl-from').value,
      lat:  parseFloat(document.getElementById('pl-lat').value),
      lon:  parseFloat(document.getElementById('pl-lon').value),
      elevation: parseFloat(document.getElementById('pl-elev').value),
      horizon: horizonPts,
    }};
    fetch('/planner/compute', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      signal: _ctrl.signal,
      body: JSON.stringify(lastPlanBody)
    }})
    .then(function(r) {{
      clearTimeout(_timer);
      if (!r.ok) throw new Error('Server error ' + r.status);
      return r.json();
    }})
    .then(function(d) {{
      btn.disabled = false;
      btn.textContent = 'Compute';
      var horizonLabel = horizonPts ? ' (custom horizon)' : '';
      document.getElementById('pl-status').textContent = d.count + ' targets scored' + horizonLabel + '.';

      if (d.narrative) {{
        var narr = document.getElementById('planner-narrative');
        narr.textContent = d.narrative;
        narr.style.display = 'block';
      }}

      // Show/hide Visible column
      var showVis = !!horizonPts;
      document.getElementById('pl-th-vis').style.display = showVis ? '' : 'none';

      var tbody = document.getElementById('pl-tbody');
      tbody.innerHTML = '';
      (d.results || []).forEach(function(r, i) {{
        var visCell = showVis
          ? '<td class="pl-num" style="color:var(--accent)">' + (r.time_visible_h || 0).toFixed(1) + 'h</td>'
          : '';
        var tr = document.createElement('tr');
        var newBadge = r.int_hours === 0 ? ' <span style="font-size:.7rem;background:#1f6feb;color:#fff;border-radius:3px;padding:1px 5px;vertical-align:middle">NEW</span>' : '';
        var mosaicTitle = r.mosaic_class === 's50_framing' ? 'Use SeeStar Framing mode + same target name → Siril max framing. No panel naming or stitching needed.' : 'Larger than S50 2× framing range. Use separate panel names + stitch in Siril/PI.';
        var mosaicBadge = r.is_mosaic ? ' <span style="font-size:.68rem;background:#6e40c922;color:#d2a8ff;border:1px solid #6e40c955;border-radius:3px;padding:1px 4px" title="' + mosaicTitle + '">' + (r.mosaic_label || (r.mosaic_panels_w + '×' + r.mosaic_panels_h + ' mosaic')) + '</span>' : '';
        var assocFmt = fmtAssoc(r.association);
        var assocBadge = assocFmt ? ' <span style="font-size:.68rem;color:var(--text2)" title="Associated: ' + r.association + '">⟷ ' + assocFmt + '</span>' : '';
        var transientBadge = r.transient ? ' <span style="font-size:.68rem;background:#7d4a0022;color:#e3a020;border:1px solid #7d4a0055;border-radius:3px;padding:1px 4px" title="Transient target — 1.5× score boost">⚡</span>' : '';
        var procBadge = r.processing_tag === 'finished' ? ' <span style="font-size:.68rem;background:#16331c22;color:#3fb950;border:1px solid #3fb95055;border-radius:3px;padding:1px 4px" title="Already has a good processed result — eased off acquisition">✓ done</span>' : '';
        var complColors = {{'almost done':['#3d2f0d','#e3b341'],'enough data':['#21262d','#8b949e']}};
        var complC = r.completion_tag ? (complColors[r.completion_tag] || ['#21262d','#8b949e']) : null;
        var complTitle = r.completion_frac != null ? Math.round(r.completion_frac * 100) + '% of recommended integration' : '';
        var complBadge = complC ? ' <span style="font-size:.68rem;background:' + complC[0] + '22;color:' + complC[1] + ';border:1px solid ' + complC[1] + '55;border-radius:3px;padding:1px 4px" title="' + complTitle + '">' + r.completion_tag + '</span>' : '';
        var scarcityPct = r.scarcity_score != null ? Math.round(r.scarcity_score * 100) : null;
        var scarcityTitle = scarcityPct != null ? 'Seasonal scarcity: ' + scarcityPct + '% of year not usefully visible' : '';
        tr.innerHTML =
          '<td style="padding:.3rem .4rem"><input type="checkbox" class="pl-check" data-target="' + r.target.replace(/"/g,'&quot;') + '"></td>'
          + '<td class="pl-rank">' + (i+1) + '</td>'
          + '<td class="pl-target"><span title="' + scarcityTitle + '">' + r.target + '</span>' + newBadge + mosaicBadge + assocBadge + transientBadge + complBadge + procBadge + '</td>'
          + '<td>' + scorePill(r.combined_score) + '</td>'
          + '<td class="pl-num">' + r.max_alt + '°</td>'
          + visCell
          + '<td class="pl-ts" style="white-space:nowrap;font-size:.8rem">' + (r.window_start_hhmm || '—') + '–' + (r.window_end_hhmm || '—') + '</td>'
          + '<td class="pl-ts">' + (r.transit_mst || r.transit_utc || '—') + '</td>'
          + '<td class="pl-num">' + r.min_moon_sep + '°</td>'
          + '<td class="pl-num">' + r.moon_illum_pct + '%</td>'
          + '<td class="pl-num">' + r.int_hours.toFixed(1) + 'h</td>'
          + '<td><button class="pl-queue-btn" data-target="' + encodeURIComponent(r.target) + '">Queue</button></td>';
        tbody.appendChild(tr);
      }});

      lastSchedule = d.schedule || [];
      document.getElementById('planner-results').style.display = '';
      document.getElementById('pl-replan-btn').style.display = '';
      document.getElementById('pl-save-tonight').style.display = '';
      document.getElementById('pl-save-tonight').textContent = 'Save plan for tonight';
      tbody.querySelectorAll('.pl-queue-btn').forEach(function(b) {{
        b.addEventListener('click', function() {{
          window.open('/queue-view?target=' + this.dataset.target, '_blank');
        }});
      }});

      // Check-all toggle
      document.getElementById('pl-check-all').checked = false;
      document.getElementById('pl-check-all').onchange = function() {{
        var on = this.checked;
        document.querySelectorAll('.pl-check').forEach(function(c) {{ c.checked = on; }});
      }};

      drawScheduleChart(d.results || [], d.schedule || [], horizonPts);
    }})
    .catch(function(e) {{
      clearTimeout(_timer);
      btn.disabled = false;
      btn.textContent = 'Compute';
      document.getElementById('pl-status').textContent =
        e.name === 'AbortError' ? 'Timed out — try again' : 'Error: ' + e.message;
    }});
  }});

  // ── Replan button ────────────────────────────────────────────────────────
  document.getElementById('pl-replan-btn').addEventListener('click', function() {{
    if (!lastPlanBody) return;
    var checked = Array.from(document.querySelectorAll('.pl-check:checked'))
                       .map(function(c) {{ return c.dataset.target; }});
    if (!checked.length) {{
      alert('Check at least one target in the ranked list first.');
      return;
    }}
    var btn = this;
    btn.disabled = true;
    btn.textContent = 'Replanning…';
    document.getElementById('planner-sched-tbl').style.display = 'none';

    fetch('/planner/compute', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(Object.assign({{}}, lastPlanBody, {{ selected: checked }}))
    }})
    .then(function(r) {{ if (!r.ok) throw new Error('Server error ' + r.status); return r.json(); }})
    .then(function(d) {{
      btn.disabled = false;
      btn.textContent = 'Replan selected';
      lastSchedule = d.schedule || [];
      // Only update the chart and schedule table — leave the ranked list untouched
      drawScheduleChart(d.results || [], d.schedule || [], lastPlanBody.horizon);
      renderScheduleTable(d.schedule || [], d.results || []);
      // Record user preference — fire-and-forget, not critical
      var allRanked = Array.from(document.querySelectorAll('.pl-check')).map(function(c) {{ return c.dataset.target; }});
      fetch('/planner/record-selection', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ all_ranked: allRanked, selected: checked }})
      }}).catch(function() {{}});
    }})
    .catch(function(e) {{
      btn.disabled = false;
      btn.textContent = 'Replan selected';
      alert('Replan failed: ' + e.message);
    }});
  }});

  // ── Save plan for tonight ─────────────────────────────────────────────────
  document.getElementById('pl-save-tonight').addEventListener('click', function() {{
    if (!lastSchedule || !lastSchedule.length) {{ alert('No schedule to save.'); return; }}
    var btn = this;
    btn.disabled = true;
    btn.textContent = 'Saving…';
    fetch('/planner/save-tonight', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ date: lastPlanBody.date_from, schedule: lastSchedule }})
    }})
    .then(function(r) {{ if (!r.ok) throw new Error('Server error ' + r.status); return r.json(); }})
    .then(function() {{
      btn.disabled = false;
      btn.textContent = 'Saved ✓';
      setTimeout(function() {{ btn.textContent = 'Save plan for tonight'; }}, 3000);
    }})
    .catch(function(e) {{
      btn.disabled = false;
      btn.textContent = 'Save plan for tonight';
      alert('Save failed: ' + e.message);
    }});
  }});

  // ── Schedule chart ────────────────────────────────────────────────────────
  // schedule: [{{target, start_idx, end_idx, start_hhmm, end_hhmm, planned_h, rec_h, int_hours}}]
  // results:  full ranked list (used for alt_curve lookup)
  function drawScheduleChart(results, schedule, horizonPts) {{
    var canvas = document.getElementById('pl-chart');
    if (!schedule || !schedule.length) {{ canvas.style.display = 'none'; return; }}

    // Build lookup: target name → result row (for alt_curve)
    var byTarget = {{}};
    results.forEach(function(r) {{ if (r.alt_curve) byTarget[r.target] = r; }});

    // Verify all scheduled targets have curves
    var slots = schedule.filter(function(s) {{ return byTarget[s.target]; }});
    if (!slots.length) {{ canvas.style.display = 'none'; return; }}

    canvas.style.display = '';
    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var pad = {{l:38, r:12, t:28, b:32}};
    var cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
    var colors = ['#58a6ff','#3fb950','#e3b341','#d2a8ff','#ff7b72','#ffa657','#39c5cf','#f778ba'];
    var n = results[0].alt_curve.length;
    var times = results[0].alt_curve.map(function(p) {{ return p[0]; }});

    // Trim chart to scheduled window — one step of padding on each side
    var i_start = Math.max(0, slots[0].start_idx - 1);
    var i_end   = Math.min(n - 1, slots[slots.length - 1].end_idx + 1);

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, W, H);

    function xp(i) {{ return pad.l + ((i - i_start) / Math.max(1, i_end - i_start)) * cw; }}
    function yp(a) {{ return pad.t + ch - (Math.max(0, Math.min(90, a)) / 90) * ch; }}

    // Altitude grid
    ctx.strokeStyle = '#21262d';
    ctx.lineWidth = 1;
    [0,15,30,45,60,75,90].forEach(function(a) {{
      ctx.beginPath(); ctx.moveTo(pad.l, yp(a)); ctx.lineTo(pad.l + cw, yp(a)); ctx.stroke();
    }});

    // 30° reference dash
    ctx.strokeStyle = '#444c56';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(pad.l, yp(30)); ctx.lineTo(pad.l + cw, yp(30)); ctx.stroke();
    ctx.setLineDash([]);

    // Ghost curves — dim altitude trace, clipped to above-horizon portion only
    slots.forEach(function(slot, ci) {{
      var row = byTarget[slot.target];
      var curve = row.alt_curve;
      var hcurve = row.horizon_curve;
      ctx.globalAlpha = 0.18;
      ctx.strokeStyle = colors[ci % colors.length];
      ctx.lineWidth = 1;
      ctx.beginPath();
      var penDown = false;
      curve.forEach(function(p, i) {{
        if (i < i_start || i > i_end) {{ penDown = false; return; }}
        var minAlt = hcurve ? hcurve[i] : 15.0;
        if (p[1] >= minAlt) {{
          if (!penDown) {{ ctx.moveTo(xp(i), yp(p[1])); penDown = true; }}
          else ctx.lineTo(xp(i), yp(p[1]));
        }} else {{
          penDown = false;
        }}
      }});
      ctx.stroke();
      ctx.globalAlpha = 1.0;
    }});

    // Horizon curves — dashed line showing effective horizon altitude for each target
    slots.forEach(function(slot, ci) {{
      var row = byTarget[slot.target];
      if (!row.horizon_curve) return;
      var hcurve = row.horizon_curve;
      ctx.globalAlpha = 0.55;
      ctx.strokeStyle = colors[ci % colors.length];
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      var hPen = false;
      hcurve.forEach(function(alt, i) {{
        if (i < i_start || i > i_end) {{ hPen = false; return; }}
        if (!hPen) {{ ctx.moveTo(xp(i), yp(alt)); hPen = true; }}
        else ctx.lineTo(xp(i), yp(alt));
      }});
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1.0;
    }});

    // Slot background tints (very subtle alternating)
    slots.forEach(function(slot, ci) {{
      var color = colors[ci % colors.length];
      // parse hex to rgba
      var r = parseInt(color.slice(1,3),16), g = parseInt(color.slice(3,5),16), b = parseInt(color.slice(5,7),16);
      ctx.fillStyle = 'rgba(' + r + ',' + g + ',' + b + ',0.04)';
      ctx.fillRect(xp(slot.start_idx), pad.t, xp(slot.end_idx) - xp(slot.start_idx), ch);
    }});

    // Active curve segments (inclusive of end_idx so 1-step slots still draw a line)
    slots.forEach(function(slot, ci) {{
      var curve = byTarget[slot.target].alt_curve;
      ctx.strokeStyle = colors[ci % colors.length];
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      var segEnd = Math.min(slot.end_idx, curve.length - 1);
      for (var i = slot.start_idx; i <= segEnd; i++) {{
        var px = xp(i), py = yp(curve[i][1]);
        if (i === slot.start_idx) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }}
      ctx.stroke();
    }});

    // Slot dividers (vertical lines between slots)
    slots.forEach(function(slot, ci) {{
      if (ci === 0) return;
      ctx.strokeStyle = '#30363d';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      var x = xp(slot.start_idx);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, H - pad.b); ctx.stroke();
      ctx.setLineDash([]);
    }});

    // Labels: target name + planned hours, centered in each slot, above the curve
    slots.forEach(function(slot, ci) {{
      var curve = byTarget[slot.target].alt_curve;
      var color = colors[ci % colors.length];

      // Find peak altitude within slot (inclusive of end_idx)
      var peakAlt = -999, peakIdx = slot.start_idx;
      var peakEnd = Math.min(slot.end_idx, curve.length - 1);
      for (var i = slot.start_idx; i <= peakEnd; i++) {{
        if (curve[i][1] > peakAlt) {{ peakAlt = curve[i][1]; peakIdx = i; }}
      }}

      // Label x: center of slot; y: just above peak, but not too close to top
      var cx = (xp(slot.start_idx) + xp(Math.min(slot.end_idx, n-1))) / 2;
      var ly = Math.min(yp(peakAlt) - 8, pad.t + ch - 20);
      ly = Math.max(ly, pad.t + 4);

      ctx.fillStyle = color;
      ctx.font = 'bold 10px system-ui,sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(slot.target, cx, ly);

      // Sub-label: planned hours  (e.g. "2.0h / 5.0h rec")
      var deficit = slot.rec_h - slot.int_hours;
      var sub = slot.planned_h.toFixed(1) + 'h planned';
      if (deficit > 0.1) sub += ' · ' + deficit.toFixed(1) + 'h needed';
      ctx.font = '8px system-ui,sans-serif';
      ctx.fillStyle = '#8b949e';
      ctx.fillText(sub, cx, ly + 11);
    }});

    // X-axis time labels and hour tick lines
    ctx.fillStyle = '#8b949e';
    ctx.font = '9px system-ui,sans-serif';
    ctx.textAlign = 'center';
    times.forEach(function(t, i) {{
      if (i < i_start || i > i_end) return;
      if (t.slice(3) === '00') {{
        ctx.strokeStyle = '#21262d';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(xp(i), pad.t); ctx.lineTo(xp(i), H - pad.b); ctx.stroke();
        ctx.fillStyle = '#8b949e';
        ctx.fillText(t, xp(i), H - pad.b + 14);
      }}
    }});

    // Y-axis labels
    ctx.textAlign = 'right';
    [0,30,60,90].forEach(function(a) {{
      ctx.fillStyle = '#8b949e';
      ctx.font = '9px system-ui,sans-serif';
      ctx.fillText(a + '°', pad.l - 4, yp(a) + 3);
    }});

    // Title
    ctx.fillStyle = '#c9d1d9';
    ctx.font = '10px system-ui,sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText("Tonight's plan (AZ local time)", pad.l, 18);

    renderScheduleTable(slots, colors);
  }}

  function renderScheduleTable(slots, colors) {{
    var tblDiv = document.getElementById('planner-sched-tbl');
    if (!tblDiv || !slots || !slots.length) return;
    fetch('/planner/autoflags').then(function(r) {{ return r.json(); }}).then(function(autoflags) {{
      var rows = slots.map(function(slot, ci) {{
        var color = colors[ci % colors.length];
        var need = Math.max(0, slot.rec_h - slot.int_hours);
        var af = autoflags[slot.target] || {{}};
        var tenc = encodeURIComponent(slot.target);
        var newBadge = slot.int_hours === 0 ? ' <span style="font-size:.68rem;background:#1f6feb;color:#fff;border-radius:3px;padding:1px 4px">NEW</span>' : '';
        var mosaicTitle2 = slot.mosaic_class === 's50_framing' ? 'Use SeeStar Framing mode + same target name → Siril max framing. No panel naming or stitching needed.' : 'Larger than S50 2× framing range. Use separate panel names + stitch in Siril/PI.';
        var mosaicBadge = slot.is_mosaic ? ' <span style="font-size:.68rem;background:#6e40c922;color:#d2a8ff;border:1px solid #6e40c955;border-radius:3px;padding:1px 4px" title="' + mosaicTitle2 + '">' + (slot.mosaic_label || (slot.mosaic_panels_w + '×' + slot.mosaic_panels_h)) + '</span>' : '';
        var assocFmt2 = fmtAssoc(slot.association);
        var assocBadge = assocFmt2 ? ' <span style="font-size:.68rem;color:var(--text2)" title="' + slot.association + '">⟷ ' + assocFmt2 + '</span>' : '';
        return '<tr data-target="' + tenc + '">'
          + '<td><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + color + ';vertical-align:middle"></span></td>'
          + '<td style="color:' + color + ';font-weight:600">' + slot.target + newBadge + mosaicBadge + assocBadge + '</td>'
          + '<td>' + slot.start_hhmm + '</td>'
          + '<td>' + slot.end_hhmm + '</td>'
          + '<td>' + slot.planned_h.toFixed(1) + 'h</td>'
          + '<td style="color:var(--text2)">' + (slot.int_hours > 0 ? slot.int_hours.toFixed(1) + 'h' : '—') + '</td>'
          + '<td style="color:' + (need > 0.1 ? '#e3b341' : '#3fb950') + '">'
          +   (need > 0.1 ? need.toFixed(1) + 'h' : '✓') + '</td>'
          + '<td style="text-align:center"><label title="Auto-stack after transfer"><input type="checkbox" class="af-stack" data-target="' + tenc + '"' + (af.auto_stack ? ' checked' : '') + '><span style="font-size:.72rem;color:var(--text2);margin-left:3px">Stack</span></label></td>'
          + '<td style="text-align:center"><label title="Auto-process after stack"><input type="checkbox" class="af-proc" data-target="' + tenc + '"' + (af.auto_process ? ' checked' : '') + '><span style="font-size:.72rem;color:var(--text2);margin-left:3px">Process</span></label></td>'
          + '</tr>';
      }});
      tblDiv.innerHTML = '<table style="border-collapse:collapse;font-size:.82rem;width:100%;max-width:960px">'
        + '<thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">'
        + '<th style="padding:.3rem .5rem"></th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Target</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Start</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Stop</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Planned</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Have</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Need</th>'
        + '<th style="padding:.3rem .5rem;text-align:center" title="Auto-stack with Siril defaults after transfer completes">Auto Stack</th>'
        + '<th style="padding:.3rem .5rem;text-align:center" title="Auto-process after stack">Auto Process</th>'
        + '</tr></thead>'
        + '<tbody>' + rows.join('') + '</tbody></table>';
      tblDiv.querySelectorAll('tbody tr').forEach(function(tr) {{
        tr.style.borderBottom = '1px solid #21262d';
        tr.querySelectorAll('td').forEach(function(td) {{
          td.style.padding = '.3rem .5rem';
          td.style.whiteSpace = 'nowrap';
        }});
      }});
      function patchFlag(target, key, val) {{
        var body = {{target: target}};
        body[key] = val;
        fetch('/planner/autoflags', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
      }}
      tblDiv.querySelectorAll('.af-stack').forEach(function(cb) {{
        cb.addEventListener('change', function() {{
          patchFlag(decodeURIComponent(this.dataset.target), 'auto_stack', this.checked);
        }});
      }});
      tblDiv.querySelectorAll('.af-proc').forEach(function(cb) {{
        cb.addEventListener('change', function() {{
          patchFlag(decodeURIComponent(this.dataset.target), 'auto_process', this.checked);
        }});
      }});
      tblDiv.style.display = '';
    }}).catch(function() {{
      tblDiv.style.display = '';
    }});
  }}
  // ── Tonight's Plan (stored 6 PM plan) ────────────────────────────────────
  document.getElementById('pl-view-plan').addEventListener('click', function() {{
    var btn = this;
    btn.disabled = true;
    btn.textContent = 'Loading…';

    // Hide computed results while showing stored plan
    document.getElementById('planner-narrative').style.display = 'none';
    document.getElementById('planner-results').style.display = 'none';
    document.getElementById('planner-sched-tbl').style.display = 'none';
    document.getElementById('pl-chart').style.display = 'none';
    document.getElementById('pl-status').textContent = '';

    Promise.all([
      fetch('/planner/stored-plan').then(function(r) {{ return r.json(); }}),
      fetch('/planner/autoflags').then(function(r) {{ return r.json(); }})
    ]).then(function(results) {{
      btn.disabled = false;
      btn.textContent = 'Tonight’s Plan';

      var data = results[0];
      var autoflags = results[1];
      var storedDiv = document.getElementById('planner-stored');
      var hdrDiv    = document.getElementById('planner-stored-date');
      var tblDiv    = document.getElementById('planner-stored-tbl');

      if (!data.plan || !data.plan.slots || !data.plan.slots.length) {{
        hdrDiv.textContent = 'No saved plan found';
        tblDiv.innerHTML = '<p style="font-size:.85rem;color:var(--text2)">The nightly plan runs at 6 PM AZ time. No plan has been saved yet.</p>';
        storedDiv.style.display = '';
        return;
      }}

      var plan = data.plan;
      hdrDiv.textContent = '🌙 Plan for ' + plan.date + ' (generated at 6 PM AZ)';

      var colors = ['#58a6ff','#3fb950','#e3b341','#d2a8ff','#ff7b72','#ffa657','#39c5cf','#f778ba'];
      var rows = plan.slots.map(function(slot, ci) {{
        var color = colors[ci % colors.length];
        var recH  = slot.rec_h || 0;
        var need  = Math.max(0, recH - slot.int_hours);
        var af    = autoflags[slot.target] || {{}};
        var tenc  = encodeURIComponent(slot.target);
        var newBadge = slot.int_hours === 0
          ? ' <span style="font-size:.68rem;background:#1f6feb;color:#fff;border-radius:3px;padding:1px 4px">NEW</span>'
          : '';
        var plannedCell = slot.planned_h != null ? slot.planned_h.toFixed(1) + 'h' : '—';
        var haveCell    = slot.int_hours > 0 ? slot.int_hours.toFixed(1) + 'h' : '—';
        var needCell    = need > 0.1
          ? '<span style="color:#e3b341">' + need.toFixed(1) + 'h</span>'
          : '<span style="color:#3fb950">✓</span>';
        return '<tr style="border-bottom:1px solid #21262d" data-target="' + tenc + '">'
          + '<td style="padding:.3rem .5rem"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + color + ';vertical-align:middle"></span></td>'
          + '<td style="padding:.3rem .5rem;color:' + color + ';font-weight:600;white-space:nowrap">' + slot.target + newBadge + '</td>'
          + '<td style="padding:.3rem .5rem;white-space:nowrap">' + (slot.start_hhmm || '—') + '</td>'
          + '<td style="padding:.3rem .5rem;white-space:nowrap">' + (slot.end_hhmm || '—') + '</td>'
          + '<td style="padding:.3rem .5rem;white-space:nowrap">' + plannedCell + '</td>'
          + '<td style="padding:.3rem .5rem;white-space:nowrap;color:var(--text2)">' + haveCell + '</td>'
          + '<td style="padding:.3rem .5rem;white-space:nowrap">' + needCell + '</td>'
          + '<td style="padding:.3rem .5rem;text-align:center"><label title="Auto-stack after transfer"><input type="checkbox" class="saf-stack" data-target="' + tenc + '"' + (af.auto_stack ? ' checked' : '') + '><span style="font-size:.72rem;color:var(--text2);margin-left:3px">Stack</span></label></td>'
          + '<td style="padding:.3rem .5rem;text-align:center"><label title="Auto-process after stack"><input type="checkbox" class="saf-proc" data-target="' + tenc + '"' + (af.auto_process ? ' checked' : '') + '><span style="font-size:.72rem;color:var(--text2);margin-left:3px">Process</span></label></td>'
          + '</tr>';
      }});

      tblDiv.innerHTML = '<table style="border-collapse:collapse;font-size:.82rem;width:100%;max-width:960px">'
        + '<thead><tr style="color:var(--text2);border-bottom:1px solid var(--border)">'
        + '<th style="padding:.3rem .5rem"></th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Target</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Start</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Stop</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Planned</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Have</th>'
        + '<th style="padding:.3rem .5rem;text-align:left">Need</th>'
        + '<th style="padding:.3rem .5rem;text-align:center">Auto Stack</th>'
        + '<th style="padding:.3rem .5rem;text-align:center">Auto Process</th>'
        + '</tr></thead>'
        + '<tbody>' + rows.join('') + '</tbody></table>';

      // Wire autoflags checkboxes
      function patchFlag2(target, key, val) {{
        var body = {{target: target}};
        body[key] = val;
        fetch('/planner/autoflags', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
      }}
      tblDiv.querySelectorAll('.saf-stack').forEach(function(cb) {{
        cb.addEventListener('change', function() {{
          patchFlag2(decodeURIComponent(this.dataset.target), 'auto_stack', this.checked);
        }});
      }});
      tblDiv.querySelectorAll('.saf-proc').forEach(function(cb) {{
        cb.addEventListener('change', function() {{
          patchFlag2(decodeURIComponent(this.dataset.target), 'auto_process', this.checked);
        }});
      }});

      storedDiv.style.display = '';
    }}).catch(function(e) {{
      btn.disabled = false;
      btn.textContent = 'Tonight’s Plan';
      document.getElementById('pl-status').textContent = 'Error loading plan: ' + e.message;
    }});
  }});

}})();
</script>"""

    css = """
  #planner-wrap { max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }
  #planner-form { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem 1.5rem; }
  .pl-label { display: flex; flex-direction: column; gap: .25rem; font-size: .8rem; color: var(--text2); }
  .pl-input { background: var(--bg3); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: .35rem .6rem; font-size: .88rem; margin-top: 2px; }
  .pl-input:focus { outline: none; border-color: var(--accent); }
  .pl-coord { width: 90px; }
  .pl-btn { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .45rem 1.2rem; cursor: pointer; font-size: .9rem; font-weight: 600; align-self: flex-end; }
  .pl-btn:hover { opacity: .85; }
  .pl-btn:disabled { opacity: .5; cursor: default; }
  .pl-btn-sec { background: transparent; color: var(--accent); border: 1px solid var(--accent); border-radius: 8px; padding: .45rem 1.2rem; cursor: pointer; font-size: .9rem; font-weight: 600; }
  .pl-btn-sec:hover { background: var(--accent)22; }
  .pl-btn-sec:disabled { opacity: .5; cursor: default; }
  .pl-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  .pl-table th { text-align: left; padding: .4rem .7rem; color: var(--text2); font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; border-bottom: 1px solid var(--border); }
  .pl-table td { padding: .45rem .7rem; border-bottom: 1px solid var(--border)22; }
  .pl-table tr:hover td { background: var(--bg2); }
  .pl-rank { color: var(--text2); font-size: .8rem; width: 30px; }
  .pl-target { font-weight: 600; }
  .pl-num { text-align: right; color: var(--text2); white-space: nowrap; }
  .pl-ts { color: var(--text2); font-size: .82rem; white-space: nowrap; }
  .pl-queue-btn { background: none; border: 1px solid var(--border); color: var(--accent); border-radius: 4px; padding: 2px 10px; cursor: pointer; font-size: .78rem; }
  .pl-queue-btn:hover { background: var(--accent); color: #fff; }
"""
    return _page_shell("Planner — SeeStar", body, css)


# ── Suggestions page ─────────────────────────────────────────────────────────

def suggestions_page() -> str:
    from nas_server.story import _page_shell
    from nas_server.database import get_agent_suggestions
    rows = get_agent_suggestions()

    _source_badge = {
        "planner": '<span class="sug-badge sug-badge-planner">📅 Planner</span>',
        "user":    '<span class="sug-badge sug-badge-user">👤 You</span>',
        "agent":   '<span class="sug-badge sug-badge-agent">🤖 Agent</span>',
    }

    if not rows:
        cards = '<p style="color:var(--text2);padding:2rem">No suggestions logged yet.</p>'
    else:
        cards = ""
        for r in rows:
            resolved = r["resolved"]
            snippet = r.get("code_snippet", "")
            snippet_html = (f'<pre style="background:var(--bg3);padding:.6rem .8rem;'
                            f'border-radius:4px;font-size:.78rem;overflow-x:auto;'
                            f'margin:.5rem 0 0">{snippet}</pre>') if snippet else ""
            source = r.get("source") or "agent"
            badge = _source_badge.get(source, _source_badge["agent"])
            cards += f"""
<div class="sug-card {'sug-resolved' if resolved else ''}">
  <div class="sug-meta">
    {badge}
    &nbsp;# {r['id']} &nbsp;·&nbsp; {_fmt_mst(r['created_at'])}
    {f'&nbsp;·&nbsp; <span style="color:#3fb950">resolved</span>' if resolved else ''}
    {f'&nbsp;·&nbsp; <code style="color:var(--text2)">{r["file_hint"]}</code>' if r.get("file_hint") else ''}
  </div>
  <div class="sug-desc">{r['description']}</div>
  {snippet_html}
  {'<button class="sug-resolve-btn" data-id="' + str(r["id"]) + '">Mark resolved</button>' if not resolved else ''}
</div>"""

    body = f"""
<div style="max-width:860px;margin:0 auto;padding:2rem 1rem">
  <h1 style="font-size:1.4rem;margin-bottom:1.25rem">Suggestions</h1>
  <p style="color:var(--text2);font-size:.85rem;margin-bottom:1.5rem">
    Ideas from the planner, the AI agent, or you. Processed daily by Claude Code.
  </p>

  <div class="sug-form-card">
    <h2 style="font-size:.95rem;margin:0 0 .7rem;color:var(--text)">Add a suggestion</h2>
    <textarea id="sg-desc" rows="3"
      placeholder="Describe the feature, fix, or improvement…"></textarea>
    <input id="sg-hint" type="text"
      placeholder="File hint (optional — e.g. nas_server/stacker.py)">
    <div style="display:flex;align-items:center;gap:.75rem;margin-top:.55rem">
      <button id="sg-submit">Submit</button>
      <span id="sg-status"></span>
    </div>
  </div>

  {cards}
</div>
<script>
document.querySelectorAll('.sug-resolve-btn').forEach(function(btn) {{
  btn.addEventListener('click', function() {{
    var id = this.dataset.id;
    fetch('/suggestions/' + id + '/resolve', {{method:'POST'}})
      .then(function() {{ location.reload(); }});
  }});
}});
document.getElementById('sg-submit').addEventListener('click', function() {{
  var desc = document.getElementById('sg-desc').value.trim();
  var hint = document.getElementById('sg-hint').value.trim();
  var st = document.getElementById('sg-status');
  if (!desc) {{ st.style.color='#f87171'; st.textContent='Description required'; return; }}
  st.style.color='var(--text2)'; st.textContent='Saving…';
  fetch('/suggestions/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{description: desc, file_hint: hint || null}})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function() {{
    st.style.color='#3fb950'; st.textContent='Added!';
    setTimeout(function() {{ location.reload(); }}, 600);
  }})
  .catch(function() {{ st.style.color='#f87171'; st.textContent='Error'; }});
}});
</script>"""
    css = """
  .sug-form-card { background:var(--bg2); border:1px solid var(--border); border-radius:10px; padding:1rem 1.2rem; margin-bottom:1.5rem; }
  .sug-form-card textarea, .sug-form-card input[type=text] { width:100%; background:var(--bg3); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:.45rem .7rem; font-size:.88rem; font-family:inherit; box-sizing:border-box; }
  .sug-form-card textarea { resize:vertical; min-height:70px; }
  .sug-form-card input[type=text] { margin-top:.4rem; font-family:ui-monospace,monospace; font-size:.82rem; }
  .sug-form-card button { background:var(--accent); color:#fff; border:none; border-radius:7px; padding:.38rem 1.05rem; cursor:pointer; font-size:.88rem; font-weight:600; }
  .sug-form-card button:hover { opacity:.85; }
  #sg-status { font-size:.82rem; }
  .sug-card { background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:1rem; margin-bottom:.75rem; }
  .sug-resolved { opacity:.5; }
  .sug-meta { font-size:.75rem; color:var(--text2); margin-bottom:.4rem; display:flex; align-items:center; flex-wrap:wrap; gap:.3rem; }
  .sug-badge { font-size:.7rem; padding:1px 6px; border-radius:4px; font-weight:600; }
  .sug-badge-planner { background:#1f6feb22; color:#58a6ff; border:1px solid #1f6feb55; }
  .sug-badge-user    { background:#23863622; color:#3fb950; border:1px solid #23863655; }
  .sug-badge-agent   { background:#6e40c922; color:#d2a8ff; border:1px solid #6e40c955; }
  .sug-desc { font-size:.9rem; line-height:1.5; }
  .sug-resolve-btn { margin-top:.6rem; background:none; border:1px solid var(--border); color:var(--text2); border-radius:4px; padding:2px 10px; font-size:.78rem; cursor:pointer; }
  .sug-resolve-btn:hover { border-color:var(--accent); color:var(--accent); }
"""
    return _page_shell("Suggestions — SeeStar", body, css)


def calibration_page() -> str:
    """Calibration frame library — masters and sub-frames from NINA captures."""
    from nas_server.database import get_conn

    masters_cols = ["frame_type", "filter", "gain", "temp_c", "exposure_time",
                    "date", "file_path", "adu_median", "valid", "created_at", "sub_count"]
    sub_cols = ["frame_type", "filter", "gain", "temp_c", "exposure_time",
                "date", "file_path", "adu_median", "valid", "created_at"]

    with get_conn() as conn:
        masters = conn.execute("""
            SELECT frame_type, filter, gain, temp_c, exposure_time, date,
                   file_path, adu_median, valid, created_at,
                   (SELECT COUNT(*) FROM calibration_frames sub
                    WHERE sub.master_of = cf.id) AS sub_count
            FROM calibration_frames cf
            WHERE is_master = 1
            ORDER BY created_at DESC
            LIMIT 200
        """).fetchall()

        subs = conn.execute("""
            SELECT frame_type, filter, gain, temp_c, exposure_time, date,
                   file_path, adu_median, valid, created_at
            FROM calibration_frames
            WHERE is_master = 0
            ORDER BY created_at DESC
            LIMIT 500
        """).fetchall()

    def _adu_badge(adu, ftype):
        if ftype != "flat" or adu is None:
            return ""
        pct = adu / 65535 * 100
        if 20 <= pct <= 55:
            color = "#3fb950"
            label = f"{pct:.0f}% ✓"
        elif pct < 20:
            color = "#e3b341"
            label = f"{pct:.0f}% dark"
        else:
            color = "#ff7b72"
            label = f"{pct:.0f}% bright"
        return f'<span style="color:{color};font-size:.78rem;margin-left:.4rem;">{label}</span>'

    def _frame_row(row, cols, is_master=False):
        d = dict(zip(cols, row))
        ftype    = d.get("frame_type", "")
        filt     = d.get("filter") or "none"
        gain     = d.get("gain")
        temp     = d.get("temp_c")
        exptime  = d.get("exposure_time")
        date_s   = (d.get("date") or d.get("created_at") or "")[:10]
        adu      = d.get("adu_median")
        valid    = d.get("valid", 1)
        sub_cnt  = d.get("sub_count", "")
        fname    = (d.get("file_path") or "").rsplit("/", 1)[-1]

        type_color = {"dark": "#58a6ff", "flat": "#e3b341", "bias": "#d2a8ff"}.get(ftype, "#8b949e")
        valid_icon = "✅" if valid else "❌"
        master_badge = '<span style="font-size:.7rem;background:#1f6feb33;color:#58a6ff;border:1px solid #1f6feb66;border-radius:4px;padding:1px 5px;margin-left:.35rem;">master</span>' if is_master else ""
        sub_info = f' <span style="color:var(--text2);font-size:.8rem;">({sub_cnt} subs)</span>' if is_master and sub_cnt else ""
        adu_badge = _adu_badge(adu, ftype)
        gain_s = f"gain {gain}" if gain is not None else ""
        temp_s = f"{temp:.0f}°C" if temp is not None else ""
        exp_s  = f"{exptime:.0f}s" if exptime is not None else ""
        meta   = " · ".join(x for x in [gain_s, temp_s, exp_s, filt if filt != "none" else ""] if x)

        return (
            f'<tr>'
            f'<td style="color:{type_color};font-weight:600;">{ftype}{master_badge}</td>'
            f'<td>{valid_icon}{adu_badge}</td>'
            f'<td style="color:var(--text2);font-size:.85rem;">{meta}</td>'
            f'<td style="font-size:.82rem;">{date_s}{sub_info}</td>'
            f'<td style="font-size:.75rem;color:var(--text2);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{fname}">{fname}</td>'
            f'</tr>'
        )

    master_rows = "".join(_frame_row(r, masters_cols + ["sub_count"], is_master=True) for r in masters) or \
        '<tr><td colspan="5" style="color:var(--text2);text-align:center;padding:1.5rem;">No calibration masters yet</td></tr>'
    sub_rows = "".join(_frame_row(r, sub_cols) for r in subs) or \
        '<tr><td colspan="5" style="color:var(--text2);text-align:center;padding:1.5rem;">No calibration sub-frames yet</td></tr>'

    table_css = "width:100%;border-collapse:collapse;"
    th_css = "text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--border);font-size:.82rem;color:var(--text2);font-weight:500;"
    td_css = "padding:.4rem .6rem;border-bottom:1px solid #21262d;"

    body = f"""
<style>
table {{ {table_css} }}
th {{ {th_css} }}
td {{ {td_css} }}
.cal-section {{ background:var(--bg2); border:1px solid var(--border); border-radius:10px; padding:1rem 1.2rem; margin-bottom:1.5rem; }}
.cal-h2 {{ font-size:1rem; font-weight:600; margin:0 0 .8rem; color:var(--text); }}
</style>
<div style="max-width:960px;margin:0 auto;padding:1rem 1rem 3rem;">
  <h1 style="font-size:1.35rem;font-weight:700;margin-bottom:1.2rem;">🔧 Calibration Library</h1>

  <div class="cal-section">
    <div class="cal-h2">Calibration Masters ({len(masters)})</div>
    <table>
      <thead><tr>
        <th>Type</th><th>Valid</th><th>Parameters</th><th>Date</th><th>File</th>
      </tr></thead>
      <tbody>{master_rows}</tbody>
    </table>
  </div>

  <div class="cal-section">
    <div class="cal-h2">Sub-frames ({len(subs)})</div>
    <table>
      <thead><tr>
        <th>Type</th><th>Valid / ADU</th><th>Parameters</th><th>Date</th><th>File</th>
      </tr></thead>
      <tbody>{sub_rows}</tbody>
    </table>
  </div>
</div>
"""
    return _shell("Calibration Library — SeeStar", body, "")


# ── Processing Videos ─────────────────────────────────────────────────────────

def videos_page() -> str:
    """
    Gallery of compiled pipeline processing videos.
    Each card shows target, workflow, date, frame count, file size, final score.
    Click to expand an inline HTML5 video player.
    """
    import json as _json
    import urllib.parse as _uparse
    from pathlib import Path as _Path

    try:
        from nas_server.video_logger import _LIBRARY
    except Exception:
        return _shell("Videos — SeeStar", "<p>Video library unavailable.</p>", "")

    # ── Scan for compiled .mp4 files ──────────────────────────────────────────
    all_vids: list[dict] = []

    for target_dir in sorted(_LIBRARY.iterdir()):
        if not target_dir.is_dir():
            continue
        video_dir = target_dir / "_video"
        if not video_dir.exists():
            continue
        target_name = target_dir.name
        target_slug = target_name.replace(" ", "_")

        for mp4 in video_dir.glob("*.mp4"):
            try:
                size_mb = mp4.stat().st_size / 1_000_000
                if size_mb < 0.05:     # skip empty/broken files
                    continue

                # Derive session_id by stripping target slug prefix from stem
                stem = mp4.stem
                session_id = (
                    stem[len(target_slug) + 1:]
                    if stem.startswith(target_slug + "_")
                    else stem
                )

                # Read frames.json if available
                frame_count  = 0
                final_score  = None
                run_date     = ""
                workflow     = ""
                thumb_frame  = None

                session_dir = video_dir / session_id
                frames_json = session_dir / "frames.json"
                if frames_json.exists():
                    try:
                        frames = _json.loads(frames_json.read_text())
                        frame_count = len(frames)
                        for fr in reversed(frames):
                            if fr.get("score") is not None:
                                final_score = fr["score"]
                                break
                        if frames:
                            ts = frames[0].get("timestamp", "")
                            run_date = ts[:10] if ts else ""
                        # Thumbnail = last frame JPG in session dir
                        jpgs = sorted(session_dir.glob("*.jpg"))
                        if jpgs:
                            thumb_frame = jpgs[-1].name
                    except Exception:
                        pass

                # Workflow from the last part of session_id: YYYYMMDD_HHMMSS_workflow
                parts = session_id.split("_")
                if len(parts) >= 3:
                    workflow = " ".join(parts[2:]).replace("seestar ", "").replace("_", " ")

                if not run_date:
                    # Fall back to mtime
                    import datetime as _dt
                    run_date = _dt.datetime.fromtimestamp(mp4.stat().st_mtime).strftime("%Y-%m-%d")

                all_vids.append({
                    "target":      target_name,
                    "target_enc":  _uparse.quote(target_name),
                    "filename":    mp4.name,
                    "filename_enc": _uparse.quote(mp4.name),
                    "session_id":  session_id,
                    "size_mb":     size_mb,
                    "frame_count": frame_count,
                    "final_score": final_score,
                    "workflow":    workflow,
                    "run_date":    run_date,
                    "mtime":       mp4.stat().st_mtime,
                    "thumb_frame": thumb_frame,
                })
            except Exception:
                continue

    # Newest first
    all_vids.sort(key=lambda v: v["mtime"], reverse=True)

    # ── Build HTML cards ──────────────────────────────────────────────────────
    def _score_badge(sc) -> str:
        if sc is None:
            return ""
        col = "#3fb950" if sc >= 7 else ("#e3b341" if sc >= 5 else "#f85149")
        return (
            f'<span style="background:{col};color:#0d1117;border-radius:4px;'
            f'padding:1px 5px;font-size:.72rem;font-weight:700;margin-left:.4rem">'
            f'{sc:.1f}</span>'
        )

    def _wf_badge(wf: str) -> str:
        if not wf:
            return ""
        return (
            f'<span style="border:1px solid var(--border);color:var(--text2);'
            f'border-radius:3px;padding:0 5px;font-size:.72rem;white-space:nowrap">'
            f'{wf}</span>'
        )

    cards_html = []
    for i, v in enumerate(all_vids):
        thumb_src = (
            f"/video-thumb/{v['target_enc']}/{_uparse.quote(v['session_id'])}"
            if v["thumb_frame"]
            else "/static/placeholder.jpg"
        )
        cards_html.append(f"""
<div class="vc" id="vc{i}">
  <div class="vc-hdr" onclick="toggleVid({i})">
    <img class="vc-thumb" src="{thumb_src}" loading="lazy"
         onerror="this.style.background='var(--bg3)';this.removeAttribute('src')">
    <div class="vc-meta">
      <div class="vc-title">{v['target']}</div>
      <div class="vc-sub">{v['run_date']}&ensp;{_wf_badge(v['workflow'])}</div>
      <div class="vc-detail">
        {v['frame_count']} frames&ensp;·&ensp;{v['size_mb']:.1f} MB{_score_badge(v['final_score'])}
      </div>
    </div>
    <div class="vc-btns">
      <span class="vc-play" id="vpbtn{i}" title="Play / Pause">▶</span>
      <a class="vc-dl" href="/video-file/{v['target_enc']}/{v['filename_enc']}"
         download="{v['filename']}" onclick="event.stopPropagation()" title="Download">⬇</a>
    </div>
  </div>
  <div class="vc-player" id="vp{i}" style="display:none">
    <video id="vid{i}" controls preload="metadata">
      <source src="/video-file/{v['target_enc']}/{v['filename_enc']}" type="video/mp4">
    </video>
  </div>
</div>""")

    empty_html = (
        '<p style="color:var(--text2);text-align:center;padding:4rem">'
        'No compiled videos yet — queue a target to generate one.</p>'
    )

    body = f"""
<div style="max-width:900px;margin:1.5rem auto;padding:0 1rem">
  <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap">
    <h1 style="margin:0;font-size:1.35rem">🎬 Processing Videos</h1>
    <span style="color:var(--text2);font-size:.85rem">{len(all_vids)} video{"s" if len(all_vids) != 1 else ""}</span>
    <input id="vf" type="search" placeholder="Filter by target or workflow…"
           oninput="filterVids(this.value)"
           style="margin-left:auto;background:var(--bg2);border:1px solid var(--border);
                  color:var(--text);border-radius:6px;padding:.35rem .75rem;
                  font-size:.85rem;width:220px;outline:none">
  </div>
  <div id="vclist">
    {"".join(cards_html) if cards_html else empty_html}
  </div>
</div>

<script>
function toggleVid(i) {{
  var player = document.getElementById('vp' + i);
  var video  = document.getElementById('vid' + i);
  var btn    = document.getElementById('vpbtn' + i);
  var isOpen = player.style.display !== 'none';

  // Pause + close every other open player
  document.querySelectorAll('.vc-player').forEach(function(p) {{
    if (p.id !== 'vp' + i && p.style.display !== 'none') {{
      p.style.display = 'none';
      var v = p.querySelector('video');
      if (v) v.pause();
      var n = p.id.replace('vp', '');
      var b = document.getElementById('vpbtn' + n);
      if (b) b.textContent = '▶';
    }}
  }});

  if (isOpen) {{
    player.style.display = 'none';
    video.pause();
    btn.textContent = '▶';
  }} else {{
    player.style.display = 'block';
    btn.textContent = '⏸';
    video.play().catch(function() {{}});   // autoplay may be blocked; ignore
    document.getElementById('vc' + i).scrollIntoView({{behavior:'smooth', block:'nearest'}});
  }}
}}

function filterVids(q) {{
  q = q.toLowerCase();
  document.querySelectorAll('.vc').forEach(function(el) {{
    var t = (el.querySelector('.vc-title').textContent + ' ' +
             el.querySelector('.vc-sub').textContent).toLowerCase();
    el.style.display = t.includes(q) ? '' : 'none';
  }});
}}
</script>
"""

    css = """
.vc { background:var(--bg2); border:1px solid var(--border); border-radius:8px;
      margin-bottom:.85rem; overflow:hidden; transition:border-color .15s; }
.vc:has(.vc-player:not([style*='none'])) { border-color:var(--accent); }
.vc-hdr { display:flex; align-items:center; gap:.9rem; padding:.8rem 1rem;
           cursor:pointer; transition:background .12s; user-select:none; }
.vc-hdr:hover { background:var(--bg3,#161b22); }
.vc-thumb { width:120px; height:68px; object-fit:cover; object-position:center;
             border-radius:4px; flex-shrink:0; background:var(--bg3);
             border:1px solid var(--border); }
.vc-meta  { flex:1; min-width:0; }
.vc-title { font-weight:600; font-size:.95rem; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis; margin-bottom:.2rem; }
.vc-sub   { color:var(--text2); font-size:.78rem; display:flex;
             gap:.4rem; align-items:center; flex-wrap:wrap; margin-bottom:.2rem; }
.vc-detail { font-size:.76rem; color:var(--text2); }
.vc-btns  { display:flex; gap:.4rem; align-items:center; flex-shrink:0; }
.vc-play  { font-size:1.5rem; color:var(--accent); padding:.2rem .45rem;
             border-radius:50%; line-height:1; transition:background .12s; }
.vc-hdr:hover .vc-play { background:var(--bg); }
.vc-dl    { color:var(--text2); font-size:1.05rem; text-decoration:none;
             padding:.25rem .4rem; border-radius:4px; line-height:1; }
.vc-dl:hover { color:var(--text); background:var(--bg3); }
.vc-player video { display:block; width:100%; max-height:580px;
                    background:#000; border-top:1px solid var(--border); }
@media (max-width:520px) {
  .vc-thumb { width:72px; height:40px; }
  .vc-title { font-size:.88rem; }
  input#vf  { width:150px; }
}
"""

    return _shell("Processing Videos — SeeStar", body, css)

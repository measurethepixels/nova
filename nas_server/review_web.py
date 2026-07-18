"""
HTML rendering for the manual review system.

review_list_page()     — /review list of pending reviews
review_rows_partial()  — /review-view/rows HTMX partial (polls every 10s)
review_detail_page()   — /review/{id} detail (blind before deciding; unblinded after)
"""
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _fmt_expires(expires_at: str) -> str:
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        secs = max(0, int((exp - now).total_seconds()))
        m, s = divmod(secs, 60)
        return f"{m}m {s:02d}s"
    except Exception:
        return expires_at


def _metric_val(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _metrics_table(metrics: dict) -> str:
    if not metrics:
        return ""
    SHOW = [
        ("bg_sigma_ratio",   "bg_σ ratio"),
        ("fwhm_delta_pct",   "FWHM Δ%"),
        ("snr_after",        "SNR after"),
        ("ssim",             "SSIM"),
        ("ringing_score",    "ringing"),
        ("entropy_after",    "entropy"),
        ("nebulosity_leakage_score", "nebulosity leak"),
        ("gradient_severity_after",  "gradient sev."),
        ("dynamic_range_ratio",      "dyn. range ratio"),
    ]
    rows = []
    for key, label in SHOW:
        if key in metrics and metrics[key] is not None:
            val = metrics[key]
            display = f"{val:+.1f}%" if key == "fwhm_delta_pct" else _metric_val(val)
            rows.append(f"<tr><td>{label}</td><td>{display}</td></tr>")
    if not rows:
        return ""
    return (
        '<table style="font-size:.78rem;border-collapse:collapse;width:100%">'
        + "".join(rows)
        + "</table>"
    )


def _card_css() -> str:
    return """
.rv-card {
  background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:1rem;display:flex;flex-direction:column;gap:.5rem;min-width:0;
}
.rv-label {
  font-size:2rem;font-weight:700;color:#fbbf24;text-align:center;
  border-bottom:1px solid var(--border);padding-bottom:.3rem;margin-bottom:.3rem;
}
.rv-img { width:100%;border-radius:4px;display:block; }
.rv-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem; }
.rv-form { display:flex;flex-direction:column;gap:.75rem; }
.badge-agree { background:#22c55e22;color:#4ade80;border:1px solid #22c55e;padding:2px 8px;border-radius:6px;font-size:.8rem; }
.badge-disagree { background:#ef444422;color:#f87171;border:1px solid #ef4444;padding:2px 8px;border-radius:6px;font-size:.8rem; }
.badge-timeout { background:#78716c22;color:#a8a29e;border:1px solid #78716c;padding:2px 8px;border-radius:6px;font-size:.8rem; }
"""


def review_rows_partial() -> str:
    """HTMX partial — rows of pending reviews for the list page."""
    from nas_server.database import get_pending_reviews
    pending = get_pending_reviews()
    if not pending:
        return '<p style="color:var(--text2);text-align:center;padding:2rem">No pending reviews.</p>'
    rows = []
    for r in pending:
        n = len(r.get("ordered_labels") or [])
        rows.append(f"""
<div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
            padding:.9rem 1rem;display:flex;flex-wrap:wrap;gap:.5rem;align-items:center">
  <div style="flex:1;min-width:180px">
    <div style="font-weight:600">{r['target']}</div>
    <div style="font-size:.82rem;color:var(--text2)">{r['step']} &middot; {n} variants</div>
  </div>
  <div style="font-size:.82rem;color:#fbbf24;min-width:80px" id="countdown-{r['id']}">
    {_fmt_expires(r['expires_at'])}
  </div>
  <a href="/review/{r['id']}" style="background:var(--accent);color:#000;padding:.35rem .9rem;
     border-radius:6px;font-size:.85rem;font-weight:600;text-decoration:none">Review</a>
</div>""")
    return "\n".join(rows)


def review_list_page() -> str:
    from nas_server.story import _page_shell
    body = f"""
<div style="max-width:860px;margin:2rem auto;padding:0 1rem">
  <h2 style="margin-bottom:1rem">Manual Reviews</h2>
  <div id="review-rows"
       hx-get="/review-view/rows"
       hx-trigger="every 10s"
       hx-swap="innerHTML"
       style="display:flex;flex-direction:column;gap:.75rem">
    {review_rows_partial()}
  </div>
</div>"""
    return _page_shell("Reviews — SeeStar", body, _card_css())


def render_disagree_confirm(review_id: int, user_label: str, claude_label: str,
                            claude_reasoning: str, user_reasoning: str) -> str:
    """Returned by /decide when user and Claude disagree — asks user to confirm or switch."""
    return f"""
<div style="background:var(--bg2);border:1px solid #ef4444;border-radius:8px;
            padding:1.25rem;max-width:600px;margin-top:1.5rem;display:flex;
            flex-direction:column;gap:1rem">
  <div style="font-size:1rem;font-weight:600;color:#f87171">
    Claude disagrees — pick {claude_label}, not {user_label}
  </div>
  <div style="font-size:.88rem;color:var(--text2);line-height:1.5">
    {claude_reasoning or "No reasoning provided."}
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:.75rem;margin-top:.25rem">
    <form hx-post="/review/{review_id}/decide-final" hx-swap="none">
      <input type="hidden" name="winner_label"   value="{user_label}">
      <input type="hidden" name="user_reasoning" value="{user_reasoning}">
      <button type="submit"
        style="background:var(--accent);color:#000;border:none;border-radius:6px;
               padding:.45rem 1.2rem;font-weight:600;cursor:pointer">
        Keep my choice ({user_label})
      </button>
    </form>
    <form hx-post="/review/{review_id}/decide-final" hx-swap="none">
      <input type="hidden" name="winner_label"   value="{claude_label}">
      <input type="hidden" name="user_reasoning" value="{user_reasoning}">
      <button type="submit"
        style="background:var(--bg3);color:var(--text);border:1px solid var(--border);
               border-radius:6px;padding:.45rem 1.2rem;cursor:pointer">
        Switch to Claude's pick ({claude_label})
      </button>
    </form>
  </div>
</div>"""


def review_detail_page(review_id: int) -> str:
    from nas_server.story import _page_shell
    from nas_server.database import get_manual_review

    r = get_manual_review(review_id)
    if not r:
        return _page_shell("Review Not Found", "<p style='padding:2rem'>Review not found.</p>")

    status = r.get("status", "pending")
    target = r.get("target", "")
    step   = r.get("step", "")
    variants: list = r.get("variants_json") or []

    if status == "pending":
        return _render_pending(r, review_id, target, step, variants)
    else:
        return _render_decided(r, review_id, target, step, variants, status)


def _render_pending(r: dict, review_id: int, target: str, step: str, variants: list) -> str:
    from nas_server.story import _page_shell

    expires_iso = r.get("expires_at", "")
    variant_cards = []
    for v in variants:
        label  = v.get("label", "?")
        jpg    = v.get("jpeg_path", "")
        met    = v.get("metrics") or {}
        ana_fail = met.get("analytically_failed", False)
        fail_banner = (
            '<div style="background:#7f1d1d;color:#fca5a5;font-size:.75rem;'
            'padding:2px 6px;border-radius:4px;margin-bottom:.3rem">Analytics rejected</div>'
            if ana_fail else ""
        )
        img_html = ""
        if jpg:
            from pathlib import Path
            jp = Path(jpg)
            if jp.exists():
                # Serve via existing /tmp image endpoint
                img_html = (
                    f'<img class="rv-img" '
                    f'src="/review/{review_id}/variant-image/{label}" '
                    f'alt="Variant {label}" loading="lazy">'
                )
        variant_cards.append(f"""
<div class="rv-card">
  {fail_banner}
  <div class="rv-label">{label}</div>
  {img_html}
  {_metrics_table(met)}
  <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;margin-top:.5rem">
    <input type="radio" name="winner_label" value="{label}" required>
    <span style="font-weight:600">Select {label}</span>
  </label>
</div>""")

    cards_html = "\n".join(variant_cards)
    body = f"""
<div style="max-width:1100px;margin:2rem auto;padding:0 1rem">
  <div style="display:flex;flex-wrap:wrap;gap:.5rem;align-items:baseline;margin-bottom:1rem">
    <h2 style="margin:0">{target} / {step}</h2>
    <span style="color:var(--text2);font-size:.85rem">Review #{review_id}</span>
    <span id="countdown" style="color:#fbbf24;font-size:.85rem;margin-left:auto"
          data-expires="{expires_iso}">...</span>
  </div>

  <div class="rv-grid">{cards_html}</div>

  <div style="margin-top:1rem">
    <a href="/review/{review_id}/crop"
       style="display:inline-block;background:var(--bg3);color:var(--text);
              border:1px solid var(--border);border-radius:6px;padding:.4rem 1rem;
              font-size:.85rem;text-decoration:none">
      ✂ Manual crop / rotate
    </a>
    <span style="font-size:.78rem;color:var(--text2);margin-left:.6rem">
      None of these? Draw your own crop.
    </span>
  </div>

  <form class="rv-form" style="margin-top:1.5rem;max-width:600px"
        hx-post="/review/{review_id}/decide" hx-swap="outerHTML"
        hx-include="[name='winner_label']">
    <label>
      <span style="font-size:.88rem;color:var(--text2)">Your reasoning (optional)</span>
      <textarea name="user_reasoning" rows="3"
        style="width:100%;background:var(--bg3);color:var(--text);border:1px solid var(--border);
               border-radius:6px;padding:.5rem;font-size:.88rem;margin-top:.3rem;resize:vertical"
        placeholder="Why did you pick this variant?"></textarea>
    </label>
    <div style="display:flex;flex-wrap:wrap;gap:.75rem">
      <button type="submit"
        style="background:var(--accent);color:#000;padding:.45rem 1.2rem;
               border:none;border-radius:6px;font-weight:600;cursor:pointer">Submit decision</button>
    </div>
  </form>

  <div style="margin-top:1.5rem;border-top:1px solid var(--border);padding-top:1rem">
    <details>
      <summary style="cursor:pointer;color:var(--text2);font-size:.85rem">Manual edit / advanced</summary>
      <div style="margin-top:.75rem;display:flex;flex-direction:column;gap:.5rem;max-width:500px">
        <form hx-post="/review/{review_id}/manual-edit" hx-swap="none"
              style="display:flex;gap:.5rem">
          <input name="fits_path" placeholder="FITS path on server"
            style="flex:1;background:var(--bg3);color:var(--text);border:1px solid var(--border);
                   border-radius:6px;padding:.4rem .7rem;font-size:.85rem">
          <button type="submit"
            style="background:var(--bg3);color:var(--text);border:1px solid var(--border);
                   border-radius:6px;padding:.4rem .8rem;font-size:.85rem;cursor:pointer">Add edit</button>
        </form>
        <div style="display:flex;gap:.75rem;margin-top:.3rem">
          <form hx-post="/review/{review_id}/retry" hx-swap="none"
                onsubmit="return confirm('Retry this step?')">
            <button type="submit"
              style="background:#78350f;color:#fbbf24;border:1px solid #92400e;
                     border-radius:6px;padding:.4rem .9rem;font-size:.85rem;cursor:pointer">Retry step</button>
          </form>
          <form hx-post="/review/{review_id}/abort" hx-swap="none"
                onsubmit="return confirm('Abort processing for this target?')">
            <button type="submit"
              style="background:#7f1d1d;color:#fca5a5;border:1px solid #991b1b;
                     border-radius:6px;padding:.4rem .9rem;font-size:.85rem;cursor:pointer">Abort processing</button>
          </form>
        </div>
      </div>
    </details>
  </div>
</div>

<script>
(function() {{
  var exp = "{expires_iso}";
  if (!exp) return;
  function tick() {{
    var now = new Date();
    var end = new Date(exp);
    var sec = Math.max(0, Math.floor((end - now) / 1000));
    var m = Math.floor(sec / 60), s = sec % 60;
    var el = document.getElementById("countdown");
    if (el) el.textContent = "Expires in: " + m + "m " + String(s).padStart(2,"0") + "s";
    if (sec > 0) setTimeout(tick, 1000);
    else if (el) el.textContent = "Parked — waiting for your input";
  }}
  tick();
}})();
</script>"""
    return _page_shell(f"Review: {target}/{step}", body, _card_css())


def _render_decided(r: dict, review_id: int, target: str, step: str,
                     variants: list, status: str) -> str:
    from nas_server.story import _page_shell

    user_label    = r.get("user_winner_label", "—")
    claude_label  = r.get("claude_winner_label", "—")
    agreed        = r.get("agreed")
    final_variant = r.get("final_winner_variant", "—")
    user_note     = r.get("user_reasoning") or ""
    claude_note   = r.get("claude_reasoning") or ""

    if user_label == "Manual":
        status_badge = '<span class="badge-agree">Manual crop applied</span>'
    elif status == "timeout":
        status_badge = '<span class="badge-timeout">Timed out — Claude\'s choice applied</span>'
    elif agreed:
        status_badge = '<span class="badge-agree">Agreed</span>'
    else:
        status_badge = '<span class="badge-disagree">Disagreed — your choice applied</span>'

    # Compute per-variant scores and margins for unblinded display
    all_claude_scores = {
        v.get("label"): v.get("claude_score")
        for v in variants
        if v.get("claude_score") is not None
    }
    score_values = list(all_claude_scores.values())
    best_score = max(score_values) if score_values else None

    variant_cards = []
    for v in variants:
        label = v.get("label", "?")
        vid   = v.get("variant_id", "")
        met   = v.get("metrics") or {}
        is_winner = (vid == final_variant)
        border = "border:2px solid #4ade80" if is_winner else ""
        winner_tag = " — winner" if is_winner else ""

        # Score + margin annotation
        score_html = ""
        cs = v.get("claude_score")
        if cs is not None:
            score_color = "#4ade80" if is_winner else "var(--text2)"
            margin_tag = ""
            if best_score is not None and cs < best_score:
                diff = cs - best_score
                close = abs(diff) < 1.0
                margin_tag = (
                    f' <span style="color:#fbbf24;font-size:.75rem">⚡close</span>'
                    if close else
                    f' <span style="color:var(--text2);font-size:.75rem">{diff:+.1f}</span>'
                )
            score_html = (
                f'<div style="font-size:1.1rem;font-weight:600;color:{score_color};'
                f'margin:.3rem 0">Claude: {cs:.1f}/10{margin_tag}</div>'
            )

        variant_cards.append(f"""
<div class="rv-card" style="{border}">
  <div class="rv-label" style="{'color:#4ade80' if is_winner else ''}">{label}{winner_tag}</div>
  <div style="font-size:.82rem;color:var(--text2);margin-bottom:.2rem">{vid}</div>
  {score_html}
  {_metrics_table(met)}
</div>""")

    cards_html = "\n".join(variant_cards)
    body = f"""
<div style="max-width:1100px;margin:2rem auto;padding:0 1rem">
  <div style="display:flex;flex-wrap:wrap;gap:.5rem;align-items:baseline;margin-bottom:1rem">
    <h2 style="margin:0">{target} / {step}</h2>
    <span style="color:var(--text2);font-size:.85rem">Review #{review_id}</span>
    {status_badge}
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;max-width:700px;margin-bottom:1.5rem">
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:.8rem">
      <div style="font-size:.8rem;color:var(--text2);margin-bottom:.3rem">Your pick</div>
      <div style="font-size:1.4rem;font-weight:700;color:#fbbf24">{user_label}</div>
      <div style="font-size:.82rem;color:var(--text2);margin-top:.3rem">{user_note}</div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:.8rem">
      <div style="font-size:.8rem;color:var(--text2);margin-bottom:.3rem">Claude's pick</div>
      <div style="font-size:1.4rem;font-weight:700;color:#58a6ff">{claude_label}</div>
      <div style="font-size:.82rem;color:var(--text2);margin-top:.3rem">{claude_note}</div>
    </div>
  </div>

  <div class="rv-grid">{cards_html}</div>
</div>"""
    return _page_shell(f"Review result: {target}/{step}", body, _card_css())

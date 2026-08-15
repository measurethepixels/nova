"""Process-first Handbook index and detail rendering for issue #286."""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable

from nas_server.handbook_contract import HandbookArticle, ProcessFamily, process_family_ids
from nas_server.workflow_docs import (
    COMPARISON_SLIDER_CSS,
    COMPARISON_SLIDER_JS,
    comparison_slider,
)

PageShell = Callable[[str, str], str]

_EXAMPLE_KEYS = {
    ProcessFamily.COSMETIC_CORRECTION: "cosmetic",
    ProcessFamily.BACKGROUND_EXTRACTION: "bge",
    ProcessFamily.COLOR_CALIBRATION: "spcc",
    ProcessFamily.DECONVOLUTION: "decon",
    ProcessFamily.DENOISE: "nr",
    ProcessFamily.STRETCH: "stretch",
}


def _list(items: tuple[str, ...]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def _default_shell(title: str, body: str) -> str:
    from nas_server.story import _page_shell

    return _page_shell(title=title, body=body, extra_css=HANDBOOK_CSS + COMPARISON_SLIDER_CSS)


def render_handbook_index(
    articles: Iterable[HandbookArticle],
    *,
    page_shell: PageShell | None = None,
    handbook_base_url: str = "",
) -> str:
    link_base = html.escape(handbook_base_url.strip("/"), quote=True)
    link_prefix = f"{link_base}/" if link_base else ""
    # process_family_ids() returns the stable launch taxonomy in ProcessFamily's
    # declaration order, which NOVA's pipeline order (pedestal removal first,
    # stretch last) -- sort by that, not alphabetically by the enum's string
    # value, so the index reads in the sequence NOVA actually runs it.
    _pipeline_order = process_family_ids()
    values = sorted(articles, key=lambda article: _pipeline_order.index(article.process_family.value))
    cards = "".join(
        '<article class="hb-card">'
        f'<h2><a href="{link_prefix}{html.escape(article.article_id)}.html">'
        f'{html.escape(article.process_family.value.replace("_", " ").title())}</a></h2>'
        f'<p>{html.escape(article.purpose)}</p>'
        f'<span>revision {article.revision} · {len(article.tool_guidance)} tool paths</span>'
        "</article>"
        for article in values
    )
    if not cards:
        cards = '<p class="empty">Handbook articles are being sourced and reviewed.</p>'
    count = len(values)
    method_word = "method" if count == 1 else "methods"
    body = (
        '<header class="hb-head"><h1>NOVA Processing Handbook</h1>'
        f'<p>A growing process-first reference. {count} {method_word} published now, with '
        'explicit sources, equivalence, measurements, and validation history; additional '
        'chapters will appear when their evidence is ready.</p></header>'
        f'<div class="hb-grid">{cards}</div>'
    )
    return (page_shell or _default_shell)("NOVA Processing Handbook", body)


def render_handbook_article(
    article: HandbookArticle,
    *,
    page_shell: PageShell | None = None,
    image_base_url: str = "img",
    handbook_base_url: str = "",
    available_example_keys: set[str] | None = None,
) -> str:
    tool_rows = []
    for guidance in article.tool_guidance:
        sources = ", ".join(guidance.source_ids) if guidance.source_ids else "unverified"
        tool_rows.append(
            "<tr>"
            f"<th>{html.escape(guidance.tool_id)}</th>"
            f"<td>{html.escape(guidance.equivalence.value)}</td>"
            f"<td>{html.escape(guidance.expected_result)}</td>"
            f"<td>{html.escape(sources)}</td>"
            "</tr>"
        )

    procedure_parts = []
    for item in article.tool_guidance:
        controls = (
            '<dl class="hb-controls">'
            + "".join(
                f"<dt>{html.escape(name)}</dt><dd>{html.escape(value)}</dd>"
                for name, value in item.controls_and_starting_ranges
            )
            + "</dl>"
            if item.controls_and_starting_ranges
            else '<p class="empty">No fixed starting range is claimed.</p>'
        )
        procedure_parts.append(
            '<section class="hb-tool">'
            f'<h2>{html.escape(item.tool_id)} '
            f'<small>{html.escape(item.tool_version)}</small></h2>'
            f'<p><b>Host:</b> {html.escape(item.host)}</p>'
            f'<ol>{"".join(f"<li>{html.escape(step)}</li>" for step in item.instructions)}</ol>'
            f'<h3>Controls and starting ranges</h3>{controls}'
            f'<p><b>Mask behavior:</b> {html.escape(item.mask_support)}</p>'
            f'<h3>Failure modes</h3>{_list(item.failure_modes)}'
            f'<h3>Recovery</h3>{_list(item.recovery)}'
            "</section>"
        )
    procedures = "".join(procedure_parts)

    key = _EXAMPLE_KEYS.get(article.process_family)
    if key and (available_example_keys is None or key in available_example_keys):
        evidence = comparison_slider(
            key,
            f"{article.process_family.value.replace('_', ' ')} before and after",
            base_url=image_base_url,
        )
    else:
        evidence = (
            '<table class="hb-diagnostics"><thead><tr><th>Measurements</th>'
            '<th>Acceptance criteria</th></tr></thead><tbody><tr>'
            f'<td>{_list(article.measurements)}</td>'
            f'<td>{_list(article.acceptance_criteria)}</td></tr></tbody></table>'
        )

    source_rows = []
    for source in article.sources:
        if source.locator.startswith(("https://", "http://")):
            title_html = (
                f'<a href="{html.escape(source.locator, quote=True)}">'
                f"{html.escape(source.title)}</a>"
            )
        else:
            title_html = (
                f"{html.escape(source.title)} "
                f"<code>{html.escape(source.locator)}</code>"
            )
        source_rows.append(
            f"<li>{title_html} — {html.escape(source.provenance.value)}</li>"
        )
    title = article.process_family.value.replace("_", " ").title()
    if article.validation_event_ids:
        validation = (
            '<strong class="hb-validation hb-validated">Jeff-validated evidence:</strong>'
            + _list(article.validation_event_ids)
        )
    else:
        validation = (
            '<strong class="hb-validation">Not yet Jeff-validated.</strong> '
            "Use this as sourced guidance, not a certification."
        )
    link_base = html.escape(handbook_base_url.strip("/"), quote=True)
    index_url = f"{link_base}/index.html" if link_base else "index.html"
    body = f"""
<p><a href="{index_url}">← Handbook</a></p>
<header class="hb-head"><h1>{html.escape(title)}</h1><p>{html.escape(article.purpose)}</p>
<span>schema {article.schema_version} · revision {article.revision}</span></header>
<section><h2>Recognize it</h2>{_list(article.observable_symptoms)}</section>
<section><h2>Intended output</h2><p>{html.escape(article.intended_output)}</p></section>
<section><h2>Required input state</h2>{_list(article.required_input_state)}</section>
<section><h2>What NOVA does</h2><p>{html.escape(article.nova_action)}</p></section>
<section class="hb-two"><div><h2>Use when</h2>{_list(article.use_when)}</div>
<div><h2>Skip when</h2>{_list(article.skip_when)}</div></section>
<section class="hb-two"><div><h2>Limits</h2>{_list(article.limits)}</div>
<div><h2>Scientific and aesthetic notes</h2>{_list(article.scientific_and_aesthetic_notes)}</div></section>
<section><h2>Evidence and measurements</h2>{evidence}</section>
<section><h2>Cross-tool matrix</h2><table class="hb-matrix"><thead><tr><th>Tool</th>
<th>Equivalence</th><th>Expected result</th><th>Evidence</th></tr></thead>
<tbody>{''.join(tool_rows)}</tbody></table></section>
{procedures}
<section><h2>Validation status</h2><p>{validation}</p></section>
<section><h2>Sources</h2><ul>{''.join(source_rows)}</ul></section>
<section><h2>Related evidence</h2>
<p>The M66 recipe and Jeff-validation ledger will link here when their static
exports are available. Until then, this article remains explicitly unvalidated.</p></section>
<script>{COMPARISON_SLIDER_JS}</script>
"""
    return (page_shell or _default_shell)(f"{title} — NOVA Handbook", body)


HANDBOOK_CSS = """
.hb-head{max-width:850px;margin:0 auto 1.5rem}.hb-head p{color:var(--text2,var(--mute))}
.hb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
.hb-card,.hb-tool{border:1px solid var(--border);border-radius:10px;padding:1rem;background:var(--bg2,var(--card))}
.hb-card h2,.hb-tool h2{margin-top:0}.hb-card span,.hb-head span,.hb-tool small{color:var(--text2,var(--mute))}
.hb-two{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.hb-matrix,.hb-diagnostics{width:100%;border-collapse:collapse}
.hb-matrix th,.hb-matrix td,.hb-diagnostics th,.hb-diagnostics td{padding:.55rem;border:1px solid var(--border);vertical-align:top;text-align:left}
.hb-tool{margin:1rem 0}.hb-tool ol,.hb-tool ul,section ul{padding-left:1.3rem}
.hb-controls{display:grid;grid-template-columns:minmax(10rem,1fr) 2fr;gap:.35rem 1rem}
.hb-controls dt{font-weight:700}.hb-controls dd{margin:0}.empty{color:var(--text2,var(--mute))}
.hb-validation{color:var(--gold,#d2a528)}.hb-validated{color:#43c97b}
@media(max-width:720px){.hb-two{grid-template-columns:1fr}.hb-matrix{font-size:.8rem}}
"""

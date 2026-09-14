"""Process-first Handbook index and detail rendering for issue #286."""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable

from nas_server.handbook_contract import (
    ConceptArticle,
    HandbookArticle,
    ProcessFamily,
    process_family_ids,
)
from nas_server.workflow_docs import (
    COMPARISON_SLIDER_CSS,
    COMPARISON_SLIDER_JS,
    TAB_CSS,
    TAB_JS,
    comparison_slider,
)

_TOOL_LABELS = {
    "nova": "NOVA", "pixinsight": "PixInsight", "siril": "Siril", "saspro": "SASpro",
}

_PROCESS_LABELS = {
    ProcessFamily.SUBFRAME_INSPECTION: "Subframe Inspection, Scoring, and Culling",
    ProcessFamily.REGISTRATION_ALIGNMENT: "Registration, Alignment, Coverage, and Mosaics",
    ProcessFamily.STACKING_INTEGRATION: "Stacking, Integration, Weighting, and Rejection",
    ProcessFamily.CROP_FRAMING: "Crop and Framing",
    ProcessFamily.DECONVOLUTION: "Deconvolution and Linear Sharpening",
    ProcessFamily.DENOISE: "Linear Denoise",
    ProcessFamily.LINEAR_STAR_SPLIT: "Linear Star Split, Star-Layer Stretch, and Recombination",
    ProcessFamily.STARLESS_FINISHING: "Nonlinear Star Removal and Starless Finishing",
    ProcessFamily.LOCAL_CONTRAST: "Local Contrast Enhancement",
    ProcessFamily.HDR_COMPRESSION: "HDR and Dynamic-Range Compression",
    ProcessFamily.HDR_CORE_BLEND: "Bright-Core Selective HDR",
    ProcessFamily.POST_STRETCH_DENOISE: "Post-Stretch Denoise",
    ProcessFamily.DARK_STRUCTURE_ENHANCEMENT: "Dark-Structure Enhancement",
    ProcessFamily.HALO_SUPPRESSION: "Bright-Star Halo Suppression",
    ProcessFamily.NARROWBAND_DUAL_BAND_STRATEGY: "Narrowband and Dual-Band Color Strategy",
}


def _process_label(family: ProcessFamily) -> str:
    return _PROCESS_LABELS.get(family, family.value.replace("_", " ").title())

# Groups the flat process taxonomy into the lifecycle phases NOVA's own
# "what NOVA can prove" diagram uses, so the index reads as phase tiles
# (each internally still pipeline-ordered) instead of one long flat list.
# Only the phases with real Handbook process-method content are tiled here;
# Measure/Record/Review are separate site sections (scoring, /validation/,
# manual review) with no ProcessFamily articles of their own yet.
_PHASES = (
    ("ingest", "Ingest", "Calibrate and inspect incoming subframes before they're trusted to stack."),
    ("stack", "Stack", "Align frames to a common frame and integrate them into one image."),
    ("process", "Process", "Refine the stacked image: color, structure, noise, and stretch."),
)
_PHASE_BY_FAMILY = {
    ProcessFamily.PEDESTAL_REMOVAL: "ingest",
    ProcessFamily.SUBFRAME_INSPECTION: "ingest",
    ProcessFamily.REGISTRATION_ALIGNMENT: "stack",
    ProcessFamily.STACKING_INTEGRATION: "stack",
    # Crop operates on the already-stacked result (P08/#539), so it belongs with
    # the post-stack "process" families even though it ships last among Group B's
    # pre-stack families in pipeline order. Named explicitly rather than left to
    # the "process" fallback -- this is the exact reconciliation #601 flagged as
    # needed whenever Group B added a new ProcessFamily.
    ProcessFamily.CROP_FRAMING: "process",
    ProcessFamily.COSMETIC_CORRECTION: "process",
    ProcessFamily.BACKGROUND_EXTRACTION: "process",
    ProcessFamily.COLOR_CALIBRATION: "process",
    ProcessFamily.DECONVOLUTION: "process",
    ProcessFamily.DENOISE: "process",
    ProcessFamily.STAR_CORRECTION: "process",
    ProcessFamily.LINEAR_STAR_SPLIT: "process",
    ProcessFamily.STRETCH: "process",
    ProcessFamily.STARLESS_FINISHING: "process",
}


def _phase_id(family: ProcessFamily) -> str:
    return _PHASE_BY_FAMILY.get(family, "process")


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
    concepts: Iterable[ConceptArticle] = (),
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

    def _card(article: HandbookArticle) -> str:
        return (
            '<article class="hb-card">'
            f'<h2><a href="{link_prefix}{html.escape(article.article_id)}.html">'
            f'{html.escape(_process_label(article.process_family))}</a></h2>'
            f'<p>{html.escape(article.purpose)}</p>'
            f'<span>revision {article.revision} · {len(article.tool_guidance)} tool paths</span>'
            "</article>"
        )

    by_phase: dict[str, list[HandbookArticle]] = {phase_id: [] for phase_id, _, _ in _PHASES}
    for article in values:
        by_phase.setdefault(_phase_id(article.process_family), []).append(article)

    phase_tiles = "".join(
        '<section class="hb-phase">'
        f'<div class="hb-phase-head"><span class="hb-phase-num">{position}</span>'
        f'<h2>{html.escape(label)}</h2></div>'
        f'<p>{html.escape(description)}</p>'
        f'<div class="hb-grid">{"".join(_card(a) for a in by_phase.get(phase_id, ()))}</div>'
        '</section>'
        for position, (phase_id, label, description) in enumerate(_PHASES, start=1)
        if by_phase.get(phase_id)
    )
    if not phase_tiles:
        phase_tiles = '<p class="empty">Handbook articles are being sourced and reviewed.</p>'
    count = len(values)
    method_word = "method" if count == 1 else "methods"
    concept_values = sorted(concepts, key=lambda concept: concept.title.casefold())
    foundation_cards = "".join(
        '<article class="hb-card hb-foundation-card">'
        f'<h2><a href="{link_prefix}{html.escape(concept.concept_id)}.html">'
        f'{html.escape(concept.title)}</a></h2>'
        f'<p>{html.escape(concept.summary)}</p>'
        f'<span>revision {concept.revision} · {len(concept.claims)} claims</span>'
        '</article>'
        for concept in concept_values
    )
    foundations = (
        '<section class="hb-foundations"><h2>Foundations</h2>'
        '<p>Methodology, measurement, evidence, and shared vocabulary.</p>'
        f'<div class="hb-grid">{foundation_cards}</div></section>'
        if foundation_cards else ""
    )
    body = (
        '<header class="hb-head"><h1>NOVA Processing Handbook</h1>'
        f'<p>A growing process-first reference. {count} {method_word} published now, with '
        'explicit sources, equivalence, measurements, and validation history; additional '
        'chapters will appear when their evidence is ready.</p></header>'
        f'{foundations}<section><h2>Processing methods</h2>'
        f'<div class="hb-phases">{phase_tiles}</div></section>'
    )
    return (page_shell or _default_shell)("NOVA Processing Handbook", body)


def render_concept_article(
    article: ConceptArticle,
    *,
    page_shell: PageShell | None = None,
    handbook_base_url: str = "",
) -> str:
    """Render a methodology concept without pretending it is a process family."""

    link_base = html.escape(handbook_base_url.strip("/"), quote=True)
    index_url = f"{link_base}/index.html" if link_base else "index.html"
    sections = "".join(
        f'<section id="{html.escape(section.section_id, quote=True)}">'
        f'<h2>{html.escape(section.title)}</h2>'
        + "".join(f'<p>{html.escape(paragraph)}</p>' for paragraph in section.body)
        + "</section>"
        for section in article.sections
    )
    claims = "".join(
        '<li class="hb-claim">'
        f'<p>{html.escape(claim.text)}</p><span>{html.escape(claim.origin.value)}'
        + (" · human-validated" if claim.human_validated else "")
        + (" · recommended" if claim.recommended else "")
        + (f' · {html.escape(claim.applicability_bound)}' if claim.applicability_bound else "")
        + f' · {html.escape(claim.contradiction_status)}</span></li>'
        for claim in article.claims
    )
    body = (
        f'<p><a href="{index_url}">← Handbook</a></p>'
        f'<header class="hb-head"><h1>{html.escape(article.title)}</h1>'
        f'<p>{html.escape(article.subtitle)}</p><span>schema {article.schema_version} · '
        f'revision {article.revision}</span></header><p>{html.escape(article.summary)}</p>'
        f'{sections}<section><h2>Auditable claims</h2><ol>{claims}</ol></section>'
    )
    return (page_shell or _default_shell)(f"{article.title} — NOVA Handbook", body)


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

    # Group tool_guidance by tool_id so a tool with several variants (e.g.
    # SASpro's three ADBE presets, or PixInsight's GraXpert/GradientCorrection/
    # DynamicBackgroundExtraction paths) gets one tab holding every variant,
    # instead of one long unbroken scroll through every tool and variant.
    by_tool: dict[str, list] = {}
    for item in article.tool_guidance:
        by_tool.setdefault(item.tool_id, []).append(item)

    tabs, panes = [], []
    for i, (tool_id, items) in enumerate(by_tool.items()):
        pane_id = f"hbtool-{article.article_id}-{tool_id}"
        label = _TOOL_LABELS.get(tool_id, tool_id.title())
        if len(items) > 1:
            label += f" ({len(items)})"
        active = " active" if i == 0 else ""
        tabs.append(f'<button class="ttab{active}" data-t="{pane_id}">{html.escape(label)}</button>')

        variant_parts = []
        for item in items:
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
            variant_parts.append(
                '<div class="hb-variant">'
                f'<h3>{html.escape(item.host)} <small>{html.escape(item.tool_version)}</small></h3>'
                f'<ol>{"".join(f"<li>{html.escape(step)}</li>" for step in item.instructions)}</ol>'
                f'<h4>Controls and starting ranges</h4>{controls}'
                f'<p><b>Mask behavior:</b> {html.escape(item.mask_support)}</p>'
                f'<h4>Failure modes</h4>{_list(item.failure_modes)}'
                f'<h4>Recovery</h4>{_list(item.recovery)}'
                '</div>'
            )
        panes.append(f'<div class="tpane{active}" id="{pane_id}">{"".join(variant_parts)}</div>')

    procedures = (
        '<div class="tools"><div class="ttabs">' + "".join(tabs) + "</div>"
        + "".join(panes) + "</div>"
    )

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
    title = _process_label(article.process_family)
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
<script>{TAB_JS}{COMPARISON_SLIDER_JS}</script>
"""
    return (page_shell or _default_shell)(f"{title} — NOVA Handbook", body)


HANDBOOK_CSS = """
.hb-head{max-width:850px;margin:0 auto 1.5rem}.hb-head p{color:var(--text2,var(--mute))}
.hb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
.hb-card{border:1px solid var(--border);border-radius:10px;padding:1rem;background:var(--bg2,var(--card))}
.hb-card h2{margin-top:0}.hb-card span,.hb-head span{color:var(--text2,var(--mute))}
.hb-two{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.hb-matrix,.hb-diagnostics{width:100%;border-collapse:collapse}
.hb-matrix th,.hb-matrix td,.hb-diagnostics th,.hb-diagnostics td{padding:.55rem;border:1px solid var(--border);vertical-align:top;text-align:left}
.hb-variant{margin:0 0 1.1rem}.hb-variant:last-child{margin-bottom:0}
.hb-variant+.hb-variant{padding-top:1.1rem;border-top:1px dashed var(--border)}
.hb-variant h3{margin:0 0 .5rem}.hb-variant h3 small{color:var(--text2,var(--mute));font-weight:400}
.hb-variant h4{margin:.8rem 0 .3rem;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;opacity:.85}
.hb-variant ol,.hb-variant ul,section ul{padding-left:1.3rem}
.hb-controls{display:grid;grid-template-columns:minmax(10rem,1fr) 2fr;gap:.35rem 1rem}
.hb-controls dt{font-weight:700}.hb-controls dd{margin:0}.empty{color:var(--text2,var(--mute))}
.hb-validation{color:var(--gold,#d2a528)}.hb-validated{color:#43c97b}
.hb-foundations{margin-bottom:2rem;padding-bottom:1rem;border-bottom:1px solid var(--border)}
.hb-foundation-card{border-color:var(--gold,#d2a528)}.hb-claim span{color:var(--text2,var(--mute));font-size:.85rem}
.hb-phases{display:flex;flex-direction:column;gap:1.75rem}
.hb-phase{border:1px solid var(--border);border-radius:12px;padding:1.25rem;background:var(--bg1,transparent)}
.hb-phase>p{margin:0 0 1rem;color:var(--text2,var(--mute))}
.hb-phase-head{display:flex;align-items:center;gap:.6rem;margin-bottom:.15rem}
.hb-phase-head h2{margin:0}
.hb-phase-num{display:inline-flex;align-items:center;justify-content:center;width:1.8rem;height:1.8rem;
  border-radius:50%;background:var(--gold,#d2a528);color:#111;font-weight:700;flex:none}
@media(max-width:720px){.hb-two{grid-template-columns:1fr}.hb-matrix{font-size:.8rem}}
""" + TAB_CSS

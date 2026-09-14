"""Validation ledger index and methodology rendering for issue #287."""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable

from nas_server.handbook_contract import ValidationEvent, ValidationLevel
from nas_server.validation_ledger import ValidationStamp, stamps_by_claim

PageShell = Callable[[str, str], str]

_STATUS_LABELS = {
    "validated": "Jeff-validated",
    "partial": "Partially validated",
    "stale": "Stale — needs re-validation",
    "failed": "Failed validation",
    "unvalidated": "Not yet validated",
}

_LEVEL_DESCRIPTIONS = {
    ValidationLevel.PROCEDURE: "The described steps were actually followed in the tool.",
    ValidationLevel.PARAMETERS: "The specific settings/values used were recorded, not just the operation.",
    ValidationLevel.STATE: "Measured before/after image state (statistics) confirmed the change.",
    ValidationLevel.ARTIFACT: "A retained output file was inspected, not just an on-screen preview.",
    ValidationLevel.BEHAVIOR: "The result's behavior (e.g. non-regression) was checked against expectations.",
    ValidationLevel.EQUIVALENCE: "The result was compared against a reference for how closely it matches.",
    ValidationLevel.ACCEPTANCE: "The result was judged against an explicit acceptance criterion.",
}


def _display_validator(name: str) -> str:
    return "Jeff" if name == "Henry" else name


def _default_shell(title: str, body: str) -> str:
    from nas_server.story import _page_shell

    return _page_shell(title=title, body=body, extra_css=VALIDATION_CSS)


def _status_badge(status: str) -> str:
    label = _STATUS_LABELS.get(status, status)
    return f'<span class="val-badge val-{html.escape(status)}">{html.escape(label)}</span>'


def _claim_row(stamp: ValidationStamp) -> str:
    event = stamp.latest_event
    if event is None:
        return (
            "<tr>"
            f"<th>{html.escape(stamp.claim_id)}</th>"
            f"<td>{_status_badge(stamp.status)}</td>"
            "<td>—</td><td>—</td><td>—</td><td>—</td>"
            "</tr>"
        )
    if event.checks:
        levels = "; ".join(
            f"{check.level.value}: {check.status.value.replace('_', ' ')}"
            for check in sorted(event.checks, key=lambda item: item.level.value)
        )
        evidence = sorted({link for check in event.checks for link in check.evidence_links})
        evidence_html = ", ".join(html.escape(link) for link in evidence) or "—"
    else:
        levels = ", ".join(
            f"{level.value}: legacy result (applicability unknown)"
            for level in sorted(event.validation_levels, key=lambda item: item.value)
        )
        evidence_html = "Not recorded per dimension"
    return (
        "<tr>"
        f"<th>{html.escape(stamp.claim_id)}</th>"
        f"<td>{_status_badge(stamp.status)}</td>"
        f"<td>{html.escape(event.outcome.value)}</td>"
        f"<td>{html.escape(event.validated_on.isoformat())} · {html.escape(_display_validator(event.validator))}</td>"
        f"<td>{html.escape(levels)}</td>"
        f"<td>{evidence_html}</td>"
        "</tr>"
    )


def render_validation_index(
    events: Iterable[ValidationEvent],
    *,
    page_shell: PageShell | None = None,
    validation_base_url: str = "",
    current_claim_hashes: dict[str, str] | None = None,
) -> str:
    values = tuple(events)
    stamps = stamps_by_claim(values, current_claim_hashes=current_claim_hashes)
    rows = "".join(
        _claim_row(stamps[claim_id]) for claim_id in sorted(stamps)
    )
    if not rows:
        table = '<p class="empty">No validation events are recorded yet.</p>'
    else:
        table = (
            '<table class="val-matrix"><thead><tr><th>Claim</th><th>Status</th>'
            "<th>Latest outcome</th><th>Event date</th><th>Dimension results</th><th>Evidence</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    link_base = html.escape(validation_base_url.strip("/"), quote=True)
    methodology_url = f"{link_base}/methodology.html" if link_base else "methodology.html"
    body = (
        '<header class="val-head"><h1>NOVA Validation Ledger</h1>'
        "<p>An append-only record of what has actually been tested, by whom, against "
        "what evidence — never a plain pass/fail flag. See the "
        f'<a href="{methodology_url}">methodology</a> for what each status and level means.</p>'
        "</header>"
        f"{table}"
    )
    return (page_shell or _default_shell)("NOVA Validation Ledger", body)


def render_validation_methodology(
    *,
    page_shell: PageShell | None = None,
    validation_base_url: str = "",
) -> str:
    level_rows = "".join(
        f"<tr><th>{html.escape(level.value)}</th><td>{html.escape(description)}</td></tr>"
        for level, description in _LEVEL_DESCRIPTIONS.items()
    )
    status_rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(_STATUS_MEANING[status])}</td></tr>"
        for status, label in _STATUS_LABELS.items()
    )
    link_base = html.escape(validation_base_url.strip("/"), quote=True)
    index_url = f"{link_base}/index.html" if link_base else "index.html"
    body = f"""
<p><a href="{index_url}">← Validation ledger</a></p>
<header class="val-head"><h1>Validation methodology</h1>
<p>Every entry in the ledger is one dated, attributed event tied to a specific claim
and tool identity — never an unattributed checkmark. A claim's current status is
derived fresh from its event history each time the ledger is rendered.</p></header>
<section><h2>Validation levels</h2>
<p>A generic pass is insufficient. Each event records which of these independent
dimensions were actually checked:</p>
<table class="val-matrix">{level_rows}</table></section>
<section><h2>Status meanings</h2>
<table class="val-matrix">{status_rows}</table></section>
<section><h2>Why not a boolean</h2>
<p>A single "validated: true" flag hides how validated something is, by whom, when,
and against which version of the claim. This ledger instead keeps every event and
derives the current status from the latest one — including detecting when the
underlying claim has since changed (marked stale) rather than silently keeping an
old stamp current.</p></section>
"""
    return (page_shell or _default_shell)("Validation methodology — NOVA", body)


_STATUS_MEANING = {
    "validated": "Every validation dimension has explicit applicability, every applicable check passed with evidence, and the event matches the current claim revision.",
    "partial": "Some dimensions passed, but another was inconclusive or applicability is incomplete; this is not universal validation.",
    "stale": "The claim's text has changed since it was last validated; the old event no longer applies.",
    "failed": "The most recent event recorded a failure.",
    "unvalidated": "No applicable passing check is current, or revision/applicability evaluation could not be completed.",
}


VALIDATION_CSS = """
.val-head{max-width:850px;margin:0 auto 1.5rem}.val-head p{color:var(--text2,var(--mute))}
.val-matrix{width:100%;border-collapse:collapse;margin:1rem 0}
.val-matrix th,.val-matrix td{padding:.55rem;border:1px solid var(--border);vertical-align:top;text-align:left}
.val-badge{display:inline-block;padding:.15rem .5rem;border-radius:4px;font-size:.85em}
.val-validated{color:#43c97b}.val-partial{color:#d2a528}.val-stale{color:#d2a528}
.val-failed{color:#e05555}.val-unvalidated{color:var(--text2,var(--mute))}
.empty{color:var(--text2,var(--mute))}
@media(max-width:720px){.val-matrix{font-size:.8rem}}
"""

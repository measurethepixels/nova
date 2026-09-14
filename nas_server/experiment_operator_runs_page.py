"""Internal HTML page for experiment run/candidate/artifact evidence."""
from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

PageShell = Callable[[str, str], str]


def _shell(title: str, body: str) -> str:
    from nas_server.story import _page_shell
    return _page_shell(title, body, RUNS_CSS)


def _value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        return f"<pre>{html.escape(json.dumps(value, sort_keys=True, indent=2))}</pre>"
    return html.escape(str(value))


def _table(rows: list[dict[str, Any]], columns: tuple[str, ...], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{html.escape(empty)}</p>'
    head = "".join(f"<th>{html.escape(c.replace('_', ' ').title())}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{_value(row.get(c))}</td>" for c in columns) + "</tr>" for row in rows)
    return f"<div class=scroll><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render_run_index(runs: list[dict[str, Any]], *, page_shell: PageShell | None = None) -> str:
    if runs:
        rows = "".join(
            "<tr>" +
            f'<td><a href="/experiment-runs/{html.escape(row["experiment_run_id"], quote=True)}">{html.escape(row["experiment_run_id"])}</a></td>' +
            "".join(f"<td>{_value(row.get(key))}</td>" for key in ("process_family", "created_at", "runtime_state", "roster_count", "attempt_count", "artifact_count")) +
            "</tr>" for row in runs
        )
        rendered = "<div class=scroll><table><thead><tr><th>Run</th><th>Process family</th><th>Created</th><th>Runtime state</th><th>Roster</th><th>Attempts</th><th>Artifacts</th></tr></thead><tbody>" + rows + "</tbody></table></div>"
    else:
        rendered = '<p class="empty">No experiment runs are registered.</p>'
    body = '<header><p class=eyebrow>Internal operator evidence</p><h1>Experiment runs</h1><p>Execution, artifact, measurement, control, and legacy facts remain separate.</p></header>' + rendered
    return (page_shell or _shell)("Experiment runs — NOVA", body)


def render_run_detail(view: dict[str, Any], *, reconciliation: list[dict] | None = None,
                      page_shell: PageShell | None = None) -> str:
    run = view["run"]
    notice = "" if reconciliation is None else f'<aside><strong>Reconciliation result</strong>{_value(reconciliation)}</aside>'
    sections = [
        ("Run identity and runtime", [run], ("experiment_run_id", "process_family", "question", "intent", "runtime_state", "lease_owner", "lease_expires_at")),
        ("Timeline", view["timeline"], ("event_sequence", "event_type", "created_at", "payload")),
        ("Complete roster", view["roster"], ("declared_ordinal", "declared_variant_id", "control_role", "definition")),
        ("Candidate execution and assessment", view["attempts"], ("declared_variant_id", "attempt_ordinal", "execution_status", "artifact_status", "assessment_status", "comparison_status", "persistence_status", "error_kind", "error_message")),
        ("Effective treatment differences", view["effective_treatments"], ("declared_variant_id", "resolution_status", "no_op_semantics", "treatment")),
        ("Failures and rejections", view["failures_and_rejections"], ("declared_variant_id", "execution_status", "assessment_status", "error_kind", "error_message")),
        ("Control roles", view["control_status"], ("declared_variant_id", "control_role", "control_role_version")),
        ("Artifacts and provenance", view["artifacts"], ("run_artifact_id", "source_artifact_id", "declared_variant_id", "artifact_role", "finalization_status", "final_path", "content_digest")),
        ("Artifact provenance edges", view["artifact_edges"], ("source_artifact_id", "run_artifact_id")),
        ("Measurement observations", view["measurements"], ("run_artifact_id", "measurand", "value", "status", "units", "applicability_reason")),
        ("Measurement gate results", view["measurement_gates"], ("run_artifact_id", "gate_result", "rationale", "decision_rule_id")),
        ("Missing measurements", view["missing_measurements"], ("run_artifact_id", "declared_variant_id", "reason")),
        ("Legacy evidence labels", view["legacy_evidence"], ("experiment_run_id", "reconstruction_level", "rows")),
        ("Orphaned files", view["orphans"], ("path", "classification")),
    ]
    content = "".join(f"<section><h2>{html.escape(title)}</h2>{_table(rows, cols, 'No records.')}</section>" for title, rows, cols in sections)
    orphan_error = f'<p class=warning>Orphan scan unavailable: {html.escape(view["orphan_scan_error"])}</p>' if view["orphan_scan_error"] else ""
    body = f'<p><a href="/experiment-runs">← All experiment runs</a></p><header><p class=eyebrow>Evidence without collapse</p><h1>{html.escape(run["experiment_run_id"])}</h1></header>{notice}{orphan_error}{content}<section><h2>Recovery action</h2><p>Explicitly reconcile all expired active experiment runs using the existing evidence-preserving recovery routine.</p><form method="post" action="/experiment-runs/reconcile?return_run_id={html.escape(run["experiment_run_id"], quote=True)}"><button type=submit>Reconcile expired runs</button></form></section>'
    return (page_shell or _shell)(f'{run["experiment_run_id"]} — NOVA', body)


RUNS_CSS = """
.eyebrow{color:#58a6ff;text-transform:uppercase;letter-spacing:.12em;font-weight:700}header{margin-bottom:1.5rem}
section{margin:1.2rem 0;padding:1rem;border:1px solid var(--border);border-radius:.6rem}table{width:100%;border-collapse:collapse;font-size:.84rem}th,td{text-align:left;vertical-align:top;padding:.45rem;border-bottom:1px solid var(--border)}pre{white-space:pre-wrap;max-width:34rem;margin:0}.scroll{overflow:auto}.empty{color:var(--text2)}aside,.warning{padding:.8rem;border-left:3px solid #d29922;background:#2b2110}button{padding:.55rem .8rem;background:#1f6feb;color:white;border:0;border-radius:.4rem;cursor:pointer}
"""

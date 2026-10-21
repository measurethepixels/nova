"""Markdown reports from verified ledger history only, without maker chat."""
import json


def _text(value):
    return str(value).replace('`', '\\`').replace('\n', ' ')


def render_report(ledger):
    steps = ledger.read()
    lines = ['# Studio measurement report', '',
             'Receipt validation is evidence integrity, not image acceptance.', '']
    for step in steps:
        failures = ledger.failures(step)
        lines.extend([f'## {_text(step.candidate_id)} / {_text(step.step_id)}', '',
                      f'Operation: `{_text(step.op)}`; decision: **{step.decision}**; '
                      f'terminal: **{step.terminal_status}**; replay: `{step.replay_status}`.', '',
                      f'Contract: `{_text(step.contract_version)}`; output: `{step.output_hash}`.', '',
                      '**FAILURES**' if failures else '**Required measurements: PASS**', ''])
        lines.extend(f'- {_text(f)}' for f in failures)
        lines.append('')
        if step.selection is not None and ledger.selection_gate is not None:
            binding = ledger.selection_gate.binding(step.selection)
            lines.extend([f"Tool selection: {binding['kind']} (`{binding['reference']}`).", '',
                          '```json', json.dumps(binding, sort_keys=True, indent=2), '```', ''])
        elif not step.full_resolution:
            lines.extend(['Preview evidence; no full-resolution selection authorization.', ''])
        for phase in ('pre', 'post'):
            for name, receipt in getattr(step, phase).items():
                lines.extend([f'{phase}.{_text(name)} ({receipt.function}@{receipt.version}):',
                              '```json', json.dumps(receipt.to_dict(), sort_keys=True, indent=2), '```', ''])
    if not steps:
        lines.append('No steps recorded; no acceptance conclusion.')
    return '\n'.join(lines)

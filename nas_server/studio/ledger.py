"""Append-only JSONL candidate DAG. Paths are explicitly supplied by the caller.

One writer per ledger: this slice does not implement locks, job dispatch or
resource leases. Reader rejects truncated/malformed history; never repairs it.
Receipt verification requires the original runner key and frozen contracts.
"""
from dataclasses import dataclass, fields, replace
import json
from pathlib import Path
import re
from .measure import Receipt, canonical
from .contracts import validate_step
from .selection import SelectionExperiment  # public forensic selection record type

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


_HASH = re.compile(r'^[0-9a-f]{64}$')


def _hashes(values):
    if not isinstance(values, dict) or any(not isinstance(k, str) or not k or
            not isinstance(v, str) or not _HASH.fullmatch(v) for k, v in values.items()):
        raise ValueError('role-labelled SHA256 hashes required')


@dataclass(frozen=True)
class Step:
    step_id: str
    candidate_id: str
    parents: tuple[str, ...]
    inputs: dict[str, str]
    output_hash: str | None
    coverage: dict[str, str]
    masks: dict[str, str]
    rois: dict[str, str]
    units: str
    state: str
    op: str
    params: dict
    contract_version: str
    tool: str
    model: str
    environment: str
    expected_effect: str
    falsifying_check: str
    pre: dict[str, Receipt]
    post: dict[str, Receipt]
    decision: str
    terminal_status: str
    replay_status: str
    full_resolution: bool = True
    run_id: str | None = None
    selection: str | None = None
    selection_binding: dict | None = None

    def __post_init__(self):
        for key in ('step_id', 'candidate_id', 'units', 'op', 'contract_version', 'tool',
                    'model', 'environment', 'expected_effect', 'falsifying_check'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f'{key}: nonempty text required')
        if not isinstance(self.parents, tuple) or any(not isinstance(p, str) or not p for p in self.parents):
            raise ValueError('parents must be step IDs')
        if len(set(self.parents)) != len(self.parents) or self.step_id in self.parents:
            raise ValueError('duplicate/self parent')
        for mapping in (self.inputs, self.coverage, self.masks, self.rois):
            _hashes(mapping)
        if not self.inputs or not self.coverage:
            raise ValueError('input and coverage provenance required')
        if self.output_hash is not None and (not isinstance(self.output_hash, str) or
                                             not _HASH.fullmatch(self.output_hash)):
            raise ValueError('invalid output hash')
        if self.decision not in ('kept', 'reverted', 'failed'):
            raise ValueError('invalid decision')
        if self.terminal_status not in ('completed', 'failed', 'cancelled', 'expired'):
            raise ValueError('invalid terminal status')
        if self.decision == 'kept' and (self.terminal_status != 'completed' or self.output_hash is None):
            raise ValueError('partial/expired/failed output cannot be kept')
        if self.state not in ('linear', 'nonlinear') or self.replay_status not in (
                'cached', 'recomputed_within_tolerance', 'unsupported'):
            raise ValueError('invalid image/replay state')
        if not isinstance(self.params, dict) or not isinstance(self.pre, dict) or not isinstance(self.post, dict):
            raise ValueError('params and measurement maps required')
        if type(self.full_resolution) is not bool:
            raise ValueError('explicit full-resolution flag required')
        if self.selection is not None and (not isinstance(self.selection, str) or not _HASH.fullmatch(self.selection)):
            raise ValueError('invalid selection authorization')
        if self.selection_binding is not None:
            if not isinstance(self.selection_binding, dict) or self.selection is None:
                raise ValueError('selection binding requires authorization')
            canonical(self.selection_binding)
        canonical(self.params)  # JSON only; excludes NaN, objects and callbacks

    def to_dict(self):
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data['parents'] = list(self.parents)
        for phase in ('pre', 'post'):
            data[phase] = {k: r.to_dict() for k, r in data[phase].items()}
        return {'schema_version': 3, **data}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {'schema_version', *(f.name for f in fields(cls))}:
            raise ValueError('invalid step schema; unknown fields/waivers forbidden')
        if type(data['schema_version']) is not int or data['schema_version'] != 3:
            raise ValueError('unsupported ledger schema')
        d = dict(data); del d['schema_version']
        if not isinstance(d['parents'], list):
            raise ValueError('parents must be an array')
        d['parents'] = tuple(d['parents'])
        for phase in ('pre', 'post'):
            if not isinstance(d[phase], dict):
                raise ValueError('measurement map required')
            d[phase] = {k: Receipt.from_dict(v) for k, v in d[phase].items()}
        return cls(**d)


class Ledger:
    def __init__(self, path, *, runner, contracts, selection_gate=None):
        self.selection_gate = selection_gate
        self.path = Path(path)
        self.runner = runner
        self.contracts = dict(contracts)  # keyed by (op, version), frozen outside maker

    def failures(self, step):
        contract = self.contracts.get((step.op, step.contract_version))
        if contract is None:
            return ['unknown operation/metric-contract version']
        failures = validate_step(step, contract, self.runner)
        if step.full_resolution:
            try:
                if self.selection_gate is None:
                    raise ValueError('full-resolution step requires selection gate')
                self.selection_gate.validate_step(step)
            except (ValueError, TypeError, KeyError) as exc:
                failures.append(str(exc))
        return failures

    def _check_dag(self, step, previous):
        if step.step_id in previous:
            raise ValueError('duplicate step ID')
        for parent_id in step.parents:
            parent = previous.get(parent_id)
            if parent is None:
                raise ValueError('parent must precede child (no cycles/forward references)')
            if parent.decision != 'kept' or parent.terminal_status != 'completed':
                raise ValueError('cannot derive from reverted/failed/partial parent')
            if parent.output_hash not in step.inputs.values():
                raise ValueError('parent output missing from child input provenance')
        # Revalidate shape, including objects mutated after construction.
        Step.from_dict(step.to_dict())
        for phase in (step.pre, step.post):
            for receipt in phase.values():
                self.runner.verify(receipt)
        failures = self.failures(step)
        if step.decision == 'kept' and failures:
            raise ValueError('; '.join(failures))

    def read(self):
        previous = {}; result = []
        if not self.path.exists():
            return result
        with self.path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    if not line.endswith('\n'):
                        raise ValueError('truncated ledger line')
                    step = Step.from_dict(json.loads(line, object_pairs_hook=_unique_object))
                    if step.selection is not None:
                        if self.selection_gate is None or step.selection_binding is None:
                            raise ValueError('persisted selection binding and trusted gate required')
                        self.selection_gate.restore(step.selection, step.selection_binding)
                    self._check_dag(step, previous)
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    raise ValueError(f'ledger line {number}: {e}') from e
                previous[step.step_id] = step; result.append(step)
        return result

    def append(self, step):
        previous = {s.step_id: s for s in self.read()}
        if step.selection is not None:
            if self.selection_gate is None:
                raise ValueError('selection gate required')
            binding = self.selection_gate.binding(step.selection)
            if step.selection_binding is not None and step.selection_binding != binding:
                raise ValueError('selection binding changed')
            step = replace(step, selection_binding=binding)
        self._check_dag(step, previous)
        encoded = canonical(step.to_dict()).decode() + '\n'
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(encoded)
            stream.flush()
        return step

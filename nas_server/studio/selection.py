"""Offline tool-selection evidence and trusted preflight (schema/mapping v1).

The caller freezes inventory, scientific acceptance, Henry evidence and source
ledgers independently of the maker. Hashes detect mutation, not authorship.
Direct engine calls and dishonest trusted callers are outside this boundary.
No engine discovery, licence acquisition, engine execution or default changes.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def frozen(value):
    return json.loads(canonical(value))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def shape(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys.split()), 'invalid/partial selection schema')


def text(value):
    require(isinstance(value, str) and bool(value.strip()), 'nonempty selection text required')


def sha(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SHA256 required')


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'finite number required')


# Selection semantics are distinct from metric-contract Requirement semantics.
# Ambiguous denoising/NXT are resolved from the frozen image state.
MAPPING_VERSION = 'studio-selection-1'
OPERATIONS = {
    'background': 'global_sensitive', 'deconvolution': 'global_sensitive',
    'denoise_linear': 'global_sensitive', 'denoise_nonlinear': 'global_sensitive',
    'star_separation': 'global_sensitive', 'star_stretch': 'global_sensitive',
    'star_recombination': 'global_sensitive', 'stretch': 'global_sensitive',
    'colour_calibration': 'global_only', 'stack_integration': 'global_only',
    'mosaic_assembly': 'global_only', 'geometry': 'global_only', 'astrometry': 'global_only',
    'star_reduction': 'global_sensitive',
}
ALIASES = {'background-extraction': 'background', 'gradient': 'background',
           'BXT': 'deconvolution', 'SXT': 'star_separation', 'SPCC': 'colour_calibration',
           'sssc': 'colour_calibration', 'display_stretch': 'stretch', 'HDRMT': 'stretch',
           'LHE': 'stretch', 'crop': 'geometry', 'solve': 'astrometry', 'stack': 'stack_integration'}


def operation(op, state):
    if op in ('denoising', 'denoise', 'NXT'):
        op = 'denoise_' + state
    op = ALIASES.get(op, op)
    require(op in OPERATIONS, 'unknown selection-controlled operation')
    return op


def identity(value):
    shape(value, 'tool build model model_hash backend contract_version params')
    for key in ('tool', 'build', 'model', 'backend', 'contract_version'):
        text(value[key])
    if value['model'] == 'not-applicable':
        require(value['model_hash'] is None, 'non-ML model hash must be null')
    else:
        sha(value['model_hash'])
    require(isinstance(value['params'], dict), 'effective parameter map required')
    canonical(value)


def population(value):
    shape(value, 'data_kind filter object_class image_state sampling noise_regime')
    for key in ('data_kind', 'filter', 'object_class', 'image_state', 'sampling'):
        text(value[key])
    require(value['image_state'] in ('linear', 'nonlinear'), 'invalid image state')
    require(value['sampling'] in ('native', 'drizzle'), 'invalid sampling')
    # A regime is an exact tested category, never an interpolated numeric range.
    text(value['noise_regime'])


def geometry(value):
    shape(value, 'inputs coverage masks rois')
    for key, hashes in value.items():
        require(isinstance(hashes, dict), 'role-labelled geometry hashes required')
        for role, value_hash in hashes.items():
            text(role); sha(value_hash)
    require(value['inputs'].get('image') and value['coverage'], 'image and coverage required')


def request(value):
    shape(value, 'run_id step_id target_id op state channels population geometry file_sha256 identity objective mapping_version')
    for key in ('run_id', 'step_id', 'target_id', 'op', 'objective'):
        text(value[key])
    require(value['mapping_version'] == MAPPING_VERSION, 'unknown operation mapping version')
    require(value['state'] in ('linear', 'nonlinear'), 'invalid image state')
    require(value['channels'] in ('mono', 'RGB', 'CFA'), 'invalid channels')
    population(value['population']); geometry(value['geometry']); sha(value['file_sha256'])
    require(value['population']['image_state'] == value['state'], 'population state mismatch')
    identity(value['identity'])
    return operation(value['op'], value['state'])


def eligible_inventory(value, req):
    shape(value, 'run_id op mapping_version population candidates budget_seconds megapixels control control_disposition')
    require(value['run_id'] == req['run_id'] and value['op'] == request(req), 'inventory run/operation mismatch')
    require(value['mapping_version'] == MAPPING_VERSION and value['population'] == req['population'], 'inventory applicability mismatch')
    number(value['budget_seconds']); number(value['megapixels'])
    require(value['budget_seconds'] > 0 and value['megapixels'] > 0, 'positive resource budget required')
    require(isinstance(value['candidates'], list) and value['candidates'], 'complete candidate inventory required')
    result = {}; all_ids = set(); incomplete = False
    for candidate in value['candidates']:
        shape(candidate, 'identity capability states channels data_kinds available licensed seconds_per_mpx cost_basis exclusion')
        from dataclasses import fields
        from .adapters.core import Capability
        capability = candidate['capability']
        require(isinstance(capability, dict) and set(capability) == {f.name for f in fields(Capability)},
                'complete capability receipt required')
        require(capability['engine'] == candidate['identity']['tool'] and
                capability['backend'] == candidate['identity']['backend'], 'capability identity mismatch')
        require(any(operation(op, req['state']) == request(req) for op in capability['operations']),
                'capability operation mismatch')
        require(not capability['network'] and not capability['spend'], 'offline capability required')
        ident = candidate['identity']; identity(ident)
        key = digest(ident)
        require(key not in all_ids, 'duplicate candidate'); all_ids.add(key)
        for field in ('states', 'channels', 'data_kinds'):
            require(isinstance(candidate[field], list) and candidate[field], 'compatibility declarations required')
            for item in candidate[field]:
                text(item)
        require(all(v is None or type(v) is bool for v in (candidate['available'], candidate['licensed'])), 'invalid availability/licence')
        reason = None
        if req['state'] not in candidate['states']: reason = 'state_mismatch'
        elif req['channels'] not in candidate['channels']: reason = 'channels_mismatch'
        elif req['population']['data_kind'] not in candidate['data_kinds']: reason = 'data_kind_mismatch'
        elif candidate['licensed'] is not True: reason = 'unlicensed'
        elif candidate['available'] is not True: reason = 'unavailable'
        elif candidate['seconds_per_mpx'] is None: reason = 'unknown_cost'
        else:
            number(candidate['seconds_per_mpx']); text(candidate['cost_basis'])
            require(candidate['seconds_per_mpx'] >= 0, 'negative cost')
            if candidate['seconds_per_mpx'] * value['megapixels'] > value['budget_seconds']: reason = 'over_budget'
        declared = candidate['exclusion']
        if reason:
            require(declared == reason, 'exclusion must record assessed reason')
            if reason not in ('state_mismatch', 'channels_mismatch', 'data_kind_mismatch'): incomplete = True
        elif declared is not None:
            require(isinstance(declared, str) and declared.startswith('implausible:') and len(declared) > 12, 'invalid exclusion')
            incomplete = True
        else:
            result[key] = ident
    require(result, 'zero eligible candidates')
    if value['control'] not in result:
        text(value['control_disposition'])
        incomplete = True
    else:
        require(value['control_disposition'] is None, 'unexpected control disposition')
    return result, incomplete


@dataclass(frozen=True)
class SelectionExperiment:
    """Immutable forensic record; JSON snapshot prevents nested-map mutation."""
    payload: bytes

    @classmethod
    def create(cls, data):
        obj = cls(canonical(data))
        validate_experiment(obj.to_dict())
        return obj

    def to_dict(self):
        return json.loads(self.payload)

    def write(self, path):
        validate_experiment(self.to_dict())
        Path(path).write_bytes(self.payload + b'\n')

    @classmethod
    def read(cls, path):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'duplicate experiment field')
                result[key] = value
            return result
        return cls.create(json.loads(Path(path).read_text(), object_pairs_hook=unique))

    @property
    def record_id(self):
        return digest(self.to_dict())


def validate_experiment(value):
    shape(value, 'schema_version request inventory preview region_rationale comparison_type frozen_settings control metrics acceptable dominated critic judgement_required sealed_key_hash scale_validity whole_frame tuning acceptance evidence')
    require(type(value['schema_version']) is int and value['schema_version'] == 1, 'unknown selection record schema')
    req = value['request']; op = request(req)
    candidates, incomplete = eligible_inventory(value['inventory'], req)
    geometry(value['preview']); text(value['region_rationale']); text(value['evidence'])
    require(value['comparison_type'] in ('defaults', 'matched_effort'), 'tuned versus untuned is not selection evidence')
    require(value['frozen_settings'] is True, 'Stage A settings must be frozen')
    require(value['control'] == value['inventory']['control'], 'pipeline control mismatch')
    require(isinstance(value['metrics'], dict) and set(value['metrics']) == set(candidates), 'metrics required for every eligible candidate')
    passed = set()
    for key, metrics in value['metrics'].items():
        require(isinstance(metrics, list) and metrics, 'candidate metrics required')
        families = set(); names = set(); good = True
        for metric in metrics:
            shape(metric, 'name family value units direction minimum maximum catches')
            text(metric['name']); text(metric['units']); text(metric['catches'])
            require(metric['name'] not in names, 'duplicate metric'); names.add(metric['name'])
            require(metric['family'] in ('intended_effect', 'preservation'), 'unknown metric family')
            families.add(metric['family']); number(metric['value'])
            require(metric['direction'] in ('minimize', 'maximize'), 'metric direction required')
            require(metric['minimum'] is not None or metric['maximum'] is not None, 'predeclared limits required')
            for bound in ('minimum', 'maximum'):
                if metric[bound] is not None: number(metric[bound])
            lo, hi = metric['minimum'], metric['maximum']
            require(lo is None or hi is None or lo <= hi, 'inverted metric limits')
            good &= (lo is None or metric['value'] >= lo) and (hi is None or metric['value'] <= hi)
        require(families == {'intended_effect', 'preservation'}, 'both metric families required')
        if good: passed.add(key)
    # Comparable, predeclared measurements: candidate-specific thresholds would
    # allow the maker to move the goalposts. Names/units/directions/limits agree.
    templates = []
    for metrics in value['metrics'].values():
        templates.append({m['name']: {k: v for k, v in m.items() if k != 'value'} for m in metrics})
    require(all(t == templates[0] for t in templates), 'candidate metric contracts differ')
    accepted = value['acceptable']
    require(isinstance(accepted, dict) and accepted and set(accepted) <= passed, 'acceptable candidates must pass hard limits')
    for key in accepted:
        mine = {m['name']: m for m in value['metrics'][key]}
        for rival in passed - {key}:
            theirs = {m['name']: m for m in value['metrics'][rival]}
            differences = [(theirs[n]['value'] - m['value']) * (1 if m['direction'] == 'maximize' else -1)
                           for n, m in mine.items()]
            require(not (all(d >= 0 for d in differences) and any(d > 0 for d in differences)),
                    'acceptable candidate is Pareto dominated')
    for objectives in accepted.values():
        require(isinstance(objectives, list) and objectives, 'objective tags required')
        for objective in objectives: text(objective)
    require(isinstance(value['dominated'], dict) and set(value['dominated']) == set(candidates) - set(accepted), 'losing candidates need dispositions')
    for reason in value['dominated'].values(): text(reason)
    require(type(value['judgement_required']) is bool, 'judgement declaration required')
    if value['judgement_required']:
        require(value['critic'] == 'accepted', 'blind critic acceptance required')
        sha(value['sealed_key_hash'])
    elif value['critic'] is not None:
        require(value['critic'] == 'accepted', 'critic rejected selection'); sha(value['sealed_key_hash'])
    else:
        require(value['sealed_key_hash'] is None, 'unexpected sealed key')
    require(value['acceptance'] == 'accepted', 'measurements alone do not prove acceptance')
    scale = value['scale_validity']
    require(scale in ('local_valid', 'global_sensitive', 'global_only'), 'unknown scale validity')
    # Algorithm-specific weakening of conservative defaults is not inferred.
    require(scale == OPERATIONS[op], 'scale validity differs from versioned operation contract')
    if scale == 'global_only':
        require(value['preview'] == req['geometry'], 'global-only experiment requires whole frame')
    if scale in ('global_sensitive', 'global_only'):
        frame = value['whole_frame']
        shape(frame, 'geometry identities evidence accepted')
        require(frame['geometry'] == req['geometry'] and frame['accepted'] is True, 'whole-frame acceptance geometry mismatch')
        require(set(frame['identities']) == set(accepted), 'whole-frame acceptance candidate/settings mismatch')
        for key, ident in frame['identities'].items():
            require(ident == candidates[key], 'whole-frame identity mismatch')
        text(frame['evidence'])
    else:
        require(value['whole_frame'] is None, 'unexpected whole-frame record')
    # Tuning is separate Stage B evidence, never silently folded into selection.
    require(isinstance(value['tuning'], dict) and set(value['tuning']) <= set(accepted), 'invalid Stage B candidate')
    for key, tuning in value['tuning'].items():
        shape(tuning, 'bounds budget_seconds identity whole_frame evidence accepted')
        identity(tuning['identity']); text(tuning['evidence']); number(tuning['budget_seconds'])
        require(tuning['budget_seconds'] > 0 and tuning['accepted'] is True, 'bounded tuning acceptance required')
        base = dict(candidates[key]); tuned = dict(tuning['identity'])
        base_params, tuned_params = base.pop('params'), tuned.pop('params')
        require(base == tuned and set(base_params) == set(tuned_params), 'Stage B changed execution identity')
        require(isinstance(tuning['bounds'], dict) and set(tuning['bounds']) == {k for k in base_params if base_params[k] != tuned_params[k]}, 'tuning bounds must name changed settings')
        for name, bounds in tuning['bounds'].items():
            require(isinstance(bounds, list) and len(bounds) == 2, 'tuning range required')
            for v in (*bounds, base_params[name], tuned_params[name]): number(v)
            require(bounds[0] <= base_params[name] <= bounds[1] and bounds[0] <= tuned_params[name] <= bounds[1], 'tuning outside envelope')
        require(tuning['whole_frame'] == digest(req['geometry']), 'tuned whole-frame evidence mismatch')
    return candidates, incomplete


def _matches(record, req):
    base = record['request']
    require(request(base) == request(req) and base['population'] == req['population'], 'selection applicability mismatch')
    chosen = digest(req['identity'])
    if chosen not in record['acceptable']:
        chosen = next((k for k, v in record['tuning'].items() if v['identity'] == req['identity']), None)
    require(chosen in record['acceptable'], 'tool/model/build/settings not in acceptable set')
    require(req['objective'] in record['acceptable'][chosen], 'choice does not match objective')
    current, _ = eligible_inventory(record['inventory'], {**req, 'run_id': base['run_id']})
    require(chosen in current, 'selected candidate unavailable')
    return chosen


# Registry rows are recomputed from validated sources, never accepted by label.
def derive_registry(sources, *, level='technique', held_out=None, source_verifier=None):
    require(level in ('technique', 'production_recommendation'), 'run-only rows cannot be reused')
    require(isinstance(sources, list) and len(sources) >= 2, 'two source ledgers required')
    require(callable(source_verifier), 'trusted source-ledger verifier required')
    records = []; source_hashes = []; targets = set()
    for source in sources:
        shape(source, 'ledger_hash record_hash record')
        sha(source['ledger_hash']); sha(source['record_hash'])
        require(source_verifier(frozen(source)) is True, 'source ledger not verified')
        record = source['record']; _, incomplete = validate_experiment(record)
        require(digest(record) == source['record_hash'], 'source record hash mismatch')
        require(not incomplete and record['comparison_type'] == 'matched_effort', 'technique requires complete matched-effort evidence')
        targets.add(record['request']['target_id']); records.append(record)
        source_hashes.append({'ledger_hash': source['ledger_hash'], 'record_hash': source['record_hash']})
    require(len(targets) == len(records), 'distinct canonical target identities required')
    first = records[0]
    ids = set(eligible_inventory(first['inventory'], first['request'])[0])
    acceptable = dict(first['acceptable'])
    for index, record in enumerate(records[1:], 1):
        require(record['request']['population'] == first['request']['population'] and request(record['request']) == request(first['request']), 'incomparable populations')
        require(set(eligible_inventory(record['inventory'], record['request'])[0]) == ids, 'candidate set changed; retest required')
        if level == 'production_recommendation' and index == len(records) - 1:
            require(all(set(objectives) <= set(record['acceptable'].get(key, []))
                        for key, objectives in acceptable.items()), 'held-out target regressed; cannot refit conclusion')
        for key in list(acceptable):
            objectives = sorted(set(acceptable[key]) & set(record['acceptable'].get(key, [])))
            if objectives: acceptable[key] = objectives
            else: del acceptable[key]
    require(acceptable, 'no common acceptable conclusion')
    if level == 'production_recommendation':
        require(held_out in targets and len(targets) >= 3 and records[-1]['request']['target_id'] == held_out, 'independent held-out target required')
    else:
        require(held_out is None, 'unexpected held-out target')
    return {'schema_version': 1, 'derivation_version': MAPPING_VERSION, 'level': level,
            'population': first['request']['population'], 'op': request(first['request']),
            'candidates': sorted(ids), 'acceptable': acceptable, 'sources': frozen(sources),
            'source_hashes': source_hashes, 'held_out': held_out}


class DecisionRegistry:
    def __init__(self, rows=(), *, source_verifier=None):
        self.source_verifier = source_verifier
        self._rows = {}
        for row in rows:
            self.add(row)

    def add(self, row):
        shape(row, 'schema_version derivation_version level population op candidates acceptable sources source_hashes held_out')
        expected = derive_registry(row['sources'], level=row['level'], held_out=row['held_out'], source_verifier=self.source_verifier)
        require(row == expected, 'registry derivation mismatch')
        self._rows[digest(row)] = canonical(row)
        return digest(row)

    def get(self, row_id):
        require(row_id in self._rows, 'unknown registry row')
        row = json.loads(self._rows[row_id])
        require(row == derive_registry(row['sources'], level=row['level'], held_out=row['held_out'],
                                       source_verifier=self.source_verifier), 'source evidence changed')
        return row

    def write(self, path):
        Path(path).write_bytes(canonical({'schema_version': 1, 'rows': [json.loads(v) for v in self._rows.values()]}) + b'\n')

    @classmethod
    def read(cls, path, *, source_verifier=None):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'duplicate registry field'); result[key] = value
            return result
        data = json.loads(Path(path).read_text(), object_pairs_hook=unique)
        shape(data, 'schema_version rows')
        require(type(data['schema_version']) is int and data['schema_version'] == 1 and isinstance(data['rows'], list), 'unknown registry schema')
        return cls(data['rows'], source_verifier=source_verifier)


class SelectionGate:
    """Trusted run-scoped inventory/evidence authority, not a maker waiver API.

    The holder supplies independently frozen evidence and verifies Henry-origin
    references outside this library. issued bindings are immutable snapshots.
    """
    def __init__(self, inventory, *, experiments=(), registry=None, overrides=()):
        self._inventory = canonical(inventory)
        self._experiments = {e.record_id: e.payload for e in experiments}
        self.registry = registry or DecisionRegistry()
        self._overrides = {digest(o): canonical(o) for o in overrides}
        self._issued = {}

    def preflight(self, req, *, experiment=None, registry_row=None, override=None, whole_frame=None):
        req = frozen(req); request(req)
        inventory = json.loads(self._inventory)
        candidates, incomplete = eligible_inventory(inventory, req)
        require(sum(x is not None for x in (experiment, registry_row, override)) <= 1, 'ambiguous selection evidence')
        choice = digest(req['identity'])
        # Overrides waive selection only, never licence/resources/availability.
        tuned_base = None
        if experiment in self._experiments:
            selected = json.loads(self._experiments[experiment])
            validate_experiment(selected)
            tuned_base = next((k for k, v in selected['tuning'].items() if v['identity'] == req['identity']), None)
        require(choice in candidates or tuned_base in candidates, 'execution identity is not eligible')
        kind, reference, evidence = 'no alternative available', digest(inventory), None
        if override is not None:
            require(override in self._overrides, 'unknown Henry override')
            evidence = json.loads(self._overrides[override])
            shape(evidence, 'run_id step_id requirement henry_evidence request_hash')
            require(evidence['run_id'] == req['run_id'] and evidence['step_id'] == req['step_id'] and evidence['request_hash'] == digest(req), 'stale Henry override')
            require(evidence['requirement'] == 'tool_selection', 'override cannot waive hard gates')
            text(evidence['henry_evidence'])
            kind, reference = 'Henry override', override
        elif experiment is not None:
            require(experiment in self._experiments, 'unknown selection experiment')
            evidence = json.loads(self._experiments[experiment]); validate_experiment(evidence)
            require(evidence['request']['run_id'] == req['run_id'] and evidence['request']['target_id'] == req['target_id'], 'run-only experiment cannot cross runs')
            require(evidence['request']['geometry'] == req['geometry'], 'selection geometry mismatch')
            require(evidence['inventory'] == inventory, 'inventory changed; retest required')
            _matches(evidence, req)
            kind, reference = 'experiment', experiment
        elif registry_row is not None:
            evidence = self.registry.get(registry_row)
            require(evidence['population'] == req['population'] and evidence['op'] == request(req), 'registry applicability mismatch')
            require(set(evidence['candidates']) == set(candidates), 'new/changed candidate; retest required')
            require(not incomplete, 'incomplete candidate set cannot reuse technique evidence')
            require(choice in evidence['acceptable'] and req['objective'] in evidence['acceptable'][choice], 'registry tool/objective mismatch')
            if OPERATIONS[request(req)] != 'local_valid':
                shape(whole_frame, 'request_hash accepted evidence')
                require(whole_frame['request_hash'] == digest(req) and whole_frame['accepted'] is True,
                        'registry requires current whole-frame acceptance')
                text(whole_frame['evidence'])
            kind, reference = 'registry', registry_row
        else:
            require(len(candidates) == 1, 'selection experiment or registry required before full-resolution execution')
        binding = {'request': req, 'inventory': inventory, 'kind': kind, 'reference': reference, 'evidence': evidence, 'whole_frame': whole_frame}
        token = digest(binding); self._issued[token] = canonical(binding)
        return token

    def binding(self, token):
        require(token in self._issued, 'unknown selection authorization')
        return json.loads(self._issued[token])

    def restore(self, token, binding):
        """Rebuild a persisted binding using this gate's trusted evidence.

        Stored evidence is never imported as authority: preflight resolves all
        references against the independently supplied inventory and records.
        """
        binding = frozen(binding)
        shape(binding, 'request inventory kind reference evidence whole_frame')
        require(digest(binding) == token, 'persisted selection binding hash mismatch')
        kwargs = {'whole_frame': binding['whole_frame']}
        key = {'experiment': 'experiment', 'registry': 'registry_row',
               'Henry override': 'override'}.get(binding['kind'])
        require(key is not None or binding['kind'] == 'no alternative available',
                'unknown selection evidence kind')
        if key:
            kwargs[key] = binding['reference']
        require(self.preflight(binding['request'], **kwargs) == token,
                'persisted selection evidence changed')

    def revalidate(self, token, req):
        binding = self.binding(token)
        require(req == binding['request'], 'execution/step changed since preflight')
        kwargs = {'experiment': None, 'registry_row': None, 'override': None, 'whole_frame': binding['whole_frame']}
        key = {'experiment': 'experiment', 'registry': 'registry_row', 'Henry override': 'override'}.get(binding['kind'])
        if key: kwargs[key] = binding['reference']
        require(self.preflight(req, **kwargs) == token, 'selection evidence changed')

    def validate_step(self, step):
        require(step.selection is not None, 'full-resolution step requires selection authorization')
        binding = self.binding(step.selection); req = binding['request']
        require((step.run_id, step.step_id, step.op, step.state) == (req['run_id'], req['step_id'], req['op'], req['state']), 'selection step binding mismatch')
        require({k: getattr(step, k) for k in ('inputs', 'coverage', 'masks', 'rois')} == req['geometry'], 'selection geometry changed')
        ident = req['identity']
        require((step.tool, step.model, step.environment, step.contract_version) == (ident['tool'] + ':' + ident['build'], ident['model'], ident['backend'], ident['contract_version']), 'selection execution identity changed')
        params = dict(step.params); params.pop('engine_receipt', None)
        require(params == ident['params'], 'effective settings changed')
        self.revalidate(step.selection, req)


def ledger_source_verifier(ledgers):
    """Build a live verifier from trusted Ledger objects (runner keys/contracts).

    The actual forensic file must still have its recorded SHA256 and a kept,
    fully revalidated step bound to this exact selection experiment.
    """
    ledgers = tuple(ledgers)
    def verify(source):
        for ledger in ledgers:
            if not ledger.path.exists():
                continue
            raw = ledger.path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != source['ledger_hash']:
                continue
            steps = ledger.read()
            if ledger.path.read_bytes() != raw:
                raise ValueError('source ledger changed during validation')
            for step in steps:
                if step.decision != 'kept' or not step.full_resolution or not step.selection:
                    continue
                binding = ledger.selection_gate.binding(step.selection)
                if (binding['kind'] == 'experiment' and binding['reference'] == source['record_hash']
                        and digest(binding['evidence']) == digest(source['record'])):
                    return True
        return False
    return verify

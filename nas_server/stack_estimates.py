"""Informational stack forecasts; no selection or execution decisions live here.

All features are available before execution. Elapsed times are fitted once (stages
are never added to elapsed). Historical engine-only scope is kept separate from
attempt telemetry. Unknown configuration fields are never imputed.
"""
import json
import logging
import math
from datetime import datetime, timezone
from statistics import median

VERSION = 1
log = logging.getLogger(__name__)


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def configuration(row):
    """Missing provenance is an explicit cohort, not today's script flags."""
    try:
        record = json.loads(row.get('stage_times_json') or '{}')
    except (ValueError, TypeError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    forecasts = record.get('forecasts')
    forecasts = forecasts if isinstance(forecasts, dict) else {}
    evidence = forecasts.get('configuration')
    evidence = evidence if isinstance(evidence, dict) else {}
    return (row.get('engine'), row.get('framing'), row.get('drizzle'),
            row.get('feather_px'), evidence.get('overlap_norm'),
            evidence.get('context'), row.get('work_dir_kind'), record.get('scope', 'legacy_engine_only'))



_CONTEXT_FIELDS = {'model_version', 'engine', 'scripts', 'binaries', 'cpu_count',
                   'binary_version', 'hero', 'fast', 'pi_mem_gb', 'ii_buffer_mb'}


def _prior_code_key(row):
    """Only the complete known context schema permits script-only fallback."""
    key = list(configuration(row))
    try:
        context = json.loads(key[5])
    except (TypeError, ValueError):
        return None
    if not isinstance(context, dict) or set(context) != _CONTEXT_FIELDS:
        return None
    if (type(context['model_version']) is not int or context['model_version'] != VERSION
            or context['engine'] != row.get('engine')
            or not isinstance(context['scripts'], str) or len(context['scripts']) != 64
            or any(c not in '0123456789abcdef' for c in context['scripts'])
            or type(context['hero']) is not bool or type(context['fast']) is not bool
            or not positive(context['cpu_count']) or not positive(context['pi_mem_gb'])
            or not positive(context['ii_buffer_mb']) or context['binary_version'] != 'unknown'):
        return None
    binaries = context['binaries']
    if not isinstance(binaries, dict) or set(binaries) != {'siril-cli', 'PixInsight', 'saspro'}:
        return None
    for value in binaries.values():
        if value == 'unknown':
            continue
        if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or v < 0 for v in value):
            return None
    if (key[-1] != 'attempt_before_cleanup' or type(key[4]) is not bool
            or key[6] not in ('local_ssd', 'nas_hdd')):
        return None
    del context['scripts']
    key[5] = json.dumps(context, sort_keys=True, allow_nan=False)
    return tuple(key)


def _successful(rows):
    return [r for r in rows if r.get('success') == 1
            and positive(r.get('elapsed_s')) and positive(r.get('frame_count'))]


def _chronology(row):
    return (row.get('started_at') or '', row.get('id') or 0)


def _prior_cohort(rows, query):
    key = _prior_code_key(query)
    if key is None:
        return []
    groups = {}
    for row in rows:
        if _prior_code_key(row) == key:
            groups.setdefault(configuration(row), []).append(row)
    eligible = [group for group in groups.values() if len(_successful(group)) >= 3]
    return max(eligible, key=lambda group: max(map(_chronology, _successful(group))), default=[])

def _supported(rows):
    counts = {r['frame_count'] for r in rows}
    return len(rows) >= 4 and len(counts) >= 3 and max(counts) >= 1.5 * min(counts)


def _fit(rows, quadratic):
    """Two-column nonnegative least squares, scaled to avoid N² conditioning."""
    slope = median(r['elapsed_s'] / r['frame_count'] for r in rows)
    candidates = [(slope, 0.0)]  # robust baseline always remains a candidate
    if quadratic and _supported(rows):
        scale = max(r['frame_count'] for r in rows)
        xs = [r['frame_count'] / scale for r in rows]
        ys = [r['elapsed_s'] for r in rows]
        s2, s3, s4 = (sum(x**p for x in xs) for p in (2, 3, 4))
        t1 = sum(x*y for x, y in zip(xs, ys))
        t2 = sum(x*x*y for x, y in zip(xs, ys))
        det = s2*s4-s3*s3
        if det > 1e-8*s2*s4:
            a, b = (t1*s4-t2*s3)/det, (t2*s2-t1*s3)/det
            if a >= 0 and b >= 0:
                candidates.append((a/scale, b/scale**2))
        candidates.extend([(max(0, t1/s2)/scale, 0),
                           (0, max(0, t2/s4)/scale**2)])
    # Absolute relative error resists large-count runs dominating the baseline.
    return min(candidates, key=lambda ab: median(
        abs((ab[0]*r['frame_count']+ab[1]*r['frame_count']**2)/r['elapsed_s']-1)
        for r in rows))


def _point(rows, query):
    own = [r for r in rows if r.get('target') == query.get('target')]
    source = 'target' if _supported(own) else 'matching_pool'
    training = own if source == 'target' else rows
    a, b = _fit(training, query.get('framing') == 'max')
    n = query['frame_count']
    return a*n+b*n*n, training, source, [a, b]


def forecast(rows, query, *, count_basis, now=None):
    """Return an immutable JSON-safe forecast, including rejected/censored counts."""
    key = configuration(query)
    compatible = [r for r in rows if configuration(r) == key]
    prior_code = False
    if not _successful(compatible):
        prior = _prior_cohort(rows, query)
        if prior:
            compatible = prior
            prior_code = True
    valid = _successful(compatible)
    invalid_count = len(compatible)-len(valid)-sum(r.get("success") != 1 for r in compatible)
    valid = sorted(valid, key=_chronology)[-200:]
    result = dict(version=VERSION, created_at=now or datetime.now(timezone.utc).isoformat(),
                  input_count=query.get('frame_count'), count_basis=count_basis,
                  configuration=list(key), history_cutoff=max((r.get('id', 0) for r in rows), default=0),
                  samples=len(valid), censored=sum(r.get('success') != 1 for r in compatible),
                  excluded_invalid=invalid_count, excluded_configuration=len(rows)-len(compatible),
                  seconds=None, lower_s=None, upper_s=None, source='unknown',
                  range_kind='unknown', extrapolation=False)
    if not positive(query.get('frame_count')):
        return result
    if not valid:
        # No fabricated precision from post-processing runs or unmatched flags.
        return result
    point, training, source, coefficients = _point(valid, query)
    # Chronological residuals, including model/target selection inside each fold.
    errors = []
    for i in range(4, len(valid)):
        held = valid[i]
        predicted, _, _, _ = _point(valid[:i], held)
        if positive(predicted):
            errors.append(abs(math.log(held['elapsed_s']/predicted)))
    counts = [r['frame_count'] for r in training]
    extrapolated = not min(counts) <= query['frame_count'] <= max(counts)
    factor = max(1.25, math.exp(sorted(errors)[min(len(errors)-1, math.ceil(.9*len(errors))-1)])) if errors else 2.0
    if extrapolated:
        factor = max(2.0, factor)
    if not all(math.isfinite(v) for v in (point, factor, point*factor)):
        return result
    result.update(seconds=point, lower_s=point/factor, upper_s=point*factor,
                  source='prior_code_version' if prior_code else source,
                  fit_source=source, coefficients=coefficients, samples=len(training),
                  observed_counts=[min(counts), max(counts)], extrapolation=extrapolated,
                  calibration_samples=len(errors),
                  range_kind=('rolling residual planning range' if len(errors) >= 8 and not extrapolated
                              else 'limited history planning range; not a confidence interval'))
    if prior_code:
        latest = max(training, key=_chronology)
        result['prior_code'] = dict(configuration=list(configuration(latest)),
                                    samples=len(training), latest_run_at=latest.get('started_at'))
    return result


def long_warning(estimate, hours=5):
    threshold = float(hours)
    if not positive(threshold):
        raise ValueError('stack_long_warn_hours must be finite and positive')
    return positive(estimate.get('upper_s')) and estimate['upper_s'] > threshold*3600


def description(estimate, hours=5):
    hours = estimate.get('warn_hours', hours)
    if not positive(estimate.get('upper_s')):
        return f'Stack duration unknown — limited matching history; may exceed {hours:g} h'
    lo, hi = estimate['lower_s']/3600, estimate['upper_s']/3600
    text = f'estimated ~{lo:.1f}–{hi:.1f} h'
    if long_warning(estimate, hours):
        text = '⚠️ ' + text
    if str(estimate.get('count_basis', '')).startswith('provisional'):
        text += ' (provisional)'
    if estimate.get('extrapolation'):
        text += ' (extrapolated)'
    if (estimate.get('configuration') or [None])[-1] == 'legacy_engine_only':
        text += ' (engine time only; total duration unknown)'
    if estimate.get('source') == 'prior_code_version':
        prior = estimate['prior_code']
        text += f" (based on {prior['samples']} runs under earlier code from {prior['latest_run_at'] or 'unknown date'})"
    return text + '; ' + estimate['range_kind']


def safe_forecast(target, count, engine, framing, drizzle, feather_px, *, count_basis,
                  context=None, overlap_norm=None, work_dir_kind=None):
    """All DB/model failures stay informational; callers must never gate on this."""
    try:
        from nas_server.database import get_conn
        with get_conn() as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM stacking_runs ORDER BY id')]
        query = dict(target=target, frame_count=count, engine=engine, framing=framing,
                     drizzle=int(drizzle), feather_px=feather_px, work_dir_kind=work_dir_kind,
                     stage_times_json=json.dumps({'scope': 'attempt_before_cleanup', 'forecasts': {
                         'configuration': {'context': context, 'overlap_norm': overlap_norm}}}))
        return forecast(rows, query, count_basis=count_basis)
    except Exception:
        log.warning('Stack forecast unavailable', exc_info=True)
        return dict(version=VERSION, created_at=datetime.now(timezone.utc).isoformat(),
                    input_count=count, count_basis=count_basis, history_cutoff=None,
                    samples=0, censored=None,
                    seconds=None, lower_s=None, upper_s=None, source='unavailable')


def runtime_context(engine, settings, *, hero=False, fast=False):
    """Version script inputs and declared resource budget; binary version is unknown.

    We do not launch an engine to ask its version. Unknown binary identity remains
    explicit; a changed script/budget cannot silently reuse this cohort.
    """
    import hashlib
    import os
    import shutil
    from pathlib import Path
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted([*root.glob('*.ssf'), root / 'stacker.py', root / 'stack_config.py']):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    binaries = {}
    for name in ('siril-cli', 'PixInsight', 'saspro'):
        found = shutil.which(name)
        if found:
            stat = Path(found).stat()
            binaries[name] = [stat.st_size, stat.st_mtime_ns]
        else:
            binaries[name] = 'unknown'
    return json.dumps(dict(model_version=VERSION, engine=engine, scripts=digest.hexdigest(), binaries=binaries,
                           cpu_count=os.cpu_count(), binary_version='unknown', hero=hero, fast=fast,
                           pi_mem_gb=settings.get('pi_register_mem_budget_gb', 32),
                           ii_buffer_mb=settings.get('pi_ii_buffer_budget_mb', 16384)), sort_keys=True)


def provisional_storage(count, engine, drizzle):
    """Predict today's disk route read-only; start re-evaluates actual capacity."""
    import shutil
    multiplier = 6 if engine == 'pixinsight_register' else 5 if drizzle else 3
    needed = count * 14 * multiplier * 1024 * 1024
    try:
        if shutil.disk_usage('/tmp').free >= needed:
            return 'local_ssd'
        if shutil.disk_usage('/mnt/nas_data').free >= needed:
            return 'nas_hdd'
    except OSError:
        pass
    return None


def admission_forecasts(item):
    """Provisional per-filter DB counts, without culling/FITS reads or selection.

    Apply the frozen applicable cap as an upper bound. Quality/EQ/files are
    not inspected here; start uses the actual post-gate selected list.
    """
    try:
        from nas_server.database import get_conn, get_frames_by_target, get_mosaic_panel_targets
        from nas_server.stack_selection import canonical_filter
        from nas_server.stack_config import feather_decision
        from nas_server.config import settings
        targets = {item['target'], *get_mosaic_panel_targets(item['target'])}
        with get_conn() as conn:
            row = conn.execute('SELECT association FROM targets WHERE target=?', (item['target'],)).fetchone()
        if row and row[0]:
            targets.update(t.strip() for t in row[0].split(',') if t.strip())
        by_path = {}
        for target in targets:
            for frame in get_frames_by_target(target):
                by_path[frame['file_path']] = frame
        counts = {}
        requested = canonical_filter(item.get('filter_name'))
        for frame in by_path.values():
            filt = canonical_filter(frame.get('filter')) or 'UNKNOWN'
            if frame.get('exclude') or (requested and requested != filt):
                continue
            if item.get('exptime') is not None and round(frame.get('exposure_time') or 0) != round(item['exptime']):
                continue
            counts[filt] = counts.get(filt, 0) + 1
        engine = 'siril' if item['engine'] == 'pixinsight_register' and item['fast'] else item['engine']
        context = runtime_context(engine, settings, hero=item["hero"], fast=item["fast"])
        from nas_server.mosaic_selection import effective_cap
        cap = effective_cap(item['extra_params'].get('stack_mosaic'), item['framing'], item['engine'])
        estimates = {}
        for filt, population in sorted(counts.items()):
            n = min(population, cap) if cap else population
            decision = feather_decision(item['extra_params']['stack_feather'], n, item['framing'], engine, item['fast'])
            storage = provisional_storage(n, engine, item['drizzle'])
            estimates[filt] = safe_forecast(item['target'], n, engine, item['framing'], item['drizzle'],
                                            decision['feather_px'] if engine == 'siril' else None, context=context,
                                            overlap_norm=bool(decision['overlap_norm']), work_dir_kind=storage,
                                            count_basis='provisional capped DB per-filter upper bound before quality/EQ/files' if cap else 'provisional DB per-filter count before quality/EQ/files')
            estimates[filt]['provisional_candidates'] = population
            estimates[filt]['applicable_cap'] = cap
            estimates[filt]['storage_basis'] = 'provisional current free-space route; reselected at start'
            estimates[filt]['warn_hours'] = settings.get('stack_long_warn_hours', 5)
            estimates[filt]['feather_policy'] = decision
            estimates[filt]['stack_options'] = decision['options']
        return estimates or {'unknown': {'upper_s': None, 'source': 'unknown', 'count_basis': 'no provisional population'}}
    except Exception:
        log.warning('Admission forecast unavailable; queue admission unchanged', exc_info=True)
        return {'unknown': {'upper_s': None, 'source': 'unavailable'}}


def attach_forecasts(record, evidence):
    """Freeze JSON evidence without allowing forecast serialization to fail a run."""
    try:
        record['forecasts'] = json.loads(json.dumps(evidence, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        log.warning('Forecast evidence could not be serialized; actual timing retained', exc_info=True)

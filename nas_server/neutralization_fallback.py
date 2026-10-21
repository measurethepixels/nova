"""Offline #984 atomic fallback; no production callers or partial correction."""
from __future__ import annotations
import hashlib
import json
import numpy as np

VERSION = 'neutralization-atomic-skip-v1'
CLIPPING_CEILING = 0.0
QUALITY_KEYS = frozenset({"sky_balance_vs_input", "sky_balance_vs_attempt", "sky_level",
                          "body_rg", "body_bg", "core", "faint_snr", "finite"})


class RefusedInput(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.receipt = {'status':'failed_invalid_input','reason':reason,'version':VERSION}


def identity(array):
    a=np.asarray(array)
    h=hashlib.sha256()
    h.update(str(a.dtype).encode());h.update(str(a.shape).encode())
    h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def select(original, attempted, valid, sky, *, provenance, quality_checks):
    """Measure a scratch attempt before returning an independently owned selection.

    Caller supplies frozen metrics for both outputs; errors never become skips.
    Invalid/no-data samples are excluded and must remain unchanged in the attempt.
    """
    x,y=np.asarray(original),np.asarray(attempted)
    valid,sky=np.asarray(valid),np.asarray(sky)
    if x.ndim!=3 or x.shape[-1]!=3 or x.shape!=y.shape:
        raise RefusedInput('matched HWC RGB input/attempt required')
    if valid.shape!=x.shape[:2] or sky.shape!=valid.shape or valid.dtype!=bool or sky.dtype!=bool:
        raise RefusedInput('boolean validity/sky masks with matching shape required')
    if np.any(sky&~valid) or not sky.any():
        raise RefusedInput('nonempty valid sky population required')
    if any(np.any(~np.isfinite(a[valid])) or np.any((a[valid]<0)|(a[valid]>1)) for a in [x,y]):
        raise RefusedInput('valid input/attempt must be finite and normalized')
    if not np.array_equal(x[~valid],y[~valid],equal_nan=True):
        raise RefusedInput('attempt changed excluded no-data samples')
    if not isinstance(provenance,dict) or not provenance:
        raise ValueError('missing frozen provenance')
    # A canonical JSON round trip prevents mutable aliases and records a stable ID.
    frozen=json.loads(json.dumps(provenance,sort_keys=True,allow_nan=False))
    if set(quality_checks)!={'input','attempt'} or any(not isinstance(c,dict) or set(c)!=QUALITY_KEYS or any(type(v)!=bool for v in c.values()) for c in quality_checks.values()):
        raise ValueError('explicit boolean quality checks for both populations required')
    positive=(x>0)&sky[...,None]
    transitions=positive&(y<=0)
    channel_population=positive.sum(axis=(0,1))
    channel_count=transitions.sum(axis=(0,1))
    any_population=int(np.any(positive,axis=-1).sum())
    if not any_population:
        raise RefusedInput('no originally positive sky samples')
    any_count=int(np.any(transitions,axis=-1).sum())
    fractions=np.divide(channel_count,channel_population,out=np.zeros(3),where=channel_population>0)
    damaged=bool(np.any(fractions>CLIPPING_CEILING) or any_count/any_population>CLIPPING_CEILING)
    path='input' if damaged else 'attempt'
    chosen_checks=dict(quality_checks[path])
    if not damaged and not all(chosen_checks.values()):
        raise ValueError('failed_quality_guard: safe clipping does not imply scientific acceptance')
    selected=(x if damaged else y).copy()
    receipt={'version':VERSION,'status':'skipped_clipping_guard' if damaged else 'applied',
        'chosen_path':path,'ceiling':CLIPPING_CEILING,'valid_pixels':int(valid.sum()),
        'excluded_pixels':int((~valid).sum()),'sky_pixels':int(sky.sum()),
        'original_positive_channel_population':channel_population.tolist(),
        'preexisting_channel_nonpositive':np.sum((x<=0)&sky[...,None],axis=(0,1)).tolist(),
        'incremental_channel_counts':channel_count.tolist(),'incremental_channel_fractions':fractions.tolist(),
        'channel_rate_defined':(channel_population>0).tolist(),
        'incremental_any_count':any_count,'original_positive_any_population':any_population,
        'incremental_any_fraction':any_count/any_population,
        'selected_quality_checks':chosen_checks,
        'neutralization_quality':('fail' if not all(chosen_checks.values()) else 'unavailable_skipped') if damaged else 'pass',
        'provenance':frozen,'provenance_sha256':hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'array_identities':{'input':identity(x),'attempt':identity(y),'valid':identity(valid),'sky':identity(sky),'selected':identity(selected)}}
    return selected,receipt

"""Experimental #984 sky-measured linked stretch; no production callers."""
from __future__ import annotations
import numpy as np

VERSION = 'linked-sky-full-mad-3.5-v1'
MULTIPLIER = 3.5
MAD_SCALE = 1.4826


def estimate(image, sky):
    """Measure the frozen input sky; refuse invalid/degenerate populations."""
    x = np.asarray(image, dtype=np.float32)
    mask = np.asarray(sky)
    if x.ndim != 3 or x.shape[-1] != 3 or mask.dtype != bool or mask.shape != x.shape[:2]:
        raise ValueError('HWC RGB and matching bool sky required')
    if not np.isfinite(x).all() or np.any((x < 0) | (x > 1)):
        raise ValueError('finite normalized input required')
    if mask.sum() < 500 or np.any(np.all(x[mask] == 0, axis=-1)):
        raise ValueError('insufficient sky or no-data in sky')
    samples = x[mask].astype(np.float64)
    medians = np.median(samples, axis=0)
    sigmas = MAD_SCALE * np.median(np.abs(samples-medians), axis=0)
    bp = float(np.min(medians-MULTIPLIER*sigmas))
    median = float(np.median(x))  # original linked transfer median, unchanged
    if np.any(sigmas <= 0) or not 0 <= bp < median < 1:
        raise ValueError('degenerate or negative black point; no floor substitution')
    return {'version': VERSION, 'sky_rgb_medians': medians.tolist(),
            'sky_rgb_sigmas': sigmas.tolist(), 'blackpoint': bp,
            'transfer_median': median, 'sky_pixels': int(mask.sum()),
            'multiplier': MULTIPLIER, 'mad_scale': MAD_SCALE}


def stretch(image, sky, target_median):
    """Invoke the installed, unchanged linked kernel with measured bp."""
    from setiastro.saspro.imageops import stretch as engine
    if not np.isfinite(target_median) or not 0 < target_median < 1:
        raise ValueError('normalized target median required')
    x = np.asarray(image, dtype=np.float32)
    plan = estimate(x, sky)
    bp, median = plan['blackpoint'], plan['transfer_median']
    denom = max(1-bp, 1e-12)
    out = engine.numba_color_linked_from_img(x, bp, denom, (median-bp)/denom, float(target_median))
    out = np.clip(out, 0, 1).astype(np.float32, copy=False)
    if not np.isfinite(out).all():
        raise ValueError('nonfinite kernel output')
    return out, plan

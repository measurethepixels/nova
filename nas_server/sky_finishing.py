"""Fixed-input sky finishing contract v1; see docs/analysis/issue-970-sky.md.

Lazy scientific imports keep the service's pure-logic imports dependency-free.
"""
ESTIMATOR_VERSION = 'input-sky40-gaussian6-v1'
MAX_CLIPPED_SKY = 0.01  # maximum newly clipped fraction per channel
MAX_PREEXISTING_CLIPPED_SKY = 0.15
DISPLAY_NOISE_FLOOR = 1 / 255
MAX_ADDED_BOUNDARY_CONTRAST = DISPLAY_NOISE_FLOOR
IDENTITY_SIGMAS = 5.0
CURVE_VERSION = "global-linear-hermite-v1"
SKY_BANDS = {
    'galaxy': (.05, .08), 'galaxy_group': (.05, .08),
    'interacting_galaxies': (.05, .08), 'emission_nebula': (.06, .16),
    'reflection_nebula': (.06, .11), 'planetary_nebula': (.05, .09),
    'supernova_remnant': (.05, .13), 'globular_cluster': (.04, .08),
    'open_cluster': (.04, .08), 'asterism': (.04, .08),
    'double_star': (.04, .08), 'nebula': (.06, .14),
}


def sky_band(object_type):
    kind = (object_type or 'galaxy').strip().lower().replace(' ', '_')
    if kind not in SKY_BANDS:
        kind = 'nebula' if 'nebula' in kind else 'open_cluster' if 'cluster' in kind else 'galaxy'
    return SKY_BANDS[kind]


def luminance(data):
    import numpy as np
    if data.ndim == 2:
        return data
    if data.ndim != 3 or data.shape[0] != 3:
        raise ValueError('finishing requires mono or channel-first RGB')
    return np.einsum('cij,c->ij', data, [.2126, .7152, .0722]).astype(np.float32)


def masks_from_input(path):
    """#958's <=40% sky; #961's faint and nearby sky, unchanged geometry."""
    import numpy as np
    from scipy.ndimage import gaussian_filter
    from nas_server import galaxy_adequacy as ga
    source = ga.luminance(path)
    local = ga.masks_from_input(path)
    valid = np.isfinite(source) & (source > 0)
    smooth = gaussian_filter(source, 6)
    low, body = np.percentile(smooth[valid], [40, 60])
    sky = valid & (smooth <= low)
    if sky.sum() < 500:
        raise ValueError('insufficient finishing sky')
    return {'sky': sky, 'faint': local['faint'], 'nearby_sky': local['sky'],
            'protect': valid & (smooth > body), 'valid': valid}


def measure(data, masks):
    import numpy as np
    from scipy.ndimage import gaussian_filter
    lum = luminance(data)
    if any(m.shape != lum.shape for m in masks.values()):
        raise ValueError('finishing mask/output shape mismatch; no resampling permitted')
    if not np.isfinite(data).all() or data.min() < -1e-6 or data.max() > 1 + 1e-6:
        raise ValueError('invalid finishing pixel range/coverage')
    sky = masks['sky']
    fine = lum - gaussian_filter(lum, 1.5)
    nearby = masks['nearby_sky']
    noise = max(float(np.std(fine[nearby])), DISPLAY_NOISE_FLOOR)
    channels = data[:, sky] if data.ndim == 3 else data[sky][None]
    return {'per_channel_clipped_sky_fraction': np.mean(channels <= 0, axis=1).tolist(),
            'sky_level': float(np.median(lum[sky])),
            'sky_sigma': float(np.std(fine[sky])),
            'clipped_sky_fraction': float(np.mean(lum[sky] <= 0)),
            'near_black_sky_fraction': float(np.mean(lum[sky] < .01)),
            'channel_clipped_sky_fraction': float(np.mean(np.any(data[:, sky] <= 0, axis=0)))
                if data.ndim == 3 else float(np.mean(lum[sky] <= 0)),
            'faint_separation_snr': float((np.median(lum[masks['faint']]) -
                                         np.median(lum[nearby])) / noise)}


def boundary_contrast(before, after, masks, before_stats=None, expected_luminance=None):
    """Measure transform-created edges and contrast in input-protected sky.

    Correction gradients subtract the original image's gradients, so natural
    subject edges/noise are not charged to finishing. The low-signal contrast
    statistic catches broad bright islands even when their edges are feathered.
    These diagnostics use frozen masks and the pre-mute noise population.
    """
    import numpy as np
    pre, post = luminance(before), luminance(after)
    # With a declared global curve, inspect departures from that curve, not
    # its intentional luminance-dependent contrast. Legacy controls can still
    # inspect raw correction against the input by omitting the reference.
    correction = post - (pre if expected_luminance is None else expected_luminance)
    peak = 0.0
    for population in (masks['protect'], masks['sky']):
        for axis in (0, 1):
            edge = (np.diff(population.astype(np.int8), axis=axis) != 0) & (
                np.abs(np.diff(pre, axis=axis)) <= DISPLAY_NOISE_FLOOR)
            if edge.any():
                peak = max(peak, float(np.max(np.abs(np.diff(correction, axis=axis)[edge]))))
    stats = before_stats if before_stats is not None else measure(before, masks)
    from scipy.ndimage import gaussian_filter
    residual = pre - gaussian_filter(pre, 1.5)
    samples = residual[masks['sky']]
    # Isolated pre-existing zeros must not inflate the low-signal interval.
    noise = max(float(1.4826 * np.median(np.abs(samples - np.median(samples)))),
                DISPLAY_NOISE_FLOOR)
    # Restrict both populations to the SAME low-signal pre-mute interval.
    # A protected bright core must not be compared to background sky.
    sky_like = (np.abs(pre - stats['sky_level']) <=
                2 * noise) & masks['valid']
    protected_sky = sky_like & masks['protect']
    reference_sky = sky_like & masks['sky'] & ~masks['protect']
    contrast = None
    if protected_sky.sum() >= 500 and reference_sky.sum() >= 500:
        contrast = abs(float(np.median(correction[protected_sky]) -
                             np.median(correction[reference_sky])))
    return {'reference': 'input' if expected_luminance is None else CURVE_VERSION,
            'max_added_edge_contrast': peak,
            'added_protected_sky_contrast': contrast,
            'protected_sky_pixels': int(protected_sky.sum()),
            'reference_sky_pixels': int(reference_sky.sum())}


def tone_luminance(lum, sky, target, identity_above):
    """Frozen C1 global curve; positive slope, no lift, exact bright identity.

    Below sky the slope is target/sky. A cubic Hermite segment joins its
    value/slope at sky to value identity_above and slope 1 at the cutoff.
    In deficit form both basis terms are nonnegative, proving no sky lift.
    The deficit derivative is <= 1-target/sky, proving monotonicity.
    """
    import numpy as np
    if not 0 < target < sky < identity_above <= 1:
        raise ValueError('invalid global finishing curve interval')
    values = np.asarray(lum, dtype=np.float64)
    width = identity_above - sky
    slope = target / sky
    u = np.clip((values - sky) / width, 0, 1)
    deficit = ((sky - target) * (1 + 2*u) + width * (1-slope) * u) * (1-u)**2
    result = np.where(values <= sky, values * slope, values - deficit)
    return np.where(values >= identity_above, values, result)


def mute(data, masks, object_type='galaxy', target=None):
    """Apply a global monotonic soft black point; preserve RGB ratios/bright pixels.

    No sky lift. Skip in-band/below-target frames, excessive clipping, or an
    unattainable target. Every accepted result is remeasured on the same pixels.
    """
    import numpy as np
    before = measure(data, masks)
    low, high = sky_band(object_type)
    target = low + .005 if target is None else float(target)
    if not low <= target <= high:
        raise ValueError('sky target outside object-type band')
    info = {'estimator_version': ESTIMATOR_VERSION, 'target': target,
            'band': [low, high], 'curve_version': CURVE_VERSION,
            'identity_sigmas': IDENTITY_SIGMAS,
            'max_newly_clipped_sky_fraction': MAX_CLIPPED_SKY,
            'max_preexisting_clipped_sky_fraction': MAX_PREEXISTING_CLIPPED_SKY,
            'per_channel_newly_clipped_sky_fraction':
                [0.0] * len(before['per_channel_clipped_sky_fraction']),
            'max_added_boundary_contrast': MAX_ADDED_BOUNDARY_CONTRAST,
            'mask_coverage': {k: float(v.mean()) for k, v in masks.items()},
            'before': before, 'after': before, 'applied': False,
            'target_error': before['sky_level'] - target}
    if max(before['per_channel_clipped_sky_fraction']) > MAX_PREEXISTING_CLIPPED_SKY:
        info['skipped'] = 'preexisting_clipping_bound'
        return data.copy(), info
    if before['sky_level'] <= high:
        info['skipped'] = 'already_in_or_below_band'
        return data.copy(), info
    lum = luminance(data)
    identity_above = min(1.0, before['sky_level'] + IDENTITY_SIGMAS *
                         max(before['sky_sigma'], DISPLAY_NOISE_FLOOR))
    info['identity_above'] = identity_above
    if identity_above <= before['sky_level']:
        info['skipped'] = 'curve_interval_unavailable'
        return data.copy(), info
    mapped = tone_luminance(lum, before['sky_level'], target, identity_above)
    ratio = np.divide(mapped, lum, out=np.zeros_like(mapped), where=lum > 0)
    out = (data * (ratio[None] if data.ndim == 3 else ratio)).astype(np.float32)
    after = measure(out, masks)
    # Count newly clipped pixels, rather than charging existing channel zeros
    # to finishing. Both populations use the frozen input sky mask.
    sky = masks['sky']
    pre_channels = data[:, sky] if data.ndim == 3 else data[sky][None]
    post_channels = out[:, sky] if out.ndim == 3 else out[sky][None]
    newly_clipped = np.mean((pre_channels > 0) & (post_channels <= 0), axis=1).tolist()
    spatial = boundary_contrast(data, out, masks, before, expected_luminance=mapped)
    reason = None
    if max(newly_clipped) > MAX_CLIPPED_SKY:
        reason = 'clipping_bound'
    elif not low <= after['sky_level'] <= high or abs(after['sky_level'] - target) > .002:
        reason = 'target_unreachable'
    elif (spatial['max_added_edge_contrast'] > MAX_ADDED_BOUNDARY_CONTRAST or
          (spatial['added_protected_sky_contrast'] is not None and
           spatial['added_protected_sky_contrast'] > MAX_ADDED_BOUNDARY_CONTRAST)):
        reason = 'protection_boundary_contrast'
    elif after['faint_separation_snr'] + 1e-5 < before['faint_separation_snr']:
        reason = 'faint_separation_regression'
    if reason:
        info.update(skipped=reason, attempted_after=after,
                    attempted_boundary_contrast=spatial,
                    attempted_per_channel_newly_clipped_sky_fraction=newly_clipped)
        return data.copy(), info
    info.update(after=after, applied=True, boundary_contrast=spatial,
                per_channel_newly_clipped_sky_fraction=newly_clipped,
                target_error=after['sky_level'] - target)
    return out, info


def delivery_evidence(path, stretch_input, object_type='galaxy'):
    """Read-only final measurement; explicit errors never suppress run.log."""
    try:
        from nas_server.seti_astro import _load_fits
        if stretch_input is None:
            raise ValueError('pre-stretch starless input required')
        data, _ = _load_fits(path)
        stats = measure(data, masks_from_input(stretch_input))
        low, high = sky_band(object_type)
        return {'estimator_version': ESTIMATOR_VERSION, **stats,
                'band': [low, high], 'in_band': low <= stats['sky_level'] <= high}
    except Exception as exc:
        return {'estimator_version': ESTIMATOR_VERSION, 'error': str(exc)}

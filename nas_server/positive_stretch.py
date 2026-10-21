"""Source-level positive statistical-stretch toe, frozen for issue #984.

No spatial mask or late additive crush lift. The installed engine still owns
black-point estimation and the original transfer above that point.
"""
from __future__ import annotations

import numpy as np

TOE_HEIGHT = 1e-4
CURVE_VERSION = "positive-stat-toe-c1-v1"
MAX_JOIN_SLOPE = 1e6
MAX_TOE_CURVATURE = 1e12


def toe_parameters(blackpoint: float, join_slope: float):
    bp, q = float(blackpoint), float(join_slope)
    if not (np.isfinite(bp) and 0 < bp < 1 and np.isfinite(q) and 0 < q <= MAX_JOIN_SLOPE):
        raise ValueError("invalid positive-toe blackpoint/slope")
    d = TOE_HEIGHT / bp
    if q <= 2.5 * d:
        a = (3 * d - q) / 2
        curvature = 2 * abs(q - a) / bp
        parameters = {"kind": "whole_cubic", "a": a, "width": bp}
    else:
        a = d / 4
        width = 3 * (TOE_HEIGHT - bp * a) / (q - a)
        curvature = 2 * (q - a) / width
        parameters = {"kind": "linear_shoulder", "a": a, "width": width}
    if curvature > MAX_TOE_CURVATURE:
        raise ValueError("positive toe exceeds frozen curvature bound")
    return {**parameters, "blackpoint": bp, "join_slope": q, "max_curvature": curvature}


def toe_values(values, blackpoint, join_slope):
    """Evaluate the toe only, on [0,bp], in float64 for derivative checks."""
    x = np.asarray(values, dtype=np.float64)
    p = toe_parameters(blackpoint, join_slope)
    bp, q, a = p["blackpoint"], p["join_slope"], p["a"]
    if np.any(~np.isfinite(x)) or np.any((x < 0) | (x > bp)):
        raise ValueError("toe samples must lie in [0,bp]")
    if p["kind"] == "whole_cubic":
        return a * x + (q - a) * x**3 / (3 * bp**2)
    width = p["width"]
    u = np.maximum(x - (bp - width), 0) / width
    return a * x + (q - a) * width * u**3 / 3


def preserve_positive(source, baseline, blackpoint, join_slope):
    """Apply the declared toe/above-bp affine transfer to one mono population.

    Baseline must be the unboosted installed statistical engine's output.
    Genuine zero stays zero. Float32-positive inputs cannot underflow to zero.
    The quantization floor is subnormal, not a display-level signal pedestal.
    """
    x = np.asarray(source)
    old = np.asarray(baseline)
    if x.shape != old.shape or np.any(~np.isfinite(x)) or np.any(~np.isfinite(old)):
        raise ValueError("source/baseline shape or finite-state mismatch")
    if np.any((x < 0) | (x > 1)) or np.any((old < 0) | (old > 1)):
        raise ValueError("positive stretch requires normalized [0,1] input/output")
    bp = float(blackpoint)
    if bp == 0:
        result = old.astype(np.float64)
    else:
        toe_parameters(bp, join_slope)
        # Compare in float64: numpy float32 <= Python float can round bp up
        # and wrongly send the next float32 sample into the toe domain.
        low = x.astype(np.float64) <= bp
        result = TOE_HEIGHT + (1 - TOE_HEIGHT) * old.astype(np.float64)
        result[low] = toe_values(x[low], bp, join_slope)
    result[x == 0] = 0
    result[x == 1] = 1
    result = result.astype(np.float32)
    positive = x > 0
    result[positive] = np.maximum(result[positive], np.nextafter(np.float32(0), np.float32(1)))
    return result


def positive_statistical_stretch(image, target_median, *, linked=True, blackpoint_sigma=5.0):
    """Experimental engine-backed mono/linked adapter; no vendor code copied."""
    from setiastro.saspro.imageops import stretch as engine

    data = np.asarray(image, dtype=np.float32)
    if data.ndim not in (2, 3) or (data.ndim == 3 and data.shape[-1] != 3):
        raise ValueError("positive statistical stretch requires mono or HWC RGB")
    if not (0 < target_median < 1) or not np.isfinite(blackpoint_sigma) or blackpoint_sigma < 0:
        raise ValueError("invalid stretch parameters")
    if np.any(~np.isfinite(data)) or np.any((data < 0) | (data > 1)):
        raise ValueError("positive statistical stretch requires finite normalized input")
    is_rgb = data.ndim == 3
    if is_rgb and not linked:
        raise ValueError("unlinked RGB is outside the preregistered candidate")
    if is_rgb:
        baseline = engine.stretch_color_image(data, target_median, linked=linked, blackpoint_sigma=blackpoint_sigma)
    else:
        baseline = engine.stretch_mono_image(data, target_median, blackpoint_sigma=blackpoint_sigma)
    result = np.empty_like(data)
    records = []
    populations = [(data, baseline, None)]
    for x, old, channel in populations:
        bp, median = engine._compute_blackpoint_sigma(x, blackpoint_sigma)
        rescaled = (median - bp) / (1 - bp)
        if not (0 < rescaled < 1):
            raise ValueError("degenerate statistical transfer; no valid positive toe")
        slope = (1 - TOE_HEIGHT) * target_median * (1 - rescaled) / (rescaled * (1 - target_median) * (1 - bp))
        corrected = preserve_positive(x, old, bp, slope)
        if channel is None:
            result[:] = corrected
        else:
            result[..., channel] = corrected
        records.append({"channel": channel, "blackpoint": bp, "median": median,
                        **(toe_parameters(bp, slope) if bp > 0 else {"kind": "no_blackpoint"})})
    return result, {"curve_version": CURVE_VERSION, "toe_height": TOE_HEIGHT, "populations": records}

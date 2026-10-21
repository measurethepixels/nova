"""Interim galaxy eligibility: shared faint region above nearby sky, not a score.

Measurements use the post-#960 FITS range contract. A one-level 8-bit display
noise floor prevents a nearly constant/black output from claiming infinite SNR.
See docs/analysis/issue-961-adequacy.md for the conservative calibration.
"""

SEPARATION_FLOOR = 0.25
DISPLAY_NOISE_FLOOR = 1.0 / 255.0
NOT_ACCEPTABLE = "best available, not acceptable"


def luminance(path):
    import numpy as np
    from astropy.io import fits
    from nas_server.fits_range import unit_range
    with fits.open(str(path), memmap=False) as hdul:
        header = hdul[0].header.copy()
        data = unit_range(hdul[0].data, header)
    if data.ndim == 3:
        if data.shape[0] in (3, 4):
            data = np.moveaxis(data[:3], 0, -1)
        if data.shape[-1] not in (3, 4):
            raise ValueError("unsupported adequacy channels")
        data = data[..., :3] @ np.array([.2126, .7152, .0722], dtype=np.float32)
    if data.ndim != 2:
        raise ValueError("unsupported adequacy dimensions")
    return data


def masks_from_input(path):
    """Exclude no-data; 60–85% faint band, nearest quartile of <=40% sky.

    Nearest sky is selected by distance to the shared faint region, not from
    each candidate's brightness. This avoids remote corner gradients without
    assuming a centered galaxy. Core/body pixels never enter the faint band.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter, distance_transform_edt
    source = luminance(path)
    valid = source > 0
    if valid.mean() < .5:
        raise ValueError("insufficient input coverage")
    smoothed = gaussian_filter(source, 6)
    low, lo, hi, core = np.percentile(smoothed[valid], [40, 60, 85, 97])
    if hi <= lo or core - low <= 1e-8:
        raise ValueError("no faint subject dynamic range")
    faint = valid & (smoothed > lo) & (smoothed <= hi)
    sky = valid & (smoothed <= low)
    distance = distance_transform_edt(~faint)
    sky &= distance <= np.percentile(distance[sky], 25)
    if min(faint.sum(), sky.sum()) < 500:
        raise ValueError("insufficient faint/sky pixels")
    return {"faint": faint, "sky": sky}


def measure(data, masks):
    import numpy as np
    from scipy.ndimage import gaussian_filter
    if any(m.shape != data.shape for m in masks.values()):
        raise ValueError("adequacy mask/output shape mismatch")
    if not np.isfinite(data).all():
        raise ValueError("non-finite adequacy pixels")
    faint, sky = masks["faint"], masks["sky"]
    faint_level, sky_level = float(np.median(data[faint])), float(np.median(data[sky]))
    noise = float(np.std((data - gaussian_filter(data, 1.5))[sky]))
    effective_noise = max(noise, DISPLAY_NOISE_FLOOR)
    separation = (faint_level - sky_level) / effective_noise
    return {"subject_adequate": bool(separation >= SEPARATION_FLOOR),
            "faint_separation_snr": separation,
            "adequacy_floor": SEPARATION_FLOOR,
            "faint_median": faint_level, "local_sky_median": sky_level,
            "local_sky_noise": noise, "adequacy_effective_noise": effective_noise,
            "adequacy_mask_pixels": {k: int(v.sum()) for k, v in masks.items()}}


def annotate(candidates, stretch_input):
    """Measure all scoreable candidates before any guard; fail closed per output."""
    try:
        masks = masks_from_input(stretch_input)
    except Exception as exc:
        for candidate in candidates:
            candidate.update(subject_adequate=False, adequacy_error=str(exc))
        return
    for candidate in candidates:
        try:
            candidate.update(measure(luminance(candidate["fits"]), masks))
        except Exception as exc:
            candidate.update(subject_adequate=False, adequacy_error=str(exc))


def delivery_quality(path, stretch_input):
    """Check actual delivered bytes, including configured/experiment overrides."""
    candidate = {"fits": str(path)}
    annotate([candidate], stretch_input)
    candidate.pop("fits")
    candidate["quality_state"] = ("adequate" if candidate["subject_adequate"]
                                  else NOT_ACCEPTABLE)
    return candidate

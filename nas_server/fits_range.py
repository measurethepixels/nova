"""FITS intensity contract: preserve unit-range floats; decode declared storage.

No extrema-based normalization is permitted. Unsupported physical units fail
closed so a caller can reject the step instead of silently changing its stretch.
"""
from pathlib import Path

FLOAT_TOLERANCE = 1e-6


def unit_range(data, header):
    """Convert astropy-scaled pixels to float32 using the FITS storage contract.

    Floats in [0,1] (±1e-6 roundoff) retain their values. Integer images are
    accepted only for FITS unsigned byte/pseudo-unsigned ADU or when BSCALE /
    BZERO explicitly encode the full storage domain into [0,1]. Arbitrary
    signed counts and out-of-range float engine output have ambiguous units.
    """
    import numpy as np
    values = np.asarray(data)
    if not values.size or not np.isfinite(values).all():
        raise ValueError("FITS intensity contains empty or non-finite data")
    bitpix = int(header.get("BITPIX", -32))
    scale = float(header.get("BSCALE", 1))
    zero = float(header.get("BZERO", 0))
    if not np.isfinite(scale) or not np.isfinite(zero) or scale <= 0:
        raise ValueError("Invalid FITS BSCALE/BZERO intensity contract")
    if bitpix > 0:
        if bitpix not in (8, 16, 32, 64):
            raise ValueError("Unsupported integer FITS storage")
        raw_lo = 0 if bitpix == 8 else -(2 ** (bitpix - 1))
        raw_hi = 2 ** bitpix - 1 if bitpix == 8 else 2 ** (bitpix - 1) - 1
        physical_lo, physical_hi = raw_lo * scale + zero, raw_hi * scale + zero
        if abs(physical_lo) <= FLOAT_TOLERANCE and abs(physical_hi - 1) <= FLOAT_TOLERANCE:
            # Astropy has already decoded calibrated unit-range integer data.
            result = values.astype(np.float32)
        elif scale == 1 and (bitpix == 8 or zero == 2 ** (bitpix - 1)):
            result = (values.astype(np.float64) / (2 ** bitpix - 1)).astype(np.float32)
        else:
            raise ValueError("Ambiguous integer FITS intensity units; expected unsigned ADU or unit scaling")
    elif bitpix in (-32, -64):
        result = values.astype(np.float32)
    else:
        raise ValueError("Unsupported FITS BITPIX")
    if not np.isfinite(result).all():
        raise ValueError("FITS conversion produced non-finite intensity")
    lo, hi = float(result.min()), float(result.max())
    if lo < -FLOAT_TOLERANCE or hi > 1 + FLOAT_TOLERANCE:
        raise ValueError(f"Ambiguous FITS intensity range [{lo}, {hi}]; no declared unit-range conversion")
    return result


def fits_numeric_range(path: str | Path):
    """Read physical extrema without interpreting or modifying image bytes."""
    import numpy as np
    from astropy.io import fits
    try:
        with fits.open(str(path), memmap=False) as hdul:
            data = hdul[0].data
            if data is None or not data.size:
                raise ValueError("empty image")
            finite = np.isfinite(data)
            count = int(np.count_nonzero(finite))
            values = data if count == data.size else data[finite]
            return {"path": str(path),
                    "min": float(values.min()) if count else None,
                    "max": float(values.max()) if count else None,
                    "nonfinite": int(data.size - count)}
    except Exception as exc:
        return {"path": str(path), "error": str(exc)}


def hdr_clip_fractions(data):
    """Absolute near-white coverage of the image HDR will actually modify."""
    import numpy as np
    lum = np.asarray(data).mean(axis=0) if data.ndim == 3 else np.asarray(data)
    if not lum.size or not np.isfinite(lum).all():
        raise ValueError("Invalid HDR gate image")
    return float((lum >= .90).mean()), float((lum >= .98).mean())

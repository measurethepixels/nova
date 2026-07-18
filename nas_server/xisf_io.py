"""Fast pure-Python XISF reading + XISF→FITS conversion.

Built on the `xisf` package (0.9.6) + astropy. Reads PixInsight XISF files
directly into numpy without spawning a siril-cli subprocess per file — far
faster than the legacy `_xisf_to_fits` in stacker.py, which we keep only as a
fallback for files the `xisf` package can't parse.
"""

from __future__ import annotations

import numpy as np
from astropy.io import fits


def _build_fits_header(image_meta: dict) -> fits.Header:
    """Reconstruct a FITS header from XISF image metadata.

    `get_images_metadata()[0]['FITSKeywords']` is a dict keyed by FITS keyword
    name; each value is a list of {'value':..., 'comment':...} dicts (a list
    because FITS allows repeated keywords such as COMMENT/HISTORY).
    """
    header = fits.Header()
    fits_kw = (image_meta or {}).get("FITSKeywords", {}) or {}
    for kw, entries in fits_kw.items():
        for entry in entries:
            value = entry.get("value")
            comment = entry.get("comment", "") or ""
            # Coerce numeric strings so astropy stores them as numbers, not str.
            if isinstance(value, str):
                v = value.strip()
                low = v.lower()
                if low in ("t", "true"):
                    value = True
                elif low in ("f", "false"):
                    value = False
                else:
                    try:
                        value = int(v)
                    except ValueError:
                        try:
                            value = float(v)
                        except ValueError:
                            value = v
            try:
                header.append((kw, value, comment), end=True)
            except Exception:
                # Skip any keyword astropy rejects (overlong/invalid) rather
                # than failing the whole conversion.
                continue
    return header


def read_xisf(path: str) -> tuple[np.ndarray, dict]:
    """Read an XISF file directly into a numpy array.

    Returns (data, meta) where:
      - data is channels-first (C, H, W) for multi-channel images, or (H, W)
        for single-channel — matching the FITS/astropy convention.
      - meta is the first image's metadata dict (includes 'FITSKeywords').
    """
    from xisf import XISF

    xisf = XISF(path)
    img = xisf.read_image(0, data_format="channels_last")  # (H, W, C)
    meta = xisf.get_images_metadata()[0]

    if img.ndim == 3:
        if img.shape[2] == 1:
            data = img[:, :, 0]
        else:
            data = np.moveaxis(img, 2, 0)  # (C, H, W)
    else:
        data = img
    return np.ascontiguousarray(data), meta


def xisf_to_fits(xisf_path: str, fits_path: str) -> str:
    """Convert an XISF file to FITS, preserving FITS keywords.

    Fast replacement for the siril-cli per-file converter. Returns fits_path.
    """
    data, meta = read_xisf(xisf_path)
    header = _build_fits_header(meta)
    fits.writeto(fits_path, data, header=header, overwrite=True)
    return fits_path

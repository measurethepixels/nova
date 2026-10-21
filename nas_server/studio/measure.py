"""Versioned array measurements and signed runner receipts.

Pixel coordinates are (x, y); arrays are (y, x[, RGB]). PA is radians from
positive x towards positive y in the array, not an astronomical sky PA.
All populations are supplied by the caller and hashed, never inferred from
maker prose. No measurement is by itself an image-quality verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any, Mapping

import numpy as np
from scipy.ndimage import gaussian_filter
from astropy.wcs import WCS

VERSION = '1'
FUNCTIONS = frozenset({'sky', 'minor_axis', 'model_correlation', 'colour_bands',
                       'star_colour', 'clipping', 'texture', 'orientation', 'difference'})


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def array_hash(array: np.ndarray) -> str:
    a = np.ascontiguousarray(array)
    if a.dtype.hasobject:
        raise ValueError('object arrays are unsupported')
    h = hashlib.sha256(canonical({'shape': a.shape, 'dtype': a.dtype.str}))
    h.update(a.tobytes())
    return h.hexdigest()


def _image(image):
    a = np.asarray(image, dtype=float)
    if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[2] != 3):
        raise ValueError('expected 2D image or RGB image')
    if not np.isfinite(a).all():
        raise ValueError('nonfinite pixels are unsupported; supply a finite image and coverage mask')
    return a


def _mask(mask, shape):
    m = np.asarray(mask)
    if m.dtype != np.bool_ or m.shape != shape[:2] or not m.any():
        raise ValueError('mask must be a nonempty boolean population matching the image')
    return m


def _luminance(a):
    return a.mean(axis=2) if a.ndim == 3 else a


def _mad(values):
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


@dataclass(frozen=True)
class Receipt:
    """Serialized receipts are accepted only after runner signature verification."""
    function: str
    version: str
    inputs: Mapping[str, str]
    mask_hash: str | None
    roi_hash: str | None
    value: Mapping[str, Any]
    signature: str

    def payload(self):
        return {'function': self.function, 'version': self.version,
                'inputs': dict(self.inputs), 'mask_hash': self.mask_hash,
                'roi_hash': self.roi_hash, 'value': dict(self.value)}

    def to_dict(self):
        return {**self.payload(), 'signature': self.signature}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {
                'function', 'version', 'inputs', 'mask_hash', 'roi_hash', 'value', 'signature'}:
            raise ValueError('invalid receipt schema (prose is not a receipt)')
        return cls(**data)


class MeasurementRunner:
    """Holds a >=32 byte private evidence key supplied by the trusted runner.

    HMAC detects forged/tampered receipts when the maker lacks this capability.
    It does not prove process identity, pixel truth, or protect a shared account.
    The package neither loads nor writes keys, settings, caches or databases.
    """
    def __init__(self, key: bytes):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('receipt key must contain at least 32 bytes')
        self._key = key

    def verify(self, receipt: Receipt):
        if not isinstance(receipt, Receipt):
            raise ValueError('measurements must be runner receipts, never prose or numbers')
        if receipt.function not in FUNCTIONS or receipt.version != VERSION:
            raise ValueError('unsupported measurement function/version')
        try:
            expected = hmac.new(self._key, canonical(receipt.payload()), hashlib.sha256).hexdigest()
        except (TypeError, ValueError) as e:
            raise ValueError('invalid numeric receipt') from e
        if not isinstance(receipt.signature, str) or not hmac.compare_digest(expected, receipt.signature):
            raise ValueError('untrusted or modified measurement receipt')

    def _receipt(self, name, inputs, value, mask=None, roi=None):
        payload = {'function': name, 'version': VERSION,
                   'inputs': {role: array_hash(a) for role, a in inputs.items()},
                   'mask_hash': array_hash(mask) if mask is not None else None,
                   'roi_hash': array_hash(roi) if roi is not None else None, 'value': value}
        signature = hmac.new(self._key, canonical(payload), hashlib.sha256).hexdigest()
        return Receipt(**json.loads(canonical(payload)), signature=signature)

    def sky(self, image, mask):
        a = _image(image); m = _mask(mask, a.shape)
        v = _luminance(a)[m]
        return self._receipt('sky', {'image': image},
                             {'level': float(np.median(v)), 'sigma': _mad(v), 'count': int(v.size)}, m)

    def minor_axis(self, image, mask, *, centre, pa, band, bin_edges):
        a = _image(image); m = _mask(mask, a.shape)
        edges = np.asarray(bin_edges, dtype=float)
        if (edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all()
                or edges[0] < 0 or np.any(np.diff(edges) <= 0)
                or not np.isfinite([*centre, pa, band]).all() or band <= 0):
            raise ValueError('invalid profile geometry')
        y, x = np.indices(a.shape[:2]); x = x - centre[0]; y = y - centre[1]
        major = x * np.cos(pa) + y * np.sin(pa)
        minor = -x * np.sin(pa) + y * np.cos(pa)
        pos, neg = [], []
        counts = []
        lum = _luminance(a)
        for lo, hi in zip(edges[:-1], edges[1:]):
            p = m & (np.abs(major) <= band / 2) & (minor >= lo) & (minor < hi)
            n = m & (np.abs(major) <= band / 2) & (minor <= -lo) & (minor > -hi)
            if not p.any() or not n.any():
                raise ValueError('profile bins require pixels on both sides')
            pos.append(float(np.median(lum[p]))); neg.append(float(np.median(lum[n])))
            counts.append([int(p.sum()), int(n.sum())])
        delta = np.array(pos) - neg
        return self._receipt('minor_axis', {'image': image},
                             {'positive': pos, 'negative': neg, 'counts': counts,
                              'symmetry': float(np.mean(np.abs(delta))),
                              'centre': [float(v) for v in centre], 'pa': float(pa), 'band': float(band),
                              'bin_edges': edges.tolist()}, m)

    def model_correlation(self, model, object_mask, roi):
        a = _image(model); r = _mask(roi, a.shape)
        o = np.asarray(object_mask)
        if o.dtype != np.bool_ or o.shape != a.shape[:2]:
            raise ValueError('object mask must be boolean and match model')
        x = _luminance(a)[r]; y = o[r].astype(float)
        if np.std(x) == 0 or np.std(y) == 0:
            raise ValueError('correlation is undefined for a constant population')
        return self._receipt('model_correlation', {'model': model, 'object_mask': o},
                             {'correlation': float(np.corrcoef(x, y)[0, 1])}, o, r)

    def colour_bands(self, image, mask, *, bands):
        a = _image(image); m = _mask(mask, a.shape)
        if a.ndim != 3:
            raise ValueError('colour measurement requires RGB')
        lum = _luminance(a); result = []
        for lo, hi in bands:
            if not np.isfinite([lo, hi]).all() or lo >= hi:
                raise ValueError('invalid brightness band')
            p = m & (lum >= lo) & (lum < hi)
            if not p.any():
                raise ValueError('empty brightness band')
            rgb = np.median(a[p], axis=0)
            if rgb[1] <= 0:
                raise ValueError('positive green denominator required')
            result.append({'lo': float(lo), 'hi': float(hi), 'count': int(p.sum()),
                           'r_g': float(rgb[0] / rgb[1]), 'b_g': float(rgb[2] / rgb[1])})
        if not result:
            raise ValueError('at least one brightness band is required')
        return self._receipt('colour_bands', {'image': image}, {'bands': result}, m)

    def star_colour(self, image, stars, mask):
        a = _image(image); m = _mask(mask, a.shape)
        s = np.asarray(stars)
        if a.ndim != 3 or s.ndim != 2 or s.shape[1] != 2 or len(s) == 0 or not np.isfinite(s).all():
            raise ValueError('RGB and nonempty (x,y) star list required')
        xy = np.rint(s).astype(int)
        if (xy < 0).any() or (xy[:, 0] >= a.shape[1]).any() or (xy[:, 1] >= a.shape[0]).any():
            raise ValueError('star outside image')
        if not m[xy[:, 1], xy[:, 0]].all():
            raise ValueError('star outside supplied population')
        rgb = a[xy[:, 1], xy[:, 0]]
        if (rgb[:, 1] <= 0).any():
            raise ValueError('positive star green denominators required')
        return self._receipt('star_colour', {'image': image, 'stars': s},
                             {'r_g': float(np.median(rgb[:, 0] / rgb[:, 1])),
                              'b_g': float(np.median(rgb[:, 2] / rgb[:, 1])), 'count': len(s)}, m)

    def clipping(self, image, mask, *, black=0., white=1., near_black=.001):
        a = _image(image); m = _mask(mask, a.shape)
        if not np.isfinite([black, white, near_black]).all() or not black <= near_black < white:
            raise ValueError('invalid intensity thresholds')
        v = a[m]
        return self._receipt('clipping', {'image': image},
                             {'low_fraction': float(np.mean(v <= black)),
                              'high_fraction': float(np.mean(v >= white)),
                              'near_black_fraction': float(np.mean(v <= near_black)),
                              'black': float(black), 'white': float(white), 'near_black': float(near_black)}, m)

    def texture(self, image, roi, *, sigma=2.):
        a = _image(image); r = _mask(roi, a.shape)
        if not np.isfinite(sigma) or sigma <= 0:
            raise ValueError('positive high-pass scale required')
        lum = _luminance(a)
        hp = lum - gaussian_filter(lum, sigma=sigma, mode='reflect')
        return self._receipt('texture', {'image': image},
                             {'sigma': _mad(hp[r]), 'filter_sigma': float(sigma)}, roi=r)

    def orientation(self, image, header, *, pixel_points, sky_points):
        """Check actual supplied image landmarks against header sky positions.

        Points must be independently identified in *this array*. Merely copying
        positions from a header cannot verify orientation. At least three
        noncollinear pairs are required. World points are (RA,Dec), degrees;
        pixel points use zero-based coordinates. North/east vectors are observed
        pixels per one header-projected pixel displacement (local affine fit).
        """
        a = _image(image); pixels = np.asarray(pixel_points, dtype=float)
        sky = np.asarray(sky_points, dtype=float)
        if pixels.ndim != 2 or pixels.shape[1] != 2 or len(pixels) < 3 or sky.shape != pixels.shape:
            raise ValueError('three or more matched pixel/sky landmarks required')
        if not np.isfinite(pixels).all() or not np.isfinite(sky).all():
            raise ValueError('nonfinite landmarks')
        if (pixels < 0).any() or (pixels[:, 0] >= a.shape[1]).any() or (pixels[:, 1] >= a.shape[0]).any():
            raise ValueError('landmark outside actual image')
        w = WCS(header).celestial
        predicted = np.asarray(w.all_world2pix(sky, 0))
        design = np.column_stack([predicted, np.ones(len(predicted))])
        if not np.isfinite(predicted).all() or np.linalg.matrix_rank(design) != 3:
            raise ValueError('degenerate landmark geometry')
        affine, _, _, _ = np.linalg.lstsq(design, pixels, rcond=None)
        matrix = affine[:2].T
        centre = np.array([[a.shape[1] / 2, a.shape[0] / 2]])
        ra, dec = w.all_pix2world(centre, 0)[0]
        if abs(dec) > 89:
            raise ValueError('polar orientation needs a spherical tangent projection')
        world = np.array([[ra, dec], [ra, dec + 1e-4], [ra + 1e-4 / np.cos(np.deg2rad(dec)), dec]])
        projected = w.all_world2pix(world, 0)
        north = matrix @ (projected[1] - projected[0]); east = matrix @ (projected[2] - projected[0])
        residual = pixels - design @ affine
        return self._receipt('orientation', {'image': image,
                              'header': np.frombuffer(header.tostring().encode(), dtype=np.uint8),
                              'pixel_points': pixels, 'sky_points': sky},
                             {'north': north.tolist(), 'east': east.tolist(),
                              'parity': float(np.sign(np.linalg.det(matrix))),
                              'header_residual_px': float(np.sqrt(np.mean((pixels - predicted)**2))),
                              'fit_residual_px': float(np.sqrt(np.mean(residual**2)))})

    def difference(self, before, after, roi):
        a = _image(before); b = _image(after); r = _mask(roi, a.shape)
        if a.shape != b.shape:
            raise ValueError('difference images must share shape and coordinate frame')
        d = (b - a)[r]
        return self._receipt('difference', {'before': before, 'after': after},
                             {'mean': float(np.mean(d)), 'rms': float(np.sqrt(np.mean(d*d))),
                              'max_abs': float(np.max(np.abs(d))), 'sigma': _mad(d)}, roi=r)

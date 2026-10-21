"""Engine-specific plans, deliberately independent of production wrappers."""
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from .core import Adapter, Capability

_WORKER = Path(__file__).with_name('worker.py')


class WorkerAdapter(Adapter):
    def __init__(self, executable=None):
        self.executable = str(Path(executable).resolve(strict=True)) if executable else None

    def command(self, invocation, params):
        self.validate(params)
        return [sys.executable, str(_WORKER), self.capability.engine,
                str(invocation.root), invocation.operation, json.dumps(params),
                self.executable or '']

    def effective_params(self, params):
        self.validate(params)
        defaults = {}
        if self.capability.engine == 'GraXpert':
            # Each operation has separate CLI defaults; include every allowed
            # setting so changes cannot hide behind omitted effective values.
            defaults = dict(strength=.5, smoothing=.5, batch_size=4, correction='Subtraction')
        elif self.capability.engine == 'ASTAP':
            defaults = dict(fov=1.3, search=180)
        elif self.capability.engine == 'SSSC':
            defaults = dict(n_ctrl=8)
        return {**defaults, **params}

    def validate(self, params):
        if params:
            raise ValueError('unknown parameters')


class GraXpert(WorkerAdapter):
    capability = Capability('GraXpert', ('background-extraction', 'denoising'),
                            ('input copy', 'binary/runtime', 'copied offline models'),
                            timeout=3600)

    def validate(self, params):
        if set(params) - {'strength', 'smoothing', 'batch_size', 'correction', 'model_version'}:
            raise ValueError('unknown GraXpert parameters')
        for key in ('strength', 'smoothing'):
            value = params.get(key, .5)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1:
                raise ValueError(f'{key} must be in [0, 1]')
        batch = params.get('batch_size', 4)
        if type(batch) is not int or not 1 <= batch <= 32:
            raise ValueError('batch_size must be in [1, 32]')
        if params.get('correction', 'Subtraction') not in ('Subtraction', 'Division'):
            raise ValueError('invalid correction')
        if 'model_version' in params:
            raise ValueError('model version is fixed per operation: BGE 1.0.1, denoise 3.0.2')


class ASTAP(WorkerAdapter):
    capability = Capability('ASTAP', ('solve',),
                            ('input copy', 'binary/runtime', 'copied star database'), timeout=120)

    def validate(self, params):
        if set(params) - {'fov', 'search'}:
            raise ValueError('unknown ASTAP parameters')
        for key, default, maximum in (('fov', 1.3, 180), ('search', 180, 180)):
            value = params.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
                raise ValueError(f'invalid {key}')

    def finish(self, invocation):
        # ASTAP may mutate pixels as well as header: import only its celestial
        # WCS into a fresh copy of archival input, never return solver pixels.
        from astropy.io import fits
        from astropy.wcs import WCS
        solved = fits.getheader(invocation.input)
        wcs = WCS(solved).celestial
        if not solved.get('PLTSOLVD') or not wcs.has_celestial:
            raise ValueError('ASTAP did not produce a celestial solution')
        with fits.open(invocation.root / 'source.fits', memmap=False) as hdul:
            header = hdul[0].header
            for key in WCS(header).to_header(relax=True):
                header.remove(key, ignore_missing=True, remove_all=True)
            header.update(wcs.to_header(relax=True))
            header['PLTSOLVD'] = True
            hdul.writeto(invocation.output, overwrite=False)


SSSC_WARNING = ('Knowledge pack #951: SSSC can green broadband targets. '
                'A successful solve is not colour-quality approval; compare an independent candidate.')


class SSSC(WorkerAdapter):
    capability = Capability('SSSC', ('sssc',),
                            ('input copy', 'SASpro runtime', 'copied enriched-star/throughput catalog'),
                            caches=('private cache/response.sqlite',),
                            database='private response SQLite only', timeout=600,
                            warnings=(SSSC_WARNING,))

    def validate(self, params):
        if set(params) - {'n_ctrl'} or type(params.get('n_ctrl', 8)) is not int or not 2 <= params.get('n_ctrl', 8) <= 32:
            raise ValueError('invalid SSSC parameters')


class Siril(WorkerAdapter):
    capability = Capability('Siril', ('crop',), ('input copy', 'binary/runtime'), timeout=120)

    def validate(self, params):
        if set(params) != {'x', 'y', 'width', 'height'} or any(type(v) is not int for v in params.values()):
            raise ValueError('integer crop rectangle required')
        if min(params['x'], params['y']) < 0 or min(params['width'], params['height']) <= 0:
            raise ValueError('invalid crop rectangle')


@dataclass(frozen=True)
class TestLockHandle:
    """Test seam only. This is NOT the future P1b lock or execution authority."""
    valid: bool
    __test__ = False


class PixInsight(WorkerAdapter):
    capability = Capability('PixInsight', ('SPCC', 'BXT', 'NXT', 'SXT', 'HDRMT', 'LHE'),
                            ('input copy', 'fake runner only'),
                            lease='P1b PixInsight lock plus studio-heavy-cpu', timeout=600,
                            backend='CPU', warnings=('P1b not validated; live PI execution disabled',))

    def authorize(self, runner, lock):
        if not isinstance(lock, TestLockHandle) or not lock.valid:
            raise ValueError('P1b lock handle required')
        if getattr(runner, 'fake', False) is not True:
            raise ValueError('live PixInsight disabled until P1b is validated')

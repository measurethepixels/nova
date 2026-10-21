"""Private subprocess entrypoint, invoked only inside LocalRunner's sandbox.

No production module import. All optional engine/scientific imports are lazy.
SSSC consumes an offline, pre-enriched catalog; it never queries Gaia/network.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def execute(engine, root, operation, params, executable):
    root = Path(root)
    inp, out = root / 'working.fits', root / 'output.fits'
    model = 'not-applicable'
    if engine in ('GraXpert', 'ASTAP', 'Siril'):
        binary = Path(executable)
        if not binary.is_file():
            raise ValueError('explicit installed engine binary required')
        version = 'sha256:' + digest(binary)
    if engine == 'GraXpert':
        # Private HOME contains only copied, preprovisioned models. No download
        # is possible in the network namespace. GraXpert 3.0.2 BGE silently
        # produces nothing with -output, so use its default adjacent filename.
        model = params.get('model_version', '1.0.1' if operation == 'background-extraction' else '3.0.2')
        models = root / 'home' / '.local' / 'share' / 'GraXpert'
        models.mkdir(parents=True)
        model_assets = sorted((root / 'assets').glob('*.onnx'))
        if len(model_assets) != 1:
            raise ValueError('exactly one offline model asset required')
        for asset in model_assets:
            if asset.suffix == '.onnx':
                command = 'bge' if operation == 'background-extraction' else 'denoise'
                dest = models / f'{command}-ai-models' / model
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(asset, dest / 'model.onnx')
        argv = [executable, str(inp), '-cli', '-cmd', operation, '-gpu', 'false']
        if operation == 'background-extraction':
            argv += ['-correction', params.get('correction', 'Subtraction'),
                     '-smoothing', str(params.get('smoothing', .5)), '-ai_version', model]
            produced = inp.with_name('working_GraXpert.fits')
        else:
            argv += ['-output', str(out.with_suffix('')), '-ai_version', model,
                     '-strength', str(params.get('strength', .5)),
                     '-batch_size', str(params.get('batch_size', 4))]
            produced = out
        subprocess.run(argv, check=True, capture_output=True)
        if produced != out:
            produced.rename(out)
        model_files = sorted((root / 'assets').glob('*.onnx'))
        if not model_files:
            raise ValueError('offline model asset required')
        model += ':' + ','.join(digest(p) for p in model_files)
    elif engine == 'ASTAP':
        result = subprocess.run([executable, '-f', str(inp), '-r', str(params.get('search', 180)),
                                '-fov', str(params.get('fov', 1.3)), '-update',
                                '-d', str(root / 'assets')], check=True, capture_output=True)
        if b'Solution found' not in result.stdout + result.stderr:
            raise ValueError('ASTAP did not report a fresh solution')
        # Parent imports WCS only, preserving the archival pixels.
    elif engine == 'Siril':
        from astropy.io import fits
        shape = fits.getdata(inp).shape
        if params['x'] + params['width'] > shape[-1] or params['y'] + params['height'] > shape[-2]:
            raise ValueError('crop outside image')
        script = root / 'crop.ssf'
        script.write_text('requires 1.2.0\nload working.fits\ncrop {x} {y} {width} {height}\nsave output.fits\n'.format(**params))
        subprocess.run([executable, '-d', str(root), '-s', str(script)], check=True, capture_output=True)
    elif engine == 'SSSC':
        import importlib.metadata
        import numpy as np
        from astropy.io import fits
        from setiastro.saspro import sssc
        version = importlib.metadata.version('setiastrosuitepro')
        catalog_path = root / 'assets' / 'catalog.json'
        catalog = json.loads(catalog_path.read_text())
        # Catalog is frozen upstream: enriched stars on exactly this file hash,
        # and explicit throughput curves. No shared Gaia/response cache access.
        if catalog['input_sha256'] != digest(root / 'source.fits'):
            raise ValueError('stale SSSC star catalog')
        stars = catalog['stars']
        curves = [np.asarray(catalog[k], dtype=float) for k in ('T_R', 'T_G', 'T_B')]
        grid = np.asarray(catalog['wavelength'], dtype=float)
        if not np.array_equal(grid, sssc._WL_GRID):
            raise ValueError('SSSC wavelength grid differs')
        cache = sssc.SessionResponseCache(str(root / 'cache' / 'response.sqlite'))
        try:
            session = catalog['session_id']
            solution = sssc._solve_system_response(stars, grid, *curves, session,
                        prior_response=cache.load_latest(session), n_ctrl=params.get('n_ctrl', 8))
            image, header = fits.getdata(inp, header=True)
            if image.ndim != 3 or image.shape[0] != 3:
                raise ValueError('SSSC requires RGB FITS')
            image = np.moveaxis(image, 0, -1)
            calibrated = sssc.apply_sssc_correction(image, solution, stars, grid, *curves)
            if not np.isfinite(calibrated).all() or not np.isfinite(solution.residual_rms):
                raise ValueError('nonfinite SSSC result')
            cache.save(solution)
            fits.writeto(out, np.moveaxis(calibrated, -1, 0), header)
        finally:
            cache.close()
        model = 'catalog-sha256:' + digest(catalog_path)
    else:
        raise ValueError('no live worker for this engine')
    # CPU is enforced by LocalRunner's private /dev, not merely requested.
    report = dict(operation=operation, backend='CPU', version=version, model=model,
                  environment='offline-bwrap-worker-sha256:' + digest(__file__), fallback=None)
    (root / 'effective.json').write_text(json.dumps(report))


if __name__ == '__main__':
    engine, root, operation, params, executable = sys.argv[1:]
    execute(engine, root, operation, json.loads(params), executable)

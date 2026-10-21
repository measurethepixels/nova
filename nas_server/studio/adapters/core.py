"""Copy-only engine execution and explicit effects; no production imports.

Bubblewrap is mandatory for real execution. Injected runners are trusted test
capabilities, not a sandbox. Same-UID concurrent adversaries are out of scope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def confined(root, path):
    """Reject symlinks in *any* component, including internal symlinks."""
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('path outside confined directory')
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('symlink in confined path')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('path outside confined directory')
    return path


def check_tree(root):
    for path in root.rglob('*'):
        confined(root, path)
        if path.is_file() and path.stat().st_nlink != 1:
            raise ValueError('hard links forbidden in confined directory')
        if not path.is_file() and not path.is_dir():
            raise ValueError('special file forbidden in confined directory')


@dataclass(frozen=True)
class Capability:
    engine: str
    operations: tuple[str, ...]
    reads: tuple[str, ...]
    # Current sandbox matches isolated_replay: host readable, writes confined.
    host_reads: str = 'read-only host filesystem (no confidentiality isolation)'
    writes: tuple[str, ...] = ('private run directory',)
    caches: tuple[str, ...] = ('private HOME/XDG cache',)
    config: tuple[str, ...] = ('private settings.json',)
    database: str = 'none'
    network: bool = False
    spend: bool = False
    lease: str = 'studio-heavy-cpu'
    timeout: int = 120
    backend: str = 'CPU'
    fallbacks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def selection_candidate(self, identity, *, states, channels, data_kinds, available,
                            licensed, seconds_per_mpx, cost_basis, exclusion=None):
        """Pre-run assessment; availability/licence/cost are trusted inventory inputs."""
        return dict(identity=identity, capability=asdict(self), states=list(states),
                    channels=list(channels), data_kinds=list(data_kinds), available=available,
                    licensed=licensed, seconds_per_mpx=seconds_per_mpx, cost_basis=cost_basis,
                    exclusion=exclusion)


@dataclass(frozen=True)
class Effective:
    operation: str
    backend: str
    version: str
    model: str
    environment: str
    fallback: str | None = None


@dataclass(frozen=True)
class Invocation:
    root: Path
    input: Path
    output: Path
    cache: Path
    settings: Path
    operation: str
    backend: str
    argv: tuple[str, ...]
    timeout: int
    environment: dict[str, str]


@dataclass(frozen=True)
class RunReceipt:
    root: Path
    input: Path
    output: Path | None
    input_sha256: str
    output_sha256: str | None
    capability: Capability
    operation: str
    effective: Effective | None
    status: str
    error: str | None
    elapsed: float
    selection: str | None = None
    full_resolution: bool = True

    def record(self, ledger, step):
        """Bind a measured Step to the actual FITS arrays and execution identity.

        Frozen metric contracts and measurements are supplied by the trusted
        caller. File hashes are separate from the ledger's canonical array hashes.
        Failed attempts remain visible; they cannot be converted into kept steps.
        """
        if step.full_resolution != self.full_resolution:
            raise ValueError('step resolution differs from executed resolution')
        if self.full_resolution:
            if ledger.selection_gate is None or step.selection != self.selection:
                raise ValueError('executed selection authorization required')
            ledger.selection_gate.validate_step(step)
        from astropy.io import fits
        from ..measure import array_hash
        def image_hash(path):
            data = fits.getdata(path)
            if data.ndim == 3 and data.shape[0] == 3:
                import numpy as np
                data = np.moveaxis(data, 0, -1)
            return array_hash(data)
        if file_hash(self.input) != self.input_sha256:
            raise ValueError('copied input changed after receipt')
        if step.inputs.get('image') != image_hash(self.input):
            raise ValueError('step input differs from executed image')
        if self.output:
            if file_hash(self.output) != self.output_sha256 or step.output_hash != image_hash(self.output):
                raise ValueError('step output differs from executed image')
        elif step.output_hash is not None or step.decision != 'failed':
            raise ValueError('failed run cannot supply output or be kept')
        if step.op != self.operation:
            raise ValueError('step operation differs from executed operation')
        if self.effective:
            step = replace(step, tool=self.capability.engine + ':' + self.effective.version,
                           model=self.effective.model, environment=(self.effective.backend if self.full_resolution else self.effective.environment))
        step = replace(step, terminal_status=self.status,
                       params={**step.params, 'engine_receipt': self.to_dict()})
        return ledger.append(step)

    def to_dict(self):
        data = asdict(self)
        for key in ('root', 'input', 'output'):
            data[key] = str(data[key]) if data[key] is not None else None
        return data


class LocalRunner:
    """Offline, CPU-only subprocess runner. No shell and no host device access.

    Read-only host mounts expose tool/model/runtime files; no confidentiality
    isolation is claimed. Child writes are limited to the private run directory
    and ephemeral /tmp and /dev. Child cannot reach host network or GPU devices.
    """
    fake = False

    def __call__(self, invocation):
        bwrap = shutil.which('bwrap')
        if bwrap is None:
            raise RuntimeError('bubblewrap required; refusing unconfined execution')
        args = [bwrap, '--die-with-parent', '--unshare-all', '--ro-bind', '/', '/',
                '--bind', str(invocation.root), str(invocation.root),
                '--tmpfs', '/tmp', '--bind', str(invocation.root), str(invocation.root),
                '--tmpfs', '/run', '--proc', '/proc', '--dev', '/dev', '--chdir', str(invocation.root),
                '--', *invocation.argv]
        subprocess.run(args, env=invocation.environment, timeout=invocation.timeout,
                       check=True, capture_output=True, text=True)
        report = confined(invocation.root, invocation.root / 'effective.json')
        return Effective(**json.loads(report.read_text()))


class Adapter:
    capability: Capability

    def command(self, invocation, params):
        raise NotImplementedError

    def finish(self, invocation):
        pass

    def effective_params(self, params):
        return dict(params)

    def run(self, source, root, *, operation, lease: Callable[[], bool],
            runner=None, params=None, provision=None, lock=None,
            preview=False, selection_gate=None, execution=None, evidence=None):
        """A fresh root per attempt; provision contains copied offline assets only.

        lease is a trusted live validity callback, checked before and after work.
        This package does not acquire leases or assert production is idle.
        """
        if type(preview) is not bool:
            raise ValueError('explicit preview flag required')
        if not preview and (selection_gate is None or execution is None):
            raise ValueError('selection preflight required before full-resolution execution')
        cap = self.capability
        if operation not in cap.operations:
            raise ValueError('unsupported operation')
        params = dict(params or {})
        if not preview:
            params = self.effective_params(params)
        runner = runner or LocalRunner()
        self.authorize(runner, lock)
        if not callable(lease) or not lease():
            raise ValueError('valid resource lease required')
        source = Path(source).resolve(strict=True)
        if not source.is_file():
            raise ValueError('regular source file required')
        root = Path(root).absolute()
        confined(root, root)
        if root.exists():
            raise ValueError('fresh private run directory required')
        # Do not let writable scratch contain the original, even indirectly.
        if source.is_relative_to(root.resolve()):
            raise ValueError('source must be outside run directory')
        original_hash = file_hash(source)
        selection = None
        if not preview:
            from ..selection import frozen, require
            from ..measure import array_hash
            from astropy.io import fits
            execution = frozen(execution)
            ident = execution['identity']
            require(execution['op'] == operation and ident['tool'] == cap.engine and
                    ident['backend'] == cap.backend and ident['params'] == params,
                    'execution plan differs from adapter invocation')
            require(execution['file_sha256'] == original_hash, 'selection input file changed')
            image = fits.getdata(source)
            if image.ndim == 3 and image.shape[0] == 3:
                import numpy as np
                image = np.moveaxis(image, 0, -1)
            require(execution['geometry']['inputs']['image'] == array_hash(image),
                    'selection image differs from executed image')
            if ident['model_hash'] is not None:
                require(ident['model_hash'] in [file_hash(p) for p in (provision or {}).values()],
                        'frozen model asset not provisioned')
            executable = getattr(self, 'executable', None)
            if executable is not None:
                require(ident['build'] == 'sha256:' + file_hash(executable), 'installed build differs from frozen identity')
            selection = selection_gate.preflight(execution, **(evidence or {}))
            binding = selection_gate.binding(selection)
            assessed = [c for c in binding['inventory']['candidates']
                        if c['identity'] == ident]
            if not assessed and binding['kind'] == 'experiment':
                from ..selection import digest
                base = next((k for k, v in binding['evidence']['tuning'].items() if v['identity'] == ident), None)
                assessed = [c for c in binding['inventory']['candidates'] if digest(c['identity']) == base]
            require(len(assessed) == 1 and assessed[0]['capability'] == frozen(asdict(cap)),
                    'adapter capability differs from frozen inventory')
        root.mkdir(mode=0o700)
        for directory in ('home', 'cache', 'config', 'tmp', 'assets'):
            (root / directory).mkdir()
        inp, out = root / 'source.fits', root / 'output.fits'
        shutil.copyfile(source, inp)
        # A second copy is the only input an engine may mutate (ASTAP).
        work = root / 'working.fits'
        shutil.copyfile(inp, work)
        for name, asset in (provision or {}).items():
            if not name or Path(name).name != name or name in ('.', '..'):
                raise ValueError('asset name must be a basename')
            asset = Path(asset).resolve(strict=True)
            if not asset.is_file():
                raise ValueError('only regular offline asset files are supported')
            shutil.copyfile(asset, root / 'assets' / name)
        settings = root / 'config' / 'settings.json'
        settings.write_text('{}\n')
        env = {'PATH': os.defpath, 'HOME': str(root / 'home'),
               'XDG_CACHE_HOME': str(root / 'cache'), 'XDG_CONFIG_HOME': str(root / 'config'),
               'XDG_DATA_HOME': str(root / 'home' / '.local' / 'share'),
               'TMPDIR': str(root / 'tmp'), 'SEESTAR_SETTINGS': str(settings),
               'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': '1'}
        inv = Invocation(root, work, out, root / 'cache', settings, operation,
                         cap.backend, (), cap.timeout, env)
        started = time.monotonic()
        effective = None
        status, error = 'failed', None
        try:
            inv = replace(inv, argv=tuple(self.command(inv, params)))
            check_tree(root)
            if not preview and ident['model_hash'] is not None:
                require(ident['model_hash'] in [file_hash(p) for p in (root / 'assets').iterdir()],
                        'copied model differs from frozen identity')
            effective = runner(inv)
            if not isinstance(effective, Effective) or (effective.operation, effective.backend) != (operation, cap.backend):
                raise ValueError('effective operation/backend mismatch')
            if not preview:
                selection_gate.revalidate(selection, execution)
                if (effective.version, effective.model, effective.backend, effective.fallback) != (
                        ident['build'], ident['model'], ident['backend'], None):
                    raise ValueError('effective identity/fallback differs from selection preflight')
            if effective.fallback not in (None, *cap.fallbacks):
                raise ValueError('unpermitted fallback')
            if any(not isinstance(x, str) or not x.strip() for x in
                   (effective.version, effective.model, effective.environment)):
                raise ValueError('effective runtime identity required')
            if time.monotonic() - started > cap.timeout:
                raise TimeoutError('engine exceeded capability timeout')
            if not lease():
                status = 'expired'
                raise ValueError('resource lease lost')
            check_tree(root)
            self.finish(inv)
            check_tree(root)
            confined(root, out)
            if not out.is_file():
                raise ValueError('engine produced no output')
            if file_hash(inp) != original_hash or file_hash(source) != original_hash:
                raise ValueError('original or archival input changed')
            status = 'completed'
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        if file_hash(source) != original_hash:
            status, error = 'failed', 'original changed during engine execution'
        receipt = RunReceipt(root, inp, out if status == 'completed' else None,
                             original_hash, file_hash(out) if status == 'completed' else None,
                             cap, operation, effective, status, error, time.monotonic() - started,
                             selection, not preview)
        confined(root, root / 'receipt.json').write_text(json.dumps(receipt.to_dict(), indent=2) + '\n')
        return receipt

    def authorize(self, runner, lock):
        pass

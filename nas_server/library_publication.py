"""Acceptance-aware immutable library artifacts (issue #1024).

Lock order: broker state.lock, then SQLite. Never invoke a broker RPC while
holding that lock. Post-acceptance copies use persisted hashes, not live tokens.
"""
from __future__ import annotations
from contextvars import ContextVar
from collections import OrderedDict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import uuid

from nas_server.pi_owner import _atomic_write, _fsync_dir, CommitRefused


# Execution context, never persisted as user configuration.
WORKER_PUBLICATION = ContextVar("worker_publication", default=False)
log = logging.getLogger(__name__)


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def content(path):
    path = Path(path)
    return {'name': path.name, 'size': path.stat().st_size, 'sha256': digest(path)}


# Process-local read acceleration only. Acceptance/publication/repair always
# hash in full; no cached decision grants write authority. Include ctime to catch
# same-size writes even if the writer restores mtime, and inode for replacement.
_READ_CACHE_LIMIT = 4096
_read_hash_cache = OrderedDict()
_read_cache_lock = threading.Lock()
_read_verify_locks = [threading.Lock() for _ in range(32)]


def _read_key(path, proof, *, renamed=False):
    p = Path(path)
    stat = p.stat()
    if (not p.is_file() or stat.st_size != proof['size']
            or (not renamed and p.name != proof['name'])):
        return None
    return (os.path.abspath(p), proof['size'], proof['sha256'], stat.st_dev,
            stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)


def _remember_read_hash(key):
    with _read_cache_lock:
        _read_hash_cache[key] = None
        _read_hash_cache.move_to_end(key)
        while len(_read_hash_cache) > _READ_CACHE_LIMIT:
            _read_hash_cache.popitem(last=False)


def _cached_read_hash(key):
    with _read_cache_lock:
        if key not in _read_hash_cache:
            return False
        _read_hash_cache.move_to_end(key)
        return True


def matches(path, proof, *, renamed=False):
    """Full content verification, including on all acceptance/repair paths."""
    try:
        before = _read_key(path, proof, renamed=renamed)
        if before is None or digest(path) != proof['sha256']:
            return False
        if _read_key(path, proof, renamed=renamed) != before:
            return False  # Changed while hashing: never cache or bless that snapshot.
        _remember_read_hash(before)
        return True
    except (OSError, KeyError, TypeError):
        return False


def _matches_for_read(path, proof, *, renamed=False):
    """Reuse a full verification only while identity/size/timestamps stay exact."""
    try:
        key = _read_key(path, proof, renamed=renamed)
        if key is None:
            return False
        if _cached_read_hash(key):
            return True
        # Collapse simultaneous cold requests for the same file. Hot readers
        # never wait for an unrelated cold hash under the cache bookkeeping lock.
        with _read_verify_locks[hash(key) % len(_read_verify_locks)]:
            key = _read_key(path, proof, renamed=renamed)
            if key is None:
                return False
            return _cached_read_hash(key) or matches(path, proof, renamed=renamed)
    except (OSError, KeyError, TypeError):
        return False


def allocate_run(parent, name):
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000000):
        p = parent / (name if n == 1 else f'{name}_{n}')
        try:
            p.mkdir()
            _fsync_dir(parent)
            return p
        except FileExistsError:
            continue
    raise FileExistsError(name)


def generation(path):
    m = re.search(r'_g(\d+)_.+_stack$', Path(path).stem)
    return int(m[1]) if m else 1


def stack_base(path):
    return re.sub(r'_g\d+(?=_.+_stack$)', '', Path(path).stem)


def stack_eligible(path, *, ignore_pending=False):
    p = Path(path)
    if not p.is_file() or p.with_suffix('.retired').exists():
        return False
    if not ignore_pending and any(p.parent.glob('.nova-pi-txn-*.pending')):
        return False
    manifest = p.parent / 'inputs.manifest.json'
    if manifest.exists():
        try:
            entries = json.loads(manifest.read_text())['files']
            return any(e['name'] == p.name and matches(p, e) for e in entries)
        except (ValueError, KeyError, TypeError, OSError):
            return False
    if generation(p) == 1:
        return True
    try:
        proof = json.loads(p.with_suffix('.accepted').read_text())
        return proof['generation'] == generation(p) and matches(p, proof)
    except (ValueError, KeyError, TypeError, OSError):
        return False


def accepted_stack_files(target, proc_dir):
    from nas_server.database import is_raw_stack
    worker_manifest = (Path(proc_dir) / 'inputs.manifest.json').exists()
    return sorted((p for p in Path(proc_dir).glob('*.fit*')
                   if (worker_manifest or is_raw_stack(p.name)) and stack_eligible(p)),
                  key=lambda p: (p.stat().st_mtime_ns, generation(p), p.name), reverse=True)


def accept_stack(path):
    p = Path(path)
    proof = dict(content(p), generation=generation(p))
    _atomic_write(p.with_suffix('.accepted'), json.dumps(proof))


def stage_accepted_inputs(target, src_proc, dst_proc):
    src_proc, dst_proc = Path(src_proc), Path(dst_proc)
    files = accepted_stack_files(target, src_proc)
    proofs = [dict(content(p), generation=generation(p)) for p in files]
    dst_proc.mkdir(parents=True, exist_ok=True)
    for p in dst_proc.iterdir():
        if p.is_file() and (p.suffix.lower() in {'.fit', '.fits', '.accepted', '.retired'} or p.name == 'inputs.manifest.json'):
            p.unlink()
    for src, proof in zip(files, proofs):
        dest = dst_proc / src.name
        shutil.copy2(src, dest)
        if not matches(dest, proof):
            raise CommitRefused(f'input transfer mismatch: {src}')
    _atomic_write(dst_proc / 'inputs.manifest.json', json.dumps({'files': proofs}))
    return [dst_proc / p.name for p in files]


def reserve_stack(proc_dir, name, tool=None):
    """Reserve a complete generation name; a crashed reservation is never reused."""
    proc_dir = Path(proc_dir)
    base = Path(name)
    stem = base.stem
    tool = tool or stem.rsplit('_', 2)[-2]
    ending = 'stack'
    prefix = stem[:-(len(tool) + len(ending) + 2)]
    for n in range(1, 1000000):
        candidate = proc_dir / (name if n == 1 else f'{prefix}_g{n}_{tool}_{ending}{base.suffix}')
        siblings = [candidate, candidate.with_suffix('.jpg'), candidate.with_name(candidate.stem + '_coverage.fit')]
        reservation = candidate.with_suffix('.reserved')
        try:
            fd = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        if any(p.exists() for p in siblings):
            continue
        _fsync_dir(proc_dir)
        return candidate
    raise FileExistsError(name)


def retire_stacks(target, path):
    from nas_server.database import get_conn
    path = Path(path)
    with get_conn() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM processed_files WHERE target=? AND retired_at IS NULL ORDER BY created_at DESC,id DESC', (target,))
                if stack_base(r['filename']) == stack_base(path)]
    for row in rows[2:]:
        p = Path(row['file_path'])
        _atomic_write(p.with_suffix('.retired'), json.dumps({'id': row['id'], 'path': str(p)}))
    reconcile_retirement(path.parent)


def reconcile_retirement(proc_dir):
    """Retry each tombstone independently; retain failed markers for retry."""
    from nas_server.database import get_conn
    for marker in Path(proc_dir).glob('*.retired'):
        try:
            rec = json.loads(marker.read_text())
            p = Path(rec['path'])
            if p.parent != Path(proc_dir) or marker != p.with_suffix('.retired'):
                raise CommitRefused('unsafe retirement marker')
            with get_conn() as c:
                row = c.execute('SELECT file_path FROM processed_files WHERE id=?', (rec['id'],)).fetchone()
                if row and row['file_path'] != str(p):
                    raise CommitRefused('retirement row changed')
                c.execute("UPDATE processed_files SET retired_at=datetime('now') WHERE id=?", (rec['id'],))
            for member in (p, p.with_suffix('.jpg'), p.with_name(p.stem + '_preview.jpg'), p.with_name(p.stem + '_coverage.fit'), p.with_suffix('.accepted')):
                member.unlink(missing_ok=True)
            _fsync_dir(p.parent)
            with get_conn() as c:
                c.execute('DELETE FROM processed_files WHERE id=? AND retired_at IS NOT NULL', (rec['id'],))
            marker.unlink()
            _fsync_dir(marker.parent)
        except Exception:
            # Keep the tombstone: this generation remains ineligible and retries.
            log.exception("Library retirement failed for marker %s", marker)


def acceptance_proof(output_path, preview_path, suffix=''):
    return {'fits': dict(content(output_path), path=str(output_path)),
            'preview': dict(content(preview_path), path=str(preview_path)), 'suffix': suffix}


def verify_proof(proof, *, for_read=False):
    check = _matches_for_read if for_read else matches
    if not all(check(proof[k]['path'], proof[k]) for k in ('fits', 'preview')):
        raise CommitRefused('accepted immutable source content changed')


def accepted_record(run_id, *, for_read=False):
    from nas_server.database import get_conn
    with get_conn() as c:
        row = c.execute('SELECT * FROM processing_runs WHERE id=? AND dry_run=0', (run_id,)).fetchone()
    if not row or not row['acceptance_json']:
        raise CommitRefused('no durable content-bound VM acceptance')
    proof = json.loads(row['acceptance_json'])
    if row['output_path'] != proof['fits']['path']:
        raise CommitRefused('acceptance output path changed')
    verify_proof(proof, for_read=for_read)
    return dict(row), proof


def materialize(run_id, proc_dir):
    from nas_server import pi_broker
    row, proof = accepted_record(run_id)
    suffix = proof['suffix']
    stem = 'auto_final' + ('_' + suffix if suffix else '')
    proc_dir = Path(proc_dir)
    manifest = {'run_id': run_id, 'fits': proof['fits'], 'preview': proof['preview']}
    source = Path(proof['fits']['path']).parent / (stem + '.publication.json')
    expected = json.dumps(manifest)
    if not source.exists():
        _atomic_write(source, expected)
    elif source.read_text() != expected:
        raise CommitRefused('publication manifest changed')
    items = [(proof['fits']['path'], proc_dir / (stem + '.fit'), False),
             (proof['preview']['path'], proc_dir / (stem + '_preview.jpg'), False),
             (source, proc_dir / (stem + '.manifest.json'), False)]
    owner = pi_broker._local_owner(None)
    txid = owner.publish_accepted(run_id, items)
    owner.complete_txn(txid)
    # Populate read metadata from a full post-publication check, not an assumed
    # successful copy. The read path will not re-read these unchanged FITS bytes.
    for path, member in ((proc_dir / (stem + '.fit'), proof['fits']),
                         (proc_dir / (stem + '_preview.jpg'), proof['preview'])):
        if not matches(path, member, renamed=True):
            raise CommitRefused('materialized accepted pair changed')


def read_final_pair(target, proc_dir, suffix=''):
    """Return the newest accepted pair, refusing corruption instead of downgrading.

    Select the authoritative row before trusting a convenience manifest. An old
    coherent root is not a substitute for a newer acceptance whose proof fails.
    A valid newest proof can still supply its immutable pair during root repair.
    Untagged legacy roots remain readable only before this target/suffix has an
    acceptance. This read exemption grants no publication or repair authority.
    """
    from nas_server.database import get_conn
    proc_dir = Path(proc_dir)
    stem = 'auto_final' + ('_' + suffix if suffix else '')
    with get_conn() as c:
        rows = c.execute(
            'SELECT id,acceptance_json FROM processing_runs WHERE target=? AND dry_run=0 '
            'AND acceptance_json IS NOT NULL AND experiment_run_id IS NULL '
            'ORDER BY finished_at DESC,id DESC', (target,)).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row['acceptance_json'])
            row_suffix = metadata['suffix']
            if not isinstance(row_suffix, str):
                raise ValueError('acceptance suffix must be a string')
        except (ValueError, KeyError, TypeError):
            # With no trustworthy suffix, even the affected output key is unknown.
            log.warning('Refusing final pair for target %s: invalid acceptance metadata for run %s',
                        target, row['id'])
            return None
        if row_suffix != suffix:
            continue
        try:
            _, proof = accepted_record(row['id'], for_read=True)
        except (OSError, ValueError, KeyError, TypeError, CommitRefused):
            log.warning('Refusing final pair for target %s suffix %r: invalid newest acceptance for run %s',
                        target, suffix, row['id'])
            return None
        fits, preview = proc_dir / (stem + '.fit'), proc_dir / (stem + '_preview.jpg')
        try:
            manifest = json.loads((proc_dir / (stem + '.manifest.json')).read_text())
            if (manifest == {'run_id': row['id'], 'fits': proof['fits'], 'preview': proof['preview']}
                    and _matches_for_read(fits, proof['fits'], renamed=True)
                    and _matches_for_read(preview, proof['preview'], renamed=True)):
                return fits, preview
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return Path(proof['fits']['path']), Path(proof['preview']['path'])
    # No acceptance exists for this output key. Preserve pre-migration readers,
    # but never reinterpret a publication-tagged root as an unaccepted legacy one.
    fits, preview = proc_dir / (stem + '.fit'), proc_dir / (stem + '_preview.jpg')
    if (not (proc_dir / (stem + '.manifest.json')).exists()
            and fits.is_file() and preview.is_file()):
        return fits, preview
    return None


def export_run(run_dir, tokens):
    """Worker transfer record; VM never trusts the worker's acceptance authority."""
    from nas_server.database import get_conn
    run_dir = Path(run_dir)
    with get_conn() as c:
        row = c.execute('SELECT * FROM processing_runs WHERE run_dir=? ORDER BY id DESC LIMIT 1', (str(run_dir),)).fetchone()
    if not row or not row['acceptance_json']:
        raise CommitRefused('worker has no completed run record')
    record = dict(row)
    proof = json.loads(record['acceptance_json'])
    verify_proof(proof)
    manifest = {'files': [dict(content(p), name=str(p.relative_to(run_dir))) for p in sorted(run_dir.rglob('*'))
                          if p.is_file() and p.name != 'outputs.manifest.json'],
                'record': record, 'pi_used': bool(tokens), 'pi_generations': tokens,
                'fits': str(Path(proof['fits']['path']).relative_to(run_dir)),
                'preview': str(Path(proof['preview']['path']).relative_to(run_dir)), 'suffix': proof['suffix']}
    _atomic_write(run_dir / 'outputs.manifest.json', json.dumps(manifest))
    return manifest


def safe_relative(name):
    p = Path(name)
    if p.is_absolute() or not p.parts or '..' in p.parts or p == Path('.'):
        raise CommitRefused('unsafe manifest path')
    return p


def _import_run_impl(staging, proc_dir):
    """Verify all transfer bytes before exposing a fresh immutable destination.

    Matching content is an idempotent retry; a different generation is allocated
    for any collision. No existing generation member is ever changed.
    """
    from nas_server.database import get_conn, save_processing_run
    staging, proc_dir = Path(staging), Path(proc_dir)
    manifest = json.loads((staging / 'outputs.manifest.json').read_text())
    if manifest['record']['target'] != proc_dir.parent.name:
        raise CommitRefused('transfer target mismatch')
    if any(p.is_symlink() for p in staging.rglob('*')):
        raise CommitRefused('transfer contains symlink')
    entries = manifest['files']
    for entry in entries:
        rel = safe_relative(entry['name'])
        p = staging / rel
        if p.is_symlink() or not p.is_file() or p.stat().st_size != entry['size'] or digest(p) != entry['sha256']:
            raise CommitRefused('transfer_failed: manifest content mismatch')
    actual = {str(p.relative_to(staging)) for p in staging.rglob('*') if p.is_file() and p.name != 'outputs.manifest.json'}
    if actual != {e['name'] for e in entries}:
        raise CommitRefused('transfer_failed: unlisted or missing output')
    runs = proc_dir / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    name = staging.name
    dest = runs / name
    if dest.exists():
        same = (dest / 'outputs.manifest.json').is_file() and (dest / 'outputs.manifest.json').read_bytes() == (staging / 'outputs.manifest.json').read_bytes()
        same = same and all((dest / e['name']).is_file() and digest(dest / e['name']) == e['sha256'] for e in entries)
        if not same:
            # Allocate fresh consistently with local creation. It remains hidden
            # until the complete verified directory is renamed into this slot.
            dest = allocate_run(runs, name)
        else:
            staging = dest
    if staging != dest:
        # The caller stages on the destination filesystem, so this is one rename.
        if not dest.exists():
            try:
                dest.mkdir()
            except FileExistsError:
                dest = allocate_run(runs, name)
        for member in staging.iterdir():
            # This invocation alone owns the newly allocated empty directory.
            os.rename(member, dest / member.name)
        staging.rmdir()
        _fsync_dir(dest)
        _fsync_dir(runs)
    declaration = manifest.get('pi_used')
    tokens = manifest.get('pi_generations')
    if declaration is not False or not isinstance(tokens, list) or tokens:
        from nas_server import telegram
        telegram.send('remote PI result not published: cross-host fencing unsupported, #1037')
        raise CommitRefused('remote PI result not published: cross-host fencing unsupported, #1037')
    record = manifest['record']
    old_run = record.get('run_dir')
    def rewrite(value):
        if isinstance(value, str) and old_run and value.startswith(old_run + '/'):
            return str(dest / value[len(old_run) + 1:])
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value
    fits, preview = dest / safe_relative(manifest['fits']), dest / safe_relative(manifest['preview'])
    proof = acceptance_proof(fits, preview, manifest['suffix'])
    with get_conn() as c:
        previous = c.execute('SELECT id FROM processing_runs WHERE output_path=? AND acceptance_json IS NOT NULL ORDER BY id DESC LIMIT 1', (str(fits),)).fetchone()
    if previous:
        run_id = previous['id']
    else:
        fields = {'target', 'workflow', 'started_at', 'elapsed_s', 'critical_eval', 'dry_run', 'workflow_version', 'object_type', 'classification_source', 'classification_strength', 'experiment_run_id'}
        kwargs = {k: record.get(k) for k in fields}
        for dest_key, src_key in [('steps', 'steps_json'), ('initial_scores', 'initial_scores'), ('final_scores', 'final_scores'), ('api_diagnostics', 'api_diagnostics')]:
            value = record.get(src_key)
            kwargs[dest_key] = rewrite(json.loads(value) if isinstance(value, str) else value)
        run_id = save_processing_run(**kwargs, output_path=str(fits), run_dir=str(dest), acceptance_json=proof)
    if not record.get('dry_run') and not record.get('experiment_run_id'):
        materialize(run_id, proc_dir)
    return dest


def recover_library():
    """Repair attributable artifacts, isolating failures by transaction/target.

    Failed artifacts stay refused and retry on the next pass; they must not
    starve unrelated targets or retirement. Shared broker/DB setup still raises.
    """
    from nas_server import pi_broker
    from nas_server.database import get_conn
    owner = pi_broker._local_owner(None)
    with owner._state_lock():
        unresolved = owner.recover_txns()
        for txn in unresolved:
            try:
                if txn.get('accepted_run_id'):
                    continue
                if txn['state'] != 'FILES_COMMITTED':
                    continue
                stacks = [r for r in txn['items'] if r['dest'].endswith(('.fit', '.fits')) and not r['dest'].endswith('_coverage.fit')]
                for item in stacks:
                    p = Path(item['dest'])
                    with get_conn() as c:
                        row = c.execute('SELECT id FROM processed_files WHERE file_path=? AND retired_at IS NULL', (str(p),)).fetchone()
                    if row and digest(p) == item.get('sha256'):
                        accept_stack(p)
                        owner.complete_txn_locked(txn)
            except Exception:
                log.exception("Library stack recovery failed for transaction %s", txn.get("txid"))
    with get_conn() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM processing_runs WHERE dry_run=0 AND acceptance_json IS NOT NULL AND experiment_run_id IS NULL ORDER BY finished_at DESC,id DESC')]
        dirs = {Path(r[0]).parent for r in c.execute('SELECT file_path FROM processed_files')}
    unresolved_dirs = {str(Path(r['dest']).parent) for txn in unresolved for r in txn['items']}
    seen = set()
    invalid_targets = set()
    for row in rows:
        if row["target"] in invalid_targets:
            continue
        key = None
        try:
            proof = json.loads(row['acceptance_json'])
            key = (row['target'], proof['suffix'])
            if key in seen:
                continue
            seen.add(key)
            proc = Path(row['run_dir']).parent.parent
            pair = read_final_pair(row['target'], proc, proof['suffix'])
            stem = 'auto_final' + ('_' + proof['suffix'] if proof['suffix'] else '')
            root = proc / (stem + '.fit')
            try:
                # Do not rewrite already coherent roots, or downgrade to an older run.
                manifest = json.loads((proc / (stem + '.manifest.json')).read_text())
                coherent = manifest['run_id'] == row['id'] and pair and pair[0] == root and str(proc) not in unresolved_dirs
            except (ValueError, KeyError, OSError):
                coherent = False
            if not coherent:
                materialize(row['id'], proc)
        except Exception:
            if key is None:
                # Unknown suffix: do not let an older row overwrite this target.
                invalid_targets.add(row["target"])
            log.exception("Library publication recovery failed for target %s run %s",
                          row["target"], row["id"])
    for proc in dirs:
        try:
            reconcile_retirement(proc)
        except Exception:
            log.exception("Library retirement scan failed for directory %s", proc)


def import_inbox():
    from nas_server.config import settings
    for inbox in Path(settings['seestar_library_path']).glob('*/_processed/.incoming'):
        for stage in inbox.iterdir():
            if stage.is_dir() and not stage.name.startswith('.') and (stage / 'outputs.manifest.json').is_file():
                import_run(stage, inbox.parent)


def import_run(staging, proc_dir):
    import logging
    try:
        return _import_run_impl(staging, proc_dir)
    except Exception as exc:
        logging.getLogger(__name__).error('transfer_failed: %s: %s', staging, exc)
        failed = Path(proc_dir) / '.transfer_failed'
        failed.mkdir(exist_ok=True)
        _atomic_write(failed / (uuid.uuid4().hex + '.json'), json.dumps({'source': str(staging), 'state': 'transfer_failed', 'error': str(exc)}))
        from nas_server import telegram
        telegram.send(f'Library transfer/acceptance refused: {Path(proc_dir).parent.name}: {exc}')
        raise

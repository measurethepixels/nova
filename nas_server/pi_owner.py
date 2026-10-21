"""PixInsight ownership broker (issue #1020, contract docs/design/pi_ownership_contract.md).

NOT WIRED INTO PRODUCTION. This module is the fake-engine phase of #1020: the
ownership state machine, fencing, cgroup supervision and recovery, exercised by
tests/test_pi_owner.py against a fake engine. `pixinsight._run_pi()` still uses
its legacy path until a separately approved integration step.

Model (library-mode broker; deviation from the contract's host-daemon
recommendation, recorded on #1020 for the checker):
  * One kernel FD lock per host + PI install (`engine.lock` in a 0700 runtime
    dir). The lock inode is created once, opened O_NOFOLLOW, verified regular and
    owned by us, and never unlinked or replaced.
  * The admitted caller holds that FD from admission through termination,
    subtree-empty proof, cleanup and the final receipt.
  * Each engine run is launched with `systemd-run --user --scope`, so every
    descendant (setsid / double-fork included) is in a per-run cgroup that the
    holder can observe and kill as a unit. No process-group fallback: if the
    scope cannot be created, the run fails closed.
  * Durable state (`state.json`, atomically replaced + fsynced) carries boot ID,
    a monotonically increasing generation, the current owner record, registered
    scopes and revoked tokens. Unreadable state blocks admission.
  * If a holder dies, the kernel releases the FD but its scope may survive. The
    next acquirer must stop and prove empty every registered scope before it
    admits anything (lock acquisition alone never proves absence of an orphan).
  * Publication goes through `Lease.commit()` / `Lease.fenced_delete()`, which
    re-check the token and pending preemption under `state.lock`, so a revoked,
    expired or failed generation can never publish, even if its engine exited 0.
  * Unknown PI processes (not inside a registered scope) block admission and are
    never signalled. Nothing here kills by PID or by process name.
"""
from __future__ import annotations

import contextlib
import dataclasses
import errno
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable

PRODUCTION, STUDIO = "production", "studio"
INTERRUPTIBLE, MUST_FINISH = "interruptible", "must_finish"


class OwnershipError(RuntimeError):
    """Base class: every failure here is fail-closed (no launch, no commit)."""


class Blocked(OwnershipError):
    """Admission impossible until an operator resolves the reported condition."""


class AdmissionTimeout(OwnershipError):
    """No admission within the deadline (the contract's explicit BLOCKED result)."""


class UnknownOwner(Blocked):
    """A PI process outside any registered scope exists; it is never signalled."""


class CommitRefused(OwnershipError):
    """Fencing rejected a publication (revoked, preempted, failed or stale token)."""


@dataclasses.dataclass(frozen=True)
class Timings:
    """Contract §'Bounded preemption' (provisional test values, #1020)."""
    admission_deadline: float = 30.0
    term_after: float = 1.0          # interruptible: TERM by t0+1s
    kill_after: float = 11.0         # interruptible: KILL by t0+11s
    must_finish_grace: float = 15.0  # must-finish: safe-checkpoint window
    must_finish_kill_after: float = 25.0
    release_deadline: float = 30.0   # subtree empty + cleanup by t0+30s
    poll: float = 0.1
    descendant_drain_grace: float = 1.0  # normal launcher exit: bounded natural shutdown


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def proc_start_time(pid: int) -> int | None:
    """Field 22 of /proc/PID/stat (clock ticks since boot): PID-reuse guard."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(raw[raw.rindex(")") + 2:].split()[19])


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def publish_file(src: Path, dest: Path, *, move: bool) -> None:
    """Atomic publication of one file: rename on the same filesystem, else copy+fsync+replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move and os.stat(src).st_dev == os.stat(dest.parent).st_dev:
        os.replace(src, dest)
    else:
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        shutil.copy2(src, tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        if move:
            src.unlink()
    _fsync_dir(dest.parent)


class Scopes:
    """systemd --user scope operations. Swappable only for unit tests of logic."""

    def __init__(self, env: dict | None = None):
        self.env = dict(env or os.environ)
        self.env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["systemctl", "--user", *args], env=self.env,
                              capture_output=True, text=True, timeout=20)

    def launch_cmd(self, unit: str, cmd: list[str]) -> list[str]:
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                f"--unit={unit}", "-p", "TasksMax=4096", "--", *cmd]

    def cgroup(self, unit: str) -> str | None:
        r = self._ctl("show", f"{unit}.scope", "-p", "ControlGroup", "--value")
        cg = r.stdout.strip()
        return cg or None

    def pids(self, unit: str) -> list[int]:
        cg = self.cgroup(unit)
        if not cg:
            return []
        try:
            raw = Path(f"/sys/fs/cgroup{cg}/cgroup.procs").read_text()
        except FileNotFoundError:
            return []
        return [int(p) for p in raw.split()]

    def signal(self, unit: str, sig: str) -> None:
        self._ctl("kill", f"--signal={sig}", f"{unit}.scope")

    def stop(self, unit: str) -> None:
        self._ctl("stop", f"{unit}.scope")


class Broker:
    def __init__(self, runtime_dir: str | os.PathLike | None = None, *,
                 engine_match: Callable[[int], bool] | None = None,
                 timings: Timings = Timings(), scopes: Scopes | None = None,
                 pi_bin: str = "/opt/PixInsight/bin/PixInsight"):
        self.dir = Path(runtime_dir or f"/run/user/{os.getuid()}/nova-pi")
        self.t = timings
        self.scopes = scopes or Scopes()
        pi_real = os.path.realpath(pi_bin)
        self.engine_match = engine_match or (
            lambda pid: os.path.realpath(f"/proc/{pid}/exe") == pi_real)
        self._prepare_dir()

    # -- files ---------------------------------------------------------------
    def _prepare_dir(self) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(self.dir)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Blocked(f"runtime dir {self.dir} must be a 0700 directory owned by uid {os.getuid()}")
        (self.dir / "requests").mkdir(mode=0o700, exist_ok=True)
        (self.dir / "receipts").mkdir(mode=0o700, exist_ok=True)

    def _open_lock(self, name: str) -> int:
        """Open (creating once) a lock inode without following symlinks."""
        path = self.dir / name
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            try:
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise Blocked(f"{path} is a symlink; refusing") from e
                raise
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            os.close(fd)
            raise Blocked(f"{path} is not a regular file owned by us")
        return fd

    @contextlib.contextmanager
    def _state_lock(self):
        fd = self._open_lock("state.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load(self) -> dict:
        path = self.dir / "state.json"
        if not path.exists():
            return {"boot_id": boot_id(), "generation": 0, "owner": None,
                    "registered": [], "revoked": [], "preempt": None}
        try:
            s = json.loads(path.read_text())
            for k in ("boot_id", "generation", "owner", "registered", "revoked", "preempt"):
                s[k]
        except (ValueError, KeyError, TypeError) as e:
            raise Blocked(f"state.json unreadable or corrupt ({e}); audited recovery required") from e
        if s["boot_id"] != boot_id():          # reboot: every older token is void
            s = {"boot_id": boot_id(), "generation": s["generation"], "owner": None,
                 "registered": [], "revoked": [], "preempt": None}
        return s

    def _save(self, s: dict) -> None:
        _atomic_write(self.dir / "state.json", json.dumps(s, indent=1))

    def receipt(self, rec: dict) -> Path:
        path = self.dir / "receipts" / f"{rec['generation']:08d}-{rec['job']}.json"
        _atomic_write(path, json.dumps(rec, indent=1))
        return path

    # -- recovery and unknown owners -------------------------------------------
    def _scope_cgroups(self, units: Iterable[str]) -> set[str]:
        return {cg for u in units if (cg := self.scopes.cgroup(u))}

    def unknown_engines(self, registered: Iterable[str]) -> list[int]:
        """PIDs matching the engine that are not inside a registered scope. Read-only."""
        known = self._scope_cgroups(registered)
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            pid = int(d.name)
            try:
                if not self.engine_match(pid):
                    continue
                cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[-1]
            except OSError:
                continue
            if cg not in known:
                out.append(pid)
        return out

    def _terminate(self, unit: str, t0: float, op_class: str = INTERRUPTIBLE,
                   on_wait: Callable[[], bool] | None = None) -> None:
        """TERM, then KILL, then prove the subtree empty, all within the deadline."""
        t = self.t
        grace = t.must_finish_grace if op_class == MUST_FINISH else 0.0
        kill_at = t.must_finish_kill_after if op_class == MUST_FINISH else t.kill_after
        while grace and time.monotonic() - t0 < grace and self.scopes.pids(unit):
            if on_wait and on_wait():
                break                                   # reached its safe checkpoint
            time.sleep(t.poll)
        if self.scopes.pids(unit):
            self.scopes.signal(unit, "TERM")
        while self.scopes.pids(unit) and time.monotonic() - t0 < kill_at:
            time.sleep(t.poll)
        if self.scopes.pids(unit):
            self.scopes.signal(unit, "KILL")
        while self.scopes.pids(unit) and time.monotonic() - t0 < t.release_deadline:
            time.sleep(t.poll)
        if self.scopes.pids(unit):
            raise Blocked(f"scope {unit} still populated after {t.release_deadline}s; refusing to release")
        self.scopes.stop(unit)

    def _recover(self, s: dict) -> dict:
        """Run with the engine FD held: stop and prove empty every registered scope."""
        for unit in list(s["registered"]):
            if self.scopes.pids(unit):
                self._terminate(unit, time.monotonic())
            s["registered"].remove(unit)
        if s["owner"]:
            s["revoked"].append(s["owner"]["token"])
            s["owner"] = None
        s["preempt"] = None
        return s

    def _validate_tokens(self, s: dict, tokens: list[list]) -> None:
        for t in tokens:
            if t[0] != s["boot_id"] or t in s["revoked"]:
                raise CommitRefused(f"generation {t[1]} revoked or from another boot")
            rp = self.dir / "receipts" / f"{t[1]:08d}-{t[2]}.json"
            try:
                rec = json.loads(rp.read_text())
            except (OSError, ValueError) as e:
                raise CommitRefused(f"no durable receipt for generation {t[1]}") from e
            if rec.get("token") != t or not rec.get("valid"):
                raise CommitRefused(f"generation {t[1]} was not a valid run")

    # -- transactional publication of a logical final (#1023 review) -----------
    # One logical final = a manifest of files (+ an optional caller-side DB marker).
    # States: STAGING -> STAGED -> FILES_COMMITTED -> DONE (or ABORTED).
    #  * Every artifact is first staged as a hidden .part beside its destination and
    #    fsynced; nothing is consumer-visible until the manifest is durably STAGED.
    #  * A `.nova-pi-txn-<id>.pending` marker sits in each destination dir from STAGING
    #    until DONE; consumers that index finals (processed_scanner) skip such dirs.
    #  * Recovery (broker start, and before every publication) rolls STAGED forward,
    #    aborts STAGING back to its sources, and leaves FILES_COMMITTED awaiting the
    #    caller's DB marker (complete_txn) -- reported, never silently accepted.
    def _txn_path(self, txid: str) -> Path:
        d = self.dir / "txns"
        d.mkdir(mode=0o700, exist_ok=True)
        return d / f"{txid}.json"

    def _all_txns(self):
        journal = self.dir / 'publication-journal.json'
        records = json.loads(journal.read_text()) if journal.exists() else {}
        for p in (self.dir / 'txns').glob('*.json'):
            records.setdefault(p.stem, json.loads(p.read_text()))
        return records

    def _save_txns(self, *records):
        # Authoritative combined state. Per-transaction files are compatibility
        # projections only; supersession and repair intent share one fsync.
        all_records = self._all_txns()
        all_records.update({r['txid']: r for r in records})
        _atomic_write(self.dir / 'publication-journal.json', json.dumps(all_records))
        for r in records:
            _atomic_write(self._txn_path(r['txid']), json.dumps(r))

    def publish_accepted(self, run_id, items):
        from nas_server.library_publication import accepted_record
        with self._state_lock():
            row, proof = accepted_record(run_id)
            payload = [{'src': str(a), 'dest': str(b), 'move': False} for a, b, _ in items]
            if [proof['fits']['path'], proof['preview']['path']] != [r['src'] for r in payload[:2]]:
                raise CommitRefused('publication is not the accepted pair')
            proc = Path(row['run_dir']).parent.parent
            stem = 'auto_final' + ('_' + proof['suffix'] if proof['suffix'] else '')
            expected = [proc / (stem + '.fit'), proc / (stem + '_preview.jpg'), proc / (stem + '.manifest.json')]
            if [Path(r['dest']) for r in payload] != expected:
                raise CommitRefused('accepted materialization destination mismatch')
            dirs = {str(Path(r['dest']).parent) for r in payload}
            unresolved = self.recover_txns()
            supersedes = []
            for old in unresolved:
                if dirs & {str(Path(r['dest']).parent) for r in old['items']}:
                    if old['state'] not in ('QUARANTINED', 'FILES_COMMITTED'):
                        raise CommitRefused('unrelated unresolved publication')
                    if not {Path(r['dest']).name for r in old['items']} <= {Path(r['dest']).name for r in payload}:
                        raise CommitRefused('unrelated unresolved publication')
                    supersedes.append(old)
            txid = uuid.uuid4().hex
            recs = [dict(r, part=str(Path(r['dest']).with_name('.' + Path(r['dest']).name + '.' + txid + '.part'))) for r in payload]
            txn = {'txid': txid, 'items': recs, 'tokens': [], 'state': 'REPAIR_PENDING',
                   'accepted_run_id': run_id, 'acceptance': proof, 'removes': []}
            for old in supersedes:
                old.update(state='SUPERSEDED', superseded_by=txid)
            self._save_txns(*supersedes, txn)
            self._resume_accepted(txn)
            return txid

    def _resume_accepted(self, txn):
        from nas_server.library_publication import accepted_record
        _, proof = accepted_record(txn['accepted_run_id'])
        if proof != txn['acceptance']:
            raise CommitRefused('acceptance proof changed')
        for old in self._all_txns().values():
            if old.get('superseded_by') == txn['txid']:
                for r in old['items']:
                    self._marker(Path(r['dest']).parent, old['txid']).unlink(missing_ok=True)
        if json.loads(Path(txn['items'][2]['src']).read_text()) != {'run_id': txn['accepted_run_id'], 'fits': proof['fits'], 'preview': proof['preview']}:
            raise CommitRefused('materialization manifest is not acceptance-bound')
        for r in txn['items']:
            dest, part = Path(r['dest']), Path(r['part'])
            _atomic_write(self._marker(dest.parent, txn['txid']), txn['txid'])
            shutil.copy2(r['src'], part)
            with part.open('rb') as f:
                os.fsync(f.fileno())
            r['sha256'] = _sha256(part)
            r['staged_by'] = 'copy'
            _fsync_dir(part.parent)
        if [r['sha256'] for r in txn['items'][:2]] != [proof['fits']['sha256'], proof['preview']['sha256']]:
            raise CommitRefused('staged bytes differ from accepted proof')
        # Revalidate both immutable sources before any destination rename.
        accepted_record(txn['accepted_run_id'])
        txn['state'] = 'STAGED'
        self._save_txns(txn)
        self._roll_forward(txn)

    @staticmethod
    def _marker(dest_dir: Path, txid: str) -> Path:
        return dest_dir / f".nova-pi-txn-{txid}.pending"

    def publish_set(self, tokens: list[list], items: list[dict], *, removes: Iterable = (),
                    txid: str | None = None, _crash_after: int | None = None,
                    _crash_staging: int | None = None, _crash_before_commit: bool = False) -> str:
        """Fenced, crash-consistent publication of one logical final.

        items: [{"src", "dest", "move"}]; removes: paths that must NOT exist in the new final
        (e.g. a coverage map the new stack lacks). Removes are applied only at commit, in the
        same transaction, so the previous accepted final stays intact until then. Refused
        while any unresolved transaction overlaps these destinations. `_crash_*` exist only
        for failure-injection tests."""
        txid = txid or uuid.uuid4().hex
        removes = [str(r) for r in removes]
        with self._state_lock():
            unresolved = self.recover_txns()
            dirs = {str(Path(it["dest"]).parent) for it in items} | {str(Path(r).parent) for r in removes}
            for t in unresolved:
                tdirs = {str(Path(r["dest"]).parent) for r in t["items"]} | \
                        {str(Path(r).parent) for r in t.get("removes", [])}
                if dirs & tdirs:
                    raise CommitRefused(f"unresolved publication {t['txid']} ({t['state']}) overlaps "
                                        f"{sorted(dirs & tdirs)}; resolve it first")
            self._validate_tokens(self._load(), tokens)
            missing = [str(it["src"]) for it in items if not Path(it["src"]).is_file()]
            if missing:                                   # refuse before touching any destination
                raise CommitRefused(f"publication source(s) missing: {missing}")
            recs = []
            for it in items:
                dest = Path(it["dest"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                recs.append({"src": str(it["src"]), "dest": str(dest), "move": bool(it["move"]),
                             "part": str(dest.with_name(f".{dest.name}.{txid}.part"))})
            txn = {"txid": txid, "tokens": tokens, "items": recs, "removes": removes, "state": "STAGING"}
            tp = self._txn_path(txid)
            self._save_txns(txn)
            for d in {Path(r["dest"]).parent for r in recs} | {Path(r).parent for r in removes}:
                _atomic_write(self._marker(d, txid), txid)
            for i, r in enumerate(recs):
                if _crash_staging == i:
                    raise OwnershipError("injected crash during staging")
                src, part = Path(r["src"]), Path(r["part"])
                if r["move"] and os.stat(src).st_dev == os.stat(part.parent).st_dev:
                    os.replace(src, part)
                    r["staged_by"] = "rename"
                else:
                    shutil.copy2(src, part)
                    r["staged_by"] = "copy"
                with open(part, "rb") as fh:
                    os.fsync(fh.fileno())
                r["sha256"] = _sha256(part)
                _fsync_dir(part.parent)
            txn["state"] = "STAGED"
            self._save_txns(txn)
            if _crash_before_commit:
                raise OwnershipError("injected crash after staging, before commit")
            self._roll_forward(txn, _crash_after=_crash_after)
        return txid

    def _roll_forward(self, txn: dict, *, _crash_after: int | None = None) -> None:
        try:
            if txn.get('accepted_run_id'):
                from nas_server.library_publication import accepted_record
                accepted_record(txn['accepted_run_id'])
            else:
                self._validate_tokens(self._load(), txn.get('tokens', []))
        except CommitRefused:
            txn['state'] = 'QUARANTINED'
            self._save_txns(txn)
            raise Blocked('publication authority refused during recovery')
        for r in txn["items"]:                        # verify before renaming anything
            part, dest = Path(r["part"]), Path(r["dest"])
            have = part if part.exists() else dest if dest.exists() else None
            if have is None or (r.get("sha256") and _sha256(have) != r["sha256"]):
                txn["state"] = "QUARANTINED"          # cannot complete the declared set: never accept
                self._save_txns(txn)
                raise Blocked(f"publication {txn['txid']} cannot be completed ({r['dest']}); quarantined")
        for i, r in enumerate(txn["items"]):
            part, dest = Path(r["part"]), Path(r["dest"])
            if part.exists():
                os.replace(part, dest)
                _fsync_dir(dest.parent)
            if _crash_after == i:
                raise OwnershipError("injected crash after committing artifact %d" % i)
        for rm in txn.get("removes", []):             # tombstones: part of the same commit
            Path(rm).unlink(missing_ok=True)
            _fsync_dir(Path(rm).parent)
        for r in txn["items"]:                        # cross-filesystem moves: drop sources last
            if r["move"] and r.get("staged_by") == "copy":
                Path(r["src"]).unlink(missing_ok=True)
        txn["state"] = "FILES_COMMITTED"
        self._save_txns(txn)

    def complete_txn(self, txid: str) -> None:
        """The caller's acceptance marker (e.g. DB row written): the final is now accepted."""
        with self._state_lock():
            tp = self._txn_path(txid)
            txn = self._all_txns()[txid]
            if txn["state"] != "FILES_COMMITTED":
                raise CommitRefused(f"txn {txid} is {txn['state']}, not FILES_COMMITTED")
            self.complete_txn_locked(txn)

    def complete_txn_locked(self, txn):
        txn['state'] = 'DONE'
        self._save_txns(txn)
        for d in {Path(r['dest']).parent for r in txn['items']} | {Path(r).parent for r in txn.get('removes', [])}:
            self._marker(d, txn['txid']).unlink(missing_ok=True)
            _fsync_dir(d)

    def recover_txns(self) -> list[dict]:
        """Finish or undo interrupted publications. Returns txns still awaiting acceptance."""
        pending = []
        d = self.dir / "txns"
        for txn in self._all_txns().values():
            if txn["state"] == "REPAIR_PENDING":
                try:
                    self._resume_accepted(txn)
                    self.complete_txn_locked(txn)
                except (OSError, ValueError, CommitRefused, Blocked):
                    txn['state'] = 'QUARANTINED'
                    self._save_txns(txn)
                    pending.append(txn)
            elif txn["state"] == "SUPERSEDED":
                for r in txn['items']:
                    self._marker(Path(r['dest']).parent, txn['txid']).unlink(missing_ok=True)
            elif txn["state"] == "STAGING":
                for r in txn["items"]:
                    part, src = Path(r["part"]), Path(r["src"])
                    if part.exists():
                        if r["move"] and not src.exists():
                            os.replace(part, src)        # give the source back
                        else:
                            part.unlink()
                txn["state"] = "ABORTED"
                self._save_txns(txn)
                for dd in {Path(r["dest"]).parent for r in txn["items"]} | {Path(r).parent for r in txn.get("removes", [])}:
                    self._marker(dd, txn["txid"]).unlink(missing_ok=True)
            elif txn["state"] == "STAGED":
                try:
                    if txn.get('accepted_run_id'):
                        from nas_server.library_publication import accepted_record
                        accepted_record(txn['accepted_run_id'])
                    self._roll_forward(txn)
                    if txn.get('accepted_run_id'):
                        self.complete_txn_locked(txn)
                except (Blocked, CommitRefused):
                    pass                                  # quarantined; marker stays, never indexed
                if txn['state'] != 'DONE':
                    pending.append(txn)
            elif txn["state"] == "QUARANTINED":
                pending.append(txn)
            elif txn["state"] == "FILES_COMMITTED":
                if txn.get('accepted_run_id'):
                    from nas_server.library_publication import accepted_record
                    try:
                        accepted_record(txn['accepted_run_id'])
                        self.complete_txn_locked(txn)
                    except CommitRefused:
                        pending.append(txn)
                else:
                    pending.append(txn)
        return pending

    def revoke_token(self, token: list) -> None:
        """Durably add a token to the revoked set (used by the supervisor before signalling)."""
        with self._state_lock():
            s = self._load()
            if token not in s["revoked"]:
                s["revoked"].append(token)
                self._save(s)

    # -- admission ---------------------------------------------------------------
    def admit(self, caller: str, op_class: str = INTERRUPTIBLE, *, job: str | None = None,
              deadline: float | None = None) -> "Lease":
        """`deadline` overrides the admission deadline (production waiting FIFO behind
        another production job may wait up to its own timeout; studio keeps the default)."""
        if caller not in (PRODUCTION, STUDIO) or op_class not in (INTERRUPTIBLE, MUST_FINISH):
            raise OwnershipError("undeclared caller or operation class; refused")
        job = job or uuid.uuid4().hex
        req = self.dir / "requests" / f"{0 if caller == PRODUCTION else 1}-{time.time_ns():020d}-{job}"
        body = json.dumps({"caller": caller, "pid": os.getpid(), "start": proc_start_time(os.getpid())})
        if caller == PRODUCTION:
            # Arrival is one serialized event: the request file and the preemption intent
            # land under state.lock, the same boundary Lease.commit() checks (#1022 review).
            with self._state_lock():
                req.write_text(body)
                s = self._load()
                if s["owner"] and s["owner"]["caller"] == STUDIO and not s["preempt"]:
                    s["preempt"] = {"by": job, "at_wall": time.time()}
                    self._save(s)
        else:
            req.write_text(body)
        start = time.monotonic()
        fd = self._open_lock("engine.lock")
        try:
            while True:
                if self._my_turn(req, caller):
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        if caller == PRODUCTION:
                            self._request_preempt(job)
                    else:
                        try:
                            return self._grant(fd, caller, op_class, job)
                        except BaseException:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                            raise
                limit = self.t.admission_deadline if deadline is None else deadline
                if time.monotonic() - start > limit:
                    raise AdmissionTimeout(f"{caller} not admitted within {limit}s")
                time.sleep(self.t.poll)
        except BaseException:
            os.close(fd)
            raise
        finally:
            req.unlink(missing_ok=True)

    @staticmethod
    def _live_request(path: Path) -> bool:
        """A request whose admitter died (PID gone or reused) is ignored and removed."""
        try:
            r = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        if proc_start_time(r["pid"]) != r["start"]:
            path.unlink(missing_ok=True)
            return False
        return True

    def production_pending(self) -> bool:
        """True if any live production admission request exists (call under state.lock)."""
        return any(p.name.startswith("0-") and self._live_request(p)
                   for p in (self.dir / "requests").iterdir())

    def _my_turn(self, req: Path, caller: str) -> bool:
        """Production first (FIFO); studio only with no pending production request."""
        pending = sorted(p.name for p in (self.dir / "requests").iterdir()
                         if p == req or self._live_request(p))
        if caller == STUDIO and any(n.startswith("0-") for n in pending):
            return False
        same = [n for n in pending if n[0] == req.name[0]]
        return bool(same) and same[0] == req.name

    def _request_preempt(self, job: str) -> None:
        with self._state_lock():
            s = self._load()
            if s["owner"] and s["owner"]["caller"] == STUDIO and not s["preempt"]:
                s["preempt"] = {"by": job, "at_wall": time.time()}
                self._save(s)

    def _grant(self, fd: int, caller: str, op_class: str, job: str) -> "Lease":
        with self._state_lock():
            s = self._recover(self._load())
            unknown = self.unknown_engines(s["registered"])
            if unknown:
                self._save(s)
                raise UnknownOwner(f"unregistered engine process(es) {unknown}; not signalled")
            s["generation"] += 1
            token = [s["boot_id"], s["generation"], job]
            s["owner"] = {"token": token, "caller": caller, "op_class": op_class,
                          "holder_pid": os.getpid(), "holder_start": proc_start_time(os.getpid()),
                          "unit": None, "state": "ADMITTING"}
            self._save(s)
        return Lease(self, fd, caller, op_class, job, token)


class Lease:
    def __init__(self, broker: Broker, fd: int, caller: str, op_class: str, job: str, token: list):
        self.b, self.fd, self.caller, self.op_class, self.job, self.token = broker, fd, caller, op_class, job, token
        self.unit: str | None = None
        self.exit_code: int | None = None
        self.valid = False
        self.transitions: list[tuple[str, float]] = [("ADMITTING", time.monotonic())]
        self.released = False

    def _mark(self, state: str) -> None:
        self.transitions.append((state, time.monotonic()))
        with self.b._state_lock():
            s = self.b._load()
            if s["owner"] and s["owner"]["token"] == self.token:
                s["owner"]["state"] = state
                s["owner"]["unit"] = self.unit
                self.b._save(s)

    def _revoked(self) -> bool:
        s = self.b._load()
        return (self.token in s["revoked"] or s["boot_id"] != self.token[0]
                or not s["owner"] or s["owner"]["token"] != self.token)

    def _preempt_pending(self) -> bool:
        return bool(self.b._load()["preempt"]) or self.b.production_pending()

    def _revoke_locked(self) -> None:
        """Revoke when the caller already holds state.lock."""
        s = self.b._load()
        if self.token not in s["revoked"]:
            s["revoked"].append(self.token)
            self.b._save(s)
        self.valid = False
        self.transitions.append(("REVOKING", time.monotonic()))

    def _revoke(self) -> None:
        """Durably invalidate the token before any signal is sent."""
        with self.b._state_lock():
            s = self.b._load()
            if self.token not in s["revoked"]:
                s["revoked"].append(self.token)
            self.b._save(s)
        self.valid = False
        self._mark("REVOKING")

    def run(self, cmd: list[str], *, timeout: float, env: dict | None = None,
            checkpoint_file: str | os.PathLike | None = None,
            abort: Callable[[], bool] | None = None, stdout_path: str | None = None) -> int:
        """Launch via a per-job supervisor that co-holds the engine lock (pi_supervisor.py).

        The supervisor runs in its own scope with the lock FD inherited and starts the
        engine in a separate scope. This holder only relays preemption / caller loss
        ("stop <reason>") and collects the supervisor's durable result. If this process
        dies, the supervisor sees EOF on its control pipe, revokes, terminates, proves the
        engine empty and only then releases its copy of the lock.
        """
        if self._revoked():
            raise CommitRefused("token revoked before launch")
        tag = f"g{self.token[1]}-{self.job[:8]}"
        self.unit, sup_unit = f"nova-pi-{tag}", f"nova-pi-sup-{tag}"
        self.sup_unit = sup_unit
        with self.b._state_lock():                       # register both scopes before launch
            s = self.b._load()
            s["registered"] += [self.unit, sup_unit]
            s["owner"]["unit"] = self.unit
            self.b._save(s)
        self._mark("RUNNING")
        results = self.b.dir / "results"
        results.mkdir(mode=0o700, exist_ok=True)
        result_path = results / f"{self.token[1]:08d}-{self.job}.json"
        args = {"unit": self.unit, "cmd": list(cmd), "env": env or {}, "timeout": timeout,
                "op_class": self.op_class, "timings": dataclasses.asdict(self.b.t),
                "stdout_path": str(stdout_path) if stdout_path else None,
                "runtime_dir": str(self.b.dir), "token": self.token, "result_path": str(result_path),
                "checkpoint_file": str(checkpoint_file) if checkpoint_file else None}
        repo = str(Path(__file__).resolve().parents[1])
        sup_env = dict(self.b.scopes.env, NOVA_PI_LOCK_FD=str(self.fd),
                       PYTHONPATH=repo + os.pathsep + self.b.scopes.env.get("PYTHONPATH", ""))
        sup = subprocess.Popen(
            self.b.scopes.launch_cmd(sup_unit, [sys.executable, "-m", "nas_server.pi_supervisor",
                                                json.dumps(args)]),
            env=sup_env, cwd=repo, pass_fds=(self.fd,), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL)
        sent = None
        hard_deadline = time.monotonic() + timeout + self.b.t.release_deadline + 60
        while sup.poll() is None:
            reason = None
            if self.caller == STUDIO and self._preempt_pending():
                reason = "preempted"
            elif abort and abort():
                reason = "caller_lost"
            elif time.monotonic() > hard_deadline:
                break                                    # supervisor wedged: take over below
            if reason and not sent:
                sent = reason
                self._revoke()
                self._mark("TERMINATING")
                try:
                    sup.stdin.write(f"stop {reason}\n".encode())
                    sup.stdin.flush()
                except OSError:
                    pass
            time.sleep(self.b.t.poll)
        try:
            sup.stdin.close()
        except OSError:
            pass
        result = None
        if sup.poll() is not None and result_path.exists():
            result = json.loads(result_path.read_text())
        if result is None:                               # supervisor lost: this holder still owns the FD
            self._revoke()
            self._mark("TERMINATING")
            self.b.scopes.stop(sup_unit)
            self.b._terminate(self.unit, time.monotonic())
            result = {"exit_code": None, "reason": sent or "supervisor_lost"}
        self.exit_code = result["exit_code"]
        reason = result["reason"]
        if reason and reason != "blocked" and not self._revoked():
            self._revoke()
        self._mark("VALIDATING")
        self.valid = reason is None and self.exit_code == 0 and not self._revoked()
        self.end_reason = reason or ("exit_nonzero" if self.exit_code else "ok")
        return self.exit_code

    def _check_commit(self) -> None:
        if not self.valid:
            raise CommitRefused(f"run not valid ({getattr(self, 'end_reason', 'not run')}); quarantined")
        s = self.b._load()
        if self.caller == STUDIO and (s["preempt"] or self.b.production_pending()):
            self._revoke_locked()
            raise CommitRefused("production arrived before commit; studio token revoked")
        if self._revoked():
            raise CommitRefused("token revoked or stale")

    def commit(self, staged: str | os.PathLike, dest: str | os.PathLike,
               check: Callable[[Path], bool] | None = None) -> Path:
        """Fenced, atomic publication of one staged artifact."""
        staged, dest = Path(staged), Path(dest)
        with self.b._state_lock():                       # serialized with preemption requests
            self._check_commit()
            if check and not check(staged):
                raise CommitRefused(f"output check failed for {staged}")
            with open(staged, "rb") as fh:
                os.fsync(fh.fileno())
            if os.stat(staged).st_dev == os.stat(dest.parent).st_dev:
                os.replace(staged, dest)                 # same filesystem: atomic rename, no copy
            else:
                tmp = dest.with_name(f".{dest.name}.{self.job[:8]}.part")
                shutil.copy2(staged, tmp)
                with open(tmp, "rb") as fh:
                    os.fsync(fh.fileno())
                os.replace(tmp, dest)
            _fsync_dir(dest.parent)
        self._mark("COMMITTING")
        return dest

    def fenced_delete(self, paths: Iterable[str | os.PathLike]) -> None:
        with self.b._state_lock():
            if self._revoked():
                raise CommitRefused("token revoked; destructive cleanup refused")
            for p in paths:
                Path(p).unlink(missing_ok=True)

    def release(self) -> Path:
        """Prove subtree empty, write the receipt, then release the FD lock."""
        if self.released:
            raise OwnershipError("already released")
        self._mark("CLEANING")
        for unit in (getattr(self, "sup_unit", None), self.unit):
            if unit and self.b.scopes.pids(unit):
                self._revoke()
                self.b._terminate(unit, time.monotonic())
        with self.b._state_lock():
            s = self.b._load()
            for unit in (self.unit, getattr(self, "sup_unit", None)):
                if unit in s["registered"]:
                    s["registered"].remove(unit)
            if s["owner"] and s["owner"]["token"] == self.token:
                s["owner"] = None
            s["preempt"] = None
            self.b._save(s)
        path = self.b.receipt({"generation": self.token[1], "job": self.job, "token": self.token,
                               "caller": self.caller, "op_class": self.op_class, "unit": self.unit,
                               "exit_code": self.exit_code, "end": getattr(self, "end_reason", None),
                               "valid": self.valid, "transitions": self.transitions})
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.released = True
        return path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if not self.released:
            self.release()

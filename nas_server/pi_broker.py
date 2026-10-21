"""Host PixInsight broker daemon + client (issue #1020, production-integration phase).

Ownership survives caller failure: the broker (a long-lived systemd --user
service) holds the engine lease for each job. A caller only talks to it over a
Unix socket in the 0700 runtime dir. If the caller disconnects at any point
before `release`, the broker revokes the token, terminates and proves empty the
job's scope, and only then frees the engine. If the broker itself dies, the
engine scopes (separate systemd units) survive and the restarted broker
recovers every registered scope before admitting anything (pi_owner._recover).

Callers never launch PixInsight themselves and there is no fallback: if the
broker is unreachable, `EngineSession` raises BrokerUnavailable (fail closed).

Protocol: one connection per job, newline-delimited JSON requests, one JSON
reply per request.
  {"op": "admit", "caller", "op_class", "job"}          -> {"ok", "token"}
  {"op": "run", "cmd", "env", "timeout"}                -> {"ok", "exit_code", "valid", "end"}
  {"op": "commit", "staged", "dest"}                    -> {"ok"} | {"ok": false, "refused"}
  {"op": "delete", "paths"}                             -> {"ok"} | refused
  {"op": "release"}                                     -> {"ok", "receipt"}
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import select
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

from nas_server import pi_owner as po


class BrokerUnavailable(po.OwnershipError):
    """No broker socket: PixInsight is not launched (no legacy fallback)."""


# PI generations a pipeline run consumed (valid, committed runs only). A pipeline
# entry point (auto_process, stack_target) opens a scope; _run_pi records into it;
# fenced_publish() presents them to the broker at the final publication.
_GENERATIONS: contextvars.ContextVar[list | None] = contextvars.ContextVar("nova_pi_generations", default=None)


@contextlib.contextmanager
def generation_scope():
    tok = _GENERATIONS.set([])
    try:
        yield _GENERATIONS.get()
    finally:
        _GENERATIONS.reset(tok)


def record_generation(token: list) -> None:
    scope = _GENERATIONS.get()
    if scope is not None:
        scope.append(list(token))


def _broker_call(req: dict, runtime_dir: Path | None = None) -> dict:
    path = (runtime_dir or default_runtime_dir()) / "broker.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
    except OSError as e:
        sock.close()
        raise BrokerUnavailable(f"fenced publication needs the broker at {path}: {e}") from e
    with sock, sock.makefile("rwb") as f:
        f.write((json.dumps(req) + "\n").encode())
        f.flush()
        reply = json.loads(f.readline() or b'{"ok": false, "error": "BrokerUnavailable"}')
    if not reply.get("ok"):
        err = getattr(po, reply.get("error", ""), None)
        cls = err if isinstance(err, type) and issubclass(err, po.OwnershipError) else po.CommitRefused
        raise cls(reply.get("detail", reply))
    return reply


def _local_owner(runtime_dir: Path | None) -> po.Broker:
    """In-process view of the shared ownership state (no engine is ever launched with it).
    All transaction state lives on disk under state.lock, so this and the broker daemon
    serialize on the same records."""
    return po.Broker(runtime_dir or default_runtime_dir(), engine_match=lambda pid: False)


def fenced_publish_set(items: list[tuple[str | os.PathLike, str | os.PathLike, bool]], *,
                       removes: list[str | os.PathLike] = (), runtime_dir: Path | None = None) -> str:
    """Publish one logical final (all its files) as ONE crash-consistent manifest transaction.

    items: [(src, dest, move)]; removes: tombstones applied only at commit. Returns the
    transaction id; the caller writes its acceptance marker (e.g. the DB row) and then calls
    complete_publication(txid).

    The transaction always exists (#1023 review). Generation tokens only control *fencing*:
    if this pipeline run consumed PixInsight generations the broker validates them (and an
    unreachable broker fails closed); a run that consumed none commits the same manifest
    protocol in-process with no token validation and no engine lease."""
    tokens = list(_GENERATIONS.get() or [])
    payload = [{"src": str(s), "dest": str(d), "move": bool(m)} for s, d, m in items]
    rm = [str(r) for r in removes]
    if not tokens:
        return _local_owner(runtime_dir).publish_set([], payload, removes=rm)
    return _broker_call({"op": "publish_set", "tokens": tokens, "items": payload, "removes": rm},
                        runtime_dir)["txid"]


@contextlib.contextmanager
def acceptance_fence():
    """Hold shared ownership state across the actual caller SQLite commit.

    Lock order is state.lock -> SQLite. This path never calls the daemon.
    """
    tokens = list(_GENERATIONS.get() or [])
    if not tokens:
        yield
        return
    owner = _local_owner(None)
    with owner._state_lock():
        owner._validate_tokens(owner._load(), tokens)
        yield


def consumed_generations():
    return list(_GENERATIONS.get() or [])


def complete_publication(txid: str, *, runtime_dir: Path | None = None) -> None:
    """Acceptance marker written: mark the transaction DONE (on-disk state, no daemon needed)."""
    _local_owner(runtime_dir).complete_txn(txid)


def default_runtime_dir() -> Path:
    """Shared ownership state: the user runtime dir (tmpfs, 0700) when it exists, else a
    private local fallback (hosts without a systemd user session, e.g. CI runners)."""
    if os.environ.get("NOVA_PI_RUNTIME_DIR"):
        return Path(os.environ["NOVA_PI_RUNTIME_DIR"])
    run = Path(f"/run/user/{os.getuid()}")
    return run / "nova-pi" if run.is_dir() else Path.home() / ".local" / "state" / "nova-pi"


def _peer_gone(sock: socket.socket) -> bool:
    """True once the client has closed its end (EOF) or reset the connection."""
    try:
        r, _, _ = select.select([sock], [], [], 0)
        if not r:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        broker: po.Broker = self.server.broker
        lease: po.Lease | None = None
        try:
            for raw in self.rfile:
                try:
                    req = json.loads(raw)
                    reply = self._dispatch(broker, req, lease)
                    if req.get("op") == "admit" and reply.get("ok"):
                        lease = reply.pop("_lease")
                    if req.get("op") == "release" and reply.get("ok"):
                        lease = None
                except po.OwnershipError as e:
                    reply = {"ok": False, "error": type(e).__name__, "detail": str(e)}
                except Exception as e:                       # never let a bad request kill the broker
                    reply = {"ok": False, "error": "BadRequest", "detail": repr(e)}
                try:
                    self.wfile.write((json.dumps(reply) + "\n").encode())
                    self.wfile.flush()
                except OSError:
                    return                                   # caller gone; finally reclaims the lease
                if req.get("op") in ("release", "publish_set", "complete_txn"):
                    return
        finally:
            if lease is not None and not lease.released:   # caller lost: ownership stays here
                try:
                    if lease.unit and broker.scopes.pids(lease.unit):
                        lease._revoke()
                    lease.release()
                except po.OwnershipError:
                    pass                                     # Blocked: state stays registered for recovery

    def _dispatch(self, broker: po.Broker, req: dict, lease: po.Lease | None) -> dict:
        op = req.get("op")
        if op == "admit":
            if lease is not None:
                raise po.OwnershipError("one job per connection")
            ln = broker.admit(req["caller"], req.get("op_class", po.INTERRUPTIBLE), job=req.get("job"),
                              deadline=req.get("deadline"))
            return {"ok": True, "token": ln.token, "_lease": ln}
        if op == "publish_set":                              # needs no engine lease
            return {"ok": True, "txid": broker.publish_set(req["tokens"], req["items"],
                                                           removes=req.get("removes", []))}
        if op == "complete_txn":
            broker.complete_txn(req["txid"])
            return {"ok": True}
        if lease is None:
            raise po.OwnershipError(f"{op!r} before admit")
        if op == "run":
            code = lease.run(list(req["cmd"]), timeout=float(req["timeout"]), env=req.get("env"),
                             abort=lambda: _peer_gone(self.connection), stdout_path=req.get("stdout_path"))
            return {"ok": True, "exit_code": code, "valid": lease.valid, "end": lease.end_reason}
        if op == "commit":
            lease.commit(req["staged"], req["dest"])
            return {"ok": True}
        if op == "delete":
            lease.fenced_delete(req["paths"])
            return {"ok": True}
        if op == "release":
            return {"ok": True, "receipt": str(lease.release())}
        raise po.OwnershipError(f"unknown op {op!r}")


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, broker: po.Broker, socket_path: Path):
        self.broker = broker
        socket_path.unlink(missing_ok=True)                  # the socket, never the lock inode
        super().__init__(str(socket_path), _Handler)
        os.chmod(socket_path, 0o600)


def startup_recovery(broker: po.Broker) -> None:
    """Reconcile scopes and interrupted publications left by a previous broker before
    serving anyone."""
    ln = broker.admit(po.PRODUCTION, job="broker-startup-recovery")
    ln.release()
    with broker._state_lock():
        for txn in broker.recover_txns():
            print(f"nova-pi-broker: publication {txn['txid']} awaiting acceptance "
                  f"({txn['state']}): {[r['dest'] for r in txn['items']]}", flush=True)


def serve(runtime_dir: Path | None = None) -> None:
    rt = runtime_dir or default_runtime_dir()
    broker = po.Broker(rt)
    try:
        startup_recovery(broker)
    except po.OwnershipError as e:          # e.g. an unknown PI session: serve, but every admit refuses
        print(f"nova-pi-broker startup recovery incomplete: {e}", flush=True)
    srv = BrokerServer(broker, rt / "broker.sock")
    print(f"nova-pi-broker serving on {rt / 'broker.sock'}", flush=True)
    srv.serve_forever()


class EngineSession:
    """Client side. Use as a context manager; leaving the block releases the job."""

    def __init__(self, caller: str = po.PRODUCTION, op_class: str = po.INTERRUPTIBLE, *,
                 runtime_dir: Path | None = None, job: str | None = None, connect_timeout: float = 5.0,
                 admission_timeout: float = 60.0):
        path = (runtime_dir or default_runtime_dir()) / "broker.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(connect_timeout)
        try:
            self.sock.connect(str(path))
        except OSError as e:
            self.sock.close()
            raise BrokerUnavailable(f"PixInsight broker not reachable at {path}: {e}") from e
        self.sock.settimeout(None)
        self.f = self.sock.makefile("rwb")
        self.caller, self.op_class, self.job = caller, op_class, job
        self.admission_timeout = admission_timeout
        self.token = None
        self.result: dict | None = None
        self.closed = False

    def _call(self, **req) -> dict:
        self.f.write((json.dumps(req) + "\n").encode())
        self.f.flush()
        line = self.f.readline()
        if not line:
            raise BrokerUnavailable("broker closed the connection")
        reply = json.loads(line)
        if not reply.get("ok"):
            err = getattr(po, reply.get("error", ""), None)
            cls = err if isinstance(err, type) and issubclass(err, po.OwnershipError) else po.OwnershipError
            raise cls(reply.get("detail", reply))
        return reply

    def admit(self) -> list:
        self.token = self._call(op="admit", caller=self.caller, op_class=self.op_class, job=self.job,
                                deadline=self.admission_timeout)["token"]
        return self.token

    def run(self, cmd: list[str], *, timeout: float, env: dict | None = None,
            stdout_path: str | os.PathLike | None = None) -> dict:
        self.result = self._call(op="run", cmd=cmd, timeout=timeout, env=env or {},
                                 stdout_path=str(stdout_path) if stdout_path else None)
        return self.result

    def commit(self, staged: str | os.PathLike, dest: str | os.PathLike) -> None:
        self._call(op="commit", staged=str(staged), dest=str(dest))

    def delete(self, paths) -> None:
        self._call(op="delete", paths=[str(p) for p in paths])

    def release(self) -> str:
        r = self._call(op="release")
        self.close()
        return r["receipt"]

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            for c in (self.f, self.sock):
                try:
                    c.close()
                except OSError:
                    pass

    def __enter__(self):
        self.admit()
        return self

    def __exit__(self, *exc):
        if not self.closed:
            try:
                self.release()
            except po.OwnershipError:
                self.close()                                 # broker still owns cleanup


def _cli(argv: list[str]) -> int:
    """`python -m nas_server.pi_broker [serve [DIR] | pending | complete TXID]`.

    pending: list publications not yet accepted (FILES_COMMITTED / QUARANTINED). They block
    overlapping publications until resolved. complete: mark one accepted, only after you
    have verified its acceptance marker (e.g. the processed_files DB row) is correct."""
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        serve(Path(argv[1]) if len(argv) > 1 else None)
        return 0
    owner = _local_owner(None)
    if cmd == "pending":
        with owner._state_lock():
            for t in owner.recover_txns():
                print(t["txid"], t["state"], [r["dest"] for r in t["items"]], t.get("removes", []))
        return 0
    if cmd == "complete" and len(argv) == 2:
        owner.complete_txn(argv[1])
        return 0
    print(_cli.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

"""Per-job PixInsight supervisor (issue #1020; contract: independently surviving holder).

Launched by `pi_owner.Lease.run()` inside its own `nova-pi-sup-*` systemd --user
scope (outside both the broker's cgroup and the engine's scope), with the engine
lock FD inherited. Both this process and the broker hold the same open file
description, so a broker crash does not release the engine lock: this supervisor
keeps it until the engine scope is proven empty, its children are reaped and the
terminal result is durable.

Control channel: stdin. "stop <reason>" lines come from the broker (preemption,
caller lost). EOF means the broker itself is gone: treat as caller_lost.
"""
from __future__ import annotations

import ctypes
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from nas_server import pi_owner as po

PR_SET_CHILD_SUBREAPER = 36


def _read_control(buf: list[str]) -> str | None:
    """Non-blocking read of stdin; returns 'EOF', a stop reason, or None."""
    try:
        chunk = os.read(0, 4096)
    except BlockingIOError:
        return None
    if chunk == b"":
        return "EOF"
    buf.append(chunk.decode(errors="replace"))
    text = "".join(buf)
    for line in text.splitlines():
        if line.startswith("stop "):
            return line[5:].strip() or "stopped"
    return None


def _drain_descendants(b: po.Broker, unit: str, buf: list[str]) -> str | None:
    """Allow normal launcher cleanup to finish; never signal or extend the deadline.

    Cancellation still wins during this window. A populated scope at the fixed
    deadline follows the same revocation/termination path as any other orphan.
    """
    deadline = time.monotonic() + b.t.descendant_drain_grace
    while b.scopes.pids(unit):
        ctl = _read_control(buf)
        if ctl:
            return "caller_lost" if ctl == "EOF" else ctl
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "orphaned_descendants"
        time.sleep(min(b.t.poll, remaining))
    return None


def main() -> int:
    a = json.loads(sys.argv[1])
    lock_fd = int(os.environ["NOVA_PI_LOCK_FD"])
    try:
        ctypes.CDLL(None).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except OSError:
        pass
    fl = fcntl.fcntl(0, fcntl.F_GETFL)
    fcntl.fcntl(0, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    b = po.Broker(a["runtime_dir"], timings=po.Timings(**a["timings"]), engine_match=lambda pid: False)
    unit, token = a["unit"], a["token"]
    ckpt = Path(a["checkpoint_file"]) if a.get("checkpoint_file") else None
    out = open(a["stdout_path"], "wb") if a.get("stdout_path") else None
    env = dict(b.scopes.env)
    env.update(a.get("env") or {})
    proc = subprocess.Popen(b.scopes.launch_cmd(unit, a["cmd"]), env=env, close_fds=True,   # lock FD not passed on
                            stdin=subprocess.DEVNULL, stdout=out,
                            stderr=subprocess.STDOUT if out else None)
    if out:
        out.close()
    t0 = time.monotonic()
    reason, buf = None, []
    while proc.poll() is None:
        ctl = _read_control(buf)
        if ctl == "EOF":
            reason = "caller_lost"
        elif ctl:
            reason = ctl
        elif time.monotonic() - t0 > a["timeout"]:
            reason = "timeout"
        if reason:
            b.revoke_token(token)                          # durable revocation before any signal
            try:
                b._terminate(unit, time.monotonic(), a["op_class"], (lambda: ckpt.exists()) if ckpt else None)
            except po.Blocked:
                pass                                       # handled by the empty-proof loop below
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            break
        time.sleep(b.t.poll)
    if reason is None:
        reason = _drain_descendants(b, unit, buf)
        if reason:
            b.revoke_token(token)
    if b.scopes.pids(unit):                                # survivors beyond grace = failure
        reason = reason or "orphaned_descendants"
        b.revoke_token(token)
        try:
            b._terminate(unit, time.monotonic())
        except po.Blocked:
            # Cannot prove empty (e.g. uninterruptible sleep): stay BLOCKED and keep holding
            # the engine lock until the subtree is really gone; never release over a survivor.
            po._atomic_write(Path(a["result_path"]), json.dumps({"exit_code": proc.returncode,
                                                                "reason": "blocked"}))
            while b.scopes.pids(unit):
                time.sleep(1)
    if proc.poll() is None:
        # A stop can land before the engine has entered its scope (empty cgroup at the
        # time of _terminate). Never release while our own launch is still alive.
        b.revoke_token(token)
        reason = reason or "caller_lost"
        b.scopes.signal(unit, "KILL")
        proc.kill()
        proc.wait()
        while b.scopes.pids(unit):
            time.sleep(b.t.poll)
    while True:                                            # reap anything re-parented to us
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break
    po._atomic_write(Path(a["result_path"]), json.dumps({"exit_code": proc.returncode, "reason": reason}))
    os.close(lock_fd)                                      # release only after empty proof + durable result
    return 0


if __name__ == "__main__":
    sys.exit(main())

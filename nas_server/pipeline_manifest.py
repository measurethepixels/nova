"""Durable pipeline manifest: checkpoint records for a multi-stage remote
pipeline run, so a resumed pipeline can determine what was ACTUALLY
completed and validated, not merely what was intended.

Candidate 3 of the 2026-08-23 RunPod CPU-pod planning session. Scope for
this first PR is deliberately narrow: the manifest/checkpoint module
itself, fully tested in isolation -- NOT auto_process.py integration.
auto_process() (nas_server/auto_process.py) is a single ~6300-line function
with a real per-step loop (`for step_name in _steps_queue:`) and a running
steps_applied list, so stage boundaries do conceptually exist -- but that
loop body branches heavily (physics checks, experiment variants, retries,
fallback paths) with steps_applied.append() called from more than a dozen
distinct points scattered through deeply nested conditionals. Wiring
checkpoint recording into all of those correctly, in the same PR as
designing the schema itself, is exactly the "broad internal rewrite" risk
the planning session's own stop rule for this candidate calls out. Rather
than either stopping entirely or attempting a rushed wire-up I can't
confidently verify against every branch of that function, this PR builds
and thoroughly tests the manifest module on its own -- valuable
independent of when the integration lands -- and defers integration to a
focused follow-up PR that can get the review depth (adversarial-
concurrency, per the planning session's own risk note) a change to a live
nightly-automation pipeline function deserves.

Design:

- PipelineManifest: one JSON document per pipeline run, stored wherever the
  caller wants (a local path; syncing it to/from a RemoteWorkspace via
  runpod_workspace.py's existing stage_input()/download() is a caller
  concern, not this module's -- no new infrastructure, matching this
  candidate's own "no persistent RunPod database" non-goal).
- StageRecord: one entry per pipeline stage. Every completed record
  captures the tool/engine identity, its parameters, and SHA-256 hashes of
  its input and output (and sidecar, if the stage has one) -- not just a
  boolean "done" flag, so resumability can be re-verified against the
  ACTUAL files on disk, not just trusted from a possibly-stale record.
- A stage is never "done" merely because its record says status=
  "completed" -- resumable_stages() re-verifies the output file's current
  hash, and (if given) that the caller's requested params/engine_version
  still match what was actually run.
- write_manifest_atomic() writes to a sibling temp file then os.replace()s
  it into place -- a crash mid-write leaves the ORIGINAL manifest
  untouched (never a half-written one), and start_stage() writes the
  manifest immediately after appending a "running" record, so a crash
  between start and completion leaves that stage durably recorded as
  "running" -- which resumable_stages() always treats as untrusted,
  never as evidence of either success or failure.
- claim_manifest_for_resume() is a best-effort, advisory single-resumer
  safeguard, not a real distributed lock -- consistent with
  runpod_volume_ledger.py's own documented "advisory-only" precedent for
  the same reason (no coordination service exists to make it a real lock;
  this candidate's own non-goals rule one out).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_RUNNING = "running"
_COMPLETED = "completed"
_FAILED = "failed"

# How long a resume claim is considered still-active. A crashed resumer
# never releases its claim explicitly, so a claim older than this is
# treated as abandoned rather than blocking a legitimate second attempt
# forever. Generous on purpose -- a real auto_process run can take hours;
# this is a safeguard against a genuinely concurrent duplicate attempt; not
# a tight lease.
_CLAIM_STALE_AFTER_S = 6 * 3600


def sha256_file(path: str | Path) -> str:
    """SHA-256 of a file's contents, streamed (never loads the whole file
    into memory -- a source stack or final output can be hundreds of MB to
    several GB)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class StageRecord:
    name: str
    status: str  # "running" | "completed" | "failed"
    engine: str | None = None
    engine_version: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    input_hash: str | None = None
    output_hash: str | None = None
    sidecar_hash: str | None = None
    validated: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageRecord:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PipelineManifest:
    run_id: str
    target: str
    workflow: str
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=_now_iso)
    stages: list[StageRecord] = field(default_factory=list)
    nas_ack: bool = False
    resumed_by: str | None = None
    resumed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stages"] = [s.to_dict() if isinstance(s, StageRecord) else s for s in self.stages]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineManifest:
        stages = [StageRecord.from_dict(s) for s in data.get("stages", [])]
        known = {f for f in cls.__dataclass_fields__} - {"stages"}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(stages=stages, **kwargs)


class ManifestCorruptError(ValueError):
    """Raised only by load_manifest_strict(); load_manifest() itself never
    raises on a corrupt/truncated file -- see its own docstring for why."""


def new_manifest(target: str, workflow: str, run_id: str | None = None) -> PipelineManifest:
    return PipelineManifest(run_id=run_id or uuid.uuid4().hex, target=target, workflow=workflow)


def write_manifest_atomic(path: str | Path, manifest: PipelineManifest) -> None:
    """Write to a sibling temp file, fsync it, then os.replace() it into
    place. os.replace() is atomic on POSIX (and on Windows since Python
    3.3's implementation) -- a reader can only ever see the fully-old or
    fully-new content, never a partial write, even if the process is
    killed mid-write. The temp file lives beside the target (not in a
    shared /tmp) so the final rename stays on the same filesystem -- a
    cross-filesystem rename is not atomic."""
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)  # no-op once replace() succeeds


def load_manifest(path: str | Path) -> PipelineManifest | None:
    """Load a manifest, or None if it's missing, truncated, or otherwise
    unusable. Deliberately never raises: a manifest that fails to parse is
    itself evidence of a crash mid-write (write_manifest_atomic's
    temp-file+replace strategy means a FULLY on-disk manifest is never
    truncated -- so a truncated/corrupt file can only be a leftover of a
    write that never reached the atomic replace, or external corruption)
    and the correct response is the same as "no manifest exists yet": the
    caller starts fresh, never crashes on it. Use load_manifest_strict()
    if a caller genuinely needs to distinguish "no manifest" from
    "corrupt manifest" (e.g. to alert an operator)."""
    try:
        return load_manifest_strict(path)
    except (ManifestCorruptError, FileNotFoundError):
        return None


def load_manifest_strict(path: str | Path) -> PipelineManifest:
    """Like load_manifest(), but raises ManifestCorruptError on a
    present-but-unusable file, and FileNotFoundError if it's missing --
    for a caller that needs to distinguish the two rather than treating
    both as "start fresh"."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestCorruptError(f"{p}: truncated or invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or "run_id" not in data or "stages" not in data:
        raise ManifestCorruptError(f"{p}: missing required manifest fields")
    try:
        return PipelineManifest.from_dict(data)
    except TypeError as exc:
        raise ManifestCorruptError(f"{p}: schema mismatch: {exc}") from exc


def start_stage(manifest: PipelineManifest, name: str, *, engine: str | None = None,
                engine_version: str | None = None, params: dict[str, Any] | None = None,
                input_path: str | Path | None = None) -> StageRecord:
    """Append a new "running" stage record and return it. Does NOT write
    the manifest -- callers combine this with write_manifest_atomic()
    immediately afterward (kept as two calls, not one, so a caller that
    wants to batch multiple mutations before a single write still can;
    the "write immediately after start" discipline lives in the caller,
    not enforced here)."""
    record = StageRecord(
        name=name, status=_RUNNING, engine=engine, engine_version=engine_version,
        params=dict(params or {}),
        input_hash=sha256_file(input_path) if input_path is not None else None,
        started_at=_now_iso(),
    )
    manifest.stages.append(record)
    return record


def complete_stage(stage: StageRecord, output_path: str | Path, *,
                   validated: bool, sidecar_path: str | Path | None = None) -> None:
    """Mark a stage completed in place, computing real hashes from the
    actual output file(s) -- never trusts a caller-supplied hash, so a
    caller can't accidentally (or a bug can't silently) record a hash that
    doesn't match what's really on disk."""
    stage.output_hash = sha256_file(output_path)
    stage.sidecar_hash = sha256_file(sidecar_path) if sidecar_path is not None else None
    stage.validated = validated
    stage.status = _COMPLETED
    stage.completed_at = _now_iso()


def fail_stage(stage: StageRecord, error: str) -> None:
    stage.status = _FAILED
    stage.error = error
    stage.completed_at = _now_iso()


def verify_stage_output(stage: StageRecord, output_path: str | Path, *,
                        input_path: str | Path | None = None,
                        sidecar_path: str | Path | None = None,
                        expected_params: dict[str, Any] | None = None,
                        expected_engine_version: str | None = None) -> bool:
    """Whether `stage` is genuinely safe to treat as already done -- never
    trusts stage.status alone. False whenever ANY of the following holds:
    not status == "completed" and validated; the output file is missing or
    its current hash no longer matches the recorded one (stale/tampered/
    truncated); the stage's recorded input_hash doesn't match the current
    input file (or no current input was given to check against one that
    was recorded) -- an unchanged output byte-for-byte relative to an OLD
    upstream input is not evidence the output is valid for a NEW one,
    which is exactly the class of stale checkpoint this exists to catch;
    a sidecar was recorded but is now missing or mismatched, or a sidecar
    is now expected but none was recorded; the caller's requested params
    or engine_version differ from what was actually run (a config change
    invalidates a cached result -- see this candidate's own non-goal: no
    resumability across changed recipes/tool versions unless exact
    compatibility is proven, and an exact hash/param match IS that
    proof)."""
    if stage.status != _COMPLETED or not stage.validated:
        return False
    output_path = Path(output_path)
    if not output_path.exists() or sha256_file(output_path) != stage.output_hash:
        return False
    if stage.input_hash is not None:
        # A hash was recorded for this stage's input -- fail closed rather
        # than silently resume if there's no current input to check it
        # against, or if the current input has since changed.
        if input_path is None:
            return False
        input_path = Path(input_path)
        if not input_path.exists() or sha256_file(input_path) != stage.input_hash:
            return False
    elif input_path is not None:
        # An input is being checked now but none was recorded before --
        # the requested work has changed shape since this record was made.
        return False
    if stage.sidecar_hash is not None:
        if sidecar_path is None:
            return False
        sidecar_path = Path(sidecar_path)
        if not sidecar_path.exists() or sha256_file(sidecar_path) != stage.sidecar_hash:
            return False
    elif sidecar_path is not None:
        # A sidecar is expected now but wasn't recorded as produced before
        # -- the requested work has changed shape since this record was made.
        return False
    if expected_params is not None and expected_params != stage.params:
        return False
    if expected_engine_version is not None and expected_engine_version != stage.engine_version:
        return False
    return True


def resumable_stage_names(manifest: PipelineManifest,
                          output_paths: dict[str, str | Path],
                          input_paths: dict[str, str | Path] | None = None,
                          sidecar_paths: dict[str, str | Path] | None = None) -> set[str]:
    """The set of stage names that are genuinely safe to skip on resume --
    for each stage the manifest records, only if a real output path was
    supplied for it AND verify_stage_output() confirms it. A stage the
    manifest never mentions (or that's still "running", the untrusted
    crash state) is never included.

    Pass input_paths whenever the caller can supply them -- a stage whose
    record has an input_hash but gets no current input_path here fails
    closed in verify_stage_output() rather than silently resuming against
    upstream data that may have since changed."""
    input_paths = input_paths or {}
    sidecar_paths = sidecar_paths or {}
    resumable: set[str] = set()
    for stage in manifest.stages:
        output_path = output_paths.get(stage.name)
        if output_path is None:
            continue
        if verify_stage_output(stage, output_path,
                               input_path=input_paths.get(stage.name),
                               sidecar_path=sidecar_paths.get(stage.name)):
            resumable.add(stage.name)
    return resumable


def mark_nas_ack(manifest: PipelineManifest) -> None:
    """Record that the final output was durably confirmed written to NAS
    -- deliberately separate from "the pipeline completed": a pipeline can
    finish every stage and still fail to land its output on NAS (network
    blip, disk full, etc.), and cleanup_eligible() must not be fooled by
    the former into assuming the latter."""
    manifest.nas_ack = True


def cleanup_eligible(manifest: PipelineManifest) -> bool:
    """Whether the remote workspace backing this manifest is safe to
    delete -- only once the final output has a confirmed NAS receipt, not
    merely once every stage's status looks "completed"."""
    return manifest.nas_ack


class ClaimResult:
    __slots__ = ("claimed", "reason")

    def __init__(self, claimed: bool, reason: str):
        self.claimed = claimed
        self.reason = reason

    def __repr__(self) -> str:
        return f"ClaimResult(claimed={self.claimed!r}, reason={self.reason!r})"

    def __eq__(self, other):
        return (isinstance(other, ClaimResult)
                and self.claimed == other.claimed and self.reason == other.reason)


def claim_manifest_for_resume(manifest: PipelineManifest, worker_id: str, *,
                              stale_after_s: float = _CLAIM_STALE_AFTER_S) -> ClaimResult:
    """Best-effort, ADVISORY single-resumer safeguard -- not a real
    distributed lock (see the module docstring for why one isn't available
    here). Mutates `manifest` in place to record the claim on success; the
    caller is responsible for persisting it via write_manifest_atomic()
    immediately afterward, same discipline as start_stage(). A second
    concurrent/duplicate resume attempt against the same (unwritten-back)
    in-memory manifest object will correctly see the first claim and
    refuse; a second attempt reading a FRESH manifest from disk only sees
    the claim if the first caller already wrote it back -- this module
    cannot enforce that ordering itself without a real lock."""
    if manifest.resumed_by is not None and manifest.resumed_at is not None:
        try:
            # calendar.timegm(), NOT time.mktime() -- resumed_at is written
            # by _now_iso() via time.gmtime() (UTC) with a trailing "Z".
            # time.mktime() interprets its struct_time argument as LOCAL
            # time, so on this VM (UTC-7) a fresh UTC timestamp would be
            # read as if it were ~7h in the future, making a 6h staleness
            # window take ~13h of real time to actually expire --
            # defeating the safeguard in exactly the crash/recovery path
            # it exists to protect. calendar.timegm() is mktime()'s
            # UTC-correct counterpart.
            claimed_epoch = calendar.timegm(time.strptime(manifest.resumed_at, "%Y-%m-%dT%H:%M:%SZ"))
            age_s = time.time() - claimed_epoch
        except ValueError:
            age_s = 0.0  # unparseable timestamp -- treat as fresh, don't override blindly
        if age_s < stale_after_s:
            return ClaimResult(False, f"already claimed by {manifest.resumed_by!r}")
    manifest.resumed_by = worker_id
    manifest.resumed_at = _now_iso()
    return ClaimResult(True, "claimed")

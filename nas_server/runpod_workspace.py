"""Pipeline-owned RunPod workspace: a shared prefix on the RunPod volume
that lets a caller stage one input, run a sequence of RC-Astro/ML-tools
operations against it, and hand a prior stage's output directly to the
next endpoint call -- instead of each call owning and cleaning its own
UUID prefix, forcing every intermediate result back through the caller's
local disk between stages.

Existing callers are unaffected: run_rcastro_gpu()/run_ml_tool_gpu() (see
those modules) default to their original per-call-owned-prefix behavior
when no workspace is passed. Passing a RemoteWorkspace switches a call
into workspace mode: it uses the workspace's shared prefix, accepts a
WorkspaceRef as input to skip a redundant upload, and never deletes the
workspace itself -- only the workspace's own owner (whoever created it)
may do that, via workspace.cleanup(), once every stage using it is done.

This is deliberately narrow (Candidate 1 of the RunPod CPU-pod planning
session, 2026-08-23): no CPU pod, no routing policy, no automatic
GPU-vs-CPU selection, no production queue-routing change. It is the
storage/handoff contract those things would be built on top of.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkspaceRef:
    """A key that already lives inside a RemoteWorkspace -- e.g. a prior
    stage's real output path, as reported by the worker itself. Pass this
    instead of a local Path as a call's input to skip the upload/download
    round trip for a chained operation."""
    key: str


class RemoteWorkspace:
    """Owns one shared prefix on the RunPod volume for a multi-stage
    remote pipeline segment.

    Not a context manager -- a workspace is commonly kept alive across
    several sequential calls to different endpoints (RC-Astro then
    ML-tools, or several RC-Astro stages in a row), so auto-cleanup on
    `__exit__` would be wrong for the primary use case of "several stages,
    one caller decides when they're all done." Call cleanup() explicitly.
    """

    def __init__(self, s3_client: Any, bucket: str, run_id: str | None = None):
        self._s3 = s3_client
        self.bucket = bucket
        self.run_id = run_id or uuid.uuid4().hex
        self.prefix = f"nova_runs/{self.run_id}"
        self._used_names: set[str] = set()

    def stage_input(self, local_path: str | Path, name: str = "input") -> WorkspaceRef:
        """Upload a local file into this workspace once. Returns a ref
        other calls can pass as their input to skip re-uploading."""
        local_path = Path(local_path)
        self._reserve_name(name)
        key = f"{self.prefix}/{name}{local_path.suffix}"
        self._s3.upload_file(str(local_path), self.bucket, key)
        return WorkspaceRef(key)

    def output_dir_key(self, name: str) -> str:
        """Compute (not create) the remote output_dir a stage should ask
        the worker to write into, under this workspace. Raises on a name
        collision -- two stages writing under the same name would risk one
        stage's output silently colliding with or shadowing another's."""
        self._reserve_name(name)
        return f"{self.prefix}/{name}"

    def is_reserved(self, name: str) -> bool:
        """Whether `name` is currently reserved (by stage_input() or
        output_dir_key()). Lets a caller record "was this free before I
        tried to reserve it" so it can tell, after a failure, whether IT
        was the one that reserved the name (and so may safely release it)
        versus the name already belonging to a different, still-live
        stage (which it must never release out from under)."""
        return name in self._used_names

    def release_name(self, name: str) -> None:
        """Un-reserve `name` so a legitimate retry of the same logical
        stage can reuse it after a failed attempt. Never raises --
        releasing a name that isn't currently reserved is simply a no-op.
        Callers must only release a name they confirmed (via is_reserved,
        before *and* after their own reservation attempt) that they
        themselves reserved -- see run_rcastro_gpu's retry handling.
        Blindly releasing an arbitrary name would let one stage's failure
        silently clobber a different, still-live stage's reservation."""
        self._used_names.discard(name)

    def contains(self, ref: WorkspaceRef) -> bool:
        """Whether `ref` names a key that actually lives inside this
        workspace's prefix -- not just some other workspace whose run_id
        happens to share a lexical prefix (e.g. "run1" is a *string* prefix
        of "run10", but nova_runs/run10/... is not inside nova_runs/run1/).
        A directory-exact boundary check, not a raw startswith on
        self.prefix."""
        return ref.key.startswith(f"{self.prefix}/")

    def download(self, ref: WorkspaceRef, local_path: str | Path) -> None:
        """Fetch a workspace object to local disk -- for the FINAL result a
        caller actually needs back on the NAS, not intermediate handoff
        between stages (pass WorkspaceRefs to each other for that). Raises
        ValueError if `ref` belongs to a different workspace -- a caller
        should never be able to pull another run's object out just by
        holding a stray ref."""
        if not self.contains(ref):
            raise ValueError(
                f"workspace {self.run_id}: ref {ref.key!r} does not belong to "
                f"this workspace (prefix {self.prefix!r})"
            )
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, ref.key, str(local_path))

    def cleanup(self) -> bool:
        """Delete the entire workspace prefix. Only the workspace's owner
        should call this -- if a single stage's per-call cleanup ever
        starts deleting workspace-shared keys instead of just its own
        invocation scratch, a still-pending sibling stage could lose its
        input out from under it. Never raises; returns whether it
        succeeded, matching the existing _cleanup_job_prefix/_cleanup
        best-effort convention in rcastro_gpu.py/ml_tools_gpu.py -- a
        single list_objects_v2 call, not a paginator, matching that same
        convention (a pipeline-run workspace stays well under the 1000-key
        single-page limit; the ledger's own paginated walk is a distinct
        case handling the SASpro venv's 23k+ objects, not this one).
        Revisit if a later candidate (durable checkpoints, longer remote
        segments) makes a single workspace's object count grow toward
        that limit -- this assumption is documented, not enforced.

        Queries with a trailing slash (f"{self.prefix}/", not bare
        self.prefix) -- S3 prefix matching is lexical, not directory-aware,
        so a bare "nova_runs/run1" prefix would also match a sibling
        workspace's "nova_runs/run10/..." keys and delete another live
        run's data. The trailing slash makes the boundary directory-exact."""
        try:
            query_prefix = f"{self.prefix}/"
            objects = self._s3.list_objects_v2(Bucket=self.bucket, Prefix=query_prefix)
            for obj in objects.get("Contents", []):
                self._s3.delete_object(Bucket=self.bucket, Key=obj["Key"])
            return True
        except Exception:
            return False

    def _reserve_name(self, name: str) -> None:
        if name in self._used_names:
            raise ValueError(
                f"workspace {self.run_id}: name {name!r} already used in this "
                "workspace -- pick a distinct name per stage to avoid one "
                "stage's output silently colliding with another's"
            )
        self._used_names.add(name)


def create_workspace(s3_client: Any = None, bucket: str | None = None,
                     run_id: str | None = None) -> RemoteWorkspace:
    """Construct a workspace with a real settings-backed S3 client/bucket
    when not supplied -- mirrors runpod_volume_ledger.py's s3=None/
    bucket=None injection pattern so tests never need real credentials.

    Pass `run_id` to attach to a workspace another process already created
    (e.g. a CPU-pod worker rehydrating the workspace a caller staged its
    input into before dispatch) rather than starting a fresh one."""
    if s3_client is None:
        from nas_server.rcastro_gpu import _s3_client  # noqa: PLC0415
        s3_client = _s3_client()
    if bucket is None:
        from nas_server.rcastro_gpu import _volume_bucket  # noqa: PLC0415
        bucket = _volume_bucket()
    return RemoteWorkspace(s3_client, bucket, run_id=run_id)

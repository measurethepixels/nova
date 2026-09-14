# RunPod GPU processing

NOVA can execute ML-heavy processing locally or through RunPod. Remote GPU
execution is optional: the public pipeline must remain usable without a RunPod
account or an AI API key.

## Referral disclosure

Measure the Pixels' referral link is
[runpod.io?ref=p2blsn83](https://runpod.io?ref=p2blsn83). Measure the Pixels may
receive referral credit if you create an account through that link. You can
instead visit [runpod.io](https://runpod.io/) directly; the technical setup is
the same.

Never commit RunPod API or S3 credentials. Put them only in the gitignored
private settings file described by `docs/REPLICATE.md`.

## Current evidence

The first controlled comparison used the same small M71 FITS and this sequence:

`full BXT -> NXT -> BXT correct-only -> SXT star split`

| Path | End-to-end time |
|---|---:|
| RunPod GPU | 53.454 s |
| Local CPU | 16.684 s |

The remote worker executed each model in 1.239–2.088 seconds, but the first
invocation had 22.405 seconds of queue/cold delay. Every intermediate and both
SXT outputs were finite, dimensionally valid, hashed, and WCS-preserved. The
lesson is narrow but important: remote GPU compute can be faster while the
complete small-file job is slower.

## Lossless FITS transfer policy

Raw FITS is the compatibility baseline. Automatic compression uses Zstandard
level 1 only after a candidate clears all three gates:

1. original size is at least 8 MiB;
2. the archive saves at least 10% of the bytes;
3. measured compression + compressed transfer + decompression is predicted to
   save at least one second over raw transfer.

The calculation is implemented in `nas_server/transfer_compression.py`. It
requires measured transfer throughput; cold-start and polling delays are not
part of the byte-transfer comparison because both modes incur them.

Operators must retain `raw`, `zstd`, and `auto` modes. Every compressed manifest
records original/transferred byte counts and SHA-256 values. The worker must
decode into a unique staging path, verify original size/hash, and only then
promote the FITS. Outputs apply the policy independently.

Local benchmarks on 0.53 MB through 3.18 GB FITS files found that Zstd level 3
added substantial CPU time for less than one extra percentage point on the two
NGC 7000 files. Level 1 is therefore the supported candidate. The exact default
remains provisional until raw-versus-compressed timing is measured against the
real RunPod S3 endpoint.

Reproduce the local codec study with representative files from your own system:

```bash
python scripts/benchmark_fits_compression.py \
  --output fits-compression-report.json \
  small.fit normal-stack.fit large-mosaic.fit
```

The command never alters its inputs and verifies every decoded candidate against
the source SHA-256. Treat the output as codec evidence only. It cannot establish
the network crossover without a controlled raw-versus-compressed endpoint test.

### RunPod S3 transfer crossover (measured 2026-08-23)

`scripts/benchmark_runpod_compression_transfer.py` runs the same codec
candidates through a real round trip against the production RunPod S3 volume
(upload, list-confirm, download, decode) and checks transport-exact and
scientific-validity (via `compare_fits_pixels`) on every result, cleaning up
its unique job prefix afterward. This closes the "actual raw-versus-Zstd S3
upload/download crossover" item below with real network evidence, not just
the local codec study above.

| File | raw | zstd1 | zstd3 | fpack-lossless |
|---|---:|---:|---:|---:|
| 12.45 MB | 9.99 s | **5.21 s** | 5.53 s | 12.64 s |
| 401.45 MB | 268.85 s | **73.66 s** (3.65x) | 84.27 s | 131.43 s |

Zstd level 1 wins the full round trip at both sizes measured so far, and the
margin widens with file size (the earlier local-only study undersold this:
round-trip time is dominated by the download side on a real network, not just
upload). `fpack-lossless` is never competitive for round-trip wall time despite
its smaller archive, and was worse than raw at both sizes measured.

One real gotcha found while measuring this: `boto3`'s default multipart
`max_concurrency=2` produced repeated `524 Gateway Timeout` errors on
`UploadPart` for the 401 MB file, even at a conservative 16 MiB chunk size.
`max_concurrency=1` has been reliable across every multi-hundred-MB-to-
multi-GB upload attempted against this RunPod S3 endpoint this session
(the same fix already applied to the ml-tools-worker model provisioning);
`benchmark_runpod_compression_transfer.py`'s `transfer_config()` now always
uses `max_concurrency=1`.

Reproduce against your own volume:

```bash
python scripts/benchmark_runpod_compression_transfer.py \
  --output runpod-codec-report.json \
  --codec raw --codec zstd1 --codec zstd3 --codec fpack-lossless \
  small.fit large.fit
```

## Tool-family boundary

Use one versioned request/result contract and transport, but separate worker
images for:

- RC Astro (BXT/NXT/SXT);
- GraXpert;
- Cosmic Clarity and compatible SASpro headless models;
- SyQon Prism Mini.

Separate images keep paid-license artifacts, model redistribution rules, CUDA
runtimes, and update cycles isolated. A `COMPLETED` RunPod status is not enough:
the client validates expected output/sidecars, finite data, dimensions, hashes,
and WCS before accepting a remote result, then cleans the unique job prefix.

### Passive RC-Astro worker health identity

The live RC-Astro endpoint is recorded in
`docker/rcastro-worker/deployed-image.json`. NOVA Environment Health reports
that file's immutable linux/amd64 image digest as the single deployed-worker
identity. CUDA, cuDNN, PyTorch, and ONNX Runtime declarations remain nested
metadata beneath that artifact; they are not independent inventory rows.
Routine checks read only this tracked file and never call RunPod, query a
registry, or start a billable pod. Refresh the declaration as part of an
authorized image cutover after resolving the pushed tag to its platform
digest; do not edit it merely because a candidate image was built.

## Shared pipeline workspace (Candidate 1, RunPod CPU-pod planning session, 2026-08-23)

`run_rcastro_gpu()` (`nas_server/rcastro_gpu.py`) and `run_ml_tool_gpu()`
(`nas_server/ml_tools_gpu.py`) each own a fresh per-call UUID prefix on the
shared RunPod volume by default: upload input, run, download output, delete
the whole prefix. That is unchanged and remains every existing caller's
behavior.

`nas_server/runpod_workspace.py` adds an opt-in alternative for a caller
running several stages in sequence (e.g. an RC-Astro chain feeding an
ML-tools step, or several RC-Astro stages in a row) that would otherwise
round-trip every intermediate result back through the caller's local disk
between stages for no reason -- both endpoints already share one RunPod
volume. A `RemoteWorkspace` owns one shared prefix (`nova_runs/<run_id>`)
across those calls; a `WorkspaceRef` wraps a key already living inside it
(typically a prior stage's real output, as reported by the worker itself) so
the next call can consume it directly instead of downloading then
re-uploading the same bytes.

`run_rcastro_gpu()` takes two new optional parameters, `workspace` and
`output_name`, to opt into this: pass a `RemoteWorkspace` and the call uses
its shared prefix instead of a fresh one, accepts a `WorkspaceRef` as
`input_path` to skip a redundant upload, and returns a `workspace_ref` in its
result the caller can chain into the next call. `output_path` becomes
optional in workspace mode -- when omitted, no local download happens at all.
Neither parameter changes anything about the legacy (no-workspace) call
shape. Critically, a workspace-mode call's `finally` block never deletes the
shared prefix -- doing so from inside one stage's call could destroy a still-
pending sibling stage's input or output. Only the workspace's own creator
calls `workspace.cleanup()`, once every stage using it is done.

This is intentionally narrow: no CPU pod, no routing/policy logic, no
automatic GPU-vs-CPU selection, no production queue-routing change, and (as
of this writing) `run_ml_tool_gpu()` does not yet accept a `workspace`
parameter -- only `run_rcastro_gpu()` does. It is the storage/handoff
contract later planning-session candidates (CPU pipeline-worker backend,
durable checkpoints, segment planner) build on top of, not a change to what
runs in production today.

Candidate 2 (the CPU pipeline-worker backend that consumes this workspace
contract) is documented separately in `docs/RUNPOD_CPU_WORKER.md`.

## ml-tools-worker production-size and failure-injection evidence (2026-08-23)

The `nova-ml-tools-gpu-ca` endpoint (GraXpert, Cosmic Clarity/SASpro, DarkStar,
SyQon Parallax) was smoke-tested end to end this session -- see PR #373. Two
more items from "Validation still required" below are now answered:

**Production-size local-versus-GPU chain.** `graxpert_denoise` on a real
401 MB / 6108x5477 FITS: 87.9 s worker-side execution on RunPod GPU (CUDA
execution provider confirmed engaged via the worker's own stderr). The same
operation run to completion locally on this VM's CPU-only path (no GPU on the
current development VM) took 4610 s (76 min 50 s) -- a measured 52.4x
speedup, not just a projection. This is a stronger and more
production-representative result than the earlier RC Astro small-file
comparison above: at real production scale, GPU compute time is a rounding
error next to CPU-only time, the opposite of the small-job overhead problem
that comparison found.

**Even larger scale, single job.** The same operation on a real 3.18 GB /
NGC 7000 drizzle mosaic (the same file used in the compression study above):
582.4 s (~9.7 min) worker-side GPU execution, clean success. On a real 717 MB
NGC 7000 full-resolution stack (also from the compression study): 114.5 s.
Both ran alone against an otherwise-idle endpoint.

### Concurrent-load stress test (2026-08-23): two real findings

Henry asked for the full 12-operation matrix (Cosmic Clarity x5, DarkStar,
GraXpert x3, Parallax x3) against all three mid/large representative files
(12.45 MB, 111.93 MB, 717.95 MB -- the same files used in the compression
study above, submitted with up to 7 jobs running concurrently against the
ml-tools endpoint), plus the RC Astro BXT->NXT->BXT-correct->SXT chain
against the 717.95 MB file. This surfaced two real, unplanned findings worth
recording alongside the timing data:

**A false-negative race in `worker_core.py`, since root-caused and fixed
properly (superseding the "fixed" note originally written here).** Of 26
"FAILED" results in the 36-job matrix, 12 were actually successful -- the
subprocess's own log said `Saved FITS image to: <output_path>` with
`returncode 0`, but `run_operation()`'s `output_path.is_file()` check, taken
immediately after `subprocess.run()` returns, sometimes didn't see it yet.
The first fix (a short retry-with-backoff, 0.2s-3s total) was insufficient
-- the same race reappeared, still exceeding an even wider 18s retry budget
on a *solo*, non-concurrent 717.95 MB run. Root cause: SASpro's own
`save_image()` (`setiastro/saspro/legacy/image_manager.py`) unconditionally
strips whatever extension is requested and writes `.fits`, so every
`cosmic_*`/`darkstar` call whose caller asked for a different suffix (e.g.
the real archive's `.fit` files) was polling for a path that was never going
to exist -- not a transient visibility lag at all for those cases. Fixed by
having `_cosmic_command()`/`_darkstar_command()` track the true `.fits`
output name separately (the same pattern already used for GraXpert's own
`{stem}_GraXpert.fits` divergence), and folding the move-from-real-name step
into the retry loop too. Verified against the live endpoint (v1.6.0,
2026-08-23): `cosmic_correct` on the same 717.95 MB `.fit` file completed in
248.1s with the relocated `.fit`-suffixed output confirmed via a direct S3
`HeadObject` (717,952,320 bytes, matching the input) and the intermediate
`.fits` temp file confirmed gone (moved, not copied). This matters beyond
noisy benchmark data: the NOVA GPU Compute Routing and Experiment Search
Plan note (below) explicitly relies on this exact success/failure signal to
decide whether a parallel experiment candidate survived -- a false failure
here would have silently discarded a good result.

**Correction: the "real, non-race capacity limit at 717.95 MB" conclusion
below was wrong.** This section originally reported 10 of 12 operations
genuinely failing at this file size under concurrent load, with
`graxpert_denoise`'s code-255 crash "consistent with an OOM kill," and
concluded it was a real per-file-size ceiling distinct from the write-
visibility race above. Investigating the ceiling further (in order to narrow
it down) surfaced the actual cause: the shared 20 GB network volume was
genuinely full (20.70 GB used, 103.5%) from accumulated scratch files left
by earlier crashed cleanup attempts (they failed during the initial listing
step, before deleting anything). `OSError: Not enough space on disk` from a
full volume under concurrent large writes produces exactly this symptom
pattern -- partial/empty outputs, a mid-write crash on the operation that
happened to need the most scratch space -- with no GPU or memory limit
involved at all. The volume was cleaned back to 8.16 GB/40.8% (legitimate
`ml-models/` + `rcastro-config/` baseline). This does not mean no real
per-file-size concurrency ceiling exists -- that question is still open, see
"Validation still required" below -- only that this specific piece of
evidence for one doesn't hold up. RC Astro's SXT stars-sidecar partial
failure on the same run is unaffected by this correction and remains an open
item to root-cause separately (same "Validation still required" list).

Three distinct bugs, for the record, all found chasing what first looked
like one flaky test run: (1) the false-negative write-visibility race
(real, fixed via retry), (2) the SASpro `.fits`-only output bug (real,
fixed via correct extension tracking -- this was the actual majority cause
of bug #1's worst cases), (3) the volume-space exhaustion (real, a one-time
operational cleanup issue, not a code bug, but the actual cause of the
capacity-limit finding this section originally reported).

**Re-run, 2026-08-23: no real per-file-size concurrency ceiling found up to
the endpoint's full worker count.** With v1.6.0 deployed and the volume
clean, `cosmic_correct` on the same 717.95 MB file was tested at
concurrency levels 1 through 7 (the ml-tools endpoint's `workersMax`),
cross-checking real output via direct S3 `HeadObject`/listing at each
level rather than trusting the API's own status. The first attempt at this
(same day) again reported failures starting at level 5 -- and once again
the cause was the *test script itself* filling the shared volume: it never
cleaned up a level's ~717 MB output files before starting the next, so by
level 5 it had accumulated 10 leftover files (~7.2 GB) on top of the
~11 GB baseline, then tried to write 5 more concurrently, genuinely
exceeding the 20 GB volume (`OSError: ... [Errno 122] Disk quota
exceeded`, confirmed via each failing job's actual returned error, not
inferred). This is the exact same failure class as the original
mis-diagnosed "OOM kill" above, just self-inflicted by the test this time.
Fixed by deleting each level's own outputs before advancing to the next;
re-run with that fix in place: **levels 1 through 7 all genuinely
succeeded (1/1, 2/2, 3/3, 4/4, 5/5, 6/6, 7/7)**, each cross-checked via a
direct S3 size check, not the API's self-report. Worker-log inspection
during the level-5 investigation also surfaced that RunPod doesn't
necessarily dispatch N simultaneous requests to N separate workers even
when N workers are configured -- one request queued behind an
already-running job on the same warm worker rather than reaching an idle
cold one -- which is itself useful input for the routing/scheduler design
question, distinct from a compute ceiling. Conclusion: for this operation
and file size, worker-count bookkeeping (`mltools_max_inflight = 7`) is
sufficient on its own; no additional per-file-size concurrency ceiling
below the raw worker count is needed, contrary to what this section
originally (wrongly) concluded.

**Volume-space ledger, added in response to (3) -- lives on a separate,
still-unmerged branch.** Nothing had been checking free space before a
large job, and a full `list_objects_v2` walk of the volume (the only
ground-truth check available) takes ~90 s, dominated by the SASpro runtime
venv's 23k+ tiny files -- too slow to call before every job.
`scripts/runpod_volume_ledger.py` (branch `feature/runpod-volume-ledger`,
**not part of this PR's file set, not yet merged**) keeps a small JSON
object on the volume itself (`ledger/volume_usage.json`) holding a running
bytes-used total: `reconcile()` does the expensive real listing once to
seed/correct it against ground truth, `record_delta()` is the cheap
incremental path callers use after an upload or cleanup, and
`has_capacity(needed_bytes)` answers "would this fit?" from the cached
figure with a safety margin (default 1 GB, an operator-configurable
heuristic, not a derived bound). Independent review (2026-08-23) correctly
flagged the read-modify-write as unsafe as a sole authority for a real
capacity decision under genuine concurrent writers -- it is now explicitly
documented as advisory-only, and a version counter tracks writes for
observability without claiming to be a lock. Seeded 2026-08-23 against a
real listing: 11.03 GB used / 20 GB capacity (51.4%). Not yet wired into
`ml_tools_gpu.py`/`rcastro_gpu.py` as an actual pre-flight check -- the
mechanism exists, the call site doesn't yet. This repo's own public export
does not currently package it, since it isn't part of this repo state
until PR #377 merges -- don't reference it as reproducible until then.

**Local fallback failure injection -- describes code on a separate,
still-unmerged branch, not part of this PR.** `nas_server/ml_tools_gpu.py`
is not among PR #370's changed files; it lives on PR #373
(`feature/ml-tools-worker`, not yet merged). Independent review flagged
that an earlier version of this section had the same problem the ledger
paragraph above already correctly avoids -- describing #373's code as
current when it isn't part of this PR or main yet. Recorded here as
external evidence about that sibling PR, not a claim about this repo's
state: `run_ml_tool_gpu()` is designed to never raise and never silently
fall back to another algorithm, so that Experiment mode can keep each
engine as a distinct candidate. A prior version of `run_ml_tool_gpu()` let
a handful of pre-network steps (settings lookup, path construction) raise
past that documented contract; #373 fixed this by moving the entire body
inside `try`/`except`/`finally`, with a regression test proving a raising
settings-lookup now returns a clean `{"ok": false, ...}` instead of
propagating. No caller yet exists that chooses between remote and local
execution, so there is no fallback *decision* to test yet; what could be
verified is that the function's own failure contract holds against the
real RunPod API. Live test (not mocked, run against #373's branch): pointed
the function at a genuinely nonexistent endpoint ID with otherwise-real
credentials. Result: a clean `{"ok": false, "error": "404 Client Error: Not
Found..."}` in 6.7 s (no hang, no raise), and a follow-up real S3 listing
confirmed no orphaned objects were left under the job's prefix -- cleanup
ran correctly even on a submission-time failure. None of this is
verifiable against PR #370's own branch, since the code doesn't live here.

## Validation still required

- posted billing evidence for the ml-tools-worker endpoint (RC Astro's is
  posted above; the newer endpoint's has not been reconciled against a closed
  hourly bucket yet);
- clean-machine worker build and endpoint deployment instructions;
- an actual caller that chooses between remote GPU and local execution (no
  such fallback decision point exists yet to failure-inject against -- see
  above);
- ~~a real per-file-size concurrency ceiling for the routing/scheduler
  design~~ -- **closed, 2026-08-23**: re-run at levels 1-7 (the ml-tools
  endpoint's full `workersMax`) against the 717.95 MB file, with proper
  per-level volume cleanup this time, found no genuine capacity failures at
  any level (7/7 succeeded at the top level too). Worker-count bookkeeping
  alone (`mltools_max_inflight = 7`) is sufficient for this operation/file
  size; no additional per-file-size ceiling is needed. See the corrected
  finding above for detail, including the RunPod dispatch-routing
  observation (N simultaneous requests don't always reach N separate
  workers) that came out of the investigation;
- root-cause the RC Astro SXT stars-sidecar failure specifically (main
  output succeeded, sidecar generation crashed after 94%+ progress) --
  distinct from the ml-tools-worker findings above, on a different worker
  image.

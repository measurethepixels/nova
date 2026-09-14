# NOVA ML-tools RunPod worker

This optional Serverless worker runs the freely available SASpro/Cosmic
Clarity/DarkStar operations and GraXpert. RC Astro remains in its separate
licensed worker image.

The image deliberately contains no model weights or credentials. Provision
models under the private network volume and point the endpoint at that volume:

- SASpro: `/runpod-volume/ml-models/saspro/runtime/py312/models/`
- GraXpert: `/runpod-volume/ml-models/xdg/GraXpert/bge-ai-models/` and
  `/runpod-volume/ml-models/xdg/GraXpert/denoise-ai-models/`
- Parallax: the six purchased files under `syqon-parallax/`: Natural models in
  `natural/{correction,sharpen,star-reduction}.pth` and Defined models in
  `defined/{correction,sharpen,star-reduction}.pt`. The archive and weights
  remain private and are never copied into the image or repository. Experiment
  steps select `mode: natural` (default) or `mode: defined`; Defined star
  reduction accepts levels 1–7, while Natural accepts 1–10.

Provisioning must also write
`/runpod-volume/ml-models/worker-model-manifest.json`. Schema version 1 contains
an `operations` mapping; each operation has `available: true` and a non-empty
`models` list of paths relative to `/runpod-volume/ml-models`, exact byte sizes,
and SHA-256 hashes. The worker validates every required file and hash before
running a paid job. SASpro operations additionally require the exact 1.20.1
filenames used by that operation; listing an unrelated model cannot satisfy a
capability check. Cosmic Clarity correction uses the V2 AI4 model. Denoise
supports explicit `standard`, `lite`, and `walking` choices and requires the
mono/color pair for all three, preventing silent model fallback. Executable
presence alone never constitutes capability.

Parallax is intentionally restricted to full-field inputs whose width and
height are both at least 512 pixels. Its correction models are coordinate-aware;
an undersized crop can complete without an engine error while yielding invalid
output, so the worker rejects that case before inference.

Build requirements:

1. Resolve and record an immutable CUDA/PyTorch base image digest compatible
   with Python 3.12 and the selected SASpro/ONNX runtime. Resolved
   2026-08-22 via `docker buildx imagetools inspect
   runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` (linux/amd64 manifest
   digest, not the multi-arch index digest):

   ```
   runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404@sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851
   ```

   Verified inside the pulled image: CUDA 12.8.1, Python 3.12.3. This is the
   same base tag the live `nova-rcastro-worker` endpoint already runs in
   production (proves the RunPod GPU node pulls and runs it correctly) and
   the same digest already pinned in `docker/gptsovits-pod/Dockerfile` — one
   less base image to track drift on across the two GPU workers. Because the
   tag itself already encodes the RunPod image version, CUDA version, PyTorch
   version, and Ubuntu version, re-publishes under this exact tag are
   unlikely, but re-run the `imagetools inspect` command before a build if
   there is ever doubt the pinned digest is stale.
2. Stage the official GraXpert 3.0.2 "Umbriel" Linux release as a directory
   named `GraXpert-linux-3.0.2/` in the build context — **not** a single
   file. GraXpert's Linux distribution is a cx_Freeze "onedir" bundle: the
   `GraXpert` executable dynamically loads ~65 shared libraries from a
   sibling `lib/` directory (confirmed by running the bare binary alone:
   `error while loading shared libraries: libcrypt-eb21b399.so.2`), plus a
   `share/` resources directory. Copying just the renamed binary produces an
   image that fails at container start. The directory layout the Dockerfile
   expects:

   ```
   GraXpert-linux-3.0.2/
     GraXpert          (the executable)
     lib/               (~65 shared libraries GraXpert loads at runtime)
     share/
     frozen_application_license.txt
   ```

   Verify the `GraXpert` executable's own SHA-256 before building —
   `d2d02f22d5ee82778cc3aac4179765c9261b8155b3e1c49318f617f916cc5bee` for the
   3.0.2 release (recorded 2026-08-22 from the same install already used to
   validate `graxpert_denoise` locally). GraXpert itself is free/open-source
   (unlike RC-Astro's account-gated CLI), so baking its ~1.1GB software
   bundle into this image is a size tradeoff, not a licensing one — its AI
   model *weights* are the part that must never be baked in, and those
   already live on the network volume under `XDG_DATA_HOME`
   (`xdg/GraXpert/bge-ai-models/`, `xdg/GraXpert/denoise-ai-models/`,
   matching GraXpert's own lookup path), provisioned separately from this
   image.
3. Build with:

   ```
   docker build --platform linux/amd64 \
     --build-arg BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404@sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851 \
     -t <registry>/nova-ml-tools-worker:<tag> .
   ```

   Never substitute a mutable tag in a durable deployment.

The handler accepts only operation identifiers listed in `worker_core.py`,
only relative network-volume paths, and only validated parameters. Every step
receives a numbered FITS output; chained jobs retain all intermediate FITS for
manual review and benchmark evidence.

Recommended endpoint scaling remains `workersMin=0`, `workersMax=8`,
`idleTimeout=30`, with FlashBoot enabled. The endpoint is not production-routed
until its image, model hashes, local/remote equivalence, billing, cleanup, and
large-file benchmarks pass the protocol in `docs/ML_GPU_BENCHMARK_PROTOCOL.md`.

## Standing up your own RunPod account

This worker runs on [RunPod](https://runpod.io) Serverless. If you don't
already have an account, Measure the Pixels' creator has a referral link --
<https://runpod.io?ref=p2blsn83> -- using it may earn Measure the Pixels
credit at no extra cost to you. The plain, non-referral signup is
<https://runpod.io> if you'd rather not use it.

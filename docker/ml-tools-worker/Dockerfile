# BASE_IMAGE must be supplied as an immutable name@sha256 digest.  There is no
# mutable default by design; see README.md.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG SASPRO_VERSION=1.20.1
ARG RUNPOD_VERSION=1.12.0
ARG CRYPTOGRAPHY_VERSION=50.0.0

ENV PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen \
    SASPRO_RUNTIME_DIR=/runpod-volume/ml-models/saspro/runtime \
    XDG_DATA_HOME=/runpod-volume/ml-models/xdg
WORKDIR /opt/nova-worker

# SASpro's darkstar/parallax code paths import PyQt6.QtWidgets (via
# setiastro.saspro.remove_stars) even for headless CLI use. QT_QPA_PLATFORM=
# offscreen avoids needing a real X server, but QtWidgets still dlopen's
# libEGL.so.1 at import time -- confirmed live: darkstar/syqon_parallax_*
# failed with "ImportError: libEGL.so.1: cannot open shared object file",
# and `ldd` on QtWidgets.abi3.so showed exactly one missing direct
# dependency (libEGL.so.1), so this is a targeted fix, not a guess at a
# larger Qt/X11 bundle.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libegl1 \
    && rm -rf /var/lib/apt/lists/*

# --ignore-installed cryptography / --break-system-packages: the base image's
# cryptography package is Debian-managed (no pip RECORD file), so a normal
# install/upgrade of a dependency that pulls in a newer cryptography fails
# with "Cannot uninstall cryptography ... no RECORD file was found". Same
# fix already proven in docker/rcastro-worker/Dockerfile for the identical
# failure on that base image. Pinned explicitly (not left to pip's resolver)
# so a rebuild from the same BASE_IMAGE digest can't silently resolve a
# different cryptography release -- CRYPTOGRAPHY_VERSION is the version that
# actually resolved and was verified working (all 12 operations smoke-tested
# on live GPU hardware) in the v1.2.0 image.
RUN pip install --ignore-installed "cryptography==${CRYPTOGRAPHY_VERSION}" \
      --break-system-packages --no-cache-dir \
      "runpod==${RUNPOD_VERSION}" "setiastrosuitepro==${SASPRO_VERSION}"

# onnxruntime-gpu's CUDA execution provider dlopen's libcudnn.so.8 at runtime
# regardless of onnxruntime version or install channel (confirmed against
# both default PyPI and Microsoft's CUDA-12-specific package index -- both
# resolve to the same wheel, still hardwired to the cuDNN 8 SONAME). The base
# image only ships cuDNN 9 (libcudnn.so.9, for PyTorch/SASpro's own use,
# which is unaffected and must not be touched). Confirmed live on a RunPod
# GPU worker: without this, GraXpert silently falls back to CPUExecutionProvider
# (job still succeeds, just ~28x slower on the smoke test: 245s vs 8.9s of
# which the CPU inference itself was the remaining gap after a separate model-
# path fix). Installing the full nvidia-cudnn-cu12==8.9.7.29 wheel via pip
# would downgrade/conflict with torch's own nvidia-cudnn-cu12==9.10.2.21
# pin, so instead extract only the inference .so.8 files (skip the
# *_train variants GraXpert never calls) directly from the wheel into a
# path-visible directory, alongside cuDNN 9, without registering the
# conflicting pip package.
RUN pip download --no-deps --no-cache-dir -d /tmp/cudnn8dl \
      "nvidia-cudnn-cu12==8.9.7.29" \
    && cd /tmp/cudnn8dl \
    && python3 -m zipfile -e nvidia_cudnn_cu12-*.whl /tmp/cudnn8dl/extracted \
    && cp /tmp/cudnn8dl/extracted/nvidia/cudnn/lib/libcudnn.so.8 \
          /tmp/cudnn8dl/extracted/nvidia/cudnn/lib/libcudnn_ops_infer.so.8 \
          /tmp/cudnn8dl/extracted/nvidia/cudnn/lib/libcudnn_cnn_infer.so.8 \
          /tmp/cudnn8dl/extracted/nvidia/cudnn/lib/libcudnn_adv_infer.so.8 \
          /usr/lib/x86_64-linux-gnu/ \
    && ldconfig \
    && rm -rf /tmp/cudnn8dl

# GraXpert's official Linux release is a cx_Freeze "onedir" bundle, not a
# single self-contained binary -- the GraXpert executable dynamically loads
# ~65 shared libraries from its sibling lib/ directory (confirmed by running
# the bare binary in isolation: `error while loading shared libraries:
# libcrypt-eb21b399.so.2`). It must be staged and copied as a whole directory,
# never as a single renamed file. GraXpert itself is free/open-source (unlike
# RC-Astro's gated CLI), so baking its ~1.1GB software bundle into this image
# is a size cost, not a licensing one -- its AI model *weights* are the part
# that must never be baked in, and those already live on the network volume
# under XDG_DATA_HOME (xdg/GraXpert/bge-ai-models/, xdg/GraXpert/denoise-ai-models/,
# matching GraXpert's own lookup path), never in this image.
COPY GraXpert-linux-3.0.2/ /opt/graxpert/
RUN chmod -R a+rX /opt/graxpert \
    && chmod 0555 /opt/graxpert/GraXpert \
    && ln -s /opt/graxpert/GraXpert /usr/local/bin/GraXpert

COPY worker_core.py parallax_runner.py handler.py ./
CMD ["python3", "-u", "/opt/nova-worker/handler.py"]

"""Deterministic routing-policy evidence for Candidate 4.

This module is deliberately data-only and is NOT wired into normal processing yet.
Candidate 4 depends on the shared-workspace and RunPod CPU-worker work (Candidates
1 and 2) before it can safely choose execution backends in production.

The table captures only evidence already measured and recorded in docs/RUNPOD_GPU.md.
It does not convert the current VM-vs-GPU observations into RunPod-CPU thresholds.
Any policy whose future decision depends on RunPod CPU performance is explicitly
marked ``needs_runpod_cpu_baseline=True`` and ``activation_ready=False``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceDirection(str, Enum):
    """What a completed benchmark actually demonstrated."""

    LOCAL_END_TO_END_FASTER = "local_end_to_end_faster"
    REMOTE_GPU_END_TO_END_FASTER = "remote_gpu_end_to_end_faster"
    REMOTE_GPU_COMPUTE_FASTER = "remote_gpu_compute_faster"
    CAPACITY_ONLY = "capacity_only"
    UNKNOWN = "unknown"


class PolicyClass(str, Enum):
    """Candidate-4 policy categories; not active routing decisions yet."""

    CPU_PREFERRED = "cpu_preferred"
    GPU_PREFERRED = "gpu_preferred"
    BORDERLINE = "borderline"
    CHAIN_AWARE = "chain_aware"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BenchmarkEvidence:
    source: str
    operation: str
    input_mb: float
    direction: EvidenceDirection
    local_seconds: float | None = None
    remote_end_to_end_seconds: float | None = None
    remote_worker_seconds: float | None = None
    note: str = ""


@dataclass(frozen=True)
class OperationPolicy:
    """A provisional policy row derived from current evidence.

    ``operations`` uses the identifiers the future planner will see at NOVA's
    processing layer (for RC-Astro, the existing ``seti_astro`` callable names),
    not the lower-level worker tool tokens such as ``bxt``/``nxt``.

    ``activation_ready`` must remain False while ``needs_runpod_cpu_baseline``
    is True. Candidate 2 will provide the CPU-pod timings needed to turn these
    rows into actual thresholds or fixed production preferences.
    """

    family: str
    operations: tuple[str, ...]
    policy_class: PolicyClass
    chain_aware: bool
    provisional_preference: str | None
    needs_runpod_cpu_baseline: bool
    activation_ready: bool
    rationale: str
    evidence: tuple[BenchmarkEvidence, ...] = ()


RUNPOD_GPU_DOC = "docs/RUNPOD_GPU.md (measured 2026-08-23)"


RC_ASTRO_SMALL_CHAIN = BenchmarkEvidence(
    source=RUNPOD_GPU_DOC,
    operation="bxt -> nxt -> bxt_correct_only -> sxt_star_split",
    input_mb=12.45,
    direction=EvidenceDirection.LOCAL_END_TO_END_FASTER,
    local_seconds=16.684,
    remote_end_to_end_seconds=53.454,
    note=(
        "Small-file end-to-end result. Remote model execution itself was only "
        "1.239-2.088 s per model, but the first invocation incurred 22.405 s of "
        "queue/cold delay. This is evidence for chain-aware overhead handling, "
        "not a RunPod-CPU crossover threshold."
    ),
)


GRAXPERT_DENOISE_401MB = BenchmarkEvidence(
    source=RUNPOD_GPU_DOC,
    operation="graxpert_denoise",
    input_mb=401.45,
    direction=EvidenceDirection.REMOTE_GPU_COMPUTE_FASTER,
    local_seconds=4610.0,
    remote_worker_seconds=87.9,
    note=(
        "Measured 52.4x worker-side GPU compute advantage over the current VM's "
        "CPU-only path. Candidate 2 must still measure the RunPod CPU baseline "
        "before a production crossover threshold is activated."
    ),
)


GRAXPERT_DENOISE_717MB = BenchmarkEvidence(
    source=RUNPOD_GPU_DOC,
    operation="graxpert_denoise",
    input_mb=717.95,
    direction=EvidenceDirection.REMOTE_GPU_COMPUTE_FASTER,
    remote_worker_seconds=114.5,
    note="Single-job RunPod GPU worker execution on the 717.95 MB stack.",
)


GRAXPERT_DENOISE_3180MB = BenchmarkEvidence(
    source=RUNPOD_GPU_DOC,
    operation="graxpert_denoise",
    input_mb=3180.0,
    direction=EvidenceDirection.REMOTE_GPU_COMPUTE_FASTER,
    remote_worker_seconds=582.4,
    note="Single-job RunPod GPU worker execution on the 3.18 GB drizzle mosaic.",
)


MLTOOLS_717MB_CONCURRENCY = BenchmarkEvidence(
    source=RUNPOD_GPU_DOC,
    operation="cosmic_correct",
    input_mb=717.95,
    direction=EvidenceDirection.CAPACITY_ONLY,
    note=(
        "Concurrency levels 1 through 7 all succeeded after per-level cleanup. "
        "RunPod did not necessarily map simultaneous requests to distinct workers, "
        "so configured worker count must not be treated as guaranteed immediate "
        "parallel start capacity."
    ),
)


POLICY_TABLE: tuple[OperationPolicy, ...] = (
    OperationPolicy(
        family="rcastro",
        operations=("bxt_deconvolve", "denoise_nxt", "bxt_star_correct", "sxt_star_split"),
        policy_class=PolicyClass.CHAIN_AWARE,
        chain_aware=True,
        provisional_preference=None,
        needs_runpod_cpu_baseline=True,
        activation_ready=False,
        rationale=(
            "Treat adjacent RC-Astro operations as one planning segment so a small "
            "file does not pay queue/cold/transfer overhead independently at every "
            "step. The existing 12.45 MB chain was faster end-to-end locally despite "
            "much faster remote model execution, but that local result came from the "
            "current VM, not the future RunPod CPU worker."
        ),
        evidence=(RC_ASTRO_SMALL_CHAIN,),
    ),
    OperationPolicy(
        family="graxpert_denoise",
        operations=("graxpert_denoise",),
        policy_class=PolicyClass.GPU_PREFERRED,
        chain_aware=False,
        provisional_preference="runpod_gpu_at_production_scale",
        needs_runpod_cpu_baseline=True,
        activation_ready=False,
        rationale=(
            "Production-size evidence shows a very large GPU compute advantage, "
            "including 87.9 s GPU versus 4610 s on the current VM CPU path at "
            "401.45 MB. Keep this as a strong provisional GPU preference, but do not "
            "encode a byte threshold until Candidate 2 measures the actual RunPod CPU."
        ),
        evidence=(
            GRAXPERT_DENOISE_401MB,
            GRAXPERT_DENOISE_717MB,
            GRAXPERT_DENOISE_3180MB,
        ),
    ),
    OperationPolicy(
        family="ml_tools_other",
        operations=(
            "graxpert_subtraction",
            "graxpert_division",
            "cosmic_sharpen",
            "cosmic_correct",
            "cosmic_denoise",
            "cosmic_both",
            "cosmic_satellite",
            "darkstar",
            "syqon_parallax_correct",
            "syqon_parallax_sharpen",
            "syqon_parallax_star_reduce",
        ),
        policy_class=PolicyClass.UNKNOWN,
        chain_aware=False,
        provisional_preference=None,
        needs_runpod_cpu_baseline=True,
        activation_ready=False,
        rationale=(
            "These operations have live endpoint success/concurrency evidence but no "
            "matched RunPod-CPU-vs-GPU crossover measurements in the current benchmark "
            "record. Do not infer a routing threshold from GraXpert denoise or from "
            "successful GPU execution alone."
        ),
        evidence=(MLTOOLS_717MB_CONCURRENCY,),
    ),
)


def policy_for(operation: str) -> OperationPolicy | None:
    """Return the provisional row containing *operation*, if one exists."""
    for policy in POLICY_TABLE:
        if operation in policy.operations:
            return policy
    return None


def activation_ready_policies() -> tuple[OperationPolicy, ...]:
    """Rows currently safe to wire into automatic routing.

    Expected to be empty until Candidate 2 contributes RunPod CPU measurements
    and the eventual Candidate-4 implementation promotes specific rows.
    """
    return tuple(policy for policy in POLICY_TABLE if policy.activation_ready)

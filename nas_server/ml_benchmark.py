"""Versioned contract for local-versus-remote ML processing benchmarks.

The registry is intentionally independent of dispatch.  It describes what may
be benchmarked and what evidence must be captured; it does not start a worker,
move a FITS file, or select production routing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


SCHEMA_VERSION = 1
ExecutionPath = Literal["local_cpu", "local_gpu", "runpod_cold", "runpod_warm"]
InputStage = Literal["linear", "linear_with_trail", "nonlinear"]


@dataclass(frozen=True)
class OperationSpec:
    operation_id: str
    family: str
    input_stage: InputStage
    output_roles: tuple[str, ...]
    local_callable: str
    remote_image_family: str
    default_args: dict[str, object] = field(default_factory=dict)
    notes: str = ""


OPERATIONS: dict[str, OperationSpec] = {
    spec.operation_id: spec
    for spec in (
        OperationSpec("rcastro_bxt", "rcastro", "linear", ("processed",),
                      "nas_server.seti_astro.bxt_deconvolve", "rcastro",
                      {"bxt_stars": 0.5, "bxt_nonstellar": 0.3}),
        OperationSpec("rcastro_bxt_correct", "rcastro", "linear", ("processed",),
                      "nas_server.seti_astro.bxt_star_correct", "rcastro"),
        OperationSpec("rcastro_nxt", "rcastro", "linear", ("processed",),
                      "nas_server.seti_astro.denoise_nxt", "rcastro",
                      {"nxt_denoise": 0.7, "nxt_iterations": 2}),
        OperationSpec("rcastro_sxt", "rcastro", "linear", ("starless",),
                      "nas_server.seti_astro.star_removal_starxt", "rcastro"),
        OperationSpec("rcastro_sxt_split", "rcastro", "linear",
                      ("starless", "stars"), "nas_server.seti_astro.sxt_star_split",
                      "rcastro"),
        OperationSpec("graxpert_subtraction", "graxpert", "linear", ("processed",),
                      "nas_server.seti_astro.background_extract", "graxpert",
                      {"correction": "Subtraction", "smoothing": 0.5}),
        OperationSpec("graxpert_division", "graxpert", "linear", ("processed",),
                      "nas_server.seti_astro.background_extract", "graxpert",
                      {"correction": "Division", "smoothing": 0.5},
                      "Use only on an input with a meaningful multiplicative gradient."),
        OperationSpec("graxpert_denoise", "graxpert", "linear", ("processed",),
                      "GraXpert -cmd denoising", "graxpert",
                      {"strength": 0.5, "batch_size": 4},
                      "Requires a pinned GraXpert denoise AI model; executable support "
                      "alone is not a usable capability."),
        OperationSpec("cosmic_sharpen", "saspro", "linear", ("processed",),
                      "nas_server.seti_astro.cc_sharpen_inprocess", "saspro"),
        OperationSpec("cosmic_correct", "saspro", "linear", ("processed",),
                      "setiastrosuitepro cc correct", "saspro"),
        OperationSpec("cosmic_denoise", "saspro", "linear", ("processed",),
                      "nas_server.seti_astro.cc_denoise_inprocess", "saspro"),
        OperationSpec("cosmic_both", "saspro", "linear", ("processed",),
                      "setiastrosuitepro cc both", "saspro"),
        OperationSpec("cosmic_satellite", "saspro", "linear_with_trail",
                      ("processed",), "setiastrosuitepro cc satellite", "saspro",
                      notes="Requires a representative frame containing a real trail."),
        OperationSpec("darkstar", "saspro", "linear", ("starless",),
                      "nas_server.seti_astro.remove_stars_inprocess", "saspro",
                      {"mode": "unscreen"}),
        OperationSpec("darkstar_split", "saspro", "linear",
                      ("starless", "stars"), "nas_server.seti_astro.remove_stars_split",
                      "saspro", {"mode": "unscreen"}),
        OperationSpec("syqon_prism_mini", "syqon", "linear", ("processed",),
                      "nas_server.seti_astro.syqon_prism_denoise", "syqon",
                      {"strength": 0.85}),
        OperationSpec("syqon_parallax_correct", "syqon", "linear", ("processed",),
                      "SASpro 1.20.1 Parallax correction", "saspro"),
        OperationSpec("syqon_parallax_sharpen", "syqon", "linear", ("processed",),
                      "SASpro 1.20.1 Parallax sharpen", "saspro",
                      {"alpha": 0.5}),
        OperationSpec("syqon_parallax_star_reduce", "syqon", "linear", ("processed",),
                      "SASpro 1.20.1 Parallax star reduction", "saspro",
                      {"level": 5},
                      "Star reduction is not equivalent to full star removal."),
    )
}


@dataclass(frozen=True)
class ArtifactEvidence:
    role: str
    path: str
    bytes: int
    sha256: str
    shape: tuple[int, ...]
    finite: bool
    celestial_wcs: bool

    def validate(self) -> None:
        if not self.role or not self.path:
            raise ValueError("artifact role and path are required")
        if self.bytes <= 0:
            raise ValueError("artifact bytes must be positive")
        if len(self.sha256) != 64:
            raise ValueError("artifact SHA-256 must contain 64 hexadecimal characters")
        int(self.sha256, 16)
        if not self.shape or any(axis <= 0 for axis in self.shape):
            raise ValueError("artifact shape axes must be positive")


@dataclass(frozen=True)
class TimingEvidence:
    compression_s: float = 0.0
    upload_s: float = 0.0
    queue_s: float = 0.0
    worker_start_s: float = 0.0
    execution_s: float = 0.0
    download_s: float = 0.0
    decompression_s: float = 0.0
    client_wall_s: float = 0.0

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        measured_parts = (
            self.compression_s + self.upload_s + self.queue_s + self.worker_start_s
            + self.execution_s + self.download_s + self.decompression_s
        )
        if self.client_wall_s and measured_parts > self.client_wall_s + 0.1:
            raise ValueError("timing components exceed client wall time")


@dataclass(frozen=True)
class BenchmarkRecord:
    run_id: str
    operation_id: str
    execution_path: ExecutionPath
    source: ArtifactEvidence
    outputs: tuple[ArtifactEvidence, ...]
    timings: TimingEvidence
    tool_version: str
    model_versions: dict[str, str]
    args: dict[str, object]
    metrics_before: dict[str, object]
    metrics_after: dict[str, object]
    transfer_encoding: Literal["raw", "zstd"]
    status: Literal["valid", "invalid", "failed"]
    manual_review: Literal["pending", "accepted", "rejected"] = "pending"
    billing_amount_usd: float | None = None
    billing_posted_at: str | None = None
    cleanup_ok: bool | None = None
    notes: str = ""

    def validate(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        spec = OPERATIONS.get(self.operation_id)
        if spec is None:
            raise ValueError(f"unknown operation_id: {self.operation_id}")
        self.source.validate()
        for artifact in self.outputs:
            artifact.validate()
        self.timings.validate()
        if self.status == "valid":
            roles = {artifact.role for artifact in self.outputs}
            missing = set(spec.output_roles) - roles
            if missing:
                raise ValueError(f"valid record is missing output role(s): {sorted(missing)}")
            if not all(artifact.finite for artifact in self.outputs):
                raise ValueError("valid record contains non-finite output")
        if self.billing_amount_usd is not None and self.billing_amount_usd < 0:
            raise ValueError("billing amount must be non-negative")
        if self.billing_posted_at and self.billing_amount_usd is None:
            raise ValueError("billing timestamp requires an amount")

    def to_manifest(self) -> dict[str, object]:
        self.validate()
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}


def operation_manifest() -> dict[str, object]:
    """Return the stable, JSON-serializable benchmark operation inventory."""
    return {
        "schema_version": SCHEMA_VERSION,
        "operations": {key: asdict(value) for key, value in OPERATIONS.items()},
    }

"""Pure policy and manifest helpers for lossless remote FITS transfer.

This module deliberately does not perform network or filesystem mutation.  The
client and worker use the same arithmetic to decide whether a completed Zstd
archive is worth transferring; codec execution remains an adapter concern.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


TransferMode = Literal["auto", "raw", "zstd"]
TransferEncoding = Literal["raw", "zstd"]


@dataclass(frozen=True)
class CompressionPolicy:
    mode: TransferMode = "auto"
    min_bytes: int = 8 * 1024 * 1024
    min_space_savings_ratio: float = 0.10
    min_wall_savings_s: float = 1.0
    zstd_level: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"auto", "raw", "zstd"}:
            raise ValueError(f"unknown transfer mode: {self.mode}")
        if self.min_bytes < 0:
            raise ValueError("min_bytes must be non-negative")
        if not 0 <= self.min_space_savings_ratio < 1:
            raise ValueError("min_space_savings_ratio must be in [0, 1)")
        if self.min_wall_savings_s < 0:
            raise ValueError("min_wall_savings_s must be non-negative")
        if self.zstd_level < 1:
            raise ValueError("zstd_level must be positive")


@dataclass(frozen=True)
class CompressionDecision:
    encoding: TransferEncoding
    reason: str
    original_bytes: int
    transferred_bytes: int
    space_savings_ratio: float
    raw_transfer_s: float | None = None
    compressed_path_s: float | None = None
    predicted_wall_savings_s: float | None = None


@dataclass(frozen=True)
class TransferArtifact:
    encoding: TransferEncoding
    original_bytes: int
    transferred_bytes: int
    original_sha256: str
    transferred_sha256: str
    zstd_level: int | None = None

    def to_manifest(self) -> dict:
        return asdict(self)


def should_attempt_compression(original_bytes: int, policy: CompressionPolicy) -> bool:
    """Return whether the caller should spend CPU producing a candidate archive."""
    if original_bytes < 0:
        raise ValueError("original_bytes must be non-negative")
    if policy.mode == "raw":
        return False
    if policy.mode == "zstd":
        return True
    return original_bytes >= policy.min_bytes


def choose_transfer_encoding(
    *,
    original_bytes: int,
    compressed_bytes: int,
    compression_s: float,
    decompression_s: float,
    transfer_mbps: float,
    policy: CompressionPolicy,
) -> CompressionDecision:
    """Choose raw or Zstd after a candidate archive exists.

    ``transfer_mbps`` is measured end-to-end throughput in decimal megabits per
    second.  Polling and worker cold-start time are intentionally excluded: they
    affect both encodings equally and would distort the byte-transfer crossover.
    """
    if original_bytes < 0 or compressed_bytes < 0:
        raise ValueError("byte counts must be non-negative")
    if compression_s < 0 or decompression_s < 0:
        raise ValueError("codec times must be non-negative")
    if transfer_mbps <= 0:
        raise ValueError("transfer_mbps must be positive")

    savings_ratio = (
        0.0 if original_bytes == 0 else 1.0 - compressed_bytes / original_bytes
    )
    bytes_per_second = transfer_mbps * 1_000_000 / 8
    raw_transfer_s = original_bytes / bytes_per_second
    compressed_path_s = compression_s + compressed_bytes / bytes_per_second + decompression_s
    wall_savings = raw_transfer_s - compressed_path_s

    def decision(encoding: TransferEncoding, reason: str) -> CompressionDecision:
        return CompressionDecision(
            encoding=encoding,
            reason=reason,
            original_bytes=original_bytes,
            transferred_bytes=compressed_bytes if encoding == "zstd" else original_bytes,
            space_savings_ratio=savings_ratio,
            raw_transfer_s=raw_transfer_s,
            compressed_path_s=compressed_path_s,
            predicted_wall_savings_s=wall_savings,
        )

    if policy.mode == "raw":
        return decision("raw", "operator forced raw transfer")
    if policy.mode == "zstd":
        return decision("zstd", "operator forced Zstd transfer")
    if original_bytes < policy.min_bytes:
        return decision("raw", "file is below the compression size floor")
    if savings_ratio < policy.min_space_savings_ratio:
        return decision("raw", "candidate archive does not save enough bytes")
    if wall_savings < policy.min_wall_savings_s:
        return decision("raw", "predicted wall-time benefit is below the required margin")
    return decision("zstd", "candidate clears size, space, and wall-time thresholds")

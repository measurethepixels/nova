#!/usr/bin/env python3
"""Benchmark Zstandard and fpack transfer candidates on representative FITS files.

This measures local codec cost and compression ratio.  It does not measure the
network, RunPod queueing, worker startup, or billing; those require a separate
endpoint test and must not be inferred from this report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import numpy as np
from astropy.io import fits


CHUNK_BYTES = 4 * 1024 * 1024


def sha256_file(path: Path) -> tuple[str, float]:
    started = time.perf_counter()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest(), time.perf_counter() - started


def sha256_decoded_zstd(path: Path) -> tuple[str, float]:
    started = time.perf_counter()
    digest = hashlib.sha256()
    process = subprocess.Popen(
        ["zstd", "-q", "-d", "-c", str(path)],
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    for chunk in iter(lambda: process.stdout.read(CHUNK_BYTES), b""):
        digest.update(chunk)
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"zstd decode failed with exit code {return_code}")
    return digest.hexdigest(), time.perf_counter() - started


def command_version(command: str) -> str:
    result = subprocess.run(
        [command, "-V"], check=False, capture_output=True, text=True,
    )
    return (result.stdout or result.stderr).strip()


def fits_storage_metadata(path: Path) -> dict[str, object]:
    with fits.open(path, memmap=True, do_not_scale_image_data=True) as hdul:
        header = hdul[0].header
        data = hdul[0].data
        return {
            "bitpix": header.get("BITPIX"),
            "bscale": header.get("BSCALE", 1.0),
            "bzero": header.get("BZERO", 0.0),
            "storage_dtype": str(data.dtype) if data is not None else None,
            "shape": list(data.shape) if data is not None else None,
        }


def benchmark_candidate(
    path: Path,
    *,
    level: int,
    scratch: Path,
    original_sha256: str,
) -> dict[str, object]:
    compressed = scratch / f"{path.name}.level-{level}.zst"
    started = time.perf_counter()
    result = subprocess.run(
        ["zstd", "-q", "-T0", f"-{level}", "-f", str(path), "-o", str(compressed)],
        check=False,
        capture_output=True,
        text=True,
    )
    compression_s = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"zstd exited {result.returncode}")

    decoded_sha256, decode_and_sha256_s = sha256_decoded_zstd(compressed)
    original_bytes = path.stat().st_size
    compressed_bytes = compressed.stat().st_size
    compressed.unlink()
    return {
        "codec": "zstd",
        "level": level,
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "ratio": compressed_bytes / original_bytes if original_bytes else 0.0,
        "space_saved_pct": (
            (1 - compressed_bytes / original_bytes) * 100 if original_bytes else 0.0
        ),
        "compression_s": compression_s,
        "decode_and_sha256_s": decode_and_sha256_s,
        "decoded_sha256": decoded_sha256,
        "lossless_verified": decoded_sha256 == original_sha256,
    }


def compare_fits_pixels(original: Path, decoded: Path) -> dict[str, object]:
    """Compare FITS pixels in bounded chunks and retain basic WCS continuity."""
    wcs_keys = ("CTYPE1", "CTYPE2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2")
    total = 0
    sum_abs = 0.0
    sum_sq = 0.0
    max_abs = 0.0
    exact = True
    with fits.open(original, memmap=True, do_not_scale_image_data=True) as source_hdul, fits.open(
        decoded, memmap=True, do_not_scale_image_data=True
    ) as decoded_hdul:
        source = source_hdul[0].data
        restored = decoded_hdul[0].data
        if source is None or restored is None:
            raise RuntimeError("primary FITS image data is required")
        if source.shape != restored.shape:
            return {
                "shape_equal": False,
                "pixel_exact": False,
                "wcs_equal": False,
            }
        source_flat = source.reshape(-1)
        restored_flat = restored.reshape(-1)
        source_scale = float(source_hdul[0].header.get("BSCALE", 1.0))
        source_zero = float(source_hdul[0].header.get("BZERO", 0.0))
        decoded_scale = float(decoded_hdul[0].header.get("BSCALE", 1.0))
        decoded_zero = float(decoded_hdul[0].header.get("BZERO", 0.0))
        source_blank = source_hdul[0].header.get("BLANK")
        decoded_blank = decoded_hdul[0].header.get("BLANK")
        for start in range(0, source_flat.size, CHUNK_BYTES // 8):
            source_raw = source_flat[start:start + CHUNK_BYTES // 8]
            decoded_raw = restored_flat[start:start + CHUNK_BYTES // 8]
            left = np.asarray(source_raw, dtype=np.float64) * source_scale + source_zero
            right = np.asarray(decoded_raw, dtype=np.float64) * decoded_scale + decoded_zero
            if source_blank is not None:
                left[np.asarray(source_raw) == source_blank] = np.nan
            if decoded_blank is not None:
                right[np.asarray(decoded_raw) == decoded_blank] = np.nan
            delta = left - right
            abs_delta = np.abs(delta)
            exact = exact and bool(np.array_equal(left, right, equal_nan=True))
            finite = np.isfinite(abs_delta)
            if finite.any():
                values = abs_delta[finite]
                total += int(values.size)
                sum_abs += float(values.sum(dtype=np.float64))
                sum_sq += float(np.square(values).sum(dtype=np.float64))
                max_abs = max(max_abs, float(values.max()))
        source_header = source_hdul[0].header
        decoded_header = decoded_hdul[0].header
        wcs_equal = all(source_header.get(key) == decoded_header.get(key) for key in wcs_keys)
        return {
            "shape_equal": True,
            "shape": list(source.shape),
            "pixel_exact": exact,
            "finite_compared_pixels": total,
            "mean_abs_error": sum_abs / total if total else None,
            "rms_error": (sum_sq / total) ** 0.5 if total else None,
            "max_abs_error": max_abs if total else None,
            "wcs_equal": wcs_equal,
        }


def benchmark_fpack_candidate(
    path: Path,
    *,
    mode: str,
    scratch: Path,
    original_sha256: str,
) -> dict[str, object]:
    """Benchmark documented floating-point fpack modes.

    ``lossless`` uses GZIP_2 with quantization disabled. ``q100`` uses Rice
    quantization at 100 levels per measured background sigma; it is high
    fidelity but changes floating-point values and is not mathematically lossless.
    """
    if mode not in {"lossless", "q100"}:
        raise ValueError(f"unknown fpack mode: {mode}")
    compressed = scratch / f"{path.name}.{mode}.fz"
    decoded = scratch / f"{path.name}.{mode}.decoded.fit"
    options = ["-g2", "-q", "0"] if mode == "lossless" else ["-qzt", "100"]
    started = time.perf_counter()
    result = subprocess.run(
        ["fpack", *options, "-O", str(compressed), str(path)],
        check=False, capture_output=True, text=True,
    )
    compression_s = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"fpack exited {result.returncode}")

    started = time.perf_counter()
    result = subprocess.run(
        ["funpack", "-O", str(decoded), str(compressed)],
        check=False, capture_output=True, text=True,
    )
    decompression_s = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"funpack exited {result.returncode}")
    decoded_sha256, decoded_sha256_s = sha256_file(decoded)
    compressed_sha256, compressed_sha256_s = sha256_file(compressed)
    comparison = compare_fits_pixels(path, decoded)
    original_bytes = path.stat().st_size
    compressed_bytes = compressed.stat().st_size
    compressed.unlink()
    decoded.unlink()
    return {
        "codec": "fpack",
        "mode": mode,
        "command_options": options,
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "compressed_sha256": compressed_sha256,
        "compressed_sha256_s": compressed_sha256_s,
        "ratio": compressed_bytes / original_bytes if original_bytes else 0.0,
        "space_saved_pct": (
            (1 - compressed_bytes / original_bytes) * 100 if original_bytes else 0.0
        ),
        "compression_s": compression_s,
        "decompression_s": decompression_s,
        "decoded_sha256_s": decoded_sha256_s,
        "decoded_sha256": decoded_sha256,
        "whole_file_sha_equal": decoded_sha256 == original_sha256,
        "mathematically_lossless": mode == "lossless" and comparison.get("pixel_exact") is True,
        "scientifically_lossless_candidate": mode == "q100",
        "fits_comparison": comparison,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="JSON report path")
    parser.add_argument(
        "--level",
        dest="levels",
        type=int,
        action="append",
        help="Zstd level to test; repeat as needed (default: 1 and 3)",
    )
    parser.add_argument("fits", nargs="+", type=Path, help="representative FITS inputs")
    parser.add_argument(
        "--include-fpack", action="store_true",
        help="also test fpack GZIP_2/q0 lossless and Rice/q100 high-fidelity modes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("zstd") is None:
        raise SystemExit("zstd is required but was not found on PATH")
    if args.include_fpack and (shutil.which("fpack") is None or shutil.which("funpack") is None):
        raise SystemExit("fpack and funpack are required with --include-fpack")
    levels = args.levels or [1, 3]
    if any(level < 1 for level in levels):
        raise SystemExit("every Zstd level must be positive")

    inputs = [path.expanduser().resolve() for path in args.fits]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise SystemExit("missing FITS input(s): " + ", ".join(missing))

    report: dict[str, object] = {
        "contract": "Lossless FITS transfer compression benchmark",
        "scope": "local codec timing and compression ratio only",
        "codecs": ["zstd", *(["fpack"] if args.include_fpack else [])],
        "tools": {
            "zstd": command_version("zstd"),
            **(
                {"fpack": command_version("fpack"), "funpack": command_version("funpack")}
                if args.include_fpack else {}
            ),
        },
        "levels": levels,
        "files": [],
    }
    args.output = args.output.expanduser().resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="fits-compression-") as temp:
        scratch = Path(temp)
        files = report["files"]
        assert isinstance(files, list)
        for path in inputs:
            original_sha256, source_sha256_s = sha256_file(path)
            record = {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": original_sha256,
                "source_sha256_s": source_sha256_s,
                "fits_storage": fits_storage_metadata(path),
                "results": [
                    benchmark_candidate(
                        path,
                        level=level,
                        scratch=scratch,
                        original_sha256=original_sha256,
                    )
                    for level in levels
                ] + (
                    [
                        benchmark_fpack_candidate(
                            path, mode=mode, scratch=scratch,
                            original_sha256=original_sha256,
                        )
                        for mode in ("lossless", "q100")
                    ] if args.include_fpack else []
                ),
            }
            files.append(record)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    report["ok"] = all(
        result.get("lossless_verified", result.get("fits_comparison", {}).get("shape_equal", False))
        for file_record in report["files"]
        for result in file_record["results"]
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": report["ok"], "report": str(args.output)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

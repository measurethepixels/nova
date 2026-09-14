#!/usr/bin/env python3
"""Benchmark raw and compressed FITS round trips through RunPod S3 storage."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_fits_compression import compare_fits_pixels


CHUNK = 8 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def s3_client():
    import boto3
    from botocore.config import Config
    from nas_server.config import settings  # noqa: PLC0415

    region = settings["runpod_rcastro_region"]
    return boto3.client(
        "s3", region_name=region,
        endpoint_url=f"https://s3api-{region}.runpod.io/",
        aws_access_key_id=settings["runpod_s3_access_key_id"],
        aws_secret_access_key=settings["runpod_s3_secret_access_key"],
        config=Config(
            s3={"addressing_style": "path"}, connect_timeout=30, read_timeout=600,
            retries={"max_attempts": 10, "mode": "standard"}, max_pool_connections=4,
        ),
    )


def transfer_config(size_bytes: int):
    from boto3.s3.transfer import TransferConfig

    # max_concurrency=1 always: confirmed live against this RunPod S3 gateway
    # that concurrency=2 (even at a 16MiB chunk size well under the 64MiB used
    # for very-large files) hits repeated 524 Gateway Timeout errors on
    # UploadPart for a real 401MB file. Concurrency=1 has been reliable across
    # every multi-hundred-MB-to-multi-GB upload tried this session.
    very_large = size_bytes >= 512 * 1024 * 1024
    return TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=(64 if very_large else 16) * 1024 * 1024,
        max_concurrency=1,
        use_threads=True,
    )


def download_object(s3, bucket: str, key: str, destination: Path) -> None:
    """Stream GET without boto3's preliminary HeadObject request.

    The RunPod volume accepts HeadObject for raw FITS keys but returns 403 for
    some compressed suffixes, while GetObject remains authorized.
    """
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    with destination.open("wb") as handle:
        for chunk in iter(lambda: body.read(CHUNK), b""):
            handle.write(chunk)


def encode(source: Path, codec: str, scratch: Path) -> tuple[Path, float]:
    if codec == "raw":
        return source, 0.0
    if codec.startswith("zstd"):
        level = codec.removeprefix("zstd")
        output = scratch / f"{source.name}.{codec}.zst"
        command = ["zstd", "-q", "-T0", f"-{level}", "-f", str(source), "-o", str(output)]
    elif codec == "fpack-lossless":
        output = scratch / f"{source.name}.lossless.fz"
        command = ["fpack", "-g2", "-q", "0", "-O", str(output), str(source)]
    elif codec == "fpack-q100":
        output = scratch / f"{source.name}.q100.fz"
        command = ["fpack", "-qzt", "100", "-O", str(output), str(source)]
    else:
        raise ValueError(codec)
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"encoder exited {result.returncode}")
    return output, elapsed


def decode(downloaded: Path, codec: str, output: Path) -> float:
    if codec == "raw":
        started = time.perf_counter()
        shutil.copyfile(downloaded, output)
        return time.perf_counter() - started
    command = (["zstd", "-q", "-d", "-f", str(downloaded), "-o", str(output)]
               if codec.startswith("zstd") else
               ["funpack", "-O", str(output), str(downloaded)])
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"decoder exited {result.returncode}")
    return elapsed


def cleanup(s3, bucket: str, prefix: str) -> bool:
    try:
        for upload in s3.list_multipart_uploads(Bucket=bucket, Prefix=prefix).get("Uploads", []):
            s3.abort_multipart_upload(Bucket=bucket, Key=upload["Key"],
                                      UploadId=upload["UploadId"])
        for item in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=item["Key"])
        return (not s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents")
                and not s3.list_multipart_uploads(Bucket=bucket, Prefix=prefix).get("Uploads"))
    except Exception:
        return False


def benchmark(source: Path, codec: str, s3, bucket: str, root_prefix: str) -> dict:
    source = source.resolve()
    source_sha = sha256(source)
    prefix = f"{root_prefix}/{source_sha[:12]}-{codec}-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="runpod-codec-") as temp:
        scratch = Path(temp)
        encoded, compression_s = encode(source, codec, scratch)
        encoded_sha = sha256(encoded)
        key = f"{prefix}/{encoded.name}"
        downloaded = scratch / f"downloaded-{encoded.name}"
        restored = scratch / f"restored-{source.name}"
        try:
            started = time.perf_counter()
            s3.upload_file(str(encoded), bucket, key,
                           Config=transfer_config(encoded.stat().st_size))
            upload_s = time.perf_counter() - started
            listed = s3.list_objects_v2(Bucket=bucket, Prefix=key).get("Contents", [])
            remote = next((item for item in listed if item.get("Key") == key), None)
            if remote is None:
                raise RuntimeError(f"uploaded object not visible in listing: {key}")
            started = time.perf_counter()
            download_object(s3, bucket, key, downloaded)
            download_s = time.perf_counter() - started
            downloaded_sha = sha256(downloaded)
            decompression_s = decode(downloaded, codec, restored)
            restored_sha = sha256(restored)
            comparison = compare_fits_pixels(source, restored)
            transport_exact = encoded_sha == downloaded_sha
            scientifically_valid = (
                restored_sha == source_sha if codec in {"raw", "zstd1", "zstd3"}
                else comparison.get("pixel_exact") is True if codec == "fpack-lossless"
                else comparison.get("shape_equal") is True and comparison.get("wcs_equal") is True
            )
            return {
                "status": "valid" if transport_exact and scientifically_valid else "failed",
                "codec": codec,
                "source": str(source),
                "source_bytes": source.stat().st_size,
                "source_sha256": source_sha,
                "encoded_bytes": encoded.stat().st_size,
                "encoded_sha256": encoded_sha,
                "remote_content_length": int(remote["Size"]),
                "downloaded_sha256": downloaded_sha,
                "transport_exact": transport_exact,
                "restored_sha256": restored_sha,
                "whole_file_lossless": restored_sha == source_sha,
                "scientifically_valid": scientifically_valid,
                "fits_comparison": comparison,
                "compression_s": compression_s,
                "upload_s": upload_s,
                "download_s": download_s,
                "decompression_s": decompression_s,
                "roundtrip_s": compression_s + upload_s + download_s + decompression_s,
                "space_saved_pct": 100 * (1 - encoded.stat().st_size / source.stat().st_size),
                "prefix": prefix,
            }
        finally:
            cleaned = cleanup(s3, bucket, prefix)
            if not cleaned:
                raise RuntimeError(f"remote cleanup failed: {prefix}")


def main() -> int:
    from nas_server.config import settings  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codec", action="append",
                        choices=("raw", "zstd1", "zstd3", "fpack-lossless", "fpack-q100"))
    parser.add_argument("fits", nargs="+", type=Path)
    args = parser.parse_args()
    codecs = args.codec or ["raw", "zstd1", "zstd3", "fpack-lossless", "fpack-q100"]
    if shutil.which("zstd") is None or shutil.which("fpack") is None or shutil.which("funpack") is None:
        raise SystemExit("zstd, fpack, and funpack are required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "scope": "RunPod S3 codec round trip",
              "codecs": codecs, "results": [], "ok": False}
    s3 = s3_client()
    bucket = settings["runpod_rcastro_volume_id"]
    for source in args.fits:
        for codec in codecs:
            try:
                result = benchmark(source, codec, s3, bucket, "nova_compression_benchmarks")
            except Exception as exc:
                result = {"status": "failed", "codec": codec, "source": str(source),
                          "error": str(exc)}
            report["results"].append(result)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            if result["status"] != "valid":
                print(json.dumps(result, indent=2), flush=True)
                return 1
            print(json.dumps({k: result[k] for k in
                              ("codec", "source_bytes", "encoded_bytes", "roundtrip_s")}),
                  flush=True)
    report["ok"] = True
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": True, "report": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate the public sample package and compare a result to golden metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


PRIVATE_HEADER_KEYS = {
    "SITELAT", "SITELONG", "OBSGEO-X", "OBSGEO-Y", "OBSGEO-Z",
    "LATITUDE", "LONGITUD",
}
PUBLIC_HEADER_KEYS = {
    "SIMPLE", "BITPIX", "EXTEND", "BSCALE", "BZERO", "BAYERPAT", "CCD-TEMP",
    "CCDXBIN", "CCDYBIN", "CREATOR", "DATE-OBS", "DEC", "EXPOSURE",
    "EXPTIME", "FILTER", "FOCALLEN", "FOCUSPOS", "GAIN", "IMAGETYP",
    "INSTRUME", "OBJECT", "RA", "TELESCOP", "XBINNING", "XORGSUBF",
    "XPIXSZ", "YBINNING", "YORGSUBF", "YPIXSZ", "COMMENT", "HISTORY",
}
REQUIRED_METRICS = ("snr", "fwhm_px", "background", "final_score")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_package(sample_dir: Path) -> dict[str, Any]:
    import numpy as np
    from astropy.io import fits

    manifest_path = sample_dir / "manifest.json"
    golden_path = sample_dir / "golden.json"
    if not manifest_path.is_file() or not golden_path.is_file():
        raise ValueError("sample_data requires manifest.json and golden.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    frames = manifest.get("frames") or []
    if manifest.get("frame_count") != len(frames) or not frames:
        raise ValueError("manifest frame_count does not match a non-empty frames list")
    for frame in frames:
        path = sample_dir / frame["file"]
        if not path.is_file() or _sha256(path) != frame["sha256"]:
            raise ValueError(f"sample frame checksum mismatch: {frame['file']}")
        with fits.open(path, memmap=False) as hdus:
            leaked = sorted(PRIVATE_HEADER_KEYS.intersection(hdus[0].header))
            pixel_sha256 = hashlib.sha256(
                np.ascontiguousarray(hdus[0].data).tobytes()
            ).hexdigest()
            unexpected = sorted(
                key for key in hdus[0].header
                if key and not key.startswith("NAXIS") and key not in PUBLIC_HEADER_KEYS
            )
        if leaked:
            raise ValueError(f"private FITS metadata in {frame['file']}: {leaked}")
        if pixel_sha256 != frame.get("pixel_sha256"):
            raise ValueError(f"sample pixel checksum mismatch: {frame['file']}")
        if unexpected:
            raise ValueError(
                f"non-allowlisted FITS metadata in {frame['file']}: {unexpected}"
            )
    if golden.get("status") not in {
        "pending_baseline", "pending_second_machine", "verified"
    }:
        raise ValueError(
            "golden status must be pending_baseline, pending_second_machine, or verified"
        )
    metrics = golden.get("metrics") or {}
    missing = [name for name in REQUIRED_METRICS if name not in metrics]
    if missing:
        raise ValueError("golden metrics missing: " + ", ".join(missing))
    for name, spec in metrics.items():
        if name in REQUIRED_METRICS and not {"expected", "absolute_tolerance"} <= set(spec):
            raise ValueError(f"golden metric lacks expected/tolerance: {name}")
    return {"manifest": manifest, "golden": golden}


def measured_metrics(result_fits: Path, run_log: Path) -> dict[str, float]:
    from nas_server import image_analyzer

    stats = image_analyzer.analyze(str(result_fits))
    run = json.loads(run_log.read_text(encoding="utf-8"))
    return {
        "snr": float(stats["noise"]["snr"]),
        "fwhm_px": float(stats["psf"]["fwhm_median"]),
        "background": float(stats["background"]["sky_background"]),
        "final_score": float(run["final_scores"]["overall"]),
    }


def compare_metrics(actual: dict[str, float], golden: dict[str, Any]) -> list[dict[str, Any]]:
    comparisons = []
    for name in REQUIRED_METRICS:
        spec = golden["metrics"][name]
        expected = float(spec["expected"])
        tolerance = float(spec["absolute_tolerance"])
        value = float(actual[name])
        comparisons.append({
            "metric": name,
            "actual": value,
            "expected": expected,
            "absolute_tolerance": tolerance,
            "passed": math.isclose(value, expected, abs_tol=tolerance),
        })
    return comparisons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-dir", type=Path, default=Path("sample_data"))
    parser.add_argument("--result-fits", type=Path)
    parser.add_argument("--run-log", type=Path)
    args = parser.parse_args()
    package = validate_package(args.sample_dir)
    golden = package["golden"]
    if args.result_fits or args.run_log:
        if not (args.result_fits and args.run_log):
            raise SystemExit("--result-fits and --run-log must be supplied together")
        comparisons = compare_metrics(
            measured_metrics(args.result_fits, args.run_log), golden
        )
        print(json.dumps(comparisons, indent=2))
        if not all(item["passed"] for item in comparisons):
            raise SystemExit(1)
    elif golden["status"] != "verified":
        print("sample package: PASS; golden baseline awaits second-machine evidence")
        raise SystemExit(2)
    else:
        print("sample package and verified golden baseline: PASS")


if __name__ == "__main__":
    main()

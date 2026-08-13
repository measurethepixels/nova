#!/usr/bin/env python3
"""Fail-fast smoke check for a sanitized NOVA public release tree."""
from __future__ import annotations

import compileall
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    required = ("README.md", "REPLICATE.md", "RELEASE.md", "SHA256SUMS", "settings.example.json")
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("missing release files: " + ", ".join(missing))

    example = json.loads((ROOT / "settings.example.json").read_text(encoding="utf-8"))
    if not compileall.compile_dir(ROOT / "nas_server", quiet=1):
        raise SystemExit("Python source compilation failed")

    for line in (ROOT / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"checksum mismatch: {relative}")

    sys.path.insert(0, str(ROOT))
    from scripts.public_sample_verify import validate_package

    sample = validate_package(ROOT / "sample_data")
    incomplete = False
    if sample["manifest"].get("license_status") != "approved":
        print("public release smoke: sample data licence is still pending")
        incomplete = True
    if sample["golden"].get("status") != "verified":
        print("public release smoke: golden evidence is incomplete")
        incomplete = True

    with tempfile.TemporaryDirectory(prefix="nova-public-smoke-") as temp_dir:
        scratch = Path(temp_dir)
        for key in (
            "seestar_incoming_path",
            "seestar_library_path",
            "nas_work_path",
            "calibration_library_path",
            "pixinsight_cache_dir",
        ):
            if key in example:
                example[key] = str(scratch / key)
        if "db_path" in example:
            example["db_path"] = str(scratch / "astro_data.db")
        settings_path = scratch / "settings.json"
        settings_path.write_text(json.dumps(example), encoding="utf-8")
        os.environ["SEESTAR_SETTINGS"] = str(settings_path)
        __import__("nas_server.main")
    if incomplete:
        print("public release structural smoke: PASS (end-to-end release remains pending)")
    else:
        print("public release smoke: PASS")


if __name__ == "__main__":
    main()

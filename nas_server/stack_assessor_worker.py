"""Disposable worker for SEP-backed post-stack metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nas_server.stack_assessor import _assess_stack_direct


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: stack_assessor_worker REQUEST_JSON RESULT_JSON", file=sys.stderr)
        return 2
    request_path, result_name = map(Path, args)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = _assess_stack_direct(
            request["fits_path"],
            int(request["frame_count"]),
            list(request.get("single_frame_snrs") or []),
            bool(request.get("mask_zero_border", False)),
        )
        Path(result_name).write_text(
            json.dumps(result, allow_nan=False), encoding="utf-8"
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

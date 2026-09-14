"""Disposable native image-analysis worker.

This module intentionally has a tiny interface.  The parent service launches it
as a subprocess so a crash in SEP or another compiled dependency cannot bring
down uvicorn or erase the in-memory processing queue.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nas_server.image_analyzer import _analyze_direct


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: image_analyzer_worker FITS_PATH RESULT_JSON", file=sys.stderr)
        return 2
    fits_path, result_name = args
    result_path = Path(result_name)
    try:
        result = _analyze_direct(fits_path)
        result_path.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

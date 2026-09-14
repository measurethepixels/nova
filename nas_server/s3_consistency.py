"""Bounded handling for S3 read-after-write visibility races."""
from __future__ import annotations

import time
from collections.abc import Callable

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound", "NoSuchObject"})


def is_not_found_error(exc: Exception) -> bool:
    """Recognize structured S3 not-found responses without retrying auth errors."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error") or {}
    metadata = response.get("ResponseMetadata") or {}
    return (str(error.get("Code", "")) in _NOT_FOUND_CODES
            or metadata.get("HTTPStatusCode") == 404)


def retry_not_found(operation: Callable[[], None], *,
                    backoff_seconds: tuple[float, ...] = (0.5, 1.0, 2.0),
                    sleep: Callable[[float], None] = time.sleep) -> None:
    """Retry only eventual-consistency misses; propagate every final error."""
    for delay in (*backoff_seconds, None):
        try:
            operation()
            return
        except Exception as exc:
            if delay is None or not is_not_found_error(exc):
                raise
            sleep(delay)

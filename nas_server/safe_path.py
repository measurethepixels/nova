"""Contain filesystem paths beneath an explicitly trusted base directory."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class UnsafePathError(ValueError):
    """Raised when user-controlled path components escape their trusted base."""


def safe_resolve(base: Path, *parts: str) -> Path:
    """Resolve ``parts`` below ``base`` or raise :class:`UnsafePathError`.

    Backslashes are treated as separators even on Linux so Windows-style
    traversal cannot become dangerous if a path is later passed to another
    platform or path-aware tool.
    """
    resolved_base = Path(base).resolve()
    normalized: list[str] = []
    for raw_part in parts:
        if not isinstance(raw_part, str):
            raise UnsafePathError("path components must be strings")
        if "\x00" in raw_part:
            raise UnsafePathError("NUL is not allowed in a path")
        windows_path = PureWindowsPath(raw_part)
        if Path(raw_part).is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise UnsafePathError("absolute paths are not allowed")
        normalized.append(raw_part.replace("\\", "/"))

    candidate = resolved_base.joinpath(*normalized).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise UnsafePathError("path escapes its allowed base") from exc
    return candidate

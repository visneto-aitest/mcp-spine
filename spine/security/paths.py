"""
MCP Spine — Path Traversal Guard

Resolves and jails file paths to allowed root directories.
Prevents symlink escapes, null byte injection, and ../ traversal.
"""

from __future__ import annotations

from pathlib import Path


class PathViolation(Exception):
    """Raised when a path escapes the allowed jail."""


def validate_path(requested_path: str, allowed_roots: list[str]) -> Path:
    """
    Resolve a path and verify it lives under one of the allowed roots.

    Prevents:
      - ../../../etc/passwd
      - Symlink escapes
      - Null byte injection
      - Absolute paths outside jail

    Returns the resolved, safe Path.
    Raises PathViolation if the path escapes.
    """
    if "\x00" in requested_path:
        raise PathViolation(f"Null byte in path: {requested_path!r}")

    resolved = Path(requested_path).resolve()

    for root in allowed_roots:
        root_resolved = Path(root).resolve()
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue

    raise PathViolation(
        f"Path {resolved} escapes allowed roots: {allowed_roots}"
    )


def is_path_safe(requested_path: str, allowed_roots: list[str]) -> bool:
    """Non-throwing version of validate_path."""
    try:
        validate_path(requested_path, allowed_roots)
        return True
    except PathViolation:
        return False

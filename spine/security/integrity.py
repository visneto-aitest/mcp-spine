"""
MCP Spine — Integrity Helpers

Content-addressed hashing for the State Guard and
HMAC fingerprints for tamper-evident audit logging.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def hash_content(content: bytes) -> str:
    """SHA-256 hash of file content, hex-encoded."""
    return hashlib.sha256(content).hexdigest()


def hash_tool_schema(schema: dict[str, Any]) -> str:
    """
    Deterministic hash of a tool schema for change detection.
    Sorts keys to ensure consistent hashing.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def audit_fingerprint(
    event_type: str,
    tool_name: str,
    timestamp: float,
    payload_hash: str,
    secret_key: bytes | None = None,
) -> str:
    """
    Generate an HMAC fingerprint for an audit log entry.

    If no secret_key is provided, generates a simple SHA-256 hash
    (tamper-detectable but not tamper-proof without the key).
    """
    message = f"{event_type}|{tool_name}|{timestamp}|{payload_hash}"
    if secret_key:
        return hmac.new(secret_key, message.encode(), hashlib.sha256).hexdigest()[:24]
    return hashlib.sha256(message.encode()).hexdigest()[:24]

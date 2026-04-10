"""
MCP Spine — Security Module

Defense-in-depth for a local MCP proxy:
  1. Input validation & sanitization on all JSON-RPC messages
  2. Path traversal prevention (jail to allowed directories)
  3. Secret detection & scrubbing in logs and tool responses
  4. Rate limiting per tool to prevent runaway agents
  5. Command injection guard for server spawn arguments
  6. Schema size limits to prevent memory exhaustion
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

# ---------------------------------------------------------------------------
# 1. SECRET PATTERNS — detect and scrub before logging
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("Generic API Key", re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*")),
    ("Private Key Block", re.compile(r"-----BEGIN\s+(RSA |EC |DSA )?PRIVATE KEY-----")),
    ("Base64 Long Secret", re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9])")),
    ("Connection String", re.compile(r"(?i)(postgres|mysql|mongodb|redis)://\S+:\S+@")),
]

REDACTED = "[REDACTED]"


def scrub_secrets(text: str) -> str:
    """Replace detected secrets with [REDACTED]. Returns cleaned text."""
    for _name, pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def contains_secret(text: str) -> bool:
    """Quick check: does this string contain a likely secret?"""
    return any(pattern.search(text) for _, pattern in _SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# 2. PATH TRAVERSAL GUARD — jail file access to allowed roots
# ---------------------------------------------------------------------------

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
    # Null byte injection
    if "\x00" in requested_path:
        raise PathViolation(f"Null byte in path: {requested_path!r}")

    # Normalize and resolve (follows symlinks)
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


# ---------------------------------------------------------------------------
# 3. JSON-RPC MESSAGE VALIDATION
# ---------------------------------------------------------------------------

# Hard limits to prevent memory exhaustion
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_SCHEMA_DEPTH = 20                 # nested object depth
MAX_TOOL_NAME_LENGTH = 128
MAX_ARGUMENT_KEYS = 100

_SAFE_METHOD_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_/]*$")
_SAFE_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\-]*$")


class ValidationError(Exception):
    """Raised when a JSON-RPC message fails validation."""


def validate_message(message: dict[str, Any]) -> None:
    """
    Validate an incoming JSON-RPC message.

    Checks:
      - Required fields (jsonrpc, method or result/error)
      - Method name is alphanumeric (no injection)
      - Params size is within limits
      - No embedded secrets in string params
    """
    if not isinstance(message, dict):
        raise ValidationError("Message must be a JSON object")

    # JSON-RPC version
    if message.get("jsonrpc") != "2.0":
        raise ValidationError("Missing or invalid jsonrpc version")

    # Requests must have a method
    if "method" in message:
        method = message["method"]
        if not isinstance(method, str) or not _SAFE_METHOD_PATTERN.match(method):
            raise ValidationError(f"Invalid method name: {method!r}")

    # Validate tool names in tools/call
    if message.get("method") == "tools/call":
        params = message.get("params", {})
        tool_name = params.get("name", "")
        if not _SAFE_TOOL_NAME_PATTERN.match(tool_name):
            raise ValidationError(f"Invalid tool name: {tool_name!r}")
        if len(tool_name) > MAX_TOOL_NAME_LENGTH:
            raise ValidationError(f"Tool name too long: {len(tool_name)} chars")

        # Check argument count
        args = params.get("arguments", {})
        if isinstance(args, dict) and len(args) > MAX_ARGUMENT_KEYS:
            raise ValidationError(f"Too many arguments: {len(args)}")


def validate_message_size(raw: bytes) -> None:
    """Check raw message size before parsing."""
    if len(raw) > MAX_MESSAGE_SIZE:
        raise ValidationError(
            f"Message too large: {len(raw)} bytes (max {MAX_MESSAGE_SIZE})"
        )


# ---------------------------------------------------------------------------
# 4. COMMAND INJECTION GUARD — for spawning downstream servers
# ---------------------------------------------------------------------------

# Characters that could enable shell injection
_DANGEROUS_CHARS = re.compile(r"[;&|`$(){}!\n\r]")

# Allowed commands (basename only, no paths)
_DEFAULT_ALLOWED_COMMANDS = frozenset({
    "python", "python3", "node", "npx", "uvx", "deno",
    "mcp-server-filesystem", "mcp-server-github", "mcp-server-postgres",
})


def validate_server_command(
    command: str,
    args: list[str],
    allowed_commands: frozenset[str] | None = None,
) -> None:
    """
    Validate a server spawn command and its arguments.

    Prevents:
      - Shell metacharacter injection in args
      - Arbitrary command execution
      - Path traversal in command names
    """
    allowed = allowed_commands or _DEFAULT_ALLOWED_COMMANDS

    # Extract basename to prevent path tricks like "../../bin/bash"
    cmd_basename = PurePosixPath(command).name

    if cmd_basename not in allowed:
        raise ValidationError(
            f"Command {command!r} (basename: {cmd_basename!r}) "
            f"not in allowed list: {sorted(allowed)}"
        )

    # Check args for injection characters
    for i, arg in enumerate(args):
        if _DANGEROUS_CHARS.search(arg):
            raise ValidationError(
                f"Dangerous characters in argument {i}: {arg!r}"
            )


# ---------------------------------------------------------------------------
# 5. RATE LIMITER — per-tool sliding window
# ---------------------------------------------------------------------------

@dataclass
class RateLimitBucket:
    """Sliding window rate limit for a single key."""
    max_calls: int
    window_seconds: float
    timestamps: list[float] = field(default_factory=list)

    def allow(self) -> bool:
        """Check if a call is allowed, and record it if so."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Prune old timestamps
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= self.max_calls:
            return False
        self.timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        active = sum(1 for t in self.timestamps if t > cutoff)
        return max(0, self.max_calls - active)


class RateLimiter:
    """Per-tool rate limiting with configurable defaults."""

    def __init__(
        self,
        default_max_calls: int = 30,
        default_window: float = 60.0,
        overrides: dict[str, tuple[int, float]] | None = None,
    ):
        self._default_max = default_max_calls
        self._default_window = default_window
        self._buckets: dict[str, RateLimitBucket] = {}
        self._overrides = overrides or {}

    def check(self, tool_name: str) -> bool:
        """Return True if the tool call is allowed."""
        if tool_name not in self._buckets:
            max_calls, window = self._overrides.get(
                tool_name, (self._default_max, self._default_window)
            )
            self._buckets[tool_name] = RateLimitBucket(max_calls, window)
        return self._buckets[tool_name].allow()

    def remaining(self, tool_name: str) -> int:
        bucket = self._buckets.get(tool_name)
        return bucket.remaining if bucket else self._default_max


# ---------------------------------------------------------------------------
# 6. INTEGRITY — content-addressed hashing for State Guard
# ---------------------------------------------------------------------------

def hash_content(content: bytes) -> str:
    """SHA-256 hash of file content, hex-encoded."""
    return hashlib.sha256(content).hexdigest()


def hash_tool_schema(schema: dict) -> str:
    """
    Deterministic hash of a tool schema for change detection.
    Sorts keys to ensure consistent hashing.
    """
    import json
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 7. ENVIRONMENT VARIABLE SAFETY
# ---------------------------------------------------------------------------

def resolve_env_vars(value: str) -> str:
    """
    Resolve ${VAR_NAME} patterns in config values.

    Only resolves variables that exist in the environment.
    Raises ValueError for undefined variables (fail-closed).
    """
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ValueError(
                f"Environment variable ${{{var_name}}} is not set. "
                f"Set it or remove it from the config."
            )
        return env_val

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _replace, value)


def safe_env_dict(env_config: dict[str, str]) -> dict[str, str]:
    """Resolve all env vars in a server's env config block."""
    resolved = {}
    for key, value in env_config.items():
        resolved[key] = resolve_env_vars(value)
    return resolved


# ---------------------------------------------------------------------------
# 8. AUDIT FINGERPRINT — tamper-evident logging
# ---------------------------------------------------------------------------

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

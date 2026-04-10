"""
MCP Spine — Message Validation

Input validation for JSON-RPC 2.0 messages: size limits,
method name allowlisting, tool name sanitisation, and
argument count caps.
"""

from __future__ import annotations

import re
from typing import Any

MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_SCHEMA_DEPTH = 20
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
      - Tool name sanitisation in tools/call
    """
    if not isinstance(message, dict):
        raise ValidationError("Message must be a JSON object")

    if message.get("jsonrpc") != "2.0":
        raise ValidationError("Missing or invalid jsonrpc version")

    if "method" in message:
        method = message["method"]
        if not isinstance(method, str) or not _SAFE_METHOD_PATTERN.match(method):
            raise ValidationError(f"Invalid method name: {method!r}")

    if message.get("method") == "tools/call":
        params = message.get("params", {})
        tool_name = params.get("name", "")
        if not _SAFE_TOOL_NAME_PATTERN.match(tool_name):
            raise ValidationError(f"Invalid tool name: {tool_name!r}")
        if len(tool_name) > MAX_TOOL_NAME_LENGTH:
            raise ValidationError(f"Tool name too long: {len(tool_name)} chars")

        args = params.get("arguments", {})
        if isinstance(args, dict) and len(args) > MAX_ARGUMENT_KEYS:
            raise ValidationError(f"Too many arguments: {len(args)}")


def validate_message_size(raw: bytes) -> None:
    """Check raw message size before parsing."""
    if len(raw) > MAX_MESSAGE_SIZE:
        raise ValidationError(
            f"Message too large: {len(raw)} bytes (max {MAX_MESSAGE_SIZE})"
        )

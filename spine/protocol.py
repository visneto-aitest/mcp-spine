"""
MCP Spine — JSON-RPC Protocol Handler

Handles reading and writing MCP-compliant JSON-RPC 2.0 messages
over stdio streams. Includes message validation and size limits.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from spine.security import ValidationError, validate_message, validate_message_size


class ProtocolError(Exception):
    """Raised on malformed or oversized messages."""


async def read_jsonrpc(
    reader: asyncio.StreamReader,
    max_size: int = 10 * 1024 * 1024,
) -> AsyncIterator[dict[str, Any]]:
    """
    Read JSON-RPC messages from a stream.

    MCP uses newline-delimited JSON over stdio.
    Each line is a complete JSON-RPC message.

    Validates:
      - Size limits (before parsing)
      - JSON structure (during parsing)
      - JSON-RPC conformance (after parsing)
    """
    while True:
        try:
            line = await reader.readline()
        except (asyncio.IncompleteReadError, ConnectionError):
            break

        if not line:
            break  # EOF

        # Strip trailing newline/whitespace
        line = line.strip()
        if not line:
            continue  # skip empty lines

        # Size check before parsing
        try:
            validate_message_size(line)
        except ValidationError as e:
            raise ProtocolError(str(e)) from e

        # Parse JSON
        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            raise ProtocolError(f"Invalid JSON: {e}") from e

        # Validate JSON-RPC structure
        try:
            validate_message(message)
        except ValidationError as e:
            raise ProtocolError(f"Invalid JSON-RPC: {e}") from e

        yield message


async def write_jsonrpc(
    writer: asyncio.StreamWriter,
    message: dict[str, Any],
) -> None:
    """
    Write a JSON-RPC message to a stream.

    Serializes to compact JSON + newline.
    """
    data = json.dumps(message, separators=(",", ":")) + "\n"
    writer.write(data.encode())
    await writer.drain()


def make_response(
    id: int | str | None,
    result: Any,
) -> dict[str, Any]:
    """Create a JSON-RPC 2.0 success response."""
    return {
        "jsonrpc": "2.0",
        "id": id,
        "result": result,
    }


def make_error(
    id: int | str | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Create a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": error,
    }


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP-specific error codes
TOOL_NOT_FOUND = -32000
TOOL_BLOCKED = -32001
RATE_LIMITED = -32002
PATH_VIOLATION = -32003

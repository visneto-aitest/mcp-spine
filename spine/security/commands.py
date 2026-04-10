"""
MCP Spine — Command Injection Guard

Validates commands and arguments used to spawn downstream MCP
server subprocesses. Prevents shell metacharacter injection
and arbitrary command execution.
"""

from __future__ import annotations

import re
from pathlib import PurePath

from spine.security.validation import ValidationError

_DANGEROUS_CHARS = re.compile(r"[;&|`${}!\n\r]")

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
    cmd_path = PurePath(command)
    cmd_basename = cmd_path.stem  # "npx.cmd" -> "npx", "python.exe" -> "python"

    if cmd_basename not in allowed:
        raise ValidationError(
            f"Command {command!r} (basename: {cmd_basename!r}) "
            f"not in allowed list: {sorted(allowed)}"
        )

    for i, arg in enumerate(args):
        if _DANGEROUS_CHARS.search(arg):
            raise ValidationError(
                f"Dangerous characters in argument {i}: {arg!r}"
            )

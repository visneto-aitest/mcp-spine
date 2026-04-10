"""
MCP Spine — Environment Variable Safety

Fail-closed resolution of ${VAR} patterns in config values.
Undefined variables raise immediately rather than passing empty strings.
"""

from __future__ import annotations

import os
import re


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

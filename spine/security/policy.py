"""
MCP Spine — Security Policy

Declarative security policies loaded from spine.toml.
Defines what tools, paths, commands, and patterns are allowed or denied.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PolicyAction(Enum):
    ALLOW = "allow"
    DENY = "deny"
    AUDIT = "audit"  # allow but log with warning


@dataclass
class ToolPolicy:
    """Per-tool security policy."""
    name_pattern: str  # glob pattern, e.g. "file_*" or "github_*"
    action: PolicyAction = PolicyAction.ALLOW
    rate_limit: int | None = None          # calls per minute
    allowed_arg_patterns: dict[str, str] = field(default_factory=dict)
    blocked_arg_patterns: dict[str, str] = field(default_factory=dict)
    require_confirmation: bool = False      # future: human-in-the-loop

    def matches(self, tool_name: str) -> bool:
        return fnmatch.fnmatch(tool_name, self.name_pattern)


@dataclass
class PathPolicy:
    """File system access policy."""
    allowed_roots: list[str] = field(default_factory=lambda: ["."])
    denied_patterns: list[str] = field(default_factory=lambda: [
        "**/.env",
        "**/.env.*",
        "**/secrets.*",
        "**/*.pem",
        "**/*.key",
        "**/id_rsa*",
        "**/.ssh/*",
        "**/.aws/*",
        "**/.gnupg/*",
        "**/node_modules/.cache/**",
    ])
    max_file_size: int = 50 * 1024 * 1024  # 50 MB

    def is_path_allowed(self, path: str) -> bool:
        """Check if a path is allowed by policy."""
        for pattern in self.denied_patterns:
            if fnmatch.fnmatch(path, pattern):
                return False
        return True


@dataclass
class NetworkPolicy:
    """Network access controls for downstream servers."""
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost", "127.0.0.1"])
    blocked_hosts: list[str] = field(default_factory=list)
    max_connections_per_server: int = 5


@dataclass
class SecurityPolicy:
    """
    Root security policy for the Spine.

    Loaded from [security] section of spine.toml.
    Defaults are secure (deny-by-default for dangerous ops).
    """
    # Global switches
    scrub_secrets_in_logs: bool = True
    scrub_secrets_in_responses: bool = False  # opt-in: may break tool output
    audit_all_tool_calls: bool = True
    max_message_size: int = 10 * 1024 * 1024  # 10 MB

    # Rate limiting
    global_rate_limit: int = 60        # calls per minute across all tools
    per_tool_rate_limit: int = 30      # calls per minute per tool

    # Sub-policies
    tool_policies: list[ToolPolicy] = field(default_factory=list)
    path_policy: PathPolicy = field(default_factory=PathPolicy)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)

    # Command allowlist for spawning servers
    allowed_commands: frozenset[str] = frozenset({
        "python", "python3", "node", "npx", "uvx", "deno",
    })

    def get_tool_policy(self, tool_name: str) -> ToolPolicy | None:
        """Find the first matching tool policy for a given tool name."""
        for policy in self.tool_policies:
            if policy.matches(tool_name):
                return policy
        return None

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool call is permitted by policy."""
        policy = self.get_tool_policy(tool_name)
        if policy is None:
            return True  # no explicit policy = allowed
        return policy.action != PolicyAction.DENY

    def should_audit_tool(self, tool_name: str) -> bool:
        """Check if a tool call should be audit-logged."""
        if self.audit_all_tool_calls:
            return True
        policy = self.get_tool_policy(tool_name)
        return policy is not None and policy.action == PolicyAction.AUDIT


def load_security_policy(config: dict[str, Any]) -> SecurityPolicy:
    """
    Load security policy from parsed TOML config.

    Example spine.toml:
        [security]
        scrub_secrets_in_logs = true
        audit_all_tool_calls = true
        global_rate_limit = 60

        [security.path]
        allowed_roots = ["/home/user/project"]
        denied_patterns = ["**/.env", "**/*.key"]

        [[security.tools]]
        pattern = "execute_*"
        action = "deny"

        [[security.tools]]
        pattern = "file_write"
        action = "audit"
        rate_limit = 10
    """
    sec = config.get("security", {})
    policy = SecurityPolicy(
        scrub_secrets_in_logs=sec.get("scrub_secrets_in_logs", True),
        scrub_secrets_in_responses=sec.get("scrub_secrets_in_responses", False),
        audit_all_tool_calls=sec.get("audit_all_tool_calls", True),
        max_message_size=sec.get("max_message_size", 10 * 1024 * 1024),
        global_rate_limit=sec.get("global_rate_limit", 60),
        per_tool_rate_limit=sec.get("per_tool_rate_limit", 30),
    )

    # Path policy
    path_cfg = sec.get("path", {})
    if path_cfg:
        policy.path_policy = PathPolicy(
            allowed_roots=path_cfg.get("allowed_roots", ["."]),
            denied_patterns=path_cfg.get("denied_patterns", policy.path_policy.denied_patterns),
            max_file_size=path_cfg.get("max_file_size", policy.path_policy.max_file_size),
        )

    # Tool policies
    for tool_cfg in sec.get("tools", []):
        tp = ToolPolicy(
            name_pattern=tool_cfg["pattern"],
            action=PolicyAction(tool_cfg.get("action", "allow")),
            rate_limit=tool_cfg.get("rate_limit"),
            require_confirmation=tool_cfg.get("require_confirmation", False),
        )
        policy.tool_policies.append(tp)

    # Command allowlist
    if "allowed_commands" in sec:
        policy.allowed_commands = frozenset(sec["allowed_commands"])

    return policy

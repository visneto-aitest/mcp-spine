# MCP Spine

**Context Minifier & State Guard** — A local-first MCP middleware proxy that reduces token waste, prevents tool attrition, and eliminates context rot.

## Quick Start

```bash
# Install
pip install -e .

# Generate config
mcp-spine init

# Edit spine.toml to add your MCP servers, then:
mcp-spine verify    # validate config
mcp-spine serve     # start the proxy
```

### Claude Desktop Integration

Replace all your individual MCP server entries with a single Spine entry:

```json
{
  "mcpServers": {
    "spine": {
      "command": "mcp-spine",
      "args": ["serve", "--config", "/path/to/spine.toml"]
    }
  }
}
```

## Security Model

The Spine is designed with defense-in-depth. Every layer assumes the others might fail.

### Threat Model

| Threat | Mitigation |
|---|---|
| **Prompt injection via tool args** | Input validation on all JSON-RPC messages; tool name and method allowlists |
| **Path traversal** | All file paths resolved and jailed to `allowed_roots`; symlink-aware |
| **Secret leakage in logs** | Automatic regex-based scrubbing of AWS keys, GitHub tokens, bearer tokens, private keys, connection strings |
| **Runaway agent loops** | Per-tool and global rate limiting with sliding windows |
| **Command injection via server spawn** | Command allowlist (only `python`, `node`, `npx`, etc.); shell metacharacter blocking in args |
| **Denial of service** | Message size limits (10MB); schema depth limits; circuit breakers on failing servers |
| **Sensitive file access** | Deny-list patterns for `.env`, `.key`, `.pem`, `.ssh/`, `.aws/` files |
| **Tool abuse** | Policy-based tool blocking (`action = "deny"`) and audit logging (`action = "audit"`) |
| **Log tampering** | HMAC fingerprints on every audit entry |
| **Env var exposure** | Fail-closed resolution: undefined `${VAR}` raises an error, never silently passes empty |

### Security Configuration

```toml
[security]
scrub_secrets_in_logs = true       # Always on in production
audit_all_tool_calls = true        # Full audit trail
global_rate_limit = 60             # Calls/minute across all tools

[security.path]
allowed_roots = ["/home/user/project"]
denied_patterns = ["**/.env", "**/*.key"]

[[security.tools]]
pattern = "execute_*"
action = "deny"                    # Block dangerous tools entirely
```

### Audit Trail

Every tool call is logged to SQLite with tamper-evident fingerprints:

```bash
# View recent audit entries
mcp-spine audit --last 50

# Security events only
mcp-spine audit --security-only

# Filter by tool
mcp-spine audit --tool write_file --last 20
```

## Architecture

```
Client ◄──stdio──► MCP Spine ◄──stdio──► Server A
                       │                  Server B
                       │                  Server C
                   ┌───┴───┐
                   │Router │  ← Stage 2: Semantic routing
                   │Minify │  ← Stage 3: Schema minification
                   │Guard  │  ← Stage 4: State guard
                   │SecPol │  ← Security policy enforcement
                   └───────┘
```

## Build Stages

| Stage | Description | Status |
|---|---|---|
| 1 | Secure proxy with audit logging | ✅ Built |
| 2 | Semantic routing (local embeddings) | 🔜 Next |
| 3 | Schema minification | 🔜 Planned |
| 4 | State Guard (file truth pinning) | 🔜 Planned |
| 5 | CLI polish & dashboard | 🔜 Planned |

## Project Structure

```
mcp-spine/
├── pyproject.toml          # Dependencies & CLI entry point
├── spine/
│   ├── __init__.py
│   ├── cli.py              # Click CLI (init, serve, verify, audit)
│   ├── config.py           # TOML config loader with validation
│   ├── proxy.py            # Core proxy event loop
│   ├── protocol.py         # JSON-RPC message handling
│   ├── transport.py        # Downstream server management
│   ├── audit.py            # Structured logging + SQLite trail
│   └── security/
│       ├── __init__.py     # Guards: secrets, paths, rate limits, validation
│       └── policy.py       # Declarative security policies
├── tests/
│   ├── test_security.py    # Security test suite
│   └── test_config.py      # Config validation tests
└── configs/
    └── example.spine.toml  # Real-world example config
```

## License

MIT

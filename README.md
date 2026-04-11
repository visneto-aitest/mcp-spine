# MCP Spine

**Context Minifier & State Guard** — A local-first MCP middleware proxy that reduces token waste, prevents tool attrition, and eliminates context rot.

MCP Spine sits between your LLM client (Claude Desktop, etc.) and your MCP servers, providing security hardening, intelligent tool routing, schema compression, and file state tracking — all through a single proxy.

## Why

LLM agents using MCP tools face three problems:

1. **Token waste** — Tool schemas consume thousands of tokens per request. With 40+ tools loaded, you're burning context on JSON schemas before the conversation even starts.
2. **Context rot** — In long sessions, LLMs revert to editing old file versions they memorized earlier, silently overwriting your latest changes.
3. **No security boundary** — MCP servers run with full access. There's no audit trail, no rate limiting, no secret scrubbing between the LLM and your tools.

MCP Spine solves all three.

## Features

### Stage 1: Security Proxy
- JSON-RPC message validation and sanitization
- Secret scrubbing (AWS keys, GitHub tokens, bearer tokens, private keys, connection strings)
- Per-tool and global rate limiting with sliding windows
- Path traversal prevention with symlink-aware jail
- Command injection guards for server spawning
- HMAC-fingerprinted SQLite audit trail
- Circuit breakers on failing servers
- Declarative security policies from config

### Stage 2: Semantic Router
- Local vector embeddings using `all-MiniLM-L6-v2` (no API calls, no data leaves your machine)
- ChromaDB-backed tool indexing
- Query-time routing: only the most relevant tools are sent to the LLM
- `spine_set_context` meta-tool for explicit context switching
- Keyword overlap + recency boost reranking
- Background model loading — tools work immediately, routing activates when ready

### Stage 3: Schema Minification
- 4 aggression levels (0=off, 1=light, 2=standard, 3=aggressive)
- Level 2 achieves **61% token savings** on tool schemas
- Strips `$schema`, titles, `additionalProperties`, parameter descriptions, defaults
- Preserves all required fields and type information

### Stage 4: State Guard
- Watches project files via `watchfiles`
- Maintains SHA-256 manifest with monotonic versioning
- Injects compact state pins into tool responses
- Prevents LLMs from editing stale file versions

### Human-in-the-Loop
- `require_confirmation` policy flag for destructive tools
- Spine intercepts the call, shows the arguments, and waits for user approval
- `spine_confirm` / `spine_deny` meta-tools for the LLM to relay the decision
- Per-tool granularity via glob patterns

## Quick Start

```bash
# Install core
pip install -e .

# Install with semantic routing (optional)
pip install -e ".[ml]"

# Generate config
mcp-spine init

# Edit spine.toml to add your MCP servers, then:
mcp-spine verify    # validate config
mcp-spine serve     # start the proxy
```

## Claude Desktop Integration

Replace all your individual MCP server entries with a single Spine entry:

```json
{
  "mcpServers": {
    "spine": {
      "command": "python",
      "args": ["-u", "-m", "spine.cli", "serve", "--config", "/path/to/spine.toml"],
      "cwd": "/path/to/mcp-spine"
    }
  }
}
```

The `-u` flag ensures unbuffered stdout, preventing pipe hangs on Windows.

### Example Config

```toml
[spine]
log_level = "info"
audit_db = "spine_audit.db"

# Add as many servers as you need
[[servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/project"]
timeout_seconds = 120

[[servers]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "ghp_..." }
timeout_seconds = 60

# Semantic routing — filter to top-K tools per request
[routing]
max_tools = 20
rerank = true

# Schema minification — 61% token savings at level 2
[minifier]
level = 2

# State guard — prevent context rot
[state_guard]
enabled = true
watch_paths = ["/path/to/project"]

# Human-in-the-loop — require approval for destructive tools
[[security.tools]]
pattern = "write_file"
action = "allow"
require_confirmation = true

[[security.tools]]
pattern = "delete_file"
action = "allow"
require_confirmation = true

# Security
[security]
scrub_secrets_in_logs = true
audit_all_tool_calls = true
global_rate_limit = 120
per_tool_rate_limit = 60

[security.path]
allowed_roots = ["/path/to/project"]
denied_patterns = ["**/.env", "**/*.key", "**/*.pem"]
```

## Security Model

The Spine is designed with defense-in-depth. Every layer assumes the others might fail.

| Threat | Mitigation |
|---|---|
| **Prompt injection via tool args** | Input validation on all JSON-RPC messages; tool name and method allowlists |
| **Path traversal** | All file paths resolved and jailed to `allowed_roots`; symlink-aware |
| **Secret leakage in logs** | Automatic regex-based scrubbing of AWS keys, GitHub tokens, bearer tokens, private keys, connection strings |
| **Runaway agent loops** | Per-tool and global rate limiting with sliding windows |
| **Command injection via server spawn** | Command allowlist; shell metacharacter blocking; `PureWindowsPath` basename extraction for paths with spaces/parens |
| **Denial of service** | Message size limits (10MB); schema depth limits; circuit breakers on failing servers |
| **Sensitive file access** | Deny-list patterns for `.env`, `.key`, `.pem`, `.ssh/`, `.aws/` files |
| **Tool abuse** | Policy-based tool blocking, audit logging, and human-in-the-loop confirmation |
| **Log tampering** | HMAC fingerprints on every audit entry |
| **Env var exposure** | Fail-closed resolution: undefined `${VAR}` raises an error |
| **Destructive operations** | `require_confirmation` flag pauses execution until user approves |

### Audit Trail

Every tool call is logged to SQLite with tamper-evident fingerprints:

```bash
mcp-spine audit --last 50          # Recent entries
mcp-spine audit --security-only    # Security events only
mcp-spine audit --tool write_file  # Filter by tool
```

## Architecture

```
Client ◄──stdio──► MCP Spine ◄──stdio──► Filesystem Server
                       │                  GitHub Server
                       │                  Any MCP Server
                   ┌───┴───┐
                   │SecPol │  ← Rate limits, path jail, secret scrub
                   │Router │  ← Semantic routing (local embeddings)
                   │Minify │  ← Schema compression (61% savings)
                   │Guard  │  ← File state pinning (SHA-256)
                   │HITL   │  ← Human-in-the-loop confirmation
                   └───────┘
```

### Startup Sequence

The Spine uses a two-phase background initialization:

1. **Instant handshake** (~2ms) — Responds to `initialize` immediately
2. **Background init** — Connects servers concurrently, sets ready as soon as any server has tools
3. **ML loading** — Semantic router model loads in a separate background task; routing activates silently when done

This ensures Claude Desktop never times out, even with slow servers or large ML models.

## Windows Support

MCP Spine is battle-tested on Windows with specific hardening for:

- MSIX sandbox paths for Claude Desktop config and logs
- `npx.cmd` resolution via `shutil.which()`
- Paths with spaces (`C:\Users\John Doe\...`) and parentheses (`C:\Program Files (x86)\...`)
- `PureWindowsPath` for cross-platform basename extraction
- UTF-8 encoding without BOM for config file generation
- Unbuffered stdout (`-u` flag) to prevent pipe hangs

## Project Structure

```
mcp-spine/
├── pyproject.toml
├── spine/
│   ├── cli.py              # Click CLI (init, serve, verify, audit)
│   ├── config.py           # TOML config loader with validation
│   ├── proxy.py            # Core proxy event loop
│   ├── protocol.py         # JSON-RPC message handling
│   ├── transport.py        # Server pool, circuit breakers, concurrent startup
│   ├── audit.py            # Structured logging + SQLite audit trail
│   ├── router.py           # Semantic routing (ChromaDB + sentence-transformers)
│   ├── minifier.py         # Schema pruning (4 aggression levels)
│   ├── state_guard.py      # File watcher + SHA-256 manifest + pin injection
│   └── security/
│       ├── secrets.py      # Credential detection & scrubbing
│       ├── paths.py        # Path traversal jail
│       ├── validation.py   # JSON-RPC message validation
│       ├── commands.py     # Server spawn guards
│       ├── rate_limit.py   # Sliding window throttling
│       ├── integrity.py    # SHA-256 + HMAC fingerprints
│       ├── env.py          # Fail-closed env var resolution
│       └── policy.py       # Declarative security policies
├── tests/
│   ├── test_security.py    # 93 security tests
│   ├── test_config.py      # Config validation tests
│   ├── test_minifier.py    # Schema minification tests
│   └── test_state_guard.py # State guard tests
└── configs/
    └── example.spine.toml
```

## Tests

```bash
# Run all tests
pytest

# Security tests only
pytest tests/test_security.py -v

# End-to-end tests
python test_e2e.py
```

98 tests covering security, config validation, schema minification, state guard, and Windows path edge cases.

## License

MIT

"""
MCP Spine - End-to-End Test

Spawns `mcp-spine serve` as a subprocess, sends JSON-RPC messages
through stdin, reads responses from stdout, and verifies everything
works correctly.

Usage:
    python test_e2e.py

Prerequisites:
    - npm/npx installed (for @modelcontextprotocol/server-filesystem)
    - pip install -e ".[dev]"
"""

import json
import subprocess
import sys
import time
import os


class SpineE2ETest:
    def __init__(self, config_path="spine_test.toml"):
        self.config_path = config_path
        self.process = None
        self.passed = 0
        self.failed = 0

    def start_spine(self):
        """Spawn the Spine as a subprocess."""
        print("\n[1/6] Starting mcp-spine serve...")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "spine.cli", "serve", "--config", self.config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
        )
        # Give the Spine time to start and connect to downstream servers
        time.sleep(3)

        if self.process.poll() is not None:
            stderr = self.process.stderr.read().decode(errors="replace")
            print(f"  FATAL: Spine exited early. stderr:\n{stderr}")
            sys.exit(1)

        print("  Spine is running (pid: {})".format(self.process.pid))

    def send(self, message: dict) -> dict | None:
        """Send a JSON-RPC message and read the response."""
        line = json.dumps(message) + "\n"
        try:
            self.process.stdin.write(line.encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            print(f"  ERROR writing to Spine: {e}")
            return None

        # Read response with timeout
        try:
            # Give the server time to process
            time.sleep(1)
            response_line = self.process.stdout.readline()
            if not response_line:
                print("  ERROR: No response from Spine (empty)")
                return None
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            print(f"  ERROR: Invalid JSON response: {e}")
            print(f"  Raw: {response_line!r}")
            return None
        except Exception as e:
            print(f"  ERROR reading response: {e}")
            return None

    def check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            self.passed += 1
            print(f"  PASS: {name}")
        else:
            self.failed += 1
            print(f"  FAIL: {name} {detail}")

    def test_initialize(self):
        """Test MCP initialize handshake."""
        print("\n[2/6] Testing initialize handshake...")
        resp = self.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "1.0"},
            },
        })

        self.check("Got response", resp is not None)
        if resp is None:
            return

        result = resp.get("result", {})
        self.check("Has serverInfo", "serverInfo" in result)
        self.check(
            "Server is mcp-spine",
            result.get("serverInfo", {}).get("name") == "mcp-spine",
        )
        self.check("Has capabilities", "capabilities" in result)

        # Send initialized notification (no response expected)
        self.process.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        self.process.stdin.flush()
        time.sleep(0.5)

    def test_tools_list(self) -> list:
        """Test tools/list - should return filesystem tools."""
        print("\n[3/6] Testing tools/list...")
        resp = self.send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })

        self.check("Got response", resp is not None)
        if resp is None:
            return []

        result = resp.get("result", {})
        tools = result.get("tools", [])
        self.check("Has tools", len(tools) > 0, f"(got {len(tools)})")

        tool_names = [t["name"] for t in tools]
        print(f"  Tools found: {tool_names}")

        # The filesystem server should provide these
        expected = {"read_file", "write_file", "list_directory"}
        found = expected & set(tool_names)
        self.check(
            "Has filesystem tools",
            len(found) > 0,
            f"(expected some of {expected}, found {found})",
        )

        # Check that spine_set_context is included
        self.check(
            "Has spine_set_context",
            "spine_set_context" in tool_names,
        )

        return tools

    def test_tool_call(self):
        """Test calling a real tool - list the current directory."""
        print("\n[4/6] Testing tools/call (list_directory)...")
        resp = self.send({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_directory",
                "arguments": {"path": "."},
            },
        })

        self.check("Got response", resp is not None)
        if resp is None:
            return

        result = resp.get("result", {})
        # The result should contain directory contents
        content = result.get("content", [])
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", "")
            self.check("Got directory listing", len(text) > 0)
            # Should see our own files
            has_spine = "spine" in text.lower() or "toml" in text.lower()
            self.check("Lists project files", has_spine, f"(content: {text[:200]}...)")
        else:
            self.check("Got directory listing", False, f"(result: {result})")

    def test_security_blocked(self):
        """Test that invalid tool names are rejected."""
        print("\n[5/6] Testing security (invalid tool name)...")
        resp = self.send({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "evil; rm -rf /",
                "arguments": {},
            },
        })

        self.check("Got response", resp is not None)
        if resp is None:
            return

        # Should get a validation error, not a crash
        error = resp.get("error")
        self.check("Rejected malicious tool name", error is not None)
        if error:
            self.check(
                "Error message is helpful",
                "Invalid" in error.get("message", "") or "tool" in error.get("message", "").lower(),
            )

    def test_spine_set_context(self):
        """Test the spine_set_context meta-tool."""
        print("\n[6/6] Testing spine_set_context...")
        resp = self.send({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "spine_set_context",
                "arguments": {"task": "reading files from the project directory"},
            },
        })

        self.check("Got response", resp is not None)
        if resp is None:
            return

        result = resp.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", "")
            self.check("Context updated", "context" in text.lower() or "tools" in text.lower())
        else:
            self.check("Context updated", False, f"(result: {result})")

    def stop_spine(self):
        """Shut down the Spine subprocess."""
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def run(self):
        """Run all end-to-end tests."""
        print("=" * 60)
        print("  MCP Spine - End-to-End Test")
        print("=" * 60)

        try:
            self.start_spine()
            self.test_initialize()
            self.test_tools_list()
            self.test_tool_call()
            self.test_security_blocked()
            self.test_spine_set_context()
        except Exception as e:
            print(f"\n  UNEXPECTED ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.stop_spine()

            # Print stderr from Spine (debug/audit logs)
            if self.process and self.process.stderr:
                stderr = self.process.stderr.read().decode(errors="replace")
                if stderr.strip():
                    print("\n--- Spine stderr (audit log) ---")
                    for line in stderr.strip().split("\n")[-20:]:
                        print(f"  {line}")

        print("\n" + "=" * 60)
        print(f"  {self.passed} passed, {self.failed} failed")
        print("=" * 60)

        return self.failed == 0


if __name__ == "__main__":
    test = SpineE2ETest()
    success = test.run()
    sys.exit(0 if success else 1)

"""CLI and MCP server tests (offline)."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest

from quotecheck import cli
from quotecheck._testing import allow_loopback_endpoints

from .fixture_server import FixtureServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAS_MCP = importlib.util.find_spec("mcp") is not None
QUOTE = "Fish & chips are not part of this study."


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", "quotecheck", *args], cwd=ROOT,
                          capture_output=True, text=True, env=env, timeout=60)


class CliTests(unittest.TestCase):
    def test_cli_prints_json_for_refused_source(self):
        proc = run_cli("hello", "ftp://example.com/x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "SOURCE_UNAVAILABLE")
        for key in ("char_start", "char_end", "similarity", "source_final_url", "snippet", "reason"):
            self.assertIn(key, data)

    def test_cli_strict_exit_code_and_loopback_refused(self):
        # A separate process has no test override, so loopback is refused.
        with FixtureServer() as srv:
            proc = run_cli("--strict", QUOTE, srv.base + "/article")
            self.assertEqual(srv.requests, [])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a public address", json.loads(proc.stdout)["reason"])

    def test_cli_exact_in_process(self):
        with FixtureServer() as srv, allow_loopback_endpoints(("127.0.0.1", srv.port)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.main(["--strict", QUOTE, srv.base + "/article"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "EXACT")
        self.assertEqual(data["similarity"], 1.0)

    def test_cli_empty_quote_is_usage_error(self):
        proc = run_cli("   ", "https://example.com/")
        self.assertEqual(proc.returncode, 2)

    def test_core_does_not_import_mcp(self):
        code = "import sys, quotecheck, quotecheck.cli; print('mcp' in sys.modules)"
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(proc.stdout.strip(), "False", proc.stderr)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed")
class McpTests(unittest.TestCase):
    def setUp(self):
        from quotecheck.mcp_server import build_server
        self.server = build_server()

    def test_single_tool_with_clear_schema(self):
        tools = asyncio.run(self.server.list_tools())
        self.assertEqual([t.name for t in tools], ["check_quote"])
        tool = tools[0]
        self.assertEqual(sorted(tool.inputSchema["properties"]), ["quote", "source"])
        self.assertEqual(sorted(tool.inputSchema.get("required", [])), ["quote", "source"])
        for word in ("EXACT", "NEAR", "NOT_FOUND", "SOURCE_UNAVAILABLE", "doi:", "pmid:"):
            self.assertIn(word, tool.description)

    def test_call_tool(self):
        result = asyncio.run(self.server.call_tool(
            "check_quote", {"quote": "x", "source": "ftp://example.com/x"}))
        self.assertIn("SOURCE_UNAVAILABLE", repr(result))

        with FixtureServer() as srv, allow_loopback_endpoints(("127.0.0.1", srv.port)):
            result = asyncio.run(self.server.call_tool(
                "check_quote", {"quote": QUOTE, "source": srv.base + "/article"}))
        self.assertIn('EXACT', repr(result))
        self.assertNotIn("SOURCE_UNAVAILABLE", repr(result))


if __name__ == "__main__":
    unittest.main()

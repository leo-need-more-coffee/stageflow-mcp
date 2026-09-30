"""The whole thing, through the transport a client will actually use.

Everything else here drives the server in-process, which leaves one thing
untested and it is the one a user meets first: that `stageflow-mcp` starts,
speaks MCP on stdio, and answers with the tools it is supposed to have. The
backend it talks to is the fake, so this needs no network.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fake import FakeBackend

ROOT = Path(__file__).resolve().parents[1]


def _has_client() -> bool:
    try:
        import mcp.client.stdio  # noqa: F401
        return True
    except ImportError:  # pragma: no cover
        return False


@unittest.skipUnless(_has_client(), "the MCP client is part of the SDK; install it to run this")
class StdioTests(unittest.TestCase):
    def test_a_server_with_no_address_asks_for_one(self):
        """Started with no --backend, the first tool asks the CLIENT — not the
        conversation — and then works against what it was told."""
        with tempfile.TemporaryDirectory() as config:
            with FakeBackend() as fake:
                report = asyncio.run(_talk_unconfigured(fake.url, config))
            self.assertEqual(report["asked"], 1)
            self.assertTrue(report["verdict"]["ok"], report["verdict"])
            self.assertIn(fake.url, report["verdict"]["checked_by"])
            # the address is remembered for next time, the credential never is
            saved = json.loads(
                (Path(config) / "stageflow-mcp" / "config.json").read_text())
            self.assertEqual(saved["backend"], fake.url)
            self.assertNotIn("token", saved)

    def test_a_client_can_connect_and_work(self):
        with FakeBackend() as fake:
            report = asyncio.run(_talk(fake.url))

        self.assertEqual(report["server"], "stageflow")
        self.assertIn("validate_pipeline", report["tools"])
        self.assertIn("run_pipeline", report["tools"])
        self.assertIn("stop_run", report["tools"])
        self.assertIn("validate_pipeline", report["instructions"])
        self.assertTrue(report["verdict"]["ok"], report["verdict"])
        self.assertIn("stageflow://guide", report["resources"])


async def _talk(backend_url: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "stageflow_mcp.cli", "--backend", backend_url],
        cwd=str(ROOT),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            start = await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            answer = await session.call_tool(
                "validate_pipeline",
                {"pipeline": {"nodes": [{"id": "start", "type": "entry"}]}},
            )
            text = "".join(getattr(part, "text", "") for part in answer.content)
            return {
                "server": start.server_info.name,
                "instructions": start.instructions or "",
                "tools": [tool.name for tool in tools.tools],
                "resources": [str(item.uri) for item in resources.resources],
                "verdict": json.loads(text),
            }


async def _talk_unconfigured(backend_url: str, config_home: str) -> dict:
    """A client that CAN be asked, against a server that was told nothing."""
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    asked: list[str] = []

    async def answer(context, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept",
                                  content={"backend": backend_url, "token": ""})

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "stageflow_mcp.cli"],
        cwd=str(ROOT),
        # a config of its own, so this never reads or writes the real one
        env={"XDG_CONFIG_HOME": config_home, "PATH": os.environ.get("PATH", "")},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, elicitation_callback=answer) as session:
            await session.initialize()
            answer_text = await session.call_tool(
                "validate_pipeline",
                {"pipeline": {"nodes": [{"id": "start", "type": "entry"}]}},
            )
            text = "".join(getattr(part, "text", "") for part in answer_text.content)
            return {"asked": len(asked), "verdict": json.loads(text)}


if __name__ == "__main__":
    unittest.main()

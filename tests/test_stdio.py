"""The whole thing, through the transport a client will actually use.


Everything else here drives the server in-process, which leaves one thing
untested and it is the one a user meets first: that `stageflow-mcp` starts,
speaks MCP on stdio, and answers with the tools it is supposed to have. The
backend it talks to is the fake, so this needs no network.
"""
import asyncio
import json
import sys
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


if __name__ == "__main__":
    unittest.main()

"""The MCP surface: that the three tools and the resources are really there,
and that calling one reaches the backend.


Driven in-process rather than over stdio. What is worth testing here is the
wiring — a tool that is registered under the wrong name, or a resource that
raises when read, is a server that fails at the moment somebody uses it — and
a subprocess would test the transport instead, which is the SDK's to test.
"""
import asyncio
import json
import unittest

from stageflow_mcp.backend import Backend
from stageflow_mcp.server import build
from tests.fake import FakeBackend

GRAPH = {"nodes": [{"id": "start", "type": "entry"}]}


def text_of(result) -> str:
    """The text a tool or a resource answered with, whatever it was wrapped in."""
    if hasattr(result, "content"):
        return "".join(getattr(part, "text", "") for part in result.content)
    return "".join(getattr(part, "content", "") for part in result)


class ServerTests(unittest.TestCase):
    def test_the_three_tools_are_registered_and_described(self):
        with FakeBackend() as fake:
            server = build(Backend(fake.url))
            names = {tool.name for tool in asyncio.run(_tools(server))}
            self.assertEqual(names, {"validate_pipeline", "run_pipeline", "stop_run"})
            for tool in asyncio.run(_tools(server)):
                self.assertTrue(tool.description, tool.name)

    def test_validating_through_the_server_reaches_the_backend(self):
        with FakeBackend() as fake:
            server = build(Backend(fake.url))
            result = asyncio.run(server.call_tool("validate_pipeline", {"pipeline": GRAPH}))
            self.assertIn('"ok": true', text_of(result).lower().replace("'", '"'))
            self.assertIn("/api/run", fake.paths("POST"))

    def test_a_refusal_comes_back_as_the_list_of_what_is_wrong(self):
        with FakeBackend() as fake:
            fake.script.refuse_message = (
                "PipelineValidationError: Pipeline validation failed: a: bad; b: worse")
            server = build(Backend(fake.url))
            answer = text_of(asyncio.run(server.call_tool("validate_pipeline",
                                                          {"pipeline": GRAPH})))
            self.assertIn("a: bad", answer)
            self.assertIn("b: worse", answer)

    def test_the_resources_are_readable(self):
        with FakeBackend() as fake:
            server = build(Backend(fake.url), lang="ru")
            guide = text_of(asyncio.run(server.read_resource("stageflow://guide")))
            self.assertIn("StageFlow pipeline", guide)

            stages = json.loads(text_of(asyncio.run(server.read_resource("stageflow://stages"))))
            self.assertEqual(stages["SetValueStage"]["description"], "Кладёт значение")

            schema = json.loads(text_of(asyncio.run(server.read_resource("stageflow://schema"))))
            self.assertIn("enum", schema["$defs"]["stage_node"]["properties"]["stage"])

    def test_an_example_is_served_by_name(self):
        with FakeBackend() as fake:
            server = build(Backend(fake.url))
            graph = json.loads(
                text_of(asyncio.run(server.read_resource("stageflow://examples/straight-line")))
            )
            self.assertEqual(graph["nodes"][0]["id"], "start")

    def test_the_server_says_how_to_work(self):
        """The instructions are the only place a client is told to check before
        it runs. An empty one would make that a thing only the author knows."""
        with FakeBackend() as fake:
            server = build(Backend(fake.url))
            self.assertIn("validate_pipeline", server.instructions or "")


async def _tools(server):
    return await server.list_tools()


if __name__ == "__main__":
    unittest.main()

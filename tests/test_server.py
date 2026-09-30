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
from stageflow_mcp.bridge import Bridge
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

    def test_the_editor_tools_do_not_exist_without_a_bridge(self):
        """The only honest way to make a capability optional: an agent that is
        told about a tool will call it, and a bridge that was never asked for
        is not a socket this process gets to open."""
        with FakeBackend() as fake:
            server = build(Backend(fake.url))
            names = {tool.name for tool in asyncio.run(_tools(server))}
            self.assertNotIn("show_in_editor", names)
            self.assertNotIn("get_editor_graph", names)
            self.assertNotIn("editor", (server.instructions or "").lower())

    def test_with_a_bridge_they_are_there_and_they_reach_it(self):
        bridge = Bridge()
        with FakeBackend() as fake:
            server = build(Backend(fake.url), bridge=bridge)
            names = {tool.name for tool in asyncio.run(_tools(server))}
            self.assertIn("show_in_editor", names)
            self.assertIn("get_editor_graph", names)

            graph = {"nodes": [{"id": "start", "type": "entry"}]}
            asyncio.run(server.call_tool("show_in_editor",
                                         {"pipeline": graph, "note": "have a look"}))
            sent = bridge._messages[-1]
            self.assertEqual(sent["type"], "graph")
            self.assertEqual(sent["pipeline"], graph)
            self.assertEqual(sent["note"], "have a look")

    def test_reading_the_canvas_before_an_editor_connected(self):
        bridge = Bridge()
        with FakeBackend() as fake:
            server = build(Backend(fake.url), bridge=bridge)
            answer = text_of(asyncio.run(server.call_tool("get_editor_graph", {})))
            self.assertIn('"connected": false', answer.lower())

            bridge.graph_from_editor({"nodes": [{"id": "drawn_by_hand", "type": "entry"}]})
            answer = text_of(asyncio.run(server.call_tool("get_editor_graph", {})))
            self.assertIn("drawn_by_hand", answer)

    def test_the_bridge_link_is_where_the_agent_can_read_it(self):
        """Under an MCP client the server's stderr is a log file nobody is
        looking at, so the link it printed at startup has to reach the person
        some other way — which means the model has to know it."""
        bridge = Bridge()
        bridge.url = "http://127.0.0.1:7433"
        bridge.link = bridge.editor_link("https://host/editor/", "https://sf.example")
        with FakeBackend() as fake:
            server = build(Backend(fake.url), bridge=bridge)
            self.assertIn(bridge.link, server.instructions or "")

            answer = text_of(asyncio.run(server.call_tool(
                "show_in_editor", {"pipeline": {"nodes": []}})))
            self.assertIn("127.0.0.1:7433", answer)

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

"""The MCP server: three tools and five resources over one backend.

The split is deliberate and worth keeping. Everything that decides anything
lives in `tools.py` and `catalog.py`, which know nothing about MCP; this file
only names those functions to a protocol. The MCP specification moves faster
than a pipeline framework does, and when it moves again there is one file to
rewrite rather than a server's worth of logic to pick apart from it.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .backend import Backend
from .bridge import Bridge
from .catalog import EXAMPLES, GUIDE, capabilities, example, pipeline_schema, stage_catalog
from .tools import DEFAULT_RUN_TIMEOUT, run_pipeline, stop_run, validate_pipeline

BRIDGE_INSTRUCTIONS = """\

An editor may be open on this graph. If none is, the person opens one here,
and it is worth telling them the link rather than waiting to be asked:

  {link}

 `get_editor_graph` is the canvas the
person is looking at — start from it when they say "here", "this node" or "the
one on screen" rather than asking them to paste anything. `show_in_editor`
puts a graph on that canvas, where they can see it, keep editing it, or undo
it. Show your work as you go: a graph that is only in this conversation is a
graph nobody can look at.
"""

INSTRUCTIONS = """\
This server is a client of one StageFlow backend: the stages it offers, what
the caller's plan allows, and the running of graphs are all that backend's, and
nothing is validated or executed here.

Write a pipeline as JSON, then call `validate_pipeline`. It costs no run and
answers with every violation at once, the plan's refusals included. Fix the
list, validate again, and only then `run_pipeline`.

Before writing anything, read `stageflow://guide` for the shape of a graph and
`stageflow://stages` for what this backend can actually do — stage names,
arguments and outputs are the backend's, never guessable.
"""


def build(backend: Backend, lang: str | None = None, bridge: Bridge | None = None) -> MCPServer:
    """An MCP server bound to one backend, and optionally to an open editor.

    The bridge is a parameter rather than a setting because it is a listening
    socket: a server built without one has no way to reach a page, and the two
    tools that would need it are not offered at all. An agent cannot be told
    about a capability that does not exist, which is the only honest way to
    make one optional.
    """
    server = MCPServer(
        name="stageflow",
        title="StageFlow",
        version=__version__,
        instructions=INSTRUCTIONS + (
            BRIDGE_INSTRUCTIONS.format(link=bridge.link or "(the link this server printed)")
            if bridge else ""
        ),
    )

    # ------------------------------------------------------------- tools

    @server.tool(
        name="validate_pipeline",
        title="Validate a pipeline",
        description=(
            "Check a pipeline against the backend without running it. Answers "
            "{ok, errors[]} — every violation at once: the JSON schema, the "
            "graph's own checks, the declared types, and what the caller's plan "
            "refuses. Costs no run. Use it after every edit."
        ),
    )
    def validate_pipeline_tool(
        pipeline: dict[str, Any], variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return validate_pipeline(backend, pipeline, variables)

    @server.tool(
        name="run_pipeline",
        title="Run a pipeline",
        description=(
            "Run a pipeline to the end on the backend and report what happened: "
            "status, result, artifacts, the error if it failed, the meters "
            "against the plan's ceilings, and the path of nodes the run actually "
            "took. Validate first — a run is a slot on a shared backend."
        ),
    )
    def run_pipeline_tool(
        pipeline: dict[str, Any],
        variables: dict[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_RUN_TIMEOUT,
    ) -> dict[str, Any]:
        return run_pipeline(backend, pipeline, variables, timeout=timeout_seconds)

    @server.tool(
        name="stop_run",
        title="Stop a run",
        description="Stop a run that is still going, by the id a run answered with.",
    )
    def stop_run_tool(run_id: str) -> dict[str, Any]:
        return stop_run(backend, run_id)

    if bridge is not None:

        @server.tool(
            name="get_editor_graph",
            title="Read the graph in the editor",
            description=(
                "The pipeline the open editor is showing right now, as the person "
                "sees it. Use it when they refer to what is on screen instead of "
                "asking them to paste JSON. Answers {connected: false} if no editor "
                "has connected to the bridge yet."
            ),
        )
        def get_editor_graph() -> dict[str, Any]:
            graph, seen = bridge.editor_graph
            if graph is None:
                return {
                    "connected": False,
                    "hint": "open the editor with the bridge link this server printed at startup",
                }
            return {"connected": True, "pipeline": graph, "seen_at": seen}

        @server.tool(
            name="show_in_editor",
            title="Put a graph on the editor's canvas",
            description=(
                "Send a pipeline to the open editor, where the person can look at "
                "it, keep editing it, or undo it. `note` is one line saying what "
                "changed, shown beside the connection in the status bar. Validate "
                "before showing: an invalid graph draws, but it will not run."
            ),
        )
        def show_in_editor(pipeline: dict[str, Any], note: str = "") -> dict[str, Any]:
            index = bridge.push("graph", pipeline=pipeline, note=note)
            return {
                "shown": True,
                "index": index,
                "editor_connected": bridge.connected,
                "note": (
                    None if bridge.connected else
                    "nothing has connected to the bridge yet: this is waiting for "
                    "an editor and will be delivered when one arrives. Tell the "
                    f"person to open {bridge.link}" if bridge.link else
                    "nothing has connected to the bridge yet"
                ),
            }

    # --------------------------------------------------------- resources

    @server.resource(
        "stageflow://guide",
        name="How to write a StageFlow pipeline",
        description="The frame, the node types, arguments and outputs, expressions.",
        mime_type="text/markdown",
    )
    def guide() -> str:
        return GUIDE

    @server.resource(
        "stageflow://stages",
        name="The stages this backend offers",
        description=(
            "Every stage the caller may use, with its arguments and outputs, "
            "in one language. The backend's own registry — not guessable."
        ),
        mime_type="application/json",
    )
    def stages() -> str:
        return json.dumps(stage_catalog(backend, lang), ensure_ascii=False, indent=2)

    @server.resource(
        "stageflow://capabilities",
        name="What this backend can run",
        description=(
            "The node types this caller may use, the plan, and the ceilings a "
            "run is held to. From /api/meta, which is optional in the contract."
        ),
        mime_type="application/json",
    )
    def what_it_can_run() -> str:
        return json.dumps(capabilities(backend), ensure_ascii=False, indent=2)

    @server.resource(
        "stageflow://schema",
        name="The pipeline JSON Schema",
        description=(
            "The core's schema, narrowed to this backend: the stage names it "
            "serves and the node types it can run are enums in it."
        ),
        mime_type="application/schema+json",
    )
    def schema() -> str:
        return json.dumps(pipeline_schema(backend), ensure_ascii=False, indent=2)

    @server.resource(
        "stageflow://examples/{name}",
        name="An example pipeline",
        description=f"One of: {', '.join(sorted(EXAMPLES))}. Built from the core's own stages.",
        mime_type="application/json",
    )
    def one_example(name: str) -> str:
        if name not in EXAMPLES:
            raise ValueError(f"no such example: {name!r}; have {', '.join(sorted(EXAMPLES))}")
        return example(name)

    return server

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
from .catalog import EXAMPLES, GUIDE, capabilities, example, pipeline_schema, stage_catalog
from .tools import DEFAULT_RUN_TIMEOUT, run_pipeline, stop_run, validate_pipeline

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


def build(backend: Backend, lang: str | None = None) -> MCPServer:
    """An MCP server bound to one backend."""
    server = MCPServer(
        name="stageflow",
        title="StageFlow",
        version=__version__,
        instructions=INSTRUCTIONS,
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

"""What a model has to know before it can write a graph.

Three of these four come from the backend at run time — the stages, what the
caller's plan allows, the schema narrowed to both — so there is nothing here to
keep in step with anybody's release. The fourth, the guide, is written prose
and therefore the one thing that can fall behind; the test suite checks its
list of node types against a live `/api/meta` so that at least the names cannot
quietly become fiction.

The stage prose is collapsed to ONE language here, which is the opposite of
what the editor does with the same answer and right for the opposite reason. A
backend sends every language it has because the editor's reader may change
their mind long after the specs were fetched. A model has no reader and no
second thoughts: sending it `{"en": …, "ru": …}` for every argument of every
stage spends context on the half it will not use.
"""
from __future__ import annotations

import json
from typing import Any

from .backend import Backend, BackendError

FALLBACK_LANGUAGE = "en"


def _tags(wanted: str) -> list[str]:
    """`ru-RU` asks for `ru-ru`, then `ru`. The same widening the editor does:
    a regional tag is satisfied by the language it belongs to."""
    tag = (wanted or "").strip().lower()
    if not tag:
        return []
    out = [tag]
    while "-" in tag:
        tag = tag.rsplit("-", 1)[0]
        out.append(tag)
    return out


def prose(value: Any, lang: str | None) -> Any:
    """One piece of the backend's prose, in one language.

    A bare string is prose that exists in one language and comes back as it
    came. A mapping is asked for the wanted tag, then for English, and failing
    both it gives up its only entry rather than nothing — a stage described in
    Polish and nothing else is better read in Polish than not at all.
    """
    if not isinstance(value, dict):
        return value
    have = {str(k).lower(): k for k in value}
    for tag in [*_tags(lang or ""), FALLBACK_LANGUAGE]:
        if tag in have:
            return value[have[tag]]
    return next(iter(value.values()), "")


#: What a stage spec carries for the editor's palette and nothing else. A
#: reader that cannot draw a card spends context on these and gets nothing: an
#: icon, a colour, a timeout it does not control. Dropped from what goes to a
#: model; the editor still gets the whole spec from the backend.
_FOR_DRAWING = ("icon", "icon_mono", "color", "skipable", "reserve", "timeout")

#: Kept only when they say something. Almost every stage has neither, and an
#: empty list on every one of twenty-three stages is pure noise — but a stage
#: that WAITS FOR INPUT is a stage that will hang a run, so when there is
#: something here it has to be visible.
_WHEN_PRESENT = ("allowed_events", "allowed_inputs")


def _localize_spec(spec: dict, lang: str | None) -> dict:
    """A stage spec as somebody writing a graph needs it: every description in
    one language, and nothing that only matters to whoever draws the card."""
    out = {k: v for k, v in spec.items()
           if k not in _FOR_DRAWING and (k not in _WHEN_PRESENT or v)}
    if "description" in out:
        out["description"] = prose(out["description"], lang)
    for section in ("arguments", "outputs", "allowed_events", "allowed_inputs"):
        rows = out.get(section)
        if isinstance(rows, list):
            out[section] = [
                {**row, "description": prose(row.get("description", ""), lang)}
                if isinstance(row, dict) else row
                for row in rows
            ]
    return out


def stage_catalog(backend: Backend, lang: str | None = None) -> dict[str, Any]:
    """Every stage this caller may use, in one language.

    Narrowed by the backend, not here: a spec it did not send is a stage a run
    would refuse, and the two must not disagree.
    """
    specs = backend.stages()
    return {name: _localize_spec(spec, lang) for name, spec in sorted(specs.items())}


def capabilities(backend: Backend) -> dict[str, Any]:
    """`/api/meta`, or an honest account of its absence.

    Optional in the contract, and a backend that does not serve it is not
    second-guessed here any more than it is by the editor: what comes back says
    so, and the caller knows it is working without the list rather than being
    handed a guess.
    """
    try:
        return backend.meta()
    except BackendError as refused:
        if refused.status == 0:
            # nothing is listening there. Calling that "the backend does not
            # serve /meta" sends somebody reading a contract when what they
            # have is a process that is not running
            return {
                "available": False,
                "reason": refused.message,
                "note": (
                    f"nothing answered at {backend.url} — this is not a backend "
                    "without /api/meta, it is an address with nothing behind it"
                ),
            }
        return {
            "available": False,
            "reason": refused.message,
            "note": (
                "this backend does not serve /api/meta; the node types it can run "
                "and the ceilings it holds a run to are unknown from here"
            ),
        }


def pipeline_schema(backend: Backend) -> dict[str, Any]:
    """The pipeline JSON Schema, narrowed to what THIS backend will accept.

    The schema itself is the core's file, read from the installed
    `stageflow-framework` — the one dependency in this package, and it is a
    dependency on a data file rather than on behaviour: nothing here validates
    anything locally, because the verdict that matters is the backend's and a
    second implementation would be a second opinion to drift.

    Two narrowings are applied on top, and both come from the live backend:

    - `stage` gets an enum of the stages this caller may use, so a stage that
      does not exist is rejected by the shape, before any semantics;
    - `type` gets an enum of the node types this backend can run, which the
      shipped schema deliberately leaves open (it accepts any string and lets
      the core say `Unknown node type`). Against a known backend that is a
      refusal we can hand over earlier.
    """
    try:
        from stageflow.docs import load_pipeline_schema
    except ImportError:  # pragma: no cover - a broken install, not a normal path
        return {
            "available": False,
            "reason": "stageflow-framework is not installed, and the schema is its file",
        }

    schema = load_pipeline_schema()
    defs = schema.get("$defs", {})

    try:
        names = sorted(backend.stages())
        if names:
            defs["stage_node"]["properties"]["stage"]["enum"] = names
    except BackendError:
        pass  # the schema without the enum is still the schema

    meta = capabilities(backend)
    types = meta.get("node_types")
    if isinstance(types, list) and types:
        defs["node"]["properties"]["type"]["enum"] = sorted(types)

    schema["title"] = f"StageFlow Pipeline — as {backend.url} will accept it"
    return schema


GUIDE = """\
# Writing a StageFlow pipeline

A pipeline is JSON: a list of nodes, each with an `id` and a `type`, and one of
them is the entry. Nodes are wired by naming each other — there is no separate
list of edges — and they pass data through a single shared frame of variables.

## The frame

Every node reads and writes the same flat namespace. There are no per-node
scopes and no wires carrying values: a stage reads `text` because some earlier
node wrote `text`. Two nodes that both write `total` are two writers of one
variable, in execution order.

## A stage node

    {"id": "stats", "type": "stage", "stage": "TextStatsStage",
     "arguments": {"vars": {"text": "text"}, "const": {"limit": 10}},
     "outputs": {"words": "words", "longest": "longest"},
     "next": "is_long"}

- `arguments.vars` maps an argument name to the VARIABLE it is read from
  (`{"text": "text"}` means the argument `text` takes the value of the frame
  variable `text`). A bare list is sugar for keeping the same name.
- `arguments.const` holds literals, written out as they are.
- `outputs` maps a field of the stage's result onto a variable name. A stage
  whose outputs are not mapped has produced nothing the rest of the graph can
  see.
- Which arguments and outputs a stage has is in its spec — read the `stages`
  resource, do not guess.

## Expressions

A key ending in `.$` means the value is CEL rather than a literal:

    {"total.$": "vars.a + vars.b"}

Variables are reached as `vars.name`. A name that is not a plain identifier —
anything non-ASCII, anything with a dash — has to be written `vars['имя']`; the
dotted form is for identifiers only.

## The entry

    {"id": "start", "type": "entry", "variables": {"n": 3}, "next": "first"}

Entry variables are DEFAULTS. A name handed to the run from outside is not
overwritten by them, which is what makes "run it again with another n" a run
argument rather than an edit.

## Choosing a road

    {"id": "is_long", "type": "condition", "condition": "vars.words > 10",
     "then": "slug", "else": "short"}
    {"id": "pick", "type": "switch", "switch": "vars.kind",
     "cases": [{"when": "'a'", "then": "road_a"}], "default": "road_b"}

## Ending

A run ends at a `terminal` node or at a node with nowhere left to go. Every
road should reach one: a graph whose only paths lead back into themselves never
finishes.

## Blocks: a body, and where it comes back

Four node types own a piece of the graph rather than pointing at the next one.
They are written the same way: a field names the ENTRY of the body, and the
body's last node leads nowhere — control comes back to the block, and the
block's `next` is where everything goes afterwards. Writing a body that ends by
jumping to the node after the block is the usual mistake; it leaves the block
without an exit.

### try — a body with handlers

    {"id": "guard", "type": "try", "body": "risky",
     "except": [{"error_equals": ["*"], "next": "recover", "result_var": "err"}],
     "next": "after"}

`except` entries take **`next`**, not `then` — the one field in this format most
likely to be guessed wrong. `error_equals` is a list of exception class names,
`"*"` for any. `result_var` is where the error object lands for the handler to
read. A handler's own road may rejoin the graph wherever it likes.

### map — a body per item

    {"id": "each", "type": "map", "items": "vars.rolls", "item_var": "roll",
     "index_var": "i", "collect": {"text": "labels"},
     "body": "label", "next": "after"}

`items` is CEL, not a variable name. `item_var` is what the element is called
inside the body, `index_var` the zero-based position. `collect` maps a name the
body WRITES onto a list outside — `{"text": "labels"}` means "the body's `text`,
one entry per item, as `labels`". A list is shorthand for keeping the name.
Without `collect` a loop computes and keeps nothing.

### parallel — branches at once

    {"id": "both", "type": "parallel",
     "branches": [{"id": "left", "entry": "fetch"}, {"id": "right", "entry": "count"}],
     "next": "after"}

Each branch names the entry of its own body; the frames are merged when they
all finish. A bare string is shorthand for `{"entry": that}`.

### subpipeline — a graph as a node

    {"id": "inner", "type": "subpipeline", "subpipeline_id": "scoring", "next": "after"}

The child graph lives in the pipeline's `subpipelines` object under that id.

## Retries, renames, and forgetting

Any node takes these; none of them needs a stage.

    "retry": [{"error_equals": ["*"], "max_attempts": 3,
               "interval_seconds": 0.5, "backoff_rate": 2}]
    "expose": {"total": "sum"}        rename or copy inside the frame
    "consume": ["scratch", "tmp"]     names removed from the frame after this node

What `max_attempts` and `interval_seconds` may be is the caller's plan, not a
preference: the `capabilities` resource has the ceilings, and a graph that asks
for more is refused by `validate_pipeline` rather than at run time.

## Do not place the nodes

`metadata` is yours to put things in, but **`metadata.ui` is the editor's**: it
is where a person dragged that card. Leave it out. A graph without coordinates
is laid out when it is opened — by execution order, with the branches and the
bodies of blocks in their own columns — which is better than anything invented
from here, where nothing knows how wide a card is. Coordinates that would stack
the cards are discarded on arrival anyway.

## Working method

Write the graph, call `validate_pipeline`, fix everything it lists, repeat. It
costs no run and it answers with every violation at once — including what the
caller's plan refuses, which is not visible in the JSON. Only then
`run_pipeline`.
"""


EXAMPLES: dict[str, dict] = {
    "straight-line": {
        "metadata": {"title": "A straight line with a decision"},
        "nodes": [
            {"id": "start", "type": "entry", "variables": {"n": 3}, "next": "bump"},
            {"id": "bump", "type": "stage", "stage": "IncrementStage",
             "arguments": {"vars": {"current": "n"}, "const": {"delta": 4}},
             "outputs": {"value": "n"}, "next": "big_enough"},
            {"id": "big_enough", "type": "condition", "condition": "vars.n > 5",
             "then": "shout", "else": "done"},
            {"id": "shout", "type": "stage", "stage": "LogStage",
             "arguments": {"const": {"message": "big"}, "vars": {"n": "n"}},
             "next": "done"},
            {"id": "done", "type": "terminal"},
        ],
    },
    "expressions-and-a-list": {
        "metadata": {"title": "CEL in arguments and in outputs"},
        "nodes": [
            {"id": "start", "type": "entry",
             "variables": {"items": [1, 2, 3], "label": "sum"}, "next": "total"},
            {"id": "total", "type": "stage", "stage": "SetValueStage",
             "arguments": {"vars": {"value.$": "vars.items.map(x, x * 2)"}},
             "outputs": {"value": "doubled"}, "next": "say"},
            {"id": "say", "type": "stage", "stage": "ConcatStage",
             "arguments": {"vars": {"parts": "doubled"}, "const": {"separator": ", "}},
             "outputs": {"value": "line"}, "next": "done"},
            {"id": "done", "type": "terminal"},
        ],
    },
    "guarded-by-try": {
        "metadata": {"title": "A body that may fail, and what catches it"},
        "nodes": [
            {"id": "start", "type": "entry", "next": "guard"},
            {"id": "guard", "type": "try", "body": "boom",
             "except": [{"error_equals": ["StageFlowError"], "next": "recover"}],
             "next": "done"},
            {"id": "boom", "type": "stage", "stage": "FailStage",
             "arguments": {"const": {"message": "on purpose"}}},
            {"id": "recover", "type": "stage", "stage": "SetValueStage",
             "arguments": {"const": {"value": "recovered"}},
             "outputs": {"value": "outcome"}},
            {"id": "done", "type": "terminal"},
        ],
    },
}


def example(name: str) -> str:
    return json.dumps(EXAMPLES[name], ensure_ascii=False, indent=2)

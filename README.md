<div align="center">

# StageFlow MCP

**Let an agent write StageFlow pipelines: check them against a real backend, run
them, read what happened — without inventing anything the backend does not
already serve.**

[Core](https://github.com/leo-need-more-coffee/stageflow) ·
[Editor](https://github.com/leo-need-more-coffee/stageflow-ui) ·
[Example backend](https://github.com/leo-need-more-coffee/stageflow-example)

</div>

A StageFlow pipeline is JSON, and a model can write JSON. What it cannot do is
know whether the graph it wrote is one this backend will accept: the stages are
the backend's, the node types are its core's, and the ceilings are its caller's
plan. This is the missing half — a client of the same seven endpoints the
editor talks to, handed to an agent as three tools and five resources.

## Add it

```bash
claude mcp add stageflow -- uvx stageflow-mcp --backend https://stageflow.lazy.su
```

That address is the one you would type on the editor's connection screen, and
it is read the same way: `localhost:8765`, `http://localhost:8765/` and
`.../api` all mean one backend. A bare hostname gets `https://` unless it is
loopback — see [below](#two-things-worth-knowing).

**A credential, if that backend wants one.** It is the same credential the
editor carries, in the same header, and it is not this tool's to issue: the
core has no idea what a token is, so whoever runs the backend decides what one
looks like and hands it over. In the reference backends it is one environment
variable (`SF_TOKENS="secret:plan"`) and forty lines of `auth.py` that a real
deployment replaces wholesale. Without one you are whatever that backend calls
an anonymous caller — on the public demo above, a narrow plan that works.

```bash
uvx stageflow-mcp --backend https://sf.example.org --token "$SF_TOKEN"
uvx stageflow-mcp --backend https://sf.example.org --token "$SF_TOKEN" \
                  --auth-header X-Api-Key          # if it is not Authorization
```

Check the address before wiring an agent to it — the same questions, printed:

```console
$ stageflow-mcp --backend stageflow.lazy.su --check
backend      https://stageflow.lazy.su
credential   none sent
stages       23
core         0.13.0
plan         demo (open)
node types   condition, entry, map, parallel, stage, subpipeline, switch, terminal, try
limits       {"counters": {"seconds": 30, "steps": 300, …}}
```

## What it gives an agent

| Tool | |
|---|---|
| `validate_pipeline` | every violation at once — the schema, the graph's own checks, the declared types, and what the plan refuses. **Costs no run.** |
| `run_pipeline` | runs it and reports status, result, artifacts, the meters against the ceilings, and the path of nodes the run actually took |
| `stop_run` | stops one that is still going |
| `show_in_editor` | with `--bridge`: puts the graph on an open editor's canvas |
| `get_editor_graph` | with `--bridge`: reads the graph that editor is showing |

| Resource | |
|---|---|
| `stageflow://guide` | the frame, the node types, arguments and outputs, expressions |
| `stageflow://stages` | the stages this caller may use, in one language |
| `stageflow://capabilities` | node types, plan and ceilings, from `/api/meta` |
| `stageflow://schema` | the pipeline JSON Schema, with the stage names and node types of **this** backend as enums |
| `stageflow://examples/{name}` | small graphs built from the core's own stages |

## The bridge to an open editor

```bash
stageflow-mcp --backend https://sf.example --bridge
```

prints a link. Open it, and the
[editor](https://github.com/leo-need-more-coffee/stageflow-ui) is looking at
the same graph the agent is: `show_in_editor` puts a pipeline on the canvas,
`get_editor_graph` reads back what the person changed there. "Add a retry to
this node" stops being a request to paste anything.

The link is printed to stderr *and* handed to the agent, because under an MCP
client stderr is a log file nobody is looking at — so the assistant can simply
tell you where to open it. `--editor http://127.0.0.1:8080/` points the link at
a copy of the editor you serve yourself.

Off unless asked for — the two tools do not exist without `--bridge`, because a
tool an agent has been told about is a tool it will call, and a socket on
somebody's machine is not this process's to open uninvited.

It binds to loopback, requires a token made fresh at every start, and answers
only to the editor and to pages served from this machine. The token travels in
the **fragment** of the link, so it never reaches the host serving the editor —
not its access log, not the `Referer` of anything the page fetches.

## Your backend needs no changes

There is no `/api/validate` in the StageFlow contract and this asks for none.
`POST /api/run` with `mode: "step"` parses a graph, validates it against the
caller's policy, and only then admits a run — both reference backends do it in
that order, before a thread, a slot or an id exists. So an invalid graph is
refused for free, and a valid one comes back parked before its first node,
having executed nothing, and is stopped immediately.

Which means this works against a backend that was deployed before it existed,
and against yours without asking you for anything.

Nothing is validated or executed here. The semantics live in the core the
backend runs, and a second opinion in this process would be a second
implementation to drift from it. The one thing read locally is the pipeline
JSON Schema — a data file out of `stageflow-framework`, narrowed by what the
live backend says it serves.

## Two things worth knowing

**A credential travels.** An address typed without a scheme becomes `https://`
unless it is loopback, and a token bound for plain http to something that is
not this machine is warned about. The editor fills in `http://` instead, and
can afford to: a browser will not let it reach one anyway.

**A shared backend has slots.** A public demo holds a few runs at a time and a
couple per caller. Checking costs none of them, which is why the working method
is validate, fix, validate, and only then run.

## What it does not do

- **Inputs.** A stage can await input in the core, but the HTTP contract has no
  channel for it — the editor has none either — so a graph that waits will time
  out.
- **Step debugging.** Stepping exists so a person can watch a graph go by;
  an agent that is not watching would only be parking a slot.
- **Serving over HTTP.** stdio only. This process carries the credential of
  whoever started it; making it a service that hands that to whoever connects
  is a different thing, and one to build deliberately.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

They run against a fake backend on a real socket, so they need no network. The
ones that check the contract itself is still what this assumes — that a bad
graph is refused before a run is admitted — talk to a live backend and are
skipped without one:

```bash
STAGEFLOW_BACKEND=https://stageflow.lazy.su python -m unittest tests.test_live
```

MIT.

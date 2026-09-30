"""What an agent can do to a pipeline: check it, run it, stop it.

Three verbs, and the first one is the point. Writing a graph is a loop —
propose, be told what is wrong, fix — and a loop needs an answer that lists
everything wrong at once. The core was built for exactly that
(`PipelineValidationError` carries the whole list, "so that the author fixes
them in one pass"), the backend puts that list in its refusal, and this pulls
it back apart into something a model can act on item by item.

**Checking costs no run.** There is no `/api/validate` in the contract and this
package does not ask for one: `POST /api/run` with `mode: "step"` parses the
graph, validates it against the caller's policy, and only then admits a run —
both reference backends do it in that order, before a thread, a slot or an id
exists. So an invalid graph is refused for free, and a valid one comes back
parked before its first node, having executed nothing, and is stopped
immediately.

That last step is not optional and not tidiness: a parked run holds one of the
caller's slots until the backend's own TTL reaps it, and a caller usually has
two. Every path out of `validate_pipeline` stops what it started.
"""
from __future__ import annotations

import time
from typing import Any

from .backend import Backend, BackendError

#: How long `run_pipeline` waits for a run to end before it stops it and says
#: so. Generous next to what a demo plan allows itself (30 seconds of runtime),
#: and still an answer rather than a hang.
DEFAULT_RUN_TIMEOUT = 90.0

#: How often the run state is asked for while waiting. The events say what
#: happened, the state says where it got to; for "is it over yet" the state is
#: the cheaper question.
POLL_SECONDS = 0.3

#: What a class name in front of a refusal means for the caller.
_VALIDATION_ERRORS = ("PipelineValidationError",)
_DEFINITION_ERRORS = ("PipelineDefinitionError", "TypeDeclarationError")


def split_errors(message: str) -> list[str]:
    """The backend's one-line refusal, back into the list it was made from.

    `PipelineValidationError` joins every violation with `"; "` behind a
    localized preamble that ends in a colon, and the HTTP layer prefixes the
    class name: `PipelineValidationError: <preamble>: a; b; c`. The class name
    is stable across languages and the separator is not translated, so both
    survive a Russian catalog.

    Anything else is one problem and comes back as one item — a schema
    complaint quotes the value it rejected and may well contain a semicolon of
    its own, and cutting it into pieces would invent violations that do not
    exist.
    """
    text = (message or "").strip()
    kind, _, rest = text.partition(": ")
    if kind not in _VALIDATION_ERRORS:
        if kind in _DEFINITION_ERRORS:
            return [rest.strip() or text]
        return [text]
    _preamble, _, listed = rest.partition(": ")
    listed = listed.strip() or rest.strip()
    return [part.strip() for part in listed.split("; ") if part.strip()]


def validate_pipeline(
    backend: Backend, pipeline: dict, variables: dict | None = None
) -> dict[str, Any]:
    """Everything this backend refuses about a graph, without running it.

    The verdict is the backend's, in full: the JSON schema, the cross-graph
    checks, the type declarations, and what the caller's plan will not allow —
    including the ceilings that can be judged from the shape alone, like a
    shortest path that does not fit the allowed number of steps.

    What it does NOT catch is anything that only happens with data: an
    expression that divides by a zero that arrives at run time is a valid graph
    until it runs.
    """
    run_id = None
    try:
        started = backend.start_run(pipeline, variables, mode="step", delay=0.0)
        run_id = (started or {}).get("id")
        return {"ok": True, "errors": [], "checked_by": backend.url}
    except BackendError as refused:
        if refused.status in (0, 429, 500, 502, 503, 504):
            # not a verdict about the graph: nothing answered, or the stand is
            # full. Saying "invalid" here would send an agent rewriting a graph
            # that was never looked at.
            raise
        return {
            "ok": False,
            "errors": split_errors(refused.message),
            "raw": refused.message,
            "status": refused.status,
            "checked_by": backend.url,
        }
    finally:
        # a valid graph left a run parked before its first node, holding a slot
        if run_id:
            try:
                backend.control(run_id, "stop")
            except BackendError:
                pass  # the reaper will have it; losing the check over it would be worse


def run_pipeline(
    backend: Backend,
    pipeline: dict,
    variables: dict | None = None,
    *,
    timeout: float = DEFAULT_RUN_TIMEOUT,
) -> dict[str, Any]:
    """Run a graph to the end and report what happened.

    `mode: "run"` and no delay: stepping is a person's tool — it exists so a
    human can watch a graph go by — and a caller that is not watching would
    only be parking a slot.

    What comes back is the state the backend keeps (`status`, `result`,
    `artifacts`, `error`, the meters against the allowance) plus the path the
    run actually took, read from the event log afterwards. The path is the
    thing that explains a surprising result: a `condition` that went the other
    way is invisible in a result and obvious in a list of nodes.
    """
    started = backend.start_run(pipeline, variables, mode="run", delay=0.0)
    run_id = (started or {}).get("id")
    if not run_id:
        raise BackendError(0, "the backend started a run without giving it an id")

    deadline = time.monotonic() + timeout
    state = (started or {}).get("state") or {}
    timed_out = False
    while time.monotonic() < deadline:
        state = backend.run_state(run_id)
        if state.get("status") != "running":
            break
        time.sleep(POLL_SECONDS)
    else:
        timed_out = True

    if timed_out or state.get("status") == "running":
        timed_out = True
        try:
            state = backend.control(run_id, "stop")
        except BackendError:
            pass

    report = {
        "run_id": run_id,
        "status": "timed_out" if timed_out else state.get("status"),
        "result": state.get("result"),
        "artifacts": state.get("artifacts"),
        "error": state.get("error"),
        "meters": state.get("meters"),
        "limits": state.get("limits"),
    }
    report.update(_what_happened(backend, run_id))
    if timed_out:
        report["note"] = (
            f"the run was still going after {timeout:g}s and was stopped; "
            "what is below is how far it got"
        )
    return report


def stop_run(backend: Backend, run_id: str) -> dict[str, Any]:
    """Stop a run that is still going. Answers with its state."""
    return backend.control(run_id, "stop")


def _what_happened(backend: Backend, run_id: str, limit: int = 400) -> dict[str, Any]:
    """The path through the graph, and what went wrong on it.

    Read from the event log rather than assembled from the state, because the
    state is where a run GOT to and the log is how it got there. Bounded: a
    graph with a loop can produce thousands of events, and a model does not
    need the thousandth to see the shape.
    """
    path: list[str] = []
    failures: list[dict] = []
    seen = 0
    try:
        for event in backend.events(run_id, 0):
            seen += 1
            if seen > limit:
                break
            kind = event.get("type")
            node = event.get("node")
            if kind == "node_enter" and node and (not path or path[-1] != node):
                path.append(node)
            if kind and ("fail" in kind or "rejected" in kind or kind == "error"):
                failures.append({"type": kind, "node": node, "payload": event.get("payload")})
    except BackendError:
        return {"path": path, "failures": failures, "path_note": "the event log was not readable"}
    out: dict[str, Any] = {"path": path, "failures": failures}
    if seen > limit:
        out["path_note"] = f"only the first {limit} events were read"
    return out

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

import json
import re
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

#: How much of the frame comes back with a run. A graph that built a big list
#: would otherwise push everything else out of the answer; what matters is
#: seeing what it computed, and a value that long is read by running it again
#: with the editor open.
MAX_FRAME_CHARS = 4_000

#: Statuses that mean nothing was looked at: the question never arrived or the
#: backend could not take it. Never a verdict about the graph.
_NOT_A_VERDICT = (0, 429, 500, 502, 503, 504)

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
    if kind in _DEFINITION_ERRORS:
        _preamble, _, listed = rest.partition(": ")
        return _located_parts(listed.strip() or rest.strip())
    if kind not in _VALIDATION_ERRORS:
        return [text]
    _preamble, _, listed = rest.partition(": ")
    listed = listed.strip() or rest.strip()
    return [part.strip() for part in listed.split("; ") if part.strip()]


#: `node.field[0]: ` — what a located shape complaint starts with.
_PLACE = re.compile(r"^[A-Za-z_][\w\-.]*(\[\d+\])?([.\w\-]|\[\d+\])*: ")


def _located_parts(listed: str) -> list[str]:
    """A list of shape complaints, split only where a new one really begins.

    The core puts the place in front of each — `guard.except[0]: …` — and joins
    them with the same `"; "` that may well appear INSIDE one of them: a schema
    message quotes the value it rejected. So a piece that does not begin with a
    place is not a new complaint, it is the rest of the last one.
    """
    parts: list[str] = []
    for piece in listed.split("; "):
        if parts and not _PLACE.match(piece):
            parts[-1] = f"{parts[-1]}; {piece}"
        elif piece.strip():
            parts.append(piece.strip())
    return parts or [listed]


def _blocked(refused: BackendError, backend: Backend) -> dict[str, Any]:
    """Nothing was looked at, and saying which is the whole point.

    Returned rather than raised. An exception reaches a client as "the tool
    failed", with the reason somewhere in a log nobody is reading — and the
    model's next move is to rewrite a graph that was never read. An answer that
    says `blocked` cannot be mistaken for a verdict and carries what to do.
    """
    busy = refused.status == 429
    return {
        "blocked": "backend-busy" if busy else (
            "backend-unreachable" if refused.status == 0 else "backend-error"),
        "checked": False,
        "detail": refused.message,
        "status": refused.status,
        "backend": backend.url,
        "hint": (
            "the backend is at its limit of running runs; wait and ask again"
            if busy else
            f"nothing answered at {backend.url} — is the backend running, and is "
            "that the address you meant?" if refused.status == 0 else
            "the backend failed to answer; this says nothing about the graph"
        ),
    }


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
        if refused.status in _NOT_A_VERDICT:
            # not a verdict about the graph: nothing answered, or the stand is
            # full. Saying "invalid" here would send an agent rewriting a graph
            # that was never looked at.
            return _blocked(refused, backend)
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
    try:
        started = backend.start_run(pipeline, variables, mode="run", delay=0.0)
    except BackendError as refused:
        if refused.status in _NOT_A_VERDICT:
            return _blocked(refused, backend)
        # the graph was read and refused: the same list validate_pipeline gives
        return {
            "ran": False,
            "errors": split_errors(refused.message),
            "raw": refused.message,
            "status": refused.status,
            "hint": "the backend would not run this graph; validate_pipeline says the same",
        }
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
        # what it computed. The first thing anybody asks after a run, and it
        # was being thrown away: `result` is only what a terminal node returns,
        # and a graph that writes its answer into a variable — which is most of
        # them — looked like it had produced nothing at all
        "variables": _frame(state.get("vars")),
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


def _frame(variables: Any) -> Any:
    """The frame as it stood when the run ended, trimmed if it is enormous.

    Trimmed per value rather than as a whole: losing the tail of one long list
    still leaves every other name readable, where a cut across the object would
    take away names the author is looking for.
    """
    if not isinstance(variables, dict):
        return variables
    out: dict[str, Any] = {}
    for name, value in variables.items():
        shown = json.dumps(value, ensure_ascii=False, default=repr)
        if len(shown) > MAX_FRAME_CHARS:
            out[name] = f"{shown[:MAX_FRAME_CHARS]}… ({len(shown)} characters in all)"
        else:
            out[name] = value
    return out


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

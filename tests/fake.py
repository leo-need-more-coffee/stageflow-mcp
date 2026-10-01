"""A StageFlow backend that is not one: the seven endpoints, scripted.

Enough of the contract to answer this package's questions, and nothing behind
it — no core, no session, no thread. What the tests are about is how a client
behaves when a backend says a particular thing, so the backend saying it is the
part worth controlling, and the part worth recording: `calls` is the list of
requests that actually left, which is how "a check stops the run it started"
becomes something a test can see rather than believe.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STAGES = {
    "SetValueStage": {
        "stage_name": "SetValueStage",
        "category": "builtin.vars",
        "description": {"en": "Set a value", "ru": "Кладёт значение"},
        "arguments": [{"name": "value", "type": "any", "optional": False,
                       "description": {"en": "what to set", "ru": "что положить"}}],
        "outputs": [{"name": "value", "type": "any",
                     "description": {"en": "what was set", "ru": "что положили"}}],
    },
    "LonelyStage": {
        "stage_name": "LonelyStage",
        "category": "demo",
        "description": "Only one language, so a plain string",
        "arguments": [],
        "outputs": [],
    },
}

META = {
    "api": 1,
    "plan": "demo",
    "plan_source": "open",
    "stageflow": "0.13.0",
    "node_types": ["condition", "entry", "stage", "terminal"],
    "stages": len(STAGES),
    "limits": {"counters": {"steps": 300}, "gauges": {"depth": 3}},
}


class Script:
    """What this fake will do next, and what was asked of it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.headers: list[dict] = []
        #: a graph carrying this key is refused, with this message
        self.refuse_message: str | None = None
        self.refuse_status: int = 400
        #: how many state polls answer "running" before the run is over
        self.running_polls: int = 0
        self.final_status: str = "finished"
        self.serve_meta: bool = True
        #: whether LonelyStage declares an input it waits for
        self.waiting_stage: bool = False
        #: what the frame holds when a run ends
        self.frame: dict = {}
        self.events: list[dict] = []
        self.runs: dict[str, int] = {}
        self._next = 0


class _Handler(BaseHTTPRequestHandler):
    script: Script

    def log_message(self, *args) -> None:  # noqa: D102 - quiet in the test output
        pass

    # ------------------------------------------------------------ plumbing

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body: dict | None = None) -> str:
        path = urlparse(self.path).path
        self.script.calls.append((self.command, self.path, body))
        self.script.headers.append({k.lower(): v for k, v in self.headers.items()})
        return path

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    # ------------------------------------------------------------ the API

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
        path = self._record()
        script = self.script
        if path == "/api/stages":
            stages = {k: dict(v) for k, v in STAGES.items()}
            if script.waiting_stage:
                stages["LonelyStage"]["allowed_inputs"] = [
                    {"type": "answer", "description": "what the person says"}]
            return self._send(200, {"stages": stages})
        if path == "/api/meta":
            if not script.serve_meta:
                return self._send(404, {"error": "no meta here"})
            return self._send(200, META)
        if path == "/api/secrets":
            return self._send(200, {"names": [], "source": "none"})
        if path.endswith("/events"):
            return self._events()
        if path.startswith("/api/run/"):
            run_id = path.rsplit("/", 1)[-1]
            left = script.runs.get(run_id, 0)
            if left > 0:
                script.runs[run_id] = left - 1
                return self._send(200, {"id": run_id, "status": "running"})
            return self._send(200, {
                "id": run_id, "status": script.final_status,
                "result": {"done": True}, "artifacts": {},
                "vars": script.frame,
                "error": None, "meters": {"steps": 3}, "limits": {"steps": 300},
            })
        return self._send(404, {"error": f"no such thing: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._body()
        path = self._record(body)
        script = self.script
        if path == "/api/run":
            if script.refuse_message is not None:
                return self._send(script.refuse_status, {"error": script.refuse_message})
            script._next += 1
            run_id = f"run-{script._next}"
            script.runs[run_id] = script.running_polls
            return self._send(201, {"id": run_id, "state": {"id": run_id, "status": "running"}})
        if path.endswith("/control"):
            run_id = path.split("/")[3]
            script.runs[run_id] = 0
            return self._send(200, {"id": run_id, "status": "stopped"})
        if path.endswith("/vars"):
            return self._send(200, {"ok": True})
        return self._send(404, {"error": f"no such thing: {path}"})

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        for index, event in enumerate(self.script.events):
            frame = f"data: {json.dumps({**event, 'index': index}, ensure_ascii=False)}\n\n"
            self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()


class FakeBackend:
    """`with FakeBackend() as fake:` — a real socket on a real port.

    A real server rather than a patched transport: the client under test builds
    URLs, sets headers and reads a stream, and a stub that skipped the wire
    would stop testing exactly those.
    """

    def __init__(self) -> None:
        self.script = Script()

    def __enter__(self) -> "FakeBackend":
        handler = type("_Bound", (_Handler,), {"script": self.script})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def calls(self) -> list[tuple[str, str, dict | None]]:
        return self.script.calls

    def paths(self, method: str | None = None) -> list[str]:
        return [path for verb, path, _ in self.calls if method in (None, verb)]

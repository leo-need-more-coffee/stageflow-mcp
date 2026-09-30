"""The bridge to an open editor: what the agent draws, appearing on the canvas.

Without this the loop has a gap at the end. An agent writes a graph, checks it
and runs it, and then hands over a wall of JSON for somebody to paste into
"File → Import". The graph exists on both sides and neither side knows it.

So: a small HTTP server on the loopback interface, started only when asked for,
that the editor connects to. Two directions, and the second matters more —

  - **to the editor**: every graph the agent settles on is pushed, and the
    editor puts it on the canvas. Nodes without coordinates get laid out by the
    editor itself, and its undo history is intact, so a person can throw away
    what the model did with one keystroke.
  - **from the editor**: the page posts the graph it is showing whenever it
    changes, so `get_editor_graph` is the canvas the person is looking at.
    "Add a retry here" stops being a request to paste anything.

SSE and POST rather than a WebSocket. The editor already reads SSE by hand for
run events — it cannot use `EventSource`, which will not send a credential — so
this is a format it has code for, and it costs this package no dependency.

**This is a listening socket on somebody's machine, so:**

  - a token, generated at startup and required on every request. Without it any
    page open in the same browser could read and rewrite the graph, because the
    browser will happily let it try;
  - an `Origin` allowlist on top, because the token travels in a URL the user
    pastes, and the second lock costs nothing;
  - loopback only. Binding anywhere else is not a flag, because the thing it
    would enable is a stranger driving an editor on your machine.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

#: Where the hosted editor lives. Allowed by default because it is the copy
#: most people will have open; anything else is named with --bridge-origin.
HOSTED_EDITOR = "https://leo-need-more-coffee.github.io"

#: How often a quiet stream says something, so the page can tell a live
#: connection from a dead one.
HEARTBEAT_SECONDS = 15.0


class Bridge:
    """What the two sides know about each other.

    A log with indices rather than a queue, for the same reason the run event
    stream is one: the editor connects over a separate request, possibly after
    the agent has already pushed something, and a subscriber that missed the
    beginning would sit in front of an empty canvas waiting for an edit that
    already happened.
    """

    def __init__(self, token: str | None = None, origins: list[str] | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.origins = origins or [HOSTED_EDITOR]
        #: This process, told apart from the last one that held this port.
        #:
        #: The message log starts again at zero every time an agent is started,
        #: and a page that remembered "I have read 7 of them" would skip the
        #: first seven of the new one — including the graph it opened with. A
        #: reader compares this and starts over when it changed. The same
        #: problem the run stream does not have because a run has an id.
        self.session = secrets.token_hex(8)
        self._messages: list[dict] = []
        self._graph: dict | None = None
        self._graph_seen: float = 0.0
        self._lock = threading.Condition()
        #: how many editors are reading the stream right now. Tracked because
        #: "an editor has connected at some point" and "somebody is looking at
        #: this" are different answers, and only the second one makes "I have
        #: put it on your canvas" true
        self._listeners = 0
        self._server: ThreadingHTTPServer | None = None
        self.url = ""
        #: the address a person should open, once someone has worked out which
        #: editor and which backend. Kept here because the process that prints
        #: it writes to stderr, and under an MCP client stderr is a log file
        #: nobody is looking at — so the agent has to be able to say it out loud
        self.link = ""

    # --------------------------------------------------------------- state

    def push(self, kind: str, **payload: Any) -> int:
        """Send something to whatever editors are listening."""
        with self._lock:
            message = {"type": kind, "index": len(self._messages), **payload}
            self._messages.append(message)
            self._lock.notify_all()
            return message["index"]

    def graph_from_editor(self, pipeline: dict) -> None:
        with self._lock:
            self._graph = pipeline
            self._graph_seen = time.time()

    @property
    def editor_graph(self) -> tuple[dict | None, float]:
        with self._lock:
            return self._graph, self._graph_seen

    @property
    def listening(self) -> int:
        """Editors reading the stream at this moment."""
        with self._lock:
            return self._listeners

    @property
    def connected(self) -> bool:
        """Whether an editor is there to receive what is pushed.

        The count of live readers rather than "one said something once": a page
        that was closed an hour ago would otherwise have an agent announcing it
        had put a graph on a canvas nobody has open.
        """
        return self.listening > 0

    def _joined(self) -> None:
        with self._lock:
            self._listeners += 1

    def _left(self) -> None:
        with self._lock:
            self._listeners = max(0, self._listeners - 1)

    def follow(self, start: int = 0):
        """Messages from `start`, waiting for new ones; `None` as a heartbeat."""
        index = start
        while True:
            with self._lock:
                while index >= len(self._messages):
                    if not self._lock.wait(HEARTBEAT_SECONDS):
                        break
                if index >= len(self._messages):
                    yield None
                    continue
                batch = self._messages[index:]
                index = len(self._messages)
            for message in batch:
                yield message

    # -------------------------------------------------------------- server

    def start(self, port: int = 0) -> str:
        """Listen on loopback. Returns the address the editor should be given."""
        handler = type("_Bound", (_Handler,), {"bridge": self})
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        host, bound = self._server.server_address[:2]
        self.url = f"http://{host}:{bound}"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def editor_link(self, editor: str, backend: str) -> str:
        """The address to open, with the token in the FRAGMENT.

        Not in the query: a fragment is never sent to the server, and the editor
        is a page on somebody else's host — GitHub Pages in the usual case. A
        token in the query string would be in their access log, in the referrer
        of everything the page fetches, and in the history of whoever is
        screen-sharing.
        """
        joiner = "&" if "?" in editor else "?"
        return (f"{editor}{joiner}backend={backend}&bridge={self.url}"
                f"#bridge-token={self.token}")


class _Handler(BaseHTTPRequestHandler):
    bridge: Bridge

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass  # the MCP client owns this process's stderr; a request log would be noise

    # ----------------------------------------------------------- the locks

    def _origin_ok(self) -> bool:
        """An allowlisted origin, or a page served from this machine.

        Localhost is allowed without being listed because running the editor
        yourself is the one setup where the port it landed on is not knowable
        in advance — and a page on this machine is a page whoever is at this
        machine opened.
        """
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return True  # not a browser: curl, a test, the MCP process itself
        if origin in self.bridge.origins:
            return True
        return origin.startswith(("http://localhost:", "http://127.0.0.1:",
                                  "http://localhost", "http://127.0.0.1"))

    def _token_ok(self, query: dict) -> bool:
        given = (query.get("token") or [""])[0]
        if not given:
            header = self.headers.get("Authorization") or ""
            given = header.partition(" ")[2] if header.lower().startswith("bearer ") else header
        return secrets.compare_digest(given.strip(), self.bridge.token)

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin.rstrip("/"))
            self.send_header("Vary", "Origin")
        # Chrome calls a request from an https page to 127.0.0.1 a private
        # network request and will not send it unless the preflight says this.
        # The editor's own backend guide has been through exactly this.
        if self.headers.get("Access-Control-Request-Private-Network"):
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, query: dict) -> bool:
        if not self._origin_ok():
            self._send(403, {"error": "this origin is not allowed to use the bridge"})
            return False
        if not self._token_ok(query):
            self._send(401, {"error": "the bridge token is missing or wrong"})
            return False
        return True

    # ---------------------------------------------------------- the routes

    def do_OPTIONS(self) -> None:  # noqa: N802 - the stdlib's spelling
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parts = urlparse(self.path)
        query = parse_qs(parts.query)
        if parts.path == "/hello":
            # what a page checks before it says it is connected; deliberately
            # behind the same locks as everything else
            if not self._guard(query):
                return
            return self._send(200, {
                "bridge": "stageflow",
                "ok": True,
                "session": self.bridge.session,
                "messages": len(self.bridge._messages),
            })
        if parts.path == "/events":
            if not self._guard(query):
                return
            return self._events(int((query.get("from") or ["0"])[0]))
        return self._send(404, {"error": f"no such thing: {parts.path}"})

    def do_POST(self) -> None:  # noqa: N802
        parts = urlparse(self.path)
        query = parse_qs(parts.query)
        if parts.path != "/graph":
            return self._send(404, {"error": f"no such thing: {parts.path}"})
        if not self._guard(query):
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "the body is not JSON"})
        pipeline = body.get("pipeline")
        if not isinstance(pipeline, dict):
            return self._send(400, {"error": "expected {\"pipeline\": {...}}"})
        self.bridge.graph_from_editor(pipeline)
        return self._send(200, {"ok": True})

    def _events(self, start: int) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.bridge._joined()
        try:
            for message in self.bridge.follow(start):
                if message is None:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    frame = f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
                    self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return  # the page was closed or reloaded; it will come back with ?from=
        finally:
            self.bridge._left()

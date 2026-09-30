"""A StageFlow backend, as this server talks to it.

Everything here is somebody else's backend. The seven endpoints it answers are
the contract the editor is written against — `GET /api/stages`, `/api/meta`,
`/api/secrets`, and the five of the run API — and this is a second client of
exactly that contract, so it asks for nothing a backend does not already
serve. Nothing in this package executes a pipeline, validates one, or knows
what a node means: the semantics live in the core the backend runs, and a
second opinion here would be a second implementation to drift.

Two things travel with every request, and both are settings rather than
constants, for the same reasons the editor gives them:

  - **the credential** — the header NAME as well as the value, because
    `Authorization: Bearer …`, `X-Api-Key: …` and whatever a gateway reads are
    all real, and a tool that picks one for its user is a tool that cannot talk
    to half the backends there are;
  - **the language** — `Accept-Language`, which is how a backend's own
    messages (a refused run, a list of validation errors) come back in the
    language the conversation is happening in. Stage prose is not negotiated
    this way and must not be: it arrives in every language at once and is
    chosen from later (see `catalog.py`).

Written on `urllib` rather than a client library on purpose. This process is a
relay; a dependency that has to be installed before an agent can ask whether a
graph is valid is a dependency that will be the reason it never asks.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

#: What the editor sends when nothing else was configured, and so do we.
DEFAULT_AUTH_HEADER = "Authorization"

#: How long any single request may take. A run is not waited out inside one
#: request — the run API is asked again and again instead — so this bounds a
#: question, never an execution.
DEFAULT_TIMEOUT = 30.0


class BackendError(RuntimeError):
    """Anything the backend answered that is not a result.

    `status` is the HTTP code where there was one and 0 for a connection that
    never got that far, which is a distinction worth keeping: "it refused the
    graph" and "nothing answered at that address" are different problems and
    have different fixes.
    """

    def __init__(self, status: int, message: str, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.url = url


def _is_loopback(host: str) -> bool:
    """Whether an address names this machine — where plain http is normal."""
    text = host.strip().lower()
    name = text.split("]")[0].lstrip("[") if text.startswith("[") else text.split(":")[0]
    return name in ("localhost", "::1") or name.startswith("127.")


def normalize_url(raw: str) -> str:
    """Bring a typed address to the shape the endpoints are built from.

    Mostly the editor's `normalizeBackendUrl`, and deliberately so: people
    paste the address they used there. `localhost:8765`,
    `http://localhost:8765/` and `http://localhost:8765/api` are one backend —
    a trailing slash and a trailing `/api` are cut off, or the endpoints come
    out as `…/api/api/stages`.

    One rule differs, and on purpose. A typed address with no scheme gets
    `https://` unless it is loopback, where it gets `http://`. The editor fills
    in `http://` for everything because it runs on a page that cannot reach a
    plain-http backend anyway — the browser stops it. Nothing stops this
    process, and it carries a credential: a bare hostname that quietly became
    `http://` would put somebody's token on the wire in the clear.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("no backend address given")
    if "://" not in text:
        text = f"http://{text}" if _is_loopback(text) else f"https://{text}"
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) address: {raw!r}")
    path = parts.path.rstrip("/")
    if path.endswith("/api"):
        path = path[: -len("/api")]
    return f"{parts.scheme}://{parts.netloc}{path}"


def accept_language(lang: str | None) -> str:
    """The header the editor sends: the chosen language, English behind it.

    The fallback is not politeness — a backend with no catalog for the asked
    language would otherwise answer in whatever its source language happens to
    be, and an agent relaying an error to a reader cannot tell that it did.
    """
    tag = (lang or "").strip()
    if not tag or tag.lower().startswith("en"):
        return "en"
    return f"{tag}, en;q=0.8"


class Backend:
    """One backend at one address, with one credential.

    Deliberately not a session pool or a retry policy: every method is one
    question, answered or refused, and the caller — a tool, which is an agent —
    decides what to do about a refusal. A retry hidden in here would turn "the
    stand is busy" into a silent wait an agent cannot reason about.
    """

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        auth_header: str = DEFAULT_AUTH_HEADER,
        lang: str | None = None,
        plan: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = normalize_url(url)
        self.token = (token or "").strip() or None
        self.auth_header = (auth_header or DEFAULT_AUTH_HEADER).strip()
        self.lang = (lang or "").strip() or None
        self.plan = (plan or "").strip() or None
        self.timeout = timeout

    @property
    def credential_in_the_clear(self) -> bool:
        """A token about to travel over plain http to something that is not this
        machine. Reported rather than refused: a backend behind a TLS-terminating
        proxy on a private network is a real deployment, and this process is in no
        position to know. Saying nothing at all is what would be wrong."""
        return bool(self.token) and self.url.startswith("http://") and not _is_loopback(
            self.url[len("http://"):]
        )

    # ----------------------------------------------------------- the wire

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Language": accept_language(self.lang),
            "User-Agent": "stageflow-mcp",
        }
        if self.token:
            headers[self.auth_header] = self.token
        return headers

    def _shown(self, path: str) -> str:
        """`?plan=` goes on the two questions asked BEFORE a run and on no
        others. A run takes its plan from the credential; sending one would be
        offering the caller a say in its own ceiling, and the backend refuses
        the mismatch anyway."""
        if not self.plan:
            return path
        return f"{path}?plan={urllib.parse.quote(self.plan)}"

    def _request(
        self, method: str, path: str, body: dict | None = None, timeout: float | None = None
    ) -> Any:
        url = f"{self.url}{path}"
        data = None
        headers = self.headers
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as answer:
                raw = answer.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as refused:
            raise BackendError(refused.code, _message_of(refused), url) from None
        except urllib.error.URLError as unreachable:
            raise BackendError(0, f"{url}: {unreachable.reason}", url) from None
        except TimeoutError:
            raise BackendError(0, f"{url}: timed out", url) from None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise BackendError(0, f"{url}: the answer is not JSON", url) from None

    # ------------------------------------------------------- what it is

    def meta(self) -> dict:
        """`/api/meta` — the node types, the plan and the limits of THIS caller.

        Optional in the contract, so a backend that does not serve it is not a
        broken one; the caller decides what to do with the absence rather than
        having a guess made for it here.
        """
        return self._request("GET", self._shown("/api/meta"))

    def stages(self) -> dict:
        """`/api/stages` — the specs of the stages this caller may use.

        The one endpoint the contract calls required, which makes it the
        connection check as well: an address that answers this is a backend.
        """
        answer = self._request("GET", self._shown("/api/stages"))
        # a bare object of specs without the `stages` wrapper is accepted too,
        # exactly as the editor accepts it
        return answer.get("stages", answer) if isinstance(answer, dict) else {}

    def secrets(self) -> dict:
        return self._request("GET", "/api/secrets")

    # ---------------------------------------------------------- the runs

    def start_run(
        self,
        pipeline: dict,
        variables: dict | None = None,
        *,
        mode: str = "run",
        delay: float = 0.0,
    ) -> dict:
        """`POST /api/run` -> `{id, state}`.

        A graph the backend will not run is refused HERE, from the request:
        both reference backends parse and validate before a thread, a slot or
        an id exists. That is what makes this endpoint usable as a check and
        not only as an execution (see `tools.validate_pipeline`).
        """
        body: dict[str, Any] = {"pipeline": pipeline, "mode": mode, "delay": delay}
        if variables:
            body["vars"] = variables
        return self._request("POST", "/api/run", body)

    def run_state(self, run_id: str) -> dict:
        return self._request("GET", f"/api/run/{urllib.parse.quote(run_id)}")

    def control(self, run_id: str, action: str, **extra: Any) -> dict:
        """`step` | `resume` | `pause` | `stop` | `delay`."""
        return self._request(
            "POST", f"/api/run/{urllib.parse.quote(run_id)}/control", {"action": action, **extra}
        )

    def events(self, run_id: str, start: int = 0, timeout: float = 15.0) -> Iterator[dict]:
        """The event log of a run, from `start`, as it comes.

        SSE read by hand rather than by a library: the frames are `data: {…}`
        and a comment line is a heartbeat, which is the whole format. Every
        event carries its own `index`, so a reader that broke off says where to
        resume without consulting anything else.

        The stream ends when the run does. A run that is still going will hold
        this open, so nothing here is called on a live run without a bound.
        """
        url = f"{self.url}/api/run/{urllib.parse.quote(run_id)}/events?from={int(start)}"
        headers = {**self.headers, "Accept": "text/event-stream"}
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as stream:
                for line in stream:
                    text = line.decode("utf-8", "replace").rstrip("\n")
                    if not text.startswith("data:"):
                        continue  # a heartbeat comment, or the blank between frames
                    try:
                        yield json.loads(text[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as refused:
            raise BackendError(refused.code, _message_of(refused), url) from None
        except (urllib.error.URLError, TimeoutError):
            return  # a log that stopped arriving is not a reason to lose the run


def _message_of(refused: urllib.error.HTTPError) -> str:
    """What a backend says when it refuses.

    The contract puts it in `{"error": "…"}` — that is the field the editor
    shows in its status bar — so that is the field to read. Anything else gets
    the body as it came, trimmed: a wall of HTML from a proxy in front of the
    backend is still the most useful thing there is to say.
    """
    try:
        raw = refused.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - the body was already consumed
        raw = ""
    finally:
        # an HTTPError is a response, and an unclosed one is a socket held
        # until the collector gets round to it
        refused.close()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            return parsed["error"]
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
            return parsed["detail"]
    except json.JSONDecodeError:
        pass
    text = " ".join(raw.split())
    return text[:500] if text else f"HTTP {refused.code}"

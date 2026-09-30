"""Which backend this is talking to, and how that gets decided.

Naming it on the command line is the certain way, and for a long-lived setup
the right one: it is in the client's config, it is the same every session, and
nothing has to be agreed at the moment somebody wanted to get work done.

It is not the only way, because it cannot be. A person who has just installed
this does not yet know they were supposed to put an address in a JSON file, and
the assistant they are talking to cannot put it there for them. So if no
address was given, the first tool that needs one **asks** — through the MCP
client's own prompt (`elicitation`), not through the conversation.

Through the client's prompt specifically, and that is the whole reason this
file exists rather than a `backend` argument on every tool. An argument is
written by the model, which means it passes through the conversation, the
transcript and whatever logs that client keeps. An address surviving that is
untidy; a credential surviving it is a leak. So the credential is only ever
taken from the command line, the environment, or a prompt the model does not
see — and there is deliberately no tool that accepts one.

Not every client can be asked: at the time of writing the terminal Claude Code
can, its VS Code extension declares the capability and declines every request,
and the Desktop code tab offers none. So the asking is attempted, and what
happens when it is refused is an error that says what to do instead — never a
silent failure, and never a guess at an address.

The answer is remembered in a config file so it is asked once rather than once
a session. **The address only.** A token in a dotfile is a token in a backup,
in a synced folder, and in whatever reads the home directory next.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .backend import DEFAULT_AUTH_HEADER, Backend


class BackendUnknown(RuntimeError):
    """No address, and no way to ask for one. The message says what to do."""


class WhichBackend(BaseModel):
    """What a person is asked for, when the client can ask."""

    backend: str = Field(
        description=(
            "The StageFlow backend's address — the same one you would type on "
            "the editor's connection screen, e.g. https://stageflow.lazy.su or "
            "localhost:8765"
        ),
    )
    token: str = Field(
        default="",
        description=(
            "The credential, if that backend wants one. Leave empty to be an "
            "anonymous caller. It is not stored anywhere and is not shown to "
            "the assistant."
        ),
    )


def config_path() -> Path:
    """Where the remembered address lives — XDG, or its default."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "stageflow-mcp" / "config.json"


class Connection:
    """The backend in force, and everything needed to build another one.

    Holds the settings that are not the address — the header a credential goes
    in, the language, the plan to be shown — because those come from the
    command line either way and should not be asked about: somebody who has to
    be told what `--auth-header` is has not got a credential to put in it.
    """

    def __init__(
        self,
        backend: Backend | None = None,
        *,
        auth_header: str = DEFAULT_AUTH_HEADER,
        lang: str | None = None,
        plan: str | None = None,
        remember: bool = True,
    ) -> None:
        self.backend = backend
        self.auth_header = auth_header
        self.lang = lang
        self.plan = plan
        self.remember = remember

    # ------------------------------------------------------------- setting

    def use(self, address: str, token: str | None = None) -> Backend:
        self.backend = Backend(
            address,
            token=token,
            auth_header=self.auth_header,
            lang=self.lang,
            plan=self.plan,
        )
        return self.backend

    def recall(self) -> str | None:
        try:
            saved = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # never saved, unreadable, or written by something else
        address = saved.get("backend")
        return address if isinstance(address, str) and address else None

    def keep(self, address: str) -> None:
        """Remember the address for next time. Never the credential.

        Failing to write is not worth an error: the session has its backend,
        and the cost of a read-only home directory is being asked again.
        """
        if not self.remember:
            return
        path = config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"backend": address}, indent=2) + "\n",
                            encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ getting

    async def resolve(self, ctx=None) -> Backend:
        """The backend to use, asking for one if that is the only way left."""
        if self.backend is not None:
            return self.backend

        remembered = self.recall()
        if remembered:
            return self.use(remembered)

        answer = await self._ask(ctx)
        backend = self.use(answer.backend.strip(), (answer.token or "").strip() or None)
        self.keep(backend.url)
        return backend

    async def _ask(self, ctx) -> WhichBackend:
        if ctx is None or not getattr(getattr(ctx, "client_capabilities", None),
                                      "elicitation", None):
            raise BackendUnknown(
                "No StageFlow backend is configured, and this MCP client cannot be "
                "asked for one. Start the server with the address instead:\n"
                "  stageflow-mcp --backend https://your-backend.example\n"
                "It is the same address you would type on the editor's connection "
                "screen."
            )
        result = await ctx.elicit(
            "Which StageFlow backend should I work against?", WhichBackend
        )
        if result.action != "accept" or result.data is None:
            raise BackendUnknown(
                "No backend was given, so there is nothing to check a pipeline "
                "against. Ask again, or start the server with "
                "--backend https://your-backend.example"
            )
        if not (result.data.backend or "").strip():
            raise BackendUnknown("An empty address is not a backend.")
        return result.data

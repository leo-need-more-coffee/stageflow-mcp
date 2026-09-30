"""Starting the server, and checking the address before an agent depends on it.

    stageflow-mcp --backend https://stageflow.lazy.su
    stageflow-mcp --backend http://127.0.0.1:8765 --check

stdio only, and on purpose: this process is a relay between an MCP client and
somebody's backend, and the credential it carries belongs to the person whose
machine it runs on. An HTTP transport would make it a service, and a service
handing out somebody's token to whoever connects is a different thing entirely
— one worth building deliberately rather than by adding a flag.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .backend import DEFAULT_AUTH_HEADER, Backend, BackendError
from .bridge import HOSTED_EDITOR, Bridge
from .catalog import capabilities
from .connection import Connection, config_path


#: Where the published editor lives, for the link the bridge prints.
HOSTED_UI = "https://leo-need-more-coffee.github.io/stageflow-ui/"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stageflow-mcp",
        description="Talk to a StageFlow backend over MCP: write pipelines, check them, run them.",
    )
    parser.add_argument(
        "--backend", default=os.environ.get("STAGEFLOW_BACKEND", ""),
        help="the backend's address, as typed into the editor "
             "(env: STAGEFLOW_BACKEND). `localhost:8765` and `.../api` both work. "
             "Leave it out and the first tool that needs one asks — through the "
             "client's own prompt, if that client can be asked",
    )
    parser.add_argument(
        "--token", default=os.environ.get("STAGEFLOW_TOKEN", ""),
        help="the credential, if the backend wants one (env: STAGEFLOW_TOKEN). "
             "Without it you are whatever that backend calls an anonymous caller",
    )
    parser.add_argument(
        "--auth-header", default=os.environ.get("STAGEFLOW_AUTH_HEADER", DEFAULT_AUTH_HEADER),
        help=f"which header carries it (default: {DEFAULT_AUTH_HEADER})",
    )
    parser.add_argument(
        "--lang", default=os.environ.get("STAGEFLOW_LANG", ""),
        help="the language for the backend's own messages and for stage prose, "
             "as a tag: ru, en, ru-RU",
    )
    parser.add_argument(
        "--plan", default=os.environ.get("STAGEFLOW_PLAN", ""),
        help="ask to be SHOWN this plan instead of the caller's own. It changes "
             "what is offered and checked, never what a run is allowed",
    )
    parser.add_argument(
        "--bridge", nargs="?", const=0, type=int, default=None, metavar="PORT",
        help="also open a bridge to the editor on loopback, so a graph can be put "
             "on the canvas and read back from it. A port may be given; 0 or "
             "nothing picks a free one. Off unless asked for: it is a listening "
             "socket on this machine",
    )
    parser.add_argument(
        "--bridge-token", default=os.environ.get("STAGEFLOW_BRIDGE_TOKEN", ""),
        metavar="TOKEN",
        help="use this token for the bridge instead of a generated one — for a "
             "link worth bookmarking, or for a script. Leave it out and a fresh "
             "one is made at every start, which is the safer default",
    )
    parser.add_argument(
        "--bridge-origin", action="append", default=[], metavar="ORIGIN",
        help="an origin allowed to use the bridge, besides the hosted editor and "
             "anything served from this machine. May be repeated",
    )
    parser.add_argument(
        "--editor", default=os.environ.get("STAGEFLOW_EDITOR", HOSTED_UI),
        help=f"which copy of the editor the bridge link should point at "
             f"(default: {HOSTED_UI})",
    )
    parser.add_argument(
        "--no-remember", action="store_true",
        help=f"do not write an asked-for address to {config_path()} (a credential "
             f"is never written there either way)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="ask the backend what it is, print it, and exit — without starting a server",
    )
    return parser.parse_args(argv)


def check(backend: Backend) -> int:
    """What that address turned out to be, in the shape a person reads.

    Worth having as its own command: an MCP server that fails to start says so
    in a client's log, at the moment somebody is trying to do something else.
    """
    try:
        stages = backend.stages()
    except BackendError as refused:
        print(f"{backend.url}: {refused.message}", file=sys.stderr)
        return 1
    meta = capabilities(backend)
    print(f"backend      {backend.url}")
    print(f"credential   {'sent in ' + backend.auth_header if backend.token else 'none sent'}")
    print(f"stages       {len(stages)}")
    if meta.get("available") is False:
        print("meta         not served (node types and limits unknown)")
    else:
        print(f"core         {meta.get('stageflow', '?')}")
        print(f"plan         {meta.get('plan', '?')} ({meta.get('plan_source', '?')})")
        print(f"node types   {', '.join(meta.get('node_types') or []) or '?'}")
        limits = meta.get("limits") or {}
        if limits:
            print(f"limits       {json.dumps(limits, ensure_ascii=False)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    connection = Connection(
        auth_header=args.auth_header,
        lang=args.lang,
        plan=args.plan,
        remember=not args.no_remember,
    )
    address = args.backend or connection.recall()
    if address:
        try:
            connection.use(address, args.token)
        except ValueError as bad:
            print(str(bad), file=sys.stderr)
            return 2
    elif args.check:
        print(
            "no backend to check: pass --backend or set STAGEFLOW_BACKEND.\n"
            "It is the same address you would type on the editor's connection screen.",
            file=sys.stderr,
        )
        return 2

    backend = connection.backend

    if backend is not None and backend.credential_in_the_clear:
        print(
            f"warning: the credential will travel to {backend.url} over plain http.\n"
            "         Write the address with https:// if that backend serves it.",
            file=sys.stderr,
        )

    if args.check:
        return check(backend)

    from .server import build  # imported here so --check works without the MCP SDK

    bridge = None
    if args.bridge is not None:
        if backend is None:
            print(
                "--bridge needs a backend: the link it prints carries the address "
                "the editor should connect to. Pass --backend as well.",
                file=sys.stderr,
            )
            return 2
        bridge = Bridge(token=args.bridge_token or None,
                        origins=[HOSTED_EDITOR, *args.bridge_origin])
        bridge.start(args.bridge)
        bridge.link = bridge.editor_link(args.editor, backend.url)
        # stderr, because stdout is the MCP transport and a word on it would be
        # a protocol error rather than a message
        print(
            "bridge open. Open the editor at this address to see what the agent "
            f"draws:\n\n  {bridge.link}\n\n"
            "The token is in the # part, so it never reaches the editor's host.",
            file=sys.stderr,
        )

    build(connection, lang=args.lang, bridge=bridge).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

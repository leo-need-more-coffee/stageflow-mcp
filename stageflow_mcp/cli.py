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
from .catalog import capabilities


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stageflow-mcp",
        description="Talk to a StageFlow backend over MCP: write pipelines, check them, run them.",
    )
    parser.add_argument(
        "--backend", default=os.environ.get("STAGEFLOW_BACKEND", ""),
        help="the backend's address, as typed into the editor "
             "(env: STAGEFLOW_BACKEND). `localhost:8765` and `.../api` both work",
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
    if not args.backend:
        print(
            "no backend given: pass --backend or set STAGEFLOW_BACKEND.\n"
            "It is the same address you would type on the editor's connection screen.",
            file=sys.stderr,
        )
        return 2
    try:
        backend = Backend(
            args.backend,
            token=args.token,
            auth_header=args.auth_header,
            lang=args.lang,
            plan=args.plan,
        )
    except ValueError as bad:
        print(str(bad), file=sys.stderr)
        return 2

    if backend.credential_in_the_clear:
        print(
            f"warning: the credential will travel to {backend.url} over plain http.\n"
            "         Write the address with https:// if that backend serves it.",
            file=sys.stderr,
        )

    if args.check:
        return check(backend)

    from .server import build  # imported here so --check works without the MCP SDK

    build(backend, lang=args.lang).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

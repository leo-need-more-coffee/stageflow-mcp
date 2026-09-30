"""StageFlow over MCP: an agent's client of a StageFlow backend.

Nothing in this package executes or validates a pipeline. It speaks the seven
endpoints a StageFlow backend already answers — the same contract the editor is
written against — so it works against any of them, including one that was
deployed before this existed, and asks no host to add anything.
"""
from importlib.metadata import PackageNotFoundError, version as _package_version

DISTRIBUTION = "stageflow-mcp"


def _installed_version() -> str:
    """The version of the installed distribution.

    Read rather than written down, because a number in two places is a number
    that will disagree with itself. Running from a checkout that was never
    installed there is nothing to read it from, and saying so is better than
    a plausible figure somebody would compare with something.
    """
    try:
        return _package_version(DISTRIBUTION)
    except PackageNotFoundError:  # pragma: no cover - only in a bare checkout
        return "0.0.0+unknown"


__version__ = _installed_version()

__all__ = ["__version__"]

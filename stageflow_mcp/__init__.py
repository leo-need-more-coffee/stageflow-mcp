"""StageFlow over MCP: an agent's client of a StageFlow backend.

Nothing in this package executes or validates a pipeline. It speaks the seven
endpoints a StageFlow backend already answers — the same contract the editor is
written against — so it works against any of them, including one that was
deployed before this existed, and asks no host to add anything.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

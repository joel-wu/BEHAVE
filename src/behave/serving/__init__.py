"""Operator-owned local serving, separate from candidate execution and rewards."""

from .spec import ServingSpec

__all__ = ["ServingFailure", "ServingSession", "ServingSpec", "close_sessions", "start_sessions"]


def __getattr__(name):
    # Manifest/spec readers do not start the execution dependency graph.
    if name in {"ServingFailure", "close_sessions", "start_sessions"}:
        from . import lifecycle

        return getattr(lifecycle, name)
    if name == "ServingSession":
        from . import session

        return getattr(session, name)
    raise AttributeError(name)

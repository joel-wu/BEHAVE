"""Trusted in-process reference loader for unit tests only.

Production accepts submitted Behavior source only through
``transaction/isolated-4``.  Small simulator unit tests still need a cheap reference
whose Python execution is part of the test process; this adapter is registered only
when a test imports this module and its name makes that weaker boundary explicit.
"""

from __future__ import annotations

from behave.sim.core.reference import load_reference, register_loader
from behave.sim.core.refusal import Refusal, Refused

ADAPTER = "tests/transaction-in-process/1"


def _loader(sources, config):
    body = sources.get("behavior.py")
    if body is None:
        raise Refused(Refusal(
            "test-reference-loader-no-model-source",
            "the test adapter needs behavior.py", sources=sorted(sources)))
    namespace = {}
    exec(compile(body, "behavior.py", "exec"), namespace)  # noqa: S102
    wanted = config.get("class")
    if wanted:
        found = namespace.get(wanted)
        candidates = [found] if type(found) is type else []
    else:
        candidates = [
            value for value in namespace.values()
            if type(value) is type
            and (hasattr(value, "process") or hasattr(value, "process_slot"))
            and hasattr(value, "reset")
        ]
    if len(candidates) != 1:
        raise Refused(Refusal(
            "test-reference-loader-class-ambiguous",
            "the test source must select exactly one reset/process model",
            wanted=str(wanted or ""),
            found=[value.__name__ for value in candidates]))
    kwargs = {name: value for name, value in config.items() if name != "class"}
    cls = candidates[0]
    return lambda: cls(**kwargs)


def load(sources, config=None, loader="tests"):
    """Load trusted test source under the explicitly weaker test adapter."""

    register_loader(ADAPTER, _loader)
    return load_reference(ADAPTER, sources, config=config, loader=loader)


__all__ = ["ADAPTER", "load"]

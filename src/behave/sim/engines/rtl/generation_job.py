"""Strict, non-authoritative RTL proposal records crossing the worker boundary.

The parent selects segments from concrete traces and owns the final replay; the
source session answers each local query. Both ends encode the answer with the
same versioned record.
"""

from __future__ import annotations

import math

from ...core.stimulus import Stimulus
from .. import probing

__all__ = [
    "decode_probe_reply",
    "encode_probe_reply",
]


_PROBE_VERSION = "rtl-generation-probe/13"


def decode_probe_reply(value, *, expected_catalogue_digest=None):
    if type(value) is not dict or value.get("version") != _PROBE_VERSION \
            or set(value) != {
                "version", "outcome", "scope", "goal", "candidate", "detail",
                "solve_ms", "catalogue_digest",
            }:
        raise ValueError("an isolated RTL Probe reply has its complete versioned shape")
    if type(value["outcome"]) is not str or type(value["scope"]) is not str \
            or value["goal"] is not None and type(value["goal"]) is not str \
            or type(value["detail"]) is not str \
            or type(value["solve_ms"]) not in {int, float} \
            or not math.isfinite(value["solve_ms"]) or value["solve_ms"] < 0:
        raise TypeError("an isolated RTL Probe reply has exact finite field types")
    outcome = probing.Outcome(value["outcome"])
    scope = probing.Scope(value["scope"])
    if scope is not probing.Scope.RTL_LOCAL_HIT:
        raise ValueError("an RTL generation worker answers only RTL-local-hit probes")
    candidate = (None if value["candidate"] is None
                 else Stimulus.from_dict(value["candidate"]))
    digest = value["catalogue_digest"]
    if type(digest) is not str or len(digest) != 64 \
            or any(char not in "0123456789abcdef" for char in digest):
        raise TypeError("an RTL Probe reply names its actual source catalogue digest")
    if expected_catalogue_digest is not None and digest != expected_catalogue_digest:
        raise ValueError("the RTL Probe worker rebuilt a different source catalogue")
    return probing.ProbeResult(
        outcome, scope, goal=value["goal"], candidate=candidate,
        detail=value["detail"], solve_ms=float(value["solve_ms"]))


def encode_probe_reply(got, catalogue_digest):
    """Both RTL query representations return the same non-authoritative proposal."""
    return {
        "version": _PROBE_VERSION,
        "outcome": got.outcome.value,
        "scope": got.scope.value,
        "goal": got.goal,
        "candidate": None if got.candidate is None else got.candidate.as_dict(),
        "detail": got.detail,
        "solve_ms": float(got.solve_ms),
        "catalogue_digest": catalogue_digest,
    }

"""Process-local authority for code already running as one named judge job.

An environment variable is input, not authority: the verifier's caller can set one.
Code that avoids recursively starting another namespace therefore asks this scope,
which the one-shot and session dispatchers enter inside a registered job. A
fresh worker process starts outside the scope and a candidate child cannot inherit the
``ContextVar`` value as a protocol claim.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

__all__ = ["inside_named_job"]


_NAMED_JOB = ContextVar("behave_named_job", default=False)


def inside_named_job() -> bool:
    """Whether this call is below the registered named-job dispatcher."""

    return _NAMED_JOB.get() is True


@contextmanager
def _named_job_scope():
    """Enter the trusted local half of one named worker dispatch."""

    token = _NAMED_JOB.set(True)
    try:
        yield
    finally:
        _NAMED_JOB.reset(token)

"""Cooperative cancellation of trusted runtime preparation, not byte validation.

The caller supplies its existing deadline check. With no scope installed these
operations retain their ordinary blocking behavior. Cancellation never grants a
mount or attestation; already-running filesystem calls still have to return.
"""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from contextvars import ContextVar

from behave.core import runtime_defaults as defaults

_CHECK = ContextVar("behave_preparation_check", default=None)


class PreparationCancelled(BaseException):
    """An invocation-local stop, which must not cancel an unrelated waiter."""

    # Only a process owner can establish this while unwinding start/run.
    # An arbitrary cancellation does not itself prove that a worker was reaped.
    cleanup_confirmed = False


def check_preparation():
    check = _CHECK.get()
    if check is not None:
        check()


def poll_interval():
    return None if _CHECK.get() is None else defaults.ISOLATION_COMPLETION_POLL_SECONDS


@contextmanager
def preparation_scope(check):
    """Install a check raising PreparationCancelled (or a subclass) on stop."""
    if not callable(check):
        raise TypeError("preparation cancellation needs a callable check")
    token = _CHECK.set(check)
    try:
        yield
    finally:
        _CHECK.reset(token)


def acquire_lock(descriptor, mode):
    """Wait for the same flock, allowing the current invocation to cancel."""
    interval = poll_interval()
    if interval is None:
        fcntl.flock(descriptor, mode)
        return
    while True:
        check_preparation()
        try:
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(interval)

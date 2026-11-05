"""Optional operation spans, separate from exact-query evidence and budgets.

Emit before entering expensive work, so an interrupted worker leaves its last
known stage. No formula traversal, new deadline, or solver policy lives here.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import time

from ..core.refusal import Refused

_SINK = ContextVar('analysis_diagnostic_sink', default=None)
_PATH = ContextVar('analysis_diagnostic_path', default=())


@contextmanager
def diagnostic_scope(sink):
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def diagnostics_active():
    return _SINK.get() is not None


@contextmanager
def diagnostic_stage(stage, **fields):
    sink = _SINK.get()
    detail = {}
    if sink is None:
        yield detail
        return

    def emit(event, **values):
        try:
            sink(dict(stage=list(path), event=event, **fields, **values))
        except Exception:
            pass  # Diagnostic failure cannot replace an answer or cancellation.

    path = (*_PATH.get(), stage)
    token = _PATH.set(path)
    started = time.monotonic_ns()
    emit('started')
    status = 'returned'
    try:
        yield detail
    except BaseException as error:
        status = 'raised'
        detail['exception'] = type(error).__name__
        if isinstance(error, Refused):
            detail['refusal'] = error.refusal.as_dict()
        raise
    finally:
        emit(status, elapsed_ns=time.monotonic_ns() - started, detail=detail)
        _PATH.reset(token)

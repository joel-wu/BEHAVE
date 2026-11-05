"""One product query's deadline, including native Z3 construction calls.

The synchronous product worker exclusively owns its Z3 context. Interrupting
that context stops its current native operation, not another rollout. Joining
the watchdog before returning prevents a late interrupt reaching the next
query. Immutable prepared terms remain owned by the worker across queries.
"""

import math
import threading
import time

import z3


def is_solver_cancellation(error):
    """Z3's native interruption is no answer, not a language capability error.

    Z3Py exposes this reason as bytes or text, without a typed status. Only
    these exact reasons qualify; sort/encoding/internal errors still propagate.
    An interruption does not by itself establish that our deadline elapsed.
    """
    return (isinstance(error, z3.Z3Exception) and len(error.args) == 1
            and error.args[0] in ("canceled", "interrupted", b"canceled", b"interrupted"))


class QueryDeadline:
    def __init__(self, context, timeout_ms, *, started):
        self._context = context
        self._deadline = started + timeout_ms / 1000
        self._expired = threading.Event()
        # A bound callback would form self -> timer -> callback -> self and
        # retain this query's native Z3 arena until Python cyclic collection.
        # The watchdog needs only these two values, not its owning deadline.
        self._timer = threading.Timer(
            max(0, self._deadline - time.monotonic()), self._interrupt,
            args=(context, self._expired))
        self._timer.daemon = True

    @staticmethod
    def _interrupt(context, expired):
        expired.set()
        context.interrupt()

    def remaining_ms(self):
        remaining = math.ceil((self._deadline - time.monotonic()) * 1000)
        if self._expired.is_set() or remaining <= 0:
            raise TimeoutError("RTL local query construction or solving exhausted its query budget")
        return remaining

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, kind, error, traceback):
        self._timer.cancel()
        self._timer.join()
        # Native cancellation can raise Z3Exception rather than returning
        # UNKNOWN. Only our own elapsed deadline establishes a time-limit cause;
        # unrelated native errors must keep their original classification.
        if kind is None or isinstance(error, z3.Z3Exception):
            self.remaining_ms()

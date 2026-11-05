"""Attempt cancellation and scoped per-operation execution windows.

The loop checks settled boundaries; supervised backends enforce the window
while blocked. This is not a way to kill arbitrary in-process Python tools.
The application must still own the complete attempt's process tree.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .attempt import AttemptInterrupted
from .replay import Tape


@dataclass(frozen=True, slots=True)
class AttemptControl:
    cancelled: Callable[[], bool] = lambda: False
    wall_clock: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not all(callable(item) for item in (self.cancelled, self.wall_clock, self.monotonic)):
            raise TypeError("attempt control requires clock/cancellation predicates")


@dataclass(frozen=True, slots=True)
class ExecutionWindow:
    control: AttemptControl
    expires_at: float | None = None
    monotonic_deadline: float | None = None

    def remaining(self) -> float:
        if self.monotonic_deadline is None:
            return math.inf  # No cumulative attempt deadline.
        wall, mono = self.control.wall_clock(), self.control.monotonic()
        if not math.isfinite(wall) or not math.isfinite(mono):
            raise AttemptInterrupted("attempt-clock-invalid")
        return max(0., min(self.expires_at - wall, self.monotonic_deadline - mono))

    def checkpoint(self, tape: Tape) -> str | None:
        # Replay settled history independent of today's clock/cancellation.
        if tape.cursor < len(tape.events):
            event = tape.events[tape.cursor]
            if event.kind == "interrupted":
                tape.take("interrupted")
                raise AttemptInterrupted(event.payload["code"])
            if event.kind == "terminal" and event.payload["reason"] == "wall_time_limit":
                return "wall_time_limit"
            return None
        if self.control.cancelled():
            tape.record("interrupted", {"code": "attempt-cancelled"})
            raise AttemptInterrupted("attempt-cancelled")
        return None


_WINDOW: ContextVar[ExecutionWindow | None] = ContextVar("behave_execution_window", default=None)


def current_window() -> ExecutionWindow | None:
    return _WINDOW.get()


@contextmanager
def execution_window(window: ExecutionWindow):
    token = _WINDOW.set(window)
    try:
        yield
    finally:
        _WINDOW.reset(token)

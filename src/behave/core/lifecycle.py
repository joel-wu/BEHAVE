"""Execution ownership, independent of committed artifacts and their verdicts.

A timeout is not evidence of a live process; a returned artifact is not evidence
of a dead one. Only the lifetime owner can establish quiescence. Unknown and
incomplete cleanup both require external fencing before another stage starts.
"""

from enum import Enum

from .records import RecordError


class CleanupState(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"

    @property
    def quiescent(self) -> bool:
        return self is CleanupState.COMPLETE

    @classmethod
    def combine(cls, *states: "CleanupState") -> "CleanupState":
        if any(type(state) is not cls for state in states):
            raise TypeError("cleanup requires explicit CleanupState witnesses")
        if cls.INCOMPLETE in states:
            return cls.INCOMPLETE
        return cls.UNKNOWN if cls.UNKNOWN in states else cls.COMPLETE

    def require_quiescent(self, owner: str) -> None:
        if not self.quiescent:
            raise RecordError(f"{owner}-cleanup-{self.value}",
                              "execution is not quiescent; external fencing is required")

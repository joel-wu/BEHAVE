"""Incremental execution of the finite ordered ready/valid monitor.

The algebraic rule in ``ready_valid_product`` remains the symbolic definition
and differential oracle. This executor uses an active queue and deadline heap:
retired requests are never scanned during a step. ``state_image`` expands the
same finite state only when an audit or a test explicitly asks for it.
"""

from collections import deque
from dataclasses import dataclass
from heapq import heappop, heappush

from .ready_valid_product import OrderedReadyValidConfig, OrderedReadyValidEvent


@dataclass(slots=True)
class _Request:
    index: int
    at: int
    owed: int
    offered: bool = False
    reported: bool = False


@dataclass(frozen=True, slots=True)
class OrderedObservation:
    hits: dict
    opportunities: dict
    deadline_hits: tuple  # (finite-state index, acceptance slot), in index order
    held_before: tuple
    stability_dropped: bool
    stability_changed: bool
    outstanding: int
    reset_policy_unresolved: bool = False


class OrderedConcreteMonitor:
    """One private executor per concrete ledger, with no cross-run state."""

    GOALS = ("monitor.sink-stability", "monitor.outstanding-exceeded",
             "monitor.output-without-transaction", "monitor.response-deadline")

    def __init__(self, config: OrderedReadyValidConfig):
        if config.identifier_index is not None:
            raise ValueError("the incremental ordered executor cannot correlate identifiers")
        self.config = config
        self._reset()

    def _reset(self):
        self.accepted_count = 0
        self.stalled = False
        self.held = (0,) * self.config.payload_fields
        self._history = []
        self._pending = deque()
        self._waiting = {}
        self._deadlines = []

    def step(self, event: OrderedReadyValidEvent) -> OrderedObservation:
        config = self.config
        if len(event.output_payload) != config.payload_fields:
            raise ValueError("the output payload does not match the configured arity")
        hits = dict.fromkeys(self.GOALS, False)
        chances = dict(hits)
        before = self.held
        if event.reset:
            unresolved = config.reset_policy is None and bool(self._pending)
            if config.reset_policy == "clears":
                self._reset()
            else:
                self.stalled = False
                self.held = (0,) * config.payload_fields
            return OrderedObservation(hits, chances, (), before, False, False,
                                      len(self._pending), unresolved)

        counting = config.answers_per_transaction is not None
        if counting and event.input_accepted:
            index = self.accepted_count
            self.accepted_count += 1
            # The finite rule still counts an acceptance beyond its table, but
            # cannot select an out-of-range slot. Production ledgers are sized
            # to their complete acceptance count; preserve this boundary too.
            if index < config.queue_capacity:
                request = _Request(index, event.slot, config.answers_per_transaction)
                self._history.append(request)
                self._pending.append(request)
                if config.response_deadline is not None:
                    self._waiting[index] = request
                    heappush(self._deadlines,
                             (event.slot + config.response_deadline, index))

        # Deadlines are checked before this slot's first offer or retirement.
        # An opportunity counts every pending, unoffered, unreported request.
        expired = []
        if config.response_deadline is not None:
            chances["monitor.response-deadline"] = len(self._waiting)
            while self._deadlines:
                deadline, index = self._deadlines[0]
                if index not in self._waiting:
                    heappop(self._deadlines)
                elif deadline < event.slot:
                    heappop(self._deadlines)
                    request = self._waiting.pop(index)
                    request.reported = True
                    expired.append((index, request.at))
                else:
                    break
            expired.sort()  # finite-table evidence order, also for nonuniform slots
            hits["monitor.response-deadline"] = bool(expired)

        target = self._pending[0] if self._pending else None
        if event.output_valid and not self.stalled and target is not None:
            target.offered = True
            self._waiting.pop(target.index, None)
        transfer = event.output_valid and event.output_ready
        if counting:
            chances["monitor.output-without-transaction"] = transfer
            hits["monitor.output-without-transaction"] = transfer and target is None
            if transfer and target is not None:
                target.offered = True
                self._waiting.pop(target.index, None)
                target.owed -= 1
                if target.owed == 0:
                    self._pending.popleft()
                    self._waiting.pop(target.index, None)

        outstanding = len(self._pending)
        if config.max_outstanding is not None:
            chances["monitor.outstanding-exceeded"] = True
            hits["monitor.outstanding-exceeded"] = outstanding > config.max_outstanding
        dropped = self.stalled and not event.output_valid
        changed = (self.stalled and event.output_valid
                   and any(old != new for old, new in zip(before, event.output_payload)))
        hits["monitor.sink-stability"] = dropped or changed
        chances["monitor.sink-stability"] = self.stalled
        self.stalled = event.output_valid and not event.output_ready
        if self.stalled:
            self.held = event.output_payload
        return OrderedObservation(hits, chances, tuple(expired), before,
                                  dropped, changed, outstanding)

    def pending_requests(self):
        return [{"at": request.at, "owed": request.owed, "reported": request.reported}
                for request in self._pending]

    def state_image(self):
        """Expand the algebraic state on demand, not on the per-cycle path."""
        if self.config.counted:
            return {"accepted_count": self.accepted_count,
                    "outstanding": len(self._pending),
                    "stalled": self.stalled, "held": self.held}
        padding = self.config.queue_capacity - len(self._history)
        return {
            "accepted_count": self.accepted_count,
            "pending": tuple(r.owed > 0 for r in self._history) + (False,) * padding,
            "owed": tuple(r.owed for r in self._history) + (0,) * padding,
            "accepted_at": tuple(r.at for r in self._history) + (0,) * padding,
            "offered": tuple(r.offered for r in self._history) + (False,) * padding,
            "reported": tuple(r.reported for r in self._history) + (False,) * padding,
            "stalled": self.stalled,
            "held": self.held,
        }

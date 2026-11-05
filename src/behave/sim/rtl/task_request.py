"""Validated queue/schedule requests for the finite ready/valid RTL product.

The concrete stimulus, symbolic formula and isolated worker share this codec.
It owns finite request coordinates and task/run membership, not solver state,
initial-state selection or a score decision.
"""

from __future__ import annotations

from ..core.input_queue import input_completion_environment
from ..core.physical_value import pack_value, packed_width, unpack_value
from ..core.refusal import Refusal, Refused
from ..core.stimulus import EventSlot, Stimulus
from .request_layout import coordinate_bounds

__all__ = ["ReadyValidRequestCodec"]


def _positive_integer(value, name):
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


class ReadyValidRequestCodec:
    """A finite valuation image of one queue plus a per-slot schedule."""

    def __init__(self, contract, queue_bound, reset_hold_cycles,
                 memory_environment=None, initial=None):
        self.contract = contract
        self.source = contract.inputs()[0]
        self.sink = contract.outputs()[0]
        self.queue_bound = _positive_integer(queue_bound, "queue_bound")
        self.offer = f"schedule.offer.{self.source.name}"
        self.ready = self.sink.adapter.environment_controls[0]
        self.reset = contract.clocks[0].reset
        if type(reset_hold_cycles) is not int:
            raise TypeError("reset_hold_cycles must be an exact integer")
        if reset_hold_cycles < 0:
            raise ValueError("reset_hold_cycles must be nonnegative")
        self.reset_hold = reset_hold_cycles
        self.memory_environment = memory_environment
        self.initial = initial
        self.length = f"queue.{self.source.name}.length"
        bounds = [(self.offer, (0, 1)), (self.ready, (0, 1)),
                  (self.reset, (0, 1)), (self.length, (0, self.queue_bound))]
        self.cells = []
        for index in range(self.queue_bound):
            row = []
            for field in self.source.fields:
                name = f"queue.{self.source.name}.{index}.{field.name}"
                bounds.append((name, (0, (1 << packed_width(field.type)) - 1)))
                row.append(name)
            self.cells.append(tuple(row))
        if initial is not None:
            bounds.extend(initial.bounds.items())
        self.bounds = coordinate_bounds(bounds)

    def encode(self, stimulus, *, initial_state=None):
        if type(stimulus) is not Stimulus:
            raise TypeError("a ready/valid exact request encodes one exact Stimulus")
        faults = stimulus.faults_against(self.contract)
        if faults:
            raise Refused(Refusal(
                "rtl-task-request-outside-contract",
                "the request is not a run of the admitted task contract",
                first=faults[0], count=len(faults)))
        environment = input_completion_environment(stimulus, self.contract)
        if environment.faults:
            raise Refused(Refusal(
                "rtl-task-request-environment-outside-domain",
                "the concrete request violates the terminal input environment",
                first=environment.faults[0].why,
                count=len(environment.faults)))
        if self.contract.memories:
            got = stimulus.memory_environment
            expected = self.memory_environment
            if got is None or expected is None or got.digest != expected.digest \
                    or got.as_dict() != expected.as_dict():
                raise Refused(Refusal(
                    "rtl-task-memory-environment-mismatch",
                    "the concrete request uses a different external-memory run "
                    "from this exact product"))
        queues = dict(stimulus.queues)
        queue = tuple(queues.get(self.source.name, ()))
        common = {self.length: len(queue)}
        for index, names in enumerate(self.cells):
            payload = queue[index] if index < len(queue) else None
            common.update({
                name: (pack_value(field.type, value)
                       if payload is not None else 0)
                for field, name, value in zip(
                    self.source.fields, names,
                    payload if payload is not None else (0,) * len(names))
            })
        if self.initial is not None:
            common.update(self.initial.encode(initial_state))
        rows = []
        for slot in stimulus.slots:
            row = dict(common)
            row[self.offer] = int(self.source.name in slot.offers)
            row[self.ready] = int(dict(slot.controls)[self.ready])
            row[self.reset] = int(dict(slot.controls)[self.reset])
            rows.append(row)
        return rows

    def decode(self, rows):
        if not rows:
            raise Refused(Refusal(
                "rtl-task-request-empty",
                "a task-domain request needs one schedule valuation per slot"))
        if self.initial is not None:
            self.initial.decode(rows)
        first = dict(rows[0])
        if set(first) != set(self.bounds):
            raise Refused(Refusal(
                "rtl-task-request-shape",
                "the decoded request does not carry the complete queue and schedule "
                "valuation", expected=sorted(self.bounds), got=sorted(first)))
        length = first[self.length]
        if type(length) is not int or not 0 <= length <= self.queue_bound:
            raise Refused(Refusal(
                "rtl-task-request-queue-length",
                "the decoded queue length is outside the declared finite bound",
                got=repr(length), bound=self.queue_bound))
        fixed = {name: first[name] for name in self.bounds
                 if name not in (self.offer, self.ready, self.reset)}
        for index, source in enumerate(rows):
            row = dict(source)
            if set(row) != set(self.bounds) or any(
                    type(value) is not int for value in row.values()):
                raise Refused(Refusal(
                    "rtl-task-request-shape",
                    "every schedule valuation has the complete exact-integer field set",
                    slot=index))
            if any(row[name] != value for name, value in fixed.items()):
                raise Refused(Refusal(
                    "rtl-task-request-queue-changed",
                    "the initial queue is one product-state component and cannot "
                    "change between schedule slots", slot=index))
            if any(row[name] not in (0, 1)
                   for name in (self.offer, self.ready, self.reset)):
                raise Refused(Refusal(
                    "rtl-task-request-control-not-bit",
                    "offer, ready and reset are one-bit environment actions",
                    slot=index))
            outside = sorted(name for name, value in row.items()
                             if not self.bounds[name][0] <= value <= self.bounds[name][1])
            if outside:
                raise Refused(Refusal(
                    "rtl-task-request-value-outside-contract",
                    "every request coordinate, including unused queue cells, "
                    "belongs to its declared finite interval",
                    slot=index, first=outside[0], count=len(outside)))
        queue = []
        for index in range(length):
            try:
                payload = tuple(
                    unpack_value(field.type, first[name])
                    for field, name in zip(
                        self.source.fields, self.cells[index]))
            except (TypeError, ValueError) as exc:
                raise Refused(Refusal(
                    "rtl-task-request-payload-pattern-invalid",
                    "the decoded queue contains a packed value outside the task "
                    "field's declaration",
                    index=index, error=str(exc))) from None
            faults = self.source.admits(payload)
            if faults:
                raise Refused(Refusal(
                    "rtl-task-request-payload-outside-contract",
                    "the decoded queue contains a payload outside the task type",
                    index=index, faults=faults))
            queue.append(payload)
        asserted = 0 if self.contract.clocks[0].reset_active_low else 1
        released = 1 - asserted
        slots = []
        for index, row in enumerate(map(dict, rows)):
            reset = row[self.reset] == asserted
            # The domain begins with exactly the run's reset prefix.  Later reset
            # sequences remain environment actions: without them the declared reset
            # restoration monitor has no scenario it can ever exercise or refute.
            expected_reset = index < self.reset_hold
            if reset != expected_reset and index <= self.reset_hold:
                raise Refused(Refusal(
                    "rtl-task-request-reset-prefix",
                    "the task domain begins with exactly its declared reset prefix "
                    "before ordinary execution; later resets are explicit schedule "
                    "actions",
                    slot=index, expected=int(expected_reset), got=int(reset)))
            if reset and row[self.offer]:
                raise Refused(Refusal(
                    "rtl-task-request-reset-not-idle",
                    "a reset slot presents no queue entry",
                    slot=index))
            controls = {self.reset: asserted if reset else released,
                        self.ready: row[self.ready]}
            slots.append(EventSlot(
                offers=(self.source.name,) if row[self.offer] else (),
                controls=controls, reset=reset))
        stimulus = Stimulus(
            queues={self.source.name: queue}, slots=slots,
            source="exact task request",
            memory_environment=self.memory_environment)
        environment = input_completion_environment(stimulus, self.contract)
        if environment.faults:
            raise Refused(Refusal(
                "rtl-task-request-environment-outside-domain",
                "the exact request violates the terminal input environment",
                first=environment.faults[0].why,
                count=len(environment.faults)))
        return stimulus

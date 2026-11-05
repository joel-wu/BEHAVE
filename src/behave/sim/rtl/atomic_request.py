"""Validated requests for the fixed global-schedule RTL product.

The codec owns queue, schedule and initial-state coordinates, not symbolic
execution or evidence. It shares the initial-register layout with the
ready/valid codec and requires the caller to choose that state explicitly.
"""

from ..core.input_queue import input_completion_environment
from ..core.physical_value import pack_value, packed_width, unpack_value
from ..core.refusal import Refusal, Refused
from ..core.stimulus import Stimulus
from .exact_product import schedule_matches_stimulus, stimulus_for_global_schedule
from .request_layout import coordinate_bounds

__all__ = ["AtomicRequestCodec"]


def _positive(value, name):
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive exact integer")
    return value


class QueueRequestCodec:
    """Shared finite queue coordinates; subclasses own physical sampling."""

    def __init__(self, contract, queue_bound, memory_environment=None,
                 initial=None):
        self.contract = contract
        self.queue_bound = _positive(queue_bound, "queue_bound")
        self.memory_environment = memory_environment
        self.initial = initial
        self.lengths = {}
        self.cells = {}
        self.offers = {}
        bounds = []
        for endpoint in contract.inputs():
            length = f"queue.{endpoint.name}.length"
            self.lengths[endpoint.name] = length
            bounds.append((length, (0, self.queue_bound)))
            rows = []
            for index in range(self.queue_bound):
                names = []
                for field in endpoint.fields:
                    name = f"queue.{endpoint.name}.{index}.{field.name}"
                    bounds.append((name, (0, (1 << packed_width(field.type)) - 1)))
                    names.append(name)
                rows.append(tuple(names))
            self.cells[endpoint.name] = tuple(rows)
            offer = f"schedule.offer.{endpoint.name}"
            self.offers[endpoint.name] = offer
            bounds.append((offer, (0, 1)))
        for control in contract.scheduled_controls():
            bounds.append((control, (0, 1)))
        if initial is not None:
            bounds.extend(initial.bounds.items())
        self.bounds = coordinate_bounds(bounds)

    def _common(self, stimulus):
        queues = dict(stimulus.queues)
        common = {}
        for endpoint in self.contract.inputs():
            queue = tuple(queues.get(endpoint.name, ()))
            common[self.lengths[endpoint.name]] = len(queue)
            for index, names in enumerate(self.cells[endpoint.name]):
                payload = queue[index] if index < len(queue) else None
                for field, name, value in zip(
                        endpoint.fields, names,
                        payload if payload is not None
                        else (0,) * len(endpoint.fields)):
                    common[name] = (
                        pack_value(field.type, value)
                        if payload is not None else 0)
        return common

    def _queues(self, first, *, prefix):
        queues = {}
        for endpoint in self.contract.inputs():
            length = first[self.lengths[endpoint.name]]
            if type(length) is not int or not 0 <= length <= self.queue_bound:
                raise Refused(Refusal(prefix + '-queue-length',
                    'a queue length is outside its finite bound',
                    endpoint=endpoint.name, got=repr(length)))
            queue = []
            for index in range(length):
                try:
                    payload = tuple(unpack_value(field.type, first[name]) for field, name in
                        zip(endpoint.fields, self.cells[endpoint.name][index], strict=True))
                except (TypeError, ValueError) as exc:
                    raise Refused(Refusal(prefix + '-payload-pattern-invalid',
                        'a queue contains a packed value outside its recursive field declaration',
                        endpoint=endpoint.name, index=index, error=str(exc))) from None
                faults = endpoint.admits(payload)
                if faults:
                    raise Refused(Refusal(prefix + '-payload-outside-contract',
                        'a queue payload is outside its declared field types',
                        endpoint=endpoint.name, index=index, faults=faults))
                queue.append(payload)
            queues[endpoint.name] = queue
        return queues


class AtomicRequestCodec(QueueRequestCodec):
    """Finite queue valuations over one already-fixed physical/action schedule."""

    def __init__(self, contract, queue_bound, schedule, memory_environment=None, initial=None):
        super().__init__(contract, queue_bound, memory_environment, initial)
        self.schedule = schedule

    def encode(self, stimulus, *, initial_state=None):
        if type(stimulus) is not Stimulus:
            raise TypeError("an atomic exact request encodes one exact Stimulus")
        if not schedule_matches_stimulus(
                self.contract, self.schedule, stimulus):
            raise Refused(Refusal(
                "rtl-atomic-request-schedule-mismatch",
                "the request does not carry the exact GlobalSchedule bound to this "
                "atomic analysis"))
        faults = stimulus.faults_against(self.contract)
        if faults:
            raise Refused(Refusal(
                "rtl-atomic-request-outside-contract",
                "the atomic exact request is not a run of the admitted task contract",
                first=faults[0], count=len(faults)))
        environment = input_completion_environment(stimulus, self.contract)
        if environment.faults:
            raise Refused(Refusal(
                "rtl-atomic-request-environment-outside-domain",
                "the atomic request violates the terminal input environment",
                first=environment.faults[0].why,
                count=len(environment.faults)))
        if any(stimulus.depth(endpoint.name) > self.queue_bound
               for endpoint in self.contract.inputs()):
            raise Refused(Refusal(
                "rtl-atomic-request-queue-too-deep",
                "one request queue exceeds the declared exact bound"))
        expected_memory = self.memory_environment
        got_memory = stimulus.memory_environment
        same_memory = (
            (expected_memory is None and got_memory is None)
            or (expected_memory is not None and got_memory is not None
                and expected_memory.digest == got_memory.digest
                and expected_memory.as_dict() == got_memory.as_dict()))
        if not same_memory:
            raise Refused(Refusal(
                "rtl-atomic-request-memory-environment-mismatch",
                "the request does not carry the external-memory initialization and "
                "ready schedule frozen into this exact product"))
        common = self._common(stimulus)
        if self.initial is not None:
            common.update(self.initial.encode(initial_state))
        rows = []
        for slot in stimulus.slots:
            row = dict(common)
            for endpoint in self.contract.inputs():
                row[self.offers[endpoint.name]] = int(endpoint.name in slot.offers)
            controls = dict(slot.controls)
            for name in self.contract.scheduled_controls():
                row[name] = controls[name]
            rows.append(row)
        return rows

    def decode(self, rows):
        rows = tuple(dict(row) for row in rows)
        if self.initial is not None:
            self.initial.decode(rows)
        if len(rows) != len(self.schedule.slots):
            raise Refused(Refusal(
                "rtl-atomic-request-horizon-mismatch",
                "an atomic exact request has one valuation per bound global slot",
                expected=len(self.schedule.slots), got=len(rows)))
        first = rows[0]
        if set(first) != set(self.bounds):
            raise Refused(Refusal(
                "rtl-atomic-request-shape",
                "an atomic exact valuation has the complete queue/action field set"))
        queue_fixed = set(self.bounds) - set(self.offers.values()) \
            - set(self.contract.scheduled_controls())
        for index, row in enumerate(rows):
            if set(row) != set(self.bounds) or any(type(value) is not int
                                                   for value in row.values()):
                raise Refused(Refusal(
                    "rtl-atomic-request-shape",
                    "every atomic exact valuation has exact integer fields",
                    slot=index))
            outside = sorted(
                name for name, value in row.items()
                if not self.bounds[name][0] <= value <= self.bounds[name][1])
            if outside:
                raise Refused(Refusal(
                    "rtl-atomic-request-value-outside-contract",
                    "an atomic exact valuation contains a value outside its finite "
                    "field interval",
                    slot=index, first=outside[0], count=len(outside)))
            if any(row[name] != first[name] for name in queue_fixed):
                raise Refused(Refusal(
                    "rtl-atomic-request-queue-changed",
                    "queue contents are one initial product component",
                    slot=index))
            scheduled = self.schedule.slots[index].event
            expected_offers = set(scheduled.offers)
            got_offers = {
                endpoint for endpoint, name in self.offers.items() if row[name]
            }
            expected_controls = dict(scheduled.controls)
            got_controls = {
                name: row[name] for name in self.contract.scheduled_controls()
            }
            if got_offers != expected_offers or got_controls != expected_controls:
                raise Refused(Refusal(
                    "rtl-atomic-request-action-schedule-mismatch",
                    "offers and controls are fixed by the authenticated schedule",
                    slot=index))

        queues = self._queues(first, prefix='rtl-atomic-request')
        stimulus = stimulus_for_global_schedule(
            self.schedule, queues,
            source="exact global-schedule request",
            memory_environment=self.memory_environment)
        faults = stimulus.faults_against(self.contract)
        if faults:
            raise Refused(Refusal(
                "rtl-atomic-request-outside-contract",
                "the decoded atomic request is outside the admitted contract",
                first=faults[0], count=len(faults)))
        environment = input_completion_environment(stimulus, self.contract)
        if environment.faults:
            raise Refused(Refusal(
                "rtl-atomic-request-environment-outside-domain",
                "the decoded atomic request violates the terminal input environment",
                first=environment.faults[0].why,
                count=len(environment.faults)))
        return stimulus

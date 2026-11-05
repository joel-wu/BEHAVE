"""Clockless queue requests: one input per explicit settled sample, no clock."""

from ..core.protocol import SETTLED
from ..core.refusal import Refusal, Refused
from ..core.stimulus import EventSlot, Stimulus
from .atomic_request import QueueRequestCodec


class SettledRequestCodec(QueueRequestCodec):
    """The finite plain-pin request domain, without fictitious edge/time data.

    This codec alone does not advertise a working exact provider. Source,
    protocol and observation products must separately support its sampling.
    """

    def __init__(self, contract, queue_bound, *, initial=None):
        if (contract.clocks or contract.memories or not contract.inputs()
                or any(e.adapter.name != SETTLED or e.adapter.version != 1
                       or e.clock is not None or e.adapter.environment_controls
                       or e.adapter.artifact_controls for e in contract.endpoints)):
            raise ValueError('a settled request needs clockless plain-pin endpoints without external memory')
        super().__init__(contract, queue_bound, initial=initial)

    def slots(self, count):
        return (EventSlot(offers=tuple(self.offers)),) * count

    def encode(self, stimulus, *, initial_state=None):
        if type(stimulus) is not Stimulus:
            raise TypeError('a settled request encodes one exact Stimulus')
        faults = stimulus.faults_against(self.contract)
        if (faults or not stimulus.slots or stimulus.clock_schedule is not None
                or stimulus.memory_environment is not None or stimulus.memory_policy
                or tuple(stimulus.slots) != self.slots(len(stimulus.slots))):
            raise Refused(Refusal('rtl-settled-request-outside-contract',
                'a settled request has only plain input samples, with no reset, clock or handshake schedule'))
        count = len(stimulus.slots)
        if count > self.queue_bound or any(stimulus.depth(e.name) != count for e in self.contract.inputs()):
            raise Refused(Refusal('rtl-settled-request-sample-count',
                'each settled step consumes exactly one value from every input queue'))
        common = self._common(stimulus)
        if self.initial is not None:
            common.update(self.initial.encode(initial_state))
        return [{**common, **dict.fromkeys(self.offers.values(), 1)} for _ in stimulus.slots]

    def decode(self, rows):
        rows = tuple(dict(row) for row in rows)
        if not rows or len(rows) > self.queue_bound:
            raise Refused(Refusal('rtl-settled-request-sample-count',
                'a settled request has a positive number of samples within the queue bound'))
        if self.initial is not None:
            self.initial.decode(rows)
        first = rows[0]
        for index, row in enumerate(rows):
            if set(row) != set(self.bounds) or any(type(v) is not int for v in row.values()):
                raise Refused(Refusal('rtl-settled-request-shape',
                    'every sample binds all queue, action and initial coordinates', slot=index))
            if any(not low <= row[name] <= high for name, (low, high) in self.bounds.items()):
                raise Refused(Refusal('rtl-settled-request-value-outside-contract',
                    'every coordinate retains its complete declared finite interval', slot=index))
            if row != first:
                raise Refused(Refusal('rtl-settled-request-queue-changed',
                    'initial queues and startup coordinates cannot change between samples', slot=index))
        if (any(first[name] != len(rows) for name in self.lengths.values())
                or any(first[name] != 1 for name in self.offers.values())):
            raise Refused(Refusal('rtl-settled-request-sample-count',
                'every input queue supplies exactly one value at every settled sample'))
        stimulus = Stimulus(queues=self._queues(first, prefix='rtl-settled-request'),
            slots=self.slots(len(rows)), source='exact settled request')
        faults = stimulus.faults_against(self.contract)
        if faults:
            raise Refused(Refusal('rtl-settled-request-outside-contract',
                'the decoded settled request violates its task contract', first=faults[0]))
        return stimulus

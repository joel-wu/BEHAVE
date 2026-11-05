"""Finite, repeatable workloads for independently sampled periodic interfaces.

No handshake or response ratio is invented. Each domain supplies one input and
one observation per declared active edge. A whole common-period window gives
compatible queue lengths even when isolated workloads are concatenated. Reset
holds are minima: the faster domains remain reset until the common body begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import lcm
from typing import TYPE_CHECKING

from .protocol import registered_adapter
from .refusal import Refusal, Refused

if TYPE_CHECKING:
    from .contract import TaskContract


@dataclass(frozen=True, slots=True)
class SampledClockWindow:
    contract: TaskContract
    per_period: dict[str, int]
    reset_edges: dict[str, int]

    @classmethod
    def for_contract(cls, contract, policy):
        if len(contract.clocks) < 2 or not contract.inputs() or not all(
                registered_adapter(endpoint.adapter.name, endpoint.adapter.version)
                .samples_every_active_edge(endpoint) for endpoint in contract.endpoints):
            return None
        timings = policy.clock_timings
        if {timing.name for timing in timings} != {clock.name for clock in contract.clocks}:
            raise ValueError("sampled runtime needs timings for every declared clock")
        period = lcm(*(timing.period for timing in timings))
        anchor = max(timing.phase + (policy.reset_hold_cycles - 1) * timing.period
                     for timing in timings)
        if not policy.reset_hold_cycles and anchor >= min(timing.phase for timing in timings):
            raise Refused(Refusal(
                "runtime-sampled-reset-prefix-needed",
                "these clock phases need a reset prefix before a repeatable sampling window"))
        return cls(contract,
                   {timing.name: period // timing.period for timing in timings},
                   {timing.name: max(0, (anchor - timing.phase) // timing.period + 1)
                    for timing in timings})

    def baseline_depths(self, maximum):
        cycles = min(maximum // self.per_period[endpoint.clock]
                     for endpoint in self.contract.inputs())
        if not cycles:
            raise Refused(Refusal(
                "runtime-sampled-window-too-short",
                "the input depth must fit at least one common clock period",
                required=max(self.per_period[endpoint.clock] for endpoint in self.contract.inputs()),
                maximum=maximum))
        return {endpoint.name: cycles * self.per_period[endpoint.clock]
                for endpoint in self.contract.inputs()}

    def outputs_for(self, request):
        cycles = set()
        for endpoint in self.contract.inputs():
            count, remainder = divmod(request.depth(endpoint.name), self.per_period[endpoint.clock])
            if remainder:
                raise Refused(Refusal(
                    "runtime-sampled-queue-window",
                    "sampled input queues must cover whole common clock periods",
                    endpoint=endpoint.name, depth=request.depth(endpoint.name)))
            cycles.add(count)
        if len(cycles) != 1:
            raise Refused(Refusal(
                "runtime-sampled-queue-window",
                "independent sampled input queues must cover the same physical time window"))
        count = cycles.pop()
        return {endpoint.name: count * self.per_period[endpoint.clock]
                for endpoint in self.contract.outputs()}

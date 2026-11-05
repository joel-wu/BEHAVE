"""Compact exact bus products using the concrete adapter's shared transitions.

The integer carrier packs control flags and a bounded wait counter. Held request
data is the unconsumed input queue head, not a representative zero: decoding a
model restores it for the complete replay-state audit. The fixed-schedule driver
is the authority for holding that queue head until logical acceptance or reset.
"""

from types import MappingProxyType

from .bus_control import apb_control, axi_lite_control
from .protocol import APB_ACCESS, ARTIFACT
from .protocol_exact import AdapterExactEncoding
from .stimulus import canonical_json


class BusExactProduct:
    VERSION = "bus-exact-product/1"

    def __init__(self, program, contract):
        program.validate(contract)
        self.program, self.contract = program, contract
        self.apb = program.name == APB_ACCESS
        self.source, self.sink = contract.inputs()[0], contract.outputs()[0]
        self.instance = program._instance(contract)
        self.pins = self.instance.bindings
        self.limit = self.instance.max_wait_cycles
        self.channels = () if self.apb else program.request_channels
        # AXI also retains a held request before either request channel accepts.
        self.flags = 1 if self.apb else len(self.channels) + 2
        self.radix = 1 << self.flags
        self.state_count = self.radix * (1 if self.limit is None else self.limit + 2)
        self.initial_state = 0
        self.presented_names = (self.source.name,)
        reset = contract.clocks[0].reset
        self.scheduled_names = tuple(n for n in contract.scheduled_controls() if n != reset)
        self.observed_widths = {n: 1 for e in contract.endpoints
                                for n in e.adapter.artifact_controls}
        if not self.apb:
            self.observed_widths[self.pins["response"]] = 2
        self.observed_names = tuple(sorted(self.observed_widths))
        self.goal_names = program.monitor_goals_owned_by(contract, ARTIFACT)
        self.digest = canonical_json({"version": self.VERSION,
            "transition": program.exact_transition_policy, "instance": self.instance.as_dict()})

    def held_requests(self, state):
        request = state["adapter.request"]
        return {} if request is None else {self.source.name: request}

    def _bits(self, state, algebra):
        return tuple(algebra.eq(algebra.mod(algebra.div(state, 1 << i), 2), 1)
                     for i in range(self.flags))

    def _valid(self, state, algebra):
        return algebra.land(algebra.lnot(algebra.gt(0, state)),
                            algebra.gt(self.state_count, state))

    def state_index(self, state):
        waits = state["adapter.wait_cycles"]
        if type(waits) is not int or not 0 <= waits < self.state_count // self.radix:
            raise ValueError("bus wait counter is outside its declared carrier")
        if self.apb:
            flags = (state["adapter.waiting"],)
        else:
            flags = (*(c in state["adapter.accepted_channels"] for c in self.channels),
                     state["adapter.pending"], state["adapter.request"] is not None)
        return waits * self.radix + sum(int(v) << i for i, v in enumerate(flags))

    def state_at(self, index, request_value=None):
        if type(index) is not int or not 0 <= index < self.state_count:
            raise ValueError("bus state is outside its declared carrier")
        flags = tuple(bool(index & (1 << i)) for i in range(self.flags))
        held = flags[0] if self.apb else flags[-1]
        request = None
        if held:
            request = request_value(self.source.name)
            if request is None:
                raise ValueError("a held bus request requires its unconsumed queue head")
        state = {"adapter.request": request, "adapter.wait_cycles": index // self.radix}
        if self.apb:
            state["adapter.waiting"] = held
        else:
            state["adapter.accepted_channels"] = tuple(sorted(
                c for c, seen in zip(self.channels, flags) if seen))
            state["adapter.pending"] = flags[-2]
        return state

    def presentation_allowed(self, state, endpoint, algebra):
        if endpoint != self.source.name:
            raise ValueError("a bus presentation names its request endpoint")
        return (algebra.bool(True) if self.apb else
                algebra.lnot(self._bits(state, algebra)[-2]))

    def symbolic_elapse(self, state, algebra):
        return self._valid(state, algebra), state

    def symbolic(self, state, reset, presented, scheduled, observed, algebra):
        a, pins = algebra, self.pins
        active = a.lnot(reset)
        offered = a.land(active, presented[self.source.name])
        bits = self._bits(state, a)
        waits = a.div(state, self.radix)
        clock = self.contract.clocks[0]
        drive = {clock.reset: a.lnot(reset) if clock.reset_active_low else reset,
                 **scheduled}
        if self.apb:
            control = apb_control(bits[0], waits, offered, observed[pins["ready"]], self.limit, a)
            drive.update({pins["select"]: offered, pins["enable"]: a.land(offered, bits[0])})
            next_bits = (control.waiting,)
            accepted = emitted = control.complete
            hits = {self.program.WAIT: control.wait_hit} if self.limit is not None else {}
            retained = bits[0]
        else:
            offered = a.land(offered, a.lnot(bits[-2]))
            control = axi_lite_control(
                bits[:len(self.channels)], bits[-2], waits, offered,
                tuple(observed[pins[c + "ready"]] for c in self.channels),
                a.land(active, observed[pins[self.program.response_channel + "valid"]]),
                scheduled[pins[self.program.response_channel + "ready"]],
                observed[pins["response"]], self.limit, a)
            drive.update({pins[c + "valid"]: a.land(offered, a.lnot(seen))
                          for c, seen in zip(self.channels, bits)})
            next_bits = (*control.seen, control.pending, control.held)
            accepted, emitted = control.assembled, control.emitted
            hits = {self.program.EARLY: control.early_hit, self.program.CODE: control.code_hit}
            if self.limit is not None:
                hits[self.program.WAIT] = control.wait_hit
            retained = bits[-1]
        packed = control.waits * self.radix
        for i, bit in enumerate(next_bits):
            packed = a.add(packed, a.ite(bit, a.integer(1 << i), a.integer(0)))
        return AdapterExactEncoding(
            # The queue driver cannot withdraw a partially transferred request.
            valid=a.land(self._valid(state, a), a.lor(reset, a.lnot(retained), offered)),
            state=a.ite(reset, a.integer(0), packed),
            drive=MappingProxyType(drive),
            accepted=MappingProxyType({self.source.name: a.land(active, accepted)}),
            emitted=MappingProxyType({self.sink.name: a.land(active, emitted)}),
            hits=MappingProxyType({g: a.land(active, hit) for g, hit in hits.items()}))

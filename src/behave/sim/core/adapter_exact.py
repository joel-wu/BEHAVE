"""Exact adapter construction, including independent same-edge interfaces.

Composition packs component indices with mixed radices; it never enumerates the
Cartesian product of channel states or serializes coincident interface actions.
"""

from types import MappingProxyType

from .bus_exact import BusExactProduct
from .interface_product import InterfaceProduct

# The registry owns adapter initialization; do not re-enter it through an
# individual implementation before that implementation's class is defined.
from .protocol import APBAccessV1, AXILiteReadV1, AXILiteWriteV1
from .protocol_exact import AdapterExactEncoding, AdapterExactTable
from .stimulus import canonical_json


def adapter_exact_product(program, contract):
    if isinstance(program, InterfaceProduct):
        return InterfaceExactProduct(program, contract)
    if isinstance(program, (APBAccessV1, AXILiteReadV1, AXILiteWriteV1)):
        return BusExactProduct(program, contract)
    return AdapterExactTable(program, contract)


class InterfaceExactProduct:
    VERSION = "interface-exact-product/1"

    def __init__(self, program, contract):
        self.program, self.contract = program, contract
        self.parts = []
        radix = 1
        self.initial_state = 0
        for name, view, child in program.parts:
            product = adapter_exact_product(child, view)
            self.parts.append((name, view, child, product, radix))
            self.initial_state += radix * product.initial_state
            radix *= product.state_count
        self.state_count = radix
        self.presented_names = tuple(e.name for e in contract.inputs())
        self.scheduled_names = tuple(sorted({n for *_, p, _ in self.parts for n in p.scheduled_names}))
        self.observed_widths = {n: w for *_, p, _ in self.parts for n, w in p.observed_widths.items()}
        self.observed_names = tuple(sorted(self.observed_widths))
        self.goal_names = tuple(sorted({g for *_, p, _ in self.parts for g in p.goal_names}))
        self.digest = canonical_json({"version": self.VERSION,
            "components": {name: p.digest for name, _, _, p, _ in self.parts}})

    def held_requests(self, state):
        return {endpoint: value for name, view, child, p, _ in self.parts
                for endpoint, value in p.held_requests(self.program._state(name, view, child, state)).items()}

    def state_index(self, state):
        return sum(radix * p.state_index(self.program._state(name, view, child, state))
                   for name, view, child, p, radix in self.parts)

    def state_at(self, index, request_value=None):
        if type(index) is not int or not 0 <= index < self.state_count:
            raise ValueError("interface product index is outside its carrier")
        return {self.program._key(name, key): value
                for name, _, _, p, radix in self.parts
                for key, value in p.state_at((index // radix) % p.state_count, request_value).items()}

    @staticmethod
    def _index(state, product, radix, algebra):
        return algebra.mod(algebra.div(state, radix), product.state_count)

    def _valid(self, state, a):
        return a.land(a.lnot(a.gt(0, state)), a.gt(self.state_count, state))

    def presentation_allowed(self, state, endpoint, algebra):
        for _, _, _, p, radix in self.parts:
            if endpoint in p.presented_names:
                return p.presentation_allowed(self._index(state, p, radix, algebra), endpoint, algebra)
        raise ValueError("interface presentation names a declared input")

    def symbolic_elapse(self, state, algebra):
        a = algebra
        valid, successor = [self._valid(state, a)], a.integer(0)
        for _, _, _, p, radix in self.parts:
            guard, stepped = p.symbolic_elapse(self._index(state, p, radix, a), a)
            valid.append(guard)
            successor = a.add(successor, stepped * radix)
        return a.land(*valid), successor

    def symbolic(self, state, reset, presented, scheduled, observed, algebra):
        a = algebra
        valid, successor = [self._valid(state, a)], a.integer(0)
        drive, accepted, emitted, hits = {}, {}, {}, {}
        for _, _, _, p, radix in self.parts:
            encoded = p.symbolic(
                self._index(state, p, radix, a), reset,
                {n: presented[n] for n in p.presented_names},
                {n: scheduled[n] for n in p.scheduled_names},
                {n: observed[n] for n in p.observed_names}, a)
            valid.append(encoded.valid)
            successor = a.add(successor, encoded.state * radix)
            for n, value in encoded.drive.items():
                if n in drive:  # Only the shared domain reset may overlap.
                    valid.append(a.eq(drive[n], value))
                drive[n] = value
            accepted.update(encoded.accepted)
            emitted.update(encoded.emitted)
            for g, value in encoded.hits.items():
                hits[g] = a.lor(hits.get(g, a.bool(False)), value)
        return AdapterExactEncoding(a.land(*valid), successor, *(
            MappingProxyType(values) for values in (drive, accepted, emitted, hits)))

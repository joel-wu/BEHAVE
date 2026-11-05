"""A native witness before HDL startup, distinct from a settled-state deposit."""

from ..core.frozen import Frozen
from .initial_state import RTLInitialState

VERSION = "rtl-startup-state/2"


class RTLStartupState(Frozen):
    """Complete observed registers and explicit environment pins before first eval.

    HDL initialization is still executed. The exact domain owns restrictions on
    declaration initializers; the native runner does not suppress their writes.
    Pin fields use the same packed-value carrier, but are never register targets.
    After HDL construction, the ordinary replay environment parks clocks and
    releases reset. This choice does not suppress that startup drive or its events.
    """

    __slots__ = ("pins", "registers")

    def __init__(self, registers, pins):
        if type(registers) is not RTLInitialState:
            raise TypeError("startup needs an exact register choice")
        self.registers = registers
        self.pins = RTLInitialState(registers.artifact, pins)
        if set(registers.values()) & set(self.pins.values()):
            raise ValueError("startup registers and environment pins are disjoint")
        self._freeze()

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RTLStartupState is a closed replay record")

    def as_dict(self):
        return {"version": VERSION, "registers": self.registers.as_dict(),
                "pins": [list(row) for row in self.pins.fields]}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"version", "registers", "pins"} \
                or image["version"] != VERSION:
            raise ValueError("startup needs its complete versioned shape")
        return cls(RTLInitialState.from_dict(image["registers"]), image["pins"])

    def check_binding(self, artifact, names, widths, contract):
        self.registers.check_binding(artifact, names, widths)
        pins = startup_pin_widths(contract)
        self.pins.check_binding(artifact, pins, pins)

    def check_trace(self, trace):
        from .initial_state import check_initialization

        check_initialization(self, trace)
        # HDL startup may overwrite registers. Its observed successor belongs
        # to the source formula audit, not equality with pre-evaluation storage.

    def __reduce__(self):
        return type(self), (self.registers, self.pins.fields)


def startup_pin_widths(contract):
    """The same complete environment-pin layout for capture and selected replay."""
    from ..core.memory import memory_artifact_signal_set, memory_port_signal_set
    from ..core.physical_value import packed_width

    pins = {field.port: packed_width(field.type)
            for endpoint in contract.inputs() for field in endpoint.fields}
    pins.update((pin, 1) for pin in contract.environment_controls())
    pins.update((pin, 1) for clock in contract.clocks for pin in (clock.clock, clock.reset))
    for memory in contract.memories:
        inputs = set().union(*(memory_port_signal_set(memory.name, port.id, port.kind)
                              for port in memory.ports)) - memory_artifact_signal_set(memory)
        pins.update((pin, memory.data_width if pin.endswith('_rdata') else 1) for pin in inputs)
    return pins

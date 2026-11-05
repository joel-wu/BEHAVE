"""Initialization coordinates shared by RTL exact request codecs.

The choice is constant across the rows of one request, separate from its queue and
schedule. These coordinates enforce layout, not membership in the HDL initial
relation; the complete symbolic product supplies that constraint. An explicit
startup pin layout selects pre-HDL construction instead of a settled deposit.
Both register and pin choices stay fixed across the entire request.
"""

from types import MappingProxyType

from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused
from .initial_state import RTLInitialState


class InitialStateCoordinates(Frozen):
    __slots__ = ("artifact", "bounds", "coordinates", "widths", "pin_coordinates", "startup_pins")

    def __init__(self, artifact, widths, *, startup_pins=None):
        widths = dict(widths)
        # One record constructor owns exact names, widths and artifact validation.
        shape = RTLInitialState.from_values(
            artifact, widths, dict.fromkeys(widths, 0))
        self.artifact = artifact
        self.widths = MappingProxyType(widths)
        self.coordinates = tuple(
            (f"initial.rtl.{name}", name, width)
            for name, width, _ in shape.fields)
        self.startup_pins = None
        self.pin_coordinates = ()
        if startup_pins is not None:
            from .startup_state import RTLStartupState

            pins = dict(startup_pins)
            pin_shape = RTLInitialState.from_values(artifact, pins, dict.fromkeys(pins, 0))
            RTLStartupState(shape, pin_shape.fields)  # Reject overlapping physical roles.
            self.startup_pins = MappingProxyType(pins)
            self.pin_coordinates = tuple(
                (f"initial.pin.{name}", name, width) for name, width, _ in pin_shape.fields)
        self.bounds = MappingProxyType({
            coordinate: (0, (1 << width) - 1)
            for coordinate, _name, width in (*self.coordinates, *self.pin_coordinates)})
        self._freeze()

    def choice(self, values):
        if self.startup_pins is not None:
            from .startup_state import RTLStartupState

            if type(values) is not RTLStartupState:
                raise TypeError("construction coordinates need a complete RTLStartupState")
            values.registers.check_binding(self.artifact, self.widths, self.widths)
            values.pins.check_binding(self.artifact, self.startup_pins, self.startup_pins)
            return values
        if values is None and not self.widths:
            values = {}
        return RTLInitialState.from_values(self.artifact, dict(self.widths), values)

    def encode(self, values):
        choice = self.choice(values)
        values = choice.values() if self.startup_pins is None else choice.registers.values()
        out = {coordinate: values[name] for coordinate, name, _width in self.coordinates}
        if self.startup_pins is not None:
            pins = choice.pins.values()
            out.update((coordinate, pins[name]) for coordinate, name, _width in self.pin_coordinates)
        return out

    def decode(self, rows):
        if not rows:
            raise Refused(Refusal(
                "rtl-initial-state-request-empty",
                "an exact initial choice accompanies a nonempty request"))
        expected = set(self.bounds)
        if not expected <= rows[0].keys():
            raise Refused(Refusal(
                "rtl-initial-state-request-incomplete",
                "the request must carry every declared initialization coordinate"))
        first = {coordinate: rows[0][coordinate] for coordinate in expected}
        try:
            registers = RTLInitialState.from_values(self.artifact, dict(self.widths),
                {name: first[coordinate] for coordinate, name, _width in self.coordinates})
            if self.startup_pins is None:
                choice = registers
            else:
                from .startup_state import RTLStartupState

                choice = RTLStartupState(registers,
                    [(name, width, first[coordinate]) for coordinate, name, width in self.pin_coordinates])
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "rtl-initial-state-request-invalid", str(exc))) from exc
        for row in rows:
            if not expected <= row.keys() or any(
                    type(row[coordinate]) is not int
                    or row[coordinate] != first[coordinate]
                    for coordinate in expected):
                raise Refused(Refusal(
                    "rtl-initial-state-request-changed",
                    "the initialization choice cannot change between request rows"))
        return choice

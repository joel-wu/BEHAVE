"""Simulator startup before the first scheduled event, shared by both RTL drivers."""


def decode_input_aliases(rows):
    """Closed compiler-derived input mirror layout; None means unavailable."""
    if rows is None:
        return None
    if not isinstance(rows, (tuple, list)):
        raise ValueError('startup input aliases need a sequence')
    result = []
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 4:
            raise ValueError('startup input alias has an invalid shape')
        path, width, pin, low = row
        if (not isinstance(path, (tuple, list)) or not path or type(path[0]) is not str
                or any(not (type(part) is str and part or type(part) is int) for part in path)
                or type(width) is not int or width <= 0 or type(pin) is not str or not pin
                or type(low) is not int or low < 0):
            raise ValueError('startup input alias has invalid physical coordinates')
        result.append((tuple(path), width, pin, low))
    if len({row[0] for row in result}) != len(result):
        raise ValueError('startup input aliases must not repeat a physical destination')
    return tuple(result)


async def settle_initial_state(dut, clocks, *, drive_clocks=True):
    """Settle startup with clocks inactive and reset released before any slot.

    The backend may evaluate HDL startup before cocotb's inertial pin writes.
    Those evaluations and the resulting startup edges stay in the separate native
    startup interval. Return in a writable phase so the caller can capture state
    and drive the first scheduled slot. No design register is assigned here.
    """
    from cocotb.triggers import ReadOnly, Timer

    for clock in clocks if drive_clocks else ():
        dut[clock.clock].value = int(clock.edge == "falling")
        dut[clock.reset].value = int(clock.reset_active_low)
    await Timer(1, unit="ps")
    await ReadOnly()
    await Timer(1, unit="ps")


def _physical_choice(choice, handles, paths):
    from ..core.refusal import Refusal, Refused
    from .initial_state import RTLInitialState
    from .source_handles import physical_name

    if type(choice) is not RTLInitialState:
        raise TypeError("source initialization requires an exact RTLInitialState")
    if type(handles) is not dict or set(handles) != set(choice.values()):
        raise Refused(Refusal(
            "rtl-initial-state-layout-mismatch",
            "source initialization requires every selected register handle"))
    for name, width, _value in choice.fields:
        handle = handles[name]
        try:
            actual = len(handle) if handle is not None else None
        except (TypeError, ValueError):
            actual = None
        if actual != width:
            raise Refused(Refusal(
                "rtl-initial-state-field-unavailable",
                "the simulator cannot write the declared register layout",
                field=name, expected=width, actual=actual))
    physical = choice
    if paths is not None:
        # Deposit the very same source words that observation reads. The public
        # choice and subsequent evidence keep their structural field identity.
        physical = RTLInitialState(choice.artifact, tuple(sorted(
            (physical_name(path), width, value)
            for name, width, value in choice.fields for path in (paths[name],))))
    return physical


def _input_alias_choice(choice, input_aliases):
    from ..core.refusal import Refusal, Refused
    from .initial_state import RTLInitialState
    from .source_handles import physical_name

    aliases = decode_input_aliases(input_aliases)
    if aliases is None:
        raise Refused(Refusal('rtl-startup-input-alias-unavailable',
            'HDL construction needs the compiler-owned input mirror layout'))
    pins = {name: (width, value) for name, width, value in choice.pins.fields}
    fields = []
    for path, width, pin, low in aliases:
        if pin not in pins or low + width > pins[pin][0]:
            raise Refused(Refusal('rtl-startup-input-alias-invalid',
                'an input mirror must be a declared slice of one complete startup pin', pin=pin))
        fields.append((physical_name(path), width, (pins[pin][1] >> low) & ((1 << width)-1)))
    return RTLInitialState(choice.registers.artifact, tuple(sorted(fields)))


def capture_startup_state(layout, dut, handles, *, paths=None, input_aliases=(), observations=False):
    """Read construction storage synchronously, without deposits or pin writes.

    The compiler-owned layout supplies names and widths, never values. Native
    inputs, public pins and collapsed input aliases must already agree; this
    function cannot normalize them to make an exact request representable.
    """
    from cocotb.utils import get_sim_time

    from ..core.refusal import Refusal, Refused
    from .initial_request import InitialStateCoordinates
    from .initial_state import RTLInitialState
    from .register_init import read_registers
    from .source_handles import exact_handle, physical_name, read_handle, width_of
    from .startup_state import RTLStartupState

    if type(layout) is not InitialStateCoordinates or layout.startup_pins is None:
        raise TypeError('startup capture requires the complete construction layout')
    if get_sim_time(unit='step') != 0:
        raise Refused(Refusal('rtl-startup-state-late',
            'construction must be recorded before simulation advances'))
    shape = RTLInitialState.from_values(layout.artifact, dict(layout.widths),
                                       dict.fromkeys(layout.widths, 0))
    physical = _physical_choice(shape, handles, paths)
    values = read_registers(dut._name, physical, **({'observations': True} if observations else {}))
    registers = RTLInitialState.from_values(layout.artifact, dict(layout.widths),
        {name: values[name if paths is None else physical_name(paths[name])]
         for name in layout.widths})
    pins = RTLInitialState.from_values(layout.artifact, dict(layout.startup_pins),
                                      dict.fromkeys(layout.startup_pins, 0))
    pins = RTLInitialState.from_values(layout.artifact, dict(layout.startup_pins),
                                      read_registers(dut._name, pins, inputs=True))
    observed = {}
    for name, width, _ in pins.fields:
        handle = exact_handle(dut, name)
        if width_of(handle) != width:
            raise Refused(Refusal('rtl-startup-pin-unavailable',
                'the simulator cannot read the declared environment pin', pin=name))
        observed[name] = read_handle(handle)
    pins.check_observed(observed)
    captured = RTLStartupState(registers, pins.fields)
    aliases = _input_alias_choice(captured, input_aliases)
    aliases.check_observed(read_registers(dut._name, aliases, aliases=True))
    return captured


async def install_initial_state(choice, handles, *, top, paths=None):
    """Deposit after startup; verify the settled boundary without forcing it."""
    from cocotb.triggers import ReadOnly, Timer

    from .register_init import deposit_registers

    physical = _physical_choice(choice, handles, paths)
    deposit_registers(top, physical)
    await Timer(1, unit="ps")
    await ReadOnly()
    choice.check_observed({name: int(handle.value)
                           for name, handle in handles.items()})
    await Timer(1, unit="ps")


def install_startup_state(choice, dut, handles, *, paths=None, input_aliases=()):
    """Synchronous first-callback deposit. No HDL evaluation or yield occurs here.

    Read back source storage before startup, not after HDL initial blocks have
    changed it. All pins are explicit, including clock/reset initial levels.
    """
    from cocotb.handle import Immediate
    from cocotb.utils import get_sim_time

    from ..core.refusal import Refusal, Refused
    from .register_init import deposit_registers, read_registers
    from .source_handles import exact_handle, read_handle, width_of

    if get_sim_time(unit="step") != 0:
        raise Refused(Refusal("rtl-startup-state-late",
            "a construction witness must be installed before simulation advances"))
    physical = _physical_choice(choice.registers, handles, paths)
    alias_choice = _input_alias_choice(choice, input_aliases)
    pins = {name: exact_handle(dut, name) for name, _, _ in choice.pins.fields}
    for name, width, _value in choice.pins.fields:
        if width_of(pins[name]) != width:
            raise Refused(Refusal("rtl-startup-pin-unavailable",
                "the simulator cannot initialize the declared environment pin", pin=name))
    deposit_registers(dut._name, physical)
    physical.check_observed(read_registers(dut._name, physical))
    for name, _width, value in choice.pins.fields:
        pins[name].value = Immediate(value)
    # Verilator may copy public pin mirrors to the source ports only after its
    # initial evaluation. Initialize that registered input storage as well, or
    # initial blocks would read zero despite successful VPI readback above.
    deposit_registers(dut._name, choice.pins, inputs=True)
    choice.pins.check_observed(read_registers(dut._name, choice.pins, inputs=True))
    deposit_registers(dut._name, alias_choice, aliases=True)
    alias_choice.check_observed(read_registers(dut._name, alias_choice, aliases=True))
    choice.pins.check_observed({name: read_handle(handle) for name, handle in pins.items()})

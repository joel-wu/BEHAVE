"""Analysis-only source register writes through the compiled Verilator adapter.

The adapter resolves registered storage, never generated C++ offsets or VPI output
mirrors. Complete preflight precedes the first write; the caller independently
reads the ordinary observation handles after the simulator settles.
"""

import ctypes

from ..core.refusal import Refusal, Refused


def deposit_registers(top, choice, *, inputs=False, aliases=False):
    from .initial_state import RTLInitialState

    if type(choice) is not RTLInitialState:
        raise TypeError("register initialization needs an exact RTLInitialState")
    if type(top) is not str or not top or "\0" in top:
        raise TypeError("source initialization names one exact simulator top")
    if any(width > 2**31 - 1 or "\0" in name
           for name, width, _value in choice.fields):
        raise ValueError("source register names/widths must fit the simulator ABI")
    if inputs and aliases:
        raise ValueError('construction storage has one physical role')
    try:
        operation = getattr(ctypes.CDLL(None),
                            'behave_initial_alias_v1' if aliases else
                            "behave_initial_input_v1" if inputs else "behave_initial_state_v1")
    except (OSError, AttributeError) as exc:
        raise Refused(Refusal(
            "rtl-initial-state-adapter-unavailable",
            "this simulator has no source-register initialization adapter")) from exc
    operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_char_p]
    operation.restype = ctypes.c_int
    root = top.encode("utf-8")
    fields = [(name.encode("utf-8"), width,
               format(value, f"0{width}b").encode("ascii"))
              for name, width, value in choice.fields]
    for writing in (False, True):
        for name, width, bits in fields:
            code = operation(root, name, width, bits if writing else None)
            if code:
                raise Refused(Refusal(
                    "rtl-initial-state-register-unavailable",
                    "the simulator cannot initialize the exact source storage",
                    field=name.decode("utf-8"), width=width,
                    adapter_code=code, phase="write" if writing else "preflight"))


def read_registers(top, choice, *, inputs=False, aliases=False, observations=False):
    """Read the exact registered words, including before their first HDL update."""
    if sum((inputs, aliases, observations)) > 1:
        raise ValueError('construction storage has one physical role')
    try:
        operation = getattr(ctypes.CDLL(None),
                            'behave_observation_read_v1' if observations else
                            'behave_initial_alias_read_v1' if aliases else
                            "behave_initial_input_read_v1" if inputs else "behave_initial_state_read_v1")
    except (OSError, AttributeError) as exc:
        raise Refused(Refusal("rtl-initial-state-adapter-unavailable",
            "this simulator has no source-register readback adapter")) from exc
    operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    operation.restype = ctypes.c_int
    values = {}
    for name, width, _value in choice.fields:
        bits = ctypes.create_string_buffer(width + 1)
        code = operation(top.encode("utf-8"), name.encode("utf-8"), width, bits)
        if code:
            raise Refused(Refusal("rtl-initial-state-register-unavailable",
                "the simulator cannot read the exact source storage",
                field=name, width=width, adapter_code=code, phase="readback"))
        values[name] = int(bits.value, 2)
    return values

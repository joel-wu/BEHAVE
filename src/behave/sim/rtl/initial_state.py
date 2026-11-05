"""Complete analysis-only source-register choices before the first scheduled event.

The exact product checks initial-support membership. Replay checks the artifact,
field layout and actual readback. Neither check alone establishes bounded evidence;
the choice is separate from the stimulus and the observed trace.
"""

from __future__ import annotations

from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused

INITIAL_STATE_VERSION = "rtl-initial-state/1"


def decode_initialization(image):
    """The closed worker choice: settled deposit or pre-HDL construction."""
    from .startup_state import RTLStartupState, VERSION

    if type(image) is dict and image.get("version") == VERSION:
        return RTLStartupState.from_dict(image)
    return RTLInitialState.from_dict(image)


def check_initialization(choice, trace):
    from ..core.record_value import thaw_record_value

    try:
        observed = decode_initialization(thaw_record_value(trace.initialization))
    except (TypeError, ValueError):
        observed = None
    if observed is None or observed.as_dict() != choice.as_dict():
        raise Refused(Refusal("rtl-initialization-receipt-mismatch",
            "source replay did not retain the requested initialization phase and values"))


class RTLInitialState(Frozen):
    """Immutable, artifact-bound packed register values (name, width, value)."""

    __slots__ = ("artifact", "fields")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RTLInitialState is a closed replay record")

    def __init__(self, artifact, fields):
        if type(artifact) is not str or len(artifact) != 64 \
                or any(char not in "0123456789abcdef" for char in artifact):
            raise ValueError("an initial-state choice binds a SHA-256 artifact")
        if type(fields) not in (tuple, list):
            raise TypeError("initial-state fields are a finite ordered sequence")
        rows = []
        for row in fields:
            if type(row) not in (tuple, list) or len(row) != 3:
                raise TypeError("an initial-state field is (name, width, value)")
            name, width, value = row
            if type(name) is not str or not name:
                raise TypeError("an initial-state field has a nonempty exact name")
            if type(width) is not int or width < 1:
                raise ValueError("an initial-state field has a positive exact width")
            if type(value) is not int or not 0 <= value < (1 << width):
                raise ValueError("an initial-state value is an in-width bit pattern")
            rows.append((name, width, value))
        names = tuple(name for name, _width, _value in rows)
        if names != tuple(sorted(set(names))):
            raise ValueError("initial-state fields are sorted and unique")
        self.artifact = artifact
        self.fields = tuple(rows)
        self._freeze()

    @classmethod
    def from_values(cls, artifact, widths, values):
        if type(widths) is not dict or type(values) is not dict \
                or set(widths) != set(values):
            raise ValueError("an initial-state choice includes every declared field")
        return cls(artifact, [(name, widths[name], values[name])
                              for name in sorted(widths)])

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {
                "version", "artifact", "fields"} \
                or image["version"] != INITIAL_STATE_VERSION \
                or type(image["fields"]) is not list:
            raise ValueError("an initial-state image has its complete versioned shape")
        if any(type(row) is not list for row in image["fields"]):
            raise TypeError("initial-state wire fields are exact lists")
        return cls(image["artifact"], image["fields"])

    def as_dict(self):
        return {"version": INITIAL_STATE_VERSION, "artifact": self.artifact,
                "fields": [list(row) for row in self.fields]}

    def values(self):
        return {name: value for name, _width, value in self.fields}

    def check_binding(self, artifact, names, widths):
        if self.artifact != artifact:
            raise Refused(Refusal(
                "rtl-initial-state-artifact-mismatch",
                "the selected initial state belongs to another RTL artifact"))
        layout = {name: width for name, width, _value in self.fields}
        if set(layout) != set(names) or layout != widths:
            raise Refused(Refusal(
                "rtl-initial-state-layout-mismatch",
                "the initial choice must cover the complete observed register layout"))

    def check_observed(self, values):
        if type(values) is not dict or set(values) != set(self.values()) \
                or any(type(value) is not int for value in values.values()) \
                or values != self.values():
            raise Refused(Refusal(
                "rtl-initial-state-readback-mismatch",
                "the simulator did not start in the complete selected initial state"))

    def check_trace(self, trace):
        check_initialization(self, trace)
        self.check_observed(dict(trace.initial_state))

    def __reduce__(self):
        return type(self), (self.artifact, self.fields)

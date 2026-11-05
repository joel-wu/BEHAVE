"""Parse and evaluate the finite RTLIL ``SigSpec`` switch language.

Yosys process switches may select a wire, a constant slice, or a concatenation of
several such values.  Structural goals use the same parsed record for solver terms and
simulator samples; keeping both renderings here prevents their bit order from drifting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from behave.core import runtime_defaults as defaults

__all__ = ["rtlil_name", "signal_term", "signal_value", "symbolic_name", "wire_names"]


_NAME = re.compile(
    r"(?:\\[^\s]+|\$[^\s]+|[A-Za-z_][A-Za-z0-9_$.:/<>-]*)")
_CONSTANT = re.compile(r"(?P<width>[0-9]+)'(?P<bits>[01xzam?-]+)")
_INTEGER = re.compile(r"-?[0-9]+")
_SELECTION = re.compile(
    r"(?P<first>[0-9]+)(?:\s*(?P<kind>:|\+:|-:)\s*(?P<second>[0-9]+))?")


@dataclass(frozen=True)
class _Wire:
    name: str
    high: int | None = None
    low: int | None = None


@dataclass(frozen=True)
class _Constant:
    width: int
    bits: str


@dataclass(frozen=True)
class _Integer:
    """An unsized integer constant emitted by pre-proc RTLIL.

    Yosys retains an integer loop/function argument as e.g. ``switch 0``.
    Such a value has no bit-vector width in the text.  Keeping it distinct from
    a sized bit string lets equality remain exact while refusing to invent a
    width if it ever occurs inside a concatenation.
    """

    value: int


@dataclass(frozen=True)
class _Concat:
    parts: tuple


class _Parser:
    def __init__(self, text):
        self.text = text
        self.position = 0

    def space(self):
        while (self.position < len(self.text)
               and self.text[self.position].isspace()):
            self.position += 1

    def atom(self):
        self.space()
        if self.position >= len(self.text):
            raise ValueError("an RTLIL switched expression ends before its value")
        if self.text[self.position] == "{":
            self.position += 1
            parts = []
            while True:
                self.space()
                if self.position >= len(self.text):
                    raise ValueError("an RTLIL concatenation has no closing brace")
                if self.text[self.position] == "}":
                    self.position += 1
                    if not parts:
                        raise ValueError("an RTLIL concatenation is empty")
                    return _Concat(tuple(parts))
                parts.append(self.atom())

        start = self.position
        if self.text[start] in "\\$":
            while (self.position < len(self.text)
                   and not self.text[self.position].isspace()):
                self.position += 1
        else:
            while (self.position < len(self.text)
                   and not self.text[self.position].isspace()
                   and self.text[self.position] not in "{}[]"):
                self.position += 1
        token = self.text[start:self.position]
        constant = _CONSTANT.fullmatch(token)
        if constant is not None:
            width = int(constant.group("width"))
            bits = constant.group("bits")
            if width < 1 or len(bits) != width:
                raise ValueError(f"malformed RTLIL constant {token!r}")
            return _Constant(width, bits)
        if _INTEGER.fullmatch(token) is not None:
            return _Integer(int(token))
        if _NAME.fullmatch(token) is None:
            raise ValueError(f"unsupported RTLIL SigSpec token {token!r}")

        self.space()
        if self.position >= len(self.text) or self.text[self.position] != "[":
            return _Wire(token)
        end = self.text.find("]", self.position + 1)
        if end < 0:
            raise ValueError("an RTLIL wire selection has no closing bracket")
        body = self.text[self.position + 1:end].strip()
        self.position = end + 1
        selected = _SELECTION.fullmatch(body)
        if selected is None:
            raise ValueError(f"unsupported RTLIL wire selection [{body}]")
        first = int(selected.group("first"))
        kind = selected.group("kind")
        second = selected.group("second")
        if kind is None:
            high = low = first
        elif kind == ":":
            high, low = first, int(second)
        elif kind == "+:":
            low = first
            high = first + int(second) - 1
        else:
            high = first
            low = first - int(second) + 1
        if high < low or low < 0:
            raise ValueError(f"invalid RTLIL wire selection [{body}]")
        return _Wire(token, high, low)


@lru_cache(maxsize=defaults.SIM_RTL_SIGSPEC_CACHE_ENTRIES)
def _parse(signal):
    if type(signal) is not str:
        raise TypeError("an RTLIL switched expression is exact text")
    parser = _Parser(signal.strip())
    spec = parser.atom()
    parser.space()
    if parser.position != len(parser.text):
        raise ValueError(
            f"unsupported trailing RTLIL SigSpec text {parser.text[parser.position:]!r}")
    return spec


def rtlil_name(name):
    """Remove RTLIL's leading escape, retaining the source/trace spelling."""

    return name.removeprefix("\\")


def symbolic_name(name):
    """Encode an admitted RTLIL wire name as the locked SMT2 writer does.

    Backslashes inside an identifier are not legal in SMT-LIB quoted symbols;
    Yosys emits them as slashes. This is a one-way encoding, not an inverse
    guess from a solver name to a source signal. Concrete readers keep using
    ``rtlil_name``. Script-unsafe names are refused by the keep-wire boundary.
    """
    return rtlil_name(name).replace("\\", "/")


def _wires(spec):
    if type(spec) is _Wire:
        yield spec
    elif type(spec) is _Concat:
        for part in spec.parts:
            yield from _wires(part)


def wire_names(signal):
    """Return every wire referenced by ``signal`` in RTLIL spelling."""

    return tuple(wire.name for wire in _wires(_parse(signal)))


def _concrete(spec, values, widths):
    if type(spec) is _Integer:
        return spec.value, None, None
    if type(spec) is _Constant:
        if any(bit not in "01" for bit in spec.bits):
            raise ValueError(
                f"RTLIL constant {spec.width}'{spec.bits} is not two-state")
        return int(spec.bits, 2), spec.width, None
    if type(spec) is _Wire:
        public = rtlil_name(spec.name)
        if public not in values:
            return None, None, public
        value = values[public]
        width = widths.get(public)
        if spec.high is None:
            return value, width, None
        if width is not None and spec.high >= width:
            raise ValueError(
                f"RTLIL selection of bit {spec.high} exceeds {public!r}'s width {width}")
        if isinstance(value, int):
            value = ((value >> spec.low)
                     & ((1 << (spec.high - spec.low + 1)) - 1))
        return value, spec.high - spec.low + 1, None

    value = 0
    total = 0
    for part in spec.parts:
        piece, width, missing = _concrete(part, values, widths)
        if missing is not None:
            return None, None, missing
        if width is None:
            names = [rtlil_name(wire.name) for wire in _wires(part)]
            name = names[0] if names else str(part)
            raise ValueError(
                f"this reading does not carry the width of {name!r}")
        if not isinstance(piece, int):
            return piece, None, None
        value = (value << width) | (piece & ((1 << width) - 1))
        total += width
    return value, total, None


def signal_value(signal, values, widths=None):
    """Evaluate ``signal`` against one simulator reading.

    Returns ``(value, missing_wire)``.  A malformed expression or a concatenation whose
    wire widths were not sampled raises ``ValueError`` so the site becomes undecidable.
    """

    value, _width, missing = _concrete(
        _parse(signal), values, {} if widths is None else widths)
    return value, missing


def _bitvector(term):
    import z3

    if z3.is_bool(term):
        return z3.If(term, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
    return term


def _symbolic(explorer, spec):
    import z3

    if type(spec) is _Integer:
        return z3.IntVal(spec.value), None, None
    if type(spec) is _Constant:
        if any(bit not in "01" for bit in spec.bits):
            raise ValueError(
                f"RTLIL constant {spec.width}'{spec.bits} is not two-state")
        return z3.BitVecVal(int(spec.bits, 2), spec.width), spec.width, None
    if type(spec) is _Wire:
        public = symbolic_name(spec.name)
        var, width = explorer.value_var(public)
        if var is None:
            return None, None, public
        if spec.high is None:
            return var, width, None
        if spec.high >= width:
            raise ValueError(
                f"RTLIL selection of bit {spec.high} exceeds {public!r}'s width {width}")
        if z3.is_bool(var):
            if spec.high != 0 or spec.low != 0:
                raise ValueError(
                    f"RTLIL selection [{spec.high}:{spec.low}] exceeds Boolean "
                    f"wire {public!r}")
            return var, 1, None
        return z3.Extract(spec.high, spec.low, var), \
            spec.high - spec.low + 1, None

    terms = []
    total = 0
    for part in spec.parts:
        term, width, missing = _symbolic(explorer, part)
        if missing is not None:
            return None, None, missing
        if width is None:
            raise ValueError(
                "an unsized RTLIL integer cannot be used in a concatenation")
        terms.append(_bitvector(term))
        total += width
    return (z3.Concat(*terms) if len(terms) > 1 else terms[0]), total, None


def signal_term(explorer, signal):
    """Return ``(term, width, missing_wire)`` for the solver-side reading."""

    return _symbolic(explorer, _parse(signal))

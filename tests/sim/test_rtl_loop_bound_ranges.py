"""Universal header bounds contain every finite scalar result, including casts."""

import operator

import pytest

from behave.sim.rtl.source_loop_constants import _integral_interval

# NamedValue is compiler-bound even when its value is unconstrained. The type
# stays on each expression; the symbol distinguishes variables from constants.
_SYMBOLS = {name: {"kind": "Variable", "name": name} for name in ("a", "b")}


def _type(width, signed=False):
    return {"kind": "PredefinedIntegerType", "bitWidth": width, "isSigned": signed}


def _wrap(value, width, signed):
    bits = value % (1 << width)
    return bits - (1 << width) if signed and bits >= 1 << (width - 1) else bits


def _values(width, signed):
    return [_wrap(bits, width, signed) for bits in range(1 << width)]


@pytest.mark.parametrize("kind", ("Propagated", "Explicit", "Implicit"))
@pytest.mark.parametrize("source_signed", (False, True))
@pytest.mark.parametrize("target_signed", (False, True))
@pytest.mark.parametrize("width", (2, 4, 8))
def test_conversion_interval_contains_every_source_value(kind, source_signed, target_signed, width):
    node = {"kind": "Conversion", "type": _type(width, target_signed), "conversionKind": kind,
            "operand": {"kind": "NamedValue", "symbol": "a", "type": _type(4, source_signed)}}
    low, high = _integral_interval(node, _SYMBOLS)
    sign = target_signed if kind == "Propagated" else source_signed
    for bits in range(16):
        expected = _wrap(_wrap(bits, 4, sign), width, target_signed)
        assert low <= expected <= high


@pytest.mark.parametrize("signed", (False, True))
@pytest.mark.parametrize("name,operation", (
    ("Add", operator.add), ("Subtract", operator.sub), ("Multiply", operator.mul),
    ("BinaryAnd", operator.and_), ("BinaryOr", operator.or_), ("BinaryXor", operator.xor),
    ("BinaryXnor", lambda a, b: ~(a ^ b))))
def test_arithmetic_interval_keeps_intermediate_wrap(name, operation, signed):
    kind = _type(4, signed)
    node = {"kind": "BinaryOp", "type": kind, "op": name,
            "left": {"kind": "NamedValue", "symbol": "a", "type": kind},
            "right": {"kind": "NamedValue", "symbol": "b", "type": kind}}
    low, high = _integral_interval(node, _SYMBOLS)
    for a in _values(4, signed):
        for b in _values(4, signed):
            assert low <= _wrap(operation(a, b), 4, signed) <= high


@pytest.mark.parametrize("signed", (False, True))
@pytest.mark.parametrize("name,operation", (("Minus", operator.neg), ("BitwiseNot", operator.invert)))
def test_unary_interval_keeps_intermediate_wrap(name, operation, signed):
    kind = _type(4, signed)
    node = {"kind": "UnaryOp", "type": kind, "op": name,
            "operand": {"kind": "NamedValue", "symbol": "a", "type": kind}}
    low, high = _integral_interval(node, _SYMBOLS)
    assert all(low <= _wrap(operation(a), 4, signed) <= high for a in _values(4, signed))


def test_mask_bound_is_not_lost_to_context_width():
    kind = _type(32)
    node = {"kind": "BinaryOp", "type": kind, "op": "BinaryAnd",
            "left": {"kind": "NamedValue", "symbol": "a", "type": kind},
            "right": {"kind": "IntegerLiteral", "type": kind, "constant": "3"}}
    assert _integral_interval(node, _SYMBOLS) == (0, 3)


def test_parameter_uses_its_compiler_identity_and_not_a_variable_domain():
    node = {"kind": "NamedValue", "symbol": "12 limit", "type": _type(8)}
    assert _integral_interval(node, {"12": {"kind": "Parameter", "value": "3"}}) == (3, 3)


def test_unbound_symbol_is_not_given_an_invented_parameter_value():
    node = {"kind": "NamedValue", "symbol": "missing", "type": _type(8)}
    with pytest.raises(KeyError):
        _integral_interval(node, {})


def test_unhandled_expression_keeps_its_universal_type_not_a_sample():
    node = {"kind": "Call", "type": _type(4, True), "name": "f"}
    assert _integral_interval(node, {}) == (-8, 7)


def test_invariant_range_is_bound_by_identity_not_source_spelling():
    from behave.sim.symbolic.ranges import Range

    symbols = {"1": {"kind": "Variable", "name": "limit"},
               "2": {"kind": "Variable", "name": "limit"}}
    node = {"kind": "NamedValue", "symbol": "1 limit", "type": _type(32, True)}
    assert _integral_interval(node, symbols, symbol_ranges={"1": Range(1, 7)}) == (1, 7)
    assert _integral_interval(node, symbols, symbol_ranges={"2": Range(1, 7)}) == (-(1 << 31), (1 << 31)-1)


@pytest.mark.parametrize("kind", ("Propagated", "Explicit", "Implicit"))
@pytest.mark.parametrize("source_signed", (False, True))
@pytest.mark.parametrize("target_signed", (False, True))
@pytest.mark.parametrize("width", (2, 4, 8))
def test_invariant_range_keeps_every_contextual_conversion(kind, source_signed, target_signed, width):
    from behave.sim.symbolic.ranges import Range

    node = {"kind": "Conversion", "type": _type(width, target_signed), "conversionKind": kind,
            "operand": {"kind": "NamedValue", "symbol": "a", "type": _type(4, source_signed)}}
    values = range(-3, 4) if source_signed else range(6, 11)
    low, high = _integral_interval(node, _SYMBOLS, symbol_ranges={"a": Range(min(values), max(values))})
    sign = target_signed if kind == "Propagated" else source_signed
    for value in values:
        assert low <= _wrap(_wrap(value, 4, sign), width, target_signed) <= high

"""Exhaustive small-width laws for the explicit integer-carrier BV family."""

import itertools
import operator
from functools import reduce

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import audit
from behave.sim.symbolic import encoder, interp, ir, primitives, ranges, typecheck as types

WRAPS = ("uwrap", "swrap")
SATURATES = ("usat", "ssat")
UNSIGNED_COMPARE = {
    "ult": operator.lt,
    "ule": operator.le,
    "ugt": operator.gt,
    "uge": operator.ge,
}
SIGNED_COMPARE = {
    "slt": operator.lt,
    "sle": operator.le,
    "sgt": operator.gt,
    "sge": operator.ge,
}
SHIFTS = ("ushl", "sshl", "ulshr", "sashr")
FIXED = frozenset((
    *WRAPS, *SATURATES, *UNSIGNED_COMPARE, *SIGNED_COMPARE, *SHIFTS))


@pytest.mark.parametrize("op", sorted(FIXED))
@pytest.mark.parametrize("carrier,leaf_widths", [
    (1, (3, 7)), (4, (2, 12)), (8, (16, 3)), (16, (5, 32))])
def test_fixed_callback_without_common_extension_is_symbolically_identical(
        op, carrier, leaf_widths):
    spec = primitives.primitive(op)
    context = encoder._Encoder(None, {}, tag="fixed_carrier")
    args = [z3.BitVec(f"arg_{i}", leaf_widths[i])
            for i in range(len(spec.signature.arguments) - 1)]
    result_width = carrier + 5
    common = max(result_width, *(arg.size() for arg in args))
    extended = [encoder._extend(arg, common) for arg in args]
    before = spec.symbolic(context, [*extended, carrier], result_width)
    after = spec.symbolic(context, [*args, carrier], result_width)
    before, after = encoder.widen(before, after)
    solver = z3.Solver()
    for term, argument in zip(spec.signature.arguments, args):
        if term is primitives.SHIFT:
            solver.add(argument >= 0)
    solver.add(before != after)
    assert solver.check() == z3.unsat


def test_fixed_wrap_does_not_pre_extend_its_operands(monkeypatch):
    original = encoder._Encoder.fixed_wrap
    calls = []

    def wrapped(self, value, width, result_width, *, signed):
        calls.append((value.size(), width))
        assert type(width) is int
        return original(self, value, width, result_width, signed=signed)

    monkeypatch.setattr(encoder._Encoder, "fixed_wrap", wrapped)
    enc = encoder._Encoder(None, {"x": ranges.Range(-4, 3)}, tag="original_carrier")
    enc.expr(ir.Op("uwrap", ir.Var("x"), ir.Lit(8)), {"x": z3.BitVec("x", 3)})
    assert calls == [(3, 8)]


@pytest.mark.parametrize('operation,arguments', [
    ('uwrap', (ir.Lit(-3), ir.Lit(8))),
    ('sashr', (ir.Lit(-3), ir.Lit(1), ir.Lit(8))),
    ('arrayfill', (ir.Lit(-3), ir.Lit(8))),
])
def test_static_parameters_do_not_become_symbolic_values(monkeypatch, operation, arguments):
    original = encoder._Encoder.expr
    parameter = arguments[-1]

    def record(self, expression, rho):
        assert expression is not parameter
        return original(self, expression, rho)

    monkeypatch.setattr(encoder._Encoder, 'expr', record)
    encoder._Encoder(None, {}).value(ir.Op(operation, *arguments), {})


def _u(value, width):
    return value % (1 << width)


def _s(value, width):
    pattern = _u(value, width)
    sign = 1 << (width - 1)
    return pattern - (1 << width) if pattern & sign else pattern


def _shift(op, value, amount, width):
    pattern = _u(value, width)
    if op == "ushl":
        return _u(0 if amount >= width else pattern << amount, width)
    if op == "sshl":
        return _s(0 if amount >= width else pattern << amount, width)
    if op == "ulshr":
        return 0 if amount >= width else pattern >> amount
    return _s(pattern, width) >> amount


def _native(op, values):
    if op == "uwrap":
        return _u(*values)
    if op == "swrap":
        return _s(*values)
    if op == "usat":
        value, width = values
        return min(max(value, 0), (1 << width) - 1)
    if op == "ssat":
        value, width = values
        bound = 1 << (width - 1)
        return min(max(value, -bound), bound - 1)
    if op in UNSIGNED_COMPARE:
        left, right, width = values
        return UNSIGNED_COMPARE[op](_u(left, width), _u(right, width))
    if op in SIGNED_COMPARE:
        left, right, width = values
        return SIGNED_COMPARE[op](_s(left, width), _s(right, width))
    value, amount, width = values
    return _shift(op, value, amount, width)


def _encoded(op, values):
    expression = ir.Op(op, *(ir.Lit(value) for value in values))
    term = encoder._Encoder(None, {}, tag="fixed").expr(expression, {})
    return z3.simplify(term).as_signed_long()


def _assert_parity(op, values):
    expected = _native(op, values)
    expression = ir.Op(op, *(ir.Lit(value) for value in values))
    assert primitives.primitive(op).denotation(*values) == expected
    assert interp._Interp({}).expr(expression) == expected
    assert _encoded(op, values) == int(expected)
    carrier = ranges.of_expression(expression, {})
    assert carrier is not None
    assert carrier.lo <= int(expected) <= carrier.hi


def test_fixed_width_family_is_typed_versioned_and_does_not_create_a_bv_type():
    assert FIXED <= primitives.primitive_names()
    assert primitives.REGISTRY_VERSION == "behavior-primitives/15"
    assert not hasattr(types, "BV") and not hasattr(ir, "BV")

    for name in (*WRAPS, *SATURATES):
        spec = primitives.primitive(name)
        assert spec.version == (3 if name in WRAPS else 1)
        assert spec.signature == primitives.TypeSignature(
            (primitives.INTEGER, primitives.WIDTH), primitives.INTEGER)
    for name in (*UNSIGNED_COMPARE, *SIGNED_COMPARE):
        spec = primitives.primitive(name)
        assert spec.version == 2
        assert spec.signature == primitives.TypeSignature(
            (primitives.INTEGER, primitives.INTEGER, primitives.WIDTH),
            primitives.BOOLEAN,
        )
    for name in SHIFTS:
        spec = primitives.primitive(name)
        assert spec.version == 4
        assert spec.signature == primitives.TypeSignature(
            (primitives.INTEGER, primitives.SHIFT, primitives.WIDTH),
            primitives.INTEGER,
        )

    # Ordinary integer arithmetic remains deliberately non-modular, even at
    # values that would overflow a four-bit carrier.
    mathematical = {
        "add": ((15, 1), 16),
        "sub": ((-8, 1), -9),
        "mul": ((8, 4), 32),
    }
    for name, (values, expected) in mathematical.items():
        expression = ir.Op(name, *(ir.Lit(value) for value in values))
        assert primitives.primitive(name).denotation(*values) == expected
        assert interp._Interp({}).expr(expression) == expected
        assert _encoded(name, values) == expected
        assert ranges.of_expression(expression, {}) == ranges.Range(
            expected, expected)
    assert primitives.primitive("uwrap").denotation(15 + 1, 4) == 0


def test_exhaustive_small_width_wrap_native_ir_range_and_symbolic_parity():
    for width in range(1, 6):
        extent = 1 << (width + 1)
        for value in range(-extent, extent + 1):
            for op in WRAPS:
                _assert_parity(op, (value, width))


def test_exhaustive_small_width_saturation_native_ir_range_and_symbolic_parity():
    for width in range(1, 6):
        extent = 1 << (width + 1)
        for value in range(-extent, extent + 1):
            for op in SATURATES:
                _assert_parity(op, (value, width))


def test_exhaustive_small_width_comparison_native_ir_and_symbolic_parity():
    for width in range(1, 5):
        patterns = range(1 << width)
        for left, right in itertools.product(patterns, repeat=2):
            for op in (*UNSIGNED_COMPARE, *SIGNED_COMPARE):
                _assert_parity(op, (left, right, width))


def test_exhaustive_small_width_shift_native_ir_and_symbolic_parity():
    for width in range(1, 5):
        for value in range(1 << width):
            # Includes both exact-width and beyond-width counts.
            for amount in range(width + 2):
                for op in SHIFTS:
                    _assert_parity(op, (value, amount, width))


@pytest.mark.parametrize('op', SHIFTS)
def test_constant_fixed_shift_range_is_a_point_not_the_whole_carrier(op):
    for width in range(1, 6):
        for value, amount in itertools.product(range(-16, 17), range(width + 2)):
            expression = ir.Op(op, ir.Var('value'), ir.Var('amount'), ir.Lit(width))
            domain = ranges.bounds_of_expression(expression, {
                'value': ranges.Range(value, value),
                'amount': ranges.Range(amount, amount),
            })
            expected = _shift(op, value, amount, width)
            assert domain is not None and domain.lo == domain.hi == expected


@pytest.mark.parametrize('op', SHIFTS)
def test_fixed_shift_interval_transfer_contains_wrapping_and_sign_fill(op):
    transform = primitives.primitive(op).range_transform
    interval = primitives.PrimitiveInterval
    for width in range(1, 5):
        period = 1 << width
        ends = sorted({-period-1, -period, -period//2, -1, 0, 1, period//2, period-1, period})
        shifts = (0, width-1, width, width+2)
        for lo, hi in itertools.combinations_with_replacement(ends, 2):
            for low, high in itertools.combinations_with_replacement(shifts, 2):
                result = transform(interval(lo, hi), interval(low, high), interval(width, width))
                observed = [_shift(op, value, count, width)
                            for value in range(lo, hi+1) for count in range(low, high+1)]
                assert result.lo <= min(observed) <= max(observed) <= result.hi


@pytest.mark.parametrize('width,bits,shift', [(8, 3, 2), (64, 24, 3), (128, 23, 25)])
def test_fixed_shift_preserves_small_ranges_inside_wide_carriers(width, bits, shift):
    value = ir.Op('ushl', ir.Var('x'), ir.Lit(shift), ir.Lit(width))
    bound = ranges.of_expression(value, {'x': ranges.Range(0, (1 << bits)-1)})
    assert (bound.lo, bound.hi) == (0, ((1 << bits)-1) << shift)
    positive = ir.Op('swrap', ir.Op('add', value, ir.Lit(1 << (bits+shift))), ir.Lit(width))
    environment = {'x': ranges.Range(0, (1 << bits)-1)}
    assert ranges.of_expression(positive, environment).lo > 0
    assert ranges.assume(ir.Op('eq', positive, ir.Lit(0)), True, environment) is None


@pytest.mark.parametrize('op', SHIFTS)
def test_fixed_shift_open_or_huge_count_needs_no_enumeration(op):
    interval = primitives.PrimitiveInterval
    transform = primitives.primitive(op).range_transform
    value, width = interval(-3, 5), interval(8, 8)
    for amount in (interval(8, None), interval(10**100, 10**100)):
        result = transform(value, amount, width)
        assert (result.lo, result.hi) == ((-1, 0) if op == 'sashr' else (0, 0))
    result = transform(value, interval(1, None), width)
    observed = [_shift(op, x, n, 8) for x in range(-3, 6) for n in range(1, 10)]
    assert result.lo <= min(observed) <= max(observed) <= result.hi


def _fixed_procedure(width):
    expressions = {
        "uw": ir.Op("uwrap", ir.Var("x"), ir.Lit(width)),
        "sw": ir.Op("swrap", ir.Var("x"), ir.Lit(width)),
        **{
            name: ir.Op(name, ir.Var("x"), ir.Var("y"), ir.Lit(width))
            for name in (*UNSIGNED_COMPARE, *SIGNED_COMPARE)
        },
        **{
            name: ir.Op(name, ir.Var("x"), ir.Var("n"), ir.Lit(width))
            for name in SHIFTS
        },
    }
    body = reduce(ir.Seq, (
        ir.Assign(name, expression) for name, expression in expressions.items()))
    names = tuple(expressions)
    variable_types = {name: types.INTEGER for name in ("x", "y", "n")}
    variable_types.update({
        name: (types.BOOLEAN if primitives.primitive(expression.op)
               .signature.result is primitives.BOOLEAN else types.INTEGER)
        for name, expression in expressions.items()
    })
    proc = ir.Procedure(
        (), ("x", "y", "n"), names, (), body,
        types=variable_types,
    )
    return proc, expressions


def _expected_fields(width, x, y, n):
    return {
        "uw": _u(x, width),
        "sw": _s(x, width),
        **{name: _native(name, (x, y, width))
           for name in (*UNSIGNED_COMPARE, *SIGNED_COMPARE)},
        **{name: _native(name, (x, n, width)) for name in SHIFTS},
    }


def test_exhaustive_dynamic_small_width_audit_and_false_unsat_cases():
    """One formula per width agrees on every value and admits no wrong row."""
    for width in range(1, 4):
        proc, _expressions = _fixed_procedure(width)
        sign = 1 << (width - 1)
        values = range(-sign, sign)
        env = {
            "x": ranges.Range(-sign, sign - 1),
            "y": ranges.Range(-sign, sign - 1),
            "n": ranges.Range(0, width + 1),
        }
        rows = [dict(zip(("x", "y", "n"), row)) for row in
                itertools.product(values, values, range(width + 2))]
        assert audit.step_audit(
            proc, env, [[row] for row in rows], workers=1) == []

        encoded = encoder.encode_step(proc, env=env)
        for row in rows:
            expected = _expected_fields(width, **row)
            wrong = z3.Or(*(
                encoded.final[name] != int(value)
                for name, value in expected.items()
            ))
            solver = z3.Solver()
            solver.add(encoded.constraint, wrong)
            solver.add(*(encoded.pre[name] == value
                         for name, value in row.items()))
            assert solver.check() == z3.unsat, (width, row)


def test_generated_interval_transformers_cover_every_small_width_result():
    intervals = [(lo, hi) for lo in range(-4, 5)
                 for hi in range(lo, 5)]
    for width in range(1, 5):
        width_interval = primitives.PrimitiveInterval(width, width)
        for lo, hi in intervals:
            value = primitives.PrimitiveInterval(lo, hi)
            for op in (*WRAPS, *SATURATES):
                got = primitives.primitive(op).range_transform(
                    value, width_interval)
                for concrete in range(lo, hi + 1):
                    result = _native(op, (concrete, width))
                    assert got.lo <= result <= got.hi
            for amount_lo in range(width + 2):
                for amount_hi in range(amount_lo, width + 2):
                    amount = primitives.PrimitiveInterval(
                        amount_lo, amount_hi)
                    for op in SHIFTS:
                        got = primitives.primitive(op).range_transform(
                            value, amount, width_interval)
                        for concrete, shift in itertools.product(
                                range(lo, hi + 1),
                                range(amount_lo, amount_hi + 1)):
                            result = _native(op, (concrete, shift, width))
                            assert got.lo <= result <= got.hi


@pytest.mark.parametrize("op", WRAPS)
def test_wrap_interval_is_exact_across_signed_and_unsigned_discontinuities(op):
    spec = primitives.primitive(op)
    for width in range(1, 6):
        period = 1 << width
        carrier = primitives.PrimitiveInterval(width, width)
        for lower in range(-2 * period, 2 * period + 1):
            minimum = maximum = spec.denotation(lower, width)
            for upper in range(lower, 2 * period + 1):
                actual = spec.denotation(upper, width)
                minimum, maximum = min(minimum, actual), max(maximum, actual)
                result = spec.range_transform(primitives.PrimitiveInterval(lower, upper), carrier)
                assert (result.lo, result.hi) == (minimum, maximum), (op, width, lower, upper)


@pytest.mark.parametrize("op", SHIFTS)
def test_shift_carrier_does_not_reuse_unshifted_wrap_interval(op):
    spec = primitives.primitive(op)
    for value, amount in itertools.product(range(-8, 9), range(6)):
        actual = spec.denotation(value, amount, 4)
        result = spec.range_transform(primitives.PrimitiveInterval(value, value),
            primitives.PrimitiveInterval(amount, amount), primitives.PrimitiveInterval(4, 4))
        assert result.lo <= actual <= result.hi


@pytest.mark.parametrize("expression, message", [
    (ir.Op("uwrap", ir.Lit(1), ir.Var("w")), "literal width"),
    (ir.Op("swrap", ir.Lit(1), ir.Lit(0)), "positive literal width"),
    (ir.Op("ushl", ir.Lit(1), ir.Lit(-1), ir.Lit(4)),
     "nonnegative shift"),
])
def test_invalid_width_and_negative_shift_refuse_in_every_ir_consumer(
        expression, message):
    held = {"w": types.INTEGER}
    faults = []
    assert types._of_expression(expression, held, faults, "test") is None
    assert any(message in fault for fault in faults)
    assert ranges.of_expression(
        expression, {"w": ranges.Range(1, 4)}) is None
    with pytest.raises(interp.InterpError, match=message):
        interp._Interp({"w": 4}).expr(expression)
    with pytest.raises(encoder.EncodingError, match=message):
        encoder._Encoder(
            None, {"w": ranges.Range(1, 4)}).expr(
                expression, {"w": z3.BitVecVal(4, 4)})


def test_dynamic_shift_range_must_prove_the_nonnegative_domain():
    expression = ir.Op(
        "sashr", ir.Var("x"), ir.Var("n"), ir.Lit(4))
    held = {"x": types.INTEGER, "n": types.INTEGER}
    faults = []
    assert types._of_expression(expression, held, faults, "test") == \
        types.INTEGER
    assert faults == []
    env = {"x": ranges.Range(-8, 7), "n": ranges.Range(-1, 2)}
    assert ranges.of_expression(expression, env) is None
    with pytest.raises(encoder.EncodingError) as caught:
        encoder._Encoder(None, env).expr(
            expression,
            {"x": z3.BitVec("x", 4), "n": z3.BitVec("n", 3)},
        )
    assert caught.value.kind == "range"
    assert "not nonnegative" in str(caught.value)

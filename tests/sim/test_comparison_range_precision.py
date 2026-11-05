"""Known comparisons must stay known inside compound value expressions."""

import itertools
import operator

import pytest

from behave.sim.behavior import instance
from behave.sim.symbolic import encoder, interp, ir, primitives, ranges
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


@pytest.mark.parametrize("op", ["lt", "le", "gt", "ge", "eq", "ne"])
def test_ordered_comparison_hull_matches_every_small_integer_rectangle(op):
    intervals = [(lo, hi) for lo in range(-3, 4) for hi in range(lo, 4)]
    compare = getattr(operator, op)
    expression = ir.Op(op, ir.Var("a"), ir.Var("b"))
    for left, right in itertools.product(intervals, repeat=2):
        values = [int(compare(a, b)) for a, b in itertools.product(
            range(left[0], left[1] + 1), range(right[0], right[1] + 1))]
        expected = ranges.Range(min(values), max(values))
        env = {"a": ranges.Range(*left), "b": ranges.Range(*right)}
        assert ranges.of_expression(expression, env) == expected
        assert ranges.bounds_of_expression(expression, env) == ranges.Bounds.of(expected)


@pytest.mark.parametrize("op", ["lt", "le", "gt", "ge", "eq", "ne"])
def test_open_or_unknown_bounds_never_invent_a_comparison_proof(op):
    intervals = [(None, None), (None, -1), (None, 0), (0, None),
                 (1, None), (-2, 2), (0, 0), (None, 3), (-3, None)]
    compare = getattr(operator, op)
    expression = ir.Op(op, ir.Var("a"), ir.Var("b"))
    for left, right in itertools.product(intervals, repeat=2):
        env = {"a": ranges.Bounds(*left), "b": ranges.Bounds(*right)}
        got = ranges.bounds_of_expression(expression, env)
        for a, b in itertools.product(range(-8, 9), repeat=2):
            if all((lo is None or lo <= value) and (hi is None or value <= hi)
                   for value, (lo, hi) in zip((a, b), (left, right))):
                assert got.lo <= int(compare(a, b)) <= got.hi
    primitive = primitives.primitive(op)
    assert primitive.range_transform(None, None) == primitives.PrimitiveInterval(0, 1)
    assert ranges.bounds_of_expression(expression, {
        "a": ranges.Bounds(None, -1), "b": ranges.Bounds(0, None)
    }) == ranges.Bounds(int(compare(-1, 0)), int(compare(-1, 0)))


@pytest.mark.parametrize("op", ["truth", "not"])
def test_boolean_conversion_preserves_all_finite_and_open_bounds(op):
    intervals = [(lo, hi) for lo in range(-3, 4) for hi in range(lo, 4)]
    intervals += [(None, None), (None, -1), (1, None), (0, None), (None, 0)]
    primitive = primitives.primitive(op)
    for lo, hi in intervals:
        got = primitive.range_transform(primitives.PrimitiveInterval(lo, hi))
        values = [int(bool(x) if op == "truth" else not x) for x in range(-8, 9)
                  if (lo is None or lo <= x) and (hi is None or x <= hi)]
        assert got == primitives.PrimitiveInterval(min(values), max(values))
    assert primitive.range_transform(None) == primitives.PrimitiveInterval(0, 1)


@pytest.mark.parametrize("op,combine", [("land", operator.and_), ("lor", operator.or_)])
def test_boolean_composition_preserves_constants_but_not_unproved_correlations(op, combine):
    interval = primitives.PrimitiveInterval
    for left, right in itertools.product((None, (0, 0), (1, 1), (0, 1)), repeat=2):
        got = primitives.primitive(op).range_transform(
            None if left is None else interval(*left),
            None if right is None else interval(*right))
        a_lo, a_hi = (0, 1) if left is None else left
        b_lo, b_hi = (0, 1) if right is None else right
        values = [combine(a, b) for a in range(a_lo, a_hi + 1)
                  for b in range(b_lo, b_hi + 1)]
        assert got == interval(min(values), max(values))


@pytest.mark.solver
def test_fixed_dispatch_survives_lowering_before_loop_expansion():
    import z3
    from behave.sim.symbolic import plan

    source = '''class Model:
    def reset(self): pass
    def process(self, a, x):
        if a == 0:
            y = x
        elif a == 1:
            y = x + 1
        elif a == 3:
            y = 0
        else:
            y = 0
            for i in range(8):
                y += x * i
        return {"y": y}
'''
    schema = TransactionSchema([
        Endpoint("a", "input", 2), Endpoint("x", "input", 4),
        Endpoint("y", "output", 16)
    ], source="control-flow fixture", authority=Authority.TASK, reading=Reading.ELABORATED)
    model = instance.lower(source, schema, bound=4)
    broad = plan.plan_encoding(model.proc, model.env)
    for value in range(4):
        planned = plan.plan_encoding(model.proc, model.env, fixed_inputs={"a": value})
        assert planned.complete and bool(planned.loop_bounds) == (value == 2)
        assert planned.widths == broad.widths and planned.env == broad.env
        if value != 2:
            assert planned.expansion_cost < broad.expansion_cost
        encoded = encoder.encode_step(model.proc, env=model.env, plan=planned,
                                      pre={"a": z3.BitVecVal(value, planned.widths["a"])})
        for x in range(16):
            solver = z3.Solver()
            solver.add(encoded.constraint, encoded.pre["x"] == x)
            assert solver.check() == z3.sat
            actual = solver.model()
            _, output, hits, error = interp.run_step(model.proc, {"a": value, "x": x})
            assert not error and z3.is_true(actual.eval(encoded.ok))
            assert actual.eval(encoded.post["y"][0]).as_signed_long() == output.transactions[0]["y"]
            assert {g for g, condition in encoded.hits.items()
                    if z3.is_true(actual.eval(condition))} == hits
    with pytest.raises(plan.PlanMismatch, match="input support"):
        plan.plan_encoding(model.proc, {**model.env, "a": ranges.Range(0, 0)})


@pytest.mark.parametrize("condition,expected", [
    ((0, 0), (9, 12)), ((1, 1), (-4, 3)), ((0, 1), (-4, 12)),
    (None, (-4, 12)),
])
def test_conditional_range_uses_only_a_proved_boolean_choice(condition, expected):
    interval = primitives.PrimitiveInterval
    got = primitives.primitive("ite").range_transform(
        None if condition is None else interval(*condition),
        interval(-4, 3), interval(9, 12))
    assert got == interval(*expected)
    assert primitives.primitive("ite").range_transform(
        interval(0, 0), None, interval(9, 12)) == interval(9, 12)
    assert primitives.primitive("ite").range_transform(
        interval(1, 1), interval(-4, 3), None) == interval(-4, 3)
    assert primitives.primitive("ite").range_transform(
        interval(0, 1), None, interval(9, 12)) is None


@pytest.mark.parametrize("op", ["lt", "le", "gt", "ge"])
def test_comparison_choice_retains_precision_below_other_arithmetic(op):
    comparison = ir.Op(op, ir.Var("x"), ir.Lit(0))
    selected = ir.Op("ite", comparison, ir.Op("neg", ir.Var("x")), ir.Var("x"))
    expression = ir.Op("fdiv", selected, ir.Var("d"))
    compare = getattr(operator, op)
    for lo, hi in [(-8, -1), (0, 0), (1, 8), (-8, 8)]:
        env = {"x": ranges.Range(lo, hi), "d": ranges.Range(1, 4)}
        values = [(int(-x if compare(x, 0) else x) // d)
                  for x in range(lo, hi + 1) for d in range(1, 5)]
        got = ranges.of_expression(expression, env)
        assert got.lo <= min(values) <= max(values) <= got.hi
        if hi < 0 or lo >= 0:
            assert got == ranges.Range(min(values), max(values))


@pytest.mark.solver
@pytest.mark.parametrize("numerator", [0, 17, 64])
def test_unsigned_helper_output_needs_no_exact_fallback_and_keeps_native_hits(
        monkeypatch, numerator):
    def unexpected(*args, **kwargs):
        pytest.fail("a proved conditional value must not encode the whole output formula")

    source = f'''def quotient(x, d):
    q = abs(x) // d
    if x < 0:
        q = -q
    return q

class M:
    def reset(self): pass
    def process(self, a):
        value = quotient({numerator}, (a & 7) + 1)
        if a > 12:
            value += 1
        return {{"y": value}}
'''
    schema = TransactionSchema([
        Endpoint("a", "input", 4), Endpoint("y", "output", 8)
    ], source="test task", authority=Authority.TASK, reading=Reading.ELABORATED)
    with monkeypatch.context() as patch:
        patch.setattr(encoder, "encode_step", unexpected)
        model = instance.lower(source, schema, bound=16)
    rows = [{"a": a} for a in range(16)]
    audit = model.source_audit((rows,))
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()
    for row in rows:
        _state, output, _hits, error = interp.run_step(model.proc, row)
        assert not error
        assert output.transactions == [{
            "y": numerator // ((row["a"] & 7) + 1) + int(row["a"] > 12)
        }]

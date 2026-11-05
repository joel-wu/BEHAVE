"""Internal signed padding must not shrink an admitted scalar capability."""

import pytest
import z3

from behave.sim.behavior import budgets
from behave.sim.symbolic import encoder, ir, plan, ranges, typecheck as types
from behave.sim.symbolic.step_template import StepTemplate, _leaves, _tree

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('width', [1, 4, 32, 128])
@pytest.mark.parametrize('bounds', [(0, 1), (0, 0), (1, 1), (-1, 0)])
def test_template_input_bounds_preserve_signed_domain(width, bounds):
    lo, hi = bounds
    lo, hi = lo * ((1 << width)-1), hi * ((1 << width)-1)
    proc = ir.Procedure((), ('a',), (), (), ir.Skip())
    template = StepTemplate(proc, {'a': ranges.Range(lo, hi)})
    encoded = template.encode()
    value = encoded.pre['a']
    expected = z3.And(value >= lo, value <= hi)
    solver = z3.Solver()
    solver.add(encoded.constraint != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('width', [1, 4, 32, 128])
@pytest.mark.parametrize('private_context', [False, True])
def test_padded_physical_input_has_no_residual_range_dependency(width, private_context):
    proc = ir.Procedure((), ('a',), (), (), ir.Skip())
    template = StepTemplate(proc, {'a': ranges.Range(0, (1 << width)-1)})
    ctx = z3.Context() if private_context else z3.main_ctx()
    if private_context:
        template = template.translate(ctx)
    payload = z3.BitVec('physical_input', width, ctx=ctx)
    encoded = template.encode({'a': z3.ZeroExt(1, payload)})
    assert z3.is_true(z3.simplify(encoded.constraint))


@pytest.mark.parametrize("width", [3, 8, 64])
@pytest.mark.parametrize("array", [False, True])
def test_template_accepts_the_full_carrier_of_a_maximum_unsigned_state(monkeypatch, width, array):
    monkeypatch.setattr(encoder, "SCALAR_BITS", width)
    monkeypatch.setattr(plan, "SCALAR_BITS", width)
    mask = (1 << width) - 1
    value = ir.Sel(ir.Var("q"), ir.Lit(0)) if array else ir.Var("q")
    expression = ir.Op("and", value, ir.Lit(mask))
    write = ir.Store("q", ir.Lit(0), expression) if array else ir.Assign("q", expression)
    proc = ir.Procedure(("q",), (), ("i",), (), ir.For("i", 1, write),
                        initial={"q": [0] if array else 0},
                        types={"q": types.Array(1)} if array else {})
    compiled = StepTemplate(proc, {"q": ranges.Range(0, mask)}).encode()
    # The sign-padded carrier includes -2**width. It needs no additional bit,
    # and the reusable transition must still cover it rather than assume q>=0.
    full = {"q": ranges.Range(-(1 << width), mask)}
    plain = encoder.encode_step(proc, pre=compiled.pre, env=full)
    differences = [plain.ok != compiled.ok, plain.error != compiled.error]
    for field in ("state_post", "post", "hits"):
        for name, value in getattr(plain, field).items():
            differences.extend(left != right for left, right in zip(
                _leaves(_tree(value)), _leaves(getattr(compiled, field)[name]), strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint, z3.Or(*differences))
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(compiled.constraint,
               next(_leaves(compiled.pre["q"])) == -(1 << width),
               next(_leaves(compiled.state_post["q"])) == 0)
    assert solver.check() == z3.sat


def test_default_maximum_packed_state_can_enter_a_retained_loop_template():
    mask = (1 << budgets.SCALAR_BITS) - 1
    proc = ir.Procedure(("q",), (), ("i",), (),
                        ir.For("i", 1, ir.Assign("q", ir.Var("q"))),
                        initial={"q": 0})
    template = StepTemplate(proc, {"q": ranges.Range(0, mask)})
    encoded = template.encode()
    assert encoded.pre["q"].size() == budgets.SCALAR_BITS + 1
    assert z3.is_true(z3.simplify(encoded.state_post["q"] == encoded.pre["q"]))


@pytest.mark.parametrize("bounds", [(0, 256), (-257, 0)])
def test_truly_wider_internal_carriers_are_still_refused(monkeypatch, bounds):
    monkeypatch.setattr(encoder, "SCALAR_BITS", 8)
    monkeypatch.setattr(plan, "SCALAR_BITS", 8)
    proc = ir.Procedure(("q",), (), (), (), ir.Skip(), initial={"q": 0})
    env = {"q": ranges.Range(*bounds)}
    assert any(fault.kind == "width" for fault in plan.plan_encoding(proc, env).unresolved)
    with pytest.raises(encoder.EncodingError, match="carrier"):
        encoder.validated_width(env["q"])

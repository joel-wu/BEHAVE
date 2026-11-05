"""A goal query requires carriers for its audited dependencies, not its tail."""

import pytest

from behave.sim.symbolic import encoder, interp, ir, plan, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome

pytestmark = pytest.mark.solver


def _problem(*, depends=False, preceding_failure=False):
    early = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
                  ir.Mark("early"), ir.Skip())
    tail = ir.For("i", 1, ir.Assign(
        "x", ir.Op("shl", ir.Lit(1), ir.Var("a"))))
    if depends:
        body = ir.Seq(tail, ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)),
                                   ir.Mark("depends"), ir.Skip()))
    else:
        body = ir.Seq(early, tail)
    if preceding_failure:
        body = ir.Seq(ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
                            ir.Fail("Stopped", "bad"), ir.Skip()), body)
    proc = ir.Procedure((), ("a",), ("i", "x"), (), body, initial={},
        signature=ProcedureSignature((("a", FiniteType(16)),), ()))
    return proc, {"a": ranges.Range(0, 65535)}


@pytest.mark.parametrize("value", [0, 1, 8192, 8193, 65535])
def test_dropped_tail_does_not_block_exact_goal_query(value):
    proc, env = _problem()
    prepared = plan.plan_encoding(proc, env)
    assert prepared.unresolved
    assert all(isinstance(item.what, plan.ExpressionPosition)
               for item in prepared.unresolved)
    expected = interp.run_step(proc, {"a": value}, {})[2]
    result = local_hit_any(proc, env, {}, ("early",),
                          input_constraints=lambda inputs: (inputs["a"] == value,),
        timeout_ms=60_000)
    assert result.outcome is (Outcome.SAT if "early" in expected else Outcome.LOCAL_UNSAT), result.detail
    if result.outcome is Outcome.SAT:
        assert result.candidate == {"a": value}
        assert result.goal in interp.run_step(proc, result.candidate, {})[2]
    # This is not permission to encode the complete unrepresented transition.
    with pytest.raises(encoder.EncodingError, match="encoding plan is unresolved"):
        encoder.encode_step(proc, env=env, plan=prepared)


def test_relevant_unrepresented_expression_is_still_refused():
    proc, env = _problem(depends=True)
    assert "depends" in interp.run_step(proc, {"a": 0}, {})[2]
    result = local_hit_any(proc, env, {}, ("depends",),
        timeout_ms=60_000)
    assert result.outcome is Outcome.UNSUPPORTED
    assert "left-shift" in result.detail


def test_preceding_failure_still_blocks_the_requested_hit():
    proc, env = _problem(preceding_failure=True)
    assert interp.run_step(proc, {"a": 0}, {})[2] == {"bad"}
    result = local_hit_any(proc, env, {}, ("early",),
        timeout_ms=60_000)
    assert result.outcome is Outcome.LOCAL_UNSAT, result.detail
    failure = local_hit_any(proc, env, {}, ("bad",),
        timeout_ms=60_000)
    assert failure.outcome is Outcome.SAT, failure.detail
    assert failure.goal in interp.run_step(proc, failure.candidate, {})[2]


def test_input_boundary_mismatch_is_not_erased_by_cone_projection():
    proc, env = _problem()
    result = local_hit_any(proc, {**env, "a": ranges.Range(0, 3)}, {}, ("early",),
        timeout_ms=60_000)
    assert result.outcome is Outcome.UNSUPPORTED

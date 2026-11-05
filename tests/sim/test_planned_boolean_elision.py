"""A proved Boolean value elides its formula, not its source execution events."""

import itertools

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, plan, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType

pytestmark = pytest.mark.solver


def procedure(expression, *, failure_first=False):
    failure = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(3)),
                    ir.Fail("Stopped", "failed"), ir.Skip())
    body = ir._sequence((
        ir.Mark("start"), failure if failure_first else ir.Skip(),
        ir.If(ir.Op("truth", ir.Var("gate")),
              ir.For("i", 3, ir._sequence((
                  ir.Assign("flag", expression), ir.Mark("calculated"),
                  ir.Assign("saved", ir.Op("add", ir.Var("saved"),
                                             ir.Op("toint", ir.Var("flag")))),
                  ir.If(ir.Var("flag"), ir.Mark("true"), ir.Mark("false")),
                  ir.Skip() if failure_first else failure))), ir.Skip()),
        ir.Mark("end")))
    proc = ir.Procedure(("saved",), ("a", "gate"), ("i", "flag"), (), body,
        initial={"saved": 0},
        signature=ProcedureSignature((("a", FiniteType(2)), ("gate", FiniteType(1))), ()))
    env = {"a": ranges.Range(0, 3), "gate": ranges.Range(0, 1),
           "saved": ranges.Range(-1, 4)}
    return proc, env


@pytest.mark.parametrize("failure_first", [False, True])
@pytest.mark.parametrize("kind", ["false", "true", "dynamic"])
def test_complete_state_failure_and_hits_match_every_small_concrete_execution(kind, failure_first):
    i = ir.Var("i")
    expression = {
        "false": ir.Op("lt", ir.Op("sub", i, ir.Lit(1)), ir.Lit(-1)),
        "true": ir.Op("ge", ir.Op("mul", i, i), ir.Lit(0)),
        "dynamic": ir.Op("eq", ir.Var("a"), i),
    }[kind]
    proc, env = procedure(expression, failure_first=failure_first)
    prepared = plan.plan_encoding(proc, env)
    assert not prepared.unresolved
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for a, gate, saved in itertools.product(range(4), range(2), (-1, 0, 4)):
        state, _outputs, hits, failure = interp.run_step(proc, {"a": a, "gate": gate},
                                                       {"saved": saved})
        solver.push()
        solver.add(encoded.pre["a"] == a, encoded.pre["gate"] == gate,
                   encoded.pre["saved"] == saved)
        assert solver.check() == z3.sat
        model = solver.model()
        assert model.eval(encoded.state_post["saved"]).as_signed_long() == state["saved"]
        assert {goal for goal, term in encoded.hits.items()
                if z3.is_true(model.eval(term))} == hits
        assert z3.is_true(model.eval(encoded.ok)) == (not failure)
        assert encoder.error_name(model.eval(encoded.error).as_long(), encoded.error_codes) == failure
        solver.pop()


def test_proved_assignment_does_not_construct_its_arithmetic_operand(monkeypatch):
    arithmetic = ir.Op("sub", ir.Var("i"), ir.Lit(1))
    proc, env = procedure(ir.Op("lt", arithmetic, ir.Lit(-1)))
    prepared = plan.plan_encoding(proc, env)
    original = encoder._Encoder.expr

    def observed(self, expression, rho):
        assert expression is not arithmetic, "proved RHS still expanded its operand"
        return original(self, expression, rho)

    monkeypatch.setattr(encoder._Encoder, "expr", observed)
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.goal_condition("calculated"))
    assert solver.check() == z3.sat
    solver.add(encoded.goal_condition("true"))
    assert solver.check() == z3.unsat


def test_unplanned_encoding_has_no_carrier_based_shortcut():
    # This internal arithmetic diagnostic has no admitted occurrence plan.
    # Its env is not evidence that the unconstrained symbolic input is zero.
    raw = encoder._Encoder(None, {"x": ranges.Range(0, 0)}, "unplanned")
    value = z3.BitVec("unplanned_x", 8)
    term = raw.value(ir.Op("eq", ir.Var("x"), ir.Lit(0)), {"x": value})
    solver = z3.Solver()
    solver.add(value == 1, term != 0)
    assert solver.check() == z3.unsat


def test_constant_result_cannot_hide_an_invalid_primitive_width():
    invalid = ir.Op("eq", ir.Op("uwrap", ir.Var("a"), ir.Lit(0)), ir.Lit(0))
    proc, env = procedure(invalid)
    with pytest.raises(encoder.EncodingError):
        encoder.encode_step(proc, env=env)


def test_boolean_semantic_type_stays_distinct_from_integer():
    proc, env = procedure(ir.Op("lt", ir.Var("i"), ir.Lit(0)))
    encoded = encoder.encode_step(proc, env=env)
    inferred, faults = types.of_procedure(proc)
    assert not faults and inferred["flag"] is types.BOOLEAN
    assert not z3.is_bool(encoded.pre["a"])


def test_supplied_state_is_not_constrained_by_its_proposed_carrier():
    condition = ir.Op("eq", ir.Var("s"), ir.Lit(0))
    proc = ir.Procedure(("s", "b"), (), (), (),
        ir.Assign("b", condition),
        initial={"s": 0, "b": False}, signature=ProcedureSignature((), ()))
    env = {"s": ranges.Range(0, 0), "b": ranges.Range(0, 1)}
    prepared = plan.plan_encoding(proc, env)
    before = {name: z3.BitVec("supplied_" + name, prepared.widths[name]) for name in proc.state}
    encoded = encoder.encode_step(proc, pre=before, env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint, before["s"] == -1)
    assert solver.check() == z3.sat
    solver.add(encoded.state_post["b"] != 0)
    assert solver.check() == z3.unsat


def test_branch_hits_do_not_assume_the_supplied_state_carrier():
    proc = ir.Procedure(("s",), (), (), (),
        ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(0)),
              ir.Mark("zero"), ir.Mark("nonzero")),
        initial={"s": 0}, signature=ProcedureSignature((), ()))
    env = {"s": ranges.Range(0, 0)}
    prepared = plan.plan_encoding(proc, env)
    before = {"s": z3.BitVec("branch_supplied_s", prepared.widths["s"])}
    encoded = encoder.encode_step(proc, pre=before, env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint, before["s"] == -1)
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(encoded.goal_condition("nonzero")))
    assert z3.is_false(model.eval(encoded.goal_condition("zero")))


def test_inadequate_plan_refuses_instead_of_omitting_a_possible_branch():
    proc = ir.Procedure(("s", "b"), (), (), (),
        ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(0)),
              ir.Assign("b", ir.Lit(True)), ir.Assign("b", ir.Lit(False))),
        initial={"s": 0, "b": False}, signature=ProcedureSignature((), ()))
    env = {"s": ranges.Range(0, 0), "b": ranges.Range(0, 1)}
    prepared = plan.plan_encoding(proc, env)
    before = {name: z3.BitVec("inadequate_" + name, prepared.widths[name])
              for name in proc.state}
    # The supplied state is not bounded to zero. The plan has no RHS carrier
    # for the else branch, so it cannot describe this more general relation.
    with pytest.raises(encoder.EncodingError, match="no carrier"):
        encoder.encode_step(proc, pre=before, env=env, plan=prepared)

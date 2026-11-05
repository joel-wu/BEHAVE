"""A branch proved constant at its entry must not need dead-arm carriers."""

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.symbolic.plan import ExpressionPosition, _command_numbers, plan_encoding

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


def _sequence(*commands):
    result = commands[-1]
    for command in reversed(commands[:-1]):
        result = ir.Seq(command, result)
    return result


@pytest.mark.parametrize("fixed", [0, 3, -2, None])
def test_guarded_loop_uses_proved_branch_range_without_losing_outputs_or_hits(fixed):
    yes = _sequence(ir.Mark("yes"), ir.Assign("y", ir.Op("add", ir.Var("a"), ir.Lit(10))))
    no = _sequence(ir.Mark("no"), ir.Assign("y", ir.Op("add", ir.Var("a"), ir.Lit(20))))
    body = _sequence(
        ir.Assign("flag", ir.Lit(1 if fixed == 0 else 0)),
        ir.Assign("y", ir.Lit(7)),
        ir.If(ir.Op("ne", ir.Var("enabled"), ir.Lit(0)), _sequence(
            ir.Assign("flag", ir.Var("a") if fixed is None else ir.Lit(fixed)),
            ir.For("i", ir.Lit(2), ir.If(ir.Op("ne", ir.Var("flag"), ir.Lit(0)), yes, no))), ir.Skip()))
    proc = ir.Procedure([], ["enabled", "a"], ["flag", "i"], ["y"], body)
    proc.types = types.of_procedure(proc)[0]
    env = ranges.fixpoint(proc, {"enabled": ranges.Range(0, 1), "a": ranges.Range(0, 3)})
    plan = plan_encoding(proc, env)
    assert plan.complete, plan.unresolved
    if fixed is not None:
        dead = no.second if fixed else yes.second
        number = _command_numbers(proc.body)[id(dead)]
        assert ExpressionPosition(number, "rhs") not in plan.occurrence_carriers

    enc = encoder.encode_step(proc, env=env, plan=plan)
    for enabled in (0, 1):
        for a in range(4):
            _, output, hits, error = interp.run_step(proc, {"enabled": enabled, "a": a})
            assert not error
            expected = output.transactions[0]["y"]
            expected_hits = (set() if not enabled else {"yes" if (a if fixed is None else fixed) else "no"})
            assert hits == expected_hits
            solver = z3.Solver()
            solver.add(enc.constraint, enc.pre["enabled"] == enabled, enc.pre["a"] == a)
            assert solver.check() == z3.sat
            # Prove the encoded output and every goal agree, not just one model.
            differs = [enc.post["y"] != expected]
            differs.extend(term != (goal in hits) for goal, term in enc.hits.items())
            assert hits <= enc.hits.keys()
            solver.add(z3.Or(*differs))
            assert solver.check() == z3.unsat

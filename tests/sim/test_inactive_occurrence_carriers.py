"""No carrier is required at a command that provably has no visits."""

from behave.sim.symbolic import closure_proof

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import residual
from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types


@pytest.mark.parametrize("steps", [1, 3])
def test_terminal_failure_does_not_prime_unvisited_command_carriers(steps):
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Seq(ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
                     ir.Fail("DivZero", "bad"), ir.Skip()),
               ir.Assign("y", ir.Op("add", ir.Var("a"), ir.Lit(1)))),
        types={"a": types.INTEGER, "y": types.INTEGER})
    env = {"a": ranges.Range(0, 0)}
    prepared = closure_proof.prepare_positions(proc, env, steps)
    encoded = residual.unroll(proc, env, steps, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.goal_condition("bad"))
    assert solver.check() == z3.sat
    _states, _outputs, hits, error = interp.run_trace_detailed(proc, [{"a": 0}] * steps)
    assert error == "DivZero" and hits == [{"bad"}] + [set()] * (steps - 1)


def test_unvisited_code_still_receives_complete_procedure_validation(monkeypatch):
    from behave.sim.symbolic import capabilities

    monkeypatch.setattr(capabilities, "REPRESENTATION_DEPTH", 4)
    deep = ir.Lit(1)
    for _ in range(5):
        deep = ir.Op("add", deep, ir.Lit(1))
    proc = ir.Procedure(
        (), (), (), ("y",),
        ir.Seq(ir.Fail("DivZero", "bad"),
               ir.Assign("y", deep)),
        types={"y": types.INTEGER})
    with pytest.raises(encoder.EncodingError, match="representation-depth-exceeded"):
        residual.unroll(proc, {}, 1)


@pytest.mark.parametrize("value", [-2, 0, 3])
def test_singleton_inputs_keep_boundary_symbols_without_assuming_state_ranges(value):
    proc = ir.Procedure(
        ("s",), ("a",), (), ("y",),
        ir.Assign("y", ir.Op("add", ir.Var("s"), ir.Var("a"))),
        initial={"s": 0}, types={name: types.INTEGER for name in ("s", "a", "y")})
    state, input_ = z3.BitVecs("supplied_state declared_input", 8)
    encoded = encoder.encode_step(
        proc, pre={"s": state, "a": input_},
        env={"a": ranges.Range(value, value), "s": ranges.Range(0, 0)})
    assert z3.eq(encoded.pre["a"], input_)
    assert z3.eq(encoded.pre["s"], state)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    solver.push()
    solver.add(input_ != value)
    assert solver.check() == z3.unsat  # input restriction is retained
    solver.pop()
    solver.add(state == 5)
    assert solver.check() == z3.sat  # supplied state was not restricted to zero
    solver.add(encoded.post["y"] != 5 + value)
    assert solver.check() == z3.unsat

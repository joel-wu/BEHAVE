"""Selected inputs may vary; the recorded environment and state may not."""

import itertools

import pytest

from behave.sim.symbolic import interp, ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.core.value_type import ValueType
from behave.sim.engines import local_ir, probing

pytestmark = pytest.mark.solver


def problem():
    proc = ir.Procedure(("state",), ("a", "b", "active"), (), (),
        ir.If(ir.Var("active"),
            ir.If(ir.Op("eq", ir.Op("add", ir.Var("a"), ir.Var("b")), ir.Var("state")),
                  ir.Mark("hit"), ir.Skip()), ir.Skip()),
        types={"state": types.INTEGER, "a": types.INTEGER, "b": types.INTEGER,
               "active": types.BOOLEAN},
        signature=ProcedureSignature((("a", FiniteType(2)), ("b", FiniteType(2)),
                                      ("active", ValueType.boolean())), ()))
    env = {"a": ranges.Range(0, 3), "b": ranges.Range(0, 3),
           "active": ranges.Range(0, 1), "state": ranges.Range(0, 6)}
    return proc, env


@pytest.mark.parametrize("active", [False, True])
def test_fixed_inputs_match_exhaustive_replay(active):
    proc, env = problem()
    for s, b in itertools.product(range(7), range(4)):
        fixed = {"active": active, "b": b}
        inputs = [dict(fixed, a=a) for a in range(4)]
        possible = any("hit" in interp.run_step(proc, u, {"state": s})[2] for u in inputs)
        result = local_ir.local_hit(proc, env, {"state": s}, "hit", fixed_inputs=fixed,
            timeout_ms=60_000)
        assert result.outcome is (probing.Outcome.SAT if possible else probing.Outcome.LOCAL_UNSAT)
        if possible:
            assert result.candidate in inputs
            assert "hit" in interp.run_step(proc, result.candidate, {"state": s})[2]


@pytest.mark.parametrize("fixed", [
    {"active": 1}, {"active": 2}, {"b": 4}, {"b": -1}, {"b": True},
    {"state": 0}, {"typo": 0},
])
def test_invalid_pins_are_not_silently_ignored(fixed):
    proc, env = problem()
    result = local_ir.local_hit(proc, env, {"state": 2}, "hit", fixed_inputs=fixed,
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED


def test_fixed_inputs_do_not_fill_a_missing_state():
    proc, env = problem()
    result = local_ir.local_hit(proc, env, {}, "hit", fixed_inputs={"active": True},
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED
    assert "snapshot" in result.detail


@pytest.mark.parametrize("fixed", [[7, 3], [7, 7], [0, 15], None])
def test_recursive_input_pin_uses_the_shared_value_encoding(fixed):
    proc = ir.Procedure((), ("a", "table"), (), (),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Sel(ir.Var("table"), ir.Lit(1))),
              ir.Mark("hit"), ir.Skip()),
        types={"a": types.INTEGER, "table": types.Array(2, types.INTEGER)},
        signature=ProcedureSignature((("a", FiniteType(4)),
            ("table", ValueType.array(2, ValueType.integer(FiniteType(4))))), ()))
    env = {"a": ranges.Range(0, 15), "table": ranges.Range(0, 15)}
    result = local_ir.local_hit(proc, env, {}, "hit",
                              fixed_inputs={} if fixed is None else {"table": fixed},
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    if fixed is not None:
        assert result.candidate == {"a": fixed[1], "table": fixed}
    else:
        assert result.candidate["a"] == result.candidate["table"][1]
    assert "hit" in interp.run_step(proc, result.candidate, {})[2]


def test_pins_enter_encoding_before_symbolic_construction(monkeypatch):
    proc, env = problem()
    original = local_ir._encode_query_step
    seen = []

    def checked(*args, **kwargs):
        pre = kwargs["pre"]
        seen.append(pre)
        assert pre["active"].as_long() == 0
        assert pre["b"].as_long() == 1
        assert "a" not in pre
        return original(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_encode_query_step", checked)
    result = local_ir.local_hit(proc, env, {"state": 2}, "hit",
                               fixed_inputs={"active": False, "b": 1},
        timeout_ms=60_000)
    assert seen and result.outcome is probing.Outcome.LOCAL_UNSAT

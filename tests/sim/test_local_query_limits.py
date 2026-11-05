"""An incomplete finite representation cannot prove a source goal absent."""

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import ir, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir, probing


def _problem(code="CapacityExceeded"):
    late = ir.Op("eq", ir.Var("a"), ir.Lit(3))
    body = ir.Seq(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)),
              ir.Mark("early"), ir.Skip()),
        ir.Seq(ir.If(late, ir.Fail(code, "representation-limit"), ir.Skip()),
               ir.If(late, ir.Mark("late"), ir.Skip())))
    proc = ir.Procedure((), ("a",), (), (), body,
        signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    return proc, {"a": ranges.Range(0, 3)}


@pytest.mark.parametrize("code", ["CapacityExceeded", "Unwound"])
def test_reachable_representation_limit_does_not_prove_local_unsat(code):
    proc, env = _problem(code)
    result = local_ir.local_hit_any(proc, env, {}, ("late",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNKNOWN, result.detail
    assert result.candidate is None
    assert "representation" in result.detail.lower()


def test_artifact_failure_is_a_real_terminal_path_not_an_analysis_limit():
    proc, env = _problem("IndexError")
    result = local_ir.local_hit_any(proc, env, {}, ("late",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT, result.detail


def test_a_hit_before_a_representation_limit_still_proposes_a_witness():
    proc, env = _problem()
    result = local_ir.local_hit_any(proc, env, {}, ("early",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 1}


@pytest.mark.parametrize("fixed", [True, False])
def test_representation_limit_query_keeps_the_original_input_restrictions(fixed):
    proc, env = _problem()
    options = ({"fixed_inputs": {"a": 0}} if fixed else
               {"input_constraints": lambda inputs: (inputs["a"] != 3,)})
    result = local_ir.local_hit_any(proc, env, {}, ("late",), **options,
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT, result.detail


def test_prepared_limit_checks_remain_scoped_to_the_supplied_state():
    proc, env = _problem()
    proc = ir.Procedure(("s",), proc.inputs, proc.locals, proc.outputs,
        ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(1)), proc.body,
              ir.If(ir.Lit(False), ir.Mark("late"), ir.Skip())),
        initial={"s": 0}, signature=proc.signature)
    env["s"] = ranges.Range(0, 1)
    prepared = local_ir._LocalQueryPreparation(proc, env)
    for state, expected in ((0, probing.Outcome.LOCAL_UNSAT),
                            (1, probing.Outcome.UNKNOWN),
                            (0, probing.Outcome.LOCAL_UNSAT)):
        result = prepared.probe({"s": state}, ("late",),
            timeout_ms=60_000)
        assert result.outcome is expected, result.detail


def test_completion_query_consumes_only_the_remaining_logical_budget(monkeypatch):
    proc, env = _problem()
    calls = []

    def query(proc, env, state, goals, timeout_ms, tag, **options):
        calls.append((tuple(goals), timeout_ms))
        return probing.ProbeResult(
            probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT,
            solve_ms=70 if len(calls) == 1 else 10)

    # Establish the same admitted procedure owner the real occurrence query
    # installs; the stub below isolates budget accounting, not solver semantics.
    prepared = local_ir._LocalQueryPreparation(proc, env)
    prepared.validation()
    monkeypatch.setattr(local_ir, "_query_local_hit_any", query)
    result = prepared.probe({}, ("late",), timeout_ms=100)
    assert calls == [(("late",), 100), (("representation-limit",), 30)]
    assert result.outcome is probing.Outcome.LOCAL_UNSAT
    assert result.solve_ms == 80


def test_no_budget_left_is_unknown_without_starting_completion_query(monkeypatch):
    proc, env = _problem()
    calls = []

    def query(proc, env, state, goals, timeout_ms, tag, **options):
        calls.append(tuple(goals))
        return probing.ProbeResult(
            probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT,
            solve_ms=timeout_ms)

    prepared = local_ir._LocalQueryPreparation(proc, env)
    prepared.validation()
    monkeypatch.setattr(local_ir, "_query_local_hit_any", query)
    result = prepared.probe({}, ("late",), timeout_ms=100)
    assert calls == [("late",)]
    assert result.outcome is probing.Outcome.UNKNOWN


@pytest.mark.parametrize("outcome", [probing.Outcome.CONSTRUCTING, probing.Outcome.UNKNOWN,
                                      probing.Outcome.UNSUPPORTED])
def test_an_unfinished_completion_query_cannot_become_unsat(monkeypatch, outcome):
    proc, env = _problem()

    def query(proc, env, state, goals, timeout_ms, tag, **options):
        return probing.ProbeResult(
            probing.Outcome.LOCAL_UNSAT if tuple(goals) == ("late",) else outcome,
            probing.Scope.EXACT_LOCAL_HIT, detail="unfinished", solve_ms=1)

    prepared = local_ir._LocalQueryPreparation(proc, env)
    prepared.validation()
    monkeypatch.setattr(local_ir, "_query_local_hit_any", query)
    result = prepared.probe({}, ("late",),
        timeout_ms=60_000)
    assert result.outcome is outcome

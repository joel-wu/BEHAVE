"""Exact local formula reduction preserves domains, failures and cancellation."""

import pytest
import z3

from behave.sim.symbolic.exact_formula import ExactFormula, FormulaDefinition, SliceMode
from behave.sim.symbolic import encoder, interp
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing

from .test_local_query_sharing import problem

pytestmark = pytest.mark.solver


def capture_slices(monkeypatch):
    slices = []
    original = ExactFormula.slice

    def record(self, target):
        result = original(self, target)
        slices.append((self, result))
        return result

    monkeypatch.setattr(ExactFormula, "slice", record)
    return slices


def test_closed_local_encoding_uses_reduced_relation_without_materializing_full_and(monkeypatch):
    proc, env = problem()
    selected = capture_slices(monkeypatch)

    def full_constraint(_self):
        pytest.fail("the local sliced path materialized an unused complete conjunction")

    monkeypatch.setattr(encoder.StepEncoding, "constraint", property(full_constraint))
    answer = exact.local_hit_any(proc, env, {"s": 0}, ("hit", "bad"),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.goal in interp.run_step(proc, answer.candidate, {"s": 0})[2]
    assert selected and all(f.sliced_sat_extends for f, _s in selected)
    assert all(s.mode is SliceMode.SLICED for _f, s in selected)
    assert any(s.dropped_definitions for _f, s in selected)


@pytest.mark.parametrize("state", [0, 2, 4])
@pytest.mark.parametrize("input_value", [0, 1, 2, 3])
def test_sliced_query_keeps_failure_activation_and_every_legal_input_restriction(
        monkeypatch, state, input_value):
    proc, env = problem()
    selected = capture_slices(monkeypatch)
    answer = exact.local_hit_any(
        proc, env, {"s": state}, ("hit", "bad"),
        input_constraints=lambda pre: (pre["a"] == input_value,),
        timeout_ms=60_000)
    expected = interp.run_step(proc, {"a": input_value}, {"s": state})[2]
    assert answer.outcome is (probing.Outcome.SAT if expected & {"hit", "bad"}
                             else probing.Outcome.LOCAL_UNSAT), answer.detail
    if answer.outcome is probing.Outcome.SAT:
        assert answer.candidate == {"a": input_value}
        assert answer.goal in expected
    # Check equivalence at each boundary value, not only the returned witness.
    for formula, sliced in selected:
        (a,) = formula.boundary_atoms  # The fixture has one input; state is pinned.
        for value in range(4):
            full, cut = z3.Solver(), z3.Solver()
            full.add(*formula.full_constraints, sliced.target, a == value)
            cut.add(*sliced.query_assertions, a == value)
            assert full.check() == cut.check()


def test_domain_only_definition_is_not_dropped(monkeypatch):
    proc, env = problem()
    selected, domain_terms = capture_slices(monkeypatch), []
    encode = exact._encode_query_step

    def with_domain(*args, **kwargs):
        step = encode(*args, **kwargs)
        value = z3.BitVec("local_slice_domain_only", step.pre["a"].size())
        step.definitions += (FormulaDefinition("domain-only", value, step.pre["a"]),)
        domain_terms.append(value)
        return step

    monkeypatch.setattr(exact, "_encode_query_step", with_domain)
    answer = exact.local_hit_any(
        proc, env, {"s": 0}, ("hit", "bad"),
        input_constraints=lambda _pre: (domain_terms[-1] == 2,),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate == {"a": 2}
    assert answer.goal in interp.run_step(proc, answer.candidate, {"s": 0})[2]
    assert selected and all("domain-only" in s.kept_definitions for _f, s in selected)


def test_bad_unobserved_definition_forces_complete_relation_not_false_sat(monkeypatch):
    proc, env = problem()
    selected = capture_slices(monkeypatch)
    encode = exact._encode_query_step

    def cyclic(*args, **kwargs):
        step = encode(*args, **kwargs)
        bad = z3.BitVec("local_slice_bad_dead_definition", 8)
        step.definitions += (FormulaDefinition("bad-dead", bad, bad + 1),)
        return step

    monkeypatch.setattr(exact, "_encode_query_step", cyclic)
    answer = exact.local_hit_any(proc, env, {"s": 0}, ("hit", "bad"),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.LOCAL_UNSAT
    assert selected and all(s.mode is SliceMode.FULL for _f, s in selected)
    assert all(not f.sliced_sat_extends for f, _s in selected)
    assert all("bad-dead" in s.kept_definitions for _f, s in selected)


@pytest.mark.parametrize("stage", [
    "_ordered_dependency_steps", "_dependencies", "_walk_registered", "_slice",
])
def test_registration_and_slicing_share_probe_deadline_and_never_publish_partial_proof(
        monkeypatch, stage):
    proc, env = problem()
    prepared, clock, stopped, checked = exact._LocalQueryPreparation(proc, env), [0.0], [], []
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    original = getattr(ExactFormula, stage)
    check = z3.Solver.check

    def expire(self, *args, **kwargs):
        if not stopped:
            clock[0] = .101
            stopped.append(stage)
        return original(self, *args, **kwargs)

    def queried(self, *args, **kwargs):
        checked.append(1)
        return check(self, *args, **kwargs)

    monkeypatch.setattr(ExactFormula, stage, expire)
    monkeypatch.setattr(z3.Solver, "check", queried)
    proved = set()
    first = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, _proved_unsat=proved)
    assert first.outcome is probing.Outcome.UNKNOWN
    assert first.candidate is None and not proved and not checked
    assert stopped == [stage]
    clock[0] = 0.0
    fresh = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, _proved_unsat=proved)
    assert fresh.outcome is probing.Outcome.SAT, fresh.detail
    assert fresh.goal in interp.run_step(proc, fresh.candidate, {"s": 0})[2]
    assert not proved


def test_generic_step_registration_does_not_assume_a_closed_encoder_product():
    proc, env = problem()
    step = encoder.encode_step(proc, env=env)
    assert not step.exact_formula().sliced_sat_extends

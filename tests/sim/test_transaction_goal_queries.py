"""Predicates use actual logical transactions in native and symbolic execution."""

import itertools
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic import interp
from behave.sim.behavior.task_boundary import lower_task_behavior
from behave.sim.behavior.transaction_goals import TransactionQueries, predicate_term
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome, Scope

from .test_baseline_public_sequence import _contract


def _lower(body, rules=(), *, bound=4, monitors=(), latencies=()):
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, observation_goals=rules,
                                transaction_monitors=monitors, latency_goals=latencies)
    source = "class M:\n    def reset(self):\n        pass\n    def process(self, a):\n" + body
    raw, boundary = lower_task_behavior(source, "model.py", contract, bound)
    instance = raw.bind(bound, task_contract=contract.digest,
                        catalogue_manifest=raw.catalogue_manifest()).instance
    return contract, instance, TransactionQueries(contract, boundary)


def _probe(instance, binding, previous, goal, *, position=0):
    return local_hit_any(instance.proc, instance.env, instance.proc.initial, (goal,),
        position_plan=instance._position_plan, position=position,
        observation_query=binding.at(instance.proc, previous),
        timeout_ms=60_000)


@pytest.mark.parametrize("signed", (False, True))
def test_symbolic_predicate_matches_every_small_carrier_pair(signed):
    carrier = api.FiniteType(4, signed=signed)
    endpoint = api.Endpoint("in", api.Direction.IN, [api.Field("x", carrier, "x")],
                            api.ready_valid("in", "v", "r"), clock="clk")
    current, previous = api.GoalField("x"), api.GoalField("x", previous=True)
    conditions = (
        api.GoalCondition("gt", current, previous),
        api.GoalCondition("eq", current, 16),
        api.GoalCondition("lt", current, -9),
        api.GoalCondition("and", api.GoalCondition("eq", api.GoalField("x", bit=3), True),
                          api.GoalCondition("ne", api.GoalField("x", previous=True, bit=0), False)),
    )
    low, high = carrier.bounds
    for condition in conditions:
        rule = api.TransactionGoal("test", "in", condition)
        _endpoint, concrete = rule.bind((endpoint,))
        for a, b in itertools.product(range(low, high + 1), repeat=2):
            symbolic = predicate_term(rule, endpoint, (z3.BitVecVal(a, 6),), (z3.BitVecVal(b, 6),))
            assert z3.is_true(z3.simplify(symbolic)) is concrete((a,), (b,))


def test_output_predicate_is_solved_not_sampled_and_replayed():
    rule = api.TransactionGoal("rare", "out", api.GoalCondition("eq", api.GoalField("y"), 173))
    _contract, instance, binding = _lower('        return {"y": a ^ 85}\n', (rule,))
    result = _probe(instance, binding, {}, "observation:rare")
    assert result.outcome is Outcome.SAT
    assert result.scope is Scope.EXACT_LOCAL_HIT
    assert result.candidate == {"a": 248}
    _state, outputs, _hits, failure = interp.run_step(instance.proc, result.candidate)
    assert not failure
    assert "observation:rare" in binding.observe({}, result.candidate, outputs.transactions)


def test_batch_activity_uses_previous_emission_and_ignores_padding():
    rule = api.TransactionGoal("pair", "out", api.GoalCondition("and",
        api.GoalCondition("eq", api.GoalField("y", previous=True), 7),
        api.GoalCondition("eq", api.GoalField("y"), 8)))
    _contract, instance, binding = _lower(
        '        if a == 0:\n            return None\n'
        '        return [{"y": a}, {"y": (a + 1) & 255}]\n', (rule,))
    result = _probe(instance, binding, {}, "observation:pair")
    assert result.outcome is Outcome.SAT
    assert result.candidate == {"a": 7}
    _state, outputs, _hits, failure = interp.run_step(instance.proc, result.candidate)
    assert not failure
    assert "observation:pair" in binding.observe({}, result.candidate, outputs.transactions)
    _state, empty, _hits, failure = interp.run_step(instance.proc, {"a": 0})
    assert not failure and empty.transactions == []
    assert not binding.observe({}, {"a": 0}, empty.transactions)


def test_history_survives_an_empty_batch_and_changes_the_local_question():
    rule = api.TransactionGoal("history", "out", api.GoalCondition("and",
        api.GoalCondition("eq", api.GoalField("y", previous=True), 5),
        api.GoalCondition("eq", api.GoalField("y"), 9)))
    _contract, instance, binding = _lower(
        '        if a == 0:\n            return None\n        return {"y": a}\n', (rule,))
    assert _probe(instance, binding, {}, "observation:history").outcome is Outcome.LOCAL_UNSAT
    previous = {}
    for value in (5, 0):
        _state, outputs, _hits, failure = interp.run_step(instance.proc, {"a": value})
        assert not failure
        assert not binding.observe(previous, {"a": value}, outputs.transactions)
    result = _probe(instance, binding, previous, "observation:history", position=2)
    assert result.outcome is Outcome.SAT and result.candidate == {"a": 9}


def test_accepted_input_can_hit_even_when_the_step_fails(monkeypatch):
    from behave.sim.engines import local_ir

    encode, cones = local_ir._encode_query_step, []

    def record(*args, **kwargs):
        cones.append(kwargs["keep"])
        return encode(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_encode_query_step", record)
    rule = api.TransactionGoal("zero", "in", api.GoalCondition("eq", api.GoalField("a"), 0))
    _contract, instance, binding = _lower('        return {"y": 1 // a}\n', (rule,))
    result = _probe(instance, binding, {}, "observation:zero")
    assert result.outcome is Outcome.SAT and result.candidate == {"a": 0}
    assert cones == [frozenset()]  # No source computation is needed for acceptance.
    _state, outputs, _hits, failure = interp.run_step(instance.proc, result.candidate)
    assert failure
    assert binding.observe({}, result.candidate, outputs.transactions) == {"observation:zero"}


def test_impossible_literal_is_not_truncated_into_the_output_carrier():
    rule = api.TransactionGoal("outside", "out", api.GoalCondition("eq", api.GoalField("y"), 256))
    _contract, instance, binding = _lower('        return {"y": a}\n', (rule,))
    result = _probe(instance, binding, {}, "observation:outside")
    assert result.outcome is Outcome.LOCAL_UNSAT
    assert "observation:outside" in binding.rules


def test_wide_signed_values_and_booleans_keep_their_logical_types():
    endpoint = api.Endpoint("in", "in", [
        api.Field("ready", api.ValueType.boolean(), "ready"),
        api.Field("x", api.FiniteType(64, signed=True), "x")], api.settled("in"), None)
    rule = api.TransactionGoal("wide", "in", api.GoalCondition("and",
        api.GoalCondition("eq", api.GoalField("ready"), True),
        api.GoalCondition("eq", api.GoalField("x", bit=63), True)))
    _endpoint, concrete = rule.bind((endpoint,))
    for ready, value in itertools.product((False, True), (-(1 << 63), -1, 0, (1 << 63) - 1)):
        symbolic = predicate_term(rule, endpoint, (z3.BitVecVal(int(ready), 2), z3.BitVecVal(value, 66)), None)
        assert z3.is_true(z3.simplify(symbolic)) is concrete((ready, value), None)
    assert not z3.is_true(z3.simplify(predicate_term(rule, endpoint, (1, -1), None)))
    assert concrete((1, -1), None) is None


def test_logical_vectors_keep_wide_literals_and_need_no_integer_theory():
    from behave.sim.core.observation_symbolic import logical_payload

    endpoint = api.Endpoint("out", "out", [
        api.Field("x", api.FiniteType(4, signed=True), "x"),
        api.Field("y", api.FiniteType(64), "y")], api.settled("out"), None)
    x, y = z3.BitVec("history_x", 4), z3.BitVec("history_y", 64)
    payload = logical_payload(endpoint, (x, y))
    rule = api.TransactionGoal("wide", "out", api.GoalCondition("and",
        api.GoalCondition("lt", api.GoalField("x"), -1),
        api.GoalCondition("eq", api.GoalField("y"), (1 << 64) - 1)))
    condition = predicate_term(rule, endpoint, payload, None)
    pending, seen = [condition], set()
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert z3.is_bool(term) or z3.is_bv(term)
        pending.extend(term.children())
    solver = z3.Solver()
    solver.add(condition != z3.And(x < -1, y == (1 << 64) - 1))
    assert solver.check() == z3.unsat
    invalid = api.TransactionGoal("invalid", "out",
        api.GoalCondition("eq", api.GoalField("y"), 1 << 64))
    solver = z3.Solver()
    solver.add(predicate_term(invalid, endpoint, payload, None))
    assert solver.check() == z3.unsat


def test_reference_transaction_feedback_keeps_endpoint_identity():
    from behave.sim.core.reference_progress import ReferenceProgress

    base = _contract()
    other = api.Endpoint("other", "in", [api.Field("b", api.FiniteType(8), "b")],
                         api.ready_valid("in", "other_valid", "other_ready"), clock="clk")
    rules = (
        api.TransactionGoal("in-seven", "in", api.GoalCondition("eq", api.GoalField("a"), 7)),
        api.TransactionGoal("other-nine", "other", api.GoalCondition("eq", api.GoalField("b"), 9)),
        api.TransactionGoal("out-nine", "out", api.GoalCondition("eq", api.GoalField("y"), 9)),
    )
    contract = api.TaskContract((*base.endpoints, other), base.clocks, observation_goals=rules)
    progress = ReferenceProgress(contract)
    trace = SimpleNamespace(records=(api.SlotRecord(accepted={"in": (7,), "other": (9,)}),))
    progress.observe_transactions(trace, {"out": [(9,)]})
    assert progress._transaction_hits == {"observation:" + rule.name for rule in rules}

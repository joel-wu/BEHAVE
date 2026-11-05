"""Search may witness an exact event prefix, never prove a whole goal from it."""

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir, probing

pytestmark = pytest.mark.solver


def problem(trips=4, *, failing=False, blocked=False):
    goal = ir.Fail("Assertion", "hit") if failing else ir.Mark("hit")
    body = ir._sequence((
        ir.Assign("x", ir.Var("s")),
        ir.For("i", trips, ir._sequence((
            ir.If(ir.Op("eq", ir.Var("a"), ir.Var("x")), goal, ir.Skip()),
            ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))))))))
    if blocked:
        body = ir.Seq(ir.Fail("Assertion", "earlier"), body)
    proc = ir.Procedure(("s",), ("a",), ("i", "x"), (), body,
        initial={"s": 0}, types={name: types.INTEGER for name in ("s", "a", "i", "x")},
        signature=ProcedureSignature((("a", FiniteType(3)),), ()))
    return proc, {"s": ranges.Range(0, 7), "a": ranges.Range(0, 7)}


@pytest.mark.parametrize("failing", [False, True])
def test_first_hit_does_not_wait_for_later_loop_copies(monkeypatch, failing):
    proc, env = problem(32, failing=failing)
    writes = []
    assign = encoder._Commands._assign

    def observe(self, command, *args, **kwargs):
        writes.append(command.var)
        return assign(self, command, *args, **kwargs)

    monkeypatch.setattr(encoder._Commands, "_assign", observe)
    result = local_ir._LocalQueryPreparation(proc, env).probe(
        {"s": 0}, ("hit",), _constructions={},
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 0}
    assert result.goal in interp.run_step(proc, result.candidate, {"s": 0})[2]
    assert writes.count("i") == 1
    assert writes.count("x") == 1


@pytest.mark.parametrize("value,outcome", [(0, probing.Outcome.SAT),
    (2, probing.Outcome.SAT), (7, probing.Outcome.LOCAL_UNSAT)])
def test_prefix_unsat_continues_to_later_occurrences(value, outcome):
    proc, env = problem()
    completed = set()
    result = local_ir._LocalQueryPreparation(proc, env).probe(
        {"s": 0}, ("hit",), _constructions={}, _proved_unsat=completed,
        input_constraints=lambda pre: (pre["a"] == value,),
        timeout_ms=60_000)
    assert result.outcome is outcome, result.detail
    if outcome is probing.Outcome.SAT:
        assert result.candidate == {"a": value}
        assert result.goal in interp.run_step(proc, result.candidate, {"s": 0})[2]
        assert not completed
    else:
        assert completed and result.candidate is None


def test_prefix_unknown_does_not_prevent_a_complete_answer(monkeypatch):
    proc, env = problem()
    checked, check = [], type(z3.Solver()).check

    def first_unknown(self, *args, **kwargs):
        checked.append(1)
        return z3.unknown if len(checked) == 1 else check(self, *args, **kwargs)

    monkeypatch.setattr(type(z3.Solver()), "check", first_unknown)
    result = local_ir._LocalQueryPreparation(proc, env).probe(
        {"s": 0}, ("hit",), _constructions={},
        input_constraints=lambda pre: (pre["a"] == 2,),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 2}
    assert len(checked) == 2


@pytest.mark.parametrize("prefix_answer", [z3.unsat, z3.unknown])
def test_consumed_prefix_resumes_full_query_after_its_quantum(monkeypatch, prefix_answer):
    proc, env = problem()
    clock, checked = [0.0], []
    monkeypatch.setattr(local_ir.time, "perf_counter", lambda: clock[0])
    check = type(z3.Solver()).check

    def expire_prefix(self, *args, **kwargs):
        checked.append(1)
        if len(checked) == 1:
            clock[0] += 0.101
            return prefix_answer
        return check(self, *args, **kwargs)

    monkeypatch.setattr(type(z3.Solver()), "check", expire_prefix)
    prepared, constructions, proved = local_ir._LocalQueryPreparation(proc, env), {}, set()
    options = {"timeout_ms": 100, "_constructions": constructions, "_proved_unsat": proved,
               "input_constraints": lambda pre: (pre["a"] == 2,)}
    paused = prepared.probe({"s": 0}, ("hit",), **options)
    assert paused.outcome is probing.Outcome.CONSTRUCTING
    assert paused.candidate is None and not proved
    assert next(iter(constructions.values())).encoding.prefix is None
    result = prepared.probe({"s": 0}, ("hit",), **options)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 2} and len(checked) == 2
    assert result.goal in interp.run_step(proc, result.candidate, {"s": 0})[2]


def test_prior_failure_still_blocks_the_prefix():
    proc, env = problem(blocked=True)
    result = local_ir._LocalQueryPreparation(proc, env).probe(
        {"s": 0}, ("hit",), _constructions={},
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT, result.detail
    assert result.candidate is None


@pytest.mark.parametrize("state", [0, 5])
def test_prefix_formula_implies_complete_hit_for_every_input(state):
    proc, env = problem()
    pre = local_ir.pin_state(proc, env, {"s": state})
    complete = encoder.encode_step(proc, env=env, pre=pre, tag="whole")
    owner = encoder._StepConstruction()
    keep = frozenset(id(cmd) for cmd in ir.walk(proc.body) if not isinstance(cmd, ir.Seq))
    options = dict(env=env, pre=pre, tag="prefix", keep=keep, goals=("hit",),
                   _construction=owner, _prefix_goals=True)
    first = encoder._encode_query_step(proc, **options)
    assert isinstance(first, encoder._QueryPrefix)
    assert owner.result is None
    assert not first.encoding.post and not first.encoding.state_post
    # A paused consumer gets the same immutable prefix until it consumes it.
    assert encoder._encode_query_step(proc, **options) is first
    solver = z3.Solver()
    solver.add(first.encoding.constraint, complete.constraint,
               first.encoding.pre["a"] == complete.pre["a"],
               first.encoding.goal_condition("hit"),
               z3.Not(complete.goal_condition("hit")))
    assert solver.check() == z3.unsat
    owner.prefix = None
    resumed = encoder._encode_query_step(proc, **options)
    assert isinstance(resumed, encoder.StepEncoding)
    assert owner.result is resumed
    solver = z3.Solver()
    solver.add(resumed.constraint, complete.constraint,
               resumed.pre["a"] == complete.pre["a"],
               resumed.goal_condition("hit") != complete.goal_condition("hit"))
    assert solver.check() == z3.unsat


def test_prefix_keeps_input_domain_and_fixed_snapshot():
    proc, env = problem()
    prepared = local_ir._LocalQueryPreparation(proc, env)
    for state in (0, 6, 0):
        result = prepared.probe({"s": state}, ("hit",), _constructions={},
            timeout_ms=60_000)
        assert result.outcome is probing.Outcome.SAT, result.detail
        assert result.candidate == {"a": state}
    excluded = prepared.probe({"s": 0}, ("hit",), _constructions={},
        input_constraints=lambda pre: (pre["a"] < 0,),
        timeout_ms=60_000)
    assert excluded.outcome is probing.Outcome.LOCAL_UNSAT


def test_direct_encoder_cannot_return_an_incomplete_transition():
    proc, env = problem()
    with pytest.raises(TypeError, match="owned local query"):
        encoder._encode_query_step(proc, env=env, keep=frozenset(),
                                   goals=("hit",), _prefix_goals=True)
    assert isinstance(encoder.encode_step(proc, env=env), encoder.StepEncoding)

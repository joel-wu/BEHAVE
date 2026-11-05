"""Plan-proved decisions avoid formula expansion without changing goal meaning."""

from behave.sim.symbolic import closure_proof

import itertools

import pytest
import z3

from behave.sim.behavior import goal_projection
from behave.sim.symbolic import encoder, interp, ir, plan, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir as local
from behave.sim.engines.probing import Outcome

pytestmark = pytest.mark.solver


def _procedure(*commands, state=False):
    body = ir._sequence(commands)
    written = {node.var for node in ir.walk(body) if isinstance(node, (ir.Assign, ir.Store))}
    written.update(node.index for node in ir.walk(body) if isinstance(node, ir.For))
    locals_ = tuple(sorted(written - ({"s"} if state else set())))
    return ir.Procedure(("s",) if state else (), ("a",), locals_, (),
        body, initial={"s": 0} if state else {},
        signature=ProcedureSignature((("a", FiniteType(2)),), ()))


def _prove(proc):
    env = {"a": ranges.Range(0, 3), **({"s": ranges.Range(0, 3)} if proc.state else {})}
    prepared = plan.plan_encoding(proc, env)
    assert not prepared.unresolved, prepared.unresolved
    return env, prepared, goal_projection._planned_false_goals(proc, prepared, checkpoint=lambda: None)


def test_fixed_loop_bounds_block_only_the_index_failure_not_the_declared_site(monkeypatch):
    body = ir.If(ir.Op("lor", ir.Op("lt", ir.Var("i"), ir.Lit(0)),
                            ir.Op("ge", ir.Var("i"), ir.Lit(7))),
                 ir.Fail("IndexError", "invalid-index"), ir.Mark("valid-index"))
    proc = _procedure(ir.For("i", 7, body))
    env, _prepared, false = _prove(proc)
    assert false == {"invalid-index"}
    assert not goal_projection.literal_false_goals(proc)
    declared = {c.goal for c in ir.walk(proc.body) if isinstance(c, (ir.Fail, ir.Mark))}
    assert declared == {"invalid-index", "valid-index"}

    def no_formula(*_args, **_kwargs):
        pytest.fail("a plan-proved false goal expanded a symbolic formula")

    monkeypatch.setattr(local, "_encode_query_step", no_formula)
    result = local.local_hit_any(proc, env, {}, ("invalid-index",),
        timeout_ms=60_000)
    assert result.outcome is Outcome.LOCAL_UNSAT
    assert result.candidate is None
    assert {c.goal for c in ir.walk(proc.body) if isinstance(c, (ir.Fail, ir.Mark))} == declared


@pytest.mark.parametrize("shared", [False, True])
def test_one_live_occurrence_prevents_a_false_proof(shared):
    dead = ir.Mark("goal")
    live = dead if shared else ir.Mark("goal")
    proc = _procedure(ir.For("i", 2, ir.If(ir.Op("lt", ir.Var("i"), ir.Lit(0)),
                                          dead, ir.Skip())),
                      ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), live, ir.Skip()))
    env, _prepared, false = _prove(proc)
    assert "goal" not in false
    result = local.local_hit_any(proc, env, {}, ("goal",),
        timeout_ms=60_000)
    assert result.outcome is Outcome.SAT
    assert result.goal in interp.run_step(proc, result.candidate)[2]


@pytest.mark.parametrize("trips", [0, 1, 3, None])
@pytest.mark.parametrize("stateful", [False, True])
def test_every_proved_goal_is_false_in_full_formulas_and_exhaustive_execution(trips, stateful):
    count = ir.Var("a") if trips is None else ir.Lit(trips)
    second = ir.Var("s") if stateful else ir.Var("a")
    proc = _procedure(ir.For("i", count, ir._sequence((
        ir.If(ir.Op("lt", ir.Var("i"), ir.Lit(0)), ir.Mark("negative"), ir.Skip()),
        ir.If(ir.Op("ge", ir.Var("i"), ir.Lit(3)), ir.Mark("overshoot"), ir.Mark("inside")),
        ir.If(ir.Op("eq", ir.Var("i"), second), ir.Mark("equal"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), ir.Fail("Stopped", "failure"), ir.Skip()),
        ir.Mark("after")))), state=stateful)
    env, prepared, false = _prove(proc)
    full = encoder.encode_step(proc, env=env, plan=prepared)
    for goal in false:
        solver = z3.Solver()
        solver.add(full.constraint, full.goal_condition(goal))
        assert solver.check() == z3.unsat, goal
    for a, s in itertools.product(range(4), range(4) if stateful else [None]):
        state = {"s": s} if stateful else {}
        hits = interp.run_step(proc, {"a": a}, state)[2]
        assert not false & hits
    for goal in ("negative", "overshoot", "inside", "equal", "failure", "after"):
        result = local.local_hit_any(proc, env, {"s": 0} if stateful else {}, (goal,),
            timeout_ms=60_000)
        possible = any(goal in interp.run_step(proc, {"a": a}, {"s": 0} if stateful else {})[2]
                       for a in range(4))
        assert (result.outcome is Outcome.SAT) == possible
        if not possible:
            assert result.outcome is Outcome.LOCAL_UNSAT


def test_missing_occurrence_carriers_are_not_unreachable_evidence():
    proc = _procedure(ir.For("i", 2, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                                          ir.Mark("yes"), ir.Mark("no"))))
    _env, prepared, false = _prove(proc)
    assert not false
    # This private helper does not admit plans. Missing data can only reduce
    # optimization: both arms must remain possible, not become silently dead.
    sparse = prepared.replaced(occurrence_carriers={})
    assert not goal_projection._planned_false_goals(proc, sparse, checkpoint=lambda: None)


def test_an_interrupted_projection_is_not_published(monkeypatch):
    proc = _procedure(ir.For("i", 3, ir.If(ir.Op("lt", ir.Var("i"), ir.Lit(0)),
                                          ir.Mark("bad"), ir.Mark("good"))))
    env, prepared, _false = _prove(proc)
    owner = local._LocalQueryPreparation(proc, env)
    owner._plan = prepared

    def interrupted():
        raise local._ProbeDeadlineExceeded

    with pytest.raises(local._ProbeDeadlineExceeded):
        owner.false_goals(prepared, interrupted)
    assert owner._plan_false_goals is None
    assert owner.false_goals(prepared, lambda: None) == {"bad"}


def test_position_plan_does_not_borrow_a_different_positions_proof():
    proc = _procedure(ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(0)),
                            ir.Mark("zero"), ir.Mark("nonzero")),
                      ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1))), state=True)
    env = {"a": ranges.Range(0, 3), "s": ranges.Range(0, 2)}
    positioned = closure_proof.prepare_positions(proc, env, 2)
    owner = local._LocalQueryPreparation(proc, env)
    for position in (0, 1):
        options = {"position_plan": positioned, "position": position}
        for goal in ("zero", "nonzero"):
            result = owner.probe({"s": position}, (goal,), **options,
                timeout_ms=60_000)
            assert (result.outcome is Outcome.SAT) == (goal == ("zero" if position == 0 else "nonzero"))
    assert owner._plan is None and owner._plan_false_goals is None


def test_unknown_declared_input_domain_or_missing_goal_is_not_a_false_proof():
    proc = _procedure(ir.For("i", 2, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                                          ir.Mark("yes"), ir.Mark("no"))))
    env, _prepared, false = _prove(proc)
    assert not false
    assert local.local_hit_any(proc, env, {}, ("absent",),
        timeout_ms=60_000).outcome is Outcome.UNSUPPORTED
    assert local.local_hit_any(proc, {"a": ranges.Range(0, 0)}, {}, ("yes",),
        timeout_ms=60_000).outcome is Outcome.UNSUPPORTED


@pytest.mark.parametrize("converted", [False, True])
def test_compiler_temporaries_keep_every_definition_not_just_one_assignment(converted):
    from behave.sim.symbolic import cfg

    guard = ir.Op("lt", ir.Var("i"), ir.Lit(0))
    body = ir._sequence((ir.Assign("flag", ir.Lit(False)),
        ir.For("i", 3, ir._sequence((
            ir.Assign("bad", guard),
            ir.If(ir.Var("bad"), ir.Mark("index-error"), ir.Skip()),
            ir.Assign("flag", ir.Op("eq", ir.Var("a"), ir.Var("i"))),
            ir.If(ir.Var("flag"), ir.Mark("live"), ir.Skip()))))))
    if converted:
        body = cfg.if_convert(cfg.of_command(body))
    proc = _procedure(body)
    env, prepared, false = _prove(proc)
    assert "index-error" in false and "live" not in false
    full = encoder.encode_step(proc, env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(full.constraint, full.goal_condition("index-error"))
    assert solver.check() == z3.unsat
    for a in range(4):
        hits = interp.run_step(proc, {"a": a})[2]
        assert not false & hits
        assert ("live" in hits) == (a < 3)


def test_real_lowering_keeps_source_index_goals_but_needs_no_loop_formula(monkeypatch):
    from behave.sim.behavior.lowering import lower_source

    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        table = [11, 22, 33, 44, 55, 66, 77]
        result = a
        for k in range(1, 7):
            if a < table[k - 1]:
                result = table[k]
        return {"y": result}
'''
    instance = lower_source(source, input_bounds={"a": (0, 3)}, bound=1)
    proc, env = instance.proc, instance.proc.state_ranges
    goals = tuple(name for name in instance.catalogue if name.endswith(":indexerror"))
    assert len(goals) == 2
    before = set(instance.catalogue)

    def no_formula(*_args, **_kwargs):
        pytest.fail("a lowered fixed-loop index proof expanded the loop")

    monkeypatch.setattr(local, "_encode_query_step", no_formula)
    answer = local.local_hit_any(proc, env, proc.initial, goals,
        timeout_ms=60_000)
    assert answer.outcome is Outcome.LOCAL_UNSAT
    assert set(instance.catalogue) == before
    for a in range(4):
        assert not set(goals) & interp.run_step(proc, {"a": a}, proc.initial)[2]

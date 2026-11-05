"""Exact plans/prefixes share construction, never merge query domains or verdicts."""

import pytest
import z3

from behave.sim.behavior import residual
from behave.sim.symbolic import encoder, plan, ranges

from .test_occurrence_query_reuse import _loop_proc, _single_site_fragments

pytestmark = pytest.mark.solver


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize("fixed", [False, True])
def test_growing_windows_reuse_only_unchanged_transitions(stateful, fixed, monkeypatch):
    from collections import Counter

    proc = _loop_proc(stateful=stateful)
    env = {"a": ranges.Range(-1, 4), **({"s": ranges.Range(0, 4)} if stateful else {})}
    goals = ("hit", "bad", "never")
    values = tuple({"a": value} for value in (0, -1, 2, 0, 1, 4, 0, 2)) if fixed else None
    cold = residual.unroll(proc, env, 8, goals=goals, _input_values=values)
    encode, starts = encoder._encode_step_iter, Counter()

    def counted(*args, **kwargs):
        starts[kwargs["tag"]] += 1
        return (yield from encode(*args, **kwargs))

    monkeypatch.setattr(encoder, "_encode_step_iter", counted)
    previous, prior_run = None, None
    for length in (2, 4, 8):
        owner = residual._RunConstruction(previous)
        run = residual.unroll(proc, env, length, goals=goals, _construction=owner,
                              _input_values=values[:length] if fixed else None)
        # A goal-only terminal cone omits the state update needed by the next
        # step. Extending a stateful horizon must rebuild that former terminal
        # transition, while preserving the identical leading equations.
        reused = 0 if prior_run is None else prior_run.steps - int(stateful)
        assert owner.reused_steps == reused
        if prior_run is not None:
            assert all(a is b for a, b in zip(
                prior_run.encodings[:reused], run.encodings[:reused], strict=True))
            if stateful:
                assert prior_run.encodings[-1] is not run.encodings[prior_run.steps - 1]
        assert prior_run is None or prior_run.steps == len(prior_run.encodings)
        previous, prior_run = owner, run
    assert starts == {f"@{step}": 1 + int(stateful and step in (1, 3))
                      for step in range(8)}
    assert z3.eq(run.constraint, cold.constraint)
    for goal in goals:
        assert z3.eq(run.goal_condition(goal), cold.goal_condition(goal))
    assert all(z3.eq(a, b) for a, b in zip(run.halted, cold.halted, strict=True))
    assert all(z3.eq(a, b) for a, b in zip(run.errors, cold.errors, strict=True))


@pytest.mark.parametrize("change", ["inputs", "goals", "environment", "mutable_environment", "source", "initial", "reset", "horizon"])
def test_a_prefix_cannot_change_the_question_it_replaces(change):
    proc = _loop_proc(stateful=True)
    env = {"a": ranges.Range(-1, 4), "s": ranges.Range(0, 4)}
    goals, values = ("hit", "bad", "never"), ({"a": -1}, {"a": 0})
    first = residual._RunConstruction()
    residual.unroll(proc, env, 2, goals=goals, _input_values=values, _construction=first)
    arguments = dict(proc=proc, env=env, steps=4, goals=goals,
                     _input_values=values + ({"a": 0}, {"a": 0}))
    if change == "inputs":
        arguments["_input_values"] = ({"a": 0},) * 4
    elif change == "goals":
        arguments["goals"] = ("never",)
    elif change == "environment":
        arguments["env"] = {**env, "a": ranges.Range(-1, 3)}
    elif change == "mutable_environment":
        env["a"].hi = 3
    elif change == "source":
        arguments["proc"] = _loop_proc(stateful=True)
    elif change == "initial":
        arguments["initial"] = {"s": z3.BitVecVal(3, 4)}
    elif change == "reset":
        proc.initial["s"] = 3
    elif change == "horizon":
        arguments.update(steps=1, _input_values=values[:1])
    cold = residual.unroll(**arguments)
    owner = residual._RunConstruction(first)
    actual = residual.unroll(**arguments, _construction=owner)
    assert owner.reused_steps == 0
    assert z3.eq(actual.constraint, cold.constraint)
    assert all(z3.eq(actual.goal_condition(goal), cold.goal_condition(goal))
               for goal in arguments["goals"])


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize("steps", [1, 3])
def test_fragments_reuse_exact_environment_plans_and_preserve_formulas(
        stateful, steps, monkeypatch):
    proc = _loop_proc(stateful=stateful)
    env = {"a": ranges.Range(-1, 4), **({"s": ranges.Range(0, 4)} if stateful else {})}
    goals = ("hit", "bad", "never")
    fragments = [(goal, part) for goal in goals
                 for part in _single_site_fragments(proc, goal, steps)]
    fresh = [residual.unroll(proc, env, steps, goals=(goal,), _occurrence=part)
             for goal, part in fragments]
    prepared = residual._prepare_unroll(proc, env, steps, None)
    derive, builds = plan.plan_encoding, []

    def counted(procedure, environment, *args, **kwargs):
        builds.append(plan._environment_image(environment))
        return derive(procedure, environment, *args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", counted)
    for (goal, part), expected in zip(fragments, fresh, strict=True):
        actual = residual.unroll(proc, env, steps, goals=(goal,),
                                 _occurrence=part, _prepared=prepared)
        assert actual is not expected
        assert z3.eq(actual.constraint, expected.constraint)
        assert z3.eq(actual.goal_condition(goal), expected.goal_condition(goal))
        assert all(z3.eq(a, b) for a, b in zip(actual.halted, expected.halted, strict=True))
    assert builds and len(builds) == len(set(builds))
    assert len(builds) < len(fragments)
    if not stateful:
        assert len(builds) == 1
    else:
        # Wider successor terms need their own plan, not the reset carrier.
        assert len(builds) > 1 if steps > 1 else len(builds) == 1


def test_plan_key_owns_range_values_and_keeps_cell_precision():
    proc = _loop_proc()
    owner = encoder._procedure_validation(proc)
    env = {"a": ranges.Range(-1, 4)}
    first = plan.reuse_encoding_plan(owner, env)
    env["a"].hi = 8
    second = plan.reuse_encoding_plan(owner, env)
    assert second is not first
    assert first.env["a"].hi == 4
    assert plan.reuse_encoding_plan(owner, {"a": ranges.Range(-1, 4)}) is first
    left = {**env, "scratch": ranges._CellRange((ranges.Range(0, 0), ranges.Range(1, 1)))}
    right = {**env, "scratch": ranges._CellRange((ranges.Range(1, 1), ranges.Range(0, 0)))}
    assert (left["scratch"].lo, left["scratch"].hi) == (right["scratch"].lo, right["scratch"].hi)
    assert plan.reuse_encoding_plan(owner, left) is not plan.reuse_encoding_plan(owner, right)
    assert plan.reuse_encoding_plan(encoder._procedure_validation(proc), left) is not plan.reuse_encoding_plan(owner, left)


def test_literal_entry_plans_keep_distinct_state_and_goal_answers(monkeypatch):
    proc = _loop_proc(stateful=True)
    env = {"a": ranges.Range(-1, 4), "s": ranges.Range(0, 4)}
    prepared = residual._prepare_unroll(proc, env, 1, None)
    derive, calls = plan.plan_encoding, []

    def counted(*args, **kwargs):
        calls.append(1)
        return derive(*args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", counted)
    for initial, expected in ((0, z3.sat), (4, z3.unsat), (0, z3.sat)):
        run = residual.unroll(proc, env, 1, initial={"s": z3.BitVecVal(initial, 4)},
                              _prepared=prepared)
        solver = z3.Solver()
        solver.add(run.constraint, run.encodings[0].pre["a"] == 0, run.goal_condition("hit"))
        assert solver.check() == expected
    # Literal entry states get exact plans; revisiting the same literal state
    # reuses its plan, rather than treating both as arbitrary 4-bit values.
    assert len(calls) == 2


def test_failed_derivation_is_not_published(monkeypatch):
    proc = _loop_proc()
    owner = encoder._procedure_validation(proc)
    derive, calls = plan.plan_encoding, []

    def once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise encoder.EncodingError("interrupted plan")
        return derive(*args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", once)
    with pytest.raises(encoder.EncodingError, match="interrupted plan"):
        plan.reuse_encoding_plan(owner, {"a": ranges.Range(-1, 4)})
    result = plan.reuse_encoding_plan(owner, {"a": ranges.Range(-1, 4)})
    assert result is plan.reuse_encoding_plan(owner, {"a": ranges.Range(-1, 4)})
    assert len(calls) == 2


def test_cached_plan_does_not_accept_another_procedure_or_explicit_wrong_plan():
    proc, another = _loop_proc(), _loop_proc(trips=3)
    env = {"a": ranges.Range(-1, 4)}
    owner = encoder._procedure_validation(proc)
    plan.reuse_encoding_plan(owner, env)
    with pytest.raises(TypeError, match="another procedure"):
        encoder._encode_step(another, env=env, _procedure_validation=owner)
    with pytest.raises(TypeError, match="another procedure"):
        residual._prepare_unroll(another, env, 2, None, _procedure_validation=owner)
    wrong = plan.plan_encoding(proc, {"a": ranges.Range(-1, 8)})
    with pytest.raises(plan.PlanMismatch, match="environment"):
        encoder._encode_step(proc, env=env, plan=wrong, _procedure_validation=owner)


def _dispatched_loop():
    from behave.sim.symbolic import ir, typecheck as types

    body = ir._sequence([
        ir.Assign("y", ir.Var("x")),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), ir.Mark("idle"),
              ir.For("i", ir.Lit(8), ir._sequence([
                  ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)),
                        ir.Fail("DivZero", "bad"), ir.Skip()),
                  ir.Assign("y", ir.Op("add", ir.Var("y"), ir.Lit(1))),
                  ir.Mark("work")]))),
        ir.Mark("done")])
    proc = ir.Procedure([], ["a", "x"], ["y", "i"], ["y"], body,
                        types={name: types.INTEGER for name in ("a", "x", "y", "i")})
    return proc, {"a": ranges.Range(-1, 3), "x": ranges.Range(0, 3)}


@pytest.mark.parametrize("value", range(-1, 4))
def test_conditioned_plans_preserve_outputs_failures_and_every_goal(value):
    proc, env = _dispatched_loop()
    broad = plan.plan_encoding(proc, env)
    exact = plan.plan_encoding(proc, env, fixed_inputs={"a": value})
    assert exact.complete and exact.validate(proc, env) is exact
    assert exact.widths == broad.widths and exact.env == broad.env
    if value == 0:
        assert not exact.loop_bounds and exact.expansion_cost < broad.expansion_cost
    boundary = {"a": z3.BitVecVal(value, broad.widths["a"]),
                "x": z3.BitVec("payload", broad.widths["x"])}
    cold = encoder.encode_step(proc, pre=boundary, env=env, plan=broad, tag="broad")
    warm = encoder.encode_step(proc, pre=boundary, env=env, plan=exact, tag="bound")
    auto = encoder.encode_step(proc, pre=boundary, env=env, tag="implicit")
    assert set(cold.hits) == set(warm.hits) == set(auto.hits)
    for actual in (warm, auto):
        solver = z3.Solver()
        solver.add(cold.constraint, actual.constraint)
        assert solver.check() == z3.sat
        different = [cold.ok != actual.ok, cold.error != actual.error]
        different.extend(cold.hits[g] != actual.hits[g] for g in cold.hits)
        different.append(z3.And(cold.ok, z3.BV2Int(cold.post["y"], True)
                                != z3.BV2Int(actual.post["y"], True)))
        solver.add(z3.Or(*different))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize("supplied", [None, "symbol", "different", "wrong-width"])
def test_conditioned_plan_cannot_be_transplanted_to_another_input(supplied):
    proc, env = _dispatched_loop()
    exact = plan.plan_encoding(proc, env, fixed_inputs={"a": 0})
    width = exact.widths["a"]
    pre = ({"a": z3.BitVec("command", width)} if supplied == "symbol" else
           {"a": z3.BitVecVal(1, width)} if supplied == "different" else
           {"a": z3.BitVecVal(0, width + 1)} if supplied == "wrong-width" else None)
    with pytest.raises((plan.PlanMismatch, encoder.EncodingError)):
        encoder.encode_step(proc, pre=pre, env=env, plan=exact)


def test_conditioned_plan_identity_and_cache_include_literal_bindings():
    import pickle

    proc, env = _dispatched_loop()
    owner = encoder._procedure_validation(proc)
    bindings = {"a": 0}
    idle = plan.reuse_encoding_plan(owner, env, fixed_inputs=bindings)
    bindings["a"] = 1
    work = plan.reuse_encoding_plan(owner, env, fixed_inputs=bindings)
    assert idle is plan.reuse_encoding_plan(owner, env, fixed_inputs={"a": 0})
    assert idle.fixed_inputs == {"a": 0}
    assert idle.fingerprint != work.fingerprint
    assert idle.fingerprint != plan.reuse_encoding_plan(owner, env).fingerprint
    assert idle.replaced().fixed_inputs == idle.fixed_inputs
    restored = pickle.loads(pickle.dumps(idle))
    assert restored == idle and restored.fixed_inputs == {"a": 0}
    assert restored.validate(proc, env) is restored
    with pytest.raises(TypeError):
        plan.reuse_encoding_plan(owner, env, fixed_inputs={"a": True})
    with pytest.raises(plan.PlanMismatch):
        idle.replaced(fixed_inputs={}).validate(proc, env)


@pytest.mark.parametrize("bindings", [{"absent": 0}, {"y": 0}, {"a": 4},
                                       {"a": -2}, {"a": True}, {1: 0}])
def test_conditioned_plan_checks_the_unchanged_public_support(bindings):
    proc, env = _dispatched_loop()
    with pytest.raises((plan.PlanMismatch, TypeError)):
        plan.plan_encoding(proc, env, fixed_inputs=bindings)


def test_cancelled_conditioned_derivation_does_not_enter_the_shared_cache():
    proc, env = _dispatched_loop()
    owner = encoder._procedure_validation(proc)

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        plan.reuse_encoding_plan(owner, env, fixed_inputs={"a": 0}, _checkpoint=cancel)
    assert not owner._implicit_plans
    exact = plan.reuse_encoding_plan(owner, env, fixed_inputs={"a": 0})
    assert exact.fixed_inputs == {"a": 0}

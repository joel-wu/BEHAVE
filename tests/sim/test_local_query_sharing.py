"""Local sharing saves construction, never occurrence or replay obligations."""

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, ir, ranges, slicing, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation

from .test_occurrence_query_reuse import _loop_proc


def test_state_grouping_tracks_control_stores_and_cross_transaction_dependencies():
    def sequence(*commands):
        return ir._sequence(commands)

    body = sequence(
        ir.If(ir.Var("command"), ir.Assign("left", ir.Var("data")), ir.Skip()),
        ir.If(ir.Var("command"), ir.Assign("right", ir.Var("data")), ir.Skip()),
        ir.If(ir.Var("left"), ir.Mark("left-goal"), ir.Skip()),
        ir.If(ir.Var("right"), ir.Mark("right-goal"), ir.Skip()),
        ir.Store("memory", ir.Var("right"), ir.Var("data")),
        ir.Assign("value", ir.Sel(ir.Var("memory"), ir.Var("left"))),
        ir.If(ir.Var("value"), ir.Mark("memory-goal"), ir.Skip()),
        ir.For("i", ir.Var("left"), ir.If(ir.Var("i"), ir.Mark("loop-goal"), ir.Skip())),
        ir.If(ir.Var("data"), ir.Mark("input-goal"), ir.Skip()))
    states = ("left", "right", "memory")
    proc = ir.Procedure(states, ("command", "data"), ("value", "i"), (), body)
    actual = slicing.goal_state_dependencies(proc)
    assert actual == {"left-goal": 1, "right-goal": 2, "memory-goal": 7,
                      "loop-goal": 1, "input-goal": 0}
    # A successor assignment can connect states for a later transaction even
    # though that assignment occurs after the source goal in this transaction.
    proc.body = sequence(body, ir.Assign("left", ir.Var("right")))
    assert slicing.goal_state_dependencies(proc)["left-goal"] == 3


def test_state_grouping_does_not_walk_the_body_for_each_goal(monkeypatch):
    width = 256
    states = tuple(f"s{index}" for index in range(width))
    body = ir._sequence(tuple(ir.If(ir.Var(name), ir.Mark(f"g{index}"), ir.Skip())
                              for index, name in enumerate(states)))
    proc = ir.Procedure(states, (), (), (), body)
    original, calls = slicing.reads, []

    def counted(expression, out):
        calls.append(expression)
        return original(expression, out)

    monkeypatch.setattr(slicing, "reads", counted)
    assert slicing.goal_state_dependencies(proc) == {f"g{i}": 1 << i for i in range(width)}
    assert len(calls) == width


def test_state_grouping_keeps_reaching_versions_not_every_same_name_assignment():
    body = ir._sequence((
        ir.Assign("scratch", ir.Var("left")),
        ir.If(ir.Var("scratch"), ir.Mark("early"), ir.Skip()),
        ir.Assign("scratch", ir.Var("right")),
        ir.If(ir.Var("scratch"), ir.Mark("late"), ir.Skip()),
        ir.Assign("scratch", ir.Lit(0)),
        ir.If(ir.Var("scratch"), ir.Mark("cleared"), ir.Skip())))
    proc = ir.Procedure(("left", "right"), (), ("scratch",), (), body)
    assert slicing.goal_state_dependencies(proc) == {"early": 1, "late": 2, "cleared": 0}


def test_state_grouping_branch_join_retains_control_and_unwritten_arm():
    body = ir._sequence((
        ir.If(ir.Var("gate"), ir.Assign("scratch", ir.Var("left")),
              ir.Assign("scratch", ir.Var("right"))),
        ir.If(ir.Var("scratch"), ir.Mark("both-arms"), ir.Skip()),
        ir.Assign("scratch", ir.Var("left")),
        ir.If(ir.Var("gate"), ir.Assign("scratch", ir.Lit(0)), ir.Skip()),
        ir.If(ir.Var("scratch"), ir.Mark("one-arm"), ir.Skip())))
    proc = ir.Procedure(("left", "right", "gate"), (), ("scratch",), (), body)
    assert slicing.goal_state_dependencies(proc) == {"both-arms": 7, "one-arm": 5}


def test_state_grouping_loop_backedge_reaches_earlier_goal_without_scalar_pollution():
    body = ir._sequence((
        ir.Assign("scratch", ir.Lit(0)),
        ir.For("i", ir.Var("count"), ir._sequence((
            ir.If(ir.Var("scratch"), ir.Mark("loop"), ir.Skip()),
            ir.Assign("scratch", ir.Var("right"))))),
        ir.Assign("scratch", ir.Var("left")),
        ir.If(ir.Var("scratch"), ir.Mark("after"), ir.Skip())))
    proc = ir.Procedure(("left", "right", "count"), (), ("scratch", "i"), (), body)
    assert slicing.goal_state_dependencies(proc) == {"loop": 6, "after": 1}


def test_state_grouping_shared_ir_occurrences_keep_their_distinct_reaching_values():
    shared = ir.If(ir.Var("scratch"), ir.Mark("shared"), ir.Skip())
    body = ir._sequence((ir.Assign("scratch", ir.Var("left")), shared,
                         ir.Assign("scratch", ir.Var("right")), shared))
    proc = ir.Procedure(("left", "right"), (), ("scratch",), (), body)
    assert slicing.goal_state_dependencies(proc) == {"shared": 3}


def test_dependency_families_precede_unions_without_dropping_or_renumbering_goals():
    from types import SimpleNamespace

    manifest = {goal: SimpleNamespace(cls="safety" if goal == "bad" else "coverage")
                for goal in ("combined", "input-only", "right", "left", "bad")}
    supports = dict(zip(manifest, (3, 0, 2, 1, 0), strict=True))
    groups = generation._remaining_goal_groups(manifest, tuple(manifest), dependencies=supports)
    assert groups == ((0, ("input-only",)), (1, ("right",)), (2, ("left",)),
                      (3, ("combined",)), (4, ("bad",)))
    assert generation._remaining_goal_groups(manifest, tuple(manifest),
        hits=("input-only", "left"), dependencies=supports) == tuple(groups[i] for i in (1, 3, 4))


def test_prepared_state_grouping_is_source_owned_and_shared_across_snapshots(monkeypatch):
    proc, env = problem()
    owner = exact._LocalQueryPreparation(proc, env)
    original, calls = slicing.goal_state_dependencies, []

    def counted(procedure):
        calls.append(procedure)
        return original(procedure)

    monkeypatch.setattr(slicing, "goal_state_dependencies", counted)
    first = owner.state_dependencies()
    assert owner.state_dependencies() is first
    assert owner.for_state({"s": 1}).state_dependencies() is first
    assert calls == [proc]


def test_compact_planning_retains_interleaved_target_selections(monkeypatch):
    proc, env = overlapping_problem()
    owner = exact._LocalQueryPreparation(proc, env)
    events = owner.events()
    occurrences = tuple((site, cmd.goal) for site, (cmd, _) in enumerate(events)
                        if isinstance(cmd, ir.Mark))
    original, started = slicing.SliceIndex._occurrence_masks, []

    def counted(self, members):
        started.append(tuple(members))
        return (yield from original(self, members))

    monkeypatch.setattr(slicing.SliceIndex, "_occurrence_masks", counted)
    checkpoints = []

    def pause():
        checkpoints.append(None)
        if len(checkpoints) == 5:
            raise exact._ProbeDeadlineExceeded

    with pytest.raises(exact._ProbeDeadlineExceeded):
        owner.components(occurrences, pause)
    owner.components(occurrences[:1], lambda: None)
    result = owner.components(occurrences, lambda: None)
    assert len(result) == 1 and result[0][1] == occurrences
    assert owner.components(occurrences, lambda: None) is result
    assert started == [occurrences, occurrences[:1]]


def problem(trips=2, stateful=True, failure_goals=True):
    template = _loop_proc(trips, stateful, failure_goals)
    proc = ir.Procedure(
        template.state, template.inputs, (*template.locals, *template.outputs), (),
        template.body, types=template.types, initial=template.initial,
        signature=ProcedureSignature(
            (("a", FiniteType(2)),), ()))
    env = {"a": ranges.Range(0, 3)}
    if stateful:
        env["s"] = ranges.Range(0, 4)
    return proc, env


@pytest.mark.parametrize("guard", [ir.Lit(False),
    ir.Op("lt", ir.Var("i"), ir.Lit(0)),
    ir.Op("ge", ir.Var("i"), ir.Lit(0)),
    ir.Op("lt", ir.Var("a"), ir.Lit(0))])
def test_proved_guard_is_not_encoded_then_discarded(monkeypatch, guard):
    body = ir.For("i", ir.Lit(3), ir.If(guard, ir.Mark("true"),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(3)),
              ir.Fail("Stopped", "bad"), ir.Mark("false"))))
    proc = ir.Procedure((), ("a",), ("i",), (), body,
        types={name: types.INTEGER for name in ("a", "i")},
        signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    original = encoder._Commands.expr

    def checked(self, expression, state):
        assert expression is not guard, "an occurrence-proved decision built unused SSA"
        return original(self, expression, state)

    monkeypatch.setattr(encoder._Commands, "expr", checked)
    encoded = encoder.encode_step(proc, env={"a": ranges.Range(0, 3)})
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for a in range(4):
        _, _, hits, failure = interp.run_step(proc, {"a": a})
        solver.push()
        solver.add(encoded.pre["a"] == a)
        assert solver.check() == z3.sat
        model = solver.model()
        assert {g for g, term in encoded.hits.items()
                if z3.is_true(model.eval(term))} == hits
        assert z3.is_true(model.eval(encoded.ok)) == (failure == "")
        solver.pop()


def counted_builds(monkeypatch):
    built = []
    original = exact._encode_query_step

    def build(*args, **kwargs):
        enc = original(*args, **kwargs)
        built.append((kwargs, enc))
        return enc

    monkeypatch.setattr(exact, "_encode_query_step", build)
    return built


def overlapping_problem(*, state=0, failure=False):
    def chain(*commands):
        result = commands[-1]
        for command in reversed(commands[:-1]):
            result = ir.Seq(command, result)
        return result

    proc = ir.Procedure(("s",), ("a",), ("x",), (), chain(
        ir.Assign("x", ir.Op("add", ir.Var("a"), ir.Var("s"))),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("early"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)),
              ir.Fail("Stopped", "bad") if failure else ir.Mark("middle"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(3)), ir.Mark("late"), ir.Skip())),
        initial={"s": state}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    return proc, {"a": ranges.Range(0, 3), "s": ranges.Range(0, 4)}


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("state", [0, 4])
def test_overlapping_cones_build_once_with_pointwise_exact_hits(monkeypatch, failure, state):
    proc, env = overlapping_problem(state=state, failure=failure)
    goals = ("early", "bad" if failure else "middle", "late")
    built = counted_builds(monkeypatch)
    result = exact.local_hit_any(proc, env, {"s": state}, goals,
        timeout_ms=60_000)
    assert result.outcome is (probing.Outcome.SAT if state == 0
                             else probing.Outcome.LOCAL_UNSAT), result.detail
    assert len(built) == 1
    options, shared = built[0]
    assert set(options["goals"]) == set(goals)
    full = encoder.encode_step(proc, env=env, pre=exact.pin_state(proc, env, {"s": state}),
                               tag="full")
    solver = z3.Solver()
    solver.add(shared.constraint, full.constraint, shared.pre["a"] == full.pre["a"])
    solver.add(z3.Or(*(shared.goal_condition(g) != full.goal_condition(g) for g in goals)))
    assert solver.check() == z3.unsat
    for value in range(4):
        hits = interp.run_step(proc, {"a": value}, {"s": state})[2]
        solver = z3.Solver()
        solver.add(shared.constraint, shared.pre["a"] == value)
        solver.add(z3.Or(*(shared.goal_condition(g) != (g in hits) for g in goals)))
        assert solver.check() == z3.unsat
    if result.candidate is not None:
        assert result.goal in interp.run_step(proc, result.candidate, {"s": state})[2]


@pytest.mark.parametrize("state", [0, 4])
def test_unsupported_union_refines_without_losing_an_answerable_member(monkeypatch, state):
    proc, env = overlapping_problem(state=state)
    original, built = exact._encode_query_step, []

    def reject_larger(*args, **kwargs):
        built.append(kwargs["goals"])
        if len(kwargs["goals"]) > 1:
            raise encoder.EncodingError("fixture union unavailable", kind="range")
        return original(*args, **kwargs)

    monkeypatch.setattr(exact, "_encode_query_step", reject_larger)
    prepared, constructions, proved = exact._LocalQueryPreparation(proc, env), {}, set()
    for _ in range(2):
        result = prepared.probe({"s": state}, ("early", "late"),
                                _constructions=constructions, _proved_unsat=proved,
            timeout_ms=60_000)
        assert result.outcome is (probing.Outcome.SAT if state == 0
                                 else probing.Outcome.LOCAL_UNSAT), result.detail
        if result.candidate is not None:
            assert result.goal in interp.run_step(proc, result.candidate, {"s": state})[2]
    assert sum(len(goals) > 1 for goals in built) == 1


@pytest.mark.parametrize("state", [0, 4])
def test_lazy_singleton_fallback_can_pause_and_resume_without_losing_members(monkeypatch, state):
    proc, env = overlapping_problem(state=state)
    original, builds = exact._encode_query_step, []
    clock, pause = [0.0], [True]
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])

    def reject_union(*args, **kwargs):
        builds.append(kwargs["goals"])
        if len(kwargs["goals"]) > 1:
            raise encoder.EncodingError("fixture union unavailable", kind="range")
        return original(*args, **kwargs)

    walk = slicing.SliceIndex.occurrence_batch

    def delayed(self, members):
        for row in walk(self, members):
            if pause[0]:
                clock[0] += 0.101
                pause[0] = False
            yield row

    monkeypatch.setattr(exact, "_encode_query_step", reject_union)
    monkeypatch.setattr(slicing.SliceIndex, "occurrence_batch", delayed)
    owner = exact._LocalQueryPreparation(proc, env)
    constructions, proved = {}, set()
    first = owner.probe({"s": state}, ("early", "late"), timeout_ms=100,
                        _constructions=constructions, _proved_unsat=proved)
    assert first.outcome is probing.Outcome.CONSTRUCTING
    assert first.candidate is None and not proved
    result = owner.probe({"s": state}, ("early", "late"), timeout_ms=100,
                         _constructions=constructions, _proved_unsat=proved)
    assert result.outcome is (probing.Outcome.SAT if state == 0
                             else probing.Outcome.LOCAL_UNSAT), result.detail
    assert sum(len(goals) > 1 for goals in builds) == 1
    if result.candidate is not None:
        assert result.goal in interp.run_step(proc, result.candidate, {"s": state})[2]


@pytest.mark.parametrize("state", [0, 4])
@pytest.mark.parametrize("failure", [False, True])
def test_compact_components_match_every_input_and_target_subset(state, failure):
    proc, env = overlapping_problem(state=state, failure=failure)
    goals = ("early", "bad" if failure else "middle", "late")
    owner = exact._LocalQueryPreparation(proc, env)
    for value in range(4):
        hits = interp.run_step(proc, {"a": value}, {"s": state})[2]
        for size in range(1, 4):
            for selected in itertools.combinations(goals, size):
                result = owner.probe({"s": state}, selected, fixed_inputs={"a": value},
                    timeout_ms=60_000, _constructions={}, _proved_unsat=set())
                assert result.outcome is (probing.Outcome.SAT if hits.intersection(selected)
                                         else probing.Outcome.LOCAL_UNSAT), result.detail
                if result.candidate is not None:
                    assert result.candidate == {"a": value} and result.goal in hits


def test_small_dependency_subset_can_hit_before_shared_downstream_computation(monkeypatch):
    expensive = ir.Assign("y", ir.Op("mul", ir.Var("x"), ir.Var("x")))
    proc = ir.Procedure((), ("a",), ("x", "y"), (), ir.Seq(
        ir.Assign("x", ir.Op("add", ir.Var("a"), ir.Lit(1))), ir.Seq(
            ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)), ir.Mark("easy"), ir.Skip()),
            ir.Seq(expensive, ir.If(ir.Op("eq", ir.Var("y"), ir.Lit(9)),
                                    ir.Mark("later"), ir.Skip())))),
        signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    built = counted_builds(monkeypatch)
    result = exact.local_hit_any(proc, {"a": ranges.Range(0, 3)}, {}, ("easy", "later"),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT and result.goal == "easy"
    assert result.candidate == {"a": 1}
    assert len(built) == 1 and id(expensive) not in built[0][0]["keep"]


@pytest.mark.parametrize('answer', ['early', 'late', None])
def test_owned_prefix_encodes_shared_work_once_without_delaying_an_early_hit(monkeypatch, answer):
    proc = ir.Procedure((), ('a',), ('x', 'y'), (), ir._sequence((
        ir.Assign('x', ir.Op('add', ir.Var('a'), ir.Lit(1))),
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(2 if answer == 'early' else 0)),
              ir.Mark('early'), ir.Skip()),
        ir.Assign('y', ir.Op('mul', ir.Var('x'), ir.Var('x'))),
        ir.If(ir.Op('eq', ir.Var('y'), ir.Lit(9 if answer == 'late' else 25)),
              ir.Mark('late'), ir.Skip()))),
        signature=ProcedureSignature((('a', FiniteType(2)),), ()))
    original, assigned = encoder._Commands._assign, []

    def assign(self, command, *args, **kwargs):
        assigned.append(command.var)
        if answer == 'early' and command.var == 'y':
            pytest.fail('an early hit must not encode its downstream suffix')
        return original(self, command, *args, **kwargs)

    monkeypatch.setattr(encoder._Commands, '_assign', assign)
    prepared = exact._LocalQueryPreparation(proc, {'a': ranges.Range(0, 3)})
    result = prepared.probe({}, ('early', 'late'), timeout_ms=60_000,
                            _constructions={}, _proved_unsat=set())
    assert result.outcome is (probing.Outcome.SAT if answer else probing.Outcome.LOCAL_UNSAT)
    assert result.goal == answer
    assert assigned.count('x') == 1
    assert assigned.count('y') == (0 if answer == 'early' else 1)
    if result.candidate is not None:
        assert answer in interp.run_step(proc, result.candidate, {})[2]


@pytest.mark.parametrize("trips", [0, 1, 2])
@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize("failure_goals", [False, True])
@pytest.mark.parametrize("owned", [False, True])
def test_every_goal_subset_matches_exhaustive_replay(trips, stateful, failure_goals, owned):
    proc, env = problem(trips, stateful, failure_goals)
    for state in ({"s": 0}, {"s": 4}) if stateful else ({},):
        hits = set().union(*(interp.run_step(proc, {"a": a}, state)[2]
                             for a in range(4)))
        for size in (1, 2, 3):
            for goals in itertools.combinations(("hit", "bad", "never"), size):
                result = (exact._LocalQueryPreparation(proc, env).probe(
                    state, goals, timeout_ms=60_000, _constructions={}, _proved_unsat=set())
                    if owned else exact.local_hit_any(proc, env, state, goals, timeout_ms=60_000))
                if hits.intersection(goals):
                    assert result.outcome is probing.Outcome.SAT, result.detail
                    assert result.goal in goals
                    assert result.goal in interp.run_step(proc, result.candidate, state)[2]
                else:
                    assert result.outcome is probing.Outcome.LOCAL_UNSAT, result.detail


@pytest.mark.parametrize("state", [0, 4])
def test_equal_cones_build_once_and_preserve_each_hit_formula(monkeypatch, state):
    proc, env = problem()
    built = counted_builds(monkeypatch)
    result = exact.local_hit_any(proc, env, {"s": state}, ("hit", "bad"),
        timeout_ms=60_000)
    assert result.outcome is (probing.Outcome.SAT if state == 0
                             else probing.Outcome.LOCAL_UNSAT), result.detail
    assert len(built) == 1
    kwargs, shared = built[0]
    assert set(kwargs["goals"]) == {"hit", "bad"}
    # Compare formulas at every input, not just their overall satisfiability.
    full = encoder.encode_step(proc, pre=exact.pin_state(proc, env, {"s": state}),
                               env=env, tag="full")
    solver = z3.Solver()
    solver.set(timeout=5_000)
    solver.add(shared.constraint, full.constraint, shared.pre["a"] == full.pre["a"])
    solver.add(z3.Or(*(shared.goal_condition(g) != full.goal_condition(g)
                       for g in ("hit", "bad"))))
    assert solver.check() == z3.unsat


def test_internal_loop_plan_is_not_immediately_derived_again(monkeypatch):
    from behave.sim.symbolic import plan

    proc, env = problem()
    calls = []
    original = plan._loop_plan

    def derive(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(plan, "_loop_plan", derive)
    result = exact.local_hit_any(proc, env, {"s": 4}, ("hit", "bad"),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT, result.detail
    assert len(calls) == 1 and calls[0].inputs == proc.inputs


def test_distinct_occurrence_cones_share_only_their_prepared_loop_plan(monkeypatch):
    from behave.sim.symbolic import plan

    body = ir.Seq(
        ir.For("i", 2, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(4)), ir.Mark("g"), ir.Skip())),
        ir.For("j", 2, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), ir.Mark("g"), ir.Skip())))
    proc = ir.Procedure((), ("a",), ("i", "j"), (), body,
                        signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    plans = []
    original = plan.plan_encoding

    def derive(*args, **kwargs):
        result = original(*args, **kwargs)
        plans.append(result)
        return result

    built = counted_builds(monkeypatch)
    monkeypatch.setattr(plan, "plan_encoding", derive)
    result = exact.local_hit_any(proc, {"a": ranges.Range(0, 3)}, {}, ("g",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 2}
    assert "g" in interp.run_step(proc, result.candidate)[2]
    assert len(plans) == 1 and len(built) == 2
    assert all(item[0]["plan"] is plans[0] for item in built)
    assert built[0][0]["keep"] != built[1][0]["keep"]


def test_local_plan_binds_fixed_inputs_without_reusing_another_domains_false_goals(monkeypatch):
    from behave.sim.symbolic import plan

    body = ir.For("i", ir.Op("add", ir.Var("a"), ir.Lit(1)),
                  ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                        ir.Mark("g"), ir.Skip()))
    proc = ir.Procedure((), ("a",), ("i",), (), body,
                        signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    env = {"a": ranges.Range(0, 3)}
    owner = exact._LocalQueryPreparation(proc, env)
    original, plans = plan.plan_encoding, []

    def derive(*args, **kwargs):
        result = original(*args, **kwargs)
        plans.append(result)
        return result

    monkeypatch.setattr(plan, "plan_encoding", derive)
    for fixed, expected in (({"a": 0}, probing.Outcome.LOCAL_UNSAT),
                            ({"a": 0}, probing.Outcome.LOCAL_UNSAT),
                            ({"a": 2}, probing.Outcome.SAT),
                            ({}, probing.Outcome.SAT)):
        result = owner.probe({}, ("g",), timeout_ms=20_000, fixed_inputs=fixed)
        assert result.outcome is expected, result.detail
        if result.candidate is not None:
            assert "g" in interp.run_step(proc, result.candidate)[2]
    assert [dict(p.fixed_inputs) for p in plans] == [{"a": 0}, {"a": 2}, {}]
    assert env == {"a": ranges.Range(0, 3)}


def test_formula_deadline_interrupts_expansion_without_solver_or_partial_answer(monkeypatch):
    proc, env = problem(trips=8)
    clock = [0.0]
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    assignments = []
    original = encoder._Commands._assign

    def advance(self, command, *args, **kwargs):
        result = original(self, command, *args, **kwargs)
        assignments.append(command)
        clock[0] += 0.060
        return result

    def no_solver(*args, **kwargs):
        pytest.fail("an interrupted formula must not be submitted to a solver")

    monkeypatch.setattr(encoder._Commands, "_assign", advance)
    monkeypatch.setattr(exact.z3, "Solver", no_solver)
    result = exact.local_hit_any(proc, env, {"s": 4}, ("hit", "bad"), timeout_ms=100)
    assert result.outcome is probing.Outcome.UNKNOWN, result.detail
    assert result.candidate is None and result.goal is None
    assert "formula construction" in result.detail
    assert len(assignments) == 2


@pytest.mark.parametrize("state", [0, 4])
@pytest.mark.parametrize("phase", ["encoding", "registration"])
def test_retained_construction_pause_resumes_without_restarting_or_crediting_hit(
        monkeypatch, state, phase):
    proc, env = problem(trips=8)
    prepared, constructions, proved = exact._LocalQueryPreparation(proc, env), {}, set()
    clock, delayed, starts = [0.0], [True], []
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    original_iter = encoder._encode_step_iter

    def start(*args, **kwargs):
        starts.append(kwargs)
        return original_iter(*args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_step_iter", start)
    owner, name = ((encoder._Commands, "_assign") if phase == "encoding" else
                   (encoder.StepEncoding, "_start_query_formula"))
    original = getattr(owner, name)

    def pause(*args, **kwargs):
        result = original(*args, **kwargs)
        if delayed[0]:
            clock[0] += 0.101
        return result

    monkeypatch.setattr(owner, name, pause)
    options = {"timeout_ms": 100, "_constructions": constructions, "_proved_unsat": proved}
    answer = prepared.probe({"s": state}, ("hit", "bad"), **options)
    assert answer.outcome is probing.Outcome.CONSTRUCTING, answer.detail
    assert answer.candidate is None and answer.goal is None
    assert len(starts) == 1 and len(constructions) == 1
    continuation = next(iter(constructions.values())).encoding
    assert continuation.iterator is not None or continuation.result is not None

    delayed[0] = False
    answer = prepared.probe({"s": state}, ("hit", "bad"), **options)
    assert answer.outcome is (probing.Outcome.SAT if state == 0
                             else probing.Outcome.LOCAL_UNSAT), answer.detail
    assert len(starts) == 1 and not constructions
    if answer.candidate is not None:
        assert answer.goal in interp.run_step(proc, answer.candidate, {"s": state})[2]


@pytest.mark.parametrize("later_pause", [False, True])
def test_retained_solver_unknown_does_not_request_construction_retry(monkeypatch, later_pause):
    proc = ir.Procedure((), ("a", "b"), (), (), ir.Seq(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), ir.Mark("first"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("b"), ir.Lit(1)), ir.Mark("second"), ir.Skip())),
        signature=ProcedureSignature((("a", FiniteType(2)), ("b", FiniteType(2))), ()))
    prepared = exact._LocalQueryPreparation(proc, {name: ranges.Range(0, 3) for name in proc.inputs})
    clock, constructions, checks = [0.0], {}, []
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    original = exact._encode_query_step

    def complete_query(*args, **kwargs):
        # Exercise UNKNOWN from a complete query, not an early occurrence prefix.
        kwargs["_prefix_goals"] = False
        result = original(*args, **kwargs)
        if checks and later_pause:
            clock[0] += 0.101
        return result

    class UnknownSolver:
        def add(self, *args):
            pass

        def set(self, *args):
            pass

        def check(self):
            checks.append(self)
            return z3.unknown

        def reason_unknown(self):
            return "fixture solver unknown"

    monkeypatch.setattr(exact, "_encode_query_step", complete_query)
    monkeypatch.setattr(exact.z3, "Solver", UnknownSolver)
    answer = prepared.probe({}, ("first", "second"), timeout_ms=100,
                            _constructions=constructions)
    assert answer.outcome is probing.Outcome.UNKNOWN, answer.detail
    assert "fixture solver unknown" in answer.detail
    assert answer.candidate is None and answer.goal is None
    assert len(checks) == (1 if later_pause else 2)
    assert all(item.solver is None for item in constructions.values())


def test_one_formula_still_identifies_a_replayable_member(monkeypatch):
    proc, env = problem()
    built = counted_builds(monkeypatch)
    result = exact.local_hit_any(proc, env, {"s": 0}, ("bad", "hit"),
        timeout_ms=60_000)
    assert len(built) == 1 and result.outcome is probing.Outcome.SAT, result.detail
    assert result.goal == "hit"  # The first requested member cannot fire.
    assert "hit" in interp.run_step(proc, result.candidate, {"s": 0})[2]


def test_different_cones_are_not_unioned_or_settled_by_another_occurrence(monkeypatch):
    body = ir.Seq(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(4)), ir.Mark("g"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), ir.Mark("g"), ir.Skip()))
    proc = ir.Procedure((), ("a",), (), (), body, signature=ProcedureSignature(
        (("a", FiniteType(2)),), ()))
    built = counted_builds(monkeypatch)
    result = exact.local_hit_any(proc, {"a": ranges.Range(0, 3)}, {}, ("g",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate == {"a": 2}
    assert len(built) == 2 and built[0][0]["keep"] != built[1][0]["keep"]


@pytest.mark.parametrize("fault", ["audit", "build", "hit"])
def test_incomplete_shared_work_never_becomes_local_unsat(monkeypatch, fault):
    proc, env = problem()
    events = slicing.linearize(proc.body)
    site = next(i for i, (cmd, _) in enumerate(events) if isinstance(cmd, ir.Fail))
    if fault == "audit":
        original = exact._goal_occurrence_keep

        def incomplete(proc, goal, at, events):
            if at == site:
                raise encoder.EncodingError("fixture missing occurrence", kind="goal")
            return original(proc, goal, at, events)

        monkeypatch.setattr(exact, "_goal_occurrence_keep", incomplete)
    elif fault == "build":
        def unavailable(*args, **kwargs):
            raise encoder.EncodingError("fixture unencodable group", kind="goal")

        monkeypatch.setattr(exact, "_encode_query_step", unavailable)
    else:
        original = encoder.StepEncoding.supports
        monkeypatch.setattr(encoder.StepEncoding, "supports",
                            lambda self, goal: goal != "hit" and original(self, goal))
    result = exact.local_hit_any(proc, env, {"s": 4}, ("hit", "bad"),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED, result.detail


@pytest.mark.parametrize("phase", ["planning", "construction", "solver"])
def test_shared_work_uses_one_deadline_and_unknown_stays_open(monkeypatch, phase):
    proc, env = problem()
    clock = [0.0]
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    built = counted_builds(monkeypatch)
    if phase in ("planning", "construction"):
        name = "_goal_occurrence_keep" if phase == "planning" else "_encode_query_step"
        original = getattr(exact, name)

        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += 0.101
            return result

        monkeypatch.setattr(exact, name, delayed)
    else:
        class UnknownSolver:
            def set(self, *args):
                assert args == ("timeout", 100)

            def add(self, *args):
                pass

            def check(self):
                clock[0] += 0.101
                return z3.unknown

            def reason_unknown(self):
                return "fixture timeout"

        monkeypatch.setattr(exact.z3, "Solver", UnknownSolver)
    result = exact.local_hit_any(proc, env, {"s": 4}, ("hit", "bad"), timeout_ms=100)
    assert result.outcome is probing.Outcome.UNKNOWN, result.detail
    assert len(built) == (0 if phase == "planning" else 1)
    assert result.solve_ms >= 100


def test_every_group_member_requires_its_own_audited_occurrence(monkeypatch):
    proc, _ = problem()
    events = slicing.linearize(proc.body)
    site = next(i for i, (cmd, _) in enumerate(events) if isinstance(cmd, ir.Fail))
    goal = events[site][0].goal
    assert encoder._goal_occurrence_keep(proc, goal, site, events)
    with pytest.raises(encoder.EncodingError):
        encoder._goal_occurrence_keep(proc, "another-goal", site, events)
    monkeypatch.setattr(slicing, "_binding_faults", lambda *args, **kwargs: ["not closed"])
    with pytest.raises(encoder.EncodingError, match="not closed"):
        encoder._goal_occurrence_keep(proc, goal, site, events)

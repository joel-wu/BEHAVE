"""Reuse immutable preparation, never concrete state or incomplete answers."""

from behave.sim.symbolic import closure_proof

import copy

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, plan, ranges, slicing
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing

from .test_local_query_sharing import problem
from .test_local_cone_representability import _problem as unresolved_tail

pytestmark = pytest.mark.solver


def answer_image(answer):
    return ({k: v for k, v in answer.as_dict().items() if k != "solve_ms"}, answer.candidate)


def test_occurrence_planning_reuses_body_structure_not_goal_dependencies(monkeypatch):
    proc, env = problem()
    events = slicing.linearize(proc.body)
    occurrences = [(site, command.goal) for site, (command, _) in enumerate(events)
                   if isinstance(command, (ir.Mark, ir.Fail))]
    expected = {(site, goal): encoder._goal_occurrence_keep(proc, goal, site, events)
                for site, goal in occurrences}
    builds, parents = [], []
    bodies_of, parents_of = slicing._loop_bodies, slicing._loop_parents

    def bodies(events):
        builds.append(events)
        return bodies_of(events)

    def controls(events):
        parents.append(events)
        return parents_of(events)

    monkeypatch.setattr(slicing, "_loop_bodies", bodies)
    monkeypatch.setattr(slicing, "_loop_parents", controls)
    owner = exact._LocalQueryPreparation(proc, env)
    for site, goal in occurrences:
        assert owner.cone(goal, site) == expected[site, goal]
        assert owner.cone(goal, site) == expected[site, goal]
    assert len(builds) == len(parents) == 1

    # A different invocation gets its own structure. Neither the index nor a
    # per-goal command selection is a global cache over mutable caller IR.
    second = exact._LocalQueryPreparation(proc, env)
    for site, goal in occurrences:
        assert second.cone(goal, site) == expected[site, goal]
    assert len(builds) == len(parents) == 2


def test_fresh_occurrence_cone_is_derived_once_and_still_checks_bindings(monkeypatch):
    proc, _env = problem()
    indexed = slicing.index(proc)
    site, mark = next((i, cmd) for i, (cmd, _) in enumerate(indexed.events)
                      if isinstance(cmd, (ir.Mark, ir.Fail)))
    derive, calls = slicing.cone, []

    def counted(*args, **kwargs):
        calls.append(kwargs.get("_site"))
        return derive(*args, **kwargs)

    monkeypatch.setattr(slicing, "cone", counted)
    keep = encoder._goal_occurrence_keep(proc, mark.goal, site, indexed.events, _index=indexed)
    assert calls == [site]
    assert not slicing.audit_occurrence(proc, mark.goal, site, keep, _index=indexed)
    assert calls == [site, site], "an external selection still gets the full audit"
    assert slicing.audit_occurrence(proc, mark.goal, site, keep - {id(mark)}, _index=indexed)

    malformed = ir.Procedure((), (), (), (), ir.If(ir.Var("missing"), ir.Mark("bad"), ir.Skip()))
    events = slicing.linearize(malformed.body)
    site = next(i for i, (cmd, _) in enumerate(events) if isinstance(cmd, ir.Mark))
    with pytest.raises(encoder.EncodingError, match="missing"):
        encoder._goal_occurrence_keep(malformed, "bad", site, events)


def test_worker_plan_is_shared_but_states_domains_and_solvers_are_fresh(monkeypatch):
    proc, env = problem()
    prepared = exact._LocalQueryPreparation(proc, env)
    builds, cones, solvers, validations = [], [], [], []
    derive, audit, solver = plan.plan_encoding, exact._goal_occurrence_keep, z3.Solver
    validate = exact._procedure_validation

    def count_validation(proc, **options):
        result = validate(proc, **options)
        validations.append(result)
        return result

    def count_plan(*args, **kwargs):
        result = derive(*args, **kwargs)
        builds.append(result)
        return result

    def count_cone(*args, **kwargs):
        result = audit(*args, **kwargs)
        cones.append((args[1], args[2]))
        return result

    def count_solver(*args, **kwargs):
        result = solver(*args, **kwargs)
        solvers.append(result)
        return result

    monkeypatch.setattr(plan, "plan_encoding", count_plan)
    monkeypatch.setattr(exact, "_goal_occurrence_keep", count_cone)
    monkeypatch.setattr(exact.z3, "Solver", count_solver)
    monkeypatch.setattr(exact, "_procedure_validation", count_validation)
    for state in (4, 0, 4, 0):
        result = prepared.probe({"s": state}, ("hit", "bad"),
            timeout_ms=60_000)
        assert result.outcome is (probing.Outcome.SAT if state == 0 else probing.Outcome.LOCAL_UNSAT)
        if result.candidate is not None:
            assert result.goal in interp.run_step(proc, result.candidate, {"s": state})[2]
    assert len(builds) == 1 and len(cones) == len(set(cones)) and len(solvers) == 4
    # No solver assumptions leak from fixed inputs or an external input domain.
    for value in range(4):
        fixed = prepared.probe({"s": 0}, ("hit", "bad"), fixed_inputs={"a": value},
            timeout_ms=60_000)
        expected_hits = interp.run_step(proc, {"a": value}, {"s": 0})[2]
        assert (fixed.outcome is probing.Outcome.SAT) == bool(expected_hits & {"hit", "bad"})
        constrained = prepared.probe({"s": 0}, ("hit", "bad"),
                                    input_constraints=lambda inputs, value=value: [inputs["a"] == value],
            timeout_ms=60_000)
        assert constrained.outcome is fixed.outcome
        if constrained.candidate is not None:
            assert constrained.candidate == {"a": value}
    assert len(builds) == 5 and len(validations) == 2
    assert [dict(p.fixed_inputs) for p in builds] == [{}, *({"a": v} for v in range(4))]
    # Each fixed-input domain gets its own specialized plan. Returning to the
    # unrestricted domain reuses its first plan, rather than deriving it again.
    # Original admission and the immutable query projection are each checked
    # once; neither is revalidated for every state or input restriction.


def test_failed_validation_is_not_retained(monkeypatch):
    proc, env = problem()
    owner = exact._LocalQueryPreparation(proc, env)
    validate, calls = exact._procedure_validation, []

    def interrupted(proc, **options):
        calls.append(proc)
        if len(calls) == 1:
            raise encoder.EncodingError("unfinished admission", kind="resource")
        return validate(proc, **options)

    monkeypatch.setattr(exact, "_procedure_validation", interrupted)
    assert owner.probe({"s": 0}, ("hit",),
        timeout_ms=60_000).outcome is probing.Outcome.UNSUPPORTED
    assert owner._structure.validation is None
    assert owner.probe({"s": 0}, ("hit",),
        timeout_ms=60_000).outcome is probing.Outcome.SAT
    assert calls[:2] == [proc, proc] and len(calls) == 3
    assert calls[2] is owner.for_query(("hit",))._proc


def test_batched_occurrence_planning_resumes_and_remains_source_only():
    proc = ir.Procedure((), ("a",), ("x",), (), ir._sequence((
        ir.Assign("x", ir.Var("a")),
        *(ir.If(ir.Var("x"), ir.Mark(f"g{i}"), ir.Skip()) for i in range(30)))))
    owner = exact._LocalQueryPreparation(proc, {})
    occurrences = tuple((site, cmd.goal) for site, (cmd, _) in enumerate(owner.events())
                        if isinstance(cmd, ir.Mark))
    calls = []

    def pause():
        calls.append(None)
        if len(calls) == 10:
            raise exact._ProbeDeadlineExceeded()

    with pytest.raises(exact._ProbeDeadlineExceeded):
        owner.prepare_cones(occurrences, pause)
    retained = owner._structure.cone_work
    assert retained is not None and not owner._structure.cones
    owner.prepare_cones(occurrences[::2], lambda: None)
    assert owner._structure.cone_work is None
    for site, goal in occurrences:
        assert owner.cone(goal, site) == encoder._goal_occurrence_keep(
            proc, goal, site, owner.events())


def test_literal_control_is_reused_without_reusing_a_state_verdict(monkeypatch):
    from behave.sim.behavior import goal_projection

    proc, env = problem()
    owner = exact._LocalQueryPreparation(proc, env)
    original, calls = goal_projection.literal_false_goals, []

    def counted(procedure):
        calls.append(procedure)
        return original(procedure)

    monkeypatch.setattr(goal_projection, "literal_false_goals", counted)
    for state in (0, 4, 0, 4):
        result = owner.probe({"s": state}, ("hit", "bad"),
            timeout_ms=60_000)
        assert result.outcome is (probing.Outcome.SAT if state == 0
                                 else probing.Outcome.LOCAL_UNSAT)
    assert len(calls) == 1
    exact.local_hit_any(proc, env, {"s": 0}, ("hit", "bad"),
        timeout_ms=60_000)
    assert len(calls) == 2, "a new public invocation owns fresh preparation"


def test_interrupted_literal_control_does_not_publish_exclusions(monkeypatch):
    from behave.sim.behavior import goal_projection

    proc, env = problem()
    owner = exact._LocalQueryPreparation(proc, env)
    owner.validation()
    original, calls = goal_projection.literal_false_goals, []

    def interrupted(procedure):
        calls.append(procedure)
        if len(calls) == 1:
            raise TimeoutError("unfinished literal control")
        return original(procedure)

    monkeypatch.setattr(goal_projection, "literal_false_goals", interrupted)
    with pytest.raises(TimeoutError, match="unfinished literal control"):
        owner.literal_false_goals()
    assert owner._structure.literal_false_goals is None
    assert owner.literal_false_goals() == original(proc)
    assert len(calls) == 2


@pytest.mark.parametrize("trips", [0, 1, 3])
def test_prepared_and_one_shot_queries_match_all_inputs_and_goal_sets(trips):
    proc, env = problem(trips=trips)
    prepared = exact._LocalQueryPreparation(proc, env)
    for state in ({"s": 4}, {"s": 0}):
        for goals in (("hit",), ("bad",), ("never",), ("hit", "bad"), ("bad", "hit", "never")):
            result = prepared.probe(state, goals,
                timeout_ms=60_000)
            fresh = exact.local_hit_any(proc, env, state, goals,
                timeout_ms=60_000)
            assert answer_image(result) == answer_image(fresh)


def test_public_queries_do_not_reuse_a_mutated_external_procedure(monkeypatch):
    proc, env = problem()
    calls = []
    derive = plan.plan_encoding

    def counted(*args, **kwargs):
        calls.append(args[0])
        return derive(*args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", counted)
    assert exact.local_hit_any(proc, env, {"s": 0}, ("hit",),
        timeout_ms=60_000).outcome is probing.Outcome.SAT
    proc.body = ir.Seq(ir.For("i", 0, proc.body), ir.Mark("replacement"))
    result = exact.local_hit_any(proc, env, {"s": 0}, ("hit",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT
    assert len(calls) == 1 and calls[0] is not proc
    # Only the original query projection was planned. The new body proves
    # the old goal literally absent without reusing that plan.


def test_plan_is_lazy_and_separate_for_each_carrier_and_procedure(monkeypatch):
    proc, env = problem()
    owners = [exact._LocalQueryPreparation(proc, env),
              exact._LocalQueryPreparation(proc, {**env, "s": ranges.Range(0, 8)}),
              exact._LocalQueryPreparation(copy.deepcopy(proc), env)]
    plans = []
    derive = plan.plan_encoding

    def counted(*args, **kwargs):
        result = derive(*args, **kwargs)
        plans.append(result)
        return result

    monkeypatch.setattr(plan, "plan_encoding", counted)
    assert plans == []
    for owner in owners:
        for state in (0, 4):
            owner.probe({"s": state}, ("hit", "bad"), timeout_ms=60_000)
    assert len(plans) == 3
    assert plans[0].environment_fingerprint != plans[1].environment_fingerprint


@pytest.mark.parametrize("retained", [False, True])
def test_completed_plan_survives_a_query_deadline_but_no_answer_does(monkeypatch, retained):
    proc, env = problem()
    prepared = exact._LocalQueryPreparation(proc, env)
    clock, builds = [0.0], []
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    derive = plan.plan_encoding

    def delayed(*args, **kwargs):
        result = derive(*args, **kwargs)
        builds.append(result)
        clock[0] += 0.101
        return result

    monkeypatch.setattr(plan, "plan_encoding", delayed)
    options = {"_constructions": {}, "_proved_unsat": set()} if retained else {}
    first = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, **options)
    assert first.outcome is (probing.Outcome.CONSTRUCTING if retained else probing.Outcome.UNKNOWN)
    assert first.candidate is None
    assert len(builds) == 1
    second = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, **options)
    assert second.outcome is probing.Outcome.SAT
    assert len(builds) == 1
    assert second.goal in interp.run_step(proc, second.candidate, {"s": 0})[2]


def test_occurrence_planning_resumes_before_any_solver_query(monkeypatch):
    proc, env = problem()
    prepared = exact._LocalQueryPreparation(proc, env)
    clock, cones = [0.0], []
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    derive = exact._goal_occurrence_keep

    def delayed(*args, **kwargs):
        result = derive(*args, **kwargs)
        cones.append((args[1], args[2]))
        clock[0] += 0.101
        return result

    monkeypatch.setattr(exact, "_goal_occurrence_keep", delayed)
    options = {"_constructions": {}, "_proved_unsat": set()}
    # At most one new cone fits each artificial slice; cached cones must not
    # spend the next slice again. The bound follows actual source occurrences.
    count = sum(isinstance(command, (ir.Mark, ir.Fail))
                for command, _under in prepared.events())
    outcomes = []
    for _ in range(count + 1):
        result = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, **options)
        outcomes.append(result.outcome)
        if result.outcome is not probing.Outcome.CONSTRUCTING:
            break
    assert outcomes[0] is probing.Outcome.CONSTRUCTING
    assert outcomes[-1] is probing.Outcome.SAT
    assert len(cones) == len(set(cones))
    assert result.goal in interp.run_step(proc, result.candidate, {"s": 0})[2]


def test_expression_diagnostics_are_reused_without_reusing_a_query_answer(monkeypatch):
    proc, env = unresolved_tail()
    proc.body = ir.Seq(proc.body, ir.If(
        ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("depends"), ir.Skip()))
    owner = exact._LocalQueryPreparation(proc, env)
    derive, plans = plan.plan_encoding, []

    def counted(*args, **kwargs):
        result = derive(*args, **kwargs)
        plans.append(result)
        return result

    monkeypatch.setattr(plan, "plan_encoding", counted)
    for value in (0, 1, 65535, 0):
        answer = owner.probe({}, ("early",), fixed_inputs={"a": value},
            timeout_ms=60_000)
        assert answer.outcome is (probing.Outcome.SAT if value == 0 else probing.Outcome.LOCAL_UNSAT)
        if answer.candidate is not None:
            assert answer.goal in interp.run_step(proc, answer.candidate, {})[2]
    blocked = owner.probe({}, ("depends",),
        timeout_ms=60_000)
    assert blocked.outcome is probing.Outcome.UNSUPPORTED
    assert "left-shift" in blocked.detail
    assert len(plans) == 1 and owner.for_query(("depends",))._plan is plans[0]
    assert plans[0].unresolved
    assert all(isinstance(item.what, plan.ExpressionPosition)
               for item in plans[0].unresolved)
    fresh = exact.local_hit_any(proc, env, {}, ("depends",),
        timeout_ms=60_000)
    assert fresh.outcome is blocked.outcome and fresh.detail == blocked.detail
    assert len(plans) == 2  # The public one-shot call has its own owner.


@pytest.mark.parametrize("failure", ["exception", "unresolved", "loop", "resource"])
def test_failed_preparation_is_not_published_or_mistaken_for_unsat(monkeypatch, failure):
    proc, env = problem()
    prepared = exact._LocalQueryPreparation(proc, env)
    derive, calls = plan.plan_encoding, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            if failure == "exception":
                raise encoder.EncodingError("fixture unfinished plan")
            complete = derive(*args, **kwargs)
            fields = ("widths", "cells", "shapes", "loop_bounds", "loop_visits",
                      "occurrence_carriers", "expansion_cost", "bound", "env",
                      "procedure_fingerprint")
            obligation = (plan.Unresolved("range", plan.ExpressionPosition(0, "trip_count"))
                          if failure == "loop" else
                          plan.Unresolved("resource", plan.ExpressionPosition(0, "rhs"))
                          if failure == "resource" else
                          plan.Unresolved("capability", "fixture unfinished plan"))
            return plan.EncodingPlan(**{name: getattr(complete, name) for name in fields},
                                     unresolved=(obligation,))
        return derive(*args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", flaky)
    assert prepared.probe({"s": 4}, ("hit", "bad"),
        timeout_ms=60_000).outcome is probing.Outcome.UNSUPPORTED
    assert prepared._plan is None
    assert prepared.probe({"s": 0}, ("hit", "bad"),
        timeout_ms=60_000).outcome is probing.Outcome.SAT
    assert len(calls) == 2


def test_cached_cones_still_leave_unknown_and_unsupported_goals_open():
    proc, env = problem()
    prepared = exact._LocalQueryPreparation(proc, env)
    prepared.probe({"s": 4}, ("hit", "bad"),
        timeout_ms=60_000)
    result = prepared.probe({"s": 4}, ("hit", "bad", "not-declared"),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED
    assert "not-declared" in result.detail


def test_position_specific_carriers_do_not_use_the_workers_common_plan(monkeypatch):
    proc, env = problem()
    owner = exact._LocalQueryPreparation(proc, env)
    assert owner.probe({"s": 0}, ("hit",),
        timeout_ms=60_000).outcome is probing.Outcome.SAT
    assert owner.for_query(("hit",))._plan is not None
    positioned = closure_proof.prepare_positions(proc, env, 2)

    def wrong_plan(*_args):
        pytest.fail("a position-specific query reused the common loop plan")

    monkeypatch.setattr(owner, "plan", wrong_plan)
    monkeypatch.setattr(owner, "for_query", wrong_plan)
    for position in (0, 1):
        options = {"position_plan": positioned, "position": position}
        # A successful invocation advances s by two; position one has carrier {2}.
        state = {"s": 2 * position}
        result = owner.probe(state, ("hit", "bad"), **options,
            timeout_ms=60_000)
        fresh = exact.local_hit_any(proc, env, state, ("hit", "bad"), **options,
            timeout_ms=60_000)
        assert result.outcome is probing.Outcome.SAT
        assert answer_image(result) == answer_image(fresh)
    invalid = owner.probe({"s": 0}, ("hit",), position_plan=positioned, position=2,
        timeout_ms=60_000)
    assert invalid.outcome is probing.Outcome.UNSUPPORTED
    assert "position exceeds" in invalid.detail

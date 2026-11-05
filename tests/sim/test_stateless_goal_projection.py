"""Temporal goal equivalence, with full-length replay and negative premises."""

from behave.sim.symbolic import closure_proof

import itertools
import pickle

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import goal_projection, residual
from behave.sim.symbolic import interp, ir, plan, ranges, typecheck as types
from behave.sim.behavior import instance as behavior_instance
from behave.sim.core.audit import AuditedWitness
from behave.sim.core.query import SolverAnswer
from behave.sim.core.query_telemetry import QueryTelemetryScope
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

GOALS = ("left", "right", "failure", "never")


def _sequence(*commands):
    body = ir.Skip()
    for command in reversed(commands):
        body = ir.Seq(command, body)
    return body


def _table(left=1, right=2):
    def arm(value):
        return _sequence(
            ir.If(ir.Lit(bool(value & 1)), ir.Mark("left"), ir.Skip()),
            ir.If(ir.Lit(bool(value & 2)), ir.Mark("right"), ir.Skip()),
            ir.If(ir.Lit(bool(value & 4)), ir.Fail("DivZero", "failure"), ir.Skip()))

    return ir.Procedure(
        (), ("a",), (), ("y",),
        _sequence(ir.Assign("y", ir.Var("a")),
                  ir.If(ir.Lit(False), ir.Mark("never"), ir.Skip()),
                  ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), arm(left), arm(right))),
        types={"a": types.INTEGER, "y": types.INTEGER})


ENV = {"a": ranges.Range(0, 1)}


def _hits(proc, trace):
    _states, _outputs, hit_sets, _error = interp.run_trace_detailed(proc, trace)
    return set().union(*hit_sets)


@pytest.mark.parametrize("left,right", tuple(itertools.product(range(8), repeat=2)))
def test_all_small_fallible_tables_match_complete_unrolling(left, right):
    proc = _table(left, right)
    for steps in (1, 2, 3):
        complete = residual.unroll(proc, ENV, steps)
        expected = set()
        solver = z3.Solver()
        solver.add(complete.constraint)
        assert solver.check() == z3.sat  # no vacuous equivalence
        for goal in GOALS:
            solver.push()
            solver.add(complete.goal_condition(goal))
            if solver.check() == z3.sat:
                expected.add(goal)
            solver.pop()
        # Independent concrete traces include non-repeating input sequences.
        concrete = set().union(*(
            _hits(proc, [{"a": value} for value in values])
            for values in itertools.product(range(2), repeat=steps)))
        assert expected == concrete
        result = residual.decide_goals(proc, ENV, GOALS, steps=steps, workers=1)
        assert result.steps == steps
        assert not result.undecided
        assert set(result.reachable) == expected
        assert result.unreachable == set(GOALS) - expected
        for goal, trace in result.reachable.items():
            assert len(trace) == steps and goal in _hits(proc, trace)
        for call in result.queries:
            if call.answer is SolverAnswer.SAT:
                assert len(call.witness) == steps
                assert _hits(proc, call.witness) & set(call.goals)


@pytest.mark.parametrize("fragmented", [False, True])
def test_only_one_position_is_encoded_but_full_horizon_is_returned(monkeypatch, fragmented):
    proc = _table(5, 2)  # first branch records a hit then fails
    prepared = closure_proof.prepare_positions(proc, ENV, 5)
    identity = prepared.identity
    calls = []
    original = residual.unroll

    def observed(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(residual, "unroll", observed)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: fragmented)
    result = residual.decide_goals(proc, ENV, GOALS, steps=5, plan=prepared, workers=1)
    assert not result.undecided
    assert calls and set(calls) == {1}
    assert result.steps == 5 and prepared.bound == 5 and prepared.identity == identity
    assert result.unreachable == {"never"}
    for goal, trace in result.reachable.items():
        assert len(trace) == 5 and goal in _hits(proc, trace)
        assert len({id(record) for record in trace}) == 5


def test_prefix_is_a_separate_pickleable_plan_not_a_relabelled_original():
    proc = _table()
    prepared = closure_proof.prepare_positions(proc, ENV, 4)
    prefix = pickle.loads(pickle.dumps(prepared.first_step(proc, ENV, 4)))
    raw = prefix.checked_for(proc, ENV, 1)
    assert raw.bound == 1 and prepared.bound == 4
    assert raw.scalar_width == prepared.plan.scalar_width
    assert raw.occurrence_layers[0] == prepared.plan.occurrence_layers[0]
    assert raw.state_layers == prepared.plan.state_layers[:2]
    with pytest.raises(plan.PlanMismatch):
        prepared.checked_for(proc, ENV, 1)
    with pytest.raises(plan.PlanMismatch):
        prefix.checked_for(proc, ENV, 4)


def test_an_always_failing_plan_keeps_first_step_hits_and_full_absorption():
    proc = _table(5, 6)
    prepared = closure_proof.prepare_positions(proc, ENV, 4)
    assert prepared.plan.normal_layers == (True, False, False, False, False)
    complete = residual.unroll(proc, ENV, 4, plan=prepared)
    result = residual.decide_goals(proc, ENV, GOALS, steps=4, plan=prepared)
    assert not result.undecided
    for goal in GOALS:
        solver = z3.Solver()
        solver.add(complete.constraint, complete.goal_condition(goal))
        assert (solver.check() == z3.sat) == (goal in result.reachable)
    for trace in result.reachable.values():
        _states, _outputs, hits, error = interp.run_trace_detailed(proc, trace)
        assert error == "DivZero" and hits[0]
        assert hits[1:] == [set(), set(), set()]


def test_noninteger_wire_inputs_keep_the_original_decoder_path():
    proc = ir.Procedure(
        (), ("a",), (), ("y",), ir.Assign("y", ir.Var("a")),
        types={"a": types.BOOLEAN, "y": types.BOOLEAN})
    assert goal_projection.stateless_goals(proc, ENV, 4, None, None) is None


def test_stateful_goals_keep_the_full_temporal_relation():
    proc = ir.Procedure(
        ("x",), ("a",), (), ("y",),
        _sequence(ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))),
                  ir.Assign("y", ir.Var("x")),
                  ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)),
                        ir.Mark("later"), ir.Skip())),
        initial={"x": 0}, types={name: types.INTEGER for name in ("x", "a", "y")})
    env = {**ENV, "x": ranges.Range(0, 3)}
    assert goal_projection.stateless_goals(proc, env, 2, None, None) is None
    assert "later" in residual.decide_goals(proc, env, ("later",), steps=1).unreachable
    assert "later" in residual.decide_goals(proc, env, ("later",), steps=2).reachable


def test_custom_initial_terms_and_nonuniform_support_are_not_projected():
    proc = _table()
    assert goal_projection.stateless_goals(proc, ENV, 3, {}, None) is None
    prepared = closure_proof.prepare_positions(proc, ENV, 3)
    # Deliberately manufacture an internal bad plan to test the additional
    # uniformity premise. Public raw plans cannot pass the re-derivation gate.
    other = prepared.plan.steps[1].replaced(env={"a": ranges.Range(1, 1)})
    raw = prepared.plan.replaced(steps=(prepared.plan.steps[0], other, prepared.plan.steps[2]))
    forged = closure_proof._PreparedPositionPlan(raw, closure_proof._PREPARE_TOKEN)
    assert goal_projection.stateless_goals(proc, ENV, 3, None, forged) is None
    with pytest.raises(plan.PlanMismatch):
        residual.decide_goals(proc, ENV, GOALS, steps=3, plan=raw)


def test_exhausted_budget_does_not_construct_a_projection(monkeypatch):
    def forbidden(*_args):
        raise AssertionError("projection attempted after query budget exhausted")

    monkeypatch.setattr(goal_projection, "stateless_goals", forbidden)
    result = residual.decide_goals(_table(), ENV, GOALS, steps=8, max_queries=0)
    assert set(result.undecided) == set(GOALS)
    assert not result.reachable and not result.unreachable
    assert all(call.solver_calls == 0 for call in result.queries)


def test_unknown_and_unsupported_do_not_become_unreachable(monkeypatch):
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.behavior import exact_solver

    def unknown(*_args, **_kwargs):
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason="forced unknown")

    monkeypatch.setattr(exact_solver, "_check", unknown)
    result = residual.decide_goals(_table(), ENV, ("left", "absent"), steps=3)
    assert set(result.undecided) == {"left", "absent"}
    assert not result.reachable and not result.unreachable


@pytest.mark.parametrize("workers", [1, 2])
def test_worker_boundary_and_telemetry_keep_the_original_domain(workers):
    proc = _table()
    prepared = closure_proof.prepare_positions(proc, ENV, 4)
    result = residual.decide_goals(
        proc, ENV, GOALS, steps=4, plan=prepared, workers=workers,
        max_queries=None, memory_mb=16_384,
        telemetry_scope=QueryTelemetryScope(
            backend="behavior-z3", semantic_instance="test:stateless-domain-N4", horizon=4))
    assert not result.undecided and result.unreachable == {"failure", "never"}
    assert result.steps == 4
    for call in result.queries:
        assert call.telemetry is not None
        assert call.telemetry.horizon == 4
        assert call.telemetry.semantic_instance == "test:stateless-domain-N4"
        if call.answer is SolverAnswer.SAT:
            assert len(call.witness) == 4
            assert _hits(proc, call.witness) & set(call.goals)


@pytest.mark.parametrize("trace", [[], [{"a": 2}], [{"a": True}], [{"b": 0}],
                                   [{"a": 0}, {"a": 0}]])
def test_witness_lifting_does_not_repair_an_invalid_prefix(trace):
    projection = goal_projection.stateless_goals(_table(), ENV, 3, None, None)
    with pytest.raises(ValueError):
        projection.lift(trace)


def test_bound_provider_audits_full_length_source_and_ir_witnesses():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        if a == 0:\n"
              "            return {'y': 0}\n"
              "        return {'y': 1 // (a - 1)}\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=4).bind(4)
    domain = instance.domain
    result = instance.query(tuple(instance.goals()), workers=1)
    assert not result.undecided and result.reachable
    witnesses = instance.audit_requests(tuple(result.reachable.values()))
    assert all(type(witness) is AuditedWitness for witness in witnesses)
    assert all(len(witness.inputs) == 4 for witness in witnesses)
    assert instance.domain == domain and instance.instance._position_plan.bound == 4
    assert any(goal.startswith("safety:") for goal in result.reachable)

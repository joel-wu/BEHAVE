"""Literal control proofs cover every occurrence and retain real query evidence."""
import itertools

import pytest
import z3

from behave.sim.behavior import goal_projection, residual
from behave.sim.symbolic import interp, ir, ranges, typecheck as types
from behave.sim.core.query import SolverAnswer

pytestmark = pytest.mark.solver


def _proc(body, **kwargs):
    return ir.Procedure((), ('a',), (), ('y',),
        ir.Seq(ir.Assign('y', ir.Var('a')), body),
        types={'a': types.INTEGER, 'y': types.INTEGER}, **kwargs)


@pytest.mark.parametrize('left,right', tuple(itertools.product((True, False), repeat=2)))
def test_shared_command_identity_under_live_and_dead_paths(left, right):
    shared = ir.Mark('g')
    proc = _proc(ir.Seq(ir.If(ir.Lit(left), shared, ir.Skip()),
                        ir.If(ir.Lit(right), shared, ir.Skip())))
    dead = goal_projection.literal_false_goals(proc)
    assert ('g' in dead) == (not left and not right)
    assert ('g' in interp.run_step(proc, {'a': 0})[2]) == (left or right)


def test_unknown_control_and_loop_counts_are_not_assumed_dead():
    proc = _proc(ir.Seq(
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Mark('branch'), ir.Skip()),
        ir.For('i', ir.Var('a'), ir.Mark('loop'))))
    assert not goal_projection.literal_false_goals(proc)


@pytest.mark.parametrize('live_copy', [False, True])
def test_both_selectors_skip_only_literal_false_questions(live_copy):
    from types import SimpleNamespace
    from behave.sim.core.catalogue import CatalogueManifest, Goal
    from behave.sim.symbolic.encoder import validate_procedure
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines.behavior.generation import BehaviorExactProvider, BehaviorOccurrence
    from behave.sim.engines.behavior.generation_job import _requests
    from behave.sim.generation import StimulusField
    from tests.sim.test_api_path import schedule

    body = ir.Seq(ir.If(ir.Lit(False), ir.Mark('dead'), ir.Skip()),
                  ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Mark('live'), ir.Skip()))
    if live_copy:
        body = ir.Seq(body, ir.Mark('dead'))
    proc = _proc(body, signature=ProcedureSignature(
        (("a", FiniteType(1)),), (("y", FiniteType(1)),)))
    validate_procedure(proc)
    manifest = CatalogueManifest(tuple(Goal(
        goal, 'coverage', family='branch', provider='python-structural',
        event='T', policy='test/1') for goal in ('dead', 'live')))
    instance = SimpleNamespace(proc=proc, env={'a': ranges.Range(0, 1)},
                               catalogue_manifest=lambda: manifest)
    bindings = (("a", StimulusField("in", 0, 0, 0)),)
    occurrence = BehaviorOccurrence('seed:0', 0, {}, bindings)
    provider = BehaviorExactProvider(instance, lambda _: (occurrence,),
                                     provider_id='behavior->device')
    selected = provider.select(schedule([0], drain=2))
    isolated = _requests([dict(id=occurrence.id, rank=0, hits=(), bindings=bindings)],
                         provider.targetable_goals, manifest,
                         goal_projection.literal_false_goals(proc),
                         dict(proc.semantics.input_domains))
    assert [r.as_dict() for r in selected.requests] == [r.as_dict() for r in isolated]
    assert selected.requests[0].targets == (('dead', 'live') if live_copy else ('live',))
    assert provider.targetable_goals == ('dead', 'live')
    assert set(provider.catalogue_manifest()) == {'dead', 'live'}


@pytest.mark.parametrize('guard,dead', [
    (ir.Op('lt', ir.Lit(16), ir.Lit(0)), True),
    (ir.Op('land', ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Lit(False)), True),
    (ir.Op('lor', ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Lit(False)), False),
    (ir.Op('not', ir.Op('gt', ir.Lit(7), ir.Lit(0))), True),
    (ir.Op('ite', ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Lit(False), ir.Lit(False)), True),
])
def test_constant_boolean_algebra_matches_all_inputs(guard, dead):
    proc = _proc(ir.If(guard, ir.Mark('g'), ir.Skip()))
    assert ('g' in goal_projection.literal_false_goals(proc)) is dead
    hits = {a for a in range(-2, 3) if 'g' in interp.run_step(proc, {'a': a})[2]}
    assert bool(hits) is not dead


def test_named_activation_predicates_and_copied_trip_counts():
    proc = ir.Procedure((), ('a',), ('p', 'q', 'n'), (), ir._sequence((
        ir.Assign('p', ir.Lit(False)),
        ir.Assign('q', ir.Op('land', ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Var('p'))),
        ir.Assign('n', ir.Lit(0)),
        ir.If(ir.Var('q'), ir.Mark('branch'), ir.Skip()),
        ir.For('j', ir.Var('n'), ir.Mark('loop')))),
        types={'a': types.INTEGER, 'p': types.BOOLEAN, 'q': types.BOOLEAN,
               'n': types.INTEGER, 'j': types.INTEGER})
    assert goal_projection.literal_false_goals(proc) == {'branch', 'loop'}
    for a in range(3):
        assert not interp.run_step(proc, {'a': a})[2]


def test_if_conversion_preserves_the_proof_of_literal_dead_failure():
    from behave.sim.symbolic import cfg

    body = ir.If(ir.Op('lt', ir.Lit(3), ir.Lit(0)),
                 ir.Fail('NegativeShift', 'dead'), ir.Mark('live'))
    converted = cfg.if_convert(cfg.of_command(body))
    proc = ir.Procedure((), (), (), (), converted)
    # Production if-conversion clears branch decisions before assigning them
    # inside their block. Both assignments are false here, not a changing flag.
    assert goal_projection.literal_false_goals(proc) == {'dead'}
    assert interp.run_step(proc, {})[2] == {'live'}


@pytest.mark.parametrize('constant', [False, True])
def test_nested_literal_branch_retains_its_false_predicate_after_conversion(constant):
    from behave.sim.symbolic import cfg

    branch = ir.If(ir.Lit(constant), ir.Mark('then'), ir.Mark('else'))
    body = ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(1)), branch, ir.Mark('outside'))
    original = _proc(body)
    converted = _proc(cfg.if_convert(cfg.of_command(body)))
    dead = 'else' if constant else 'then'
    assert goal_projection.literal_false_goals(converted) == {dead}
    # Keep both source sites; this is not catalogue pruning. Inactive outer
    # control must not execute the constant branch or invent an event.
    assert {c.goal for c in ir.walk(converted.body) if isinstance(c, ir.Mark)} == {
        'then', 'else', 'outside'}
    for a in (0, 1, 2):
        assert interp.run_step(converted, {'a': a})[2:] == interp.run_step(original, {'a': a})[2:]


@pytest.mark.parametrize('start,stop,step', [
    (1, 6, 1), (6, 1, -1), (2, 9, 3), (9, 2, -3), (3, 3, 1), (1, 5, -2),
    (3, -1, -1), (-3, -8, -2), (-8, -3, 2), (-2, -5, 1),
])
def test_constant_range_keeps_all_goals_without_querying_its_impossible_entry(start, stop, step):
    from behave.sim.behavior.lowering import lower_source

    source = f'''class M:
    def reset(self):
        pass
    def process(self, a):
        total = 0
        if a:
            for k in range({start}, {stop}, {step}):
                total += k
        return {{"y": total}}
'''
    instance = lower_source(source, input_bounds={'a': (0, 2)}, bound=1)
    manifest = instance.catalogue
    loops = {g.event: name for name, g in manifest.items() if g.family == 'loop'}
    assert set(loops) == {'T', 'F'}
    dead = loops['F' if range(start, stop, step) else 'T']
    assert dead in goal_projection.literal_false_goals(instance.proc)
    assert dead in {c.goal for c in ir.walk(instance.proc.body) if isinstance(c, ir.Mark)}
    for a in (0, 1, 2):
        _state, outputs, hits, error = interp.run_step(instance.proc, {'a': a})
        assert not error
        assert outputs.transactions == [{'y': sum(range(start, stop, step)) if a else 0}]
        expected = {loops['T' if range(start, stop, step) else 'F']} if a else set()
        assert set(loops.values()) & hits == expected


def test_input_dependent_range_still_has_both_entry_outcomes():
    from behave.sim.behavior.lowering import lower_source

    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        total = 0
        for k in range(1, a):
            total += k
        return {"y": total}
'''
    lowered = lower_source(source, input_bounds={'a': (0, 3)}, bound=1)
    loops = {g.event: name for name, g in lowered.catalogue.items() if g.family == 'loop'}
    assert set(loops) == {'T', 'F'}
    assert not set(loops.values()) & goal_projection.literal_false_goals(lowered.proc)
    for a in range(4):
        _state, outputs, hits, error = interp.run_step(lowered.proc, {'a': a})
        assert not error and outputs.transactions == [{'y': sum(range(1, a))}]
        assert set(loops.values()) & hits == {loops['T' if a > 1 else 'F']}


@pytest.mark.parametrize('start,stop,step', [(3, -1, -1), (-8, -3, 2), (-2, -5, 1)])
def test_fixed_range_in_helper_keeps_native_events(start, stop, step):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.lowering import lower_source
    from behave.sim.behavior.source_plan import plan_source

    source = f'''def accumulate(a):
    total = a
    for k in range({start}, {stop}, {step}):
        total += k
    return total

class M:
    def reset(self): pass
    def process(self, a):
        return {{"y": accumulate(a) + accumulate(a + 1)}}
'''
    lowered = lower_source(source, input_bounds={'a': (0, 2)}, bound=1)
    loops = {g.event: name for name, g in lowered.catalogue.items() if g.family == 'loop'}
    assert set(loops) == {'T', 'F'}
    dead = loops['F' if range(start, stop, step) else 'T']
    assert dead in goal_projection.literal_false_goals(lowered.proc)
    assert dead in ir.goals_of(lowered.proc.body)
    goal_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=goal_plan)
    native = namespace['M']()
    for a in (0, 1, 2):
        native.reset()
        native_hits.clear()
        expected = native.process(a)
        _, outputs, hits, error = interp.run_step(lowered.proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert hits == set(native_hits)


@pytest.mark.parametrize('first,second,constant', [(False, False, True),
                                                 (False, True, False),
                                                 (True, False, False)])
def test_only_agreeing_definitions_establish_a_constant(first, second, constant):
    proc = ir.Procedure((), ('a',), ('p',), (), ir._sequence((
        ir.Assign('p', ir.Lit(first)),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
              ir.Assign('p', ir.Lit(second)), ir.Skip()),
        ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip()))),
        types={'a': types.INTEGER, 'p': types.BOOLEAN})
    assert ('g' in goal_projection.literal_false_goals(proc)) is constant
    assert any('g' in interp.run_step(proc, {'a': a})[2] for a in (0, 1)) is not constant


@pytest.mark.parametrize('body,state,initial', [
    (ir._sequence((ir.Assign('p', ir.Lit(False)), ir.Assign('p', ir.Lit(True)),
                   ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip()))), (), {}),
    (ir.Seq(ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip()),
            ir.Assign('p', ir.Lit(False))), ('p',), {'p': True}),
    (ir.Seq(ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip()),
            ir.Assign('p', ir.Lit(False))), (), {'p': True}),
    (ir.Seq(ir.Assign('p', ir.Op('eq', ir.Var('a'), ir.Lit(1))),
            ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip())), (), {}),
])
def test_rebindings_carried_values_and_inputs_are_not_constant(body, state, initial):
    proc = ir.Procedure(state, ('a',), () if state else ('p',), (), body,
                        types={'a': types.INTEGER, 'p': types.BOOLEAN}, initial=initial)
    assert 'g' not in goal_projection.literal_false_goals(proc)
    assert 'g' in interp.run_step(proc, {'a': 1})[2]


def test_cyclic_definition_is_unknown_not_a_recursive_proof():
    # Deliberately malformed IR: the production caller rejects its undefined
    # entry read; the recognizer must also terminate without inventing a proof.
    proc = ir.Procedure((), (), ('p',), (), ir.Seq(
        ir.Assign('p', ir.Op('not', ir.Var('p'))),
        ir.If(ir.Var('p'), ir.Mark('g'), ir.Skip())), types={'p': types.BOOLEAN})
    assert not goal_projection.literal_false_goals(proc)


@pytest.mark.parametrize('count', [-1, 0, 1, 3])
def test_literal_loop_and_else_side_are_distinguished(count):
    proc = _proc(ir.Seq(ir.For('i', ir.Lit(count), ir.Mark('loop')),
                       ir.If(ir.Lit(True), ir.Skip(), ir.Fail('Bad', 'failure'))))
    dead = goal_projection.literal_false_goals(proc)
    assert dead == ({'loop', 'failure'} if count <= 0 else {'failure'})


def test_registered_missing_goals_do_not_hide_a_live_occurrence():
    proc = _proc(ir.Mark('live'))
    proc.discharged = {'absent': 'registered false', 'live': 'stale proof'}
    assert goal_projection.literal_false_goals(proc) == {'absent'}
    assert 'unknown' not in goal_projection.literal_false_goals(proc)


def test_false_goals_share_one_real_unsat_without_unrolling(monkeypatch):
    proc = _proc(ir.If(ir.Lit(False),
        ir.Seq(ir.Mark('one'), ir.Fail('Bad', 'two')), ir.Skip()))
    env = {'a': ranges.Range(0, 1)}
    # Exercise the production large-occurrence path on a finite differential
    # fixture, independent of machine-specific size thresholds.
    monkeypatch.setattr(residual, '_preunroll_split', lambda *_args: True)
    original = residual.solve_exact
    calls = []
    def solve(formula, target, *args, **kwargs):
        assert z3.is_false(target)
        outcome = original(formula, target, *args, **kwargs)
        calls.append(outcome.answer)
        return outcome
    monkeypatch.setattr(residual, 'solve_exact', solve)
    def no_unroll(*_args, **_kwargs):
        raise AssertionError('literal-dead goals need no transition formula')
    monkeypatch.setattr(residual, 'unroll', no_unroll)
    result = residual.decide_goals(proc, env, ['one', 'two'], steps=1, workers=1)
    assert result.unreachable == {'one', 'two'}
    assert not result.reachable and not result.undecided
    assert calls == [SolverAnswer.UNSAT]
    assert len(result.queries) == 1 and result.queries[0].solver_calls == 1


def test_exhausted_budget_remains_unknown(monkeypatch):
    proc = _proc(ir.If(ir.Lit(False), ir.Mark('g'), ir.Skip()))
    monkeypatch.setattr(residual, '_preunroll_split', lambda *_args: True)
    result = residual.decide_goals(proc, {'a': ranges.Range(0, 1)}, ['g'],
                                  steps=1, workers=1, max_queries=0)
    assert not result.unreachable and set(result.undecided) == {'g'}
    assert all(q.solver_calls == 0 for q in result.queries)


def test_mixed_reachable_failure_and_literal_dead_goals_keep_full_audits(monkeypatch):
    from behave.sim.core.closure import close_over
    from tests.sim.test_query_feedback import SOURCE, _bound, _summary

    source = SOURCE.replace('return {"y": y}', 'return {"y": (a >> 1) // b}')
    monkeypatch.setattr(residual, '_preunroll_split', lambda *_args: True)
    after = close_over(_bound(source, steps=2), pool=[[{'a': 1, 'b': 1}] * 2],
                       workers=1, max_queries=32)
    monkeypatch.setattr(goal_projection, 'literal_false_goals', lambda _proc: frozenset())
    before = close_over(_bound(source, steps=2), pool=[[{'a': 1, 'b': 1}] * 2],
                        workers=1, max_queries=32)
    assert _summary(after) == _summary(before)
    assert after.query_transcript_complete
    assert any('negativeshift' in g and after.of(g).status.value == 'unreachable'
               for g in after.catalogue)
    assert any('divzero' in g and after.of(g).status.value == 'reached'
               for g in after.catalogue)


def test_local_guidance_omits_only_proved_false_occurrence_cones(monkeypatch):
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines import local_ir as exact
    from behave.sim.engines import probing

    proc = ir.Procedure((), ('a',), (), (), ir.Seq(
        ir.If(ir.Lit(False), ir.Mark('dead'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Mark('live'), ir.Skip())),
        types={'a': types.INTEGER}, signature=ProcedureSignature((('a', FiniteType(1)),), ()))
    built = []
    original = exact._goal_occurrence_keep
    def observe(proc, goal, *args, **kwargs):
        built.append(goal)
        return original(proc, goal, *args, **kwargs)
    monkeypatch.setattr(exact, '_goal_occurrence_keep', observe)
    result = exact.local_hit_any(proc, {'a': ranges.Range(0, 1)}, {}, ('dead',),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT and not built, result.detail
    result = exact.local_hit_any(proc, {'a': ranges.Range(0, 1)}, {}, ('dead', 'live'),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.SAT and built == ['live'], result.detail
    assert 'live' in interp.run_step(proc, result.candidate)[2]


def test_local_false_proof_does_not_bypass_state_admission():
    from behave.sim.engines import local_ir as exact
    from behave.sim.engines import probing

    proc = ir.Procedure(('s',), ('a',), (), ('y',),
        ir.Seq(ir.Assign('y', ir.Var('s')),
               ir.If(ir.Lit(False), ir.Mark('dead'), ir.Skip())),
        types={'s': types.INTEGER, 'a': types.INTEGER, 'y': types.INTEGER},
        initial={'s': 0})
    result = exact.local_hit_any(proc, {'a': ranges.Range(0, 1), 's': ranges.Range(0, 1)},
                                 {}, ('dead',),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED
    assert 'snapshot' in result.detail

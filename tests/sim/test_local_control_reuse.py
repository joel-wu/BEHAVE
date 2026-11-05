"""An impossible event cuts only paths that must execute that exact event."""

import itertools

import pytest
import z3

from behave.sim.symbolic import cfg, encoder, interp, ir, ranges
from behave.sim.behavior.goal_control import GoalControl
from behave.sim.behavior.lowering import lower_procedure
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing

pytestmark = pytest.mark.solver


def procedure(graph, locals=()):
    body = cfg.if_convert(graph)
    proc = ir.Procedure(('s',), ('a',), locals, (), body, initial={'s': 0},
                        signature=ProcedureSignature((('a', FiniteType(2)),), ()))
    ir._bind_control_flow(proc, ir._make_control_flow_authority(graph, body))
    return proc, {'a': ranges.Range(0, 3), 's': ranges.Range(0, 4)}


def nested(*, nonlinear=False):
    guard = (ir.Op('eq', ir.Op('mul', ir.Var('a'), ir.Var('a')),
                   ir.Op('add', ir.Var('s'), ir.Lit(1))) if nonlinear else
             ir.Op('eq', ir.Var('a'), ir.Var('s')))
    return procedure(cfg.CFG({
        'entry': cfg.Block('entry', [], cfg.Branch(guard, 'parent', 'exit')),
        'parent': cfg.Block('parent', [ir.Mark('parent')], cfg.Goto('loop')),
        'loop': cfg.Block('loop', [ir.For('i', 2, ir.If(
            ir.Op('eq', ir.Var('a'), ir.Var('i')), ir.Mark('child'),
            ir.Mark('other')))], cfg.Goto('exit')),
        'exit': cfg.Block('exit', [], cfg.Return()),
    }, 'entry'), ('i',))


def builds(monkeypatch):
    calls, encode = [], exact._encode_query_step

    def counted(*args, **kwargs):
        calls.append(kwargs['goals'])
        return encode(*args, **kwargs)

    monkeypatch.setattr(exact, '_encode_query_step', counted)
    return calls


def test_unsat_parent_omits_descendant_formula_not_goal_declarations(monkeypatch):
    # a*a == 5 needs a solver; a == 4 is now excluded by pinned-entry ranges.
    proc, env = nested(nonlinear=True)
    calls = builds(monkeypatch)
    prepared, proved = exact._LocalQueryPreparation(proc, env), set()
    answer = prepared.probe({'s': 4}, ('parent', 'child', 'other'), _proved_unsat=proved,
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.LOCAL_UNSAT
    assert calls == [('parent',)]
    assert {n.goal for n in ir.walk(proc.body) if isinstance(n, ir.Mark)} == {
        'parent', 'child', 'other'}
    assert {g for _, members, _ in proved for _, g in members} == {
        'parent', 'child', 'other'}
    calls.clear()
    # The preparation holds control implications, not another state's answer.
    answer = prepared.probe({'s': 0}, ('child',),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.SAT and calls
    assert answer.goal in interp.run_step(proc, answer.candidate, {'s': 0})[2]


@pytest.mark.parametrize('state', range(5))
def test_every_target_subset_agrees_with_exhaustive_replay(state):
    proc, env = nested()
    hits = set().union(*(interp.run_step(proc, {'a': a}, {'s': state})[2] for a in range(4)))
    for count in range(1, 4):
        for targets in itertools.combinations(('parent', 'child', 'other'), count):
            result = exact.local_hit_any(proc, env, {'s': state}, targets,
                timeout_ms=60_000)
            assert (result.outcome is probing.Outcome.SAT) == bool(hits.intersection(targets))
            if result.candidate is not None:
                assert result.goal in interp.run_step(proc, result.candidate, {'s': state})[2]
            else:
                assert result.outcome is probing.Outcome.LOCAL_UNSAT


def test_unknown_parent_is_not_a_proof(monkeypatch):
    proc, env = nested(nonlinear=True)
    calls = builds(monkeypatch)
    solver_type = type(z3.Solver())
    monkeypatch.setattr(solver_type, 'check', lambda self: z3.unknown)
    proved = set()
    result = exact._LocalQueryPreparation(proc, env).probe(
        {'s': 4}, ('parent', 'child', 'other'), _proved_unsat=proved,
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNKNOWN and not proved
    assert len(calls) > 1


def test_parent_proof_releases_an_earlier_unfinished_descendant(monkeypatch):
    proc, env = nested(nonlinear=True)
    prepared, pending, proved = exact._LocalQueryPreparation(proc, env), {}, set()
    solver_type = type(z3.Solver())
    check = solver_type.check
    with monkeypatch.context() as patch:
        patch.setattr(solver_type, 'check', lambda self: z3.unknown)
        result = prepared.probe({'s': 4}, ('parent', 'child'),
                                _proved_unsat=proved, _constructions=pending,
            timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNKNOWN and len(pending) > 1
    calls = []

    def completed(self):
        calls.append(self)
        return check(self)

    monkeypatch.setattr(solver_type, 'check', completed)
    result = prepared.probe({'s': 4}, ('parent', 'child'),
                            _proved_unsat=proved, _constructions=pending,
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.LOCAL_UNSAT
    assert len(calls) == 1 and not pending


def test_an_unsat_occurrence_does_not_prove_other_occurrences_of_same_goal():
    proc, env = procedure(cfg.CFG({
        'entry': cfg.Block('entry', [], cfg.Branch(
            ir.Op('eq', ir.Var('a'), ir.Var('s')), 'impossible', 'possible')),
        'impossible': cfg.Block('impossible', [ir.Mark('parent')], cfg.Return()),
        'possible': cfg.Block('possible', [ir.Mark('child'), ir.Mark('parent')], cfg.Return()),
    }, 'entry'))
    answer = exact.local_hit_any(proc, env, {'s': 4}, ('parent', 'child'),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.SAT
    assert answer.goal in interp.run_step(proc, answer.candidate, {'s': 4})[2]


def test_join_with_bypass_is_not_blocked_and_failure_is_not_a_mark():
    child = ir.Mark('child')
    graph = cfg.CFG({
        'entry': cfg.Block('entry', [], cfg.Branch(ir.Var('a'), 'cut', 'join')),
        'cut': cfg.Block('cut', [ir.Mark('parent')], cfg.Goto('join')),
        'join': cfg.Block('join', [child], cfg.Fail('DivZero', 'failure')),
    }, 'entry')
    proc, _env = procedure(graph)
    control = GoalControl(proc)
    assert id(child) not in control.blocked_marks({'parent'})
    assert id(child) not in control.blocked_marks({'failure'})


def test_loop_body_proof_does_not_cut_after_loop_and_shared_objects_need_all_paths():
    repeated, after = ir.Mark('repeated'), ir.Mark('after')
    graph = cfg.CFG({
        'entry': cfg.Block('entry', [ir.For('i', 0, ir.Seq(ir.Mark('parent'), repeated)),
                                    after, repeated], cfg.Return()),
    }, 'entry')
    proc, _env = procedure(graph, ('i',))
    blocked = GoalControl(proc).blocked_marks({'parent'})
    assert id(after) not in blocked and id(repeated) not in blocked


def test_legal_domain_and_fixed_inputs_remain_part_of_the_local_proof():
    proc, env = nested()
    prepared = exact._LocalQueryPreparation(proc, env)
    for options in ({'fixed_inputs': {'a': 0}},
                    {'input_constraints': lambda pre: (pre['a'] == 0,)}):
        closed = prepared.probe({'s': 1}, ('parent', 'child'), **options,
            timeout_ms=60_000)
        assert closed.outcome is probing.Outcome.LOCAL_UNSAT
        open_query = prepared.probe({'s': 1}, ('child',),
            timeout_ms=60_000)
        assert open_query.outcome is probing.Outcome.SAT


def test_retained_control_is_checked_before_public_query():
    proc, env = nested()
    proc._graph.blocks['parent'].instrs.clear()
    answer = exact.local_hit_any(proc, env, {'s': 4}, ('parent', 'child'),
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED
    assert 'control-flow-authority-changed' in answer.detail


def test_lowered_nested_control_implications_match_the_complete_formula_and_replay():
    source = '''class M:
    def reset(self):
        pass
    def process(self, a, b):
        y = 0
        for i in range(3):
            if a == b and a + b == 5:
                for j in range(2):
                    if a == i + j:
                        y = 1
                    else:
                        y = 2
            if a == 2:
                y = 3
        return {"y": y}
'''
    proc = lower_procedure(source, input_widths=(2, 2),
        signature=ProcedureSignature((('a', FiniteType(2)), ('b', FiniteType(2))),
                                     (('y', FiniteType(2)),)))
    env = {'a': ranges.Range(0, 3), 'b': ranges.Range(0, 3)}
    exact.validate_procedure(proc)
    control = GoalControl(proc)
    assert control.loops and any(loop.flow is not None for loop in ir.walk(proc.body)
                                 if isinstance(loop, ir.For))
    full = encoder.encode_step(proc, env=env)
    identities = {}
    for command in ir.walk(proc.body):
        if isinstance(command, ir.Mark):
            identities.setdefault(command.goal, set()).add(id(command))
    replay_hits = [interp.run_step(proc, {'a': a, 'b': b})[2]
                   for a, b in itertools.product(range(4), repeat=2)]
    implications = 0
    for parent in identities:
        blocked = control.blocked_marks({parent})
        children = {goal for goal, nodes in identities.items() if nodes <= blocked} - {parent}
        if not children:
            continue
        implications += len(children)
        solver = z3.Solver()
        solver.add(full.constraint, z3.Not(full.goal_condition(parent)),
                   z3.Or([full.goal_condition(goal) for goal in children]))
        assert solver.check() == z3.unsat, (parent, children)
        assert all(parent in hits or not children & hits for hits in replay_hits)
    assert implications > 10  # Includes source CFG edges flattened out of lexical nesting.

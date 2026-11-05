"""Dependent SSA coordinates are not a simultaneous successor image."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.rtl.monitor_template import (
    _DefinitionDependencies, _definition_layers, complete_monitor_model,
)
from behave.sim.rtl.source_member import SourceMemberPlan, source_member_hint
from .test_rtl_source_member import _interval


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('reverse', (False, True))
@pytest.mark.parametrize('kind', ('int', 'bv', 'array'))
def test_layered_completion_uses_definitions_not_order_or_stale_model(private, reverse, kind):
    ctx = z3.Context() if private else None
    sort = (z3.IntSort(ctx) if kind == 'int' else z3.BitVecSort(8, ctx)
            if kind == 'bv' else z3.ArraySort(z3.IntSort(ctx), z3.IntSort(ctx)))
    first, second, third = (z3.Const(name, sort) for name in ('layer_a', 'layer_b', 'layer_c'))
    value = z3.K(z3.IntSort(ctx), z3.IntVal(7, ctx)) if kind == 'array' else 7
    definitions = (first == value, second == first, third == second)
    if reverse:
        definitions = tuple(reversed(definitions))
    model = z3.Model(ctx)
    stale = z3.K(z3.IntSort(ctx), z3.IntVal(99, ctx)) if kind == 'array' else (
        z3.IntVal(99, ctx) if kind == 'int' else z3.BitVecVal(99, 8, ctx))
    model.update_value(first.decl(), stale)
    original = str(model)
    result = complete_monitor_model(model, definitions, (*definitions, third == value),
                                    frame_sizes=(3,), check=lambda: None)
    assert result is not None
    assert all(z3.is_true(result.eval(root, model_completion=False)) for root in definitions)
    assert str(model) == original
    assert complete_monitor_model(model, definitions, (*definitions, third != value),
                                  frame_sizes=(3,), check=lambda: None) is None


@pytest.mark.parametrize('bad', ('cycle', 'self', 'duplicate', 'not-equation'))
def test_invalid_definition_graph_has_no_hint_not_a_negative_verdict(bad):
    a, b = z3.Ints('invalid_layer_a invalid_layer_b')
    definitions = {'cycle': (a == b, b == a), 'self': (a == a,),
                   'duplicate': (a == 1, a == 1), 'not-equation': (a > 0,)}[bad]
    assert _definition_layers(definitions, check=lambda: None) is None
    assert complete_monitor_model(z3.Model(), definitions, definitions,
                                  frame_sizes=(len(definitions),), check=lambda: None) is None
    solver = z3.Solver()
    solver.add(*definitions)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('action_code', (0, 1, 2, 3))
@pytest.mark.parametrize('private', (False, True))
def test_source_member_completes_successor_before_boundary(private, action_code):
    ctx = z3.Context() if private else None
    action, successor, boundary = z3.BitVecs('layer_action successor boundary', 3, ctx=ctx)
    definitions = (successor == action + 1, boundary == successor)
    roots = (action == action_code, *definitions, boundary == action_code + 1)
    interval = replace(_interval((action,), (z3.And(*roots),)), step_definitions=(definitions,))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (interval,)))
    model = source_member_hint(z3.Model(ctx), (history,), roots, check=lambda: None)
    assert model is not None
    assert all(z3.is_true(model.eval(root, model_completion=False)) for root in roots)
    assert source_member_hint(z3.Model(ctx), (history,), (*roots, boundary == 7),
                              check=lambda: None) is None


def test_layers_keep_independent_images_batched_and_share_expression_walk():
    x = z3.Int('layer_shared_input')
    common = x + 1
    for _ in range(20):
        common = common * common + 1
    a, b, c = z3.Ints('shared_a shared_b shared_c')
    calls = []
    layers = _definition_layers((a == common, b == common, c == a + b),
                                check=lambda: calls.append(None))
    assert tuple(map(len, layers)) == (2, 1)
    assert len(calls) < 300  # Traverse the DAG, not its exponential tree.

    def stop():
        raise InterruptedError('owner cancelled')

    with pytest.raises(InterruptedError):
        _definition_layers((a == common,), check=stop)


@pytest.mark.parametrize('private', (False, True))
def test_definition_support_is_shared_across_intervals(private, monkeypatch):
    ctx = z3.Context() if private else None
    common = z3.BitVec('support_input', 16, ctx) + 1
    for _ in range(24):
        common = common * common + 1
    frames = tuple((z3.BitVec(f'support_{i}', 16, ctx) == common,) for i in range(40))
    dependencies = _DefinitionDependencies((term for frame in frames for term in frame),
                                         check=lambda: None)
    calls, original = {}, z3.ExprRef.children
    def counted(term):
        key = term.ctx, term.get_id()
        calls[key] = calls.get(key, 0) + 1
        return original(term)
    monkeypatch.setattr(z3.ExprRef, 'children', counted)
    for frame in frames:
        assert tuple(map(len, _definition_layers(frame, check=lambda: None,
                                                dependencies=dependencies))) == (1,)
    assert calls[(common.ctx, common.get_id())] == 1
    assert sum(calls.values()) < 100


def test_dependency_index_does_not_inline_other_frames_or_cross_contexts():
    a, b, c = z3.Ints('external_a external_b external_c')
    external, frame = (a == b,), (b == a + 1, c == b)
    dependencies = _DefinitionDependencies((*external, *frame), check=lambda: None)
    layers = _definition_layers(frame, check=lambda: None, dependencies=dependencies)
    assert tuple(tuple(lhs.decl().name() for lhs, _ in layer) for layer in layers) == (
        ('external_b',), ('external_c',))
    assert complete_monitor_model(z3.Model(), (*external, *frame), (*external, *frame),
                                  frame_sizes=(1, 2), check=lambda: None) is None
    ctx = z3.Context()
    other_a, other_b = z3.Ints('external_a external_b', ctx)
    other = (other_b == other_a, other_a == 3)
    assert tuple(map(len, _definition_layers(other, check=lambda: None,
                                            dependencies=dependencies))) == (1, 1)
    assert all(term.ctx != ctx for term in dependencies.roots)


@pytest.mark.parametrize('private', (False, True))
def test_constructor_order_is_a_checked_member_proposal_not_a_proof(private, monkeypatch):
    from behave.sim.rtl import monitor_template
    from behave.sim.rtl.symbolic_state import FixedBindings

    ctx = z3.Context() if private else None
    a, b = z3.BitVecs('constructor_a constructor_b', 8, ctx=ctx)
    definitions = (a == 3, b == a + 1)
    interval = replace(_interval((z3.BitVecVal(0, 1, ctx),), (z3.And(*definitions),)),
                       step_definitions=(definitions,), member_layers=(((definitions[0],), (definitions[1],)),))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (interval,)))
    def forbidden(*args, **kwargs):
        pytest.fail('constructor grouping must not rediscover the instantiated DAG')
    monkeypatch.setattr(monitor_template, '_definition_layers', forbidden)
    result = source_member_hint(z3.Model(ctx), (history,), (*definitions, b == 4), check=lambda: None)
    assert result is not None
    assert all(z3.is_true(result.eval(root)) for root in definitions)
    assert source_member_hint(z3.Model(ctx), (history,), (*definitions, b != 4), check=lambda: None) is None
    frames = interval._member_frames(check=lambda: None)
    assert frames.proposal_only
    # An existential completion plan cannot silently become universal facts.
    defect = b != 4
    assert monitor_template.reduce_monitor_defect(defect, (), FixedBindings((), (), ()),
        check=lambda: None, _frames=frames).eq(defect)


def test_stale_constructor_grouping_falls_back_to_the_actual_equations():
    a, b = z3.Ints('stale_constructor_a stale_constructor_b')
    old, current = (a == 3, b == a), (b == a, a == 7)
    from behave.sim.rtl.monitor_template import _MonitorFrames
    frames = _MonitorFrames(current, (2,), check=lambda: None, proposal_layers=((old,),))
    assert not frames.proposal_only
    result = complete_monitor_model(z3.Model(), (), (*current, b == 7), check=lambda: None, _frames=(frames,))
    assert result is not None


def test_incorrect_constructor_proposal_cannot_validate_a_witness():
    a, b = z3.BitVecs('wrong_constructor_a wrong_constructor_b', 8)
    definitions = (a == 3, b == a + 1)
    interval = replace(_interval((z3.BitVecVal(0, 1),), (z3.And(*definitions),)),
                       step_definitions=(definitions,), member_layers=((definitions,),))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (interval,)))
    assert source_member_hint(z3.Model(), (history,), definitions, check=lambda: None) is None


@pytest.mark.parametrize('seed', range(12))
def test_shared_dependency_layers_match_independent_local_graph(seed):
    import random
    rng = random.Random(seed)
    atoms = z3.Ints(' '.join(f'graph_{seed}_{i}' for i in range(18)))
    inputs = z3.Ints(f'graph_input_{seed}_a graph_input_{seed}_b')
    frames = []
    for start in range(0, len(atoms), 6):
        frame = []
        for index in range(start, start + 6):
            operands = list(inputs) + list(atoms[:index])
            frame.append(atoms[index] == rng.choice(operands) + rng.choice(operands))
        rng.shuffle(frame)
        frames.append(tuple(frame))
    dependencies = _DefinitionDependencies((term for frame in frames for term in frame),
                                         check=lambda: None)
    for frame in frames:
        owned = {equation.arg(0).get_id() for equation in frame}
        graph = {}
        for equation in frame:
            pending, seen, found = [equation.arg(1)], set(), set()
            while pending:
                node = pending.pop()
                key = node.get_id()
                if key in seen:
                    continue
                seen.add(key)
                if key in owned:
                    found.add(key)
                else:
                    pending.extend(node.children())
            graph[equation.arg(0).get_id()] = found
        levels = {}
        while len(levels) != len(graph):
            for key, children in graph.items():
                if key not in levels and children <= levels.keys():
                    levels[key] = max((levels[child] for child in children), default=0) + 1
        layers = _definition_layers(frame, check=lambda: None, dependencies=dependencies)
        assert {lhs.get_id(): level for level, layer in enumerate(layers, 1)
                for lhs, _ in layer} == levels


def test_source_member_shares_index_across_intervals_and_checks_all_roots(monkeypatch):
    from behave.sim.rtl import monitor_template
    common = z3.BitVec('member_shared_input', 8)
    for _ in range(16):
        common = common * common + 1
    definitions = tuple(z3.BitVec(f'member_shared_{i}', 8) == common for i in range(12))
    intervals = tuple(replace(_interval((z3.BitVecVal(0, 1),), (equation,)),
                              step_definitions=((equation,),)) for equation in definitions)
    histories = (SimpleNamespace(member_plan=SourceMemberPlan((), intervals)),)
    observed, original = [], monitor_template._definition_layers
    def counted(*args, **kwargs):
        observed.append(kwargs['dependencies'])
        return original(*args, **kwargs)
    monkeypatch.setattr(monitor_template, '_definition_layers', counted)
    result = source_member_hint(z3.Model(), histories, definitions, check=lambda: None)
    assert result is not None
    assert len(observed) == len(intervals) and len({id(item) for item in observed}) == 1
    assert all(z3.is_true(result.eval(root)) for root in definitions)
    assert source_member_hint(z3.Model(), histories, (*definitions, z3.Not(definitions[0])),
                              check=lambda: None) is None


def test_interrupted_dependency_walk_can_resume_without_partial_answers():
    a, b, c = z3.Ints('resume_a resume_b resume_c')
    frame = (c == a + b, b == a + 1, a == 2)
    dependencies = _DefinitionDependencies(frame, check=lambda: None)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 8:
            raise InterruptedError('owner cancelled')
    with pytest.raises(InterruptedError):
        _definition_layers(frame, check=cancel, dependencies=dependencies)
    assert tuple(map(len, _definition_layers(frame, check=lambda: None,
                                            dependencies=dependencies))) == (1, 1, 1)

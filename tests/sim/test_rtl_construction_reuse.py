"""Shared source syntax is reusable; values and verdicts are not."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.rtl.source_direct import DirectSourceCompiler
from behave.sim.rtl.source_member import SourceMemberPlan, source_member_hint
from .test_rtl_source_member import _interval


def compiler(subscriptions):
    owner = object.__new__(DirectSourceCompiler)
    owner.machine = SimpleNamespace(subscriptions=subscriptions)
    return owner


def original_events(subscriptions, before, pins):
    result = []
    for sub in subscriptions:
        if sub.storage not in pins:
            continue
        old, new = before[sub.storage], pins[sub.storage]
        if old is None or new is None:
            result.append(sub)
            continue
        width = max(old.size(), new.size())
        old, new = z3.ZeroExt(width-old.size(), old), z3.ZeroExt(width-new.size(), new)
        changed = old ^ new
        event = changed & new if sub.edge == 'rise' else changed & old if sub.edge == 'fall' else changed
        if not z3.is_false(z3.simplify(event & sub.mask != 0)):
            result.append(sub)
    return tuple(result)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('widths', ((1, 1), (3, 8), (8, 3), (64, 64), (257, 129)))
def test_shared_edges_preserve_literal_symbolic_and_unknown_drives(private, widths):
    ctx = z3.Context() if private else None
    a, b = widths
    subscriptions = tuple(SimpleNamespace(storage='bus', edge=edge, mask=mask, process=i)
        for i, (edge, mask) in enumerate((edge, mask) for edge in ('rise', 'fall', 'any')
            for mask in (0, 1, 2, -1, 1 << max(widths))))
    subscriptions += (SimpleNamespace(storage='not-driven', edge='rise', mask=1, process=99),)
    owner = compiler(subscriptions)
    values = [(z3.BitVecVal(old, a, ctx), z3.BitVecVal(new, b, ctx))
              for old in (0, 1, -1, 7) for new in (0, 1, -1, 9)]
    x, y = z3.BitVec('edge_x', a, ctx), z3.BitVec('edge_y', b, ctx)
    values += [(x, y), (x, z3.BitVecVal(0, b, ctx)), (None, y), (x, None)]
    if a == b:
        values += [(x, x), (x ^ x, z3.BitVecVal(0, b, ctx))]
    for old, new in values:
        before, pins = {'bus': old}, {'bus': new}
        expected = original_events(subscriptions, before, pins)
        assert owner._driven_events(before, pins, None) == expected
        assert owner._driven_events({}, pins, before) == expected


def test_fixed_fanout_decodes_once_and_symbolic_fanout_rewrites_once(monkeypatch):
    subscriptions = tuple(SimpleNamespace(storage='bus', edge='rise', mask=1, process=i)
                          for i in range(512))
    owner = compiler(subscriptions)
    before, pins = {'bus': z3.BitVecVal(0, 32)}, {'bus': z3.BitVecVal(1, 32)}
    decoded, simplified = [], []
    as_long, simplify = z3.BitVecNumRef.as_long, z3.simplify
    monkeypatch.setattr(z3.BitVecNumRef, 'as_long', lambda self: (decoded.append(self), as_long(self))[1])
    monkeypatch.setattr(z3, 'simplify', lambda term: (simplified.append(term), simplify(term))[1])
    assert owner._driven_events(before, pins, None) == subscriptions
    assert len(decoded) == 2 and not simplified
    before['bus'] = z3.BitVec('fanout_input', 32)
    assert owner._driven_events(before, pins, None) == subscriptions
    assert len(simplified) == 1
    # No decision survives a changed drive on the same compiler.
    assert owner._driven_events(pins, pins, None) == ()


@pytest.mark.parametrize('private', (False, True))
def test_member_layout_shared_across_models_not_answers(private, monkeypatch):
    from behave.sim.rtl import monitor_template

    ctx = z3.Context() if private else None
    action, first, second = z3.BitVecs('reuse_action reuse_first reuse_second', 3, ctx=ctx)
    definitions = (first == action + 1, second == first)
    step = replace(_interval((action,), (z3.And(*definitions),)), step_definitions=(definitions,))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (step,)))
    calls, original = [], monitor_template._definition_layers
    def counted(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)
    monkeypatch.setattr(monitor_template, '_definition_layers', counted)
    for value in range(5):
        candidate = z3.Model(ctx)
        candidate.update_value(action.decl(), z3.BitVecVal(value, 3, ctx))
        roots = (*definitions, action == value, second == value + 1)
        result = source_member_hint(candidate, (history,), roots, check=lambda: None)
        assert result is not None
        assert source_member_hint(candidate, (history,), (*roots, second == value),
                                  check=lambda: None) is None
    assert len(calls) == 1
    replacement = replace(step, step_definitions=((first == 7, second == first),))
    assert replacement._member_frames_cache is None
    assert replacement._member_frames(check=lambda: None) is not step._member_frames_cache
    assert len(calls) == 2


def test_cancelled_member_layout_is_not_published(monkeypatch):
    from behave.sim.rtl import monitor_template

    action, value = z3.BitVecs('cancel_action cancel_value', 3)
    step = replace(_interval((action,), (value == action,)), step_definitions=((value == action,),))
    original = monitor_template._MonitorFrames
    def cancelled(*args, **kwargs):
        original(*args, **kwargs)
        raise InterruptedError('cancelled after construction')
    monkeypatch.setattr(monitor_template, '_MonitorFrames', cancelled)
    with pytest.raises(InterruptedError):
        step._member_frames(check=lambda: None)
    assert step._member_frames_cache is None


@pytest.mark.parametrize('array', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_successor_deltas_preserve_full_roots_and_carry_exact_identities(array, private):
    from behave.sim.symbolic import ir, ranges, typecheck as types
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.step_template import StepTemplate, _leaves, _tree
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('held', 'q'), ('a',), (), (),
        ir._sequence((ir.Assign('q', ir.Op('and', ir.Op('add', ir.Var('q'), ir.Var('a')), ir.Lit(15))),
                      ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(3)), ir.Mark('hit'), ir.Skip()))),
        initial={'held': [0, 0, 0] if array else 0, 'q': 0},
        types={'held': types.Array(3)} if array else {},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    env = {name: ranges.Range(0, 15) for name in ('held', 'q', 'a')}
    template = StepTemplate(proc, env).normalized()
    ctx = z3.Context() if private else template.context
    if private:
        template = template.translate(ctx)
    delta = template.successor_deltas()
    assert delta.state_names == ('q',)
    full = template.encode()
    changed = delta.encode(full.pre)
    merged = {**full.pre, **changed.state_post}
    different = [full.ok != changed.ok, full.error != changed.error]
    different.extend(term != changed.hits[name] for name, term in full.hits.items())
    for name, value in full.state_post.items():
        different.extend(a != b for a, b in zip(_leaves(_tree(value)),
                                                _leaves(_tree(merged[name])), strict=True))
    solver = z3.Solver(ctx=ctx)
    solver.add(full.constraint, changed.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


def test_identical_event_roots_pack_once_without_removing_catalogue_entries():
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.step_template import StepTemplate

    names = tuple(f'event_{i}' for i in range(128))
    proc = ir.Procedure((), (), (), (), ir._sequence(tuple(ir.Mark(name) for name in names)),
                        signature=ProcedureSignature((), ()))
    original = StepTemplate(proc, {}).normalized()
    view = original.project({name: (name,) for name in names})
    assert len(view._layout['hits']) == 128
    assert len(set(view._layout['hits'].values())) == 1
    assert view._image.num_args() < 10
    result = view.encode()
    assert set(result.hits) == set(names)
    assert all(z3.is_true(z3.simplify(term)) for term in result.hits.values())


@pytest.mark.parametrize('private', (False, True))
def test_repeated_bindings_reuse_types_but_check_each_new_width(private, monkeypatch):
    from behave.sim.symbolic import ir, ranges
    from behave.sim.symbolic.encoder import EncodingError
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.step_template import StepTemplate, _leaves
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('q',), ('a',), (), (), ir.Assign('q', ir.Var('a')),
                        initial={'q': 0}, signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)})
    if private:
        template = template.translate(z3.Context())
    template = template.normalized().successor_deltas()
    formal = {term.get_id() for shape in template._parameters.values() for term in _leaves(shape)}
    widths = dict(template._carriers)
    queried, original = [], z3.BitVecRef.size
    def size(value):
        if value.get_id() in formal:
            queried.append(value)
        return original(value)
    monkeypatch.setattr(z3.BitVecRef, 'size', size)
    for value in (0, 1, -1, 7):
        pre = {name: z3.BitVecVal(value, 3, template.context) for name in widths}
        result = template.encode(pre)
        assert z3.is_true(z3.simplify(result.state_post['q'] ==
            z3.SignExt(widths['q'] - 3, pre['a'])))
    assert queried == []
    with pytest.raises(EncodingError, match='exceeds the prepared transition carrier'):
        template.encode({'a': z3.BitVec('too_wide', widths['a'] + 1, template.context)})


@pytest.mark.parametrize('private', (False, True))
def test_native_view_cache_is_bounded_by_observation_identity(private, monkeypatch):
    from behave.sim.symbolic import ir, ranges
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.step_template import StepTemplate
    from behave.sim.core.contract import FiniteType
    from behave.sim.rtl import source_direct
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    from behave.sim.rtl.source_relation import SourceIntervalRelation

    proc = ir.Procedure(('q',), ('a',), (), (), ir.Assign('q', ir.Var('a')),
        initial={'q': 0}, signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)})
    if private:
        template = template.translate(z3.Context())
    original = SourceIntervalRelation(template, template, (), (), (), (), object())
    prepared = object.__new__(PreparedSourceExact)
    prepared.machine, prepared._native_views = object(), None
    compilers, normalized = [], []
    def compiler(machine):
        assert machine is prepared.machine
        result = object()
        compilers.append(result)
        return result
    normalize = StepTemplate.normalized
    def counted(view):
        normalized.append(view)
        return normalize(view)
    monkeypatch.setattr(source_direct, 'NativeMemberSourceCompiler', compiler)
    monkeypatch.setattr(StepTemplate, 'normalized', counted)
    cover = prepared._native_interval(original, member=False)
    proposal = prepared._native_interval(original, member=True)
    for _ in range(3):
        assert prepared._native_interval(original, member=False) is cover
        assert prepared._native_interval(original, member=True) is proposal
    assert len(normalized) == 2 and len(compilers) == 1
    assert cover.source is proposal.source and cover.drive is proposal.drive
    assert cover.direct is original.direct and proposal.direct is compilers[0]
    assert original.source is template  # No mutation of the universal view.
    foreign = replace(original, source=template.translate(z3.Context()))
    changed = prepared._native_interval(foreign, member=True)
    assert changed is not proposal and changed.source.context == foreign.source.context
    assert prepared._native_views[0] is foreign and len(compilers) == 2
    assert prepared._native_interval(original, member=True) is not proposal
    assert len(compilers) == 3  # The replaced owner is evicted, not accumulated.


def test_cancelled_native_view_keeps_the_last_complete_owner(monkeypatch):
    from behave.isolation import preparation_control
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    from behave.sim.rtl.source_relation import SourceIntervalRelation

    prepared = object.__new__(PreparedSourceExact)
    before = prepared._native_views = (object(), object(), None)
    calls = []
    template = SimpleNamespace(normalized=lambda: calls.append(None))
    interval = SourceIntervalRelation(template, template, (), (), (), ())
    def check():
        if len(calls) == 2:
            raise InterruptedError('cancelled after normalization')
    monkeypatch.setattr(preparation_control, 'check_preparation', check)
    with pytest.raises(InterruptedError, match='after normalization'):
        prepared._native_interval(interval, member=False)
    assert prepared._native_views is before

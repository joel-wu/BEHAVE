"""Reuse unchanged branch facts without narrowing or inventing finite bounds."""

from dataclasses import fields, replace

import pytest

from behave.sim.behavior import flow_facts
from behave.sim.symbolic import ir, ranges
from behave.sim.behavior.lower_static_refs import _STATIC_SCALAR, _StaticIssue, _StaticRef
from behave.sim.behavior.source_values import NONE, SCALAR, ValueContract


def facts(finite, bounds, unknown=()):
    return flow_facts.FlowFacts({}, {}, {}, finite, bounds, frozenset(unknown), {})


def test_flow_owner_snapshots_and_restores_all_tables_without_aliases():
    initial = {'x': ranges.Range(0, 3)}
    state = flow_facts.FlowState.seeded(initial)
    state.lengths['xs'] = (2,)
    state.carriers['xs'] = 'tuple'
    state.contracts['x'] = SCALAR
    state.unknown_ranges.add('open')
    state.static_values['x'] = _STATIC_SCALAR
    saved = state.snapshot()
    assert state.ranges is not initial
    assert {item.name for item in fields(state)} == {item.name for item in fields(saved)}
    for item in fields(saved):
        current = getattr(state, item.name)
        assert current is not getattr(saved, item.name)
        current.clear()
        assert getattr(saved, item.name)
    state.restore(saved)
    assert state.snapshot() == saved
    for item in fields(saved):
        assert getattr(state, item.name) is not getattr(saved, item.name)
    saved.contracts.clear()
    assert state.contracts == {'x': SCALAR}
    assert flow_facts.FlowState().snapshot() == facts({}, {})


def test_expression_restore_preserves_allocations_but_restores_option_presence():
    saved = replace(facts({'x': ranges.Range(0, 1)}, {'x': ranges.Bounds(0, 1)}),
                    contracts={'option': NONE, 'regular': SCALAR},
                    static_values={'ref': _STATIC_SCALAR})
    state = flow_facts.FlowState(
        lengths={'new': (2,)}, carriers={'new': 'tuple'},
        contracts={'option': SCALAR, 'regular': ValueContract.fixed((2,)), 'new': SCALAR},
        ranges={'x': ranges.Range(9, 9)}, bounds={'x': ranges.Bounds(9, 9)},
        unknown_ranges={'x'}, static_values={'stale': _STATIC_SCALAR})
    lengths, carriers, contracts = state.lengths, state.carriers, state.contracts
    state.restore_expression(saved, ('option', 'absent'))
    assert state.lengths is lengths and state.carriers is carriers and state.contracts is contracts
    assert state.contracts == {'option': NONE, 'regular': ValueContract.fixed((2,)), 'new': SCALAR}
    assert state.ranges == saved.ranges and state.ranges is not saved.ranges
    assert state.bounds == saved.bounds and state.bounds is not saved.bounds
    assert not state.unknown_ranges
    assert state.static_values == saved.static_values and state.static_values is not saved.static_values


def test_flow_lifetime_keeps_updated_caller_facts_and_returned_names():
    state = flow_facts.FlowState.seeded({'caller': ranges.Range(0, 3)})
    before = state.names()
    state.ranges['caller'] = ranges.Range(2, 2)
    state.bounds['caller'] = ranges.Bounds(2, 2)
    for item in fields(state):
        table = getattr(state, item.name)
        if isinstance(table, set):
            table.update(('temporary', 'returned'))
        else:
            table.update(temporary='dead', returned='live')
    state.retain_names(before, frozenset({'returned'}))
    assert state.ranges['caller'] == ranges.Range(2, 2)
    assert state.bounds['caller'] == ranges.Bounds(2, 2)
    assert state.names() == {name: names | {'returned'} for name, names in before.items()}
    assert all('temporary' not in names for names in before.values())


def test_path_restore_cannot_restore_or_erase_procedure_storage():
    from behave.sim.behavior.lowering import _Lowerer

    low = _Lowerer([], env={'x': ranges.Range(0, 3)})
    storage = (low.storage.shapes, low.storage.storage_contracts,
               low.storage.declaration_contracts, low.storage.length_images, low.storage.capacities)
    saved = low._flow_facts()
    for table in storage:
        table['allocated_on_one_arm'] = 'retained'
    low.flow.contracts['only_this_arm'] = SCALAR
    low._set_flow_facts(saved)
    assert low._flow_facts() == saved
    assert all(table == {'allocated_on_one_arm': 'retained'} for table in storage)
    assert all(current is previous for current, previous in zip(
        (low.storage.shapes, low.storage.storage_contracts, low.storage.declaration_contracts,
         low.storage.length_images, low.storage.capacities), storage))
    assert not set(vars(low)) & {'env', 'bounds', 'lengths', 'carriers',
                                'value_contracts', '_range_unknown', 'static_values'}


def test_unchanged_finite_facts_are_not_reallocated(monkeypatch):
    finite = {f'x{i}': ranges.Range(-i, i) for i in range(100)}
    original = facts(finite, {k: ranges.Bounds.of(v) for k, v in finite.items()})
    monkeypatch.setattr(ranges.Bounds, 'finite', lambda self: pytest.fail('unchanged projection'))
    result = flow_facts.assume(original, ir.Lit(True), True)
    assert result.ranges == finite
    assert all(result.ranges[k] is finite[k] for k in finite)
    assert original.ranges == finite


@pytest.mark.parametrize('truth', [False, True])
def test_reused_branch_facts_match_full_projection(truth):
    original = facts({'x': ranges.Range(-9, 9), 'stale': ranges.Range(0, 1)},
                     {'x': ranges.Bounds(-9, 9), 'stale': ranges.Bounds(0, 8),
                      'open': ranges.Bounds(None, 4)}, ('open', 'absent'))
    predicate = ir.Op('lt', (ir.Var('x'), ir.Lit(3)))
    narrowed = ranges.assume(predicate, truth, original.bounds)
    expected = {k: v.finite() for k, v in narrowed.items() if v.finite() is not None}
    result = flow_facts.assume(original, predicate, truth)
    assert result.ranges == expected
    assert result.bounds == narrowed
    assert result.unknown_ranges == frozenset({'absent'})
    assert original.ranges['stale'] == ranges.Range(0, 1)


def test_shared_bounds_join_only_changed_values(monkeypatch):
    shared = ranges.Bounds(0, 9)
    left = facts({'x': ranges.Range(0, 9)}, {'x': shared, 'open': ranges.Bounds(1, None)})
    right = replace(left, bounds={**left.bounds, 'open': ranges.Bounds(-1, 4)})
    joined = ranges.Bounds.join

    def join(self, other):
        assert self is not other
        return joined(self, other)

    monkeypatch.setattr(ranges.Bounds, 'join', join)
    finite_join = ranges.Range.join

    def join_finite(self, other):
        assert self is not other
        return finite_join(self, other)

    monkeypatch.setattr(ranges.Range, 'join', join_finite)
    result = flow_facts.join((left, right))
    assert result.bounds == {'x': ranges.Bounds(0, 9), 'open': ranges.Bounds(-1, None)}
    assert result.ranges == left.ranges


def test_infeasible_arm_remains_absent():
    assert flow_facts.assume(facts({}, {}), ir.Lit(False), True) is None


def test_join_requires_facts_on_every_reaching_path():
    left = replace(facts({'x': ranges.Range(0, 3)}, {'x': ranges.Bounds(0, 3)}),
                   lengths={'same': 2, 'changed': 2, 'absent': 1},
                   carriers={'same': 'tuple', 'changed': 'tuple', 'absent': 'list'},
                   contracts={'maybe': SCALAR, 'absent': SCALAR})
    right = replace(facts({}, {}), lengths={'same': 2, 'changed': 3},
                    carriers={'same': 'tuple', 'changed': 'list'},
                    contracts={'maybe': NONE})
    result = flow_facts.join((left, right))
    assert result.lengths == {'same': 2}
    assert result.carriers == {'same': 'tuple'}
    assert result.contracts == {'maybe': ValueContract.option(SCALAR)}
    assert result.ranges == result.bounds == {}
    assert result.unknown_ranges == frozenset({'x'})


def test_unknown_finite_range_does_not_erase_structural_bound():
    left = facts({'x': ranges.Range(0, 3)}, {'x': ranges.Bounds(0, 3)})
    right = facts({'x': ranges.Range(1, 5)}, {'x': ranges.Bounds(1, None)}, ('x',))
    result = flow_facts.join((left, right))
    assert result.ranges == {}
    assert result.bounds == {'x': ranges.Bounds(0, None)}
    assert result.unknown_ranges == frozenset({'x'})


@pytest.mark.parametrize('other,code', [
    (None, 'static-collection-uninitialized'),
    (_STATIC_SCALAR, 'static-collection-join-disagrees'),
    (_StaticRef(((ir.Lit(True), (2,)),), 'other_tag'), 'static-collection-join-disagrees'),
])
def test_static_reference_join_never_invents_a_binding(other, code):
    ref = _StaticRef(((ir.Lit(True), (1,)),), 'tag')
    left = replace(facts({}, {}), static_values={'x': ref})
    right = replace(facts({}, {}), static_values={} if other is None else {'x': other})
    assert flow_facts.join((left, right)).static_values == {'x': _StaticIssue(code)}


def test_static_reference_join_retains_same_tag_alternatives():
    first = (ir.Op('eq', ir.Var('tag'), ir.Lit(1)), (3, 4))
    second = (ir.Op('eq', ir.Var('tag'), ir.Lit(2)), (5, 6))
    left = replace(facts({}, {}), static_values={'x': _StaticRef((first,), 'tag')})
    right = replace(facts({}, {}), static_values={'x': _StaticRef((second,), 'tag')})
    assert flow_facts.join((left, right)).static_values == {
        'x': _StaticRef((first, second), 'tag')}


@pytest.mark.parametrize('tags', [(None, None), ('left', 'right'), ('tag', None)])
def test_carrier_merge_requires_one_existing_runtime_tag(tags):
    refs = tuple(_StaticRef(((ir.Lit(True), (1,)),), tag) for tag in tags)
    assert _StaticRef.merge_bound(refs) is None
    assert _StaticRef.merge_bound(()) is None


def test_reference_merge_is_pure_and_preserves_typed_alternative_identity():
    from types import SimpleNamespace

    low = SimpleNamespace(_static_tag_ids={})
    left = _StaticRef.bound(low, 'tag', [True, 1, {'a': 2, 'b': 3}], None)
    right = _StaticRef.bound(low, 'tag', [1, {'b': 3, 'a': 2}], None)
    ids = dict(low._static_tag_ids)
    merged = _StaticRef.merge_bound((left, right))
    assert merged == _StaticRef((*left.alternatives, right.alternatives[1]), 'tag')
    assert low._static_tag_ids == ids
    assert len(left.alternatives) == 3 and len(right.alternatives) == 2


@pytest.mark.parametrize('operation', ['assume', 'join', 'with_bounds', 'no_updates'])
def test_fact_operations_do_not_mutate_or_alias_input_tables(operation):
    original = replace(facts({'x': ranges.Range(0, 3)}, {'x': ranges.Bounds(0, 3)}),
                       lengths={'xs': 2}, carriers={'xs': 'tuple'},
                       contracts={'x': SCALAR}, static_values={'x': _STATIC_SCALAR})
    tables = ('lengths', 'carriers', 'contracts', 'ranges', 'bounds', 'static_values')
    before = {field: dict(getattr(original, field)) for field in tables}
    if operation == 'assume':
        result = flow_facts.assume(original, ir.Lit(True), True)
    elif operation == 'join':
        result = flow_facts.join((original, original))
    else:
        result = flow_facts.with_bounds(
            original, {'x': ranges.Bounds(-2, 5)} if operation == 'with_bounds' else {})
    for field in tables:
        assert getattr(original, field) == before[field]
        assert getattr(result, field) is not getattr(original, field)
        getattr(result, field).clear()
        assert getattr(original, field) == before[field]


def test_no_reaching_path_has_no_facts():
    assert flow_facts.join(()) == facts({}, {})


@pytest.mark.parametrize('bound', [ranges.Bounds(-8, 8), ranges.Bounds(4, 4),
                                   ranges.Bounds(None, 8), ranges.Bounds(-8, None),
                                   ranges.Bounds(None, None)])
@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('unknown', [False, True])
def test_numeric_replacement_keeps_projection_and_unknown_marker_together(bound, present, unknown):
    retained = ranges.Range(1, 7)
    original = facts({'kept': retained, **({'x': ranges.Range(0, 2)} if present else {})},
                     {'kept': ranges.Bounds(1, 7), **({'x': ranges.Bounds(0, 2)} if present else {})},
                     ('untouched', 'x') if unknown else ('untouched',))
    result = flow_facts.with_bounds(original, {'x': bound})
    assert result.bounds['x'] is bound
    assert result.ranges['kept'] is retained
    assert result.bounds['kept'] is original.bounds['kept']
    assert ('x' in result.ranges) == (bound.lo is not None and bound.hi is not None)
    if 'x' in result.ranges:
        assert (result.ranges['x'].lo, result.ranges['x'].hi) == (bound.lo, bound.hi)
    assert result.unknown_ranges == frozenset(
        {'untouched', 'x'} if bound.lo is None or bound.hi is None else {'untouched'})
    assert ('x' in original.ranges) is present
    assert ('x' in original.unknown_ranges) is unknown


def test_numeric_replacements_do_not_reproject_unrelated_bounds(monkeypatch):
    kept = ranges.Bounds(-5, 5)
    replacement = ranges.Bounds(1, None)
    original = facts({'kept': ranges.Range(-2, 2)}, {'kept': kept})
    project = ranges.Bounds.finite
    projected = []

    def finite(bound):
        projected.append(bound)
        return project(bound)

    monkeypatch.setattr(ranges.Bounds, 'finite', finite)
    result = flow_facts.with_bounds(original, {'x': replacement})
    assert projected == [replacement]
    assert result.ranges['kept'] is original.ranges['kept']
    assert flow_facts.with_bounds(original, {}) == original
    assert projected == [replacement]

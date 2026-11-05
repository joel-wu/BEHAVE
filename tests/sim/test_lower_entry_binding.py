"""Entry storage and physical paths, independent of source planning policy."""

import ast
from types import SimpleNamespace

import pytest

from behave.sim.behavior import lower_context, lower_mappings, lower_state, lowering
from behave.sim.symbolic import interp, ir, ranges
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.source_options import OptionValue
from behave.sim.behavior.source_records import RecordValue
from behave.sim.behavior.source_values import NONE, SCALAR, UNKNOWN, SourceValueBudget, ValueContract
from behave.sim.symbolic.state_projection import StateProjectionError
from behave.sim.symbolic.value_binding import SolverLeafPath, ValueBinding
from behave.sim.core.contract import FiniteType
from behave.sim.core.value_type import ValueType

U4 = ValueType.integer(FiniteType(4))
SOURCE = '''class M:
    def reset(self): pass
    def process(self, a):
        return {"y": a}
'''


@pytest.fixture(params=[lowering._Lowerer, GraphLowerer])
def entry(request):
    # Layout-only records deliberately bypass planning; end-to-end native and
    # structured-value suites independently cover these contracts' derivation.
    model = source_model(SOURCE, cls='M')
    prepared = SimpleNamespace(
        model=model, signature_input_bindings={},
        goal_plan=SimpleNamespace(operation_goals=(), read_goals=()))
    declarations = SimpleNamespace(
        source_state=(), state_names={}, construction_values={}, step_writes=set(),
        declared_value_types={})
    planned = SimpleNamespace(params=['a'], declaration_contracts={}, boundary_contracts={})
    budget = SourceValueBudget()
    low = request.param([], source_value_budget=budget)
    return SimpleNamespace(low=low, prepared=prepared, declarations=declarations,
                           planned=planned, budget=budget)


def bind(entry, **overrides):
    args = {'fixed': {}, 'static_fields': {}, 'state_ranges': {}}
    args.update(overrides)
    return lower_state.bind_entry(
        entry.low, entry.prepared, entry.declarations, entry.planned, **args)


def test_binding_reuses_lowerer_budget_and_preserves_goal_order(entry):
    first, second = SimpleNamespace(site='same'), SimpleNamespace(site='same')
    entry.prepared.goal_plan.operation_goals = (first, second)
    entry.prepared.goal_plan.read_goals = (second, first)
    before = entry.budget.left
    assert bind(entry) == set()
    assert entry.low._source_value_budget is entry.budget
    assert entry.budget.left == before
    assert entry.low.operation_goals == {'same': [first, second]}
    assert entry.low.read_goals == {'same': [second, first]}
    assert entry.low.storage.shapes == {'a': ()}


def test_presence_is_allocated_before_structured_leaves(entry):
    source = '''class M:
    late = 7
    def reset(self): pass
    def process(self, a):
        self.late = a
        return {"y": a}
'''
    entry.prepared.model = source_model(source, cls='M')
    entry.declarations.source_state = ('late', 'row')
    entry.declarations.state_names = {'late': 'late', 'row': 'row'}
    entry.declarations.step_writes = {'late'}
    entry.declarations.declared_value_types = {
        'row': ValueType.record({'x': U4, 'flag': ValueType.boolean()})}
    inherited = bind(entry)
    assert inherited == {'late'}
    assert entry.low.storage.state_presence == {'late': '_t1'}
    binding = entry.low.storage.structured_bindings['row']
    assert binding.leaf_bindings == (
        (('flag',), SolverLeafPath('_t2')), (('x',), SolverLeafPath('_t3')))
    assert entry.low.storage.fresh() == '_t4'


def test_later_reset_creation_refuses_before_goal_binding(entry):
    entry.prepared.model = source_model('''class M:
    def reset(self): self.late = 1
    def process(self, a):
        self.late = a
        return {"y": a}
''', cls='M')
    entry.declarations.source_state = ('late',)
    entry.declarations.state_names = {'late': 'late'}
    entry.declarations.step_writes = {'late'}
    entry.prepared.goal_plan.operation_goals = (SimpleNamespace(site='untouched'),)
    with pytest.raises(LoweringError) as error:
        bind(entry)
    assert error.value.code == 'state-conditional-reset-unsupported'
    assert error.value.node is entry.prepared.model.reset
    assert entry.low.operation_goals == {}


def test_input_name_and_typed_state_storage_remain_distinct(entry):
    entry.declarations.state_names = {'a': 'self.a'}
    entry.declarations.source_state = ('a',)
    entry.declarations.declared_value_types = {'a': U4}
    entry.declarations.construction_values = {'a': 3}
    public = ValueBinding.direct('a', U4)
    entry.prepared.signature_input_bindings = {'a': public}
    bind(entry, state_ranges={'self.a': ranges.Range(2, 3), 'a': ranges.Range(8, 8),
                              '_t_returned': ranges.Range(1, 1)})
    assert entry.low.storage.structured_bindings['a'].solver_variables == ('self.a',)
    assert entry.low.storage.structured_input_bindings['a'] is public
    assert entry.low.flow.ranges['self.a'] == ranges.Range(2, 3)
    assert entry.low.flow.ranges['a'] == ranges.Range(0, 15)
    assert '_t_returned' not in entry.low.flow.ranges


def test_input_cannot_alias_explicit_state_leaf(entry):
    entry.declarations.source_state = ('x',)
    entry.declarations.state_names = {'x': 'x'}
    entry.declarations.declared_value_types = {'x': U4}
    entry.prepared.signature_input_bindings = {
        'a': ValueBinding('a', U4, (((), SolverLeafPath('x')),))}
    with pytest.raises(LoweringError) as error:
        bind(entry)
    assert error.value.code == 'value-binding-solver-path-duplicate'
    assert entry.low.storage.structured_input_bindings == {}


def test_input_cannot_replace_a_known_storage_shape(entry):
    entry.low.storage.shapes['a'] = (2,)
    entry.prepared.signature_input_bindings = {'a': ValueBinding.direct('a', U4)}
    with pytest.raises(LoweringError) as error:
        bind(entry)
    assert error.value.code == 'value-binding-type-mismatch'
    assert entry.low.storage.shapes['a'] == (2,)


def test_boolean_input_range_and_fixed_array_shape_are_preserved(entry):
    public = ValueBinding.direct('a', ValueType.array(2, ValueType.boolean()))
    entry.prepared.signature_input_bindings = {'a': public}
    bind(entry)
    assert entry.low.flow.ranges['a'] == ranges.Range(0, 1)
    assert entry.low.flow.bounds['a'] == ranges.Bounds(0, 1)
    assert entry.low.storage.shapes['a'] == entry.low.flow.lengths['a'] == (2,)


@pytest.mark.parametrize('shape', [(), (2,), (2, 3), (1, 2, 3)])
@pytest.mark.parametrize('boolean', [False, True])
def test_declared_state_and_input_share_shape_but_keep_separate_storage(entry, shape, boolean):
    value_type = ValueType.boolean() if boolean else U4
    for cells in reversed(shape):
        value_type = ValueType.array(cells, value_type)
    entry.declarations.source_state = ('x',)
    entry.declarations.state_names = {'x': 'x'}
    entry.declarations.declared_value_types = {'x': value_type}
    entry.prepared.signature_input_bindings = {'a': ValueBinding.direct('a', value_type)}
    entry.low.storage.shapes['untouched'] = (7,)
    bind(entry)

    low = entry.low
    for name in ('x', 'a'):
        assert low.storage.shapes[name] == shape
        assert low.flow.contracts[name] == (ValueContract.fixed(shape) if shape else SCALAR)
        if shape:
            assert low.flow.lengths[name] == shape
        else:
            assert name not in low.flow.lengths
    assert low.storage.structured_bindings['x'].solver_variables == ('x',)
    assert low.storage.structured_input_bindings['a'].solver_variables == ('a',)
    assert low.flow.ranges['a'] == ranges.Range(0, 1 if boolean else 15)
    assert low.storage.shapes['untouched'] == (7,)


def test_entry_length_does_not_shrink_storage_capacity(entry):
    storage = ValueContract.sequence('list', 0, 5, SCALAR)
    live = ValueContract.sequence('list', 1, 2, SCALAR)
    entry.declarations.source_state = ('buf',)
    entry.declarations.state_names = {'buf': 'buf'}
    entry.declarations.construction_values = {'buf': [0]}
    entry.declarations.step_writes = {'buf'}
    entry.planned.declaration_contracts = {'buf': storage}
    entry.planned.boundary_contracts = {'buf': live}
    bind(entry)
    low = entry.low
    assert low.storage.declaration_contracts['buf'] is storage
    assert low.flow.contracts['buf'] is live
    assert low.storage.shapes['buf'] == low.flow.lengths['buf'] == (5,)
    holder = low.storage.length_images['buf'][()]
    assert holder in low.storage.state_length_variables
    assert low.flow.ranges[holder] == ranges.Range(1, 2)
    assert low.flow.carriers['buf'] == 'list'


def test_static_mapping_is_not_allocated_a_second_mutable_product(entry):
    contract = ValueContract.mapping(1, 1, SCALAR)
    entry.declarations.source_state = ('table',)
    entry.declarations.state_names = {'table': 'table'}
    entry.planned.declaration_contracts = {'table': contract}
    frozen = {1: 7}
    bind(entry, static_fields={'table': frozen})
    assert entry.low.static_fields['table'] is frozen
    assert 'table' not in entry.low.storage.state_products


def test_dynamic_path_marker_is_shared_with_layout_and_native_lengths():
    inner = ValueContract.sequence('list', 0, 3, SCALAR)
    outer = ValueContract.sequence('list', 2, 2, inner)
    path, shape, capacity = next(lower_context.dynamic_length_specs(outer))
    assert path == (lower_state.SEQUENCE_ELEMENT,) and shape == (2,) and capacity == 3
    assert lower_state.contract_at_dynamic_path(outer, path) is inner
    assert lower_context.native_length_at([[1], [2, 3]], outer, path) == [1, 2]


@pytest.mark.parametrize('path', [
    (('field', 'missing'),), (('payload',),), (object(),), (('wrong',),),
])
def test_unmatched_contract_path_is_unknown_not_scalar(path):
    contract = ValueContract.record({'x': SCALAR})
    assert lower_state.contract_at_dynamic_path(contract, path) is UNKNOWN


def test_nested_record_option_path_keeps_the_same_contract():
    child = ValueContract.sequence('list', 0, 3, SCALAR)
    contract = ValueContract.option(ValueContract.record({'data': child}))
    assert lower_state.contract_at_dynamic_path(
        contract, (('payload',), ('field', 'data'))) is child


def test_failed_entry_binding_precedes_body_emission(monkeypatch):
    seen = []

    def refuse(low, prepared, declarations, planned, **kwargs):
        seen.append(low)
        raise LoweringError(prepared.model.step, 'entry rejected', code='test-entry-rejected')

    def body(*args, **kwargs):
        pytest.fail('body must not execute after failed entry binding')

    monkeypatch.setattr(lower_state, 'bind_entry', refuse)
    monkeypatch.setattr(GraphLowerer, 'body', body)
    with pytest.raises(LoweringError) as error:
        lowering.lower_procedure(SOURCE, input_widths=[4], outputs=['y'])
    assert error.value.code == 'test-entry-rejected' and len(seen) == 1
    assert isinstance(error.value.node, ast.FunctionDef)


@pytest.mark.parametrize('contract,initial,paths,registry', [
    (NONE, None, (), None),
    (ValueContract.record({'x': SCALAR, 'z': NONE}), {'x': 9, 'z': None},
     (('x',),), lowering._RecordBuilder),
    (ValueContract.option(SCALAR), 0, (('present',), ('payload',)), lowering._OptionBuilder),
    (ValueContract.option(ValueContract.record({'x': SCALAR})), {'x': 7},
     (('present',), ('payload', 'x')), lowering._OptionBuilder),
    (ValueContract.sequence('list', 2, 2, ValueContract.option(SCALAR)), [None, 0],
     (('present',), ('payload',)), lowering.OptionSequence),
    (ValueContract.sequence('list', 0, 3, ValueContract.record({'x': SCALAR})),
     [{'x': 9}, {'x': 8}], (('length',), ('elements', 'x')), lowering._BatchBuilder),
    (ValueContract.mapping(0, 3, SCALAR), {4: 9, 2: 8},
     (('length',), ('keys',), ('values',)), lower_mappings.MappingBuilder),
])
def test_persistent_product_allocation_preserves_native_image(
        entry, monkeypatch, contract, initial, paths, registry):
    low = entry.low
    low.storage.state_names = {'a': 'self.a'}  # The input and source attribute collide.

    def unexpected(*args, **kwargs):
        pytest.fail('binding must not evaluate source, emit initialization or add goals')

    for method in ('expr', 'before', '_safety_goal'):
        monkeypatch.setattr(low, method, unexpected)
    product = lower_state.bind_product(low, 'a', contract, entry.prepared.model.step)
    assert product.name == 'a' and product.contract is contract
    assert product.leaves == tuple((path, f'_t{i}') for i, path in enumerate(paths, 1))
    assert low.storage.fresh() == f'_t{len(paths) + 1}'
    assert not low.storage.state_products  # Only the entry binder publishes persistent state.
    if registry:
        assert set(low.storage.carrier_names(registry)) == {'self.a'}
    values = product.solver_values(initial)
    assert set(values) == set(product.solver_variables)
    assert product.reconstruct(values) == initial
    if registry is lowering._BatchBuilder:
        builder = low.storage.require_carrier(lowering._BatchBuilder, 'self.a')
        assert values[builder.count] == 2  # Not the ordinary local builder's zero image.
        assert low.storage.shapes[builder.buffers['x']] == (3,)


@pytest.mark.parametrize('capacity', [0, 1, 4])
def test_mapping_allocation_has_no_source_binding_or_execution(entry, monkeypatch, capacity):
    low = entry.low

    def unexpected(*args, **kwargs):
        pytest.fail('map allocation must not evaluate source, emit commands or add goals')

    monkeypatch.setattr(type(low.storage), 'state_name', unexpected)
    for method in ('expr', 'before', '_safety_goal'):
        monkeypatch.setattr(low, method, unexpected)
    contract = ValueContract.mapping(0, capacity, SCALAR)
    builder = lower_mappings.MappingBuilder.allocate(low, contract, entry.prepared.model.step)
    assert not low.storage.carrier_names(lower_mappings.MappingBuilder)
    assert builder.contract is contract and builder.capacity == max(1, capacity)
    assert (builder.count, builder.keys, builder.values) == ('_t1', '_t2', '_t3')
    assert low.storage.shapes == {'_t1': (), '_t2': (max(1, capacity),),
                                         '_t3': (max(1, capacity),)}
    assert low.storage.carrier_ranges[builder.count] == ranges.Range(0, capacity)


def test_mapping_allocation_refuses_noninteger_payload_before_publication(entry):
    node = entry.prepared.model.step
    contract = ValueContract.mapping(0, 2, ValueContract.option(SCALAR))
    with pytest.raises(LoweringError) as error:
        lower_state.bind_product(entry.low, 'data', contract, node)
    assert error.value.node is node and error.value.code == 'mapping-value-unsupported'
    assert not entry.low.storage.carrier_names(lower_mappings.MappingBuilder) and not entry.low.storage.shapes
    assert entry.low.storage.fresh() == '_t1'


def test_product_layout_error_keeps_source_and_reason(entry, monkeypatch):
    node = entry.prepared.model.step

    def refuse(*args):
        raise StateProjectionError('bad layout')

    monkeypatch.setattr(lower_state, 'SourceProductBinding', refuse)
    with pytest.raises(LoweringError) as error:
        lower_state.bind_product(entry.low, 'a', ValueContract.record({'x': SCALAR}), node)
    assert error.value.node is node and error.value.code == 'state-product-layout-unsupported'
    assert 'bad layout' in str(error.value)


def test_persistent_record_swap_snapshots_before_commit(entry, monkeypatch):
    low = entry.low
    low.storage.state_names = {'a': 'self.a'}
    low.storage.state_presence = {'a': 'exists'}
    contract = ValueContract.record({'x': SCALAR, 'y': SCALAR})
    target = ast.parse('self.a', mode='eval').body
    product = lower_state.bind_product(low, 'a', contract, target)
    low.storage.state_products['a'] = product
    old = low.storage.require_carrier(lowering._RecordBuilder, 'self.a').value.by_name
    value = RecordValue((('x', old['y']), ('y', old['x'])), contract)
    prerequisites = []
    monkeypatch.setattr(low, 'before', prerequisites.append)
    command = lower_state.assign_product(low, target, value, target, contract)
    assert prerequisites
    image = product.solver_values({'x': 3, 'y': 7}) | {'exists': False}
    writes, hits = [], set()
    machine = interp._Interp(image, watch=writes)
    for prefix in prerequisites:
        machine.run(prefix, hits)
    assert product.reconstruct(image) == {'x': 3, 'y': 7} and not image['exists']
    machine.run(command, hits)
    assert product.reconstruct(image) == {'x': 7, 'y': 3}
    assert writes[-1][1:] == ('exists', True) and not hits
    assert low.flow.contracts['self.a'] is contract


@pytest.mark.parametrize('present', [False, True])
def test_persistent_optional_assignment_keeps_none_distinct_from_zero(entry, present):
    low = entry.low
    low.storage.state_names = {'a': 'self.a'}
    low.storage.state_presence = {'a': 'exists'}
    contract = ValueContract.option(SCALAR)
    target = ast.parse('self.a', mode='eval').body
    product = lower_state.bind_product(low, 'a', contract, target)
    low.storage.state_products['a'] = product
    actual = SCALAR if present else NONE
    value = ir.Lit(0) if present else OptionValue(ir.Lit(False), None, NONE)
    command = lower_state.assign_product(low, target, value, target, actual)
    image = product.solver_values(9) | {'exists': False}
    hits = set()
    interp._Interp(image).run(command, hits)
    assert product.reconstruct(image) == (0 if present else None)
    assert image['exists'] is True and not hits
    assert low.flow.contracts['self.a'] is actual


def test_none_only_product_cannot_change_representation(entry):
    low = entry.low
    target = ast.parse('self.a', mode='eval').body
    low.storage.state_products['a'] = lower_state.bind_product(low, 'a', NONE, target)
    low.storage.state_presence['a'] = 'exists'
    command = lower_state.assign_product(
        low, target, OptionValue(ir.Lit(False), None, NONE), target, NONE)
    image, hits = {'exists': False}, set()
    interp._Interp(image).run(command, hits)
    assert image == {'exists': True} and not hits
    assert not low.flow.contracts
    with pytest.raises(LoweringError) as error:
        lower_state.assign_product(low, target, ir.Lit(0), target, SCALAR)
    assert error.value.node is target and error.value.code == 'state-value-contract-disagrees'


@pytest.mark.parametrize('source', ['a', 'other.a', 'self.missing'])
def test_non_product_target_is_left_to_existing_assignment_routes(entry, source):
    low = entry.low
    low.storage.state_products['a'] = lower_state.bind_product(
        low, 'a', NONE, entry.prepared.model.step)
    target = ast.parse(source, mode='eval').body
    assert lower_state.assign_product(low, target, ir.Lit(1), target, SCALAR) is None
    assert not low.flow.contracts and not low.storage.state_presence

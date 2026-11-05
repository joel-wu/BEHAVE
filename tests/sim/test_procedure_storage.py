"""Procedure storage invariants, independent of path and helper lifetimes."""

import ast

import pytest

from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lower_returns import (
    _BatchBuilder, _OptionBuilder, _PositionalBuilder, _RecordBuilder,
)
from behave.sim.behavior.lower_state import ProcedureStorage
from behave.sim.behavior.lowering import _Lowerer
from behave.sim.symbolic import ranges
from behave.sim.symbolic import typecheck as value_types
from behave.sim.symbolic.value_contract import NONE, SCALAR, ValueContract


def test_storage_is_per_procedure_and_copies_input_declarations():
    arrays, reserved = {'_t2': (3,)}, {'_t1'}
    first = ProcedureStorage(arrays, reserved_names=reserved)
    second = ProcedureStorage(arrays, reserved_names=reserved)
    arrays['_t3'] = (4,)
    reserved.add('_t3')
    assert first.fresh() == second.fresh() == '_t3'
    assert first.fresh() == '_t4'
    assert second.temporaries == ['_t3']
    assert first.arrays == second.arrays == {'_t2': (3,)}
    assert first.shapes == second.shapes == {'_t2': (3,)}
    for name in ProcedureStorage.__slots__:
        left, right = getattr(first, name), getattr(second, name)
        if isinstance(left, (dict, list, set)):
            assert left is not right


def test_fixed_representation_type_cannot_change_between_uses():
    storage = ProcedureStorage()
    node = ast.parse('value').body[0].value
    storage.require_shape('value', [2, 3], node)
    storage.require_shape('value', (2, 3), node)
    with pytest.raises(LoweringError) as error:
        storage.require_shape('value', (), node)
    assert error.value.code == 'value-representation-changes'
    assert storage.shapes['value'] == (2, 3)


@pytest.mark.parametrize('capacity', [-1, True, False, 1.0, None])
def test_length_storage_rejects_non_natural_capacity(capacity):
    storage = ProcedureStorage()
    with pytest.raises(ValueError, match='capacity is a natural number'):
        storage.register_sequence('xs', 'n', capacity)
    assert not storage.lengths and not storage.capacities and not storage.length_images


@pytest.mark.parametrize('capacity', [0, 1, 7])
def test_registration_reuses_one_carrier_and_checks_every_identity(capacity):
    storage = ProcedureStorage()
    assert storage.register_sequence('xs', 'n', capacity) == ranges.Bounds(0, capacity)
    image = storage.length_images['xs']
    assert storage.register_sequence('xs', 'n', capacity) == ranges.Bounds(0, capacity)
    assert storage.length_images['xs'] is image
    with pytest.raises(ValueError, match='logical-length carrier of xs changes'):
        storage.register_sequence('xs', 'other', capacity)
    with pytest.raises(ValueError, match='sequence capacity of xs changes'):
        storage.register_sequence('xs', 'n', capacity + 1)
    image[()] = 'conflict'
    with pytest.raises(ValueError, match='root length carrier of xs changes'):
        storage.register_sequence('xs', 'n', capacity)
    assert storage.lengths == {'xs': 'n'}
    assert storage.capacities == {'xs': capacity}


def test_retiring_outer_length_keeps_nested_layout_and_other_values():
    storage = ProcedureStorage()
    storage.register_sequence('xs', 'n', 3)
    storage.register_sequence('other', 'm', 2)
    storage.length_images['xs'][('nested',)] = 'nested_lengths'
    storage.retire_root_length('xs')
    assert storage.lengths == {'other': 'm'}
    assert storage.capacities == {'other': 2}
    assert storage.length_images == {
        'xs': {('nested',): 'nested_lengths'}, 'other': {(): 'm'}}
    storage.retire_root_length('other')
    storage.retire_root_length('absent')
    assert storage.length_images == {'xs': {('nested',): 'nested_lengths'}}


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_flow_restore_and_failed_helper_do_not_roll_back_physical_storage(lowerer):
    low = lowerer((), env={'_t1': ranges.Range(0, 3)})
    node = ast.parse('def helper(): pass').body[0]
    saved, caller, storage = low._flow_facts(), low.activation, low.storage
    storage.state_names['xs'] = 'state_xs'
    with pytest.raises(RuntimeError, match='interrupted'):
        with low._call_activation(node, {'xs': 'local_xs'}, None, None):
            assert low._source_name('self.xs') == 'state_xs'
            length = low._length_target('buffer', 4, node)
            storage.require_shape('buffer', (4,), node)
            low.flow.bounds[length] = ranges.Bounds(1, 2)
            low._register_dynamic_sequence('buffer', length, 4)
            carrier = _BatchBuilder(length, {'x': 'buffer'}, 4)
            storage.bind_carrier('local_xs', carrier)
            storage.widen_carrier_range(length, ranges.Range(0, 4))
            assert low.flow.ranges[length] == ranges.Range(1, 2)
            assert storage.capacities['buffer'] == 4
            raise RuntimeError('interrupted')
    assert low.activation is caller and low.storage is storage
    low._set_flow_facts(saved)
    assert low._flow_facts() == saved
    assert storage.shapes['buffer'] == (4,)
    assert low._length_target('buffer', 4, node) == length == '_t2'
    assert storage.temporaries == ['_t2']
    assert low.flow.bounds[length] == ranges.Bounds(0, 4)
    assert low.flow.ranges[length] == ranges.Range(0, 4)
    assert storage.require_carrier(_BatchBuilder, 'local_xs') is carrier
    assert storage.carrier_ranges[length] == ranges.Range(0, 4)
    assert not set(vars(low)) & {
        'locals', '_tmp', '_reserved_names', 'arrays', 'representation_shapes',
        'declaration_contracts', 'storage_contracts', 'grown', 'capacity',
        'length_images', 'state_length_variables', 'mutable_sequence_lengths',
        'state_names', 'state_presence', 'structured_bindings',
        'structured_input_bindings', 'structured_input_views', 'state_products',
        'value_bindings', 'batch_builders', 'record_builders', 'positional_builders',
        'option_builders', 'option_sequences', 'ordered_scalars', 'mapping_builders',
        '_planned_scalar_ranges'}
    assert all(not hasattr(low, name) for name in (
        '_fresh', '_state_name', '_require_representation_shape', '_retire_root_length',
        '_returned_representation_names'))


def test_carrier_registry_is_per_procedure_and_keeps_representation_kinds_distinct():
    storage, other = ProcedureStorage(), ProcedureStorage()
    names = storage.carrier_names(_BatchBuilder)
    assert not names
    batch = _BatchBuilder('n', {'x': 'xs'}, 4)
    option = _OptionBuilder('present', 'value', ValueContract.option(SCALAR))
    storage.bind_carrier('value', batch)
    storage.bind_carrier('value', option)
    assert storage.carrier(_BatchBuilder, 'value') is batch
    assert storage.require_carrier(_OptionBuilder, 'value') is option
    assert other.carrier(_BatchBuilder, 'value') is None
    assert not other.carrier_names(_OptionBuilder)
    with pytest.raises(KeyError) as error:
        other.require_carrier(_BatchBuilder, 'value')
    assert error.value.args == ('value',)
    assert set(names) == {'value'}
    assert not hasattr(names, 'clear') and not hasattr(names, '__setitem__')
    storage.bind_carrier('second', batch)
    assert set(names) == {'value', 'second'}
    replacement = _BatchBuilder('m', {'x': 'ys'}, 4)
    storage.bind_carrier('value', replacement)
    assert storage.require_carrier(_BatchBuilder, 'value') is replacement
    assert storage.require_carrier(_OptionBuilder, 'value') is option


def test_carrier_envelopes_only_widen_and_cannot_be_changed_through_a_read_view():
    storage = ProcedureStorage()
    view = storage.carrier_ranges
    storage.widen_carrier_range('n', ranges.Range(1, 2))
    storage.widen_carrier_range('n', ranges.Range(0, 4))
    storage.widen_carrier_range('n', ranges.Range(2, 3))
    assert view['n'] == ranges.Range(0, 4)
    with pytest.raises(TypeError):
        view['n'] = ranges.Range(3, 3)
    assert not ProcedureStorage().carrier_ranges


def test_retiring_a_temporary_carrier_preserves_other_forms_and_ir_declarations():
    storage = ProcedureStorage()
    option = _OptionBuilder('p', 'v', ValueContract.option(SCALAR))
    batch = _BatchBuilder('n', {'x': 'xs'}, 4)
    storage.bind_carrier('temporary', option)
    storage.bind_carrier('temporary', batch)
    storage.shapes.update({'p': (), 'v': (), 'n': (), 'xs': (4,)})
    assert storage.retire_carrier(_OptionBuilder, 'temporary') is option
    assert storage.retire_carrier(_OptionBuilder, 'temporary') is None
    assert storage.carrier(_OptionBuilder, 'temporary') is None
    assert storage.require_carrier(_BatchBuilder, 'temporary') is batch
    assert storage.shapes == {'p': (), 'v': (), 'n': (), 'xs': (4,)}


def test_return_escape_collects_recursive_physical_leaves_not_unrelated_bindings():
    storage = ProcedureStorage()
    inner_contract = ValueContract.record({'y': SCALAR})
    inner = _RecordBuilder(inner_contract, {('y',): 'nested_y'})
    optional = _OptionBuilder('nested_present', inner, ValueContract.option(inner_contract))
    record_contract = ValueContract.record({
        'absent': NONE, 'maybe': optional.contract, 'x': SCALAR})
    record = _RecordBuilder(record_contract, {
        ('absent',): None, ('maybe',): optional, ('x',): 'plain'})
    outer = _OptionBuilder('outer_present', record, ValueContract.option(record_contract))
    positional = _PositionalBuilder(
        ('tuple_a', 'tuple_b'), ((), ()), (SCALAR, SCALAR),
        (value_types.INTEGER, value_types.INTEGER),
        ValueContract.sequence('tuple', 2, 2, SCALAR))
    for carrier in (record, outer, positional,
                    _BatchBuilder('count', {'x': 'column'}, 4, form='form')):
        storage.bind_carrier('result', carrier)
    storage.bind_carrier('unrelated', _BatchBuilder('other_count', {'x': 'other_column'}, 4))
    storage.register_sequence('result', 'logical_length', 4)
    assert storage.returned_names('result', 'return_length') == frozenset({
        'result', 'return_length', 'logical_length', 'outer_present',
        'nested_present', 'nested_y', 'plain', 'tuple_a', 'tuple_b',
        'count', 'column', 'form'})
    assert storage.returned_names('scalar') == frozenset({'scalar'})


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_flow_conflict_is_not_hidden_by_intrinsic_length_bound(lowerer):
    low = lowerer(())
    low.flow.bounds['n'] = ranges.Bounds(5, 8)
    with pytest.raises(ValueError, match='logical length of xs lies outside its capacity'):
        low._register_dynamic_sequence('xs', 'n', 3)
    assert low.flow.bounds['n'] == ranges.Bounds(5, 8)

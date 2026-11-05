"""Direct storage contracts and their integration at both source-to-IR lowerers."""

import ast

import pytest

from behave.sim.behavior import lower_checks, lower_state
from behave.sim.symbolic import ir
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_procedure
from behave.sim.behavior.source_values import SCALAR, UNKNOWN, ValueContract

SITE = ast.parse('values').body[0].value
ROOT = ()
CHILD = (lower_state.SEQUENCE_ELEMENT,)
FLAT = ValueContract.sequence('list', 0, 3, SCALAR)
NESTED = ValueContract.sequence('list', 0, 2, FLAT)
PADDED = ValueContract.sequence('list', 0, 4,
                               ValueContract.sequence('list', 0, 5, SCALAR))
SHAPE_ERROR = 'source-value-contract-disagrees'
LENGTH_ERROR = 'nested-dynamic-sequence-unsupported'


@pytest.mark.parametrize(('contract', 'shape', 'storage', 'length_shapes', 'error'), [
    (SCALAR, (), UNKNOWN, {}, None),
    (SCALAR, (1,), UNKNOWN, {}, SHAPE_ERROR),
    (UNKNOWN, (3,), UNKNOWN, {}, None),
    (FLAT, (), UNKNOWN, {}, SHAPE_ERROR),
    (FLAT, (), UNKNOWN, {ROOT: ()}, SHAPE_ERROR),
    (FLAT, (3,), UNKNOWN, {}, LENGTH_ERROR),
    (FLAT, (3,), UNKNOWN, {ROOT: ()}, None),
    (FLAT, (5,), UNKNOWN, {ROOT: ()}, None),
    (FLAT, (5,), UNKNOWN, {}, SHAPE_ERROR),
    (FLAT, (2,), UNKNOWN, {ROOT: ()}, SHAPE_ERROR),
    (FLAT, (3, 1), UNKNOWN, {ROOT: ()}, SHAPE_ERROR),
    (ValueContract.fixed((2,), 'list'), (4,), UNKNOWN, {ROOT: ()}, None),
    (NESTED, (2, 3), UNKNOWN, {ROOT: (), CHILD: (2,)}, None),
    (NESTED, (2, 3), UNKNOWN, {ROOT: ()}, LENGTH_ERROR),
    (NESTED, (4, 5), UNKNOWN, {ROOT: (), CHILD: (4,)}, SHAPE_ERROR),
    (NESTED, (4, 5), PADDED, {ROOT: (), CHILD: (4,)}, None),
    (NESTED, (4, 5), PADDED, {ROOT: ()}, SHAPE_ERROR),
    (NESTED, (4, 5), PADDED, {ROOT: (), CHILD: (2,)}, SHAPE_ERROR),
    (NESTED, (4, 5), PADDED, {ROOT: (1,), CHILD: (4,)}, SHAPE_ERROR),
])
def test_storage_contract_requires_shape_before_length_metadata(
        contract, shape, storage, length_shapes, error):
    image = {path: f'length{i}' for i, path in enumerate(length_shapes)}
    shapes = {image[path]: dimensions for path, dimensions in length_shapes.items()}
    saved = dict(image)
    kwargs = {'shape': shape, 'image': image, 'storage': storage, 'shape_of': shapes.__getitem__}
    if error is None:
        assert lower_checks.check_value_storage(SITE, contract, **kwargs) is None
    else:
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_value_storage(SITE, contract, **kwargs)
        assert caught.value.node is SITE
        assert caught.value.code == error
        if error == SHAPE_ERROR:
            assert caught.value.what == (
                'the lowered value disagrees with its source storage '
                f'contract: {shape} against {contract.physical_shape}')
    assert image == saved


@pytest.mark.parametrize('broken', ['root', 'child', 'missing', 'none'])
def test_recursive_padding_shape_lookup_keeps_short_circuit_order(broken):
    image = {ROOT: 'root', CHILD: 'child', ('unused',): 'unused'}
    if broken == 'missing':
        image.pop(CHILD)
    shapes = {'root': (1,) if broken == 'root' else (),
              'child': (2,) if broken == 'child' else (4,)}
    seen = []

    def shape_of(expression):
        seen.append(expression)
        return shapes[expression]

    kwargs = {'shape': (4, 5), 'storage': PADDED, 'image': image, 'shape_of': shape_of}
    if broken == 'none':
        lower_checks.check_value_storage(SITE, NESTED, **kwargs)
    else:
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_value_storage(SITE, NESTED, **kwargs)
        assert caught.value.code == SHAPE_ERROR
    assert seen == (['root'] if broken in ('root', 'missing') else ['root', 'child'])


@pytest.mark.parametrize('storage', [UNKNOWN, SCALAR, FLAT])
def test_unrelated_storage_never_queries_length_shapes(storage):
    def unexpected(_expression):
        pytest.fail('no recursive padding proof applies here')

    lower_checks.check_value_storage(
        SITE, FLAT, shape=(5,), storage=storage,
        image={ROOT: 'root'}, shape_of=unexpected)


@pytest.mark.parametrize('record', [False, True])
@pytest.mark.parametrize('optional', [False, True])
@pytest.mark.parametrize('present', [False, True])
def test_recursive_record_lengths_keep_paths_and_diagnostics(record, optional, present):
    contract = ValueContract.record({'items': FLAT})
    path = (('field', 'items'),)
    if optional:
        contract = ValueContract.option(contract)
        path = (('payload',),) + path
    image = {path: 'length'} if present else {}
    if present:
        lower_checks.check_value_lengths(SITE, contract, image, record=record)
    else:
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_value_lengths(SITE, contract, image, record=record)
        assert caught.value.code == LENGTH_ERROR
        assert caught.value.node is SITE
        assert caught.value.what == (
            'a record value is missing one of its nested logical-length carriers'
            if record else 'a bounded recursive value is missing one of its logical-length carriers')


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_lowerer_materializes_the_read_only_length_image_once(monkeypatch, lowerer):
    low = lowerer([])
    value = ir.Var('value')
    low.flow.contracts['value'] = FLAT
    low.flow.lengths['value'] = (3,)
    low.storage.length_images['value'] = {ROOT: 'length'}
    original = low._length_image
    seen = []

    def image(expression):
        seen.append(expression)
        return original(expression)

    monkeypatch.setattr(low, '_contract_ast', lambda _source: FLAT)
    monkeypatch.setattr(low, '_length_image', image)
    assert low._contract_for_value(SITE, value) == FLAT
    assert seen == [value]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_value_check_failure_is_not_bypassed_by_either_lowerer(monkeypatch, lowerer):
    fault = LoweringError(SITE, 'injected representation failure', code='injected-storage-failure')

    def fail(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(lower_checks, 'check_value_storage', fail)
    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        values = [a, a + 1]
        return {"y": values[1]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_procedure(source, input_widths=[2], lowerer=lowerer)
    assert caught.value is fault

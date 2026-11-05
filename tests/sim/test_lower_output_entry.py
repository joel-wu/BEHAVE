"""Return storage, helper publication and private writable-input boundaries."""

import ast
from copy import deepcopy
from types import SimpleNamespace

import pytest

from behave.sim.behavior import instrumented, lower_returns, lower_state, lowering
from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.behavior.source_batches import BatchForm
from behave.sim.behavior.source_options import OptionValue
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_records import RecordValue
from behave.sim.behavior.source_values import NONE, SCALAR, UNKNOWN, ValueContract
from behave.sim.symbolic.value_binding import SolverLeafPath, ValueBinding
from behave.sim.core.contract import FiniteType
from behave.sim.core.value_type import ValueType

SOURCE = '''class M:
    def reset(self): pass
    def process(self, a):
        return {"y": a}
'''
U4 = ValueType.integer(FiniteType(4))


@pytest.fixture(params=[lowering._Lowerer, GraphLowerer])
def low(request):
    return request.param([])


def output_context(*, source=SOURCE, capacity=1, signature=None, contract=None):
    model = source_model(source, cls='M')
    prepared = SimpleNamespace(
        model=model, resolver=ScopeResolver(model),
        signature_output_bindings={} if signature is None else {
            b.name: b for b in signature.output_bindings})
    plan = SimpleNamespace(output_capacity=capacity, output_contract=contract,
                           return_reached=lambda node: True)
    planned = SimpleNamespace(sequence_plan=plan, outputs=['y'])
    return prepared, planned


def execute(entry, values=None):
    machine = interp._Interp({} if values is None else values)
    hits = set()
    machine.run(entry, hits)
    assert not hits
    return machine.rho


@pytest.mark.parametrize('capacity', [0, 1, 3])
@pytest.mark.parametrize('typed', [False, True])
def test_initialized_buffers_do_not_claim_a_transaction(low, capacity, typed):
    signature = (ProcedureSignature((('a', FiniteType(4)),),
                                    (('y', ValueType.boolean()),)) if typed else None)
    prepared, planned = output_context(capacity=capacity, signature=signature)
    result = lower_returns.initialize_outputs(low, prepared, planned,
                                               signature=signature, fixed={})
    values = execute(result.entry)
    assert result.count == '_t2' and low.activation.destination.done == '_t1'
    assert result.output_vars == {'y': '_t3'}
    assert values[low.activation.destination.done] == values[result.count] == 0
    assert low.output_capacity == capacity
    assert low.storage.shapes['_t3'] == (max(1, capacity),)
    cells = values['_t3']
    assert len(cells) == max(1, capacity)
    assert all(type(value) is (bool if typed else int) for value in cells)
    assert all(value == (False if typed else 0) for value in cells)
    assert (low.output_boundary is not None) == typed


def test_structured_output_uses_declared_physical_leaves(low):
    record = ValueType.record({'flag': ValueType.boolean(), 'x': U4})
    binding = ValueBinding('y', record, (
        (('flag',), SolverLeafPath('flags')), (('x',), SolverLeafPath('data'))))
    signature = ProcedureSignature((('a', FiniteType(4)),), (('y', binding),))
    prepared, planned = output_context(capacity=2, signature=signature)
    result = lower_returns.initialize_outputs(low, prepared, planned,
                                               signature=signature, fixed={})
    assert set(result.output_vars) == {'flags', 'data'}
    assert all(name not in {'y', 'flags', 'data'} for name in result.output_vars.values())
    values = execute(result.entry)
    assert all(type(v) is bool for v in values[result.output_vars['flags']])
    assert all(type(v) is int for v in values[result.output_vars['data']])
    assert all(low.storage.shapes[v] == (2,) for v in result.output_vars.values())


@pytest.mark.parametrize('count', [1, 8, 32])
def test_output_initialization_reuses_owner_found_with_the_type(low, monkeypatch, count):
    bindings = tuple(ValueBinding(
        f'out{index}', U4, (((), SolverLeafPath(f'physical{count - index}')),))
        for index in range(count))
    signature = ProcedureSignature((('a', FiniteType(4)),),
                                   tuple((binding.name, binding) for binding in bindings))
    prepared, planned = output_context(capacity=2, signature=signature)
    planned.outputs = list(signature.output_names)
    declared_types = ValueBinding.solver_types
    visits = []

    def count_type_reads(binding):
        visits.append(binding.name)
        return declared_types(binding)

    def unexpected_variable_scan(_binding):
        pytest.fail('output ownership should not be recovered by rescanning variables')

    monkeypatch.setattr(ValueBinding, 'solver_types', count_type_reads)
    monkeypatch.setattr(ValueBinding, 'solver_variables', property(unexpected_variable_scan))
    result = lower_returns.initialize_outputs(low, prepared, planned, signature=signature, fixed={})
    assert visits == [binding.name for binding in bindings]
    assert tuple(result.output_vars) == tuple(f'physical{count - index}' for index in range(count))
    values = execute(result.entry)
    assert values[result.count] == 0
    assert all(values[variable] == [0, 0] for variable in result.output_vars.values())


def test_output_certificate_tracks_only_planned_returns(low):
    source = SOURCE.replace('return {"y": a}', 'return {"y": a}\n        return {"y": 0}')
    signature = ProcedureSignature((('a', FiniteType(4)),), (('y', FiniteType(4)),))
    prepared, planned = output_context(source=source, signature=signature)
    returns = [n for n in ast.walk(prepared.model.step) if isinstance(n, ast.Return)]
    planned.sequence_plan.return_reached = lambda node: node is returns[0]
    lower_returns.initialize_outputs(low, prepared, planned, signature=signature, fixed={})
    assert low.output_boundary._expected_returns == frozenset({id(returns[0])})
    assert low.output_boundary._seen_returns == set()


def test_dynamic_output_shape_keeps_its_refusal_and_site(low):
    contract = ValueContract.record({'y': ValueContract.sequence('list', 0, 3, SCALAR)})
    prepared, planned = output_context(contract=contract)
    with pytest.raises(LoweringError) as error:
        lower_returns.initialize_outputs(low, prepared, planned, signature=None, fixed={})
    assert error.value.code == 'nested-dynamic-sequence-unsupported'
    assert error.value.node is prepared.model.step


def test_zero_images_keep_recursive_boolean_carriers():
    value = interp._Interp({}).value(lower_returns.zero_type(types.Array(2, types.BOOLEAN)))
    assert len(value) == 2 and all(type(v) is bool and not v for v in value)
    value = interp._Interp({}).value(lower_returns.zero_shape((2, 3)))
    assert len(value) == 2 and all(len(row) == 3 for row in value)
    assert all(type(v) is int and v == 0 for row in value for v in row)
    with pytest.raises(TypeError):
        lower_returns.zero_type('not a semantic type')


def test_recursive_inactive_image_preserves_product_and_option_structure():
    node = ast.parse('None', mode='eval').body
    contract = ValueContract.record({
        'absent': NONE, 'maybe': ValueContract.option(ValueContract.fixed((2,), 'tuple')),
        'nested': ValueContract.record({'x': SCALAR}),
    })
    value = lower_returns.zero_contract(contract, node)
    fields = value.by_name
    assert value.contract is contract
    assert fields['absent'].contract is NONE and fields['absent'].payload is None
    assert interp._Interp({}).value(fields['absent'].present) is False
    assert interp._Interp({}).value(fields['maybe'].present) is False
    assert interp._Interp({}).value(fields['maybe'].payload) == [0, 0]
    assert interp._Interp({}).value(fields['nested'].by_name['x']) == 0


@pytest.mark.parametrize('contract', [UNKNOWN, ValueContract.sequence('list', 0, 3, SCALAR)])
def test_inactive_image_does_not_guess_unrepresented_layout(contract):
    node = ast.parse('None', mode='eval').body
    with pytest.raises(LoweringError) as error:
        lower_returns.zero_contract(contract, node)
    assert error.value.node is node
    assert error.value.code == 'optional-payload-representation-unsupported'


@pytest.mark.parametrize('fact', ['unknown', 'optional', 'present', 'absent', 'wrong_shape'])
def test_option_refinement_uses_supplied_fact_without_reading_tag(fact):
    node = ast.parse('x', mode='eval').body
    value = OptionValue(ir.Var('tag'), ir.Var('payload'), ValueContract.option(SCALAR))
    contract = {'unknown': UNKNOWN, 'optional': value.contract, 'present': SCALAR,
                'absent': NONE, 'wrong_shape': ValueContract.fixed((2,), 'list')}[fact]
    if fact == 'wrong_shape':
        with pytest.raises(LoweringError) as error:
            lower_returns._OptionBuilder.refine(value, contract, node)
        assert error.value.node is node and error.value.code == 'optional-value-not-unwrapped'
        return
    result = lower_returns._OptionBuilder.refine(value, contract, node)
    if fact in ('unknown', 'optional'):
        assert result is value
    elif fact == 'present':
        assert result is value.payload
    else:
        assert result.contract is NONE and result.payload is None
        assert interp._Interp({}).value(result.present) is False


@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('kind', ['scalar', 'array', 'record'])
def test_option_conversion_uses_evaluated_values_without_allocating_or_emitting(
        low, monkeypatch, present, kind):
    node = ast.parse('x', mode='eval').body
    payload_contract = {'scalar': SCALAR, 'array': ValueContract.fixed((2,), 'tuple'),
                        'record': ValueContract.record({'x': SCALAR})}[kind]
    payload = {'scalar': ir.Lit(0), 'array': ir.Op('arraylit', ir.Lit(0), ir.Lit(2)),
               'record': RecordValue((('x', ir.Lit(0)),), payload_contract)
               if kind == 'record' else None}[kind]
    contract = ValueContract.option(payload_contract)

    def unexpected(*args, **kwargs):
        pytest.fail('conversion must not evaluate source, re-infer an explicit contract or emit/allocate')

    monkeypatch.setattr(type(low.storage), 'fresh', unexpected)
    for method in ('expr', 'before', '_safety_goal', '_contract_for_value'):
        monkeypatch.setattr(low, method, unexpected)
    result = lower_returns._OptionBuilder.coerce(
        low, payload if present else OptionValue(ir.Lit(False), None, NONE),
        node, contract, actual=payload_contract if present else NONE)
    assert result.contract is contract
    assert interp._Interp({}).value(result.present) is present
    if present:
        assert result.payload is payload
    else:
        inactive = result.payload.by_name['x'] if kind == 'record' else result.payload
        assert interp._Interp({}).value(inactive) == ([0, 0] if kind == 'array' else 0)
    assert not low.flow.contracts and not low.storage.carrier_names(lowering._OptionBuilder) and not low.storage.carrier_names(lowering._RecordBuilder)


@pytest.mark.parametrize('present', [False, True])
def test_existing_option_conversion_retains_tag_and_payload_identity(low, present):
    node = ast.parse('x', mode='eval').body
    original = ValueContract.option(SCALAR)
    target = ValueContract.option(SCALAR)
    value = OptionValue(ir.Var('tag'), ir.Lit(0), original)
    result = lower_returns._OptionBuilder.coerce(low, value, node, target)
    assert result.present is value.present and result.payload is value.payload
    assert result.contract is target
    assert interp._Interp({'tag': present}).value(result.present) is present


@pytest.mark.parametrize('form', [BatchForm.NONE, BatchForm.MAPPING])
@pytest.mark.parametrize('count', [0, 1])
def test_optional_record_conversion_keeps_source_form_separate_from_count(low, form, count):
    node = ast.parse('helper()', mode='eval').body
    record = ValueContract.record({'x': SCALAR})
    contract = ValueContract.option(record)
    builder = lower_returns._BatchBuilder('count', {'x': 'buffer'}, 1, form='form')
    image = execute(builder.initialize(low, record, node))
    # Test representation roles independently: only form is the Option's tag.
    image.update(count=count, form=lower_returns._batch_form_code(form), buffer=[7])
    value = lower_returns._BatchValue(builder, ir.Var('form'), contract)
    result = lower_returns._OptionBuilder.coerce(low, value, node, contract)
    machine = interp._Interp(image)
    assert machine.value(result.present) is (form is BatchForm.MAPPING)
    assert machine.value(result.payload.by_name['x']) == 7
    assert image['count'] == count


@pytest.mark.parametrize('fault,code', [
    ('present_contract', 'optional-payload-contract-disagrees'),
    ('present_shape', 'optional-payload-representation-unsupported'),
    ('record_representation', 'optional-payload-representation-unsupported'),
    ('optional_contract', 'optional-payload-contract-disagrees'),
    ('optional_shape', 'optional-payload-representation-unsupported'),
    ('batch_contract', 'optional-payload-contract-disagrees'),
])
def test_option_conversion_refusals_keep_source_and_reason(low, fault, code):
    node = ast.parse('x', mode='eval').body
    scalar, array = ValueContract.option(SCALAR), ValueContract.fixed((2,), 'list')
    contract, value, actual = scalar, ir.Lit(3), SCALAR
    if fault == 'present_contract':
        actual = array
    elif fault == 'present_shape':
        value = ir.Op('arraylit', ir.Lit(1), ir.Lit(2))
    elif fault == 'record_representation':
        actual = ValueContract.record({'x': SCALAR})
        contract = ValueContract.option(actual)
    elif fault == 'optional_contract':
        value = OptionValue(ir.Lit(True), ir.Op('arraylit', ir.Lit(1), ir.Lit(2)),
                            ValueContract.option(array))
    elif fault == 'optional_shape':
        contract = ValueContract.option(array)
        value = OptionValue(ir.Lit(True), ir.Lit(3), contract)
    else:
        record = ValueContract.record({'x': SCALAR})
        builder = lower_returns._BatchBuilder('count', {'x': 'buffer'}, 1)
        value = lower_returns._BatchValue(builder, ir.Lit(1), ValueContract.option(record))
    with pytest.raises(LoweringError) as error:
        lower_returns._OptionBuilder.coerce(low, value, node, contract, actual=actual)
    assert error.value.node is node and error.value.code == code
    with pytest.raises(TypeError, match='target contract is not optional'):
        lower_returns._OptionBuilder.coerce(low, ir.Lit(0), node, SCALAR)


@pytest.mark.parametrize('kind,shape,expected', [
    (types.BOOLEAN, (), False), (types.INTEGER, (), 0),
    (None, (2,), [0, 0]), (None, (2, 2), [[0, 0], [0, 0]]),
])
def test_helper_entry_uses_its_owner_without_emitting(low, monkeypatch, kind, shape, expected):
    destination = lower_returns._ValueReturn('done', 'result', planned=SCALAR)
    destination.shape = shape
    destination.result_type = kind
    destination.result_type_seen = kind is not None
    low.activation.destination = object()  # Another active destination is not this builder's metadata.

    def unexpected(command):
        pytest.fail('return initialization must not choose command placement')

    monkeypatch.setattr(low, 'before', unexpected)
    values = execute(destination.initialize(low, ast.parse('pass').body[0]))
    assert values['done'] == 0
    assert values['result'] == expected
    if shape:
        leaves = [values['result']]
        for _ in shape:
            leaves = [cell for row in leaves for cell in row]
        assert all(type(cell) is int for cell in leaves)
    else:
        assert type(values['result']) is type(expected)
    assert set(values) == {'done', 'result'}


@pytest.mark.parametrize('dynamic', [False, True])
def test_helper_publication_keeps_shape_and_logical_length_separate(low, dynamic):
    node = ast.parse('helper(a)').body[0].value
    contract = ValueContract.sequence('list', 0 if dynamic else 3, 3, SCALAR)
    destination = lower_returns._ValueReturn('done', 'result', 'length', planned=contract)
    destination.seen = True
    destination.shape = (3,)
    destination.carrier = 'list'
    destination.contract = contract
    low.activation.destination = object()
    # Stale dynamic metadata may not survive a fixed-length result.
    low.storage.lengths['result'] = 'length' if dynamic else 'old_length'
    low.storage.capacities['result'] = 3 if dynamic else 99
    result = destination.publish(low, node, helper_name='helper')
    assert isinstance(result, ir.Var) and result.name == 'result'
    assert low.flow.contracts['result'] == contract
    assert low.flow.lengths['result'] == (3,)
    assert low.flow.carriers['result'] == 'list'
    if dynamic:
        assert low.storage.lengths['result'] == 'length'
        assert low.storage.capacities['result'] == 3
    else:
        assert 'result' not in low.storage.lengths and 'result' not in low.storage.capacities


def test_helper_publication_cannot_replace_a_dynamic_length_carrier(low):
    contract = ValueContract.sequence('list', 0, 3, SCALAR)
    destination = lower_returns._ValueReturn('done', 'result', 'new_length', planned=contract)
    destination.seen = True
    destination.shape = (3,)
    destination.contract = contract
    low.storage.lengths['result'] = 'old_length'
    low.storage.capacities['result'] = 3
    with pytest.raises(ValueError, match='logical-length carrier of result changes'):
        destination.publish(low, ast.parse('helper(a)').body[0].value, helper_name='helper')


@pytest.mark.parametrize('fault,code', [
    ('none', 'helper-return-value-unsupported'),
    ('positional', 'helper-return-shape-disagrees'),
    ('static', 'static-get-result-unsupported'),
    ('contract', 'source-value-contract-disagrees'),
    ('length', 'dynamic-helper-contract-unplanned'),
])
def test_helper_publication_preserves_refusal_and_call_site(low, fault, code):
    node = ast.parse('helper(a)').body[0].value
    contract = ValueContract.sequence('list', 0, 3, SCALAR)
    destination = lower_returns._ValueReturn('done', 'result', planned=contract)
    destination.seen = True
    destination.shape = (3,)
    destination.contract = contract
    if fault == 'none':
        destination.no_value = True
    elif fault == 'positional':
        destination.positional_arity = 2
    elif fault == 'static':
        destination.static_reference = True
    elif fault == 'contract':
        destination.contract = SCALAR
    low.activation.destination = object()
    with pytest.raises(LoweringError) as error:
        destination.publish(low, node, helper_name='helper')
    assert error.value.code == code
    assert error.value.node is node


@pytest.mark.parametrize('lowerer', [lowering._Lowerer, GraphLowerer])
def test_nested_return_owner_is_published_after_caller_restoration(monkeypatch, lowerer):
    source = '''class M:
    def reset(self): self.count = 0
    def inner(self, a):
        self.count += 1
        return True if a else 2
    def outer(self, a):
        return self.inner(a) + 1
    def process(self, a):
        return {'y': self.outer(a), 'count': self.count}
'''
    phases = []
    initialize = lower_returns._ValueReturn.initialize
    publish = lower_returns._ValueReturn.publish

    def entry(destination, low, node):
        assert low.activation.destination is destination
        phases.append(('entry', destination))
        return initialize(destination, low, node)

    def expose(destination, low, node, **kwargs):
        assert low.activation.destination is not destination
        assert low.activation.scope.name == ('outer' if kwargs['helper_name'] == 'inner' else 'process')
        phases.append(('publish', destination))
        return publish(destination, low, node, **kwargs)

    monkeypatch.setattr(lower_returns._ValueReturn, 'initialize', entry)
    monkeypatch.setattr(lower_returns._ValueReturn, 'publish', expose)
    proc = lowering.lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer, bound=3)
    assert phases and len(phases) % 2 == 0
    assert all(phases[i][0] == 'entry' and phases[i + 1][0] == 'publish'
               and phases[i][1] is phases[i + 1][1] for i in range(0, len(phases), 2))
    state = None
    for count, a in enumerate((0, 1, 2), 1):
        state, output, _hits, error = interp.run_step(proc, {'a': a}, state=state)
        assert not error
        assert output.transactions == [{'y': 2 if a else 3, 'count': count}]


@pytest.mark.parametrize('computed', [False, True], ids=['literal', 'computed'])
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('reuse', [False, True])
def test_record_binding_stages_rhs_before_shared_initialization(
        low, monkeypatch, computed, partial, reuse):
    node = ast.parse('helper(a)' if computed else
                     ('{"x": 3}' if partial else '{"y": 4, "x": 1, "x": 3}')).body[0].value
    contract = ValueContract.record({'x': SCALAR, 'y': SCALAR})
    fields = (('x', ir.Lit(3)),) if partial else (('x', ir.Lit(3)), ('y', ir.Lit(4)))
    product = RecordValue(fields, ValueContract.record({key: SCALAR for key, _ in fields}))
    monkeypatch.setattr(low, '_record_fields', lambda name: ('x', 'y'))
    monkeypatch.setattr(low, '_record_contract', lambda name: contract)
    monkeypatch.setattr(low, '_is_record_builder', lambda name: True)
    previous = None
    if reuse:
        previous = lower_returns._RecordBuilder.allocate(low, contract, node)
        low.storage.bind_carrier('record', previous)
    phases = []

    def evaluate(source):
        assert source is node and computed
        phases.append('rhs')
        return product

    def ordered(*sources):
        assert not computed and tuple(sources) == tuple(node.values)
        phases.append('rhs')
        return tuple(ir.Lit(source.value) for source in sources)

    def target(name):
        assert name == 'record'
        phases.append('target')
        return name

    def source_contract(source):
        assert source is node and not computed
        phases.append('source-contract')
        return product.contract

    def seed(name, builder):
        assert low.storage.require_carrier(lowering._RecordBuilder, name) is builder
        assert name not in low.flow.contracts
        phases.append('seed')

    initialize = lower_returns._RecordBuilder.initialize

    def initialized(builder, owner, source):
        assert owner is low and source is node
        assert low.flow.contracts['record'] == contract
        phases.append('initialize')
        return initialize(builder, owner, source)

    def infer(source, item):
        assert not computed and source in node.values
        assert phases[-1] in ('initialize', 'field-contract')
        phases.append('field-contract')
        return SCALAR

    monkeypatch.setattr(low, 'expr', evaluate)
    monkeypatch.setattr(low, '_in_order', ordered)
    monkeypatch.setattr(low, '_name', target)
    monkeypatch.setattr(low, '_contract_ast', source_contract)
    monkeypatch.setattr(low, '_seed_record_builder_ranges', seed)
    monkeypatch.setattr(low, '_contract_for_value', infer)
    monkeypatch.setattr(lower_returns._RecordBuilder, 'initialize', initialized)
    command = low._set_record_value('record', node)
    builder = low.storage.require_carrier(lowering._RecordBuilder, 'record')
    if reuse:
        assert builder is previous
    image = execute(command, {holder: 99 for holder in builder.leaves.values()})
    assert image[builder.leaves[('x',)]] == 3
    assert image[builder.leaves[('y',)]] == (0 if partial else 4)
    assert phases == (['rhs', 'target'] + ([] if computed else ['source-contract'])
                      + ['seed', 'initialize']
                      + ([] if computed else ['field-contract'] * len(node.values)))


@pytest.mark.parametrize('fault,code', [
    ('unplanned', 'record-value-shape-mismatch'),
    ('computed_immutable', 'record-value-shape-mismatch'),
    ('computed_scalar', 'record-value-shape-mismatch'),
    ('computed_extra', 'source-value-contract-disagrees'),
    ('computed_contract', 'source-value-contract-disagrees'),
    ('literal_extra', 'record-value-shape-mismatch'),
    ('literal_partial_immutable', 'record-value-shape-mismatch'),
    ('literal_unknown_plan', 'source-plan-inconsistent'),
    ('literal_unknown_field', 'record-field-representation-unsupported'),
    ('literal_reused_layout', 'record-value-shape-mismatch'),
    ('computed_reused_layout', 'record-value-shape-mismatch'),
])
def test_record_binding_preserves_refusal_occurrence(low, monkeypatch, fault, code):
    computed = fault.startswith('computed')
    node = ast.parse('helper(a)' if computed else
                     ('{"z": 3}' if fault == 'literal_extra' else '{"x": 3}')).body[0].value
    contract = ValueContract.record({'x': SCALAR, 'y': SCALAR})
    actual = ValueContract.record({'x': SCALAR})
    if fault == 'computed_extra':
        actual = ValueContract.record({'z': SCALAR})
    elif fault == 'computed_contract':
        actual = ValueContract.record({'x': NONE})
    product = RecordValue(tuple((key, ir.Lit(3)) for key in actual.field_contracts), actual)
    monkeypatch.setattr(low, '_record_fields', lambda name: () if fault == 'unplanned' else ('x', 'y'))
    monkeypatch.setattr(low, '_is_record_builder', lambda name: not fault.endswith('immutable'))
    monkeypatch.setattr(low, '_record_contract', lambda name:
                        UNKNOWN if fault == 'literal_unknown_plan' else contract)
    monkeypatch.setattr(low, 'expr', lambda source: ir.Lit(3) if fault == 'computed_scalar' else product)
    monkeypatch.setattr(low, '_in_order', lambda *sources: tuple(ir.Lit(3) for _ in sources))
    monkeypatch.setattr(low, '_contract_ast', lambda source: UNKNOWN)
    monkeypatch.setattr(low, '_contract_for_value', lambda source, item: UNKNOWN)
    monkeypatch.setattr(low, '_seed_record_builder_ranges', lambda *args: None)
    if fault.endswith('reused_layout'):
        low.storage.bind_carrier('record', lower_returns._RecordBuilder.allocate(low, actual, node))
    with pytest.raises(LoweringError) as error:
        low._set_record_value('record', node)
    assert error.value.code == code
    assert error.value.node is (node.values[0] if fault == 'literal_unknown_field' else node)


@pytest.mark.parametrize('computed', [False, True], ids=['literal', 'computed'])
@pytest.mark.parametrize('payload', ['scalar', 'nested', 'optional', 'dynamic_sequence'])
@pytest.mark.parametrize('immutable', [False, True], ids=['builder', 'immutable'])
def test_record_rebinding_preserves_rhs_and_logical_lengths(
        low, monkeypatch, computed, payload, immutable):
    node = ast.parse('helper(a)' if computed else '{"x": previous}').body[0].value
    child = {
        'scalar': SCALAR,
        'nested': ValueContract.record({'a': SCALAR}),
        'optional': ValueContract.option(SCALAR),
        'dynamic_sequence': ValueContract.sequence('list', 0, 3, SCALAR),
    }[payload]
    contract = ValueContract.record({'x': child, 'y': SCALAR})
    actual = contract if immutable else ValueContract.record({'x': child})
    builder = lower_returns._RecordBuilder.allocate(low, contract, node)
    low.storage.bind_carrier('record', builder)
    old = execute(builder.initialize(low, node))
    old[builder.leaves[('y',)]] = 22
    previous = ir.Var(builder.leaves[('y',)])
    if payload == 'nested':
        previous = RecordValue((('a', previous),), child)
    elif payload == 'optional':
        previous = OptionValue(ir.Lit(True), previous, child)
    elif payload == 'dynamic_sequence':
        holder = builder.leaves[('x',)]
        old[holder], old[low.storage.lengths[holder]] = [7, 8, 9], 2
        previous = ir.Var(holder)
    fields = (('x', previous), ('y', ir.Lit(0))) if immutable else (('x', previous),)
    product = RecordValue(fields, actual)
    monkeypatch.setattr(low, '_record_fields', lambda name: ('x', 'y'))
    monkeypatch.setattr(low, '_record_contract', lambda name: contract)
    monkeypatch.setattr(low, '_is_record_builder', lambda name: True)
    monkeypatch.setattr(low, 'expr', lambda source: product)
    monkeypatch.setattr(low, '_in_order', lambda *sources: (previous,))
    monkeypatch.setattr(low, '_contract_ast', lambda source: actual)
    monkeypatch.setattr(low, '_contract_for_value', lambda source, item: child)
    monkeypatch.setattr(low, '_seed_record_builder_ranges', lambda *args: None)
    prerequisites = []
    monkeypatch.setattr(low, 'before', prerequisites.append)
    command = (low._bind_product('record', product, node) if immutable
               else low._set_record_value('record', node))
    for prerequisite in prerequisites:
        old = execute(prerequisite, old)
    image = execute(command, old)
    assert low.storage.require_carrier(lowering._RecordBuilder, 'record') is builder
    assert image[builder.leaves[('y',)]] == 0  # omitted field still initializes
    if payload == 'nested':
        assert image[builder.leaves[('x', 'a')]] == 22
    elif payload == 'optional':
        option = builder.leaves[('x',)]
        assert image[option.present] is True and image[option.payload] == 22
    elif payload == 'dynamic_sequence':
        assert image[holder] == [7, 8, 9]
        assert image[low.storage.lengths[holder]] == 2
    else:
        assert image[builder.leaves[('x',)]] == 22


def test_product_storage_allocation_preserves_leaf_order_without_emission(low, monkeypatch):
    node = ast.parse('helper(a)').body[0].value
    optional = ValueContract.option(ValueContract.fixed((2,), 'tuple'))
    contract = ValueContract.record({
        'empty': NONE,
        'nested': ValueContract.record({'maybe': optional, 'x': SCALAR}),
        'rows': ValueContract.sequence('list', 0, 3, SCALAR),
    })

    def unexpected(*args):
        pytest.fail('storage operations must not evaluate source or emit commands')

    monkeypatch.setattr(low, 'before', unexpected)
    monkeypatch.setattr(low, 'expr', unexpected)
    builder = lower_returns._RecordBuilder.allocate(low, contract, node)
    assert builder.leaves[('empty',)] is None
    option = builder.leaves[('nested', 'maybe')]
    assert option.present == '_t1' and option.payload == '_t2'
    assert builder.leaves[('nested', 'x')] == '_t3'
    assert builder.leaves[('rows',)] == '_t4'
    assert low.storage.lengths['_t4'] == '_t5'
    assert low.storage.shapes == {
        '_t1': (), '_t2': (2,), '_t3': (), '_t4': (3,),
    }
    assert low.flow.bounds['_t5'] == ranges.Bounds(0, 3)
    assert not low.storage.carrier_names(lowering._RecordBuilder) and not low.storage.carrier_names(lowering._OptionBuilder)
    image = execute(builder.initialize(low, node))
    assert image['_t1'] is False
    assert image['_t2'] == [0, 0] and image['_t4'] == [0, 0, 0]
    assert image['_t3'] == image['_t5'] == 0


@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('payload', ['scalar', 'array', 'record'])
def test_option_storage_keeps_presence_distinct_from_payload(low, payload, present):
    node = ast.parse('helper(a)').body[0].value
    contract = {'scalar': SCALAR, 'array': ValueContract.fixed((2,), 'tuple'),
                'record': ValueContract.record({'x': SCALAR})}[payload]
    value = {'scalar': ir.Lit(7), 'array': ir.Op('arraylit', ir.Lit(7), ir.Lit(8)),
             'record': RecordValue((('x', ir.Lit(7)),), ValueContract.record({'x': SCALAR}))}[payload]
    builder = lower_returns._OptionBuilder.allocate(low, ValueContract.option(contract), node)
    selected = value if present else OptionValue(ir.Lit(False), None, NONE)
    command = builder.assign(low, selected, node, actual=contract if present else NONE)
    image = execute(ir.Seq(builder.initialize(low, node), command))
    assert image[builder.present] is present
    holder = builder.payload.leaves[('x',)] if payload == 'record' else builder.payload
    expected = [7, 8] if payload == 'array' else 7
    inactive = [0, 0] if payload == 'array' else 0
    assert image[holder] == (expected if present else inactive)
    assert builder.contract == ValueContract.option(contract)


def test_record_storage_assignment_retains_padding_and_logical_length(low, monkeypatch):
    node = ast.parse('helper(a)').body[0].value
    contract = ValueContract.record({'row': ValueContract.sequence('list', 0, 3, SCALAR)})
    actual = ValueContract.record({'row': ValueContract.fixed((1,), 'list')})
    builder = lower_returns._RecordBuilder.allocate(low, contract, node, order=('row',))
    value = RecordValue((('row', ir.Op('arraylit', ir.Lit(9))),), actual, ('row',))
    entry = builder.initialize(low, node)
    prerequisites = []
    # Resizing still emits its prerequisites through the caller's lowerer.
    # Both backends must place those before the returned assignment.
    monkeypatch.setattr(low, 'before', prerequisites.append)
    command = builder.assign(low, value, node)
    assert prerequisites
    for prerequisite in prerequisites:
        entry = ir.Seq(entry, prerequisite)
    image = execute(ir.Seq(entry, command))
    holder = builder.leaves[('row',)]
    assert image[holder] == [9, 0, 0]
    assert image[low.storage.lengths[holder]] == 1
    assert low.storage.capacities[holder] == 3
    assert builder.order == ('row',)


def test_record_storage_different_source_orders_keep_canonical_field_writes(low):
    node = ast.parse('helper(a)').body[0].value
    contract = ValueContract.record({'a': SCALAR, 'b': SCALAR})
    builder = lower_returns._RecordBuilder.allocate(low, contract, node, order=('a', 'b'))
    value = RecordValue((('a', ir.Lit(3)), ('b', ir.Lit(4))), contract, ('b', 'a'))
    image = execute(builder.assign(low, value, node))
    assert builder.order is None
    assert image[builder.leaves[('a',)]] == 3
    assert image[builder.leaves[('b',)]] == 4


@pytest.mark.parametrize('fault,code', [
    ('unrepresented', 'record-field-representation-unsupported'),
    ('optional_dynamic', 'optional-payload-representation-unsupported'),
    ('field_contract', 'record-value-shape-mismatch'),
    ('field_shape', 'record-field-representation-unsupported'),
    ('record_contract', 'record-value-shape-mismatch'),
])
def test_product_storage_refusals_keep_source_and_reason(low, fault, code):
    node = ast.parse('helper(a)').body[0].value
    with pytest.raises(LoweringError) as error:
        if fault == 'unrepresented':
            lower_returns._RecordBuilder.allocate(low, ValueContract.record({'x': UNKNOWN}), node)
        elif fault == 'optional_dynamic':
            lower_returns._OptionBuilder.allocate(
                low, ValueContract.option(ValueContract.sequence('list', 0, 3, SCALAR)), node)
        else:
            builder = lower_returns._RecordBuilder.allocate(
                low, ValueContract.record({'x': SCALAR}), node)
            if fault == 'record_contract':
                builder.assign(low, ir.Lit(3), node)
            elif fault == 'field_contract':
                builder.assign_field(low, 'x', ir.Lit(3), NONE, node)
            else:
                builder.assign_field(low, 'x', ir.Op('arraylit', ir.Lit(3)), SCALAR, node)
    assert error.value.node is node and error.value.code == code


@pytest.mark.parametrize('capacity', [0, 3])
@pytest.mark.parametrize('tagged', [False, True])
@pytest.mark.parametrize('field_contract', [SCALAR, UNKNOWN, ValueContract.fixed((2,), 'tuple')])
def test_batch_initialization_preserves_count_sentinel_and_column_contract(
        low, monkeypatch, capacity, tagged, field_contract):
    node = ast.parse('helper(a)').body[0].value
    builder = lower_returns._BatchBuilder('count', {'x': 'buffer'}, capacity,
                                         form='form' if tagged else None)
    record = ValueContract.record({'x': field_contract})

    def unexpected(*args):
        pytest.fail('batch initialization must not evaluate source, emit code or add goals')

    monkeypatch.setattr(low, 'expr', unexpected)
    monkeypatch.setattr(low, 'before', unexpected)
    monkeypatch.setattr(low, '_safety_goal', unexpected)
    image = execute(builder.initialize(low, record, node))
    storage = max(capacity, 1)
    assert builder.capacity == capacity and image['count'] == 0
    assert ('form' in image) is tagged
    if tagged:
        assert image['form'] == 0
    element = SCALAR if field_contract is UNKNOWN else field_contract
    assert builder.buffer_contract(low, 'x', node) == ValueContract.sequence(
        'list', storage, storage, element)
    assert low.flow.lengths['buffer'] == low.storage.shapes['buffer'] == (
        storage, *element.physical_shape)
    assert image['buffer'] == [[0, 0] if element.is_sequence else 0 for _ in range(storage)]
    assert not low.storage.carrier_names(lowering._BatchBuilder)  # Binding ownership stays with the caller.


@pytest.mark.parametrize('tagged', [False, True])
def test_empty_batch_initialization_does_not_invent_columns(low, tagged):
    builder = lower_returns._BatchBuilder('count', {}, 0, form='form' if tagged else None)
    image = execute(builder.initialize(low, None, ast.parse('pass').body[0]))
    assert image == ({'count': 0, 'form': 0} if tagged else {'count': 0})
    assert not low.flow.lengths and not low.flow.contracts


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('dynamic', [False, True])
def test_batch_field_write_and_read_preserve_nested_lengths_without_advancing_count(
        low, monkeypatch, index, dynamic):
    node = ast.parse('helper(a)').body[0].value
    element = (ValueContract.sequence('list', 0, 3, SCALAR)
               if dynamic else ValueContract.fixed((2,), 'tuple'))
    actual = ValueContract.fixed((1,), 'list') if dynamic else element
    record = ValueContract.record({'x': element})
    builder = lower_returns._BatchBuilder('count', {'x': 'buffer'}, 2, form='form')
    batch = lower_returns._BatchValue(
        builder, ir.Var('form'), ValueContract.sequence('list', 0, 2, record))
    entry = builder.initialize(low, record, node)
    prerequisites = []
    monkeypatch.setattr(low, 'before', prerequisites.append)
    value = ir.Op('arraylit', ir.Lit(9)) if dynamic else ir.Op('arraylit', ir.Lit(9), ir.Lit(8))
    store = builder.store_field(low, 'x', ir.Lit(index), value, actual, node)
    for command in prerequisites:
        entry = ir.Seq(entry, command)
    image = execute(ir.Seq(entry, store))
    assert image['count'] == image['form'] == 0  # Only append/commit advances them.
    assert image['buffer'][1 - index] == ([0, 0, 0] if dynamic else [0, 0])
    projected = batch.record_at(low, ir.Lit(index), node).by_name['x']
    machine = interp._Interp(image)
    assert machine.value(projected) == ([9, 0, 0] if dynamic else [9, 8])
    assert low._shape(projected) == element.physical_shape
    lengths = low._length_image(projected)
    if dynamic:
        assert machine.value(lengths[()]) == 1
    else:
        assert not lengths


@pytest.mark.parametrize('fault,code', [
    ('element', 'source-plan-inconsistent'),
    ('schema', 'source-plan-inconsistent'),
    ('nested_record', 'nested-dynamic-sequence-unsupported'),
    ('column', 'source-plan-inconsistent'),
    ('missing_column', 'source-plan-inconsistent'),
    ('write_contract', 'record-value-shape-mismatch'),
    ('write_shape', 'store-value-shape-mismatch'),
    ('read_schema', 'output-batch-read-unsupported'),
])
def test_batch_storage_refusals_keep_source_and_reason(low, fault, code):
    node = ast.parse('helper(a)').body[0].value
    builder = lower_returns._BatchBuilder('count', {'x': 'buffer'}, 2)
    record = ValueContract.record({'x': SCALAR})
    with pytest.raises(LoweringError) as error:
        if fault == 'element':
            builder.initialize(low, SCALAR, node)
        elif fault == 'schema':
            builder.initialize(low, ValueContract.record({'y': SCALAR}), node)
        elif fault == 'nested_record':
            builder.initialize(low, ValueContract.record({'x': record}), node)
        else:
            builder.initialize(low, record, node)
            if fault == 'column':
                low.flow.contracts['buffer'] = SCALAR
                builder.buffer_contract(low, 'x', node)
            elif fault == 'missing_column':
                builder.buffer_contract(low, 'missing', node)
            elif fault == 'write_contract':
                builder.store_field(low, 'x', ir.Lit(0), ir.Lit(3), NONE, node)
            elif fault == 'write_shape':
                builder.store_field(low, 'x', ir.Lit(0), ir.Op('arraylit', ir.Lit(3)), SCALAR, node)
            else:
                batch = lower_returns._BatchValue(builder, ir.Lit(2), ValueContract.sequence(
                    'list', 0, 2, ValueContract.record({'y': SCALAR})))
                batch.record_at(low, ir.Lit(0), node)
    assert error.value.node is node and error.value.code == code


@pytest.mark.parametrize('carrier', ['list', 'tuple'])
def test_optional_sequence_literal_distinguishes_absence_and_zero(low, carrier):
    node = ast.parse('[None, 0, -2]' if carrier == 'list' else '(None, 0, -2)', mode='eval').body
    cells = [OptionValue(ir.Lit(False), None, NONE), ir.Lit(0), ir.Lit(-2)]
    value = lower_returns.OptionSequence.literal(low, node, cells)
    machine = interp._Interp({})
    assert value.contract == ValueContract.sequence(carrier, 3, 3, ValueContract.option(SCALAR))
    assert machine.value(value.present) == [False, True, True]
    assert machine.value(value.payload) == [0, 0, -2]
    assert machine.value(value.at(ir.Lit(1)).present) is True
    assert machine.value(value.at(ir.Lit(1)).payload) == 0


@pytest.mark.parametrize('element', [NONE, ValueContract.option(SCALAR)])
def test_optional_sequence_allocation_only_registers_storage(low, monkeypatch, element):
    def unexpected(*args, **kwargs):
        pytest.fail('allocation must not evaluate source, emit commands or add goals')

    for method in ('expr', 'before', '_safety_goal'):
        monkeypatch.setattr(low, method, unexpected)
    contract = ValueContract.sequence('list', 2, 2, element)
    value = lower_returns.OptionSequence.allocate(low, contract, ast.parse('cells', mode='eval').body)
    assert value.contract is contract
    assert value.present.name != value.payload.name
    for column in (value.present, value.payload):
        assert low.flow.lengths[column.name] == low.storage.shapes[column.name] == (2,)
        assert low.flow.contracts[column.name] == ValueContract.fixed((2,))
    if element is NONE:
        assert value.at(ir.Lit(0)).payload is None
        assert interp._Interp({}).value(value.at(ir.Lit(0)).present) is False


def test_optional_sequence_copy_snapshots_both_columns(low, monkeypatch):
    node = ast.parse('cells', mode='eval').body
    contract = ValueContract.sequence('list', 2, 2, ValueContract.option(SCALAR))
    value = lower_returns.OptionSequence.allocate(low, contract, node)
    prerequisites = []
    monkeypatch.setattr(low, 'before', prerequisites.append)
    command = value.assign(low, value, node)
    original = {value.present.name: [True, False], value.payload.name: [0, 23]}
    image = dict(original)
    for entry in prerequisites:
        image = execute(entry, image)
    assert prerequisites
    image.update({value.present.name: [False, True], value.payload.name: [9, 8]})
    image = execute(command, image)
    for name, cells in original.items():
        assert image[name] == cells


@pytest.mark.parametrize('present', [False, True])
def test_optional_sequence_cell_store_updates_both_columns(low, monkeypatch, present):
    target = ast.parse('cells[-1] = 0').body[0].targets[0]
    source = ast.parse('0' if present else 'None', mode='eval').body
    contract = ValueContract.sequence('list', 2, 2, ValueContract.option(SCALAR))
    value = lower_returns.OptionSequence.allocate(low, contract, target)
    # Index normalization/guards belong to the lowerer; this boundary consumes
    # its checked index and snapshots it before either column is written.
    monkeypatch.setattr(low, '_index', lambda *args: ir.Lit(1))
    prerequisites = []
    monkeypatch.setattr(low, 'before', prerequisites.append)
    cell = ir.Lit(0) if present else OptionValue(ir.Lit(False), None, NONE)
    command = value.store(low, target, cell, source)
    image = {value.present.name: [True, True], value.payload.name: [7, 8]}
    for entry in (*prerequisites, command):
        image = execute(entry, image)
    assert image[value.present.name] == [True, present]
    assert image[value.payload.name] == [7, 0]


@pytest.mark.parametrize('fault,code', [
    ('effectful', 'subscript-target-effectful'),
    ('slice', 'slice-assignment-unsupported'),
    ('tuple', 'store-target-immutable'),
    ('none_payload', 'optional-sequence-contract-disagrees'),
    ('copy_scalar', 'optional-sequence-contract-disagrees'),
    ('copy_layout', 'optional-sequence-contract-disagrees'),
    ('literal_nested', 'optional-sequence-payload-unsupported'),
])
def test_optional_sequence_refusals_preserve_source_and_reason(low, monkeypatch, fault, code):
    target = ast.parse('cells[:]' if fault == 'slice' else 'cells[0]', mode='eval').body
    source = ast.parse('[None, [1]]' if fault == 'literal_nested' else '1', mode='eval').body
    element = NONE if fault == 'none_payload' else ValueContract.option(SCALAR)
    contract = ValueContract.sequence('tuple' if fault == 'tuple' else 'list', 2, 2, element)
    value = lower_returns.OptionSequence.allocate(low, contract, target)
    monkeypatch.setattr(low, '_effectful', lambda node: fault == 'effectful')
    monkeypatch.setattr(low, '_index', lambda *args: ir.Lit(0))
    with pytest.raises(LoweringError) as error:
        if fault == 'literal_nested':
            lower_returns.OptionSequence.literal(low, source, [
                OptionValue(ir.Lit(False), None, NONE), ir.Op('arraylit', ir.Lit(1))])
        elif fault.startswith('copy_'):
            other = (ir.Lit(1) if fault == 'copy_scalar' else
                     lower_returns.OptionSequence(value.present, value.payload,
                         ValueContract.sequence('list', 1, 1, element)))
            value.assign(low, other, source)
        else:
            value.store(low, target, ir.Lit(1), source)
    site = target if fault in ('effectful', 'slice', 'tuple') else source
    assert error.value.node is site and error.value.code == code


def input_context(low, source, public):
    model = source_model(source, cls='M')
    low.resolver = ScopeResolver(model)
    low.storage.structured_input_bindings['a'] = public
    for variable in public.solver_variables:
        low.flow.ranges[variable] = ranges.Range(0, 15)
        low.flow.bounds[variable] = ranges.Bounds(0, 15)
    return model.step


@pytest.mark.parametrize('array', [False, True])
def test_written_input_gets_private_copy_after_prior_initializers(low, array):
    operation = 'a[0] = 7' if array else 'a = 7'
    source = SOURCE.replace('return {"y": a}', operation + '\n        return {"y": 0}')
    public = ValueBinding.direct('a', ValueType.array(2, U4) if array else U4)
    fn = input_context(low, source, public)
    prior = ir.Assign('prior', ir.Lit(99))
    entry, renames = lower_state.bind_input_views(low, fn, ['a'], ['y'], set(), prior)
    view = low.storage.structured_input_views['a']
    private, = view.solver_variables
    assert private != 'a'
    assert low.flow.ranges[private] == low.flow.ranges['a'] and low.flow.bounds[private] == low.flow.bounds['a']
    assert low.storage.shapes[private] == ((2,) if array else ())
    assert renames == ({} if array else {'a': private})
    values = execute(entry, {'a': [3, 4] if array else 3})
    assert values['prior'] == 99 and values[private] == values['a']
    if array:
        assert values[private] is not values['a']
        values[private][0] = 7
        assert values['a'] == [3, 4]
    else:
        values[private] = 7
        assert values['a'] == 3


def test_read_only_input_needs_no_private_view(low):
    fn = input_context(low, SOURCE, ValueBinding.direct('a', U4))
    prior = ir.Skip()
    entry, renames = lower_state.bind_input_views(low, fn, ['a'], ['y'], set(), prior)
    assert entry is prior and renames == {}
    assert low.storage.structured_input_views == {}


def test_scalar_parameter_named_like_an_output_is_private(low):
    model = source_model(SOURCE, cls='M')
    low.resolver = ScopeResolver(model)
    low.flow.ranges['a'] = ranges.Range(1, 2)
    low.flow.bounds['a'] = ranges.Bounds(1, 2)
    low._remember_source_scalar_type('a', types.INTEGER)
    entry, renames = lower_state.bind_input_views(low, model.step, ['a'], ['a'], set(), ir.Skip())
    private = renames['a']
    assert private != 'a' and low.source_scalar_types[private] == types.INTEGER
    assert execute(entry, {'a': 2}) == {'a': 2, private: 2}


@pytest.mark.parametrize('repetitions', [1, 16, 128])
def test_repeated_reserved_input_writes_reuse_one_private_cell(low, repetitions):
    source = ('class M:\n    def reset(self): pass\n    def process(self, a):\n'
              + '        a = a + 1\n' * repetitions + '        return {"a": a}\n')
    model = source_model(source, cls='M')
    low.resolver = ScopeResolver(model)
    low.flow.ranges['a'] = ranges.Range(1, 2)
    low.flow.bounds['a'] = ranges.Bounds(1, 2)
    low._remember_source_scalar_type('a', types.INTEGER)
    entry, renames = lower_state.bind_input_views(low, model.step, ['a'], ['a'], set(), ir.Skip())
    private = renames['a']
    assert low.storage.temporaries == [private]
    assert low.storage._next == 1
    assert execute(entry, {'a': 2}) == {'a': 2, private: 2}
    assert low.flow.ranges[private] == low.flow.ranges['a']
    assert low.flow.bounds[private] == low.flow.bounds['a']


def test_failed_output_initialization_stops_before_rom_and_body(monkeypatch):
    def refuse(low, prepared, planned, **kwargs):
        raise LoweringError(prepared.model.step, 'output entry rejected', code='test-output-entry')

    def unexpected(*args, **kwargs):
        pytest.fail('ROM and body must not be emitted after output setup failed')

    monkeypatch.setattr(lower_returns, 'initialize_outputs', refuse)
    monkeypatch.setattr(GraphLowerer, '_sequence_table_expr', unexpected)
    monkeypatch.setattr(GraphLowerer, 'body', unexpected)
    with pytest.raises(LoweringError) as error:
        lowering.lower_procedure(SOURCE, outputs=['y'], input_widths=[4])
    assert error.value.code == 'test-output-entry'


def _compiler_name_source(role, name):
    parameter = name if role == 'input' else 'a'
    reset = f'self.{name} = 0' if role == 'state' else 'pass'
    if role == 'local':
        body = (f'        {name} = a + 1\n'
                f'        if a & 1:\n            {name} += 2\n'
                f'        return {{"y": {name} & 15}}\n')
    elif role == 'state':
        body = (f'        if a & 1:\n            self.{name} = (self.{name} + a) & 15\n'
                f'        return {{"y": self.{name}}}\n')
    else:
        output = name if role == 'output' else 'y'
        body = f'        return {{"{output}": ({parameter} + 3) & 15}}\n'
    return (f'class M:\n    def reset(self): {reset}\n'
            f'    def process(self, {parameter}):\n' + body), parameter


@pytest.mark.parametrize('occupied', [(), ('_t1', '_t3'), tuple(f'_t{i}' for i in range(1, 65))])
def test_fresh_names_skip_source_names_without_registering_them(low, occupied):
    names = set(occupied)
    allocator = type(low)((), reserved_names=names)
    names.add('_t1000')
    allocated = [allocator.storage.fresh() for _ in range(4)]
    expected = [f'_t{i}' for i in range(1, 69) if f'_t{i}' not in occupied][:4]
    assert allocated == expected
    assert allocator.storage.temporaries == allocated
    assert not set(allocated).intersection(occupied)
    assert '_t1000' not in allocator.storage._reserved


def test_fresh_names_also_preserve_existing_range_and_array_bindings(low):
    allocator = type(low)((), env={'_t1': ranges.Range(0, 3)}, arrays={'_t2': (2,)})
    assert allocator.storage.fresh() == '_t3'
    assert allocator.storage.temporaries == ['_t3']


@pytest.mark.parametrize('role', ['input', 'local', 'state', 'output'])
@pytest.mark.parametrize('name', ['_t1', '_t3', '_t7'])
def test_compiler_style_identifiers_keep_native_outputs_and_goals(low, role, name):
    source, parameter = _compiler_name_source(role, name)
    planned = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    model = namespace['M']()
    model.reset()
    lowered = lowering.lower_source(source, input_widths=[4], lowerer=type(low))
    assert lowered.catalogue == planned.manifest
    state = None
    for value in (0, 1, 3, 2, 15):
        native_hits.clear()
        expected = model.process(value)
        state, output, hits, error = interp.run_step(lowered.proc, {parameter: value}, state)
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)


@pytest.mark.solver
@pytest.mark.parametrize('prefix', ['wire_', '_t'])
@pytest.mark.parametrize('mutable', [False, True])
def test_physical_binding_names_keep_structured_io_semantics(low, prefix, mutable):
    z3 = pytest.importorskip('z3')
    row = ValueBinding('row', ValueType.record({'flag': ValueType.boolean(), 'x': U4}), (
        (('flag',), SolverLeafPath(f'{prefix}1')),
        (('x',), SolverLeafPath(f'{prefix}3')),
    ))
    cells = ValueBinding('cells', ValueType.array(2, U4), (
        ((0,), SolverLeafPath(f'{prefix}4', (0,))),
        ((1,), SolverLeafPath(f'{prefix}4', (1,))),
    ))
    result = ValueBinding('y', ValueType.integer(FiniteType(6)), (((), SolverLeafPath(f'{prefix}7')),))
    signature = ProcedureSignature((('row', row), ('cells', cells)), (('y', result),))
    body = ('''        row = {'flag': not row['flag'], 'x': row['x'] + 1}
        row['x'] += int(row['flag'])
        cells[0] = row['x']
        cells = [cells[1], cells[0] + 1]
        return {'y': cells[0] + cells[1] + int(row['flag'])}
''' if mutable else '''        return {'y': row['x'] + int(row['flag']) + cells[0] + cells[1]}
''')
    source = 'class M:\n    def reset(self): pass\n    def process(self, row, cells):\n' + body
    planned = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    native = namespace['M']()
    native.reset()
    lowered = lowering.lower_source(source, signature=signature, lowerer=type(low))
    proc = lowered.proc
    assert lowered.catalogue == planned.manifest
    assert set(proc.inputs) == set(row.solver_variables + cells.solver_variables)
    assert set(proc.output_vars) == set(result.solver_variables)
    assert set(proc.inputs).isdisjoint(proc.locals)
    if mutable:
        for binding in (row, cells):
            private = proc.semantics.value_binding('local', binding.name)
            assert set(private.solver_variables).isdisjoint(binding.solver_variables)
    symbolic = encoder.encode_step(proc, env=proc.state_ranges)
    solver = z3.Solver()
    solver.add(symbolic.constraint)
    for flag in (False, True):
        for number in (0, 3, 15):
            for array in ([1, 2], [14, 15]):
                inputs = {'row': {'flag': flag, 'x': number}, 'cells': list(array)}
                native_hits.clear()
                expected = native.process(**deepcopy(inputs))
                _, output, hits, error = interp.run_step(proc, inputs)
                assert not error and output.transactions == [expected]
                assert hits == set(native_hits)
                assert inputs == {'row': {'flag': flag, 'x': number}, 'cells': array}
                physical = {**row.solver_values(inputs['row']), **cells.solver_values(inputs['cells'])}
                solver.push()
                for variable, value in physical.items():
                    if isinstance(value, list):
                        solver.add(*(symbolic.pre[variable][i] == item for i, item in enumerate(value)))
                    else:
                        solver.add(symbolic.pre[variable] == value)
                assert solver.check() == z3.sat
                different = [z3.Not(symbolic.ok), symbolic.post['#count'] != 1,
                             symbolic.post[f'{prefix}7'][0] != expected['y']]
                different.extend(term != (goal in hits) for goal, term in symbolic.hits.items())
                solver.add(z3.Or(*different))
                assert solver.check() == z3.unsat
                solver.pop()

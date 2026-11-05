"""Final physical contexts and reset images, independent of reachability policy."""

import itertools
from types import SimpleNamespace

import pytest

from behave.sim.behavior import audit, lower_checks, lower_context, lower_state, lowering
from behave.sim.symbolic import ir, typecheck as types
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.source_values import SCALAR, SourceValueBudget, ValueContract
from behave.sim.symbolic.state_projection import SourceProductBinding, StatePresenceBinding
from behave.sim.symbolic.uniform_array import UniformArray
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
def layout(request):
    model = source_model(SOURCE, cls='M')
    budget = SourceValueBudget()
    low = request.param([], source_value_budget=budget)
    prepared = SimpleNamespace(model=model, resolver=ScopeResolver(model))
    declarations = SimpleNamespace(
        step_writes=set(), dynamic_state=frozenset(), state_names={}, state_sources={},
        ir_initial={}, structured_initial={}, construction_values={})
    planned = SimpleNamespace(declaration_contracts={})
    return SimpleNamespace(low=low, prepared=prepared, declarations=declarations,
                           planned=planned, budget=budget)


def collect(layout, body=None, **kwargs):
    options = {'body': ir.Skip() if body is None else body,
               'reachable_bodies': [layout.prepared.model.step],
               'fixed': {}, 'inherited': set(), 'output_vars': {}}
    options.update(kwargs)
    return lower_context.collect_contexts(
        layout.low, layout.prepared, layout.declarations, **options)


def image(layout, state=(), structured=None, products=None):
    contexts = lower_context.ProcedureContexts(
        list(state), [], [], set(), structured or {}, products or {}, set())
    return lower_context.initial_image(
        layout.low, layout.prepared, layout.declarations, layout.planned, contexts)


def test_live_contexts_use_physical_inputs_and_retained_definitions(layout):
    binding = ValueBinding('a', ValueType.record({'x': U4, 'ok': ValueType.boolean()}), (
        (('ok',), SolverLeafPath('in_ok')), (('x',), SolverLeafPath('in_x'))))
    layout.low.storage.structured_input_bindings['a'] = binding
    scratch = layout.low.storage.fresh()
    layout.low.storage.state_names['a'] = 'self.a'
    layout.declarations.state_names['a'] = 'self.a'
    layout.declarations.step_writes.add('a')
    body = ir.Seq(ir.Assign('out', ir.Lit(0)),
                  ir.For('i', ir.Lit(2), ir.Store('scratch', ir.Var('i'), ir.Var('in_x'))))
    before = layout.budget.left
    result = collect(layout, body, output_vars={'y': 'out'})
    assert result.inputs == list(binding.solver_variables)
    assert result.state == ['self.a']  # Pruned write still carries its state.
    assert result.locals_ == ['i', 'scratch'] and result.held == {'out'}
    assert scratch not in result.locals_
    assert layout.low._source_value_budget is layout.budget and layout.budget.left == before


@pytest.mark.parametrize('read_site', ['none', 'assignment', 'loop-count'])
def test_pruned_late_attribute_is_absent_not_zero(layout, read_site):
    source = SOURCE.replace('return {"y": a}', 'return {"y": self.late}')
    model = source_model(source, cls='M')
    layout.prepared.model, layout.prepared.resolver = model, ScopeResolver(model)
    layout.declarations.state_names['late'] = 'payload'
    layout.low.storage.state_names['late'] = 'payload'
    layout.low.storage.state_presence['late'] = 'exists'
    retained = read_site != 'none'
    body = {'none': ir.Skip(), 'assignment': ir.Assign('local', ir.Var('payload')),
            'loop-count': ir.For('i', ir.Var('payload'), ir.Skip())}[read_site]
    result = collect(layout, body)
    assert result.absent_state == (set() if retained else {'late'})
    assert result.state == (['exists', 'payload'] if retained else [])
    values, kinds = lower_context.initial_image(
        layout.low, layout.prepared, layout.declarations, layout.planned, result)
    assert 'payload' not in values and 'payload' not in kinds
    assert values == ({'exists': False} if retained else {})
    assert kinds == ({'exists': types.BOOLEAN} if retained else {})


def test_contexts_preserve_bindings_without_duplicate_logical_variables(layout):
    structured = ValueBinding('row', ValueType.boolean(), (((), SolverLeafPath('row_bit')),))
    product = SourceProductBinding('opt', ValueContract.option(SCALAR),
                                   ((('present',), 'tag'), (('payload',), 'value')))
    layout.declarations.step_writes.update(['row', 'opt'])
    layout.low.storage.structured_bindings['row'] = structured
    layout.low.storage.state_products['opt'] = product
    layout.low.storage.state_length_variables.add('length')
    result = collect(layout)
    assert result.state == ['length', 'row_bit', 'tag', 'value']
    assert result.active_structured['row'] is structured
    assert result.active_products['opt'] is product


def test_resolved_method_reference_is_not_state(layout):
    source = SOURCE.replace('    def process', '    def helper(self, a): return a\n    def process')
    source = source.replace('return {"y": a}', 'f = self.helper\n        return {"y": f(a)}')
    model = source_model(source, cls='M')
    layout.prepared.model, layout.prepared.resolver = model, ScopeResolver(model)
    assert collect(layout).state == []


def test_nested_lengths_and_padding_use_declared_storage(layout):
    inner = ValueContract.sequence('list', 0, 3, SCALAR)
    outer = ValueContract.sequence('list', 0, 2, inner)
    layout.declarations.ir_initial = {'rows': [[7]]}
    layout.declarations.state_names = {'rows': 'self.rows'}
    layout.declarations.state_sources = {'self.rows': 'rows'}
    layout.planned.declaration_contracts = {'rows': outer}
    layout.low.storage.declaration_contracts = {'self.rows': outer}
    layout.low.storage.length_images['self.rows'] = {(): 'nrows', (lower_state.SEQUENCE_ELEMENT,): 'lengths'}
    layout.low.storage.state_length_variables.update(['nrows', 'lengths'])
    layout.low.storage.shapes['lengths'] = (2,)
    values, kinds = image(layout, ['self.rows', 'nrows', 'lengths'])
    assert values == {'self.rows': [[7, 0, 0], [0, 0, 0]], 'nrows': 1, 'lengths': [1, 0]}
    assert kinds == {'self.rows': types.of_shape((2, 3)), 'nrows': types.INTEGER,
                     'lengths': types.of_shape((2,))}
    assert layout.declarations.ir_initial == {'rows': [[7]]}


def test_initial_values_outside_final_state_are_not_materialized(layout):
    layout.declarations.ir_initial = {'unused': object()}
    assert image(layout) == ({}, {})


def test_unconstructed_scalar_is_not_assumed_to_be_integer(layout):
    layout.low.storage.declaration_contracts = {'known': SCALAR, 'unknown': SCALAR}
    layout.low.source_scalar_types['known'] = types.BOOLEAN
    values, kinds = image(layout, ['known', 'unknown'])
    assert values == {} and kinds == {'known': types.BOOLEAN}


def test_initial_boolean_retains_its_type(layout):
    layout.declarations.ir_initial = {'flag': False}
    values, kinds = image(layout, ['flag'])
    assert values['flag'] is False and kinds == {'flag': types.BOOLEAN}


def test_typed_record_and_optional_product_keep_their_declared_images(layout):
    record = ValueBinding('row', ValueType.record({'x': U4, 'flag': ValueType.boolean()}), (
        (('flag',), SolverLeafPath('flag')), (('x',), SolverLeafPath('x'))))
    product = SourceProductBinding('opt', ValueContract.option(SCALAR),
                                   ((('present',), 'tag'), (('payload',), 'value')))
    layout.declarations.structured_initial = {'row': {'x': 3, 'flag': True}}
    layout.declarations.construction_values = {'opt': None}
    values, kinds = image(layout, ['flag', 'x', 'tag', 'value'], {'row': record}, {'opt': product})
    assert values == {'flag': True, 'x': 3, 'tag': False, 'value': 0}
    assert kinds == {'flag': types.BOOLEAN, 'x': types.INTEGER,
                     'tag': types.BOOLEAN, 'value': types.INTEGER}


@pytest.mark.parametrize('private', [False, True])
def test_conflicting_input_types_keep_specific_refusal(layout, private):
    state = ValueBinding('row', ValueType.boolean(), (((), SolverLeafPath('shared')),))
    incoming = ValueBinding('a', U4, (((), SolverLeafPath('shared')),))
    layout.declarations.structured_initial = {'row': False}
    table = layout.low.storage.structured_input_views if private else layout.low.storage.structured_input_bindings
    table['a'] = incoming
    with pytest.raises(LoweringError) as caught:
        image(layout, ['shared'], {'row': state})
    assert caught.value.code == 'value-binding-type-mismatch'
    assert ('private input-view' if private else 'state/input binding') in str(caught.value)
    assert caught.value.node is layout.prepared.model.step


@pytest.mark.parametrize('source_value', [7, [1, 2, 3]])
def test_invalid_initial_sequence_is_a_representation_refusal(layout, source_value):
    layout.declarations.ir_initial = {'row': source_value}
    layout.planned.declaration_contracts = {'row': ValueContract.sequence('list', 0, 2, SCALAR)}
    with pytest.raises(LoweringError) as caught:
        image(layout, ['row'])
    assert caught.value.code == 'initial-state-representation-disagrees'
    assert caught.value.node is layout.prepared.model.step


@pytest.mark.parametrize('live', [False, True])
def test_only_live_length_carriers_require_an_initial_sequence(layout, live):
    layout.low.storage.length_images['row'] = {(): 'n'}
    if live:
        layout.low.storage.state_length_variables.add('n')
        with pytest.raises(LoweringError) as caught:
            image(layout, ['n'])
        assert caught.value.code == 'initial-state-representation-disagrees'
    else:
        assert image(layout) == ({}, {})


def test_compact_initial_image_keeps_identity_without_dense_expansion():
    value = UniformArray(1_000_000, 0)
    contract = ValueContract.sequence('list', len(value), len(value), SCALAR)
    assert lower_context.physical_native_value(value, contract) is value
    with pytest.raises(ValueError, match='another sequence contract'):
        lower_context.physical_native_value(value, ValueContract.sequence('list', 0, len(value), SCALAR))


def test_dynamic_path_retains_record_option_and_shared_sequence_marker():
    child = ValueContract.sequence('list', 0, 4, SCALAR)
    row = ValueContract.option(ValueContract.record({'data': child}))
    contract = ValueContract.sequence('list', 1, 2, row)
    path = (lower_state.SEQUENCE_ELEMENT, ('payload',), ('field', 'data'))
    assert list(lower_context.dynamic_length_specs(contract)) == [((), (), 2), (path, (2,), 4)]
    assert lower_context.native_length_at([{'data': [3, 7]}], contract, path) == [2, 0]


@pytest.mark.parametrize('trip_count', [ir.Lit(2), ir.Var('trips')])
def test_variable_reads_walks_control_bounds_and_store_indices(trip_count):
    body = ir.If(ir.Var('condition'),
                 ir.For('i', trip_count,
                        ir.Store('buffer', ir.Var('position'), ir.Var('value'))),
                 ir.Assign('tmp', ir.Op('+', ir.Var('other'), ir.Lit(1))))
    expected = {'condition', 'position', 'value', 'other'}
    if isinstance(trip_count, ir.Var):
        expected.add('trips')
    assert lower_context.variable_reads(body) == expected


def test_variable_reads_visits_a_shared_expression_once():
    class CountedVar(ir.Var):
        reads = 0

        def __getattribute__(self, name):
            if name == 'name':
                type(self).reads += 1
            return super().__getattribute__(name)

    shared = CountedVar('x')
    expression = shared
    for _ in range(12):
        expression = ir.Op('add', expression, expression)
    body = ir.Seq(ir.Assign('a', expression), ir.For('i', expression, ir.Assign('b', shared)))
    assert lower_context.variable_reads(body) == {'x'}
    assert CountedVar.reads == 1


def test_variable_reads_is_stack_safe_and_includes_array_selection():
    expression = ir.Sel(ir.Var('table'), ir.Var('position'))
    for _ in range(2500):
        expression = ir.Op('add', expression, ir.Lit(1))
    assert lower_context.variable_reads(ir.For('i', expression, ir.Skip())) == {'table', 'position'}


@pytest.mark.parametrize('lowerer', [lowering._Lowerer, GraphLowerer])
def test_dynamic_state_loop_matches_native_over_multistep_histories(lowerer):
    source = '''class M:
    def reset(self): self.trips = 0
    def process(self, a):
        total = 0
        for i in range(self.trips):
            total += a + i
        self.trips = a & 3
        return {"y": total}
'''
    proc = lowering.lower_with_state_ranges(
        source, input_widths=[2], outputs=['y'], lowerer=lowerer, bound=3)
    assert 'trips' in proc.state and 'trips' in lower_context.variable_reads(proc.body)
    assert any(isinstance(cmd, ir.For) and not isinstance(cmd.trip_count, ir.Lit)
               for cmd in ir.walk(proc.body))
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    histories = [[{'a': a} for a in values] for values in itertools.product(range(4), repeat=3)]
    assert audit.model_audit(proc, namespace['M'], histories) == []


def project(layout, proc, *, absent=()):
    contexts = lower_context.ProcedureContexts(
        list(proc.state), list(proc.inputs), list(proc.locals), set(), {}, {}, set(absent))
    lower_context.bind_source_state(
        proc, layout.low, layout.prepared, layout.declarations, layout.planned, contexts)


def state_source(layout, *, written=False):
    source = SOURCE.replace('def reset(self): pass', 'def reset(self): self.n = 0')
    if written:
        source = source.replace('return {"y": a}', 'self.n = a\n        return {"y": self.n}')
    model = source_model(source, cls='M')
    layout.prepared.model, layout.prepared.resolver = model, ScopeResolver(model)
    layout.declarations.source_state = model.state_contract()
    layout.declarations.state_names = {'n': 'self.n'}
    layout.declarations.construction_values = {'n': 0}


@pytest.mark.parametrize('violation', ['invented', 'missing', 'loop-read'])
def test_source_projection_rejects_invented_missing_or_unrepresented_state(layout, violation):
    state_source(layout, written=violation == 'missing')
    body = ir.For('i', ir.Var('self.n'), ir.Skip()) if violation == 'loop-read' else ir.Skip()
    proc = ir.Procedure(['unknown'] if violation == 'invented' else [], ['a'], [], [], body)
    with pytest.raises(LoweringError) as caught:
        project(layout, proc)
    assert caught.value.code == {
        'invented': 'state-outside-the-source-contract',
        'missing': 'state-missing-from-the-lowering',
        'loop-read': 'state-read-but-not-carried',
    }[violation]
    assert caught.value.node is layout.prepared.model.cls


def test_source_projection_preserves_renamed_presence_and_reset_constants(layout):
    state_source(layout, written=True)
    layout.low.storage.state_presence['n'] = 'exists'
    proc = ir.Procedure(['self.n', 'exists'], ['a'], [], [], ir.Skip())
    project(layout, proc)
    projection = proc.contract.projection('n')
    assert isinstance(projection, StatePresenceBinding)
    assert projection.reconstruct({'self.n': 7, 'exists': True}) == 7
    assert proc.contract.how('n') == ('state',)
    state_source(layout)
    layout.low.storage.state_presence.clear()
    proc = ir.Procedure([], ['a'], [], [], ir.Skip())
    project(layout, proc)
    assert proc.contract.how('n') == ('constant', 0)


@pytest.mark.parametrize('lowerer', [lowering._Lowerer, GraphLowerer])
def test_goal_refusal_still_precedes_initial_image(monkeypatch, lowerer):
    seen = []
    collect_original = lower_context.collect_contexts

    def record(*args, **kwargs):
        seen.append('contexts')
        return collect_original(*args, **kwargs)

    def refuse(fn, *args):
        seen.append('goals')
        raise LoweringError(fn, 'goal refused', code='test-goal-refusal')

    def unexpected(*args):
        pytest.fail('initial image must not be assembled after goal accounting fails')

    monkeypatch.setattr(lower_context, 'collect_contexts', record)
    monkeypatch.setattr(lower_checks, 'check_goals', refuse)
    monkeypatch.setattr(lower_context, 'initial_image', unexpected)
    with pytest.raises(LoweringError) as caught:
        lowering.lower_procedure(SOURCE, input_widths=[4], outputs=['y'], lowerer=lowerer)
    assert caught.value.code == 'test-goal-refusal' and seen == ['contexts', 'goals']

"""Bounded seeds, arbitrary-step ranges and frontend cache stay distinct."""

from types import SimpleNamespace
from dataclasses import replace
from types import MappingProxyType

import pytest

from behave.sim.behavior import lower_prepare, lower_state, lowering
from behave.sim.symbolic import ranges, typecheck as types
from behave.sim.behavior.construction import construction_facts
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.source_sequences import SequencePlanError
from behave.sim.behavior.source_values import SCALAR, SourceValueBudget, ValueContract
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

SOURCE = '''class M:
    def reset(self):
        self.counter = 0
        self.width = 3
    def process(self, x):
        self.counter += 1
        return {"y": x + self.counter + self.width}
'''


def context(*, dynamic=(), outputs=('y',)):
    budget = SourceValueBudget()
    prepared = lower_prepare.prepare_source(
        SOURCE, 'behavior.py', 'process', outputs=outputs, input_widths=[3],
        signature=None, construction=construction_facts(SOURCE), _analysis_cache=None,
        cls='M', operation_goals=(), source_rules=EMPTY_SOURCE_RULES,
        source_value_budget=budget)
    declarations = lower_state.declare_state(
        prepared.model, prepared.model.step, prepared.constants, prepared.resolver,
        prepared.declaration_owner, prepared.construction,
        signature_input_bindings={}, dynamic_state=dynamic, boundary_initial=None,
        value_types=None, read_initial=None, source_value_budget=budget)
    prepared.resolver.mark_externally_dynamic_state(declarations.dynamic_state)
    return prepared, declarations, budget


def stub_plan(prepared, *, root=None, storage=None):
    storage = {'counter': SCALAR, 'width': SCALAR} if storage is None else storage
    activation = object()

    def initial_ranges(fn, context):
        assert fn is prepared.model.step and context is activation
        return {} if root is None else root

    return SimpleNamespace(
        state_contracts=storage, state_storage_contracts=storage,
        output_fields=('y',), root_context=activation, initial_ranges=initial_ranges)


@pytest.mark.parametrize('bound', [None, 0, 4])
@pytest.mark.parametrize('dynamic', [(), ('width',)])
def test_finite_seed_is_not_the_arbitrary_step_range(monkeypatch, bound, dynamic):
    prepared, declarations, budget = context(dynamic=dynamic)
    requests = []
    sequence = stub_plan(prepared)
    state_ranges = {'counter': ranges.Range(0, 4), 'width': ranges.Range(2, 6),
                    'x': ranges.Range(1, 5), '_t_returned': ranges.Range(1, 1),
                    'local': ranges.Range(8, 9)}

    def plan(model, resolver, outputs, **request):
        assert model is prepared.model and resolver is prepared.resolver
        assert request['budget'] is budget
        requests.append(request)
        return sequence

    monkeypatch.setattr(lower_prepare, 'plan_sequences', plan)
    result = lower_prepare.plan_representation(
        prepared, declarations, state_ranges=state_ranges, bound=bound,
        _analysis_cache=None, source_value_budget=budget)
    expected = {'x': ranges.Range(1, 5)}
    if bound is not None:
        expected['self.counter'] = ranges.Range(0, 0)
        if not dynamic:
            expected['self.width'] = ranges.Range(3, 3)
    if dynamic:
        expected['self.width'] = ranges.Range(2, 6)
    assert len(requests) == 1
    assert requests[0]['input_ranges'] == expected
    assert requests[0]['state_bound'] is bound
    assert requests[0]['declarations'] == ({} if dynamic else {'width': 3})
    assert result.sequence_plan is sequence
    assert result.env == {name: state_ranges[name] for name in ('counter', 'width', 'x')}
    assert result.params == ['x'] and result.outputs == ['y']


def test_root_ranges_intersect_entry_values_without_leaking_locals(monkeypatch):
    prepared, declarations, budget = context(outputs=None)
    sequence = stub_plan(prepared, root={
        'self.counter': ranges.Range(1, 2), 'x': ranges.Range(2, 6),
        'self.ghost': ranges.Range(3, 3), 'temporary': ranges.Range(4, 4)})
    monkeypatch.setattr(lower_prepare, 'plan_sequences', lambda *a, **kw: sequence)
    result = lower_prepare.plan_representation(
        prepared, declarations, state_ranges={'counter': ranges.Range(0, 3)},
        bound=3, _analysis_cache=None, source_value_budget=budget)
    assert result.env == {'x': ranges.Range(2, 6), 'counter': ranges.Range(1, 2),
                          'width': ranges.Range(3, 3)}
    assert result.outputs == ['y']


def test_inconsistent_root_range_is_not_silently_widened(monkeypatch):
    prepared, declarations, budget = context()
    sequence = stub_plan(prepared, root={'x': ranges.Range(8, 9)},
                         storage={'counter': ValueContract.fixed((2, 3))})
    monkeypatch.setattr(lower_prepare, 'plan_sequences', lambda *a, **kw: sequence)
    with pytest.raises(LoweringError) as error:
        lower_prepare.plan_representation(
            prepared, declarations, state_ranges=None, bound=2,
            _analysis_cache=None, source_value_budget=budget)
    assert error.value.code == 'source-range-plan-inconsistent'
    assert declarations.model_arrays == {}, 'a failed plan cannot publish a partial layout'


def test_planner_cannot_publish_undeclared_source_state(monkeypatch):
    prepared, declarations, budget = context()
    sequence = stub_plan(prepared, storage={'ghost': SCALAR})
    monkeypatch.setattr(lower_prepare, 'plan_sequences', lambda *a, **kw: sequence)
    with pytest.raises(LoweringError) as error:
        lower_prepare.plan_representation(
            prepared, declarations, state_ranges=None, bound=1,
            _analysis_cache=None, source_value_budget=budget)
    assert error.value.code == 'state-outside-the-source-contract'


def test_storage_contract_belongs_to_the_plan_not_the_declaration(monkeypatch):
    prepared, declarations, budget = context()
    array = ValueContract.fixed((2, 3))
    sequence = stub_plan(prepared, storage={'counter': array})
    shapes = declarations.model_arrays
    monkeypatch.setattr(lower_prepare, 'plan_sequences', lambda *a, **kw: sequence)
    result = lower_prepare.plan_representation(
        prepared, declarations, state_ranges=None, bound=1,
        _analysis_cache=None, source_value_budget=budget)
    assert declarations.model_arrays is shapes and shapes == {}
    assert result.model_arrays == {'counter': (2, 3)}
    with pytest.raises(TypeError):
        shapes['counter'] = (9,)
    with pytest.raises(TypeError):
        result.model_arrays['counter'] = (9,)
    assert result.boundary_arrays == {'counter': types.of_shape((2, 3))}
    assert result.declaration_contracts == {'counter': array}


@pytest.mark.parametrize('declared', [{}, {'counter': (7,)}])
def test_replanning_does_not_inherit_shapes_from_another_domain(monkeypatch, declared):
    prepared, declarations, budget = context()
    declarations = replace(declarations, model_arrays=MappingProxyType(declared))
    plans = []
    for shape in ((2, 3), (4,), None):
        storage = {} if shape is None else {'counter': ValueContract.fixed(shape)}
        sequence = stub_plan(prepared, storage=storage)
        monkeypatch.setattr(lower_prepare, 'plan_sequences', lambda *a, **kw: sequence)
        plans.append(lower_prepare.plan_representation(
            prepared, declarations, state_ranges=None, bound=1,
            _analysis_cache=None, source_value_budget=budget))
    assert declarations.model_arrays == declared
    assert [dict(plan.model_arrays) for plan in plans] == [
        {'counter': (2, 3)}, {'counter': (4,)}, declared]


def test_plan_failure_keeps_its_site_and_code_and_never_emits_ir(monkeypatch):
    facts = construction_facts(SOURCE)
    calls = []
    failed_nodes = []

    def failed(model, *args, **kwargs):
        calls.append(kwargs['budget'])
        failed_nodes.append(model.step)
        raise SequencePlanError(model.step, 'test plan failure', 'test-plan-failure')

    def forbidden(*args, **kwargs):
        pytest.fail('emitter ran after failed source plan')

    monkeypatch.setattr(lower_prepare, 'plan_sequences', failed)
    with pytest.raises(LoweringError, match='test plan failure') as error:
        lowering.lower_procedure(SOURCE, input_widths=[3], lowerer=forbidden,
                                 construction=facts)
    assert error.value.code == 'test-plan-failure'
    assert len(calls) == len(failed_nodes) == 1
    assert error.value.node is failed_nodes[0]

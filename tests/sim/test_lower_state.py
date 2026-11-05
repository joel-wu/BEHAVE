"""Construction truth, boundary bindings and dynamic state are distinct inputs."""

import pytest

from behave.sim.behavior import lower_prepare, lower_state, lowering
from behave.sim.behavior.construction import construction_facts
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.source_values import SourceValueBudget, SourceValueBudgetExceeded
from behave.sim.core.contract import FiniteType
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES
from behave.sim.core.value_type import ValueType

SOURCE = '''class M:
    def reset(self):
        self.x = 0
        self.buf = [0, 1]
    def process(self, x):
        self.x = x
        return {"y": self.x + self.buf[0]}
'''


def facts(values):
    # Let the real constructor own canonical values and non-IR carrier metadata.
    reset = ''.join(f'        self.{name} = {value!r}\n'
                    for name, value in sorted(values.items()))
    source = ('class M:\n    def reset(self):\n' + reset
              + '    def process(self, x):\n        return {"y": x}\n')
    return construction_facts(source, 'state_fixture.py', cls_name='M')


def declare(source=SOURCE, values=None, **overrides):
    budget = overrides.pop('source_value_budget', SourceValueBudget())
    construction = facts({'x': 0, 'buf': [0, 1]} if values is None else values)
    prepared = lower_prepare.prepare_source(
        source, 'behavior.py', 'process', outputs=['y'], input_widths=[4],
        signature=None, construction=construction, _analysis_cache=None, cls='M',
        operation_goals=(), source_rules=EMPTY_SOURCE_RULES, source_value_budget=budget)
    args = {'signature_input_bindings': prepared.signature_input_bindings,
            'dynamic_state': (), 'boundary_initial': None, 'value_types': None,
            'read_initial': None, 'source_value_budget': budget}
    args.update(overrides)
    return lower_state.declare_state(
        prepared.model, prepared.model.step, prepared.constants, prepared.resolver,
        prepared.declaration_owner, construction, **args)


def test_state_names_do_not_alias_public_input_names():
    result = declare()
    assert result.source_state == ('buf', 'x')
    assert result.input_variables == {'x'}
    assert result.state_names == {'buf': 'buf', 'x': 'self.x'}
    assert result.state_sources == {'buf': 'buf', 'self.x': 'x'}
    assert result.construction_values == result.ir_initial == {'x': 0, 'buf': [0, 1]}
    assert result.step_writes == {'x'}
    assert result.model_arrays == {'buf': (2,)}


@pytest.mark.parametrize('wrong', [1, False])
def test_read_initial_cannot_override_authenticated_value_or_carrier(wrong):
    with pytest.raises(LoweringError) as error:
        declare(read_initial={'x': wrong})
    assert error.value.code == 'initial-state-disagrees'


def test_source_fold_cannot_override_constructed_evidence():
    with pytest.raises(LoweringError) as error:
        declare(values={'x': 3, 'buf': [0, 1]})
    assert error.value.code == 'initial-state-disagrees'


def test_environment_binding_is_separate_from_reset_check():
    initial = {'buf': [8, 9]}
    result = declare(boundary_initial=initial, read_initial={'buf': [0, 1]},
                     dynamic_state=['buf'])
    assert result.boundary_initial == initial
    assert result.boundary_initial is not initial
    assert result.construction_values['buf'] is initial['buf']
    assert result.ir_initial['buf'] is initial['buf']
    assert result.dynamic_state == frozenset({'buf'})
    assert result.step_writes == {'x', 'buf'}
    with pytest.raises(LoweringError) as error:
        declare(boundary_initial=initial, read_initial=initial)
    assert error.value.code == 'initial-state-disagrees'


@pytest.mark.parametrize(('overrides', 'code'), [
    ({'boundary_initial': {'ghost': 1}}, 'boundary-initial-outside-source-contract'),
    ({'dynamic_state': ['ghost']}, 'dynamic-state-outside-source-contract'),
    ({'value_types': {'ghost': ValueType.integer(FiniteType(4))}}, 'value-type-name-undeclared'),
])
def test_entry_declarations_cannot_invent_state(overrides, code):
    with pytest.raises(LoweringError) as error:
        declare(**overrides)
    assert error.value.code == code


@pytest.mark.parametrize('overrides', [
    {'boundary_initial': []}, {'dynamic_state': 'buf'}, {'dynamic_state': [1]},
])
def test_state_entry_contexts_keep_exact_carrier_requirements(overrides):
    with pytest.raises(TypeError):
        declare(**overrides)


def test_explicit_array_type_rejects_tuple_instead_of_erasing_source_semantics():
    declaration = ValueType.array(2, ValueType.integer(FiniteType(4)))
    with pytest.raises(LoweringError) as error:
        declare(source=SOURCE.replace('[0, 1]', '(0, 1)'),
                values={'x': 0, 'buf': (0, 1)}, value_types={'buf': declaration})
    assert error.value.code == 'declared-state-value-mismatch'


def test_explicit_array_boundary_updates_structured_and_numeric_entry_together():
    declaration = ValueType.array(2, ValueType.integer(FiniteType(4)))
    result = declare(value_types={'buf': declaration}, boundary_initial={'buf': [4, 5]})
    assert result.structured_initial['buf'] == result.ir_initial['buf'] == [4, 5]
    assert result.declared_value_types['buf'] is declaration
    contract = result.declaration_contracts['buf']
    assert contract.carrier == 'list' and contract.physical_shape == (2,)


def test_string_declaration_uses_the_callers_aggregate_budget(monkeypatch):
    budget = SourceValueBudget()
    original = lower_state.finite_string_state_domains
    seen = []

    def observe(*args, **kwargs):
        assert kwargs['budget'] is budget
        seen.append(budget.left)
        return original(*args, **kwargs)

    monkeypatch.setattr(lower_state, 'finite_string_state_domains', observe)
    source = '''class M:
    def reset(self): self.mode = "idle"
    def process(self, x):
        self.mode = "run" if x else "idle"
        return {"y": int(self.mode == "run")}
'''
    result = declare(source, {'mode': 'idle'}, source_value_budget=budget)
    assert result.declared_value_types['mode'].members == ('idle', 'run')
    assert len(seen) == 1 and budget.left < seen[0]


def test_state_planning_exhaustion_is_not_retried_or_given_a_new_budget(monkeypatch):
    budget = SourceValueBudget()
    calls = []

    def exhausted(model, resolver, initial, *, exclude, budget):
        calls.append(budget)
        raise SourceValueBudgetExceeded(model.step)

    monkeypatch.setattr(lower_state, 'finite_string_state_domains', exhausted)
    with pytest.raises(LoweringError) as error:
        declare(source_value_budget=budget)
    assert calls == [budget]
    assert error.value.code == 'source-plan-budget-exceeded'


def test_failed_state_declaration_stops_before_ir_emission(monkeypatch):
    failure = LoweringError(None, 'test declaration failure', code='test-state-failure')

    def rejected(*args, **kwargs):
        raise failure

    def forbidden(*args, **kwargs):
        pytest.fail('IR emitter ran after rejected state declaration')

    monkeypatch.setattr(lower_state, 'declare_state', rejected)
    with pytest.raises(LoweringError) as error:
        lowering.lower_procedure(SOURCE, input_widths=[4], lowerer=forbidden,
                                 construction=facts({'x': 0, 'buf': [0, 1]}))
    assert error.value is failure

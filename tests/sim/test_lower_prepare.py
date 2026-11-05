"""Source preparation keeps caller evidence, identities and budget ownership."""

import pytest

from behave.sim.behavior import lower_prepare
from behave.sim.behavior.construction import ConstructionFacts, ConstructionUnavailable
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.behavior.source_values import SourceValueBudget, SourceValueBudgetExceeded
from behave.sim.core.contract import FiniteType
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

SOURCE = '''
class Model:
    def reset(self):
        pass
    def process(self, x):
        return {"y": x + 1}
'''


def prepare(**overrides):
    args = {'source': SOURCE, 'file': 'model.py', 'method': 'process', 'outputs': ['y'],
            'input_widths': (8,), 'signature': None,
            'construction': ConstructionFacts((), (), (), ()),
            '_analysis_cache': None, 'cls': 'Model', 'operation_goals': (),
            'source_rules': EMPTY_SOURCE_RULES, 'source_value_budget': SourceValueBudget()}
    args.update(overrides)
    return lower_prepare.prepare_source(**args)


def test_preparation_spends_the_same_caller_budget_in_both_source_stages(monkeypatch):
    budget = SourceValueBudget()
    seen = []
    for name in ('source_model', 'ScopeResolver'):
        original = getattr(lower_prepare, name)

        def observe(*args, _original=original, _name=name, **kwargs):
            assert kwargs['source_value_budget'] is budget
            seen.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(lower_prepare, name, observe)
    before = budget.left
    prepare(source_value_budget=budget)
    assert seen == ['source_model', 'ScopeResolver']
    assert budget.left < before


def test_cached_passes_share_source_and_construction_but_not_resolver(monkeypatch):
    cache = lower_prepare.PreparationCache()
    facts = ConstructionFacts((), (), (), ())
    acquisitions = []

    def acquire(source, file, *, cls_name):
        acquisitions.append((source, file, cls_name))
        return facts

    monkeypatch.setattr(lower_prepare, 'construction_facts', acquire)
    first = prepare(_analysis_cache=cache, construction=None)
    second = prepare(_analysis_cache=cache, construction=None)
    assert first.model is second.model
    assert first.model.step is second.model.step
    assert first.construction is second.construction is facts
    assert first.resolver is not second.resolver
    assert first.goal_plan.manifest == second.goal_plan.manifest == cache.goal_manifest
    assert acquisitions == [(SOURCE, 'model.py', 'Model')]


@pytest.mark.parametrize('stage', ['source_model', 'ScopeResolver'])
def test_budget_exhaustion_preserves_failure_without_retry(monkeypatch, stage):
    budget = SourceValueBudget()
    calls = []

    def exhausted(*args, **kwargs):
        assert kwargs['source_value_budget'] is budget
        calls.append(stage)
        raise SourceValueBudgetExceeded(None)

    monkeypatch.setattr(lower_prepare, stage, exhausted)
    with pytest.raises(LoweringError) as error:
        prepare(source_value_budget=budget)
    assert error.value.code == 'source-plan-budget-exceeded'
    assert calls == [stage]


@pytest.mark.parametrize(('overrides', 'code'), [
    ({'source': 'import os\n' + SOURCE}, 'source-import-unapproved'),
    ({'source': SOURCE.replace('x + 1', 'self.__dict__')}, 'reflection-attribute-forbidden'),
    ({'signature': ProcedureSignature([('other', FiniteType(8))], [('y', FiniteType(8))])},
     'signature-inputs-mismatch'),
])
def test_static_rejection_precedes_construction(monkeypatch, overrides, code):
    def forbidden(*args, **kwargs):
        pytest.fail('static rejection must not acquire construction evidence')

    monkeypatch.setattr(lower_prepare, 'construction_facts', forbidden)
    with pytest.raises(LoweringError) as error:
        prepare(construction=None, **overrides)
    assert error.value.code == code


def test_signature_bindings_and_supplied_evidence_retain_identity():
    signature = ProcedureSignature([('x', FiniteType(8))], [('y', FiniteType(9))])
    facts = ConstructionFacts((), (), (), ())
    result = prepare(signature=signature, construction=facts)
    assert result.construction is facts
    assert result.signature_input_bindings['x'] is signature.input_bindings[0]
    assert result.signature_output_bindings['y'] is signature.output_bindings[0]
    assert result.outputs == ['y']
    assert result.input_widths == (8,)


def test_construction_failure_remains_an_explicit_unverified_boundary(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConstructionUnavailable('unrepeatable reset')

    monkeypatch.setattr(lower_prepare, 'construction_facts', unavailable)
    with pytest.raises(LoweringError, match='unrepeatable reset') as error:
        prepare(construction=None)
    assert error.value.code == 'state-sharing-unverified'


def test_preparation_refuses_untyped_construction_evidence():
    with pytest.raises(TypeError, match='exact ConstructionFacts'):
        prepare(construction={})


def test_preparation_handoff_keeps_one_budget_and_phase_order(monkeypatch):
    from behave.sim.behavior import lower_state
    from behave.sim.behavior.construction import construction_facts
    from behave.sim.symbolic.ranges import Range

    source = '''class Model:
    LUT = (3, 5)
    def reset(self):
        self.counter = 0
        self.width = 3
    def process(self, x):
        self.counter = (self.counter + x) & 3
        return {'y': self.counter + self.width + self.LUT[x & 1]}
'''
    budget = SourceValueBudget()
    facts = construction_facts(source)
    calls = []
    for module, name in ((lower_prepare, 'prepare_source'),
                         (lower_state, 'declare_state'),
                         (lower_prepare, 'plan_representation')):
        original = getattr(module, name)

        def observe(*args, _original=original, _name=name, **kwargs):
            assert kwargs['source_value_budget'] is budget
            if _name == 'declare_state':
                assert args[3]._externally_dynamic_state == frozenset()
            if _name == 'plan_representation':
                assert args[0].resolver._externally_dynamic_state == frozenset({'width'})
            result = _original(*args, **kwargs)
            calls.append((_name, result))
            return result

        monkeypatch.setattr(module, name, observe)
    handoff = lower_prepare.prepare_lowering(
        source, 'model.py', 'process', outputs=['y'], input_widths=[3],
        state_ranges={'width': Range(2, 5)}, read_initial=None, signature=None,
        value_types=None, dynamic_state=('width',), bound=2,
        boundary_initial=None, construction=facts, _analysis_cache=None,
        cls='Model', operation_goals=(), source_rules=EMPTY_SOURCE_RULES,
        source_value_budget=budget)
    assert [name for name, _ in calls] == ['prepare_source', 'declare_state', 'plan_representation']
    assert handoff.source is calls[0][1]
    assert handoff.declarations is calls[1][1]
    assert handoff.representation is calls[2][1]
    assert handoff.source.construction is facts
    assert {'x', 'y', 'counter', 'width'} <= handoff.reserved_names
    assert handoff.static_fields['LUT'] == (3, 5)
    assert 'width' not in handoff.fixed
    with pytest.raises(TypeError):
        handoff.fixed['width'] = 9
    with pytest.raises(TypeError):
        handoff.static_fields['LUT'] = (9, 9)


def test_ir_emitter_consumes_the_completed_preparation(monkeypatch):
    from behave.sim.behavior import lowering
    from behave.sim.behavior.lower_graph import GraphLowerer

    calls = []
    prepare_lowering = lower_prepare.prepare_lowering

    def prepare_once(*args, **kwargs):
        handoff = prepare_lowering(*args, **kwargs)
        calls.append((handoff, kwargs['source_value_budget']))
        return handoff

    def emitter(sites, primitives, functions, constants, env, arrays, *args, **kwargs):
        assert len(calls) == 1
        handoff, budget = calls[0]
        assert sites is handoff.source.goal_plan.coverage_sites
        assert constants is handoff.source.constants
        assert env is handoff.representation.env
        assert arrays == dict(handoff.representation.model_arrays)
        assert kwargs['fixed'] is handoff.fixed
        assert kwargs['reserved_names'] is handoff.reserved_names
        assert kwargs['sequence_plan'] is handoff.representation.sequence_plan
        assert kwargs['source_value_budget'] is budget
        return GraphLowerer(sites, primitives, functions, constants, env, arrays, *args, **kwargs)

    monkeypatch.setattr(lower_prepare, 'prepare_lowering', prepare_once)
    proc = lowering.lower_procedure(
        SOURCE, input_widths=[3], construction=ConstructionFacts((), (), (), ()),
        lowerer=emitter)
    assert len(calls) == 1
    assert proc.outputs == ['y']

"""Adversarial controls for horizon-local state and short-circuit facts."""

import pickle

import pytest

from behave.sim.behavior import audit
from behave.sim.symbolic import interp
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.symbolic.state_projection import ABSENT_STATE, StateAbsentBinding


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_absence_projection_survives_serialization_and_detects_a_native_extra_field(lowerer):
    source = '''class M:
    def reset(self): self.phase = 0
    def process(self, a):
        if self.phase:
            self.x = a
        self.phase = 1
        return {"y": a}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=1, lowerer=lowerer)
    binding = proc.contract.projection('x')
    assert isinstance(binding, StateAbsentBinding)
    assert binding.solver_variables == ()
    assert binding.reconstruct(proc.initial) is ABSENT_STATE
    contract = pickle.loads(pickle.dumps(proc.contract))
    assert contract == proc.contract
    assert contract.projection('x').reconstruct({}) is ABSENT_STATE
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    model = namespace['M']
    assert audit.model_audit(proc, model, [[{'a': 1}]]) == []

    class IncorrectNative(model):
        def process(self, a):
            output = super().process(a)
            self.x = 0
            return output

    assert audit.model_audit(proc, IncorrectNative, [[{'a': 1}]])
    deeper = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                     bound=2, lowerer=lowerer)
    assert not isinstance(deeper.contract.projection('x'), StateAbsentBinding)
    assert audit.model_audit(deeper, model, [[{'a': 1}, {'a': 3}]]) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', [
    'a or 7', 'a and 7', 'self.set(a) and self.n',
    'self.set(a) or self.n', 'self.n and self.set(0)',
    '(a == 0) and self.set(a + 2)', '(a != 0) or self.set(a + 2)',
])
def test_short_circuit_retains_operand_value_type_and_effects(lowerer, expression):
    source = f'''class M:
    def reset(self): self.n = 3
    def set(self, a):
        self.n = a
        return a
    def process(self, a):
        value = {expression}
        return {{"y": int(value) + self.n}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': a}, {'a': b}] for a in range(4) for b in range(4)]
    assert audit.model_audit(proc, namespace['M'], traces) == []
    for a in range(4):
        instance = namespace['M']()
        instance.reset()
        _state, output, _hits, error = interp.run_step(proc, {'a': a})
        assert not error
        assert output.transactions == [instance.process(a)]

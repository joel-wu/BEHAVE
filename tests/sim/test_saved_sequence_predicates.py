"""Saved structural tests carry the state at evaluation, not a live retest."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


def stack_model(predicate='bool(a & 1) and len(self.stack) != 0',
                guard='take', between=''):
    return f'''class M:
    def reset(self): self.stack = []; self.data_out = 0
    def process(self, a):
        take = {predicate}
{between}        if {guard}:
            self.data_out = self.stack[-1]
            self.stack.pop()
        if a & 2: self.stack.append(a)
        return {{"y": self.data_out}}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('predicate,guard', [
    ('bool(a & 1) and len(self.stack) != 0', 'take'),
    ('len(self.stack) > 0 and bool(a & 1)', 'take'),
    ('0 < len(self.stack) and a & 1 == 1', 'take'),
    ('len(self.stack) == 0 or not bool(a & 1)', 'not take'),
    ('not (len(self.stack) == 0) and bool(a & 1)', 'take'),
    ('bool(self.stack) and bool(a & 1)', 'take'),
    ('not self.stack or not bool(a & 1)', 'not take'),
    ('bool(self.stack)', 'take'),
    ('not self.stack', 'not take'),
])
def test_saved_nonempty_guard_preserves_all_four_call_traces(lowerer, predicate, guard):
    agrees(stack_model(predicate, guard), lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('between,guard', [
    ('        saved = take\n        take = False\n', 'saved'),
    ('        self.stack.append(a + 4)\n', 'take'),
    ('        self.stack = [a + 4]\n', 'take'),
])
def test_saved_value_survives_selector_or_container_changes(lowerer, between, guard):
    agrees(stack_model(between=between, guard=guard), lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_saved_true_does_not_prove_a_later_cleared_stack_is_nonempty(lowerer):
    source = stack_model(between='        self.stack = []\n')
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    native = namespace['M']()
    native.reset()
    native.process(2)
    with pytest.raises(IndexError):
        native.process(1)
    # A capability refusal is still valid until this failure has an explicit
    # IR carrier. If admitted, every native failure and hit must be retained.
    try:
        agrees(source, lowerer)
    except LoweringError:
        pass


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_bool_retains_nonboolean_short_circuit_operand(lowerer):
    source = '''def bool(value): return value
class M:
    def reset(self): self.stack = []
    def process(self, a):
        take = len(self.stack) != 0 and bool(a)
        self.stack.append(a)
        return {"y": int(take)}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_saved_guard_with_effects_does_not_use_post_effect_state(lowerer):
    source = '''class M:
    def reset(self): self.stack = []; self.calls = 0
    def clear(self):
        self.stack = []
        self.calls += 1
        return False
    def process(self, a):
        self.stack = [a]
        take = len(self.stack) == 0 or self.clear()
        return {"y": int(take) + self.calls}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('guard', ['len(self.stack) != 2', 'not (2 == len(self.stack))'])
def test_excluding_length_upper_endpoint_bounds_append(lowerer, guard):
    source = f'''class M:
    def reset(self): self.stack = []
    def process(self, a):
        room = {guard}
        if room: self.stack.append(a)
        return {{"y": len(self.stack)}}
'''
    proc = agrees(source, lowerer)
    from behave.sim.symbolic.typecheck import Array
    assert any(proc.types[name] == Array(2) for name in proc.state)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_length_disequality_does_not_remove_an_interior_interval(lowerer):
    source = '''class M:
    def reset(self): self.stack = []
    def process(self, a):
        different = len(self.stack) != 1
        self.stack = [a] * a
        return {"y": int(different)}
'''
    agrees(source, lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('between', ['', '        self.stack.append(a + 4)\n'])
def test_saved_length_query_witnesses_have_native_state_and_hits(between):
    from behave.sim.behavior.instance import BehaviorInstance

    source = stack_model(between=between)
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()

"""Boolean reductions retain their type until a Python numeric-use boundary."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', [
    'any(x > 0 for x in (a, 1)) or a == 0',
    'all(x > 0 for x in (a, 1)) and a == 0',
    'any(x > 0 for x in (a, 1)) + 1',
    '1 + all(x > 0 for x in range(a))',
    'any(x > 0 for x in range(a)) | (a == 1)',
    'all(x > 0 for x in range(a)) ^ (a == 1)',
    'any(x > 0 for x in range(a)) if a & 1 else a == 2',
    'sum(x > 0 for x in range(a))',
    'sum(any(x > 0 for x in (a, 1)) for _ in range(a))',
])
def test_reduction_expression_preserves_native_values_and_hits(lowerer, expression):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        value = {expression}
        return {{"y": int(value)}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['any', 'all'])
def test_saved_and_returned_reduction_remains_boolean(lowerer, name):
    agrees(f'''class M:
    def reset(self): self.saved = False; self.calls = 0
    def tick(self, value):
        self.calls += 1
        return value
    def helper(self, a):
        return {name}(self.tick(x) > 0 for x in range(a))
    def process(self, a):
        previous = self.saved
        self.saved = self.helper(a)
        return {{"y": int(previous) + 2 * int(self.saved) + 4 * self.calls}}
''', lowerer, bound=3)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['any', 'all'])
@pytest.mark.parametrize('value', ['5', 'True', 'False'])
@pytest.mark.parametrize('operator', ['&', '|', '^'])
def test_shadowed_reduction_is_not_classified_as_builtin_boolean(
        lowerer, name, value, operator):
    agrees(f'''def {name}(xs): return {value}
class M:
    def reset(self): self.saved = {value}
    def process(self, a):
        value = {name}([a]) {operator} True
        self.saved = value
        return {{"y": int(value)}}
''', lowerer, bound=2)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name', ['any', 'all'])
def test_reduction_queries_have_native_confirmed_goal_hits(name):
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        selected = {name}(x == 2 for x in range(a)) or a == 0
        if selected: return {{"y": 7}}
        return {{"y": 0}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    verdict = instance.query(instance.goals(), 2)
    assert verdict.reachable and not verdict.undecided
    assert set(instance.goals()) == set(verdict.reachable) | set(verdict.unreachable)
    audit = instance.source_audit(tuple(verdict.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()

"""A smaller query horizon must not require values that cannot yet exist."""

import itertools
import pickle

import pytest

from behave.sim.behavior import audit
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.symbolic.state_projection import ABSENT_STATE, StateAbsentBinding


SOURCES = {
    'empty-enumeration': '''class M:
    def reset(self):
        self.phase = 0
        self.q = []
    def process(self, a):
        result = 0
        if self.phase:
            self.q.append(a)
        else:
            for i, value in enumerate(self.q):
                result += value << i
        self.phase = a & 1
        return {"y": result}
''',
    'short-circuit-option': '''class M:
    def reset(self):
        self.count = 0
    def size(self, a):
        if a == 1: return 1
        if a == 2: return 2
        return None
    def process(self, a):
        size = self.size(a)
        valid = self.count != 0 and size is not None
        result = 0
        if valid and self.count % size != 0:
            result = 1
        self.count = a
        return {"y": result}
''',
    'delayed-field': '''class M:
    def reset(self):
        self.phase = 0
        self.result = 0
    def process(self, a):
        if self.phase == 0:
            self.phase = 1
        elif self.phase == 1:
            self.x = a + 1
            self.phase = 2
        else:
            self.result = self.x
            self.phase = 0
        return {"y": self.result}
''',
}


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('bound', [1, 2, 4])
@pytest.mark.parametrize('name,source', SOURCES.items())
def test_bounded_histories_keep_outputs_state_and_native_goals(name, source, bound, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=bound, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': value} for value in history]
              for history in itertools.product(range(4), repeat=bound)]
    assert audit.model_audit(proc, namespace['M'], traces) == [], name


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_enumerate_start_alias_is_not_silently_treated_as_empty(lowerer):
    source = '''class M:
    def reset(self): self.q = []
    def start(self, a):
        self.q.append(a)
        return 2
    def process(self, a):
        result = 0
        for i, value in enumerate(self.q, self.start(a)):
            result += i * value
        self.q = []
        return {"y": result}
'''
    # This object-sharing form is not newly supported. It must not slip
    # through empty-iteration pruning and incorrectly return zero.
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=2, lowerer=lowerer)
    assert error.value.code == 'array-assigned-by-reference'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', SOURCES.items())
@pytest.mark.parametrize('bound', [1, 4])
def test_all_shallow_and_deeper_goals_are_queried_and_sat_replayed(name, source, bound):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=bound)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=bound)
    result = instance.query(instance.goals(), bound)
    assert result.reachable and not result.undecided, name
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


def test_absent_projection_keeps_the_field_without_an_invented_payload():
    source = SOURCES['delayed-field']
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    projection = proc.contract.projection('x')
    assert isinstance(projection, StateAbsentBinding)
    assert proc.contract.how('x') == ('state',)
    assert not projection.solver_variables
    assert projection.reconstruct(proc.initial) is ABSENT_STATE
    assert 'x' not in proc.initial
    restored = pickle.loads(pickle.dumps(proc.contract))
    assert restored == proc.contract
    assert restored.projection('x').reconstruct({}) is ABSENT_STATE

    # Absence is compared, not omitted from the audit universe.
    wrong = source.replace('self.phase = 1', 'self.phase = 1; self.x = 0')
    namespace = {}
    exec(compile(wrong, 'behavior.py', 'exec'), namespace)
    differences = audit.model_audit(proc, namespace['M'], [[{'a': 0}]])
    assert any(item.what == 'the state x' for item in differences), differences


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', ['0 and a', '3 or a', 'a and 7', 'a or 7'])
def test_short_circuit_range_retains_integer_operand_values(lowerer, expression):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        value = {expression}
        return {{"y": value}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=1, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    assert audit.model_audit(proc, namespace['M'], [[{'a': a}] for a in range(4)]) == []

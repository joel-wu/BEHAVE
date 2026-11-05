"""Cross-call sharing is harmless only while the admitted step never mutates."""
import itertools
import pytest
from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.lower_graph import GraphLowerer

SOURCE = '''class M:
    def reset(self): self.old = [1, 2]; self.new = [3, 4]; self.phase = 0
    def copy(self): self.old = self.new
    def process(self, a):
        result = self.old[0] + 10 * self.new[0]
        self.copy()
        if a: self.new = [a, a + 1]
        self.phase += 1
        return {'y': result}
'''

@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_read_and_rebind_sharing_over_all_small_histories(lowerer):
    proc = lower_with_state_ranges(SOURCE, input_widths=[2], outputs=['y'], bound=4, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(SOURCE)
    traces = [[{'a': a} for a in values] for values in itertools.product(range(4), repeat=4)]
    for trace in traces:
        native = namespace['M'](); native.reset(); state = dict(proc.initial)
        for inputs in trace:
            hits.clear(); expected = native.process(**inputs)
            state, outputs, observed, error = interp.run_step(proc, inputs, state)
            assert not error and outputs.transactions == [expected]
            assert set(observed) == set(hits)
    plain = {}; exec(compile(SOURCE, 'behavior.py', 'exec'), plain)
    assert audit.model_audit(proc, plain['M'], traces) == []

@pytest.mark.parametrize('write', ['self.new[0] = a', 'self.new.append(a)', 'self.new += [a]'])
@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_a_later_mutation_still_requires_cross_call_alias_freedom(write, lowerer):
    source = SOURCE.replace('self.phase += 1', 'if self.phase >= 2: ' + write + '\n        self.phase += 1')
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4, lowerer=lowerer)

@pytest.mark.isolation
@pytest.mark.solver
def test_readonly_sharing_goals_are_queryable_and_native_confirmed():
    from behave.sim.behavior.instance import BehaviorInstance
    proc = lower_with_state_ranges(SOURCE, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=SOURCE, bound=4)
    result = instance.query(instance.goals(), 4)
    assert not result.undecided
    assert result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'

"""Optional pipeline cells retain presence independently of zero payloads."""
import itertools

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.lower_graph import GraphLowerer

SOURCE = '''class M:
    def reset(self): self.pipe = [None, None, None]
    def process(self, valid, data):
        tail = self.pipe[2]
        out = 0
        present = 0
        if tail is not None:
            out = tail
            present = 1
        shifted = [None, None, None]
        for i in range(2, 0, -1): shifted[i] = self.pipe[i-1]
        if valid: shifted[0] = data
        self.pipe = shifted
        return {'y': out, 'valid': present}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('bound', [1, 5])
def test_optional_pipeline_cells_native_state_and_hits(lowerer, bound):
    proc = lower_with_state_ranges(SOURCE, input_widths=[1, 2],
                                  outputs=['y', 'valid'], bound=bound, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(SOURCE)
    traces = [[{'valid': v, 'data': i % 4} for i, v in enumerate(values)]
              for values in itertools.product(range(2), repeat=bound)]
    for trace in traces:
        native = namespace['M']()
        native.reset()
        state = dict(proc.initial)
        for inputs in trace:
            hits.clear()
            expected = native.process(**inputs)
            state, outputs, observed, error = interp.run_step(proc, inputs, state)
            assert not error
            assert outputs.transactions == [expected]
            assert set(observed) == set(hits)
    plain = {}
    exec(compile(SOURCE, 'behavior.py', 'exec'), plain)
    assert audit.model_audit(proc, plain['M'], traces) == []


@pytest.mark.isolation
@pytest.mark.solver
def test_optional_pipeline_goal_queries_native_confirmed():
    from behave.sim.behavior.instance import BehaviorInstance
    proc = lower_with_state_ranges(SOURCE, input_widths=[1, 2],
                                  outputs=['y', 'valid'], bound=5)
    instance = BehaviorInstance(proc, proc.state_ranges, source=SOURCE, bound=5)
    result = instance.query(instance.goals(), 5)
    assert not result.undecided
    assert result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_optional_sequence_iteration_presence_and_negative_index(lowerer):
    source = '''class M:
    def reset(self): self.cells = [None, 0, 5]
    def process(self, a):
        result = 0
        self.cells[-1] = a
        for item in self.cells:
            if item is not None: result += item + 1
        if self.cells == [None, 0, a]: result += len(self.cells)
        return {'y': result}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for a in range(4):
        native = namespace['M']()
        native.reset()
        hits.clear()
        expected = native.process(a)
        _, outputs, observed, error = interp.run_step(proc, {'a': a}, dict(proc.initial))
        assert not error
        assert outputs.transactions == [expected]
        assert set(observed) == set(hits)

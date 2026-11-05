"""Persistent record lists use the same carrier and events as local batches."""

import itertools

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_values import SCALAR, ValueContract
from behave.sim.symbolic.state_projection import SourceProductBinding, StateProjectionError
from behave.sim.symbolic.uniform_array import UniformArray

CASES = [
    ('replace', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        self.rows = [{"y": i + a} for i in range(a)]
        return self.rows
'''),
    ('across-calls', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        if a: self.rows = [{"y": i + a} for i in range(a)]
        return self.rows
'''),
    ('snapshot-before-rebind', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        if a: self.rows = self.rows + [{"y": a}]
        return self.rows
'''),
    ('clear', '''class M:
    def reset(self): self.rows = [{"y": 5}]
    def process(self, a):
        if a == 0: self.rows = []
        elif a == 1: self.rows = [{"y": 7}]
        return self.rows
'''),
    ('helper', '''class M:
    def reset(self): self.rows = []
    def put(self, a): self.rows = [{"y": a}, {"y": a + 1}]
    def get(self): return self.rows
    def process(self, a):
        if a: self.put(a)
        return self.get()
'''),
    ('iteration', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        result = 0
        for row in self.rows: result += row["y"]
        self.rows = [{"y": i} for i in range(a)]
        return {"y": result}
'''),
    ('subscript', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        result = 0
        if len(self.rows): result = self.rows[-1]["y"]
        self.rows = [{"y": a} for i in range(a)]
        return {"y": result}
'''),
    ('local-to-state', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        rows = []
        for i in range(a): rows.append({"y": i + a})
        self.rows = rows
        return self.rows
'''),
    ('append-state', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        if a: self.rows.append({"y": a})
        return self.rows
'''),
    ('append-length-branch', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        self.rows = []
        self.rows.append({"y": a})
        if len(self.rows) == 1: return {"y": self.rows[0]["y"]}
        return {"y": 7}
'''),
    ('nested-field', '''class M:
    def reset(self): self.rows = []
    def process(self, a):
        self.rows = [{"pair": (i, a), "length": i + 1} for i in range(a)]
        if len(self.rows):
            return {"y": self.rows[-1]["pair"][1] + self.rows[-1]["length"]}
        return {"y": 0}
'''),
    ('capture-before-helper-rebind', '''class M:
    def reset(self): self.rows = []
    def replace(self):
        self.rows = [{"y": 7}]
        return 0
    def process(self, a):
        self.rows = [{"y": a}]
        value = self.rows[self.replace()]["y"]
        return {"y": value}
'''),
]


def _histories():
    return [[{'a': value} for value in history]
            for history in itertools.product(range(4), repeat=2)]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_state_record_list_values_and_history(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102 -- fixed fixtures
    assert audit.model_audit(proc, namespace['M'], _histories()) == [], name
    assert isinstance(proc.contract.projection('rows'), SourceProductBinding)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_state_record_list_goals_queries_and_native_replay(name, source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    expected = set()
    for history in _histories():
        native = namespace['M']()
        native.reset()
        state = dict(proc.initial)
        for inputs in history:
            hits.clear()
            native.process(**inputs)
            expected.update(hits)
            state, _, ir_hits, error = interp.run_step(proc, inputs, state)
            assert not error
            assert set(ir_hits) == set(hits), (name, history)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    assert set(result.reachable) == expected
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


def test_record_column_projection_preserves_logical_count_and_tuple_leaves():
    record = ValueContract.record({'flags': ValueContract.sequence('tuple', 2, 2, SCALAR),
                                   'length': SCALAR})
    contract = ValueContract.sequence('list', 0, 3, record)
    binding = SourceProductBinding('rows', contract, (
        (('length',), 'count'), (('elements', 'flags'), 'flags'),
        (('elements', 'length'), 'lengths')))
    source = [{'length': 9, 'flags': (False, True)}]
    state = binding.solver_values(source)
    state['flags'] = UniformArray.uniform(3, [False, False]).store(0, [False, True])
    assert binding.equivalent(binding.reconstruct(state), source)
    assert binding.reconstruct({**state, 'count': 0}) == []
    assert type(binding.reconstruct(state)[0]['flags']) is tuple
    with pytest.raises(StateProjectionError, match='outside its contract'):
        binding.reconstruct({**state, 'count': 4})
    with pytest.raises(StateProjectionError, match='wrong shape'):
        binding.reconstruct({**state, 'flags': [[False, True]]})


@pytest.mark.parametrize('body, representable', [
    ('self.rows = [row, row]', False),
    ('self.rows = [row]\n        self.other = [row]', True),
    ('self.rows = [row]\n        if a: self.rows = self.rows + self.rows', False),
])
def test_readonly_record_list_sharing_preserves_values(body, representable):
    source = f'''class M:
    def reset(self): self.rows = []; self.other = []
    def process(self, a):
        row = {{"y": a}}
        {body}
        return self.rows
'''
    if not representable:
        # The native state snapshot represents each field as a tree. This
        # boundary is distinct from cross-field read-only sharing.
        with pytest.raises(LoweringError) as error:
            lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
        assert error.value.code == 'array-assigned-by-reference'
        return
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    namespace, _, hits = instrumented.load_source(source)
    for history in _histories():
        native = namespace['M']()
        native.reset()
        state = dict(proc.initial)
        for inputs in history:
            hits.clear()
            expected = native.process(**inputs)
            state, outputs, observed, error = interp.run_step(proc, inputs, state)
            assert not error
            assert outputs.transactions == expected
            assert set(observed) == set(hits)
    plain = {}
    exec(compile(source, 'behavior.py', 'exec'), plain)
    assert audit.model_audit(proc, plain['M'], _histories()) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_queries_residual_record_state_goals(tmp_path, wrong):
    from .test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.rows = []
    def process(self, a, b):
        self.rows = [{"y": i + b} for i in range(a & 3)]
        if len(self.rows) == 3: return {"y": 7 + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'7 + (b & 1)', b'8 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

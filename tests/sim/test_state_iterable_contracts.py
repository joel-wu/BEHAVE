"""Finite iteration facts must survive empty state and helper arguments."""

import itertools

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

CASES = [
    ('class-table-helper', '''class M:
    TABLE = [3, 1, 2]
    def reset(self): self.value = 0
    def fold(self, x, table):
        y = 0
        for p in table:
            y = (y << 1) | ((x >> p) & 1)
        return y
    def process(self, a):
        self.value = self.fold(a, self.TABLE)
        return {"y": self.value}
'''),
    ('nested-class-table-helper', '''class M:
    TABLE = [[0, 3], [2, 1]]
    def reset(self): self.value = 0
    def choose(self, table, a):
        return table[a & 1][a >> 1]
    def process(self, a):
        self.value = self.choose(self.TABLE, a)
        return {"y": self.value}
'''),
    *[(f'empty-state-{form}', f'''class M:
    def reset(self):
        self.values = []
        self.value = 9
    def process(self, a):
        y = a
        for i, value in {expression}:
            y += i + value
        else:
            y += 1
        self.value = y
        if a & 1:
            self.values = [a, a + 1]
        else:
            self.values = []
        return {{"y": self.value}}
''') for form, expression in (
        ('enumerate', 'enumerate(self.values)'),
        ('reversed-enumerate', 'enumerate(reversed(self.values), 2)'),
        ('zip', 'zip(self.values, self.values)'),
    )],
    ('empty-helper-enumerate', '''class M:
    def reset(self): self.values = []; self.value = 0
    def fold(self, values):
        y = 0
        for i, value in enumerate(values):
            y += (i + 1) * value
        return y
    def process(self, a):
        self.value = self.fold(self.values)
        if a & 1:
            self.values = [a]
        else:
            self.values = []
        return {"y": self.value}
'''),
    ('empty-start-effect', '''class M:
    def reset(self): self.values = []; self.calls = 0
    def start(self):
        self.calls += 1
        return self.calls
    def process(self, a):
        y = a
        for i, value in enumerate(self.values, self.start()):
            y += i + value
        return {"y": y + self.calls}
'''),
    ('saved-empty-stack-guard', '''class M:
    def reset(self):
        self.values = []
        self.count = 0
        self.value = 0
    def process(self, a):
        n = self.count
        ready = (a == 0) and (n > 0)
        if ready:
            self.value = self.values[n - 1]
            self.values = self.values[:-1]
            self.count = n - 1
        elif a > 0 and n < 2:
            self.values.append(a)
            self.count = n + 1
        return {"y": self.value}
'''),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_state_iteration_preserves_native_outputs_and_state(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=3, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102 -- fixed fixtures
    traces = [[{'a': a} for a in values]
              for values in itertools.product(range(4), repeat=3)]
    assert model_audit(proc, namespace['M'], traces) == [], name


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_state_iteration_queries_agree_with_all_native_hits(name, source):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    namespace, _, hits = instrumented.load_source(
        source, goal_plan=plan_source(source_model(source), source))
    expected = set()
    for values in itertools.product(range(4), repeat=3):
        native = namespace['M']()
        native.reset()
        for a in values:
            hits.clear()
            native.process(a)
            expected.update(hits)
    result = instance.query(instance.goals(), 3)
    assert not result.undecided
    assert set(result.reachable) == expected, name
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    if result.reachable:
        assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_uses_helper_table_after_baseline_miss(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    TABLE = [5, 2, 0]
    def reset(self): pass
    def fold(self, a, table):
        y = 0
        for p in table:
            y = y * 2 + ((a >> p) & 1)
        return y
    def process(self, a, b):
        code = self.fold(a, self.TABLE)
        if code == 7:
            return {"y": code + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'code + (b & 1)', b'code + 1 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

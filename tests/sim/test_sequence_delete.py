"""Indexed removal must retain Python order, state, and safety goal identity."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source


def local(body, helpers=''):
    return ('class M:\n    def reset(self): self.calls = 0\n' + helpers
            + '    def process(self, a):\n' + body)


CASES = [
    ('signed-index', local('''        values = [11, 22, 33]
        del values[a - 2]
        return {"y": values[0] + 3 * values[1] + len(values)}
''')),
    ('out-of-range', local('''        values = [11]
        del values[a - 1]
        return {"y": len(values)}
''')),
    ('ordered-targets', local('''        values = [11, 22, 33, 44]
        del values[a & 1], values[-1]
        return {"y": values[0] + 3 * values[1]}
''')),
    ('nested-lengths', local('''        values = [self.row(a), self.row(a ^ 1), self.row(a ^ 2)]
        del values[a & 1]
        return {"y": len(values[0]) + 4 * len(values[1])}
''', '''    def row(self, a):
        return [11 + i for i in range(a)]
''')),
    ('index-effect', local('''        values = [11, 22, 33]
        del values[self.index(a)]
        return {"y": values[0] + len(values) + self.calls}
''', '''    def index(self, a):
        self.calls += 1
        return a & 1
''')),
    *[(f'nested-index-snapshot-{operation}', local(f'''        values = [self.row(a), self.row(a ^ 1), self.row(a ^ 2)]
        {statement}
        return {{"y": len(values[0]) + 4 * len(values[1])}}
''', '''    def row(self, a):
        out = [a & 1]
        for i in range(a): out.append(11 + i)
        return out
''')) for operation, statement in (
        ('delete', 'del values[values[0][0]]'),
        ('pop', 'values.pop(values[0][0])'),
    )],
    ('mutable-iteration', local('''        values = [11, 22, 33]
        total = 0
        for value in values:
            total += value
            if a & 1:
                del values[0]
        return {"y": total + len(values)}
''')),
    ('state-stack', '''class M:
    def reset(self): self.values = []; self.count = 0; self.value = 0
    def process(self, a):
        n = self.count
        ready = (a == 0) and (n > 0)
        if ready:
            self.value = self.values[n - 1]
            del self.values[n - 1]
            self.count = n - 1
        elif a > 0 and n < 2:
            self.values.append(a)
            self.count = n + 1
        return {"y": self.value}
'''),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_delete_preserves_python(name, source, lowerer):
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
def test_delete_queries_match_native_hits(name, source):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    expected = set()
    for values in itertools.product(range(4), repeat=3):
        native = namespace['M']()
        native.reset()
        for a in values:
            hits.clear()
            try:
                native.process(a)
            except IndexError as error:
                goal = instrumented.attribute_safety_failure(error, plan.safety_sites)
                assert goal is not None
                expected.update([*hits, goal])
                break
            expected.update(hits)
    result = instance.query(instance.goals(), 3)
    assert not result.undecided
    assert set(result.reachable) == expected, name
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.parametrize('body', [
    ('        values = [1, 2]\n        other = values\n        del values[0]\n'
     '        return {"y": len(other)}\n'),
    ('        values = [[1], [2]]\n        other = values\n        del values[0]\n'
     '        return {"y": other[0][0]}\n'),
])
def test_delete_does_not_silently_copy_mutable_aliases(body):
    with pytest.raises(LoweringError, match='alias|reference|same array'):
        lower_with_state_ranges(local(body), input_widths=[2], outputs=['y'])


def test_delete_is_a_reachable_state_write():
    source = '''class M:
    def reset(self): self.values = [1, 2]
    def remove(self): del self.values[0]
    def process(self, a): self.remove(); return {"y": len(self.values)}
'''
    model = source_model(source)
    assert 'values' in model.reachable_state_writes([model.step])


@pytest.mark.parametrize('operation', ['del self.values[self.index()]',
                                       'self.values.pop(self.index())'])
def test_removal_refuses_a_receiver_rebound_by_its_index(operation):
    source = f'''class M:
    def reset(self): self.values = [11, 22]
    def index(self):
        self.values = [33, 44]
        return 0
    def process(self, a):
        {operation}
        return {{"y": len(self.values)}}
'''
    with pytest.raises(LoweringError, match='receiver|rebind|alias'):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'])


def test_ragged_literal_remains_an_explicit_carrier_boundary():
    source = local('''        values = [[11], [22, 33], [44, 55, 66]]
        del values[a & 1]
        return {"y": len(values[0])}
''')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'])
    assert caught.value.code == 'sequence-element-shape-disagrees'


@pytest.mark.parametrize('operation', ['del values[0]', 'values.pop(0)'])
def test_removal_preserves_shifted_child_aliases(operation):
    source = local(f'''        values = [[11], [22], [33]]
        other = values[1]
        {operation}
        values[0][0] = 99
        return {{"y": other[0]}}
''')
    with pytest.raises(LoweringError, match='alias|reference|same array'):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'])


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_after_indexed_removal(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        values = [a, 0, 7]
        del values[1]
        if values[0] == values[1]:
            return {"y": values[0] + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'values[0] + (b & 1)',
                               b'values[0] + 1 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

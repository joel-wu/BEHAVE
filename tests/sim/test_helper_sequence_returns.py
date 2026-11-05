"""Sequence operators must not acquire a numeric helper-return placeholder."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source


def source(expression, *, binding='self.row(a)', setup='',
           output='len(values) + sum(values)'):
    return f'''class M:
    def reset(self): pass
    def row(self, a):
        return {expression}
    def process(self, a):
        values = {binding}
{setup}        return {{"y": {output}}}
'''


CASES = [
    ('dynamic-concat', source('[a & 1] + [11 + i for i in range(a)]')),
    ('fixed-concat', source('[a, a + 1] + [a + 2]')),
    ('empty-left-concat', source('[] + [i for i in range(a)]')),
    ('empty-right-concat', source('[i for i in range(a)] + []')),
    ('dynamic-repeat', source('[a + 1] * a')),
    ('fixed-repeat', source('[a + 1] * 2')),
    ('tuple-concat', source('(a, a + 1) + (a + 2,)')),
    ('tuple-repeat', source('(a, a + 1) * 2')),
    ('numeric-add', source('a + 3', output='values')),
    ('numeric-multiply', source('a * 3', output='values')),
    ('boolean-and', source('bool(a) & bool(a & 1)', output='int(values)')),
    *[(f'nested-{operation}', source(
        '[a & 1] + [11 + i for i in range(a)]',
        binding='[self.row(a), self.row(a ^ 1), self.row(a ^ 2)]',
        setup=f'        {statement}\n',
        output='len(values[0]) + 4 * len(values[1])'))
      for operation, statement in (
          ('read', 'index = values[0][0]'),
          ('delete', 'del values[values[0][0]]'),
          ('pop', 'values.pop(values[0][0])'),
      )],
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,program', CASES, ids=[name for name, _ in CASES])
def test_sequence_return_matches_python(name, program, lowerer):
    proc = lower_with_state_ranges(program, input_widths=[2], outputs=['y'],
                                   bound=3, lowerer=lowerer)
    namespace = {}
    exec(compile(program, 'behavior.py', 'exec'), namespace)  # noqa: S102
    traces = [[{'a': a} for a in values]
              for values in itertools.product(range(4), repeat=3)]
    assert model_audit(proc, namespace['M'], traces) == [], name


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,program', CASES, ids=[name for name, _ in CASES])
def test_sequence_return_query_hits_match_native(name, program):
    proc = lower_with_state_ranges(program, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=program, bound=3)
    plan = plan_source(source_model(program), program)
    namespace, _, hits = instrumented.load_source(program, goal_plan=plan)
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
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_through_sequence_helper(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    reference = b'''class M:
    def reset(self): pass
    def row(self, a): return [a] + [7]
    def process(self, a, b):
        values = self.row(a)
        if values[0] == values[1]:
            return {"y": values[0] + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = reference.replace(b'values[0] + (b & 1)',
                                  b'values[0] + 1 + (b & 1)') if wrong else reference
    check_public_search_loop(tmp_path, reference, candidate, wrong=wrong)

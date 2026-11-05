"""Construction-only label tables use source values, never numeric tag meanings."""

import ast
import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_rom import evaluate_frozen, freeze_value


def source(table, expression, *, setup='', helpers='', output='int(label == "start")'):
    return f'''class M:
    def __init__(self):
        self.labels = {table}
        self.reset()
    def reset(self): self.index = 0; self.calls = 0
{helpers}    def process(self, a):
{setup}        label = {expression}
        return {{"y": {output}}}
'''


CASES = [
    ('list', source('["start", "data", "stop"]', 'self.labels[a % 3]')),
    ('constructed', source('["start"] + ["data"] * 2 + ["stop"]',
                           'self.labels[a]')),
    ('tuple', source('("start", "stop")', 'self.labels[a & 1]')),
    ('negative-and-bad-index', source('["start", "stop"]', 'self.labels[a - 1]')),
    ('nested', source('[["start", "data"], ["stop", "start"]]',
                      'self.labels[a >> 1][a & 1]')),
    ('numeric-equality', source('[0, "start", True, "stop"]', 'self.labels[a]',
                                output='int(label == "start") + 2 * int(label == 1)')),
    ('optional-label', source('[None, "start", 0, "stop"]', 'self.labels[a]',
                              output='int(label is None) + 2 * int(label == "start")')),
    ('helper-return', source('["start", "stop"]', 'self.label(a)',
                             helpers='    def label(self, a): return self.labels[a & 1]\n')),
    ('key-effects', source('["start", "stop"]', 'self.labels[self.key(a)]',
                          helpers=('    def key(self, a):\n'
                                   '        self.calls += 1\n'
                                   '        return a & 1\n'),
                          output='int(label == "start") + 4 * self.calls')),
    ('state-index', source('["start", "data", "stop"]', 'self.labels[self.index]',
                          setup='        self.index = (self.index + a) % 3\n')),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,program', CASES, ids=[name for name, _ in CASES])
def test_readonly_labels_match_python(name, program, lowerer):
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
def test_readonly_label_queries_match_native_hits(name, program):
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


@pytest.mark.parametrize('write,helpers', [
    ('self.labels[0] = "changed"', ''),
    ('other = self.labels\n        other[0] = "changed"', ''),
    ('self.replace(self.labels)', '    def replace(self, table): table[0] = "changed"\n'),
    ('self.labels = ["changed", "stop"]', ''),
])
def test_written_label_tables_are_not_frozen(write, helpers):
    program = source('["start", "stop"]', 'self.labels[a & 1]',
                     setup=f'        {write}\n', helpers=helpers)
    with pytest.raises(LoweringError):
        lower_with_state_ranges(program, input_widths=[2], outputs=['y'], bound=3)


def test_externally_dynamic_labels_are_not_frozen():
    with pytest.raises(LoweringError):
        lower_with_state_ranges(CASES[0][1], input_widths=[2], outputs=['y'],
                                dynamic_state=('labels',), bound=3)


@pytest.mark.parametrize('expression', [
    'self.labels == ["start", "stop"]',
    'self.labels == ("start", "stop")',
    'self.labels is self.labels',
])
def test_frozen_lookup_does_not_erase_observed_collection_carrier(expression):
    program = source('["start", "stop"]', 'self.labels[a & 1]',
                     output=f'int({expression})')
    with pytest.raises(LoweringError) as refused:
        lower_with_state_ranges(program, input_widths=[2], outputs=['y'], bound=3)
    assert refused.value.code in {'static-collection-equality-observed',
                                 'static-collection-identity-observed'}


def test_frozen_none_retains_its_value_and_source_carrier():
    scalar = freeze_value(None)
    assert scalar is not None and scalar.kind == 'none'
    assert scalar.to_plain() is None
    value = freeze_value([None, 'start', 0])
    assert value.to_plain() == (None, 'start', 0)
    assert value.to_source_value() == [None, 'start', 0]
    assert value.fits_expansion(4)
    assert not value.fits_expansion(3)


@pytest.mark.parametrize('expression,expected', [
    ('[None, "start"]', [None, 'start']),
    ('1 if None else 2', 2),
    ('None and 3', None),
])
def test_explicit_none_is_not_an_unknown_constant(expression, expected):
    value = evaluate_frozen(ast.parse(expression, mode='eval').body,
                            lambda _node: None)
    assert value is not None
    assert value.to_source_value() == expected


@pytest.mark.parametrize('expression', ['unknown', '[None, unknown]', 'unknown()'])
def test_unresolved_bindings_do_not_become_frozen_none(expression):
    assert evaluate_frozen(ast.parse(expression, mode='eval').body,
                           lambda _node: None) is None


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_through_readonly_labels(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    reference = b'''class M:
    def __init__(self): self.labels = ["miss"] * 7 + ["hit"]
    def reset(self): pass
    def process(self, a, b):
        label = self.labels[a & 7]
        if label == "hit":
            return {"y": a + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = reference.replace(b'a + (b & 1)',
                                  b'a + 1 + (b & 1)') if wrong else reference
    check_public_search_loop(tmp_path, reference, candidate, wrong=wrong)

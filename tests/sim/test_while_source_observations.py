"""Termination bookkeeping is not an executed Python source occurrence."""

import itertools

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES, SourceGoalRules

from .test_operation_rules import _rule
from .test_source_read_goals import _read

CASES = [
    (name, f'''class M:
    def reset(self): self.count = 0
    def process(self, a):
        self.count = a
        while {condition}:
            self.count += 1
        return {{"y": self.count}}
''') for name, condition in (
        ('state', 'self.count != 4'),
        ('reversed', '4 > self.count'),
        ('affine', 'self.count + 1 <= 4'),
    )
]
CASES += [
    ('short-circuit', '''class M:
    def reset(self): self.count = 0
    def process(self, a):
        if a & 2: self.count = 0
        i = 0
        while i < (a & 1) and self.count < 4:
            self.count += 1
            i += 1
        return {"y": i}
'''),
    ('cutoff-before-read', '''class M:
    def reset(self): self.count = 0
    def process(self, a):
        if a & 2: self.count = 0
        while True:
            if a == 0: break
            if self.count >= 4: break
            self.count += 1
        return {"y": a}
'''),
    ('break-continue', '''class M:
    def reset(self): self.count = 0
    def process(self, a):
        self.count = 0
        while self.count < 4:
            self.count += 1
            if self.count == a: break
            if self.count == 2: continue
        return {"y": self.count}
'''),
    ('local', '''class M:
    def reset(self): pass
    def process(self, a):
        count = a
        while count < 4:
            count += 1
        return {"y": count}
'''),
]


def _histories():
    return [[{'a': value} for value in history]
            for history in itertools.product(range(4), repeat=2)]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_while_outputs_state_and_failure_order(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102 -- fixed fixtures
    assert audit.model_audit(proc, namespace['M'], _histories()) == [], name


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('rules', [
    EMPTY_SOURCE_RULES,
    SourceGoalRules(reads=(_read('self.count', 2, 'middle'),
                          _read('self.count', 4, 'exit'),
                          _read('count', 2, 'middle'),
                          _read('count', 4, 'exit'))),
    SourceGoalRules(operations=(_rule('add', 3),)),
], ids=['automatic', 'read-observation', 'operation-observation'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_while_goal_hits_and_queries_match_native(name, source, rules):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=2, source_rules=rules)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2,
                                source_rules=rules)
    goal_plan = plan_source(source_model(source), source, source_rules=rules)
    namespace, _, hits = instrumented.load_source(source, goal_plan=goal_plan)
    expected = set()
    for history in _histories():
        native = namespace['M']()
        native.reset()
        state = dict(proc.initial)
        for inputs in history:
            hits.clear()
            failed = False
            try:
                native.process(**inputs)
            except (AttributeError, ValueError):
                failed = True
            expected.update(hits)
            state, _, ir_hits, error = interp.run_step(proc, inputs, state)
            assert set(ir_hits) == set(hits), (name, history, set(hits) - set(ir_hits),
                                              set(ir_hits) - set(hits))
            assert bool(error) == failed
            if failed:
                break
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    assert set(result.reachable) == expected, name
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_queries_residual_while_goals(tmp_path, wrong):
    from .test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.count = 0
    def process(self, a, b):
        self.count = a & 7
        while self.count < 6:
            self.count += 1
        if self.count == 7:
            return {"y": 7 + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'7 + (b & 1)', b'8 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

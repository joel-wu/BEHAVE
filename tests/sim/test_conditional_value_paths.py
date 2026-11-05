"""A saved conditional value retains its evaluated path, not live source syntax."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source


def model(body, *, reset='self.data = [4, 5, 6, 7]; self.calls = 0', helpers=''):
    return f'''class M:
    def reset(self): {reset}
{helpers}    def process(self, a):
{body}
'''


CASES = [
    ('address-dispatch', model('''        v = self.data[a] if a >= 2 else None
        if a == 0: return {"y": 0}
        elif a == 1: return {"y": 1}
        else: return {"y": v + 1}''')),
    ('negated', model('''        v = None if a < 2 else a
        if a >= 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('nested', model('''        v = (a if a >= 2 else None) if a else None
        if a >= 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('statement-arms', model('''        if a >= 2: v = a
        else: v = None
        if a >= 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('copy', model('''        v = a if a >= 2 else None
        saved = v
        v = None
        if a >= 2: return {"y": saved + 1}
        return {"y": 0}''')),
    ('saved-read-before-write', model('''        v = self.data[a] if a >= 2 else None
        self.data[a] = a
        if a >= 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('chained-names', model('''        v = w = a if a >= 2 else None
        if a >= 2: return {"y": v + w}
        return {"y": 0}''')),
    ('annotated', model('''        v: int = a if a >= 2 else None
        if a >= 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('scalar-arms', model('''        v = a + 1 if a >= 2 else a + 2
        return {"y": v}''')),
    ('rebound-address', model('''        v = a if a >= 2 else None
        a = 3 - a
        if a < 2: return {"y": v + 1}
        return {"y": 0}''')),
    ('effects', model('''        v = self.read(a) if a >= 2 else None
        if a >= 2: return {"y": v + self.calls}
        return {"y": self.calls}''', helpers='''    def read(self, a):
        self.calls += 1
        return self.data[a]
''')),
    ('state-write', model('''        v = a if a >= 2 else None
        if a >= 2: self.calls = v + 1
        return {"y": self.calls}''')),
    ('signed-truth', model('''        a = a - 2
        v = a if a else None
        if a != 0: return {"y": v + 3}
        return {"y": 0}''')),
    ('effectful-test', model('''        v = a if self.ready(a) else None
        if v is not None: return {"y": v + self.calls}
        return {"y": self.calls}''', helpers='''    def ready(self, a):
        self.calls += 1
        return a >= 2
''')),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,program', CASES, ids=[name for name, _ in CASES])
def test_conditional_value_paths_match_native(name, program, lowerer):
    proc = lower_with_state_ranges(program, input_widths=[2], outputs=['y'],
                                   bound=3, lowerer=lowerer)
    namespace = {}
    exec(compile(program, 'behavior.py', 'exec'), namespace)  # noqa: S102
    traces = [[{'a': a} for a in values]
              for values in itertools.product(range(4), repeat=3)]
    assert model_audit(proc, namespace['M'], traces) == [], name


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('mutation', ['a = 3 - a', 'v = None'])
def test_changed_value_does_not_reuse_the_old_presence_proof(lowerer, mutation):
    program = model(f'''        v = a if a >= 2 else None
        {mutation}
        if a >= 2: return {{"y": v + 1}}
        return {{"y": 0}}''')
    with pytest.raises(LoweringError):
        lower_with_state_ranges(program, input_widths=[2], outputs=['y'],
                                bound=3, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('predicate,guard,helpers', [
    ('a == 1', 'a == 1', ''),
    ('self.ready(a)', 'a >= 2', '''    def ready(self, a):
        self.calls += 1
        return a >= 2
'''),
])
def test_unrepresented_correlations_remain_explicit_refusals(
        lowerer, predicate, guard, helpers):
    # These valid source programs need non-convex path facts or a relational
    # helper-return summary. This repair does not provide either mechanism;
    # the planner must not silently invent presence or score them as errors.
    program = model(f'''        v = a if {predicate} else None
        if {guard}: return {{"y": v + 1}}
        return {{"y": 0}}''', helpers=helpers)
    with pytest.raises(LoweringError) as refused:
        lower_with_state_ranges(program, input_widths=[2], outputs=['y'],
                                bound=3, lowerer=lowerer)
    assert refused.value.code in {
        'optional-value-not-unwrapped', 'outputs-disagree-across-returns'}


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,program', CASES, ids=[name for name, _ in CASES])
def test_conditional_value_queries_match_exhaustive_native_hits(name, program):
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
def test_public_search_uses_saved_conditional_read(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    reference = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        v = a if a >= 37 else None
        if a < 37: return {"y": 0}
        return {"y": v + b}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = reference.replace(b'v + b', b'v + b + 1') if wrong else reference
    check_public_search_loop(tmp_path, reference, candidate, wrong=wrong)

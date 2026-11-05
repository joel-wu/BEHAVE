"""Lexically fixed helper aliases retain binding, effects and source goals."""

from itertools import product

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source

STATIC = '''class M:
    def reset(self): self.n = 0
    @staticmethod
    def helper(a):
        if a > 1: return a + 1
        return 0
    def process(self, a):
        f = self.helper
        return {"y": f(a)}
'''
MODULE = '''def helper(a):
    if a > 1: return a + 1
    return 0
class M:
    def reset(self): self.n = 0
    def process(self, a):
        f = helper
        return {"y": f(a)}
'''
NESTED = '''class M:
    def reset(self): self.n = 0
    def process(self, a):
        def helper(x):
            if x > 1: return x + a
            return a
        f = helper
        return {"y": f(a)}
'''
EFFECTS = '''class M:
    def reset(self): self.n = 0
    def bump(self, a):
        self.n = (self.n + 1) & 15
        return self.n + a
    def helper(self, a, *, b=2):
        return a * 32 + b
    def process(self, a):
        f = self.helper
        bump = self.bump
        return {"y": f(self.n, b=bump(a)) + self.n}
'''

SOURCES = [
    STATIC, MODULE, NESTED, EFFECTS,
    STATIC.replace('@staticmethod\n    def helper(a):', 'def helper(self, a):'),
    STATIC.replace('f = self.helper', 'f: object = self.helper'),
    STATIC.replace('f = self.helper', 'f = self.helper\n        g = f')
          .replace('f(a)', 'g(a) + f(a)'),
    STATIC.replace('f = self.helper', 'sum = self.helper').replace('f(a)', 'sum(a)'),
    STATIC.replace('        f = self.helper',
                   '        f = self.helper\n        unused = self.helper'),
    STATIC.replace('        f = self.helper',
                   '        def identity(f): return f\n        f = self.helper')
          .replace('f(a)', 'f(identity(a))'),
    STATIC.replace('f = self.helper', 'f = g = self.helper').replace('f(a)', 'f(a) + g(a)'),
    STATIC.replace('    def process(self, a):',
                   '    def unused(self, a):\n        if a: return 31\n        return 29\n'
                   '    def process(self, a):')
          .replace('f = self.helper', 'f = self.helper\n        unused = self.unused'),
]
SOURCE_NAMES = ['static', 'module', 'nested', 'effects', 'instance', 'annotation',
                'chained', 'builtin-shadow', 'unused-reference', 'child-local',
                'chained-assignment', 'uncalled-helper']


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES, ids=SOURCE_NAMES)
def test_alias_outputs_and_every_step_hit_match_native(lowerer, source):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=3, lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    plain = {}
    exec(compile(source, 'behavior.py', 'exec'), plain)
    for values in product(range(4), repeat=3):
        observed, reference = namespace['M'](), plain['M']()
        observed.reset()
        reference.reset()
        state = None
        for value in values:
            hits.clear()
            expected = reference.process(value)
            assert observed.process(value) == expected
            state, output, actual_hits, error = interp.run_step(proc, {'a': value}, state)
            assert not error
            assert output.transactions == [expected]
            assert actual_hits == set(hits)


UNPROVED = [
    STATIC.replace('f = self.helper', 'f = self.helper\n        f = a'),
    STATIC.replace('f = self.helper', 'if a: f = self.helper'),
    STATIC.replace('f = self.helper', 'value = f(a); f = self.helper'),
    STATIC.replace('f = self.helper', 'f = self.helper\n        del f'),
    STATIC.replace('f = self.helper', 'f = self.helper\n        saved = [f]'),
    STATIC.replace('self.n = 0', 'self.n = 0; self.helper = 3'),
    STATIC.replace('f = self.helper', 'f = self.helper\n        def change():\n'
                   '            nonlocal f\n            f = a\n        change()'),
    STATIC.replace('f = self.helper', 'def use(): return f(a)\n'
                   '        result = use()\n        f = self.helper'),
    MODULE.replace('f = helper', 'f = helper\n        helper = a'),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', UNPROVED, ids=[
    'rebound', 'conditional', 'before-binding', 'deleted', 'escaped',
    'field-shadows-method', 'nonlocal-write', 'closure-before-binding', 'unbound-source'])
def test_unproved_or_escaping_callable_is_not_silently_inlined(lowerer, source):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=3, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_class_helper_alias_retains_existing_concrete_and_exact_capabilities(lowerer):
    source = (STATIC.replace('class M:', 'class M:\n    K = 7')
              .replace('@staticmethod\n    def helper(a):',
                       '@classmethod\n    def helper(klass, a):')
              .replace('return a + 1', 'return a + klass.K'))
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    instance = namespace['M']()
    instance.reset()
    for value in range(4):
        assert instance.process(value) == {'y': value + 7 if value > 1 else 0}
    assert {goal for site in plan.coverage_sites for goal in site.goals} <= set(hits)
    # Capturing a callable must not bypass the exact frontend's pre-existing
    # class-object limitation. Concrete binding is independently supported.
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=3, lowerer=lowerer)
    assert error.value.code == 'class-method-lowering-unsupported'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', [STATIC, MODULE, NESTED, EFFECTS], ids=SOURCE_NAMES[:4])
def test_all_alias_goals_query_and_native_audit(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    instance.bind(3)
    verdict = instance.query(instance.goals(), 3)
    assert verdict.reachable and not verdict.undecided
    assert set(instance.goals()) == set(verdict.reachable) | set(verdict.unreachable)
    audit = instance.source_audit(tuple(verdict.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


def test_nested_body_and_definition_time_have_distinct_lexical_owners():
    import ast

    from behave.sim.behavior.extract import ScopeResolver

    source = MODULE.replace('        f = helper',
                            '        def child(x=helper): return x\n        f = helper')
    model = source_model(source)
    resolver = ScopeResolver(model)
    child = model.step.body[0]
    assert resolver.scope_of(child.args.defaults[0]) is model.step
    read = next(node for node in ast.walk(child.body[0]) if isinstance(node, ast.Name))
    assert resolver.scope_of(read) is child


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('unsafe', [False, True])
def test_uncalled_captured_helper_goals_are_present_but_not_hit(unsafe):
    from behave.sim.behavior.instance import BehaviorInstance

    source = SOURCES[-1]
    if unsafe:
        source = source.replace('return 29', 'return 1 // a')
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    plan = plan_source(source_model(source), source)
    uncalled = {goal for site in plan.coverage_sites if site.owner[0] == 'unused'
                for goal in site.goals}
    assert uncalled
    if unsafe:
        safety = {site.id for site in plan.safety_sites}
        assert safety
        uncalled |= safety
    verdict = instance.query(instance.goals(), 2)
    assert not verdict.undecided
    assert uncalled <= set(verdict.unreachable)
    assert set(instance.goals()) == set(verdict.reachable) | set(verdict.unreachable)
    assert instance.source_audit(tuple(verdict.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_alias_missed_branch(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    @staticmethod
    def helper(a, b):
        if a == 37 and b == 37: return 7
        return 0
    def process(self, a, b):
        f = self.helper
        return {"y": f(a, b)}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'return 7', b'return 6') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

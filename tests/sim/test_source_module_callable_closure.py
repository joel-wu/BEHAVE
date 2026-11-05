"""Native Python callable bodies must belong to the frozen source catalogue."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.source_plan import plan_source


@pytest.mark.parametrize('guard', ["__name__ == '__main__'", 'False'])
def test_conditional_import_does_not_erase_a_possible_module_helper(guard):
    source = f"""def signed(value):
    if value >= 128:
        return value - 256
    return value
class M:
    def process(self, value):
        return {{'y': signed(value)}}
if {guard}:
    from nowhere import signed
"""
    model = source_model(source)
    resolver = ScopeResolver(model)
    # Coverage may declare possible source bodies without certifying which
    # binding an exact lowerer should inline after a conditional assignment.
    assert resolver.resolve_call(model.step, 'signed')[0] == 'opaque'
    plan = plan_source(model, source, resolver=resolver)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    for value in (1, 128):
        hits.clear()
        assert namespace['M']().process(value)['y'] == (value if value < 128 else value - 256)
        assert set(hits) <= set(plan.manifest)
        assert any(goal.startswith('branch:') for goal in hits)


def test_constructed_helper_class_includes_implicitly_called_comparisons():
    source = """class Key:
    def __init__(self, value):
        self.value = value
    def __lt__(self, other):
        if self.value < other.value:
            return True
        return False
    def unused(self):
        return 41
class Unrelated:
    def __lt__(self, other):
        return False
class M:
    def process(self, a, b):
        keys = sorted([Key(a), Key(b)])
        return {'y': keys[0].value}
"""
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    for a, b in ((1, 2), (2, 1), (1, 1)):
        hits.clear()
        assert namespace['M']().process(a, b) == {'y': min(a, b)}
        assert set(hits) <= set(plan.manifest)
    owners = {site.owner for site in plan.coverage_sites}
    assert ('__lt__', 4, 4) in owners
    assert ('__lt__', 11, 4) not in owners


def test_local_shadow_does_not_select_same_named_module_class():
    source = """class Key:
    def __init__(self, value):
        self.value = value
class M:
    def process(self, Key):
        return {'y': Key}
"""
    plan = plan_source(source_model(source), source)
    assert {site.owner[0] for site in plan.coverage_sites} == {'process'}


def test_conditional_module_functions_keep_both_possible_bodies():
    source = """if __name__ == '__main__':
    def choose(value):
        return value + 1
else:
    def choose(value):
        return value + 2
class M:
    def process(self, value):
        return {'y': choose(value)}
"""
    plan = plan_source(source_model(source), source)
    assert {site.owner for site in plan.coverage_sites if site.owner[0] == 'choose'} == {
        ('choose', 2, 4), ('choose', 5, 4)}
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    hits.clear()  # Module import events are outside the transaction epoch.
    assert namespace['M']().process(3) == {'y': 5}
    assert set(hits) <= set(plan.manifest)

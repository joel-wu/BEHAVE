"""Finite truth reductions agree with source state, effects and goal hits."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['all', 'any'])
@pytest.mark.parametrize('items', ['[]', '[a]', '(a, 0, 2)', '[a - 2, a - 1]',
                                 '[bool(a & 1), bool(a & 2)]',
                                 '[i - 1 for i in range(a)]'])
def test_finite_scalar_reductions(lowerer, name, items):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        return {{"y": int({name}({items}))}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['all', 'any'])
def test_building_argument_is_eager_even_when_first_item_decides(lowerer, name):
    agrees(f'''class M:
    def reset(self): self.calls = 0
    def tick(self, a):
        self.calls += 1
        return a
    def process(self, a):
        answer = {name}([self.tick({0 if name == 'all' else 1}), self.tick(a)])
        return {{"y": int(answer) + 4 * self.calls}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_dynamic_sequence_padding_is_never_an_item(lowerer):
    agrees('''class M:
    def reset(self): self.items = []
    def process(self, a):
        self.items.append(a)
        if a & 2: self.items.pop(0)
        return {"y": int(all(self.items)) + 2 * int(any(self.items))}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_reduction_is_an_ordinary_helper(lowerer):
    agrees('''def all(items): return len(items) + 7
class M:
    def reset(self): pass
    def process(self, a): return {"y": all([a, 0])}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['all', 'any'])
@pytest.mark.parametrize('generator', [
    '(self.tick(i) for i in range(a))',
    '(self.tick(i) for i in range(a) if self.tick(i + 1))',
    '(self.tick(i + j) for i in range(a) for j in range(3))',
    '(self.tick(i + j) for i in range(a) if i != 1 for j in range(3) if j < 2)',
])
def test_lazy_reducer_stops_filters_elements_and_nested_iterations(lowerer, name, generator):
    agrees(f'''class M:
    def reset(self): self.calls = 0
    def tick(self, a):
        self.calls += 1
        return a & 1
    def process(self, a):
        answer = {name}({generator})
        return {{"y": int(answer) + 4 * self.calls}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_lazy_unreached_safety_goals_are_not_spurious_failures(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        return {"y": int(any(i == 0 or 1 // (a - i) > 0 for i in range(3)))}
''', lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('lazy', [False, True])
def test_all_catalogue_queries_and_witnesses_have_native_agreement(lazy):
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    def reset(self): self.items = []
    def process(self, a):
        self.items.append(a)
        if all(self.items): return {"y": 7}
        if any(self.items): return {"y": 3}
        return {"y": 0}
'''
    if lazy:
        source = source.replace('all(self.items)', 'all(x > 0 for x in self.items)')
        source = source.replace('any(self.items)', 'any(x > 0 for x in self.items)')
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    verdict = instance.query(instance.goals(), 4)
    assert verdict.reachable and not verdict.undecided
    assert set(instance.goals()) == set(verdict.reachable) | set(verdict.unreachable)
    audit = instance.source_audit(tuple(verdict.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
@pytest.mark.parametrize('lazy', [False, True])
def test_public_search_adds_and_replays_a_baseline_missed_reduction(tmp_path, wrong, lazy):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        if all([a == 37, b == 37]): return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    if lazy:
        source = source.replace(b'all([a == 37, b == 37])', b'all(x == 37 for x in (a, b))')
    candidate = source.replace(b'{"y": 7}', b'{"y": 8}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

"""Finite sequence consumers preserve values, effects and source goal hits."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['list', 'tuple'])
@pytest.mark.parametrize('argument', [
    '', '[a, 2, 3]', '(a, 2, 3)',
    'i + a for i in range(3)',
    'i + a for i in range(a)',
    'i for i in range(4) if i < a',
    'i + j for i in range(a) for j in range(2) if j < i',
    'i for i in range(0)',
])
def test_finite_sequence_values_and_length(lowerer, name, argument):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = {name}({argument})
        return {{"y": len(xs) + sum(xs)}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['list', 'tuple'])
def test_generator_is_consumed_eagerly_in_source_order(lowerer, name):
    agrees(f'''class M:
    def reset(self): self.calls = 0
    def tick(self, a):
        self.calls += 1
        return a + self.calls
    def process(self, a):
        xs = {name}(self.tick(i) for i in range(a) if self.tick(i) & 1)
        return {{"y": sum(xs) + 16 * self.calls}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_tuple_conversion_snapshots_scalar_elements_not_list_identity(lowerer):
    agrees('''class M:
    def reset(self): self.items = []
    def process(self, a):
        self.items.append(a)
        saved = tuple(self.items)
        self.items.pop(0)
        return {"y": len(saved) + sum(saved) + 16 * len(self.items)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_tuple_remains_an_ordinary_helper(lowerer):
    agrees('''def tuple(xs): return sum(xs) + 9
class M:
    def reset(self): pass
    def process(self, a): return {"y": tuple([a, 1])}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', [
    'range(a)', 'range(a - 2)', 'range(3, a - 1, -1)', 'range(0)',
    'list(range(a))', 'tuple(range(5, a, -2))',
])
def test_materialized_range_uses_the_loop_iteration_domain(lowerer, expression):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = {expression}
        return {{"y": len(xs) + sum(xs)}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_materialized_range_evaluates_arguments_once_in_order(lowerer):
    agrees('''class M:
    def reset(self): self.calls = 0
    def tick(self, value):
        self.calls = self.calls * 2 + value
        return value
    def process(self, a):
        xs = range(self.tick(a), self.tick(a + 3), self.tick(1))
        return {"y": sum(xs) + 16 * self.calls}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_tuple_and_list_constructors_keep_distinct_carriers(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        xs = tuple(i + a for i in range(3))
        ys = list(i + a for i in range(3))
        return {"y": int(xs == ys) + 2 * int(xs == tuple(ys))}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['list', 'tuple'])
def test_constructor_does_not_hide_observable_mutable_element_aliases(lowerer, name):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        row = [a]
        xs = {name}(row for i in range(2))
        row[0] = 3 - a
        return {{"y": xs[0][0]}}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert caught.value.code == 'array-assigned-by-reference'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['list', 'tuple'])
def test_constructor_with_fresh_nested_elements_is_not_an_alias(lowerer, name):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = {name}([a + i] for i in range(2))
        return {{"y": xs[0][0] + xs[1][0]}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name', ['list', 'tuple'])
def test_consumed_boolean_generator_has_boolean_padding(lowerer, name):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = {name}(i == 1 for i in range(a))
        return {{"y": int(all(xs)) + 2 * int(any(xs)) + 4 * len(xs)}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_literal_range_membership_preserves_definite_assignment(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        if a in range(0, 3):
            if a == 0: y = 5
            elif a == 1: y = 7
            elif a == 2: y = 9
        else: y = 11
        return {"y": y}
''', lowerer)


@pytest.mark.isolation
@pytest.mark.solver
def test_constructor_goal_queries_match_native_execution():
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    def reset(self): pass
    def process(self, a):
        xs = tuple(i for i in range(4) if i < a)
        if a == 2: return {"y": xs[0] + 7}
        return {"y": sum(xs)}
'''
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
def test_public_search_replays_a_baseline_missed_constructor(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        xs = tuple(x == 37 for x in (a, b))
        if all(xs): return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'return {"y": 7}', b'return {"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

"""Finite list extension shares source contracts, effects and native hits."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('argument', [
    '[]', '()', '[a, 2]', '(a, 2)', 'range(a)',
    '[i for i in range(a)]', 'tuple(i for i in range(a))', 'xs',
    '[bool(a), False]',
])
def test_finite_extension_values_and_lengths(lowerer, argument):
    initial = '[True]' if 'bool' in argument else '[a]'
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = {initial}
        xs.extend({argument})
        return {{"y": sum(xs) + 16 * len(xs)}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_extension_inside_loop_then_iteration(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        xs = []
        for i in range(a):
            xs.extend((i, i + 1))
        total = 0
        for value in xs:
            total = total * 2 + value
        return {"y": total}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_extension_evaluates_argument_effects_once_in_order(lowerer):
    agrees('''class M:
    def reset(self): self.calls = 0
    def tick(self, a):
        self.calls = self.calls * 2 + a
        return self.calls
    def process(self, a):
        xs = [a]
        xs.extend((self.tick(a), self.tick(a + 1)))
        return {"y": sum(xs) + 16 * self.calls}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_persistent_state_extension_and_pop(lowerer):
    agrees('''class M:
    def reset(self): self.items = []
    def process(self, a):
        self.items.extend([a, a + 1])
        value = self.items.pop(0)
        return {"y": value + 16 * len(self.items)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_extension_copies_fresh_nested_elements(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        xs = []
        xs.extend([[a, 1], [a + 1, 2]])
        xs[0][0] += 3
        return {"y": xs[0][0] + xs[1][0]}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('body', [
    'row = [a]\n        xs = []\n        xs.extend([row, row])\n'
    '        xs[0][0] = 7\n        return {"y": xs[1][0]}',
    'xs = [a]\n        other = xs\n        xs.extend([2])\n'
    '        return {"y": len(other)}',
    'xs = [[a]]\n        xs.extend(xs)\n        xs[0][0] = 7\n'
    '        return {"y": xs[1][0]}',
])
def test_extension_keeps_observable_aliases_refused(lowerer, body):
    with pytest.raises(LoweringError, match='reference'):
        agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        {body}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_receiver_rebinding_in_argument_is_not_silently_retargeted(lowerer):
    with pytest.raises(LoweringError):
        agrees('''class M:
    def reset(self): self.items = [0]
    def replace(self, a):
        self.items = [a]
        return [7]
    def process(self, a):
        self.items.extend(self.replace(a))
        return {"y": sum(self.items)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_empty_boolean_comprehension_retains_element_representation(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        xs = [False for i in range(0)]
        xs.extend([bool(a), False])
        return {"y": int(all(xs)) + 2 * int(any(xs)) + 4 * len(xs)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_helper_state_extension_is_a_declared_write(lowerer):
    agrees('''class M:
    def reset(self): self.items = []
    def push(self, a): self.items.extend((a, a + 1))
    def process(self, a):
        self.push(a)
        return {"y": sum(self.items) + 16 * len(self.items)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_extending_a_materialized_dynamic_sequence(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        xs = list(range(a))
        xs.extend((a, a + 1))
        return {"y": sum(xs) + 16 * len(xs)}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('argument', ['[i for i in range(a)]', 'xs'])
def test_nested_dynamic_lengths_and_self_extension(lowerer, argument):
    rhs = ('[[i for i in range(a)], [i for i in range(a)]]'
           if argument != 'xs' else 'xs')
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = [[a, a, a]]
        xs.extend({rhs})
        return {{"y": sum(xs[1]) + 16 * len(xs[1]) + 64 * len(xs)}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('statement,code', [
    ('xs.extend()', 'call-argument-form'),
    ('xs.extend([1], [2])', 'call-argument-form'),
    ('unused = xs.extend([1])', 'extend-result-used'),
])
def test_invalid_extension_forms_remain_typed_refusals(lowerer, statement, code):
    with pytest.raises(LoweringError) as caught:
        agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = []
        {statement}
        return {{"y": a}}
''', lowerer)
    assert caught.value.code == code


@pytest.mark.isolation
@pytest.mark.solver
def test_extend_queries_all_catalogue_goals_and_confirms_native_hits():
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = '''class M:
    def reset(self): pass
    def process(self, a):
        xs = []
        for i in range(a):
            xs.extend((i, i + 1))
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
def test_public_search_replays_a_baseline_missed_extension(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        xs = [a]
        xs.extend((b,))
        if xs[0] == 37 and xs[1] == 37: return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'return {"y": 7}', b'return {"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

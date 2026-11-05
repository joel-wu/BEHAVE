"""Finite bytes preserve native values, failures and source goal identities."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('carrier', ['bytes', 'bytearray'])
@pytest.mark.parametrize('argument', [
    '', '4', 'a', '[a, 2, 255]', '(a, 1)', "b'abc'",
    'i + a for i in range(a)', 'i == 1 for i in range(a)',
])
def test_finite_bytes_values_and_lengths(lowerer, carrier, argument):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        data = {carrier}({argument})
        return {{"y": len(data) + sum(data)}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_bytearray_copy_and_store(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        data = bytearray(4)
        data[a] = a + 1
        saved = bytes(data)
        copied = bytearray(data)
        data[a] = 8
        return {"y": saved[a] + 16 * copied[a] + 256 * data[a]}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('order', ['little', 'big'])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('argument', [
    'bytes([128 + a, 1])', 'bytes(0)',
    'bytearray(i + 253 for i in range(a))',
])
def test_from_bytes(lowerer, order, signed, argument):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        data = {argument}
        return {{"y": int.from_bytes(data, "{order}", signed={signed})}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_literal_repeat_concat_slice_and_carrier_equality(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        data = b'\x01\x02' * a + bytearray([3, 4])
        part = data[1:3]
        return {"y": int.from_bytes(part, "little") + int(bytes([a]) == bytearray([a]))}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', ['bytes(a - 2)', 'bytearray([256 - a])',
                                         'int.from_bytes([256 - a], "little")'])
def test_byte_valueerror_is_the_native_goal(lowerer, expression):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        data = {expression}
        return {{"y": 0}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    for a in range(4):
        model = namespace['M']()
        model.reset()
        hits.clear()
        try:
            model.process(a)
            native_error = ''
        except ValueError as failure:
            native_error = 'ValueError'
            goal = instrumented.attribute_safety_failure(failure, plan.safety_sites)
            assert goal is not None
            observed_native = set(hits) | {goal}
        else:
            observed_native = set(hits)
        _, _, observed, error = interp.run_step(proc, {'a': a})
        assert error == native_error
        assert set(observed) == observed_native


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('reset,body', [
    ('self.data = bytearray(2)', 'self.data[a & 1] = a'),
    ('self.data = bytes([1, 2])', 'self.data = bytes([a, 2])'),
    ('self.data = bytearray()', 'self.data.append(a)'),
    ('self.data = [bytearray(), bytearray()]',
     'self.data[a & 1] = bytearray([a, 1])'),
])
def test_byte_state_keeps_source_carrier(lowerer, reset, body):
    result = ('sum(self.data[0]) + sum(self.data[1])' if '[bytearray()' in reset
              else 'sum(self.data)')
    agrees(f'''class M:
    def reset(self): {reset}
    def process(self, a):
        {body}
        return {{"y": {result}}}
''', lowerer, bound=3)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_unproved_byte_mutation_does_not_get_list_semantics(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        data = bytearray(1)
        data[0] = 255 + a
        return {"y": data[0]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    assert caught.value.code == 'byte-mutation-range-unproved'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_dynamic_nested_iteration_remains_an_explicit_limitation(lowerer):
    source = '''class M:
    def reset(self): self.data = [bytearray(), bytearray()]
    def process(self, a):
        self.data[a & 1] = bytearray([a, 1])
        return {"y": sum(sum(row) for row in self.data)}
'''
    with pytest.raises(LoweringError, match='comprehension target contains a dynamically sized value'):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_generator_stops_at_first_invalid_byte_before_later_effects(lowerer):
    source = '''class M:
    def reset(self): self.calls = 0
    def tick(self, i):
        self.calls += 1
        return 256 if i == 0 else 1 // (i - 1)
    def process(self, a):
        data = bytes(self.tick(i) for i in range(2))
        return {"y": len(data)}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    model = namespace['M']()
    model.reset()
    with pytest.raises(ValueError) as caught:
        model.process(0)
    goal = instrumented.attribute_safety_failure(caught.value, plan.safety_sites)
    state, _, observed, error = interp.run_step(proc, {'a': 0})
    assert error == 'ValueError' and model.calls == state['calls'] == 1
    assert goal is not None and set(observed) == set(hits) | {goal}


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('expression', ['bytes(a - 1)', 'bytearray([256 - a])',
                                         'int.from_bytes([256 - a], "big")'])
def test_byte_failure_queries_agree_with_native(expression):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        data = {expression}
        return {{"y": 0}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    assert any(goal.endswith(':valueerror') for goal in result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('stateful', [False, True])
def test_every_byte_goal_is_queryable_and_sat_witnesses_preserve_state(stateful):
    from behave.sim.behavior.instance import BehaviorInstance

    reset = 'self.data = bytearray()' if stateful else 'pass'
    setup = 'self.data.append(a)\n        data = bytes(self.data)' if stateful else (
        'data = bytes(i + a for i in range(a))')
    source = f'''class M:
    def reset(self): {reset}
    def process(self, a):
        {setup}
        if len(data) > 1:
            return {{"y": int.from_bytes(data, "little")}}
        return {{"y": 0}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    plan = plan_source(source_model(source), source)
    assert set(instance.goals()) == set(plan.manifest)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    assert set(result.reachable) | set(result.unreachable) == set(instance.goals())
    assert any(plan.manifest[goal].family == 'decision' for goal in result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_bytes_public_residual_search_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        data = bytearray(2)
        data[0] = a
        if int.from_bytes(data, "little") == 3:
            return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'{"y": 7}', b'{"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_byte_constructor_does_not_hide_shared_mutable_identity(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        data = bytearray(2)
        alias = data
        data[0] = a
        return {"y": alias[0]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    assert caught.value.code == 'array-assigned-by-reference'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_bytes_is_a_helper_not_a_conversion(lowerer):
    agrees('''def bytes(a): return a + 7
class M:
    def reset(self): pass
    def process(self, a): return {"y": bytes(a)}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_repeat_of_empty_dynamic_byte_sequence_has_no_spurious_failure(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        data = bytes(i for i in range(a)) * 2
        return {"y": len(data) + sum(data)}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_from_bytes_argument_evaluates_before_mode_rebind(lowerer):
    agrees('''class M:
    def reset(self): self.data = bytearray([1, 2])
    def order(self, a):
        self.data = bytearray([a, 3])
        return "little"
    def process(self, a):
        value = int.from_bytes(self.data, self.order(a))
        return {"y": value + sum(self.data)}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_byte_order_expression_effect_is_not_elided(lowerer):
    agrees('''class M:
    def reset(self): self.calls = 0
    def order(self):
        self.calls += 1
        return "little"
    def process(self, a):
        value = int.from_bytes(bytes([a, 1]), self.order())
        return {"y": value + self.calls}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', ['IV[:]', 'IV.copy()', 'bytearray(IV).copy()',
                                        '[a, 2].copy()'])
def test_flat_sequence_copy_has_fresh_outer_storage(lowerer, expression):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        IV = [1, 2]
        data = {expression}
        saved = data.copy()
        data[0] = a
        return {{"y": saved[0] + data[0] + IV[0]}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_sequence_copy_does_not_deep_copy_mutable_children(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        data = [[a, 1]]
        saved = data.copy()
        data[0][0] = 3
        return {"y": saved[0][0]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    assert caught.value.code == 'array-assigned-by-reference'

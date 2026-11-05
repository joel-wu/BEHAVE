"""Integer serialization is exact, including partial calls and source effects."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('arguments', [
    '', "2, 'little'", "2, 'big'", "length=2, byteorder='little'",
    "2, 'little', signed=True", "2, 'big', signed=True", "a, 'little'",
])
def test_integer_byte_values_lengths_and_carrier(lowerer, arguments):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        data = a.to_bytes({arguments})
        return {{"y": int.from_bytes(data, "little") + len(data) + int(data == list(data))}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('value,length,signed', [
    ('a - 2', '1', False), ('255 + a', '1', False),
    ('126 + a', '1', True), ('a - 130', '1', True),
    ('a - 2', '0', True), ('a', '0', False),
    ('a', 'a - 2', False), ('a - 1', 'a - 1', True),
    ('256 * a', '1', True), ('a - 1', '2', True),
])
def test_integer_byte_failures_match_native_goals(lowerer, value, length, signed):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        data = ({value}).to_bytes({length}, 'big', signed={signed})
        return {{"y": len(data) + sum(data)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    for a in range(4):
        model = namespace['M']()
        model.reset()
        hits.clear()
        goal, native_output, native_error = None, None, ''
        try:
            native_output = model.process(a)
        except (ValueError, OverflowError) as failure:
            native_error = type(failure).__name__
            goal = instrumented.attribute_safety_failure(failure, plan.safety_sites)
            assert goal is not None
        _, output, observed, error = interp.run_step(proc, {'a': a})
        assert error == native_error
        assert set(observed) == set(hits) | ({goal} if goal else set())
        if not native_error:
            assert output.transactions == [native_output]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_receiver_then_keywords_evaluate_once_in_source_order(lowerer):
    agrees('''class M:
    def reset(self): self.value = 1; self.calls = 0
    def order(self):
        self.value = 2
        self.calls = self.calls * 4 + 1
        return 'little'
    def size(self):
        self.value = 3
        self.calls = self.calls * 4 + 2
        return 2
    def process(self, a):
        data = self.value.to_bytes(byteorder=self.order(), length=self.size())
        return {"y": int.from_bytes(data, 'little') + self.value + 16 * self.calls}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('keywords', ['signed={sign}, byteorder=self.order()',
                                      'byteorder=self.order(), signed={sign}'])
@pytest.mark.parametrize('sign', ['True', 'self.sign()'])
def test_from_bytes_keyword_effects_and_static_mode_boundary(lowerer, keywords, sign):
    source = f'''class M:
    def reset(self): self.data = bytearray([255, 254]); self.calls = 0
    def sign(self):
        self.data = bytearray([0, 1])
        self.calls = self.calls * 4 + 1
        return True
    def order(self):
        self.data = bytearray([2, 3])
        self.calls = self.calls * 4 + 2
        return 'little'
    def process(self, a):
        value = int.from_bytes(self.data, {keywords.format(sign=sign)})
        return {{'y': value + 1024 * self.calls + 65536 * sum(self.data)}}
'''
    if sign == 'True':
        agrees(source, lowerer, bound=2)
    else:
        # A helper's Boolean result is represented dynamically, even when its
        # body returns a literal. This migration must not guess its value.
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
        assert caught.value.code == 'byte-conversion-mode-unproved'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_custom_helper_named_to_bytes_is_not_integer_conversion(lowerer):
    source = '''class M:
    def reset(self): pass
    def to_bytes(self, a): return a + 1
    def process(self, a): return {"y": self.to_bytes(a)}
'''
    agrees(source, lowerer, bound=2)
    plan = plan_source(source_model(source), source)
    assert not any(goal.endswith((':valueerror', ':overflowerror'))
                   for goal in plan.manifest)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_variable_mode_is_a_capability_refusal_not_a_guessed_conversion(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        data = a.to_bytes(2, 'little' if a else 'big')
        return {"y": sum(data)}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    assert caught.value.code == 'byte-conversion-mode-unproved'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('expression, failure', [
    ("(a - 1).to_bytes(a - 1, 'little')", 'valueerror'),
    ("(128 * a).to_bytes(1, 'big')", 'overflowerror'),
])
def test_every_conversion_goal_queries_and_replays_native(expression, failure):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        data = {expression}
        return {{"y": len(data)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()
    assert any(goal.endswith(':' + failure) for goal in result.reachable)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_serialized_bytes_persist_with_native_state_carrier(lowerer):
    agrees('''class M:
    def reset(self): self.data = b''
    def process(self, a):
        old = int.from_bytes(self.data, 'little')
        self.data = (a + 256).to_bytes(2, 'little')
        return {"y": old + sum(self.data)}
''', lowerer, bound=3)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_integer_bytes_public_search_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        data = a.to_bytes(2, 'little')
        if int.from_bytes(data, 'little') == 3: return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'{"y": 7}', b'{"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', ['TABLE[:]', 'TABLE[::-1]', 'TABLE[1:3]',
                                       'TABLE[-3:-1]', 'TABLE[a:a+1]'])
def test_closed_sequence_slice_retains_carrier_and_fresh_storage(lowerer, expression):
    agrees(f'''TABLE = [1, 2, 3, 4]
class M:
    def reset(self): pass
    def process(self, a):
        data = {expression}
        data[0] = a
        return {{"y": sum(data) + TABLE[0] + int(data == tuple(data))}}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_closed_slice_does_not_deep_copy_mutable_elements(lowerer):
    source = '''TABLE = [[1, 2], [3, 4]]
class M:
    def reset(self): pass
    def process(self, a):
        data = TABLE[:]
        data[0][0] = a
        return {"y": TABLE[0][0]}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_byte_padding_and_length_suffix(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        data = bytes(i for i in range(a))
        packed = data + b'\\0' * ((-len(data)) % 4) + a.to_bytes(2, 'little')
        return {"y": len(packed) + sum(packed)}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_literal_byte_slice_and_boolean_integer_receiver(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        data = b'abcd'[a:a+1]
        suffix = (a == 1).to_bytes(1)
        return {"y": sum(data + suffix)}
''', lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('divisor', [4, -4])
def test_modulo_sequence_extent_obeys_python_sign(lowerer, divisor):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        data = [1] * ((a - 2) % {divisor})
        return {{"y": len(data)}}
''', lowerer, bound=2)

"""Fixed-schema state records use the existing recursive product carrier."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees

SOURCES = [
    '''class M:
    def reset(self): self.regs = {"x": 1, "y": 2}
    def process(self, a):
        old = self.regs["x"]
        self.regs = {"x": self.regs["y"], "y": self.regs["x"] + a}
        return {"y": old}
''',
    '''class M:
    def reset(self): self.regs = {"x": 1, "y": 2}
    def process(self, a):
        if a & 1: self.regs = {"x": a, "y": self.regs["x"]}
        return {"y": self.regs["x"] + self.regs["y"]}
''',
    '''class M:
    def reset(self): self.regs = {"nested": {"x": 1}, "pair": (2, 3)}
    def process(self, a):
        old = self.regs["nested"]["x"]
        self.regs = {"nested": {"x": self.regs["pair"][0]}, "pair": (a, old)}
        return {"y": self.regs["pair"][1]}
''',
    '''class M:
    def reset(self): self.regs = {"flag": False, "x": 1}
    def process(self, a):
        old = int(self.regs["flag"])
        self.regs = {"flag": a == 2, "x": a}
        return {"y": old + self.regs["x"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": None, "y": 1}
    def process(self, a):
        old = 0 if self.regs["x"] is None else self.regs["x"]
        self.regs = {"x": a if a & 1 else None, "y": old}
        return {"y": self.regs["y"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": 0, "y": 1}
    def update(self, a): self.regs = {"x": a, "y": self.regs["x"]}
    def process(self, a):
        self.update(a)
        return {"y": self.regs["y"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": 1, "y": 2}
    def process(self, a):
        old = self.regs
        self.regs = {"x": a, "y": 0}
        return {"y": old["x"]}
''',
    '''class M:
    def reset(self): self.regs = {"xs": [1, 2], "y": 3}
    def process(self, a):
        old = self.regs["xs"][0]
        self.regs = {"xs": [self.regs["y"], a], "y": old}
        return {"y": self.regs["xs"][0] + self.regs["y"]}
''',
    '''class M:
    def reset(self): self.a = {"x": 1, "y": 2}
    def process(self, a):
        old = self.a["y"]
        self.a = {"x": a, "y": self.a["x"]}
        return {"y": old}
''',
    '''class M:
    def reset(self): self.regs = {"z": {"y": 2, "x": None}, "a": 1}
    def process(self, a):
        old = 0 if self.regs["z"]["x"] is None else self.regs["z"]["x"]
        self.regs = {"a": old, "z": {"x": a if a & 1 else None, "y": old}}
        return {"y": self.regs["a"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": None}
    def process(self, a):
        old = self.regs["x"] is not None and self.regs["x"] == a
        self.regs = {"x": a if a & 1 else None}
        return {"y": int(old)}
''',
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES)
def test_fixed_record_history_matches_native(source, lowerer):
    agrees(source, lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', SOURCES[:6])
def test_state_record_queries_have_native_witnesses(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('change', [
    'self.regs = {"x": a}',
    'self.regs = {"x": a, "y": 0, "new": 1}',
    'self.regs["x"] = a',
    'self.regs["x"] += a',
    'del self.regs["x"]',
])
def test_nonfixed_schema_and_unrepresented_mutation_still_refuse(lowerer, change):
    source = f'''class M:
    def reset(self): self.regs = {{"x": 1, "y": 2}}
    def process(self, a):
        {change}
        return {{"y": self.regs["x"]}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shared_record_state_is_not_silently_copied(lowerer):
    source = '''class M:
    def reset(self): self.left = {"x": 1}; self.right = {"x": 2}
    def process(self, a):
        self.right = self.left
        return {"y": self.right["x"]}
'''
    with pytest.raises(LoweringError) as exc:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert exc.value.code in ('array-assigned-by-reference', 'static-collection-escapes')


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_integer_dictionary_uses_a_mapping_not_a_fixed_record(lowerer):
    source = '''class M:
    def reset(self): self.mem = {}
    def process(self, a):
        self.mem[a] = a
        return {"y": self.mem[a]}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_variable_length_record_leaf_is_an_explicit_capability_boundary(lowerer):
    source = '''class M:
    def reset(self): self.regs = {"xs": [1]}
    def process(self, a):
        self.regs = {"xs": [a, a]}
        return {"y": self.regs["xs"][0]}
'''
    with pytest.raises(LoweringError) as exc:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert exc.value.code == 'state-product-layout-unsupported'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_unsupported_record_values_iteration_is_not_silently_sorted(lowerer):
    source = '''class M:
    def reset(self): self.regs = {"z": 1, "a": 2}
    def process(self, a):
        total = sum(value for value in self.regs.values())
        self.regs = {"a": a, "z": 1}
        return {"y": total}
'''
    with pytest.raises(LoweringError) as exc:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert exc.value.code == 'call-unlowered'


def test_product_audit_compares_fields_but_not_by_untyped_python_equality():
    from behave.sim.behavior.source_values import contract_of_value
    from behave.sim.symbolic.state_projection import SourceProductBinding

    value = {"z": [1, 2], "a": {"y": None, "x": False}}
    binding = SourceProductBinding('regs', contract_of_value(value), (
        (("a", "x"), 'flag'), (("z",), 'array')))
    reordered = {"a": {"x": False, "y": None}, "z": [1, 2]}
    assert binding.equivalent(value, reordered)
    for wrong in (
        {"a": {"x": 0, "y": None}, "z": [1, 2]},
        {"a": {"x": False, "y": None}, "z": (1, 2)},
        {"a": {"x": False, "y": None}, "z": [2, 1]},
        {"a": {"x": False}, "z": [1, 2]},
        {"a": {"x": False, "y": None}, "z": [1, 2], "extra": 0},
    ):
        assert not binding.equivalent(value, wrong)
    shared = [1]
    record = {"x": shared, "y": shared}
    alias_binding = SourceProductBinding('regs', contract_of_value(record), (
        (("x",), 'left'), (("y",), 'right')))
    assert not alias_binding.equivalent(record, {"x": [1], "y": [1]})


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('leaf,code', [
    ('"abc"', 'record-field-representation-unsupported'),
    ('0.5', 'literal-unsupported'),
])
def test_non_ir_record_leaf_refuses_at_the_representation_boundary(lowerer, leaf, code):
    source = f'''class M:
    def reset(self): self.regs = {{"x": {leaf}}}
    def process(self, a):
        self.regs = {{"x": {leaf}}}
        return {{"y": a}}
'''
    with pytest.raises(LoweringError) as exc:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert exc.value.code == code


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('replacement', ['b"abc"', 'bytes([a, 0, 255])'])
def test_record_bytes_preserve_native_history_and_carrier(lowerer, replacement):
    # Bytes already have a finite carrier; they are not an unsupported literal.
    # Keep a native state/output comparison rather than merely testing admission.
    agrees(f'''class M:
    def reset(self): self.regs = {{"x": b"abc"}}
    def process(self, a):
        old = self.regs["x"][a % 3]
        self.regs = {{"x": {replacement}}}
        return {{"y": old + 256 * self.regs["x"][0]}}
''', lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('clear', ['self.regs = {"x": None}', 'self.clear()'])
def test_optional_record_presence_is_not_kept_after_rebinding(lowerer, clear):
    source = f'''class M:
    def reset(self): self.regs = {{"x": None}}
    def clear(self): self.regs = {{"x": None}}
    def process(self, a):
        if self.regs["x"] is not None:
            {clear}
            return {{"y": self.regs["x"] + a}}
        self.regs = {{"x": a}}
        return {{"y": 0}}
'''
    with pytest.raises(LoweringError) as exc:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert exc.value.code == 'optional-value-not-unwrapped'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_replays_a_missed_state_record_branch(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.regs = {"x": 0, "y": 0}
    def process(self, a, b):
        self.regs = {"x": a, "y": b}
        if self.regs["x"] == 37 and self.regs["y"] == 37: return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'return {"y": 7}', b'return {"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

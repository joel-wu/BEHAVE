"""Finite record copies preserve snapshots, effects and shallow aliases."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees

SOURCES = [
    '''class M:
    def reset(self): self.regs = {"x": 1, "y": 2}
    def process(self, a):
        old = dict(self.regs)
        self.regs = {"x": a, "y": self.regs["x"]}
        return {"y": old["x"] + old["y"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": 1, "y": 2}
    def process(self, a):
        old = {"x": 0, "y": 0} if a == 0 else dict(self.regs)
        self.regs = {"x": a, "y": self.regs["x"]}
        return {"y": old["x"] + old["y"]}
''',
    '''class M:
    def reset(self): self.x = 0
    def make(self, a):
        self.x += 1
        return {"v": a + self.x}
    def process(self, a):
        copied = dict(self.make(a))
        return {"y": copied["v"] + self.x}
''',
    '''class M:
    def reset(self): self.x = 0
    def process(self, a):
        original = {"x": a}
        copied = dict(original)
        copied["x"] += 1
        original["x"] += 2
        return {"y": copied["x"] * 4 + original["x"]}
''',
    '''class M:
    def reset(self): self.x = 0
    def process(self, a):
        original = {"x": a}
        original["x"] += 1
        copied = dict(original)
        original["x"] += 2
        return {"y": copied["x"]}
''',
    '''class M:
    def reset(self): self.left = {"v": 0}; self.right = {"v": 0}
    def process(self, a):
        self.right = dict(self.left)
        self.left = {"v": a}
        return {"y": self.right["v"]}
''',
    '''class M:
    def reset(self): self.regs = {"r": {"x": 1}, "xs": [2, 3]}
    def process(self, a):
        old = dict(self.regs)
        self.regs = {"r": {"x": a}, "xs": [a, a]}
        return {"y": old["r"]["x"] + old["xs"][0]}
''',
    '''class M:
    def reset(self): self.regs = {"x": 0}
    def process(self, a):
        local = {"x": a}
        local["x"] += 1
        self.regs = dict(local)
        local["x"] += 1
        return {"y": self.regs["x"]}
''',
    '''class M:
    def reset(self): self.regs = {"x": None}
    def process(self, a):
        old = dict(self.regs)
        self.regs = {"x": a if a & 1 else None}
        return {"y": 0 if old["x"] is None else old["x"]}
''',
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES)
def test_record_copy_matches_native_history(lowerer, source):
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('payload', ['[0]', '{"v": 0}', '([0],)'])
def test_shallow_copy_does_not_hide_shared_persistent_children(lowerer, payload):
    source = f'''class M:
    def reset(self): self.left = {{"x": {payload}}}; self.right = {{"x": {payload}}}
    def process(self, a):
        self.right = dict(self.left)
        return {{"y": a}}
'''
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert error.value.code in ('array-assigned-by-reference', 'static-collection-escapes')


@pytest.mark.parametrize('payload', [[0], {"v": 0}, ([0],)])
def test_copy_points_to_retains_children_at_state_entry(payload):
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_values import contract_of_value
    from behave.sim.behavior.step_alias import step_alias_hazard

    source = f'''class M:
    def reset(self): self.left = {{"x": {payload!r}}}; self.right = {{"x": {payload!r}}}
    def process(self, a):
        self.right = dict(self.left)
        return {{"y": a}}
'''
    contracts = {name: contract_of_value({"x": payload}) for name in ('left', 'right')}
    hazard = step_alias_hazard(source_model(source), set(contracts), state_contracts=contracts)
    assert hazard is None  # Read-only cross-field sharing is exact by value.
    target = ('self.left["x"]["v"]' if isinstance(payload, dict) else
              'self.left["x"][0][0]' if isinstance(payload, tuple) else
              'self.left["x"][0]')
    mutating = source.replace('        self.right = dict(self.left)',
                              f'        {target} += a\n        self.right = dict(self.left)')
    hazard = step_alias_hazard(source_model(mutating), set(contracts), state_contracts=contracts)
    assert hazard is not None and hazard.kind == 'retained'


def test_alias_reuse_identity_includes_product_children():
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_values import contract_of_value
    from behave.sim.behavior.step_alias import alias_query_identity

    model = source_model(SOURCES[5])
    scalar = {'left': contract_of_value({'v': 0})}
    mutable = {'left': contract_of_value({'v': [0]})}
    assert alias_query_identity(model, {'left'}, {}, {}, state_contracts=scalar) != (
        alias_query_identity(model, {'left'}, {}, {}, state_contracts=mutable))


def test_scalar_array_children_are_not_enumerated_for_aliases(monkeypatch):
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_values import ValueContract, contract_of_value
    from behave.sim.behavior.step_alias import step_alias_hazard

    def unexpected_cell(_contract, _index):
        raise AssertionError('scalar array cells have no mutable identity')

    source = '''class M:
    def reset(self): self.left = {"x": [0] * 4096}; self.right = {"x": [0] * 4096}
    def process(self, a):
        self.left["x"][0] += a
        self.right = dict(self.left)
        return {"y": a}
'''
    contract = contract_of_value({'x': [0] * 4096})
    monkeypatch.setattr(ValueContract, 'component', unexpected_cell)
    hazard = step_alias_hazard(source_model(source), {'left', 'right'},
                              state_contracts={'left': contract, 'right': contract})
    assert hazard is not None and hazard.kind == 'retained'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_copy_does_not_invent_an_arbitrary_mapping_iteration(lowerer):
    source = '''class M:
    def reset(self): self.x = 0
    def process(self, a):
        r = dict({"z": a, "y": a + 1})
        return {"y": sum(i * value for i, (key, value) in enumerate(r.items()))}
'''
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert error.value.code == 'call-unlowered'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shallow_copy_does_not_hide_live_mutable_children(lowerer):
    source = '''class M:
    def reset(self): self.x = 0
    def process(self, a):
        xs = [a]
        original = {"x": xs}
        copied = dict(original)
        xs[0] += 1
        return {"y": copied["x"][0]}
'''
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                bound=4, lowerer=lowerer)
    assert error.value.code == 'array-assigned-by-reference'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_dict_keeps_helper_semantics(lowerer):
    source = '''def dict(record):
    return {"x": record["x"] + 1}
class M:
    def reset(self): self.x = 0
    def process(self, a):
        copied = dict({"x": a})
        return {"y": copied["x"]}
'''
    agrees(source, lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', SOURCES[:3])
def test_copy_catalogue_queries_match_native_hits(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_uses_copied_record_missed_branch(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.regs = {"x": 0, "y": 0}
    def process(self, a, b):
        self.regs = {"x": a, "y": b}
        saved = dict(self.regs)
        self.regs = {"x": 0, "y": 0}
        if saved["x"] == 37 and saved["y"] == 37: return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'return {"y": 7}', b'return {"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

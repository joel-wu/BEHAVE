"""Immutable tuple-key tables share source contracts, queries and replay."""

import ast

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges
from behave.sim.behavior.source_rom import evaluate_frozen, freeze_value
from tests.sim.test_source_closed_bindings import agrees


TABLE = '{(0, 0): 1, (0, 1): 2, (1, 0): 0, (1, 1): 1}'


def model(scope, lookup):
    declaration = f'TABLE = {TABLE}\n' if scope == 'module' else ''
    class_decl = f'    TABLE = {TABLE}\n' if scope == 'class' else ''
    local = f'        table = {TABLE}\n' if scope == 'local' else ''
    receiver = {'module': 'TABLE', 'class': 'self.TABLE', 'local': 'table'}[scope]
    return f'''{declaration}class M:
{class_decl}    def reset(self): self.state = 0
    def process(self, a):
{local}        key = (self.state & 1, a & 1)
        self.state = {lookup.replace('table', receiver)}
        return {{"y": self.state}}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('scope', ['module', 'class', 'local'])
@pytest.mark.parametrize('lookup', [
    'table[(self.state & 1, a & 1)]',
    'table[key]',
    'table.get((self.state, a), 3)',
    'table.get(key, a)',
    'int(table.get(key, 0))',
    'int((self.state & 1, a & 1) in table)',
    'int(key not in table)',
])
def test_tuple_lookup_contracts_and_native_execution(lowerer, scope, lookup):
    agrees(model(scope, lookup), lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('table,key', [
    ('{(): 4, (0,): 5, (1,): 6}', '()'),
    ('{(0,): 4, (1,): 6}', '(bool(a),)'),
    ('{(True,): 4, (1,): 6, (False,): 3}', '(a & 1,)'),
    ('{((0, 1), "run"): 5, ((1, 1), "run"): 6}', '((a & 1, 1), "run")'),
    ('{(0, "run"): 5, (1, "run"): 6}', '(a & 1, "run")'),
    ('{(0, 1): 5, (1, 1): 6, 1: 7}', '(a & 1, 1)'),
])
def test_tuple_keys_keep_shape_labels_and_python_numeric_equality(lowerer, table, key):
    source = f'''TABLE = {table}
class M:
    def reset(self): pass
    def process(self, a):
        return {{"y": TABLE.get({key}, 0)}}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_key_components_and_default_evaluate_once_in_order(lowerer):
    source = '''TABLE = {(0, 1): 4, (1, 2): 5, (2, 3): 6}
class M:
    def reset(self): self.state = 0
    def next(self):
        before = self.state
        self.state += 1
        return before
    def process(self, a):
        self.state = a
        value = TABLE.get((self.next(), self.next()), self.next())
        return {"y": value + 8 * self.state}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_bound_closed_key_and_computed_string_tuple_selector(lowerer):
    source = '''TABLE = {(0, "run"): 5, (1, "run"): 6}
class M:
    def reset(self): pass
    def process(self, a):
        fixed = (0, "run")
        return {"y": TABLE.get((a & 1, "run"), 0) + TABLE[fixed] + int((a & 1, "run") in TABLE)}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_general_mixed_runtime_sequence_is_a_typed_refusal_not_an_ir_crash(lowerer):
    source = '''TABLE = {(0, "run"): 5, (1, "run"): 6}
class M:
    def reset(self): pass
    def process(self, a):
        key = (a & 1, "run")
        return {"y": TABLE.get(key, 0)}
'''
    with pytest.raises(LoweringError) as refused:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)
    assert refused.value.code == 'static-collection-escapes'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('key', ['[a, 1]', '(a, [1])'])
def test_mutable_keys_are_not_normalized_into_hashable_tuples(lowerer, key):
    source = f'''TABLE = {{(0, 1): 5}}
class M:
    def reset(self): pass
    def process(self, a): return {{"y": TABLE.get({key}, 0)}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('key', ['["run"]', '("run", [1])'])
def test_bound_static_mutable_key_retains_unhashability(lowerer, key):
    source = f'''TABLE = {{("run",): 5}}
class M:
    def reset(self): pass
    def process(self, a):
        key = {key}
        return {{"y": TABLE.get(key, 0)}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('key', ['[0]', '(0, [1])'])
def test_local_dict_display_does_not_freeze_an_illegal_key(lowerer, key):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        table = {{{key}: 5}}
        return {{"y": table.get((0,), 0)}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


def test_closed_tuple_key_evaluation_matches_python():
    source = '{((True, 2), "x"): 7, ((1, 2), "x"): 8}'
    frozen = evaluate_frozen(ast.parse(source, mode='eval').body, lambda _node: None)
    assert frozen.to_source_value() == eval(source)
    assert freeze_value(eval(source)).to_source_value() == eval(source)
    selected = evaluate_frozen(ast.parse(source + '[((1, 2), "x")]', mode='eval').body,
                               lambda _node: None)
    assert selected.payload == 8


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('scope', ['module', 'class', 'local'])
def test_all_tuple_lookup_goals_query_and_native_replay(scope):
    from behave.sim.behavior.instance import BehaviorInstance

    source = model(scope, 'table[(self.state & 1, a & 1)]')
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_tuple_key_public_search_confirms_a_new_native_hit(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''TABLE = {(37, 1): 7}
class M:
    def reset(self): pass
    def process(self, a, b):
        key = (a, 1)
        if key in TABLE:
            return {"y": int(TABLE.get(key, 0))}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'(37, 1): 7', b'(37, 1): 6') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.isolation
@pytest.mark.solver
def test_absent_tuple_key_has_a_queryable_native_safety_hit():
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''TABLE = {(0, 1): 5}
class M:
    def reset(self): pass
    def process(self, a): return {"y": TABLE[(a, 1)]}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    instance.bind(1)
    goals = [goal for goal in instance.goals() if 'indexerror' in str(goal)]
    assert goals
    result = instance.query(goals, 1)
    assert set(result.reachable) == set(goals)
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()

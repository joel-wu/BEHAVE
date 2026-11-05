"""Length snapshots refine storage only while both endpoints stay unchanged."""

import ast

import pytest

from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError
from behave.sim.behavior.source_sequences import _FunctionContracts, _ScalarBindings
from behave.sim.behavior.source_values import SCALAR, ValueContract
from tests.sim.test_saved_sequence_predicates import stack_model
from tests.sim.test_source_closed_bindings import agrees


def counted_stack(predicate='bool(a & 1) and count != 0',
                  prefix='count = len(self.stack)', between='', guard='take'):
    return stack_model(predicate, guard, between).replace(
        '        take =', '        ' + prefix.replace('\n', '\n        ') + '\n        take =')


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('predicate,prefix,guard', [
    ('bool(a & 1) and count != 0', 'count = len(self.stack)', 'take'),
    ('0 < count and bool(a & 1)', 'count: int = len(self.stack)', 'take'),
    ('count == 0 or not bool(a & 1)', 'n = len(self.stack)\ncount = n', 'not take'),
    ('bool(count)', 'count = copy = len(self.stack)', 'take'),
    ('not count', 'count = len(self.stack)', 'not take'),
    ('count > 0', 'count = len(self.stack)', 'count > 0'),
    ('bool(count)', 'count = len(self.stack)\nif self.stack: self.stack[0] = a', 'take'),
])
def test_saved_count_refines_all_four_call_traces(lowerer, predicate, prefix, guard):
    agrees(counted_stack(predicate, prefix, guard=guard), lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('between', [
    '        count = 0\n',
    '        self.stack.append(a + 4)\n',
    '        self.stack = [a + 4]\n',
])
def test_already_saved_boolean_keeps_its_value(lowerer, between):
    agrees(counted_stack(between=between), lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_saved_full_guard_bounds_capacity(lowerer):
    source = '''class M:
    def reset(self): self.stack = []
    def process(self, a):
        n = len(self.stack)
        full = n == 2
        if not full: self.stack.append(a)
        return {"y": len(self.stack)}
'''
    from behave.sim.symbolic.typecheck import Array
    proc = agrees(source, lowerer)
    assert any(proc.types[name] == Array(2) for name in proc.state)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('mutation', [
    'self.stack = []', 'self.stack.pop()', 'self.clear()',
    'self.stack[:] = []', 'count = 1',
])
def test_stale_count_never_proves_a_later_empty_stack_nonempty(lowerer, mutation):
    source = '''class M:
    def reset(self): self.stack = []; self.data_out = 0
    def clear(self): self.stack = []
    def process(self, a):
        self.stack = [a]
        count = len(self.stack)
        MUTATION
        if count > 0: self.data_out = self.stack[-1]
        return {"y": self.data_out}
'''.replace('MUTATION', mutation)
    if mutation == 'count = 1':
        source = source.replace('self.stack = [a]', 'self.stack = []')
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    native = namespace['M']()
    native.reset()
    with pytest.raises(IndexError):
        native.process(0)
    # Until an invalid access has an explicit IR carrier a typed refusal is
    # valid. Admission may never erase the native failure using a stale fact.
    try:
        agrees(source, lowerer)
    except LoweringError:
        pass


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_count_reassignment_is_not_a_live_length_query(lowerer):
    source = counted_stack(prefix='count = len(self.stack)\ncount = 0',
                           guard='count > 0')
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('change', [
    'xs = [a, a]',
    'xs.extend([a])',
    'for i in range(2): xs.append(a)',
    'n += 1',
])
def test_local_mutation_and_loop_summary_do_not_retest_old_count(lowerer, change):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        xs = [a]
        n = len(xs)
        {change}
        if n == 1: return {{"y": len(xs)}}
        return {{"y": 0}}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shadowed_len_is_not_a_sequence_length_fact(lowerer):
    source = '''def len(value): return 0
class M:
    def reset(self): pass
    def process(self, a):
        xs = [a]
        n = len(xs)
        if n == 0: return {"y": 7}
        return {"y": 0}
'''
    agrees(source, lowerer)


def test_path_copies_and_joins_keep_only_common_relations():
    first = _ScalarBindings(length_facts={'n': 'xs', 'm': 'ys'})
    other = first.clone()
    other.forget_length('xs')
    assert first.lengths == {'n': 'xs', 'm': 'ys'}
    assert _FunctionContracts._merge_bindings(first, other).lengths == {'m': 'ys'}
    other.lengths['n'] = 'ys'
    assert _FunctionContracts._merge_bindings(first, other).lengths == {'m': 'ys'}
    first.forget_length('n')
    assert first.lengths == {'m': 'ys'}


def test_pure_query_cache_includes_relation_and_referenced_container():
    model = source_model('class M:\n def process(self, a): return {"y": a}\n')
    planner = _FunctionContracts(model, ScopeResolver(model))
    planner._context[:] = [('test',)]
    node = ast.parse('1 if n == 0 else 2', mode='eval').body
    bindings = _ScalarBindings(length_facts={'n': 'xs'})
    env = {'n': SCALAR, 'xs': ValueContract.sequence('list', 0, 2, SCALAR)}
    first = planner._contract_query_key(model.step, node, env, bindings, None)
    bindings.lengths.clear()
    assert first != planner._contract_query_key(model.step, node, env, bindings, None)
    bindings.lengths['n'] = 'xs'
    env['xs'] = ValueContract.sequence('list', 1, 2, SCALAR)
    assert first != planner._contract_query_key(model.step, node, env, bindings, None)


@pytest.mark.isolation
@pytest.mark.solver
def test_all_catalogue_queries_replay_with_native_hits():
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = counted_stack()
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
def test_public_search_replays_a_baseline_missed_saved_count(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        xs = [a] * (b & 1)
        n = len(xs)
        take = n != 0 and a == 37
        if take: return {"y": xs[0] - 30}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'xs[0] - 30', b'xs[0] - 31') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

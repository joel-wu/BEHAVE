"""Definition-time bindings and closed lookups agree across frontend stages."""

import ast
import itertools

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges


DECLARATIONS = [
    'IDLE, RUN = 0, 3',
    'IDLE, (RUN, EXTRA) = 0, (3, 8)',
    'IDLE, RUN = FIRST, LAST = 0, 3',
    'IDLE, RUN = 3, 0\nIDLE, RUN = RUN, IDLE',
    'IDLE, RUN = range(2)',
    'IDLE, RUN = range(0, 4, 3)',
    'IDLE, RUN = range(3, -1, -3)',
]


def source_for(declaration, *, class_scope=False):
    head = ('class M:\n    ' + declaration.replace('\n', '\n    ')
            if class_scope else declaration + '\nclass M:')
    prefix = 'self.' if class_scope else ''
    return head + f'''
    def reset(self): self.state = {prefix}IDLE
    def process(self, a):
        old = self.state
        self.state = {prefix}RUN if a & 1 else {prefix}IDLE
        return {{"y": old}}
'''


def agrees(source, lowerer, bound=4):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=bound, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': value} for value in values]
              for values in itertools.product(range(4), repeat=bound)]
    findings = model_audit(proc, namespace['M'], traces)
    assert not findings, findings[:3]
    return proc


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('class_scope', [False, True])
@pytest.mark.parametrize('declaration', DECLARATIONS)
def test_closed_declaration_bindings(lowerer, class_scope, declaration):
    agrees(source_for(declaration, class_scope=class_scope), lowerer)


LOOKUPS = [
    'table.get(a, 1) << 1',
    'table.get(a, a) | table.get(3 - a, 0)',
    '{0: 2, 1: 3}.get(a, 0) + table.get(a, 1)',
    '(table.get(0) if a < 2 else 0) + 1',
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', LOOKUPS)
def test_nested_closed_get_operand_contract(lowerer, expression):
    source = f'''class M:
    def reset(self): self.state = 0
    def process(self, a):
        table = {{0: 2, 1: 3}}
        self.state = {expression}
        return {{"y": self.state}}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('prefix', [
    'def range(n): return (7, 8)\n',
    'range = lambda n: (7, 8)\n',
])
def test_range_shadow_is_not_builtin(lowerer, prefix):
    source = prefix + source_for('IDLE, RUN = range(2)')
    # Refusal is valid; substituting the built-in range result is not.
    try:
        agrees(source, lowerer)
    except LoweringError:
        pass


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_module_defaults_observe_binding_time(lowerer):
    source = '''IDLE, RUN = 0, 3
class M:
    def reset(self): self.state = IDLE
    def helper(self, n=RUN): return n
    def process(self, a):
        self.state = RUN if a & 1 else self.helper()
        return {"y": self.state}
IDLE, RUN = 1, 2
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('receiver', ['table', 'self.TABLE', 'TABLE'])
def test_get_default_effect_occurs_after_key_and_even_on_hit(lowerer, receiver):
    source = f'''TABLE = {{0: 5, 1: 7}}
class M:
    TABLE = {{0: 5, 1: 7}}
    def reset(self): self.state = 0; self.count = 0
    def default(self):
        self.state = 3
        self.count += 1
        return 9
    def process(self, a):
        table = {{0: 5, 1: 7}}
        self.state = a
        y = {receiver}.get(self.state, self.default())
        return {{"y": y + 16 * self.count}}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_get_selected_mapping_dynamic_default(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        table = {0: 1} if a & 1 else {1: 2}
        return {"y": table.get(a >> 1, a)}
'''
    agrees(source, lowerer)


@pytest.mark.parametrize('declaration', [
    'A, B = range(3)', 'A, B = range(2, 2, 0)',
    'A, B = range(1 << 128)', 'A, (B, C) = range(2)',
    'VALUES = range(2)', 'VALUES = A, B = range(2)',
])
def test_range_unpack_does_not_invent_bindings_or_erase_carrier(declaration):
    from behave.sim.behavior.extract import _closed_assignment_bindings
    from behave.sim.behavior.source_values import SourceValueBudget

    statement = ast.parse(declaration).body[0]
    budget = SourceValueBudget(100)
    assert _closed_assignment_bindings(
        statement.targets, statement.value, lambda _node: None,
        builtin_range=True, budget=budget) is None


def test_class_range_shadow_does_not_create_builtin_constants():
    from behave.sim.behavior.extract import ScopeResolver, source_model

    source = source_for('range = lambda n: (7, 8)\nIDLE, RUN = range(2)', class_scope=True)
    resolver = ScopeResolver(source_model(source))
    assert resolver.class_consts.get('RUN') != 1
    assert resolver.class_values.get('RUN') != 1


@pytest.mark.parametrize('class_scope', [False, True])
@pytest.mark.parametrize('prefix,suffix', [
    ('range = lambda n: (7, 8)\n', '\ndel range\n'),
    ('def shadow():\n    global range\n    range = lambda n: (7, 8)\nshadow()\n', ''),
])
def test_definition_time_builtin_ownership_includes_deleted_and_global_bindings(
        class_scope, prefix, suffix):
    from behave.sim.behavior.extract import ScopeResolver, source_model

    source = prefix + source_for('IDLE, RUN = range(2)', class_scope=class_scope) + suffix
    resolver = ScopeResolver(source_model(source))
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    assert (namespace['M'].RUN if class_scope else namespace['RUN']) == 8
    facts = resolver.class_consts if class_scope else resolver.model_consts
    assert facts.get('RUN') != 1


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', [
    source_for('IDLE, RUN = range(0, 4, 3)'),
    source_for('IDLE, RUN = range(0, 4, 3)', class_scope=True),
    '''class M:
    def reset(self): self.state = 0
    def process(self, a):
        table = {0: 1, 2: 3}
        self.state = table.get(a, a) | 4
        return {"y": self.state}
''',
])
def test_closed_binding_all_goals_query_and_native_replay(source):
    from behave.sim.behavior.instance import BehaviorInstance

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
def test_closed_binding_public_search_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''IDLE, MAGIC = 0, 37
class M:
    def reset(self): pass
    def process(self, a, b):
        table = {37: 7}
        if a == MAGIC:
            return {"y": table.get(a, b) | 0}
        return {"y": IDLE}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'{37: 7}', b'{37: 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

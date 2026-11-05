"""Scalar live ranges retain Python types, evaluation order and goal hits."""

import pytest

from behave.sim.symbolic import typecheck as types
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


SOURCES = [
    '''class M:
    def reset(self): pass
    def process(self, a):
        a = bool(a)
        value = a and a <= 9
        return {"y": int(value)}
''',
    '''class M:
    def reset(self): pass
    def process(self, a):
        original = a
        a = bool(a)
        boolean = a & True
        a = ~a
        a = bool(a + 1)
        return {"y": original + 4 * int(boolean) + 8 * int(a)}
''',
    '''class M:
    def reset(self): pass
    def process(self, a):
        value = a + 1
        value = bool(value & 1)
        if a & 2:
            value = not value
        for i in range(a):
            value = not value
        return {"y": int(value)}
''',
    '''class M:
    def reset(self): self.calls = 0
    def helper(self, a):
        self.calls += 1
        original = a
        a = bool(a)
        return original + int(a & True)
    def process(self, a):
        first = self.helper(a)
        second = self.helper(a ^ 3)
        return {"y": first + second + self.calls}
''',
    '''class M:
    def reset(self): pass
    def process(self, a):
        def current(): return int(a)
        before = current()
        a = bool(a)
        after = current()
        return {"y": 4 * before + after}
''',
    '''class M:
    def reset(self): self.calls = 0
    def flag(self, a):
        self.calls += 1
        return bool(a)
    def process(self, a):
        a = self.flag(a)
        return {"y": self.calls + int(a & True)}
''',
    '''class M:
    def reset(self): self.a = 2
    def process(self, a):
        original = a
        a = bool(a)
        self.a += int(a)
        return {"y": self.a + original}
''',
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES)
def test_consecutive_scalar_bindings_preserve_native_semantics(lowerer, source):
    proc = agrees(source, lowerer)
    assert types.type_errors(proc) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_declared_boolean_input_can_become_an_integer_local(lowerer):
    from behave.sim.symbolic import interp
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType, ValueType

    source = '''class M:
    def reset(self): pass
    def process(self, a):
        a = int(a)
        return {"y": a + 2}
'''
    signature = ProcedureSignature(
        (("a", ValueType.boolean()),), (("y", FiniteType(2)),))
    proc = lower_with_state_ranges(source, signature=signature, lowerer=lowerer)
    assert proc.types['a'] == types.BOOLEAN
    for a in (False, True):
        _, output, _, error = interp.run_step(proc, {'a': a})
        assert not error
        assert output.transactions == [{'y': int(a) + 2}]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_indexed_scalar_input_keeps_its_structured_storage(lowerer):
    from behave.sim.symbolic import interp
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.value_binding import SolverLeafPath, ValueBinding
    from behave.sim.core.contract import FiniteType

    source = '''class M:
    def reset(self): pass
    def process(self, a):
        a = a + 1
        return {"y": a}
'''
    binding = ValueBinding('a', FiniteType(2), (((), SolverLeafPath('word', (0,))),))
    signature = ProcedureSignature((("a", binding),), (("y", FiniteType(3)),))
    proc = lower_with_state_ranges(source, signature=signature, lowerer=lowerer)
    for a in range(4):
        _, output, _, error = interp.run_step(proc, {'a': a})
        assert not error
        assert output.transactions == [{'y': a + 1}]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('body', [
    'if a:\n            a = bool(a)',
    'for i in range(a):\n            a = bool(a)',
])
def test_a_single_branch_or_loop_cannot_change_the_enclosing_binding(lowerer, body):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        {body}
        return {{"y": int(a)}}
'''
    # Mixed-type joins still need a tagged representation; they must not read
    # a Boolean version on the path that kept the original integer.
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', [SOURCES[0], SOURCES[3], SOURCES[4]])
def test_rebound_scalar_all_goals_query_and_native_replay(source):
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
def test_rebound_scalar_public_search_closes_on_native_hit(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        original = a
        a = bool(a)
        if original == 37 and a:
            return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'"y": 7', b'"y": 6') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

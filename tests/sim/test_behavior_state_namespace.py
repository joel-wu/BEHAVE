"""Public arguments and same-spelled persistent fields are distinct values."""

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.value_binding import SolverLeafPath, ValueBinding
from behave.sim.core.contract import FiniteType, ValueType


SOURCES = [
    '''class M:
    def reset(self): self.a = 0
    def process(self, a):
        old = self.a
        self.a = a
        return {"y": old}
''',
    '''class M:
    def reset(self): self.a = 0
    def helper(self, a):
        old = self.a
        self.a = a
        return old
    def process(self, a):
        old = self.helper(a)
        a += 1
        return {"y": old + a}
''',
    '''class M:
    def reset(self): self.a = False
    def process(self, a):
        old = self.a
        self.a = a != 0
        return {"y": int(old) + a}
''',
    '''class M:
    def reset(self): self.a = [0, 0]
    def process(self, a):
        old = self.a[0] + self.a[1]
        self.a[0] = a
        self.a[1] = a + 1
        return {"y": old}
''',
    '''class M:
    def reset(self): self.a = "idle"
    def process(self, a):
        old = int(self.a == "run")
        self.a = "run" if a == 2 else "idle"
        return {"y": old + a}
''',
    '''class M:
    def reset(self): self.a = 0
    def process(self, a):
        y = 7 if self.a == 2 and a == 3 else 0
        self.a = a
        return {"y": y}
''',
]

LOOPS = [
    '''class M:
    def reset(self): self.a = [0]
    def process(self, a):
        old = self.a.pop()
        self.a.append(a)
        return {"y": old}
''',
    '''class M:
    def reset(self): self.a = []
    def process(self, a):
        self.a.append(a)
        return {"y": self.a[-1] + len(self.a)}
''',
    '''class M:
    def reset(self): self.a = 0
    def process(self, a):
        self.a = 0
        while self.a < a:
            self.a += 1
        return {"y": self.a}
''',
    '''class M:
    def reset(self): self.a = 0
    def helper(self, a):
        self.a = 0
        for k in range(a):
            self.a += 1
        return self.a
    def process(self, a):
        return {"y": self.helper(a)}
''',
    '''class M:
    def reset(self): self.a = 40
    def process(self, a):
        y = self.a if a == 3 else 0
        self.a = 40 + a
        return {"y": y}
''',
]

GROUPED = [
    '''class M:
    def reset(self):
        self.saved = 0
        self.flag = 0
    def process(self, a):
        self.saved, self.flag = a, 0
        if self.saved != 0:
            self.flag = 1
        return {"y": self.saved + self.flag}
''',
    '''class M:
    def reset(self): self.saved = 0
    def process(self, a):
        self.saved, self.saved = 0, a
        return {"y": 1 if self.saved else 0}
''',
    '''class M:
    def reset(self): self.saved = 0
    def pair(self, a): return (a, 1)
    def process(self, a):
        self.saved, flag = self.pair(a)
        return {"y": flag if self.saved else 0}
''',
    '''class M:
    def reset(self):
        self.idx = 0
        self.buf = [0, 0]
    def process(self, a):
        self.idx, self.buf[self.idx] = a % 2, a
        return {"y": self.buf[a % 2]}
''',
    '''class M:
    def reset(self): self.saved = 17
    def bump(self, a):
        self.saved = a
        return a
    def process(self, a):
        self.saved, ignored = self.saved, self.bump(a)
        return {"y": 1 if self.saved == 17 else 0}
''',
    '''class M:
    def reset(self): self.saved = 0
    def process(self, a):
        local, local = 0, a
        self.saved, ignored = local, 1
        return {"y": 1 if self.saved else 0}
''',
    '''class M:
    def reset(self): self.saved = 0
    def process(self, a):
        for k in range(3):
            self.saved, ignored = a, 0
        return {"y": self.saved}
''',
]


@pytest.mark.parametrize("source", SOURCES + LOOPS + GROUPED)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("typed", [False, True])
def test_input_and_persistent_state_keep_separate_contexts(source, lowerer, typed):
    signature = (ProcedureSignature(
        (("a", FiniteType(4)),), (("y", FiniteType(6)),)) if typed else None)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], signature=signature,
        bound=4, lowerer=lowerer)
    assert set(proc.inputs).isdisjoint(proc.state)
    assert proc.inputs == ["a"]
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    native = namespace["M"]()
    native.reset()
    trace = [{"a": a} for a in (2, 3, 0, 7)]
    state = None
    for inputs in trace:
        state, output, _hits, error = interp.run_step(proc, inputs, state)
        assert error == ""
        assert output.transactions == [native.process(**inputs)]
    assert model_audit(proc, namespace["M"], [trace]) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_structured_input_physical_leaf_does_not_alias_source_state(lowerer):
    source = '''class M:
    def reset(self): self.a = 0
    def process(self, row):
        old = self.a
        self.a = row["x"]
        return {"y": old}
'''
    row_type = ValueType.record({"x": ValueType.integer(FiniteType(4))})
    row = ValueBinding("row", row_type, ((("x",), SolverLeafPath("a")),))
    signature = ProcedureSignature((("row", row),), (("y", FiniteType(4)),))
    proc = lower_with_state_ranges(source, signature=signature, lowerer=lowerer, bound=2)
    assert set(proc.inputs).isdisjoint(proc.state)
    state, first, _, error = interp.run_step(proc, {"row": {"x": 3}})
    assert error == "" and first.transactions == [{"y": 0}]
    _, second, _, error = interp.run_step(proc, {"row": {"x": 7}}, state)
    assert error == "" and second.transactions == [{"y": 3}]


def test_state_alias_hazard_is_not_hidden_by_distinct_input_namespace():
    source = '''class M:
    def reset(self): self.a = [0, 0]
    def process(self, a):
        b = self.a
        b[0] = a
        return {"y": self.a[0]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"], bound=2)
    assert caught.value.code == "array-assigned-by-reference"


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_nested_unpacking_keeps_its_explicit_capability_boundary(lowerer):
    # Recursive source facts do not implement recursive executable unpacking.
    # Keep the existing refusal visible; do not silently flatten the targets.
    source = '''class M:
    def reset(self): self.saved = 0
    def process(self, a):
        first, (second, self.saved) = a, (a + 1, a + 2)
        return {"y": first + second + self.saved}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "destructuring-target-form"


@pytest.mark.isolation
@pytest.mark.solver
def test_history_goal_with_same_named_input_has_native_query_witness():
    from behave.sim.behavior.instance import BehaviorInstance

    source = SOURCES[-1]
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    goals = set(instance.goals())
    result = instance.query(goals, 2)
    assert not result.undecided
    assert {goal for goal in goals if goal.startswith("branch:")} <= set(result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("source", GROUPED)
def test_grouped_assignment_goal_witnesses_agree_with_native(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    assert result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_public_search_queries_unhit_state_branch_and_replays_result(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.a = 0
    def bump(self, b):
        self.a = b
        return b
    def process(self, a, b):
        self.a, ignored = a, self.bump(b)
        if self.a == 37:
            return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        left = slot["inputs"]["left"]["a"]
        right = slot["inputs"]["right"]["b"]
        return {"result": self.process(left, right)}
'''
    candidate = source.replace(b'{"y": 7}', b'{"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)

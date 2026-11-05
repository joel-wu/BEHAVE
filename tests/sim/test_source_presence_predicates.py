"""A stored presence predicate keeps its value, not a live alias to its test."""

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


PREDICATES = [
    ("self.pending is not None", "ready"),
    ("self.pending is None", "not ready"),
    ("self.pending is not None and self.other is not None", "ready"),
    ("self.pending is None or self.other is None", "not ready"),
]


def model(predicate, guard, *, mutation="", copy=""):
    return f'''class M:
    def reset(self):
        self.pending = None
        self.other = None
    def process(self, a):
        if a & 1:
            self.pending = a
            self.other = a + 2
        ready = {predicate}
{copy}{mutation}        if {guard}:
            return {{"y": self.pending + 1}}
        return {{"y": 0}}
'''


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("predicate,guard", PREDICATES)
def test_named_presence_predicate_matches_native(lowerer, predicate, guard):
    source = model(predicate, guard)
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": a} for a in values]
              for values in ((0, 0, 3, 4), (1, 7, 0, 2), (2, 4, 6, 8))]
    assert model_audit(proc, namespace["M"], traces) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_saved_presence_predicate_does_not_retest_a_mutated_payload(lowerer):
    source = model("self.pending is not None", "ready",
                   mutation='''        self.pending = None
        if ready and self.pending is None:
            return {"y": 7}
''')
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    assert model_audit(proc, namespace["M"], [[{"a": a}] for a in range(16)]) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_presence_selector_copy_survives_original_name_rebinding(lowerer):
    source = model("self.pending is not None", "saved",
                   copy="        saved = ready\n        ready = False\n")
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    assert model_audit(proc, namespace["M"], [[{"a": a}] for a in range(16)]) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("predicate,guard", PREDICATES)
def test_saved_predicate_queries_preserve_native_goal_hits(predicate, guard):
    from behave.sim.behavior.instance import BehaviorInstance

    source = model(predicate, guard)
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable
    assert not result.undecided
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_named_presence_public_search_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.pending = None
    def process(self, a, b):
        self.pending = a if a == 37 else None
        ready = self.pending is not None
        if ready:
            return {"y": self.pending - 30}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'pending - 30', b'pending - 31') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("payload,other", [
    ("a", "0"), ("a", "False"), ("a", "None"),
    ("[a, 1]", "[0, 1]"), ("[a, 1]", "(0, 1)"),
    ('{"v": a}', '{"v": 0}'),
    ('{"v": a}', '{"other": 0}'),
    ('{"v": a, "w": 1}', '{"w": True, "v": 0}'),
    ('{"v": [a, 1]}', '{"v": [False, True]}'),
    ('{"v": {"w": a}}', '{"v": {"w": 0}}'),
])
def test_optional_equality_preserves_absence_and_python_carriers(lowerer, payload, other):
    source = f'''class M:
    def reset(self): self.pending = None
    def process(self, a):
        before = self.pending == {other}
        different = {other} != self.pending
        self.pending = {payload} if a < 8 else None
        return {{"y": int(before) + 2 * int(different)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": a} for a in values]
              for values in ((0, 0, 1, 8), (8, 1, 8, 0), (7, 0, 9, 15))]
    assert model_audit(proc, namespace["M"], traces) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("payload", ["a", "[a, 1]", '{"v": a}'])
def test_two_optional_values_compare_tags_and_payloads(lowerer, payload):
    source = f'''class M:
    def reset(self):
        self.left = None
        self.right = None
    def process(self, a):
        same = self.left == self.right
        different = self.left != self.right
        self.left = {payload} if a & 1 else None
        self.right = {payload} if a & 2 else None
        return {{"y": int(same) + 2 * int(different)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": a} for a in values]
              for values in ((0, 1, 2, 3), (3, 0, 2, 1), (1, 3, 7, 0))]
    assert model_audit(proc, namespace["M"], traces) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("payload,other", [("a", "0"), ('{"v": a}', '{"v": 0}')])
def test_optional_comparison_captures_left_before_effectful_right(lowerer, payload, other):
    source = f'''class M:
    def reset(self): self.pending = None
    def clear(self):
        self.pending = None
        return {other}
    def process(self, a):
        same = self.pending == self.clear()
        self.pending = {payload} if a < 8 else None
        return {{"y": int(same)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": a} for a in values]
              for values in ((0, 0, 1, 8), (8, 1, 8, 0))]
    assert model_audit(proc, namespace["M"], traces) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_record_equality_uses_values_not_insertion_order(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        left = {"v": [a, 1], "w": {"x": 0}}
        right = {"w": {"x": False}, "v": [False, True]}
        return {"y": int(left == right) + 2 * int(left != right)}
'''
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=["y"],
                                   bound=1, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    assert model_audit(proc, namespace["M"], [[{"a": a}] for a in range(8)]) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("payload,other", [
    ("a", "0"), ('{"v": a}', '{"v": False}'),
    ('{"v": [a, 1]}', '{"v": [False, True]}'),
])
def test_optional_equality_goal_queries_match_native_history(payload, other):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self): self.pending = None
    def process(self, a):
        same = self.pending == {other}
        self.pending = {payload} if a < 8 else None
        if same:
            return {{"y": 1}}
        return {{"y": 2}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    verdict = instance.query(instance.goals(), 4)
    assert verdict.reachable
    assert not verdict.undecided
    assert instance.source_audit(tuple(verdict.reachable.values())).status.value == "agreed"


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_optional_record_state_join_does_not_reuse_reset_absence(lowerer):
    source = '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        old = 0 if self.pending is None else self.pending["v"][1]
        self.pending = {"v": [a, a + 1]} if a < 8 else None
        return {"y": old}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": a} for a in values]
              for values in ((0, 1, 8, 0), (7, 8, 2, 3))]
    assert model_audit(proc, namespace["M"], traces) == []

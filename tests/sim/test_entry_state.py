"""The proof boundary for state created by the first step."""

import pytest

from behave.sim.behavior import audit, entry_state
from behave.sim.symbolic import cfg, ir, typecheck as types
from behave.sim.behavior.lowering import (
    _Lowerer,
    lower_with_state_ranges,
)


def _proc(body, *, state=("m",), kinds=None, original_graph=True):
    proc = ir.Procedure(state, ["a"], [], [], body,
                        types=({name: types.VALUE for name in state}
                               if kinds is None else kinds))
    if original_graph:
        ir._bind_control_flow(
            proc, ir._make_control_flow_authority(cfg.of_command(body), body))
    return proc


def test_an_unconditional_first_definition_makes_the_entry_value_dead():
    body = ir.Seq(ir.Assign("m", ir.Var("a")),
                  ir.Assign("y", ir.Var("m")))
    proc = _proc(body)
    got = entry_state.plan(proc, {"m"})
    assert got.entry_dead == ("m",)
    assert dict(got.values) == {"m": 0}
    assert got.unsafe == ()


def test_a_term_oracle_uses_the_same_rule_conservatively():
    body = ir.Seq(ir.Assign("m", ir.Var("a")),
                  ir.Assign("y", ir.Var("m")))
    proc = _proc(body, original_graph=False)
    assert entry_state.plan(proc, {"m"}).entry_dead == ("m",)


def test_a_read_before_the_first_definition_is_not_filled():
    body = ir.Seq(ir.Assign("y", ir.Var("m")),
                  ir.Assign("m", ir.Var("a")))
    got = entry_state.plan(_proc(body), {"m"})
    assert got.values == ()
    assert got.unsafe == ("m",)


def test_a_store_reads_the_old_array_before_writing_one_cell():
    body = ir.Store("m", ir.Lit(0), ir.Var("a"))
    proc = _proc(body, kinds={"m": types.Array(2)})
    assert entry_state.plan(proc, {"m"}).unsafe == ("m",)


def test_a_definition_is_required_on_every_normal_exit():
    body = ir.If(ir.Var("a"), ir.Assign("m", ir.Lit(1)), ir.Skip())
    assert entry_state.plan(_proc(body), {"m"}).unsafe == ("m",)


def test_a_failure_path_needs_no_successor_value():
    body = ir.If(ir.Var("a"), ir.Fail("DivZero"),
                 ir.Assign("m", ir.Lit(1)))
    got = entry_state.plan(_proc(body), {"m"})
    assert got.entry_dead == ("m",)
    assert got.unsafe == ()


def test_a_domain_pruned_path_is_not_a_normal_exit():
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry", [], cfg.Branch(ir.Var("a"), "dead", "live")),
        "dead": cfg.Block("dead", [], cfg.Pruned()),
        "live": cfg.Block("live", [ir.Assign("m", ir.Var("a"))],
                          cfg.Return()),
    }, "entry")
    proc = _proc(ir.Skip(), original_graph=False)
    ir._bind_control_flow(
        proc, ir._make_control_flow_authority(graph, proc.body))
    got = entry_state.plan(proc, {"m"})
    assert got.entry_dead == ("m",)
    assert got.unsafe == ()


def test_a_canonical_array_follows_the_inferred_recursive_type():
    body = ir.Assign("m", ir.Op(
        "arraylit", ir.Op("arraylit", ir.Lit(1), ir.Lit(2)),
        ir.Op("arraylit", ir.Lit(3), ir.Lit(4))))
    proc = _proc(body, kinds={"m": types.Array(2, types.Array(2))})
    got = entry_state.plan(proc, {"m"})
    assert dict(got.values) == {"m": [[0, 0], [0, 0]]}
    # Each nested array is a value, not a repeated Python alias.
    assert got.values[0][1][0] is not got.values[0][1][1]


def test_a_safe_field_without_an_inferred_type_is_named_separately():
    proc = _proc(ir.Assign("m", ir.Var("a")), kinds={})
    assert entry_state.plan(proc, {"m"}).untyped == ("m",)


def _lower_both(source, **kwargs):
    return [
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer,
            **kwargs)
        for lowerer in (None, _Lowerer)
    ]


def _class_of(source):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    return namespace["M"]


def test_a_step_created_scalar_is_audited_not_assumed():
    source = """\
class M:
    def reset(self):
        self.n = 3
    def process(self, a):
        self.m = a
        return {"y": self.n + self.m}
"""
    cls = _class_of(source)
    for proc in _lower_both(source, bound=2):
        assert proc.entry_dead == ("m",)
        assert proc.derived().entry_dead == ("m",)
        assert proc.guessed_initial == ()
        assert proc.initial["m"] == 0
        assert audit.model_audit(
            proc, cls, [[{"a": 2}, {"a": 7}]]) == []


def test_a_step_created_array_uses_its_inferred_shape():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        self.buf = [a, a + 1]
        return {"y": self.buf[0] + self.buf[1]}
"""
    cls = _class_of(source)
    for proc in _lower_both(source, bound=2):
        assert proc.entry_dead == ("buf",)
        assert proc.initial["buf"] == [0, 0]
        assert proc.types["buf"] == types.Array(2)
        assert audit.model_audit(
            proc, cls, [[{"a": 1}, {"a": 4}]]) == []


def test_a_range_pruned_return_does_not_owe_an_entry_dead_definition():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        if a < 0:
            return {"y": 0}
        self.x = a
        return {"y": self.x}
"""
    for proc in _lower_both(
            source, input_bounds={"a": (0, 15)}, bound=2):
        assert proc.entry_dead == ("x",)
        assert proc.initial["x"] == 0


@pytest.mark.parametrize("body", [
    "self.m = self.m + a\n        return {'y': self.m}",
    "if a:\n            self.m = a\n        return {'y': a}",
    ("if a:\n            return {'y': a}\n        self.m = a\n"
    "        return {'y': self.m}"),
])
def test_an_observable_absent_field_is_represented_and_audited(body):
    source = ("class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              f"        {body}\n")
    for lowerer in (None, _Lowerer):
        proc = lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
        assert audit.model_audit(proc, _class_of(source), [
            [{"a": 0}], [{"a": 1}], [{"a": 1}, {"a": 0}]]) == []


def test_the_canonical_filler_is_not_a_range_seed():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        self.m = a + 5
        return {"y": self.m}
"""
    for proc in _lower_both(
            source, input_bounds={"a": (0, 3)}, bound=2):
        assert proc.initial["m"] == 0
        assert proc.state_ranges["m"].lo == 5
        assert proc.state_ranges["m"].hi == 8

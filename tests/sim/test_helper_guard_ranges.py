"""Range guards on copied helper parameters also constrain their live source."""

import itertools

import pytest

from behave.sim.symbolic import cfg, ir, range_relations, ranges
from behave.sim.symbolic.interp import run_step
from behave.sim.behavior.lowering import lower_procedure, plan_state


SOURCE = '''def valid(value):
    return -7 <= value and value <= 7
class M:
    def reset(self):
        self.x = 1
    def process(self, a):
        candidate = self.x * self.x + a
        if valid(candidate):
            self.x = candidate
        return {"y": self.x}
'''


@pytest.mark.parametrize("bound", [1, 4, 20])
def test_helper_guard_has_the_same_state_envelope_as_the_inline_guard(bound):
    inline = SOURCE.replace("valid(candidate)", "-7 <= candidate and candidate <= 7")
    direct = plan_state(inline, input_widths=[3], bound=bound).env["x"]
    helper = plan_state(SOURCE, input_widths=[3], bound=bound).env["x"]
    assert helper == direct == ranges.Range(1, 7)


@pytest.mark.parametrize("changed", [None, "original", "saved"])
def test_copy_guard_refinement_never_constrains_an_overwritten_value(changed):
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (
            ir.Assign("saved", ir.Var("original")),
            *((ir.Assign(changed, ir.Lit(100)),) if changed else ()),
        ), cfg.Goto("test")),
        "test": cfg.Block("test", (), cfg.Branch(
            ir.Op("le", ir.Var("saved"), ir.Lit(3)), "yes", "no")),
        "yes": cfg.Block("yes", (), cfg.Return()),
        "no": cfg.Block("no", (), cfg.Fail("not-selected")),
    }, "entry")
    after = ranges.graph_post(graph, {"original": ranges.Range(0, 8)})
    if changed == "saved":
        assert after is ranges._NO_NORMAL
    else:
        expected = ranges.Range(100, 100) if changed else ranges.Range(0, 3)
        assert after["original"] == expected


def test_a_copy_on_only_one_predecessor_is_not_an_equality_after_the_join():
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (), cfg.Branch(ir.Var("flag"), "copy", "other")),
        "copy": cfg.Block("copy", (ir.Assign("saved", ir.Var("original")),), cfg.Goto("test")),
        "other": cfg.Block("other", (ir.Assign("saved", ir.Lit(0)),), cfg.Goto("test")),
        "test": cfg.Block("test", (), cfg.Branch(
            ir.Op("le", ir.Var("saved"), ir.Lit(3)), "yes", "no")),
        "yes": cfg.Block("yes", (), cfg.Return()),
        "no": cfg.Block("no", (), cfg.Fail("not-selected")),
    }, "entry")
    after = ranges.graph_post(graph, {"original": ranges.Range(0, 8), "flag": ranges.Range(0, 1)})
    assert after["original"] == ranges.Range(0, 8)


def test_copy_chain_propagation_is_independent_of_names_and_insertion_order():
    for names in itertools.permutations(("x", "y", "z")):
        facts = range_relations.copy({}, names[0], names[1])
        facts = range_relations.copy(facts, names[1], names[2])
        env = {name: ranges.Range(0, 9) for name in names}
        env[names[0]] = ranges.Range(2, 3)
        assert set(range_relations.refine_env(env, facts).values()) == {ranges.Range(2, 3)}


def test_new_envelope_contains_every_small_concrete_execution():
    proc = lower_procedure(SOURCE, input_widths=[3])
    envelope = plan_state(SOURCE, input_widths=[3], bound=4).env["x"]
    namespace = {}
    exec(SOURCE, namespace)
    for inputs in itertools.product(range(8), repeat=3):
        state = dict(proc.initial)
        model = namespace["M"]()
        model.reset()
        for value in inputs:
            state, _outputs, _hits, error = run_step(proc, {"a": value}, state)
            model.process(value)
            assert error == ""
            assert state["x"] == model.x
            assert envelope.lo <= state["x"] <= envelope.hi

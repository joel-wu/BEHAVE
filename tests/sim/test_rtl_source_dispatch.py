"""A balanced selector preserves complete domains, events and failure prefixes."""

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.rtl.source_scheduler import _action_dispatch

pytestmark = pytest.mark.solver


def _actions(count):
    return [(ir.Op("eq", ir.Var("enable"), ir.Lit(bool(index % 2))),
             ir._sequence([ir.Mark(f"hit:{index}"),
                           ir.Fail("AssertionError", "terminal") if index == 2 else ir.Skip(),
                           ir.Assign("value", ir.Lit(index + 1))]))
            for index in range(count)]


def _procedure(dispatch):
    return ir.Procedure(("value", "accepted"), ("action", "enable"), (), (),
        ir.Seq(ir.Assign("accepted", ir.Lit(False)), dispatch),
        types={"value": types.INTEGER, "action": types.INTEGER,
               "accepted": types.BOOLEAN, "enable": types.BOOLEAN})


@pytest.mark.parametrize("count", [1, 2, 3, 7, 32])
def test_balanced_and_linear_dispatch_have_identical_symbolic_results(count):
    actions = _actions(count)
    linear = ir.Skip()
    for index, (enabled, body) in reversed(tuple(enumerate(actions))):
        linear = ir.If(ir.Op("eq", ir.Var("action"), ir.Lit(index)),
            ir.If(enabled, ir.Seq(ir.Assign("accepted", ir.Lit(True)), body), ir.Skip()), linear)
    env = {"value": ranges.Range(0, 1023), "action": ranges.Range(0, 511),
           "accepted": ranges.Range(0, 1), "enable": ranges.Range(0, 1)}
    old = encoder.encode_step(_procedure(linear), env=env, tag="linear")
    new = encoder.encode_step(_procedure(_action_dispatch(actions)), env=env,
                              pre=old.pre, tag="balanced")
    solver = z3.Solver()
    solver.add(old.constraint, new.constraint)
    solver.add(z3.Or(old.ok != new.ok, old.error != new.error,
        *(old.post[name] != new.post[name] for name in ("value", "accepted")),
        *(old.hits[goal] != new.hits[goal] for goal in old.hits)))
    assert solver.check() == z3.unsat


def test_hundreds_of_actions_keep_shallow_ir_and_every_unused_code():
    count = 259
    proc = _procedure(_action_dispatch(_actions(count)))
    ir.validate_procedure_representation(proc, max_depth=32)
    for selected in range(512):
        for enable in (False, True):
            post, _outputs, hits, error = interp.run_step(proc,
                {"action": selected, "enable": enable}, {"value": 777, "accepted": True})
            active = selected < count and enable == bool(selected % 2)
            assert post["accepted"] is active
            assert hits == ({f"hit:{selected}", "terminal"} if active and selected == 2 else
                            {f"hit:{selected}"} if active else set())
            assert bool(error) is (active and selected == 2)
            assert post["value"] == (selected + 1 if active and selected != 2 else 777)

"""The analysis pass, run without the lowering that consumes it.

M2's circularity: how far a data-dependent loop unrolls can only be decided once the
ranges are known, the ranges are a property of the step, and the step cannot be built
until the loops are unrolled. There is no order of one pass that works, so there are two,
and `plan_state` is the first of them.

Every test here calls `plan_state` and nothing else, which is what M2 asks for: a pass
whose test does not construct the passes around it. What it produces used to be a bare
dict handed straight to the second lowering inside one function, so there was nothing to
test and nowhere to say where the input ranges came from.
"""
import pickle

import pytest

from behave.sim.symbolic import plan as encoding_plan
from behave.sim.behavior import step_alias
from behave.sim.symbolic import ranges
from behave.sim.behavior.lowering import (
    StatePlan,
    lower_with_state_ranges,
    plan_state,
)

#: A state variable the analysis can settle: masked, so the range is closed under the
#: step rather than growing by one each beat.
MASKED = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        self.n = (self.n + 1) & 7
        return {"y": self.n}
"""

#: The same counter without the mask.  Its guard gives the range transfer an exact
#: inductive upper bound, so the unbounded-time plan closes at five.
GUARDED = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        if self.n < 5:
            self.n = self.n + 1
        return {"y": self.n}
"""

#: Nothing here for the rough pass to work with: the loop bound is an input, and with no
#: contract the width is all there is.
LOOP = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        for i in range(a):
            self.n = (self.n + 1) & 7
        return {"y": self.n}
"""


@pytest.mark.parametrize('bound', [None, 3])
@pytest.mark.parametrize('informed', [False, True])
def test_repr_counts_known_and_unknown_ranges_without_lowerer_state(bound, informed):
    plan = StatePlan({'x': ranges.Range(0, 7)}, {}, informed, bound,
                     universe=('x', 'unknown'))
    how = 'contract' if informed else 'widths'
    over = 'no bound' if bound is None else '3 steps'
    assert repr(plan) == f'StatePlan(1 settled, 1 unknown, {how}, {over})'


def test_a_settled_state_variable_gets_a_range():
    """The answer the second pass cannot do without: a loop bound naming `self.n` is
    resolvable only because this pass says how large `n` gets."""
    plan = plan_state(MASKED, input_widths=[8])
    assert isinstance(plan, StatePlan)
    assert plan.env["n"].lo == 0 and plan.env["n"].hi == 7


@pytest.mark.parametrize("operation", ["100 // a", "100 % a"])
@pytest.mark.parametrize("bound", [None, 2])
def test_successful_postcondition_cannot_narrow_the_next_input_domain(operation, bound):
    from behave.sim.symbolic.interp import run_step

    source = f'''class M:
    def reset(self):
        self.x = 0
    def process(self, a):
        self.x = self.x + 1
        y = {operation}
        return {{"y": y}}
'''
    plan = plan_state(source, input_widths=[3], bound=bound)
    assert plan.env["a"] == ranges.Range(0, 7)
    proc = lower_with_state_ranges(source, input_widths=[3], bound=bound)
    state, _outputs, _hits, error = run_step(proc, {"a": 0})
    assert error == "DivZero"
    assert state["x"] == 1
    assert run_step(proc, {"a": 1})[3] == ""


def test_a_guarded_state_variable_gets_its_inductive_range():
    """The transfer follows the guard, so ``[0,5]`` is closed under the step."""
    plan = plan_state(GUARDED, input_widths=[8])
    assert plan.env["n"] == ranges.Range(0, 5)
    assert plan.ranges["n"] == ranges.Range(0, 5)


def test_state_plan_serializes_a_total_known_or_unknown_result_map():
    plan = plan_state(GUARDED, input_widths=[8])
    image = plan.ranges.as_dict()

    assert set(image) >= {"a", "n"}
    assert image["n"] == {"kind": "range", "lo": 0, "hi": 5}
    restored = ranges.AbstractRangeMap.from_dict(image)
    assert restored == plan.ranges
    assert restored.known == plan.env
    escaped = restored["a"]
    escaped.lo = -1
    assert restored["a"] == plan.env["a"], \
        "a detached Range view cannot mutate the authoritative result map"


def test_the_total_result_cannot_lose_a_declared_key_through_a_projection():
    plan = plan_state(GUARDED, input_widths=[8])
    assert {"a", "n"} <= set(plan.ranges.universe)

    projected = plan.ranges.known
    del projected["a"]
    assert plan.ranges["a"] == ranges.Range(0, 255)
    assert plan.ranges["n"] == ranges.Range(0, 5)

    with pytest.raises(TypeError):
        del plan.ranges["n"]
    with pytest.raises(AttributeError, match="immutable"):
        plan.ranges._values = {}


@pytest.mark.parametrize("compressed", [False, True])
def test_total_range_map_retains_detached_nested_cell_products(compressed):
    low, high = ranges.Range(-7, -1), ranges.Range(2, 9)
    row = ranges._CellRange((low, high))
    product = (ranges._UniformCellRange(10**18, row, [(7, high)])
               if compressed else ranges._CellRange((row, row)))
    result = ranges.AbstractRangeMap(("cells", "absent"), {"cells": product})
    # The source ranges and every exported view are separate from authority.
    low.lo = -1000
    assert result.known == {"cells": ranges.Range(-7, 9)}
    first = result.products["cells"]
    nested = first.default if compressed else first.cells[0]
    nested.cells[0].lo = -2000
    second = result.products["cells"]
    assert (second.default if compressed else second.cells[0]).cells[0].lo == -7
    assert result["absent"] is ranges.UNKNOWN
    image = result.as_dict()
    for restored in (ranges.AbstractRangeMap.from_dict(image),
                     pickle.loads(pickle.dumps(result))):
        assert restored == result
        assert restored.products == result.products
    if compressed:
        assert second.length == 10**18 and len(second.runs) == 1
    else:
        assert second.cells[0] is second.cells[1]


def test_product_containment_cannot_use_only_the_scalar_hull():
    from behave.sim.symbolic import ir

    product = ranges._CellRange((ranges.Range(0, 0), ranges.Range(3, 3)))
    proc = ir.Procedure(["cells"], [], [], [],
                        ir.Store("cells", ir.Lit(0), ir.Lit(3)),
                        initial={"cells": [0, 3]})
    assert ranges.unproved(proc, {"cells": product}) == ["cells"]
    identity = ir.Procedure(["cells"], [], [], [], ir.Skip(),
                           initial={"cells": [3, 0]})
    assert ranges.unproved(identity, {"cells": product}) == ["cells"]


def test_product_export_serialization_scales_with_shared_structure():
    product = ranges.Range(0, 1)
    for _ in range(24):
        product = ranges._CellRange((product, product))
    result = ranges.AbstractRangeMap(("cells",), {"cells": product})
    image = result.as_dict()
    row = image["cells"]
    for _ in range(24):
        assert row["cells"][0] is row["cells"][1]
        row = row["cells"][0]
    packet = pickle.dumps(result)
    assert len(packet) < 5000
    assert pickle.loads(packet) == result
    cyclic = {"kind": "cells", "cells": []}
    cyclic["cells"].append(cyclic)
    with pytest.raises(ValueError, match="cycle"):
        ranges.AbstractRangeMap.from_dict({"cells": cyclic})


@pytest.mark.parametrize("bound", [None, 1, 4])
def test_state_plan_keeps_separate_signed_array_regions(bound):
    source = '''class M:
    def reset(self):
        self.cells = [0, 0]
    def process(self, a):
        self.cells[0] = -a
        self.cells[1] = a
        return {"y": self.cells[1]}
'''
    plan = plan_state(source, input_widths=[3], bound=bound)
    assert plan.env["cells"] == ranges.Range(-7, 7)
    product = plan.ranges.products["cells"]
    assert ranges._cell_at(product, 0) == ranges.Range(-7, 0)
    assert ranges._cell_at(product, 1) == ranges.Range(0, 7)


def test_a_total_result_refuses_facts_outside_or_unknown_in_its_known_projection():
    with pytest.raises(ValueError, match="outside"):
        ranges.AbstractRangeMap(("x",), {"ghost": ranges.Range(0, 0)})
    with pytest.raises(TypeError):
        ranges.AbstractRangeMap(("x",), {"x": ranges.UNKNOWN})


def test_input_ranges_come_from_the_contract_when_there_is_one():
    plan = plan_state(MASKED, input_widths=[8], input_bounds={"a": (0, 3)})
    assert plan.informed
    assert plan.inputs["a"].lo == 0 and plan.inputs["a"].hi == 3


def test_input_ranges_come_from_the_width_when_there_is_not():
    """The weaker of the two runs, and the plan says so rather than leaving it to be
    inferred from which arguments a caller happened to pass. Which loop bounds resolve
    depends on this, so a certificate resting on the second pass rests on it."""
    plan = plan_state(MASKED, input_widths=[8])
    assert not plan.informed
    assert plan.inputs["a"].hi == 255


def test_the_contract_is_carried_forward_and_not_only_used_here():
    """The input contract determines the retained loop's encoding maximum."""
    wide = plan_state(LOOP, input_widths=[8])
    tight = plan_state(LOOP, input_widths=[8], input_bounds={"a": (0, 3)})
    assert wide.env["a"].hi == 255 and tight.env["a"].hi == 3
    assert "i" not in wide.env and "i" not in tight.env, \
        "a zero-trip step does not define its loop target"

    wide_proc = lower_with_state_ranges(LOOP, input_widths=[8])
    tight_proc = lower_with_state_ranges(
        LOOP, input_widths=[8], input_bounds={"a": (0, 3)})
    wide_encoding = encoding_plan.plan_encoding(
        wide_proc, wide_proc.state_ranges)
    tight_encoding = encoding_plan.plan_encoding(
        tight_proc, tight_proc.state_ranges)
    assert tuple(wide_encoding.loop_bounds.values()) == (255,)
    assert tuple(tight_encoding.loop_bounds.values()) == (3,)


def test_two_lowering_passes_share_identical_alias_analysis(monkeypatch):
    source = """class M:
    def reset(self):
        self.buf = [0]

    def process(self, a):
        self.buf[0] = a
        return {"y": self.buf[0]}
"""
    original = step_alias.step_alias_hazard
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(step_alias, "step_alias_hazard", counted)
    lower_with_state_ranges(source, input_widths=[2], bound=2)

    assert calls == [1]


def test_identity_state_cover_does_not_execute_the_combinational_body(
        monkeypatch):
    source = """class M:
    def reset(self):
        self.pending = 0

    def process(self, a):
        rows = []
        for i in range(16):
            rows.append(a + i)
        return {"y": rows[15]}
"""

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("an identity state transition needs no post pass")

    monkeypatch.setattr(ranges, "bounded", should_not_run)
    plan = plan_state(source, input_widths=[4], bound=64)

    assert plan.env["a"] == ranges.Range(0, 15)
    assert plan.ranges["pending"] is ranges.UNKNOWN


def test_a_step_the_rough_pass_cannot_lower_keeps_its_input_carrier():
    """A rough-pass failure withholds state facts, not task-owned input types.

    The second pass decides whether the unsupported expression is admissible, but it
    must not lose the width/range of ``a`` while doing so.
    """
    plan = plan_state("class M:\n    def process(self, a):\n"
                      "        return {'y': sorted([a])[0]}\n", input_widths=[8])
    assert plan.env == {"a": ranges.Range(0, 255)}


def test_a_rough_pass_failure_marks_each_declared_state_field_unknown():
    source = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        return {"y": mystery(a) + self.n}
"""
    plan = plan_state(source, input_widths=[8])

    assert plan.env == {"a": ranges.Range(0, 255)}
    assert plan.ranges["n"] is ranges.UNKNOWN
    assert {"a", "n"} <= set(plan.ranges.universe)


def test_a_plan_keeps_the_contract_it_was_given_even_when_the_rough_pass_fails():
    plan = plan_state("class M:\n    def process(self, a):\n"
                      "        return {'y': sorted([a])[0]}\n",
                      input_widths=[8], input_bounds={"a": (0, 3)})
    assert plan.informed and plan.inputs["a"].hi == 3
    assert plan.env["a"] == plan.inputs["a"]

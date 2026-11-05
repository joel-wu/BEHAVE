"""Adversarial checks for abstract facts carried by control-flow exits.

These tests deliberately exercise the source-to-IR boundary rather than the
standalone CFG utilities.  Every case is checked through both the production
graph lowerer and the recovered term lowerer.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

_LOWERERS = (
    pytest.param(None, id="graph"),
    pytest.param(_Lowerer, id="term"),
)

_ERROR_CODES = {
    "ZeroDivisionError": "DivZero",
    "ValueError": "NegativeShift",
    "IndexError": "IndexError",
    "KeyError": "KeyError",
}


def _native(source: str, a: int):
    namespace: dict[str, object] = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        result = model.process(a)
    except Exception as exc:  # noqa: BLE001
        return None, _ERROR_CODES[type(exc).__name__]
    rows = [result] if isinstance(result, Mapping) else result
    return [dict(row) for row in rows], ""


def _lowered(source: str, a: int, lowerer):
    procedure = lower_with_state_ranges(
        source,
        input_widths=[2],
        outputs=["y"],
        lowerer=lowerer,
    )
    _state, output, _hits, error = interp.run_step(procedure, {"a": a})
    rows = None if error else [dict(row) for row in output.transactions]
    return rows, error


@pytest.mark.parametrize("lowerer", _LOWERERS)
def test_multiple_helper_returns_join_their_exit_facts(lowerer):
    source = """\
class M:
    def reset(self):
        self.buf = [3, 4]

    def choose(self, a):
        if a:
            return 1
        return 2

    def process(self, a):
        selected = self.choose(a)
        return {"y": 10 * self.buf[0] + selected}
"""
    for a in range(4):
        assert _lowered(source, a, lowerer) == _native(source, a)


@pytest.mark.parametrize("lowerer", _LOWERERS)
def test_failure_exit_does_not_erase_facts_on_the_normal_path(lowerer):
    source = """\
class M:
    def reset(self):
        self.buf = [3, 4]

    def process(self, a):
        quotient = 8 // a
        return {"y": self.buf[0] + quotient}
"""
    for a in range(4):
        assert _lowered(source, a, lowerer) == _native(source, a)


@pytest.mark.parametrize("lowerer", _LOWERERS)
def test_infeasible_normal_arm_does_not_reach_the_loop_tail(lowerer):
    source = """\
class M:
    def reset(self):
        self.buf = [3, 4]

    def process(self, a):
        for i in range(1):
            if i == 0:
                break
            dead = self.buf[9]
        return {"y": self.buf[0] + a}
"""
    for a in range(4):
        assert _lowered(source, a, lowerer) == _native(source, a)


@pytest.mark.parametrize("lowerer", _LOWERERS)
@pytest.mark.parametrize("record", (False, True), ids=("scalar", "record"))
def test_infeasible_conditional_expression_arm_is_not_lowered(lowerer, record):
    expression = (
        '{"y": self.buf[9]} if i != 0 else {"y": self.buf[0]}'
        if record else
        'self.buf[9] if i != 0 else self.buf[0]'
    )
    tail = "return selected" if record else 'return {"y": selected + a}'
    source = f"""\
class M:
    def reset(self):
        self.buf = [3, 4]

    def process(self, a):
        for i in range(1):
            selected = {expression}
        {tail}
"""
    for a in range(4):
        assert _lowered(source, a, lowerer) == _native(source, a)


@pytest.mark.parametrize("lowerer", _LOWERERS)
def test_goal_sites_after_a_terminator_remain_explicitly_false(lowerer):
    source = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}
        if a:
            return {"y": 1}
        return {"y": 2}
"""
    procedure = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"], lowerer=lowerer)
    _state, output, hits, error = interp.run_step(procedure, {"a": 1})
    assert [dict(row) for row in output.transactions] == [{"y": 1}]
    assert error == ""
    assert hits == {"location:behavior.py:6:8:0:entry"}
    # The live return is reached.  Every goal below it remains in the encoded
    # catalogue behind a false guard, rather than disappearing with dead code.
    declared = set(ir.goals_of(procedure.body))
    assert hits <= declared
    assert len(declared - hits) == 5


@pytest.mark.parametrize("lowerer", _LOWERERS)
@pytest.mark.parametrize("control", ("break", "continue"))
def test_loop_control_exits_preserve_facts_at_normal_completion(
        lowerer, control):
    source = f"""\
class M:
    def reset(self):
        pass

    def process(self, a):
        values = [1, 2]
        for i in range(2):
            if a:
                {control}
            values[0] += 1
        return {{"y": values[0]}}
"""
    for a in range(4):
        assert _lowered(source, a, lowerer) == _native(source, a)


def test_nested_retained_loop_keeps_a_path_conditioned_trip_range():
    source = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        total = a
        for outer in range(4):
            if outer < 3:
                for inner in range(outer + 1, 4):
                    total += inner
        return {"y": total}
"""
    for a in range(4):
        assert _lowered(source, a, None) == _native(source, a)

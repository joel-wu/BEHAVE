"""Focused adversarial contracts at the Python-to-IR boundary."""

from __future__ import annotations

import pytest

from behave.sim.symbolic import interp
from behave.sim.symbolic import typecheck as value_types
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges


def _native(source, **inputs):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    result = model.process(**inputs)
    return result if isinstance(result, list) else [result]


def _lowered(source, *, widths, bounds=None, **inputs):
    proc = lower_with_state_ranges(
        source,
        input_widths=widths,
        input_bounds=bounds,
        outputs=["y"],
    )
    _state, output, _hits, error = interp.run_step(proc, inputs)
    assert error == ""
    return [dict(row) for row in output.transactions]


def _assert_agrees(source, cases, *, widths, bounds=None):
    for inputs in cases:
        assert _lowered(source, widths=widths, bounds=bounds, **inputs) \
            == _native(source, **inputs)


def test_readonly_dict_get_retains_recursive_value_and_default_contracts():
    source = """\
ROWS = {0: (2, 3), 1: (5, 7)}

def row(i):
    return ROWS.get(i, (11, 13))

class M:
    def reset(self):
        pass
    def process(self, a):
        values = row(a)
        return {"y": values[0] + values[1]}
"""

    _assert_agrees(
        source,
        [{"a": 0}, {"a": 1}, {"a": 2}],
        widths=[2],
    )


def test_static_keys_prune_unselected_table_representations():
    source = """\
VALUES = {0: (2, 3), 1: 7}

class M:
    def reset(self):
        pass
    def process(self, a):
        row = VALUES.get(0, a)
        direct = VALUES[1]
        missing = VALUES.get(9, a)
        return {"y": row[0] + row[1] + direct + missing}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=[2])


def test_readonly_table_keeps_nested_list_and_tuple_carriers_distinct():
    source = """\
VALUES = {0: [1, 2], 1: (1, 2)}

class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(VALUES[a & 1] == (1, 2))}
    """

    assert [_native(source, a=value)[0]["y"] for value in (0, 1)] == [0, 1]
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert caught.value.code == "source-value-contract-disagrees"

    # Once the selector is fixed, the source carrier is fixed too and no tag is
    # needed in the IR value.
    fixed = source.replace("VALUES[a & 1]", "VALUES[0]")
    _assert_agrees(fixed, [{"a": 0}, {"a": 1}], widths=[2])


def test_mixed_rom_carriers_do_not_block_a_complete_scalar_path():
    source = """\
VALUES = {0: [2, 3], 1: (5, 7)}

class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": VALUES[a & 1][0]}
"""

    _assert_agrees(
        source, [{"a": 0}, {"a": 1}], widths=[2])


def test_dynamic_reversed_range_target_is_scalar():
    source = """\
def identity(value):
    return value

class M:
    def reset(self):
        pass
    def process(self, n):
        total = 0
        for i in reversed(range(n)):
            total += identity(i)
        return {"y": total}
"""

    _assert_agrees(
        source,
        [{"n": 0}, {"n": 1}, {"n": 3}],
        widths=[2],
        bounds={"n": (0, 3)},
    )


def test_literal_destructuring_keeps_position_specific_contracts():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        scalar, vector = (a, [2, 3])
        return {"y": scalar + vector[0] + vector[1]}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=[2])


def test_helper_destructuring_refuses_a_heterogeneous_return_contract():
    source = """\
def split(a):
    return a, [2, 3]

class M:
    def reset(self):
        pass
    def process(self, a):
        scalar, vector = split(a)
        return {"y": scalar + vector[0] + vector[1]}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    # A heterogeneous helper result still has no common array element shape.
    # The indexed read owns that refusal after finite-value planning.
    assert caught.value.code == "subscript-length-not-static"


def test_subscript_destructuring_snapshots_a_dynamic_swap():
    source = """\
class M:
    def reset(self):
        self.cells = [10, 20]
    def process(self, a):
        i = a & 1
        j = (a >> 1) & 1
        self.cells[i], self.cells[j] = self.cells[j], self.cells[i]
        return {"y": 100 * self.cells[0] + self.cells[1]}
"""

    _assert_agrees(source, [{"a": value} for value in range(4)], widths=[2])


def test_subscript_destructuring_evaluates_one_helper_rhs_once():
    source = """\
class M:
    def reset(self):
        self.cells = [10, 20]
        self.calls = 0
    def pair(self):
        self.calls += 1
        return self.cells[1], self.cells[0]
    def process(self, a):
        self.cells[0], self.cells[1] = self.pair()
        return {"y": 10000 * self.calls + 100 * self.cells[0] + self.cells[1]}
"""

    _assert_agrees(source, [{"a": 0}], widths=[1])


def test_subscript_destructuring_writes_repeated_targets_left_to_right():
    source = """\
class M:
    def reset(self):
        self.cells = [1, 2]
    def process(self, a):
        i = a & 1
        self.cells[i], self.cells[i] = 7, 9
        return {"y": 100 * self.cells[0] + self.cells[1]}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=[1])


def test_nested_subscript_destructuring_rebuilds_each_path():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
    def process(self, a):
        self.rows[0][1], self.rows[1][0] = self.rows[1][0], self.rows[0][1]
        return {"y": (1000 * self.rows[0][0] + 100 * self.rows[0][1]
                      + 10 * self.rows[1][0] + self.rows[1][1])}
"""

    _assert_agrees(source, [{"a": 0}], widths=[1])


def test_subscript_destructuring_swaps_whole_rows():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
    def process(self, a):
        i = a & 1
        j = (a >> 1) & 1
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        return {"y": (1000 * self.rows[0][0] + 100 * self.rows[0][1]
                      + 10 * self.rows[1][0] + self.rows[1][1])}
"""

    _assert_agrees(source, [{"a": value} for value in range(4)], widths=[2])


def test_effectful_subscript_targets_are_refused_before_losing_order():
    source = """\
class M:
    def reset(self):
        self.cells = [1, 2]
        self.index = 0
    def next_index(self):
        result = self.index
        self.index += 1
        return result
    def process(self, a):
        self.cells[self.next_index()], self.cells[self.next_index()] = 7, 9
        return {"y": 1000 * self.index + 100 * self.cells[0] + self.cells[1]}
"""

    assert _native(source, a=0) == [{"y": 2709}]
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1], outputs=["y"])
    assert caught.value.code == "subscript-target-effectful"


def test_functions_named_range_and_reversed_keep_helper_semantics():
    source = """\
def range(_bound):
    return (1, 2)

def reversed(_value):
    return (3, 4)

class M:
    def reset(self):
        pass
    def process(self, a):
        total = a
        for value in range(a):
            total += value
        for value in reversed(a):
            total += value
        return {"y": total}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=[2])


def test_syntax_only_nested_declaration_carries_type_not_initial_value():
    source = """\
def identity(value):
    return value

class M:
    def reset(self):
        self.rows = [
            [identity(2), identity(3)],
            [identity(5), identity(7)],
        ]
    def process(self, a):
        row = self.rows[a & 1]
        return {"y": row[0] + row[1]}
"""

    # Syntax proves the representation; the executable construction boundary
    # independently supplies the exact recursive value.
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert proc.types["rows"] == value_types.Array(2, value_types.Array(2))
    assert proc.initial["rows"] == [[2, 3], [5, 7]]
    assert proc.guessed_initial == ()
    assert proc.entry_dead == ()

    proc = lower_with_state_ranges(
        source,
        input_widths=[2],
        outputs=["y"],
        read_initial={"rows": [[2, 3], [5, 7]]},
    )
    assert proc.guessed_initial == ()
    assert [interp.run_step(proc, {"a": value})[1].transactions[0]["y"]
            for value in (0, 1)] == [5, 12]

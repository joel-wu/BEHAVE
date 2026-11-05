"""Native/IR checks for source control and binding semantics."""

from __future__ import annotations

import textwrap
from collections.abc import Mapping

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.lowering import (
    LoweringError,
    _Lowerer,
    lower_with_state_ranges,
)


def _source(body, inputs="a"):
    body = textwrap.indent(textwrap.dedent(body).strip(), " " * 8)
    return (
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        f"    def process(self, {inputs}):\n"
        f"{body}\n"
    )


def _native(source, inputs):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        result = model.process(*inputs)
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    if result is None:
        return [], ""
    if isinstance(result, Mapping):
        return [dict(result)], ""
    return [dict(row) for row in result], ""


def _lowered(source, inputs, widths=None, lowerer=None, outputs=None):
    widths = widths or [4] * len(inputs)
    proc = lower_with_state_ranges(
        source, input_widths=widths, outputs=outputs or ["y"], lowerer=lowerer)
    values = dict(zip(proc.inputs, inputs))
    _state, out, _hits, error = interp.run_step(proc, values)
    return (None if error else [dict(row) for row in out.transactions], error)


def _agrees(source, rows, widths=None, outputs=None):
    for row in rows:
        expected = _native(source, row)
        assert _lowered(source, row, widths, outputs=outputs) == expected
        assert _lowered(
            source, row, widths, _Lowerer, outputs=outputs) == expected


def _agrees_without_input_widths(source, rows, outputs=None):
    """Exercise structural proofs without a schema-derived finite range."""
    for lowerer in (None, _Lowerer):
        proc = lower_with_state_ranges(
            source, outputs=outputs or ["y"], lowerer=lowerer)
        for row in rows:
            expected = _native(source, row)
            values = dict(zip(proc.inputs, row))
            _state, out, _hits, error = interp.run_step(proc, values)
            got = (None if error else
                   [dict(item) for item in out.transactions], error)
            assert got == expected


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_an_earlier_recursive_output_alias_cannot_hide_a_later_mutation(
        lowerer):
    source = """\
class M:
    def reset(self):
        self.x = [2]
    def bump(self):
        self.x[0] += 1
        return self.x
    def process(self, a):
        return [{"y": self.x}, {"y": self.bump()}]
"""
    # Python records two references to one list and the later mutation is
    # visible through both. SolverIR arrays have value semantics, so admitting
    # this source would silently produce [2] for the first transaction.
    assert _native(source, (0,)) == ([{"y": [3]}, {"y": [3]}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "array-assigned-by-reference"


def test_assignment_and_branch_cap_a_structural_loop_without_input_widths():
    source = _source("""
        n = a & 255
        if n > 16:
            n = 16
        y = 0
        for i in range(n):
            y += i
        return {"y": y}
    """)
    _agrees_without_input_widths(source, [(-3,), (0,), (7,), (99,)])


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_source_loop_span_breaks_the_state_range_planning_cycle(lowerer):
    source = """\
class M:
    def reset(self):
        self.n = 0
    def process(self, a):
        incoming = a & 7
        effective = incoming if incoming < self.n else self.n
        if effective < 4:
            count = effective + 1
        else:
            count = 4
        total = 0
        for i in range(count):
            total += i
        self.n = count
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, outputs=["y"], lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (7, 6, 2, 0, 7):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_bounded_state_range_sizes_a_comprehension_over_a_state_array(lowerer):
    source = """\
class M:
    def reset(self):
        self.data = [0] * 4
        self.count = 0
    def process(self, a):
        seq = [self.data[i] for i in range(self.count)]
        if a and self.count < 4:
            seq.append(a)
        updated = self.data[:]
        for i in range(len(seq)):
            updated[i] = seq[i]
        self.data = updated
        self.count = len(seq)
        return {"y": seq[0] if seq else 0}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer, bound=8)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (3, 5, 0, 7, 1, 0):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_helper_call_preserves_a_bounded_scalar_state_range(lowerer):
    source = """\
class M:
    def reset(self):
        self.data = [0] * 4
        self.count = 0
    def mask(self, value):
        return value & 7
    def process(self, a):
        seq = [self.data[i] for i in range(self.count)]
        if a and self.count < 4:
            seq.append(a)
        updated = self.data[:]
        for i in range(len(seq)):
            updated[i] = seq[i]
        self.data = updated
        self.count = len(seq)
        return {"y": self.mask(seq[0] if seq else 0)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer, bound=8)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (3, 5, 0, 7, 1, 0):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_dynamic_sequence_slice_uses_its_logical_length(lowerer):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a, b, c):
        seq = [i + 10 for i in range(a & 7)]
        lo = (b & 7) - 4
        hi = (c & 7) - 2
        cut = seq[lo:hi]
        return {"y": len(cut) * 100 + (cut[0] if cut else 0)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3, 3, 3], outputs=["y"], lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    for values in (
            (0, 0, 0), (1, 0, 7), (3, 4, 7), (5, 2, 6),
            (7, 7, 7), (7, 0, 0), (6, 5, 3)):
        _state, out, _hits, error = interp.run_step(
            proc, dict(zip(("a", "b", "c"), values)))
        assert not error
        assert out.transactions == [native.process(*values)]


def test_if_expression_upper_bound_uses_python_trip_count_semantics():
    source = _source("""
        n = a if a < 8 else 8
        y = 0
        for i in range(n):
            y += i + 1
        return {"y": y}
    """)
    _agrees_without_input_widths(source, [(-9,), (0,), (7,), (8,), (100,)])


def test_if_expression_bounds_a_range_with_nonzero_start():
    source = _source("""
        start = 0 if a == 24 else 12
        y = 0
        for i in range(start, 24):
            y += i
        return {"y": y}
    """)
    _agrees_without_input_widths(source, [(0,), (23,), (24,), (99,)])


def test_branch_assumption_reaches_an_inlined_helper_parameter():
    source = """\
class M:
    def reset(self):
        pass
    def total(self, n):
        y = 0
        for i in range(n):
            y += i
        return y
    def process(self, a):
        y = 0
        if a < 4:
            y = self.total(a)
        return {"y": y}
"""
    _agrees_without_input_widths(source, [(-2,), (0,), (3,), (4,), (30,)])


def test_a_normal_subscript_guard_can_bound_a_later_loop():
    source = _source("""
        xs = [0] * 4
        xs[a] = 1
        y = 0
        for i in range(a + 4):
            y += i
        return {"y": y}
    """)
    _agrees_without_input_widths(source, [(-5,), (-4,), (-1,), (0,), (3,), (4,)])


def test_a_conditional_guard_does_not_leak_to_the_other_arm():
    source = _source("""
        xs = [0] * 4
        if a:
            xs[b] = 1
        y = 0
        for i in range(b + 4):
            y += i
        return {"y": y}
    """, inputs="a, b")
    for lowerer in (None, _Lowerer):
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, outputs=["y"], lowerer=lowerer)
        assert caught.value.code == "loop-bound-unbounded"


def test_a_later_guard_cannot_justify_an_earlier_loop():
    source = _source("""
        y = 0
        for i in range(a):
            y += i
        xs = [0] * 4
        xs[a] = 1
        return {"y": y}
    """)
    for lowerer in (None, _Lowerer):
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, outputs=["y"], lowerer=lowerer)
        assert caught.value.code == "loop-bound-unbounded"


def test_chained_scalar_assignment_evaluates_rhs_once():
    source = _source("""
        x = y = a + 1
        x += 2
        return {"y": x * 10 + y}
    """)
    _agrees(source, [(0,), (3,), (7,)])


def test_chained_assignment_to_state_and_local_is_ordered():
    source = _source("""
        self.x = y = a
        self.x += 1
        return {"y": self.x * 10 + y}
    """)
    _agrees(source, [(0,), (3,), (7,)])


def test_chained_mutable_assignment_is_explicitly_outside_value_array_ir():
    source = _source("""
        x = y = [a, 0]
        x[1] = 7
        return {"y": y[1]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "multiple-array-assignment"


def test_chained_assignment_cannot_hide_an_array_behind_a_helper_call():
    source = """\
class M:
    def reset(self):
        pass
    def pair(self, a):
        return [a, a + 1]
    def process(self, a):
        x = y = self.pair(a)
        x[0] = 9
        return {"y": y[0]}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "multiple-array-assignment"


def test_for_continue_skips_only_the_rest_of_its_iteration():
    source = _source("""
        y = 0
        for i in range(6):
            if i < a:
                continue
            y += i
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (3,), (7,)])


def test_continue_and_break_target_the_innermost_loop():
    source = _source("""
        y = 0
        for i in range(3):
            for j in range(4):
                if j < a:
                    continue
                if j == 3:
                    break
                y += 10 * i + j
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (2,), (4,)])


def test_while_continue_retests_the_condition():
    source = _source("""
        i = 0
        y = 0
        while i < 5:
            i += 1
            if i < a:
                continue
            y += i
        return {"y": y}
    """)
    _agrees(source, [(0,), (2,), (4,), (7,)])


def test_continue_outside_a_loop_is_not_admitted_by_the_parser():
    with pytest.raises(SyntaxError):
        compile(_source("continue"), "behavior.py", "exec")


def test_exactly_empty_loop_preserves_the_entering_target_binding():
    source = _source("""
        row = (1, 2)
        for row in []:
            pass
        return {"y": row[1] + (a & 0)}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def test_scalar_builtins_are_exact_on_bounded_integers():
    source = _source("""
        x = a - 8
        return {"y": abs(x) * 100 + int(bool(x)) * 10 + x.bit_length()}
    """)
    _agrees(source, [(0,), (7,), (8,), (15,)])


def test_not_and_bool_use_a_dynamic_sequence_logical_length():
    source = """\
class M:
    def reset(self):
        pass
    def make(self, a):
        xs = [i for i in range(1) if a]
        return xs
    def process(self, a):
        xs = self.make(a)
        return {"y": 10 * int(bool(xs)) + int(not xs)}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_helper_result_index_uses_its_logical_length():
    source = """\
class M:
    def reset(self):
        pass
    def make(self, a):
        xs = []
        if a:
            xs.append(7)
        return xs
    def process(self, a):
        return {"y": self.make(a)[0]}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_captured_dynamic_sequence_keeps_length_before_effectful_index():
    source = """\
class M:
    def reset(self):
        self.k = 0
    def index(self):
        self.k += 1
        return 0
    def process(self, a):
        xs = [7 for i in range(1) if a]
        return {"y": xs[self.index()]}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_helper_result_membership_and_list_copy_use_logical_length():
    source = """\
class M:
    def reset(self):
        pass
    def make(self, a):
        xs = []
        if a:
            xs.append(0)
        return xs
    def process(self, a):
        ys = list(self.make(a))
        return {"y": 10 * int(0 in ys) + int(bool(ys))}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_conditional_expression_joins_sequence_value_and_length():
    source = _source("""
        xs = [7 for i in range(1) if a]
        ys = [8 for i in range(1) if a > 1]
        z = xs if a < 2 else ys
        return {"y": z[0]}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_dynamic_sequence_concatenation_compacts_before_fixed_suffix():
    source = _source("""
        xs = [7 for i in range(1) if a]
        return {"y": (xs + [2])[0]}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def test_statement_branch_assigns_one_stable_sequence_length_companion():
    source = _source("""
        xs = [7 for i in range(1) if a]
        ys = [8 for i in range(1) if a >= 0]
        if a < 2:
            z = xs
        else:
            z = ys
        return {"y": z[0]}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_statement_branch_defines_length_for_fixed_and_built_sequences():
    source = _source("""
        if a < 2:
            values = [i + 1 for i in range(4)]
        else:
            values = [5, 6, 7, 8]
        total = 0
        for i in range(len(values)):
            total += values[i]
        return {"y": total}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_fixed_comprehension_preserves_recursive_iteration_contracts():
    source = _source("""
        rows = [row + [3] for row in [[1, 2], [4, 5]]]
        return {"y": rows[0][2] * 10 + rows[1][0] + (a & 0)}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def test_filtered_comprehension_preserves_recursive_iteration_contracts():
    source = _source("""
        rows = [row for row in [[1, 2], [3, 4]] if row[0] <= a]
        return {"y": rows[0][1]}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_comprehension_cannot_concatenate_different_sequence_carriers():
    source = _source("""
        rows = [row + [3] for row in [(1, 2)]]
        return {"y": rows[0][0] + (a & 0)}
    """)
    assert _native(source, (0,))[1] == "TypeError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code in {
        "sequence-element-shape-disagrees",
        "sequence-value-contract-disagrees",
        "source-value-contract-disagrees",
    }


def test_numeric_unary_operator_on_a_sequence_is_typed_refusal():
    source = _source('return {"y": -[a]}')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "unary-sequence-unsupported"


def test_direct_return_payloads_follow_source_evaluation_order():
    source = """\
class M:
    def reset(self):
        self.x = 1
    def bump(self):
        self.x += 1
        return self.x
    def process(self, a):
        return {"y": self.x, "z": self.bump() + (a & 0)}
"""
    _agrees(source, [(0,), (7,)], outputs=["y", "z"])


def test_helper_product_return_follows_source_evaluation_order():
    source = """\
class M:
    def reset(self):
        self.x = 1
    def bump(self):
        self.x += 1
        return self.x
    def pair(self):
        return self.x, self.bump()
    def process(self, a):
        y, z = self.pair()
        return {"y": y + (a & 0), "z": z}
"""
    _agrees(source, [(0,), (7,)], outputs=["y", "z"])


def test_batch_literal_payloads_follow_source_evaluation_order():
    source = """\
class M:
    def reset(self):
        self.x = 1
    def bump(self):
        self.x += 1
        return self.x
    def process(self, a):
        out = [{"y": self.x + (a & 0)}, {"y": self.bump()}]
        return out
"""
    _agrees(source, [(0,), (7,)])


def test_shadowed_range_keeps_its_source_iteration_semantics():
    source = """\
def range(n):
    return [0, 1, 2]

class M:
    def reset(self):
        pass
    def process(self, a):
        xs = []
        for i in range(1):
            xs.append(i + (a & 0))
        return {"y": len(xs)}
"""
    _agrees(source, [(0,), (7,)])


@pytest.mark.parametrize("spelling, iterable", [
    ("range", "range(2)"),
    ("reversed", "reversed([1, 2])"),
])
def test_comprehension_target_cannot_be_reinterpreted_as_a_builtin(
        spelling, iterable):
    source = _source(f"""
        xs = [y for {spelling} in [0] for y in {iterable}]
        return {{"y": len(xs)}}
    """)
    assert _native(source, (0,))[1] == "TypeError"
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])


def test_bit_count_uses_the_integer_magnitude():
    source = _source('return {"y": (a - 128).bit_count()}')
    _agrees(source, [(0,), (1,), (127,), (128,), (255,)], widths=[8])


def test_construction_only_scalar_remains_fixed_when_reset_reads_it():
    source = """\
class M:
    def __init__(self, width=8):
        self.width = width
        self.reset()
    def reset(self):
        self.buf = [0 for _ in range(self.width)]
    def process(self, a):
        return {"y": (self.width - 1).bit_length() + (a & 0)}
"""
    _agrees(source, [(0,), (3,), (7,)])


def test_list_copies_a_fixed_value_array():
    source = _source("""
        xs = [a, a + 1, a + 2]
        ys = list(xs)
        ys[0] = 9
        return {"y": xs[0] * 10 + ys[0]}
    """)
    _agrees(source, [(0,), (3,), (7,)])


def test_sum_of_a_fixed_array_and_start_value():
    source = _source("""
        xs = [a, a + 1, a + 2]
        return {"y": sum(xs, 5)}
    """)
    _agrees(source, [(0,), (3,), (7,)])


def test_sum_consumes_a_bounded_generator_with_filters_in_order():
    source = _source("""
        y = sum(i * 10 + j for i in range(3) for j in range(2)
                if i + j < a)
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (2,), (4,)])


def test_membership_in_fixed_sequences_and_closed_mapping_keys():
    source = (
        "TABLE = {2: 20, 5: 50}\n" +
        _source('return {"y": 100 * int(a in (1, 3, 5)) + '
                '10 * int(a not in [2, 4]) + int(a in TABLE)}'))
    _agrees(source, [(0,), (1,), (2,), (3,), (5,), (7,)])


def test_large_finite_membership_has_logarithmic_representation_depth():
    values = ", ".join(str(value) for value in range(300))
    source = _source(
        f'return {{"y": int(a in ({values},))}}')
    _agrees(source, [(0,), (127,), (299,), (300,)], widths=[9])


def test_large_uniform_mapping_subscript_uses_a_finite_rom():
    entries = ", ".join(f"{value}: {value ^ 0x55}"
                        for value in range(256))
    source = f"TABLE = {{{entries}}}\n" + _source(
        'return {"y": TABLE[a]}')
    _agrees(source, [(0,), (127,), (255,)], widths=[8])


def test_static_sequence_selected_in_a_loop_keeps_its_source_call_contract():
    source = """\
ROWS = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]
class M:
    def reset(self):
        pass
    def pick(self, rows, index):
        return rows[index][index]
    def process(self, a):
        total = 0
        for i in range(2):
            total += self.pick(ROWS[i], a & 1)
        return {"y": total}
"""
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_membership_uses_the_runtime_length_not_sequence_padding():
    source = _source("""
        xs = []
        if a > 1:
            xs.append(0)
        if a > 2:
            xs.append(a)
        return {"y": 10 * int(a in xs) + int(0 in xs)}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_closed_table_get_returns_a_flat_tuple_payload():
    source = (
        "TABLE = {-2: (11, 2), 3: (17, 4)}\n" +
        _source("""
            x, n = TABLE.get(a - 2, (5, 1))
            return {"y": 100 * x + n}
        """))
    _agrees(source, [(0,), (1,), (2,), (5,), (7,)])


def test_closed_table_payloads_must_have_one_flat_shape():
    source = (
        "TABLE = {0: (1, 2), 1: (3, (4, 5))}\n" +
        _source('x, y = TABLE.get(a, (0, 0)); return {"y": x + y}'))
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "table-value-shape-unsupported"


def test_closed_table_get_default_must_match_the_payload_shape():
    source = (
        "TABLE = {0: (1, 2), 1: (3, 4)}\n" +
        _source('x, y = TABLE.get(a, 0); return {"y": x + y}'))
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "table-default-shape-mismatch"


@pytest.mark.parametrize("returned", ["[a]", "[a, a + 1, a + 2]"])
def test_destructuring_requires_the_exact_source_length(returned):
    source = f"""\
class M:
    def reset(self):
        pass
    def values(self, a):
        return {returned}
    def process(self, a):
        x, y = self.values(a)
        return {{"y": 10 * x + y}}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "destructuring-length"


def test_destructuring_refuses_a_runtime_logical_length():
    source = _source("""
        values = []
        if a:
            values.append(a)
        x, y = values
        return {"y": 10 * x + y}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "destructuring-length-not-static"


def test_destructuring_evaluates_rhs_values_left_to_right():
    source = """\
class M:
    def reset(self):
        self.t = [1]
    def replace(self):
        self.t = [9]
        return [0]
    def process(self, a):
        x, y = self.t, self.replace()
        return {"y": 10 * x[0] + y[0]}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_destructuring_attribute_target_writes_state_not_same_named_local():
    source = """\
class M:
    def reset(self):
        self.x = 0
    def process(self, a):
        x = 5
        self.x, y = (a, 0)
        return {"y": 10 * self.x + x + y}
"""
    _agrees(source, [(1,), (3,), (7,)])


def test_nested_closed_tables_are_selected_without_nested_ir_arrays():
    source = (
        "TABLE = {0: {0: 7, 1: 8}, 2: {0: 11, 1: 12}}\n" +
        _source('return {"y": TABLE[a & 2][(a >> 1) & 1]}'))
    _agrees(source, [(0,), (1,), (2,), (3,), (6,), (7,)])


def test_nested_static_sequences_keep_path_dependent_lengths():
    source = (
        "TABLE = {1: [5], 2: [7, 9]}\n" +
        _source("""
            key = 1 if a < 2 else 2
            index = a & 1
            return {"y": 100 * len(TABLE[key]) + TABLE[key][index]}
        """))
    _agrees(source, [(0,), (1,), (2,), (3,), (6,), (7,)])


def test_dense_mapping_miss_is_keyerror_not_indexerror():
    source = "TABLE = {0: 3, 1: 5}\n" + _source('return {"y": TABLE[a]}')
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_optional_output_local_is_a_zero_or_one_transaction_batch():
    source = _source("""
        result = None
        if a & 1:
            result = {"y": a + 3}
        return result
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_output_local_can_hold_a_finite_literal_batch():
    source = _source("""
        result = None
        if a > 1:
            result = [{"y": a}, {"y": a + 1}]
        return result
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_output_batch_record_alias_captures_values_at_assignment():
    source = _source("""
        x = a
        record = {"y": x}
        x = x + 9
        return [record, record]
    """)
    _agrees(source, [(0,), (1,), (3,), (7,)])


def test_optional_output_batch_may_contain_a_record_alias():
    source = _source("""
        result = None
        if a > 1:
            first = {"y": a}
            result = [first, {"y": a + 1}]
        return result
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_optional_output_may_be_one_captured_record():
    source = _source("""
        result = None
        if a > 1:
            record = {"y": a}
            result = record
        return result
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_reused_growing_local_may_have_one_larger_backing_capacity():
    source = _source("""
        rows = []
        values = []
        for i in range(2):
            values.append(a + i)
        rows.append({"value": values[1]})
        values = []
        for i in range(3):
            values.append(a + i)
        rows.append({"value": values[2]})
        return rows
    """)
    _agrees(source, [(0,), (1,), (3,), (7,)], outputs=["value"])


def test_output_mapping_built_by_fixed_loop_has_one_static_field_set():
    source = _source("""
        out = {}
        for i in range(3):
            out[f"y{i}"] = a + i
        return out
    """)
    outputs = ["y0", "y1", "y2"]
    for value in (0, 1, 3, 7):
        expected = _native(source, (value,))
        assert _lowered(source, (value,), outputs=outputs) == expected
        assert _lowered(
            source, (value,), outputs=outputs, lowerer=_Lowerer) == expected


def test_output_mapping_fields_must_exist_on_every_return_path():
    source = _source("""
        out = {}
        if a:
            out["y0"] = a
        out["y1"] = a + 1
        return out
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4])
    assert caught.value.code == "outputs-disagree-across-returns"


def test_output_mapping_switches_fields_from_a_branch_local():
    source = _source("""
        out = {"y0": 0, "y1": 0}
        if a & 1:
            selected = 0
        else:
            selected = 1
        out[f"y{selected}"] = a + 7
        return out
    """)
    outputs = ["y0", "y1"]
    for value in (0, 1, 2, 7):
        expected = _native(source, (value,))
        assert _lowered(source, (value,), outputs=outputs) == expected
        assert _lowered(
            source, (value,), outputs=outputs, lowerer=_Lowerer) == expected


def test_output_mapping_has_nested_finite_selector_product():
    source = _source("""
        out = {}
        for i in range(2):
            for j in range(2):
                out[f"y{i}{j}"] = a + 10 * i + j
        return out
    """)
    outputs = ["y00", "y01", "y10", "y11"]
    for value in (0, 3, 7):
        expected = _native(source, (value,))
        assert _lowered(source, (value,), outputs=outputs) == expected
        assert _lowered(
            source, (value,), outputs=outputs, lowerer=_Lowerer) == expected


def test_output_mapping_tracks_static_selector_reassignment():
    source = _source("""
        out = {}
        for i in range(2):
            selected = i + 2
            out[f"y{selected}"] = a + selected
        return out
    """)
    outputs = ["y2", "y3"]
    for value in (0, 3, 7):
        expected = _native(source, (value,))
        assert _lowered(source, (value,), outputs=outputs) == expected
        assert _lowered(
            source, (value,), outputs=outputs, lowerer=_Lowerer) == expected


def test_output_mapping_does_not_guess_a_data_dependent_field_set():
    source = _source("""
        out = {}
        out[f"y{a}"] = a
        return out
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y0", "y1"])
    assert caught.value.code == "output-name-not-static"


def test_data_dependent_write_does_not_make_every_possible_field_definite():
    source = _source("""
        out = {"y0": 0}
        index = a & 1
        out[f"y{index}"] = a
        return out
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y0", "y1"])
    assert caught.value.code == "outputs-disagree-across-returns"


def test_flat_sequence_concatenation_preserves_list_and_tuple_semantics():
    list_source = _source("""
        left = [a, a + 1]
        right = left[1:] + [a + 2]
        return {"y": right[0] * 10 + right[1]}
    """)
    tuple_source = _source("""
        left = (a, a + 1)
        right = left[1:] + (a + 2,)
        return {"y": right[0] * 10 + right[1]}
    """)
    _agrees(list_source, [(0,), (2,), (7,)])
    _agrees(tuple_source, [(0,), (2,), (7,)])


def test_bounded_comprehension_can_feed_an_ordinary_sequence_expression():
    source = _source("""
        count = a & 3
        xs = [i + 1 for i in range(count)] + [7]
        return {"y": 10 * len(xs) + xs[len(xs) - 1]}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_proved_empty_comprehension_needs_no_element_contract():
    source = """\
TABLE = {0: [], 1: [3]}

class M:
    def __init__(self):
        self.mode = 0
    def reset(self):
        pass
    def process(self, a):
        outputs = [{"y": a}]
        outputs += [{"y": item} for item in TABLE[self.mode]]
        return outputs
"""
    _agrees(source, [(0,), (7,)])


def test_growing_transaction_list_can_be_extended_by_comprehensions():
    source = """\
TABLE = {0: [], 1: [3]}

class M:
    def __init__(self):
        self.mode = 0
        self.tail = 2
    def reset(self):
        pass
    def process(self, a):
        outputs = [{"y": a}]
        for i in range(2):
            outputs.append({"y": a + i + 1})
        outputs += [{"y": item} for item in TABLE[self.mode]]
        outputs += [{"y": 9} for _ in range(self.tail)]
        return outputs
"""
    _agrees(source, [(0,), (7,)])


@pytest.mark.parametrize("expression", [
    "[a, a + 1] * 3",
    "3 * [a, a + 1]",
    "(a, a + 1) * 3",
    "[a] * -2",
])
def test_flat_sequence_repetition_has_python_value_semantics(expression):
    source = _source(f"""
        values = {expression}
        return {{"y": sum(values)}}
    """)
    _agrees(source, [(0,), (2,), (7,)])


def test_bounded_sequence_repetition_carries_its_logical_length():
    source = _source("""
        count = (a & 3) - 1
        values = [a + 1, a + 2] * count
        total = 0
        for i in range(4):
            if i < len(values):
                total = total * 10 + values[i]
        return {"y": len(values) * 100000 + total}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


@pytest.mark.parametrize("expression", [
    "[self.record(a)] * self.count()",
    "self.count() * [self.record(a)]",
])
def test_record_sequence_repetition_evaluates_each_operand_once_in_order(
        expression):
    source = f"""\
class M:
    def reset(self):
        self.s = 0
    def record(self, a):
        self.s += 1
        return {{"x": a + self.s, "z": self.s}}
    def count(self):
        self.s += 10
        return 2
    def process(self, a):
        rows = {expression}
        selected = rows[1]
        return {{"y": selected["x"] + 100 * self.s,
                 "z": selected["z"]}}
"""
    _agrees(source, [(0,), (2,), (7,)], outputs=["y", "z"])


def test_record_sequence_repetition_carries_a_runtime_length():
    source = _source("""
        rows = [{"x": a, "z": a + 1}] * ((a & 3) - 1)
        return {"y": len(rows), "z": rows[0]["z"] if rows else 0}
    """)
    _agrees(
        source, [(0,), (1,), (2,), (3,), (7,)], outputs=["y", "z"])


def test_record_sequence_repetition_refuses_observable_shared_mutation():
    source = _source("""
        rows = [{"x": 0}] * 2
        rows[0]["x"] = a
        return {"y": rows[1]["x"]}
    """)
    assert _native(source, (7,)) == ([{"y": 7}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_bounded_source_state_prunes_a_helper_unreachable_in_the_horizon(
        lowerer):
    source = """\
class M:
    def reset(self):
        self.remaining = 4
    def later(self, a):
        return {"y": a + 100}
    def process(self, a):
        if self.remaining > 0:
            rows = [{"y": a + 1}] * 2
            out = rows[self.remaining & 1]
            self.remaining -= 1
        else:
            out = self.later(a)
        return out
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer, bound=2)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (2, 5):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_source_branch_plan_prunes_a_correlated_unreachable_helper_call(
        lowerer):
    source = """\
class M:
    def __init__(self):
        self.wait = 15
        self.delay = 2
        self.total = 27
        self.reset()
    def reset(self):
        self.remaining = self.total
    def out(self, value):
        return {"y": value}
    def init_output(self):
        step = self.total - self.remaining
        first = self.wait + 1 + self.delay
        second = first + 1 + self.delay
        if step == self.wait:
            return self.out(1)
        if step == first or step == second:
            return self.out(2)
        return self.out(3)
    def process(self, a):
        result = self.init_output()
        self.remaining -= 1
        return {"y": result["y"] + (a & 0)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer, bound=1)
    state, out, _hits, error = interp.run_step(proc, {"a": 5})
    assert not error
    assert state is not None
    assert out.transactions == [{"y": 3}]


def test_output_planning_keeps_numeric_facts_for_a_conditional_scalar_value():
    source = _source("""
        values = [a] * (a & 1)
        scale = (a if a else 7) * 256
        return [{"y": value + scale} for value in values]
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_bounded_repetition_compacts_a_dynamic_source_sequence():
    source = _source("""
        base = []
        for i in range(3):
            if i < (a & 3):
                base.append(i + 1)
        values = base * (b & 3)
        total = 0
        for i in range(9):
            if i < len(values):
                total = total * 10 + values[i]
        return {"y": len(values) * 1000000000 + total}
    """, inputs="a, b")
    _agrees(source, [(0, 3), (1, 2), (2, 3), (3, 1), (7, 3)],
            widths=[3, 3])


def test_finite_table_values_bound_a_sequence_repetition():
    source = """\
COUNTS = {0: 1, 1: 3}

class M:
    def reset(self):
        pass
    def process(self, a):
        count = COUNTS[a & 1]
        values = [a + 1] * count
        return {"y": len(values) * 100 + sum(values)}
"""
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_nested_mutable_sequence_repetition_is_an_explicit_alias_boundary():
    source = _source("""
        values = [[0]] * 2
        values[0][0] = a
        return {"y": values[1][0]}
    """)
    assert _native(source, (7,)) == ([{"y": 7}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-repeat-alias-unsupported"


def test_sequence_repetition_accepts_recursively_immutable_tuple_elements():
    source = _source("""
        values = [(a, a + 1)] * 2
        return {"y": values[0][0] + values[1][1]}
    """)
    _agrees(source, [(0,), (2,), (7,)])


def test_sequence_repetition_refuses_a_mutable_descendant_inside_a_tuple():
    source = _source("""
        values = ([0],) * 2
        values[0][0] = a
        return {"y": values[1][0]}
    """)
    assert _native(source, (7,)) == ([{"y": 7}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-repeat-alias-unsupported"


def test_empty_nested_repetition_is_an_explicit_ir_type_boundary():
    source = _source("""
        values = [(a, a + 1)] * 0
        return {"y": len(values)}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "nested-empty-sequence-untyped"


def test_recursive_tuple_elements_can_be_concatenated_without_alias_loss():
    source = _source("""
        left = [(a, a + 1)]
        right = [(a + 2, a + 3)]
        values = left + right
        return {"y": values[0][1] * 10 + values[1][0]}
    """)
    _agrees(source, [(0,), (2,), (7,)])


def test_sequence_concat_refuses_mutable_elements_at_recursive_depth():
    source = _source("""
        original = [[0]]
        values = original + [[1]]
        values[0][0] = a
        return {"y": original[0][0]}
    """)
    assert _native(source, (7,)) == ([{"y": 7}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_dynamic_sequence_concat_preserves_recursive_shape_and_length():
    source = _source("""
        prefix = [(i, i + 1) for i in range(3) if i < a]
        values = prefix + [(7, 8)]
        last = values[len(values) - 1]
        return {"y": len(values) * 10 + last[1]}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,)], widths=[2])


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_proved_empty_prefix_adopts_the_suffix_recursive_shape(lowerer):
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
        self.count = 0
    def process(self, a):
        values = [self.rows[i][:] for i in range(self.count)] + [[a, a + 1]]
        self.count = 1
        return {"y": values[0][0] * 10 + values[0][1]}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer, bound=1)
    _state, output, _hits, error = interp.run_step(proc, {"a": 3})

    assert not error
    assert output.transactions == [{"y": 34}]


def test_sequence_concatenation_requires_one_proved_python_carrier():
    source = _source("""
        if a:
            left = [1]
        else:
            left = (1,)
        return {"y": (left + [2])[0]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-concat-carrier-unproven"


def test_sequence_carrier_join_keeps_a_common_branch_type():
    source = _source("""
        if a:
            left = [a]
        else:
            left = [a + 1]
        joined = left + [7]
        return {"y": joined[0] * 10 + joined[1]}
    """)
    _agrees(source, [(0,), (1,), (3,), (7,)])


def test_branch_ranges_join_before_a_later_loop_is_unrolled():
    source = _source("""
        if a:
            n = 3
        else:
            n = 2
        y = 0
        for i in range(n):
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,)])


def test_branch_lengths_share_one_backing_before_a_sequence_is_consumed():
    source = _source("""
        if a:
            xs = [1, 2, 3]
        else:
            xs = [4, 5]
        return {"y": sum(xs)}
    """)
    _agrees(source, [(0,), (1,), (3,), (7,)])


def test_only_normally_reaching_branch_contributes_flow_facts():
    source = _source("""
        if a:
            return {"y": 9}
        else:
            xs = [2, 3]
        return {"y": sum(xs)}
    """)
    _agrees(source, [(0,), (1,)])


def test_fixed_loop_range_accounts_for_every_iteration():
    source = _source("""
        n = 0
        for i in range(3):
            n += 1
        y = 0
        for j in range(n):
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,)])


def test_dynamic_loop_range_includes_every_possible_trip_count():
    source = _source("""
        n = 0
        for i in range(a):
            n += 1
        y = 0
        for j in range(n):
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,)], widths=[2])


def test_break_path_contributes_facts_at_the_loop_exit():
    source = _source("""
        n = 0
        for i in range(3):
            if a:
                n = 3
                break
        y = 0
        for j in range(n):
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,)])


def test_continue_path_contributes_facts_to_the_next_iteration():
    source = _source("""
        n = 0
        for i in range(3):
            if i < a:
                n += 1
                continue
        y = 0
        for j in range(n):
            y += 1
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,)])


def test_maybe_empty_loop_does_not_invent_a_sequence_carrier():
    source = _source("""
        xs = (1,)
        for i in range(a):
            xs = [2]
        return {"y": (xs + [3])[0]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert caught.value.code == "sequence-concat-carrier-unproven"


def test_one_iteration_can_establish_a_new_sequence_carrier():
    source = _source("""
        xs = (1,)
        for i in range(1):
            xs = [2]
        return {"y": (xs + [3])[0]}
    """)
    _agrees(source, [(0,), (1,)])


def test_module_helper_returns_a_fixed_sequence_contract_to_its_caller():
    source = """\
def unpack(value):
    return [(value >> index) & 1 for index in range(4)]

class M:
    def reset(self):
        pass
    def process(self, a):
        bits = unpack(a)
        return {"y": bits[0] + 2 * bits[1] + 4 * bits[2] + 8 * bits[3]}
"""
    _agrees(source, [(0,), (1,), (7,), (9,), (15,)])


def test_method_helper_joins_equal_sequence_shapes_across_returns():
    source = """\
class M:
    def reset(self):
        pass
    def pair(self, a):
        if a & 1:
            return [a, a + 1]
        return [a + 2, a + 3]
    def process(self, a):
        pair = self.pair(a)
        return {"y": pair[0] * 10 + pair[1]}
"""
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_helper_return_paths_must_have_one_shape():
    source = """\
class M:
    def reset(self):
        pass
    def values(self, a):
        if a:
            return [a]
        return [a, a + 1]
    def process(self, a):
        xs = self.values(a)
        return {"y": xs[0]}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "helper-return-shape-disagrees"


def test_helper_return_carrier_is_available_to_sequence_concatenation():
    source = """\
class M:
    def reset(self):
        pass
    def pair(self, a):
        return [a, a + 1]
    def process(self, a):
        xs = self.pair(a) + [9]
        return {"y": xs[0] * 100 + xs[1] * 10 + xs[2]}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_helper_return_paths_with_different_carriers_are_refused_at_return():
    source = """\
class M:
    def reset(self):
        pass
    def one(self, a):
        if a:
            return [a]
        return (a,)
    def process(self, a):
        return {"y": (self.one(a) + [9])[0]}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "helper-return-shape-disagrees"


def test_helper_returns_a_bounded_runtime_length_sequence():
    source = """\
class M:
    def reset(self):
        pass
    def prefix(self, a):
        xs = []
        for i in range(4):
            if i < a:
                xs.append(i + 1)
        return xs
    def process(self, a):
        xs = self.prefix(a)
        return {"y": 10 * len(xs) + sum(xs)}
"""
    _agrees(source, [(0,), (1,), (2,), (4,), (7,)])


def test_fixed_comprehension_supports_nested_dependent_generators():
    source = _source("""
        xs = [10 * i + j for i in range(3) for j in range(i + 1)]
        return {"y": sum(xs)}
    """)
    _agrees(source, [(0,), (1,)])


def test_filtered_fixed_comprehension_uses_the_growing_sequence_path_only():
    source = _source("""
        xs = [i for i in range(4) if i < a]
        return {"y": sum(xs)}
    """)
    # Assignment to ``xs`` is planned as a bounded growing sequence; the
    # fixed-array comprehension rule must not guess a cardinality for it.
    _agrees(source, [(0,), (1,), (2,), (4,), (7,)])


def test_comprehension_reuses_a_proved_iterable_length_interval():
    source = _source("""
        limit = a if a < 4 else 4
        xs = [i + 1 for i in range(limit)]
        return {"y": sum(xs)}
    """)
    _agrees_without_input_widths(source, [(-3,), (0,), (2,), (4,), (8,)])


def test_unrolled_index_is_static_for_slice_bounds():
    source = _source("""
        flat = [a, a + 1, a + 2, a + 3]
        sums = [sum(flat[2 * i:2 * i + 2]) for i in range(2)]
        return {"y": sums[0] * 10 + sums[1]}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def test_retained_affine_slice_read_and_fixed_width_update_agree():
    source = _source("""
        values = [0 for _ in range(8)]
        for i in range(4):
            base = 2 * i
            pair = [a + base, a + base + 1]
            values[base:base + 2] = pair
        return {"y": sum(values)}
    """)
    _agrees(source, [(0,), (1,), (7,)])


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_slice_update_cannot_change_a_fixed_list_length(lowerer):
    source = _source("""
        values = [0, 1, 2, 3]
        values[1:3] = [a]
        return {"y": len(values)}
    """)
    assert _native(source, (5,)) == ([{"y": 3}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "slice-assignment-length-mismatch"


def test_unrolled_index_is_static_for_a_dependent_range_step():
    source = _source("""
        xs = [10 * i + j for i in range(1, 3)
              for j in range(0, 5, i)]
        return {"y": sum(xs)}
    """)
    _agrees(source, [(0,), (1,)])


def test_retained_range_step_can_be_proved_strictly_negative():
    source = _source("""
        xs = [10 * i + j for i in range(1, 3)
              for j in range(5, 0, -i)]
        return {"y": sum(xs)}
    """)
    _agrees(source, [(0,), (1,)])


def test_retained_range_step_that_can_be_zero_is_refused():
    source = _source("""
        xs = [10 * i + j for i in range(-1, 2)
              for j in range(0, 5, i)]
        return {"y": sum(xs)}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "loop-step-not-constant"


def test_a_while_condition_can_prove_its_nested_range_stride_positive():
    source = _source("""
        step = 4
        y = 0
        while step > 0:
            for j in range(0, 8, step):
                y += j
            step >>= 1
        return {"y": y + (a & 0)}
    """)
    _agrees(source, [(0,), (1,)])


def test_class_constants_retain_scalar_contracts_across_state_branches():
    source = """\
class M:
    IDLE = 0
    BUSY = 1
    def reset(self):
        self.state = self.IDLE
    def process(self, a):
        state = self.state
        if a:
            state = self.BUSY
        else:
            state = self.IDLE
        self.state = state
        return {"y": state}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_integer_bit_methods_retain_scalar_contracts_across_branches():
    source = _source("""
        if a & 1:
            value = a.bit_count()
        else:
            value = a.bit_length()
        return {"y": value}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,), (15,)])


def test_sorted_is_stable_and_evaluates_a_tuple_key_once_per_item():
    source = _source("""
        xs = [a & 3, 1, 3, 1]
        order = sorted(range(4), key=lambda i: (-xs[i], i))
        return {"y": order[0] * 1000 + order[1] * 100 +
                     order[2] * 10 + order[3]}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_sorted_preserves_source_order_when_scalar_keys_are_equal():
    source = _source("""
        xs = [a & 1, 1, a & 1, 0]
        order = sorted(range(4), key=lambda i: xs[i])
        return {"y": order[0] * 1000 + order[1] * 100 +
                     order[2] * 10 + order[3]}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_sorted_key_retains_the_element_contract_in_later_updates():
    source = _source("""
        xs = [a & 3, 1, 3, 1]
        order = sorted(range(4), key=lambda i: (-xs[i], i))
        mask = 0
        for index in range(4):
            if index <= (a & 3):
                mask |= 1 << order[index]
        return {"y": mask}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_sorted_ignores_padding_of_a_runtime_length_sequence():
    source = _source("""
        xs = []
        if a > 0:
            xs.append(3)
        if a > 1:
            xs.append(1)
        if a > 2:
            xs.append(2)
        ys = sorted(xs)
        return {"y": sum(ys)}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_sorted_moves_each_recursive_element_as_one_stable_value():
    source = _source("""
        records = [
            (a & 3, 0),
            (2, 1),
            (a & 3, 2),
            (1, 3),
        ]
        ordered = sorted(records, key=lambda row: (row[0], row[1] & 1))
        return {"y": ordered[0][0] * 100000 + ordered[0][1] * 10000 +
                     ordered[1][0] * 1000 + ordered[1][1] * 100 +
                     ordered[2][0] * 10 + ordered[2][1]}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_sorted_recursive_elements_respect_a_runtime_sequence_length():
    source = _source("""
        records = []
        if a & 1:
            records.append((3, 0))
        records.append((1, 1))
        if a & 2:
            records.append((2, 2))
        ordered = sorted(records, key=lambda row: row[0])
        y = 0
        for row in ordered:
            y = y * 100 + row[0] * 10 + row[1]
        return {"y": y}
    """)
    _agrees(source, [(0,), (1,), (2,), (3,), (7,)])


def test_recursive_fixed_array_is_one_value_across_read_and_store():
    source = _source("""
        rows = [[a, a + 1], [a + 2, a + 3]]
        rows[1][0] = 9
        return {"y": rows[0][1] * 10 + rows[1][0]}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def test_recursive_array_state_round_trips_across_steps():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
    def process(self, a):
        i = a & 1
        self.rows[i][0] += a
        return {"y": self.rows[0][0] * 10 + self.rows[1][0]}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"])
    state = None
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    for value in (1, 2, 3):
        state, out, _hits, error = interp.run_step(proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


def test_list_of_tuple_concat_round_trips_state_across_steps():
    source = """\
class M:
    def reset(self):
        self.window = [(1, 2), (3, 4), (5, 6)]
    def process(self, a):
        self.window = [(a, a + 1)] + self.window[:-1]
        return {"y": self.window[0][1] * 10 + self.window[1][0]}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"])
    state = None
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    for value in (1, 2, 3):
        state, out, _hits, error = interp.run_step(proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


def test_recursive_array_primary_is_captured_before_effectful_index():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
    def bump(self):
        self.rows = [[9, 9], [9, 9]]
        return 0
    def process(self, a):
        y = self.rows[self.bump()][0]
        return {"y": y}
"""
    _agrees(source, [(0,), (1,)])


def test_recursive_store_requires_the_declared_element_shape():
    source = """\
class M:
    def reset(self):
        self.rows = [[1], [2]]
    def process(self, a):
        self.rows[0] = [3, 4]
        return {"y": self.rows[0][0] + (a & 0)}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    # Recursive storage coercion validates the logical source contract before
    # emitting the physical store; rejection is not deferred to its shape.
    assert caught.value.code == "source-value-contract-disagrees"


@pytest.mark.parametrize("body", [
    """
        rows = [[1]]
        rows[0] = (1,)
        return {"y": int(rows == [[1]]) + (a & 0)}
    """,
    """
        rows = [[[1]]]
        rows[0][0] = (1,)
        return {"y": int(rows == [[[1]]]) + (a & 0)}
    """,
])
def test_recursive_store_preserves_the_element_carrier_contract(body):
    source = _source(body)
    assert _native(source, (0,)) == ([{"y": 0}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "source-value-contract-disagrees"


@pytest.mark.parametrize("body", [
    "x = (1,)\nx[0] = 2\nreturn {\"y\": x[0] + (a & 0)}",
    ("rows = ((1,),)\nrows[0][0] = 2\n"
     "return {\"y\": rows[0][0] + (a & 0)}"),
    ("rows = [(1,)]\nrows[0][0] = 2\n"
     "return {\"y\": rows[0][0] + (a & 0)}"),
])
def test_subscript_store_requires_mutable_carriers_at_every_depth(body):
    source = _source(body)
    assert _native(source, (0,))[1] == "TypeError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "store-target-immutable"


def test_dynamic_sequence_keeps_its_length_inside_an_outer_array():
    source = _source("""
        xs = []
        for index in range(2):
            if index < a:
                xs.append(index + 1)
        rows = [[8, 9]]
        rows[0] = xs
        return {"y": rows[0][0]}
    """)
    _agrees(source, [(0,), (1,), (2,), (7,)])


@pytest.mark.parametrize("returned", [
    '{"y": [1, 2]}',
    '[{"y": [1, 2]}]',
])
def test_fixed_recursive_output_payload_is_snapshotted_at_the_boundary(returned):
    source = _source(f"return {returned}")
    _agrees(source, [(0,), (7,)])


def test_mutating_an_array_held_for_subscript_is_typed_refusal():
    source = """\
class M:
    def reset(self):
        self.x = [1]
    def index(self):
        self.x[0] = 9
        return 0
    def process(self, a):
        return {"y": self.x[self.index()] + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 9}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_mutating_an_array_held_for_comparison_is_typed_refusal():
    source = """\
class M:
    def reset(self):
        self.x = [1]
    def bump(self):
        self.x[0] = 2
        return self.x
    def process(self, a):
        return {"y": int(self.x == self.bump()) + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 1}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_fresh_array_copy_before_mutation_retains_value_semantics():
    source = """\
class M:
    def reset(self):
        self.x = [1]
    def bump(self):
        self.x[0] = 2
        return self.x
    def process(self, a):
        return {"y": int(list(self.x) == self.bump()) + (a & 0)}
"""
    _agrees(source, [(0,), (1,)])


def test_dynamic_state_slot_accepts_one_unaliased_flat_copy():
    source = """\
class M:
    def reset(self):
        self.rows = [[0, 0], [0, 0]]
    def process(self, a):
        index = a & 1
        row = [a & 7, (a + 1) & 7]
        self.rows[index] = row[:]
        return {"y": self.rows[index][0] * 10 + self.rows[index][1]}
"""
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_dynamic_state_slot_keeps_a_named_row_alias_visible():
    source = """\
class M:
    def reset(self):
        self.rows = [[0, 0], [0, 0]]
    def process(self, a):
        index = a & 1
        row = [a & 7, (a + 1) & 7]
        self.rows[index] = row
        row[0] = 9
        return {"y": self.rows[index][0]}
"""
    assert _native(source, (0,)) == ([{"y": 9}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_mutating_an_array_held_as_an_earlier_call_argument_is_refused():
    source = """\
class M:
    def reset(self):
        self.x = [1]
    def bump(self):
        self.x[0] = 2
        return 0
    def first(self, row, ignored):
        return row[0]
    def process(self, a):
        return {"y": self.first(self.x, self.bump()) + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 2}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_mutating_a_nested_array_held_as_a_call_argument_is_refused():
    source = """\
class M:
    def reset(self):
        self.rows = [[1]]
    def bump(self):
        self.rows[0][0] = 2
        return 0
    def first(self, row, ignored):
        return row[0]
    def process(self, a):
        return {"y": self.first(self.rows[0], self.bump()) + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 2}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_mutating_a_disjoint_array_after_an_earlier_call_argument_agrees():
    source = """\
class M:
    def reset(self):
        self.x = [1]
        self.other = [2]
    def bump_other(self):
        self.other[0] = 9
        return 0
    def first(self, row, ignored):
        return row[0]
    def process(self, a):
        return {"y": self.first(self.x, self.bump_other()) + (a & 0)}
"""
    _agrees(source, [(0,), (1,)])


def test_comprehension_keeps_earlier_inserted_array_references_live():
    source = """\
class M:
    def reset(self):
        self.x = [1]
    def item(self, index):
        if index:
            self.x[0] = 2
        return self.x
    def process(self, a):
        rows = [self.item(index) for index in range(2)]
        return {"y": rows[0][0] * 10 + rows[1][0] + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 22}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_assignment_location_effect_cannot_mutate_the_saved_rhs_reference():
    source = """\
class M:
    def reset(self):
        self.source = [1]
        self.destination = [[0]]
    def index(self):
        self.source[0] = 9
        return 0
    def process(self, a):
        self.destination[self.index()] = self.source
        return {"y": self.destination[0][0] + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 9}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "subscript-target-effectful"


def test_pure_index_helper_is_admitted_in_a_subscript_target():
    source = """\
class M:
    def reset(self):
        self.cells = [1, 2]
    def process(self, a):
        def index(value):
            return value & 1
        self.cells[index(a)] = 9
        return {"y": self.cells[0] * 10 + self.cells[1]}
"""
    _agrees(source, [(0,), (1,), (2,), (3,)])


def test_augmented_subscript_with_effectful_index_is_typed_refusal():
    source = """\
class M:
    def reset(self):
        self.x = [1, 2]
        self.calls = 0
    def index(self):
        self.calls += 1
        return 0
    def process(self, a):
        self.x[self.index()] += a
        return {"y": self.x[0] * 10 + self.calls}
"""
    assert _native(source, (3,)) == ([{"y": 41}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "augmented-subscript-effectful"


def test_augmented_subscript_with_effectful_rhs_is_typed_refusal():
    source = """\
class M:
    def reset(self):
        self.x = [1, 2]
    def rhs(self):
        self.x[1] = 9
        return 3
    def process(self, a):
        self.x[0] += self.rhs()
        return {"y": self.x[0] * 10 + self.x[1] + (a & 0)}
"""
    assert _native(source, (0,)) == ([{"y": 49}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "augmented-subscript-effectful"


def test_pure_augmented_subscript_retains_value_array_semantics():
    source = """\
class M:
    def reset(self):
        self.x = [1, 2]
    def process(self, a):
        index = a & 1
        self.x[index] += a
        return {"y": self.x[0] * 10 + self.x[1]}
"""
    _agrees(source, [(0,), (1,), (2,), (3,)])


@pytest.mark.parametrize("expression", [
    "xs.append(a) + 1",
    "not xs.append(a)",
])
def test_append_result_is_not_encoded_as_an_integer(expression):
    source = _source(f"""
        xs = []
        y = {expression}
        return {{"y": y}}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "append-result-used"


def test_discarded_append_remains_the_sequence_effect_form():
    source = _source("""
        xs = []
        xs.append(a)
        return {"y": xs[0]}
    """)
    _agrees(source, [(0,), (1,), (7,)])


def _assert_keyword_typeerror_is_typed_refusal(source):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    with pytest.raises(TypeError):
        model.process(0)
    # Python evaluates the keyword expression before rejecting the call.  A
    # frontend must not obtain a different program by silently dropping it.
    assert model.x == 1

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "call-argument-form"


def test_append_keyword_argument_is_not_silently_discarded():
    source = """\
class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x += 1
        return 0
    def process(self, a):
        values = []
        values.append(a, bogus=self.bump())
        return {"y": len(values)}
"""
    _assert_keyword_typeerror_is_typed_refusal(source)


def test_len_keyword_argument_is_not_silently_discarded():
    source = """\
class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x += 1
        return 0
    def process(self, a):
        return {"y": len([a], bogus=self.bump())}
"""
    _assert_keyword_typeerror_is_typed_refusal(source)


def test_static_mapping_get_keyword_argument_is_not_silently_discarded():
    source = """\
TABLE = {0: (5,)}
class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x += 1
        return 0
    def process(self, a):
        value, = TABLE.get(a, (1,), bogus=self.bump())
        return {"y": value}
"""
    _assert_keyword_typeerror_is_typed_refusal(source)


def test_range_keyword_argument_is_not_silently_discarded():
    source = """\
class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x += 1
        return 0
    def process(self, a):
        total = 0
        for value in range(3, bogus=self.bump()):
            total += value
        return {"y": total}
"""
    _assert_keyword_typeerror_is_typed_refusal(source)


def test_reversed_keyword_argument_is_not_silently_discarded():
    source = """\
class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x += 1
        return 0
    def process(self, a):
        total = 0
        for value in reversed([1, 2], bogus=self.bump()):
            total += value
        return {"y": total}
"""
    _assert_keyword_typeerror_is_typed_refusal(source)


def test_jagged_recursive_array_is_not_given_a_homogeneous_ir_type():
    source = _source("""
        rows = [[a], [a, a + 1]]
        return {"y": rows[0][0]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-element-shape-disagrees"


@pytest.mark.parametrize("body", [
    """
    row = []
    if a:
        row.append(7)
    rows = [row]
    return {"y": rows[0][0]}
    """,
    """
    row = []
    if a:
        row.append(7)
    rows = (row,)
    return {"y": rows[0][0]}
    """,
    """
    row = []
    if a:
        row.append(7)
    inner = row + [9]
    rows = [inner]
    return {"y": rows[0][1]}
    """,
])
def test_nested_dynamic_sequence_lengths_are_not_replaced_by_padding(body):
    source = _source(body)
    assert _native(source, (0,))[1] == "IndexError"
    # Recursive length carriers now support this representation. Check both
    # lowerers against Python: padding must not turn an empty read into data.
    _agrees(source, [(value,) for value in range(16)], widths=[4])


def test_dynamic_helper_result_cannot_lose_its_length_when_nested():
    source = """\
class M:
    def reset(self):
        pass
    def row(self, flag):
        result = []
        if flag:
            result.append(7)
        return result
    def process(self, a):
        rows = [self.row(a)]
        return {"y": rows[0][0]}
"""
    assert _native(source, (0,))[1] == "IndexError"
    _agrees(source, [(value,) for value in range(16)], widths=[4])


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("returned", ["return", "return None"])
def test_a_consumed_helper_must_return_a_value_on_every_path(lowerer,
                                                              returned):
    source = f"""\
class M:
    def reset(self):
        pass
    def helper(self):
        {returned}
    def process(self, a):
        return {{"y": self.helper() + (a & 0)}}
"""
    assert _native(source, (0,))[1] == "TypeError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "helper-return-value-unsupported"


def test_an_effect_only_helper_may_fall_off_without_returning():
    source = """\
class M:
    def reset(self):
        self.x = 1
    def helper(self, value):
        self.x += value
    def process(self, a):
        self.helper(a)
        return {"y": self.x}
"""
    _agrees(source, [(0,), (1,), (7,)])


def test_an_effect_only_helper_may_return_without_a_value():
    source = """\
class M:
    def reset(self):
        self.x = 1
    def helper(self, stop):
        if stop:
            return
        self.x += 2
    def process(self, a):
        self.helper(a)
        return {"y": self.x}
"""
    _agrees(source, [(0,), (1,)])


def test_a_local_must_be_defined_on_every_path_that_reads_it():
    source = _source("""
        if a:
            value = 1
        return {"y": value}
    """)
    assert _native(source, (0,))[1] == "UnboundLocalError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "variable-may-be-unassigned"


def test_repeated_unwritten_input_guard_preserves_matching_local_path():
    source = _source("""
        if choose:
            left = value + 1
        else:
            right = value + 2
        if choose:
            result = left
        else:
            result = right
        return {"y": result}
    """, inputs="choose, value")

    for row in ((0, 4), (1, 4), (3, 7)):
        assert _lowered(source, row, outputs=["y"]) == _native(source, row)


def test_a_rebound_parameter_is_not_treated_as_the_same_later_guard():
    source = _source("""
        if choose:
            value = 1
        choose = 0
        if choose:
            result = value
        else:
            result = 2
        return {"y": result}
    """, inputs="choose")

    for row in ((0,), (1,)):
        assert _lowered(source, row, outputs=["y"]) == _native(source, row)


def test_a_zero_iteration_loop_does_not_define_its_body_local():
    source = _source("""
        for index in range(a):
            value = index
        return {"y": value}
    """)
    assert _native(source, (0,))[1] == "UnboundLocalError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[2], outputs=["y"])
    assert caught.value.code == "variable-may-be-unassigned"


@pytest.mark.parametrize("body", [
    "a = a + 1\nreturn {\"y\": a}",
    "a += 1\nreturn {\"y\": a}",
    "a, other = (1, 2)\nreturn {\"y\": a + other}",
    "for a in range(2):\n    pass\nreturn {\"y\": a}",
])
@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_source_parameters_are_mutable_copies_of_read_only_ir_inputs(
        body, lowerer):
    source = _source(body)
    for value in (0, 3, 7):
        expected = _native(source, (value,))
        assert _lowered(
            source, (value,), outputs=["y"], lowerer=lowerer) == expected


@pytest.mark.parametrize("body", [
    """
        total = 0
        for index in range(2):
            index = a
            total += index
        return {"y": total}
    """,
    """
        total = 0
        for index in range(2):
            index += a
            total += index
        return {"y": total}
    """,
    """
        total = 0
        for index in range(2):
            index, other = (a, 2)
            total += index + other
        return {"y": total}
    """,
    """
        total = 0
        for index in range(2):
            for index in range(1):
                pass
            total += index
        return {"y": total}
    """,
])
@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_scalar_loop_targets_are_locals_not_internal_counters(body, lowerer):
    source = _source(body)
    for value in range(16):
        assert _lowered(source, (value,), lowerer=lowerer) == _native(source, (value,))


def test_a_loop_target_name_can_be_reused_after_the_loop_scope():
    source = _source("""
        for index in range(2):
            pass
        index = a + 7
        return {"y": index}
    """)
    _agrees(source, [(0,), (3,), (7,)])


@pytest.mark.parametrize("body", [
    """
        xs = []
        if a:
            xs.append(9)
        if a > 1:
            xs.append(8)
        for xs in [[1, 2]]:
            pass
        return {"y": len(xs) * 100 + xs[1]}
    """,
    """
        out = []
        out.append({"y": 1})
        for out in [[]]:
            pass
        return out
    """,
    """
        out = {}
        out["y"] = 1
        for out in [0]:
            pass
        return out
    """,
    """
        out = []
        out.append({"y": 1})
        out, other = ([], [])
        return out
    """,
])
@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_nonordinary_rebindings_cannot_bypass_representation_state(
        body, lowerer):
    source = _source(body)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "represented-name-rebound"

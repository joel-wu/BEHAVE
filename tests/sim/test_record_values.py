"""Native/IR contracts for fixed-schema source records."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.lowering import (
    LoweringError,
    _Lowerer,
    lower_with_state_ranges,
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


def _lowered(source, inputs, *, outputs=("y",), lowerer=None):
    proc = lower_with_state_ranges(
        source, input_widths=[4] * len(inputs), outputs=list(outputs),
        lowerer=lowerer)
    values = dict(zip(proc.inputs, inputs))
    _state, out, _hits, error = interp.run_step(proc, values)
    return (None if error else [dict(row) for row in out.transactions], error)


def _agrees(source, rows, *, outputs=("y",)):
    for row in rows:
        expected = _native(source, row)
        assert _lowered(source, row, outputs=outputs) == expected
        assert _lowered(
            source, row, outputs=outputs, lowerer=_Lowerer) == expected


def test_helper_record_is_a_fieldwise_value_across_two_activations():
    source = """\
class M:
    def reset(self):
        pass
    def rec(self, a):
        return {"sign": a & 1, "magnitude": a >> 1}
    def use(self, raw):
        return raw["magnitude"] * 2 + raw["sign"]
    def process(self, a):
        left = self.rec(a)
        right = self.rec(a + 1)
        return {"y": 10 * self.use(left) + self.use(right)}
"""
    _agrees(source, [(0,), (1,), (7,), (15,)])


def test_record_literal_captures_each_field_in_source_evaluation_order():
    source = """\
class M:
    def reset(self):
        self.x = 3
    def bump(self):
        old = self.x
        self.x += 1
        return old
    def process(self, a):
        raw = {"before": self.x, "effect": self.bump()}
        return {"y": 10 * raw["before"] + raw["effect"]}
"""
    _agrees(source, [(0,)])


def test_duplicate_literal_key_evaluates_every_value_and_last_write_wins():
    source = """\
class M:
    def reset(self):
        self.s = 0
    def bump(self, value):
        self.s = self.s * 10 + value
        return self.s
    def process(self, a):
        raw = {"x": self.bump(1), "x": self.bump(2)}
        return {"y": 100 * raw["x"] + self.s}
"""
    _agrees(source, [(0,)])


def test_nested_inferred_record_crosses_helper_and_conditional_boundaries():
    source = """\
class M:
    def reset(self):
        pass
    def rec(self, a):
        return {"meta": {"lo": a, "hi": a + 1}, "tag": a & 1}
    def process(self, a):
        raw = (self.rec(a) if a & 1
               else {"tag": 2, "meta": {"hi": a + 3, "lo": a + 2}})
        return {"y": 100 * raw["meta"]["hi"]
                     + 10 * raw["meta"]["lo"] + raw["tag"]}
"""
    _agrees(source, [(0,), (1,), (6,), (7,)])


def test_local_dict_comprehension_is_a_first_class_record_value():
    source = """\
class M:
    def reset(self):
        self.s = 0
    def bump(self, value):
        self.s = self.s * 10 + value + 1
        return {"value": self.s}
    def process(self, a):
        raw = {"k0": self.bump(i)
               for i in range(3) if i != 1}
        return {"y": 100 * raw["k0"]["value"] + self.s}
"""
    _agrees(source, [(0,)])


def test_local_dict_comprehension_requires_a_fixed_finite_schema():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {("x" if a else "z"): a for i in range(1)}
        return {"y": raw["x"]}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "record-key-not-static"


def test_record_return_arms_join_componentwise_not_by_literal_order():
    source = """\
class M:
    def reset(self):
        pass
    def rec(self, a):
        if a & 1:
            return {"x": a, "z": a + 1}
        return {"z": a + 2, "x": a + 3}
    def process(self, a):
        raw = self.rec(a)
        return {"y": 10 * raw["x"] + raw["z"]}
"""
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_conditional_expression_joins_records_fieldwise():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = ({"x": a, "z": a + 1} if a & 1
               else {"z": a + 2, "x": a + 3})
        return {"y": 10 * raw["x"] + raw["z"]}
"""
    _agrees(source, [(0,), (1,), (2,), (7,)])


def test_fixed_missing_record_field_has_exact_keyerror_semantics():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {"x": a}
        return {"y": raw["missing"]}
"""
    _agrees(source, [(0,), (7,)])


def test_record_local_projects_to_the_output_mapping_only_at_return():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        row = {"hi": a + 1, "lo": a - 1}
        return row
"""
    _agrees(source, [(0,), (4,), (9,)], outputs=("hi", "lo"))


@pytest.mark.parametrize("body, code", [
    ('raw = {"x": a}\n        raw[a & 1] = 3\n        y = 0',
     "output-key-not-static"),
    ('raw = {"x": a}\n        y = raw["x" if a else "missing"]',
     "record-key-not-static"),
    ('raw = {"x": a}\n        y = int(raw < raw)',
     "record-operation-unsupported"),
])
def test_record_operations_outside_the_fieldwise_value_subset_are_typed(
        body, code):
    source = f"""\
class M:
    def reset(self):
        pass
    def process(self, a):
        {body}
        return {{"y": y}}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == code


def test_record_list_is_a_transaction_batch_value():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {"x": a}
        rows = [raw]
        return rows
"""
    _agrees(source, [(0,), (7,)], outputs=("x",))


def test_fixed_schema_record_builder_can_be_used_inside_the_step():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {}
        raw["x"] = a
        y = raw["x"] + 1
        return {"y": y}
"""
    _agrees(source, [(0,), (7,)])


def test_record_builder_may_start_partial_when_all_observations_are_complete():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {"base": a}
        for i in range(3):
            raw[f"x{i}"] = a + i + 1
        return raw
"""
    _agrees(
        source, [(0,), (7,)], outputs=("base", "x0", "x1", "x2"))


def test_record_builder_must_be_complete_on_every_observed_path():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        raw = {"x": a}
        if a:
            raw["z"] = a + 1
        return raw
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["x", "z"])
    assert caught.value.code == "outputs-disagree-across-returns"


def test_helper_record_return_paths_must_have_one_schema():
    source = """\
class M:
    def reset(self):
        pass
    def rec(self, a):
        if a:
            return {"x": a}
        return {"z": a}
    def process(self, a):
        raw = self.rec(a)
        return {"y": raw["x"]}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "helper-return-shape-disagrees"


def test_scalar_state_cannot_change_to_a_record():
    source = """\
class M:
    def reset(self):
        self.raw = 0
    def process(self, a):
        self.raw = {"x": a}
        return {"y": a}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "state-value-contract-disagrees"

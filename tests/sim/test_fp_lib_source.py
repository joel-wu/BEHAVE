"""Public FP imports must agree after Behavior source lowering, not just in Python."""

import pytest

from behave.sim.behavior.instance import lower
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

# These bit patterns are also covered by the independent SoftFloat/doc audit.
CASES = [
    ("fp32_add", [(1, 1, 2), (0x80000000, 0x80000000, 0x80000000),
                  (0x3F800000, 1, 0x3F800000), (0x7F7FFFFF, 1, 0x7F7FFFFF)]),
    ("fp32_sub", [(0x00800001, 0x00800000, 1), (1, 2, 0x80000001)]),
    ("fp32_mul", [(0x00800000, 0x3F000000, 0x00400000),
                  (0x00800000, 0x3F7FFFFF, 0x00800000), (3, 0x3F000000, 2),
                  (0x80000001, 0x3F000000, 0x80000000)]),
    ("fp32_div", [(0x00800000, 0x40000000, 0x00400000),
                  (0x00FFFFFF, 0x40000000, 0x00800000),
                  (1, 0x7F7FFFFF, 0), (0x7F7FFFFF, 1, 0x7F800000)]),
    ("fp32_sqrt", [(2, 0x1A800000), (0x80000001, 0x7FC00000)]),
    ("fp32_rsqrt", [(2, 0x64800000), (0x80000001, 0x7FC00000)]),
    ("fp32_cmp", [(1, 0, 1), (0x80000001, 0, -1), (0x7FC00000, 0, -2)]),
    ("fp32_is_zero", [(1, 0), (0x80000000, 1)]),
    ("fp32_is_nan", [(0x7F800001, 1), (0x7F800000, 0)]),
    ("fp32_is_inf", [(0xFF800000, 1), (0x7F800001, 0)]),
    ("fp32_abs", [(0x80000001, 1), (0xFF800001, 0x7F800001)]),
    ("fp32_neg", [(0, 0x80000000), (0x7F800001, 0xFF800001)]),
    ("fp32_to_int32_sat", [(0xCF000000, -2147483648), (0x7FC00000, 0),
                           (0x4F000000, 2147483647)]),
    ("int32_to_fp32", [(-2147483648, 0xCF000000), (2147483647, 0x4F000000)]),
    ("bf16_to_fp32", [(1, 0x00010000), (0x7F81, 0x7F810000)]),
    ("fp32_to_bf16", [(0x00008000, 0), (0x00018000, 2), (0xFF800001, 0xFFC0)]),
]


@pytest.mark.parametrize(("name", "cases"), CASES, ids=[name for name, _ in CASES])
def test_public_fp_import_source_replay_and_encoding(name, cases):
    inputs = ("a", "b") if len(cases[0]) == 3 else ("a",)
    arguments = ", ".join(inputs)
    call = f"{name}({arguments})"
    if name.startswith("fp32_is_"):
        call = f"int({call})"
    source = f'''from fp_lib import {name}

class M:
    def reset(self):
        pass
    def process(self, {arguments}):
        return {{"y": {call}}}
'''
    schema = TransactionSchema(
        [Endpoint(arg, "input", 16 if name == "bf16_to_fp32" else 32,
                  signed=name == "int32_to_fp32") for arg in inputs]
        + [Endpoint("y", "output", 32,
                    signed=name in {"fp32_cmp", "fp32_to_int32_sat"})],
        authority=Authority.TASK, reading=Reading.ELABORATED,
    )
    behavior = lower(source, schema, bound=1)
    traces = []
    for *values, expected in cases:
        trace = [dict(zip(inputs, values))]
        traces.append(trace)
        replay = behavior.replay(trace)
        assert not replay.broken, replay.broken
        assert not replay.error, replay.error
        assert replay.outputs[0].transactions == [{"y": expected}]
    check = behavior.bind(1).instance.audit(traces)
    assert check.status.value == "agreed", check

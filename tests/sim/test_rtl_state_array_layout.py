"""Compiler-flattened state identities still refer to the same native words."""

from itertools import product
from pathlib import Path

import pytest

from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.core.refusal import Refused
from .test_rtl_initial_state import initial_fixture

pytestmark = pytest.mark.cosim


@pytest.mark.parametrize("atomic", [False, True], ids=["single", "multi-clock"])
@pytest.mark.parametrize("dimensions", [((2, 3), (6, 4)), ((-1, -2), (1, 2), (4, 5))])
def test_native_words_match_structural_state_before_and_after_reset(tmp_path, atomic, dimensions):
    artifact, contract, stimulus, _ = initial_fixture(
        tmp_path, atomic, False, False, "rising", declared_initial=False)
    indices = list(product(*(range(left, right + (1 if left <= right else -1),
                                   1 if left <= right else -1) for left, right in dimensions)))
    words = ['store' + ''.join(f'[{index}]' for index in row) for row in indices]
    # Distinct startup and update values catch a permutation, not just a width
    # mismatch. Assign literal elements so loop instrumentation is not the oracle.
    startup = ' '.join(f'{word} = 8\'d{i + 1};' for i, word in enumerate(words))
    resets = ' '.join(f'{word} <= 0;' for word in words)
    updates = ' '.join(f'{word} <= {word} + 8\'d{i + 1};' for i, word in enumerate(words))
    extra_ports = ', input b_clk, b_rst' if atomic else ''
    Path(artifact.rtl_path).write_text(f"""module top(
      input clk, rst, in_valid, out_ready, input [2:0] data,
      output in_ready, out_valid, output [2:0] value{extra_ports});
      reg [7:0] store {''.join(f'[{left}:{right}]' for left, right in dimensions)};
      assign in_ready=1;
      assign out_valid=1;
      assign value={' ^ '.join(words)};
      initial begin {startup} end
      always @(posedge clk) begin
        if(rst) begin {resets} end
        else begin {updates} end
      end
    endmodule""")
    ownership = artifact.state_ownership(contract)
    fields = {field.name: field.width for field in ownership.fields}
    expected = {f'store[{i}]': i + 1 for i in range(len(words))}
    assert fields == dict.fromkeys(expected, 8)
    assert artifact.bind_observation(contract).native
    try:
        trace = artifact.exact_trace(
            contract, stimulus, observe_names=('value',),
            state_names=tuple(fields), state_widths=fields)
    except Refused as exc:
        pytest.fail(f'{exc}\n{exc.refusal.detail.get("output", "")}')
    assert dict(trace.initial_state) == expected
    assert [dict(row.state) for row in trace.records] == [dict.fromkeys(expected, 0), expected]
    # The optional analysis witness deposits into exactly the same native words.
    # It does not force their values through a subsequent reset or update.
    chosen = {name: 128 + value for name, value in expected.items()}
    selected = artifact.exact_trace(
        contract, stimulus, observe_names=('value',), state_names=tuple(fields),
        state_widths=fields,
        initial_state=RTLInitialState.from_values(artifact.identity, fields, chosen))
    assert dict(selected.initial_state) == chosen
    assert [dict(row.state) for row in selected.records] == [dict.fromkeys(expected, 0), expected]

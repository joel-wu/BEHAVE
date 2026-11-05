# Vector Lane Reduce Sum

This module returns the exact unsigned sum of all sixteen input lanes, one result per accepted input transaction. The arithmetic result is fixed; the implementation may choose its adder topology and pipeline depth.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_vector_unit_vector_lane_reduce_sum #(
    parameter int LANES = 16,
    parameter int IN_WIDTH = 8,
    parameter int OUT_WIDTH = IN_WIDTH + 4
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [IN_WIDTH-1:0] in_lane_0,
    input  logic [IN_WIDTH-1:0] in_lane_1,
    input  logic [IN_WIDTH-1:0] in_lane_2,
    input  logic [IN_WIDTH-1:0] in_lane_3,
    input  logic [IN_WIDTH-1:0] in_lane_4,
    input  logic [IN_WIDTH-1:0] in_lane_5,
    input  logic [IN_WIDTH-1:0] in_lane_6,
    input  logic [IN_WIDTH-1:0] in_lane_7,
    input  logic [IN_WIDTH-1:0] in_lane_8,
    input  logic [IN_WIDTH-1:0] in_lane_9,
    input  logic [IN_WIDTH-1:0] in_lane_10,
    input  logic [IN_WIDTH-1:0] in_lane_11,
    input  logic [IN_WIDTH-1:0] in_lane_12,
    input  logic [IN_WIDTH-1:0] in_lane_13,
    input  logic [IN_WIDTH-1:0] in_lane_14,
    input  logic [IN_WIDTH-1:0] in_lane_15,

    output logic out_valid,
    input  logic out_ready,
    output logic [OUT_WIDTH-1:0] out_sum
);
```

Supported configurations have `LANES = 16`, `IN_WIDTH >= 1`, and `OUT_WIDTH = IN_WIDTH + 4`; the benchmark uses `IN_WIDTH = 8`. The `LANES` parameter does not change the sixteen-port interface. Every input lane port `in_lane_0` through `in_lane_15` participates in the reduction.

Each lane port carries an unsigned `IN_WIDTH`-bit integer. `out_sum` carries an unsigned `OUT_WIDTH`-bit integer, with `OUT_WIDTH = IN_WIDTH + 4`, which is sufficient to represent the exact sum of 16 unsigned `IN_WIDTH`-bit inputs without overflow.

### Behavior interface

```python
class VectorLaneReduceSum:
    def __init__(self, in_width=8):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_lane_0, in_lane_1, in_lane_2, in_lane_3,
        in_lane_4, in_lane_5, in_lane_6, in_lane_7,
        in_lane_8, in_lane_9, in_lane_10, in_lane_11,
        in_lane_12, in_lane_13, in_lane_14, in_lane_15
    ):
        pass
```

Set `in_width` to the RTL `IN_WIDTH`. Call `process(...)` once per accepted
input, passing sixteen unsigned `in_width`-bit Python integers. It always
returns exactly `{"out_sum": sum_value}`, where `sum_value` is an unsigned
`in_width + 4`-bit Python integer. A zero sum is a complete output record,
not `None` or an empty sequence. Do not make extra calls for stalls or latency.
`reset()` returns `None`, clears no algorithmic history, and preserves the
constructor-derived `in_width` and `out_width` configuration.

### Transaction and Timing Behavior

Transfers occur at rising edges with the corresponding valid and ready both
asserted and reset inactive. A stalled input must retain every lane; a
stalled output must retain its valid bit and sum. Responses are ordered.
The block may backpressure inputs; simultaneous retirement and acceptance
are allowed. Payload when `out_valid` is 0 is don't-care.

## Modules

- `ai_ml_arch_vector_unit_vector_lane_reduce_sum`: top-level vector-lane reduction block.

## Functional Behavior

On each accepted input transaction, the module samples all 16 lane inputs. Each sampled lane value is interpreted as an unsigned integer and masked to `IN_WIDTH` bits.

The result is

`out_sum = sum(in_lane_i for i = 0, ..., 15)`.

Every lane participates. All additions preserve their exact unsigned integer
value: no saturation, modular wrap, or intermediate truncation is allowed.
The maximum sum is `16 * (2^IN_WIDTH - 1)`, which fits in `OUT_WIDTH` bits.
A balanced four-level tree is one implementation, not a required topology
or a four-cycle latency contract.

There is exactly one output transaction for each accepted input transaction.

## Reset Behavior

Reset is asynchronous, active-high on `rst`. Assertion cancels pending responses and clears transaction storage; transfers do not occur while reset is asserted.

The module has no architectural state that affects the reduced sum across transactions. During reset, `out_valid` shall be deasserted and `out_sum` shall be `0`. After reset is released, the next accepted input transaction is processed with no dependence on any pre-reset activity.

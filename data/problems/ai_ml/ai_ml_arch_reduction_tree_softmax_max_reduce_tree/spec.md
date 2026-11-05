# Eight-Way Unsigned Maximum

This module returns the maximum of eight unsigned bytes. A balanced comparison tree is one implementation, not a required architecture. One accepted input transaction carries all vector elements for one reduction. One output transaction returns the single maximum value. The reduction is functionally equivalent to repeated pairwise `max(a, b)` operations with zero padding up to the next power-of-two element count, matching the tree structure used for reduction utilities.

## Interface

### RTL interface

```systemverilog
module reduction_tree_softmax_max_reduce_tree (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_x0,
    input  logic [7:0]  in_x1,
    input  logic [7:0]  in_x2,
    input  logic [7:0]  in_x3,
    input  logic [7:0]  in_x4,
    input  logic [7:0]  in_x5,
    input  logic [7:0]  in_x6,
    input  logic [7:0]  in_x7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_max
);
```

### Behavior interface

```python
class ReductionTreeSoftmaxMaxReduceTree:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7): ...
```

Each call represents one accepted input vector and returns exactly
`{"out_max": value}`. All eight arguments and `value` are unsigned 8-bit
integers. A zero maximum still returns this dictionary, not `None`, a scalar
or a list. No handshake signals appear in the return value. The model has no
state between vectors; construction and `reset()` establish this empty state,
and `reset()` returns `None`.

## Modules

- `reduction_tree_softmax_max_reduce_tree`: top-level eight-input maximum operator.

## Functional Behavior

Each input `in_x0` through `in_x7` is an unsigned 8-bit integer in the range `[0, 255]`.

The result can be defined by the following balanced grouping of unsigned comparisons. Other groupings produce the same required result. Define the pairwise maximum operator:

- `max_u8(a, b) = a` if `a > b`, else `b`

Equal operands return the second operand, but because the values are equal this has no effect on the numeric result.

The reduction is performed in three stages:

1. First stage:
   - `s0 = max_u8(in_x0, in_x1)`
   - `s1 = max_u8(in_x2, in_x3)`
   - `s2 = max_u8(in_x4, in_x5)`
   - `s3 = max_u8(in_x6, in_x7)`

2. Second stage:
   - `t0 = max_u8(s0, s1)`
   - `t1 = max_u8(s2, s3)`

3. Final stage:
   - `out_max = max_u8(t0, t1)`

This is exactly equivalent to the maximum of all eight inputs:
- `out_max = max(in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7)`

All comparisons are unsigned 8-bit comparisons. The output is the selected 8-bit operand value with no widening, truncation, rounding, saturation, or signed reinterpretation.

The module is a 1:1 transaction mapping: every accepted input transaction produces exactly one output transaction containing `out_max`.

### Source boundary

The source selector is the unsigned `Util.maxOf(u1,u2)` in Gemmini Util.scala.
Composing it over eight bytes is this task's adaptation; the source's separately
named `accumulateTree` adds values and is not the maximum operation. No
exponential, normalization, signed logit handling or full softmax is included.
The legacy identifier does not require a softmax unit or a particular tree.

## Reset Behavior

Reset is asynchronous and active-high. Assertion immediately clears all
architectural state to zero and cancels any outstanding transaction, without
waiting for a rising clock edge.

Because the function is purely combinational at the transaction level, the architectural state consists only of any implementation-defined pipeline or holding registers. After reset:
- `out_valid = 0`
- `out_max = 8'h00`

If the implementation contains internal data registers for accepted inputs or intermediate tree stages, all such registers shall be initialized to zero.

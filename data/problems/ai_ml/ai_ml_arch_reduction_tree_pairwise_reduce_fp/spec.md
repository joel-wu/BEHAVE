# Reduction Tree Pairwise Reduce FP

This module reduces eight binary32 inputs by summing adjacent pairs in a fixed balanced tree: four additions, then two, then one. Each node rounds independently to nearest, ties to even, with gradual underflow. No state is retained between transactions.

## Interface

### RTL interface

```systemverilog
module reduction_tree_pairwise_reduce_fp (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_x4,
    input  logic [31:0] in_x5,
    input  logic [31:0] in_x6,
    input  logic [31:0] in_x7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y
);
```

### Behavior interface

```python
class ReductionTreePairwiseReduceFp:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7): ...
```

Each `process(...)` call represents one accepted eight-word input and returns
exactly `{"out_y": value}`, where `value` is an unsigned 32-bit integer carrying
the resulting binary32 bit pattern. Arguments are also unsigned 32-bit integers,
not Python floats. Zero, infinity and NaN results still return a dictionary,
never `None` or a list. No arithmetic state persists between calls;
`reset()` returns `None` and emits no result.

## Modules

- `reduction_tree_pairwise_reduce_fp`: top-level module implementing the 8-input balanced pairwise floating-point reduction.

## Functional Behavior

An input transfers on a rising clock edge with `in_valid && in_ready`; a response transfers with `out_valid && out_ready`. Each accepted input produces exactly one ordered response unless reset cancels it. A pending input and a stalled response must keep their valid signal and payload stable. Latency is implementation-defined within the task's declared run bounds.

Each input `in_x0` through `in_x7` is a 32-bit unsigned integer carrying one FP32 bit pattern.

For each accepted input transaction, the module computes one output `out_y`, also as a 32-bit unsigned integer carrying one FP32 bit pattern.

The output is the balanced pairwise reduction of the 8 inputs using exactly this associativity:

1. `s0 = fp32_add(in_x0, in_x1)`
2. `s1 = fp32_add(in_x2, in_x3)`
3. `s2 = fp32_add(in_x4, in_x5)`
4. `s3 = fp32_add(in_x6, in_x7)`
5. `t0 = fp32_add(s0, s1)`
6. `t1 = fp32_add(s2, s3)`
7. `out_y = fp32_add(t0, t1)`

No other associativity is permitted. In particular, the module does not compute a left-fold sum and does not reorder operands.

The reduction has no zero-padding because the input count is exactly 8, which is already a power of two.

Every `fp32_add` node uses the following bit-exact arithmetic, independently of the implementation language:

- Preserve the numerical value of subnormal inputs (zero exponent, nonzero fraction).
- Any NaN input or addition of opposite infinities returns the canonical quiet NaN `0x7fc00000`. Otherwise an infinity input determines the result.
- Add finite operands and round to nearest binary32, breaking ties toward an even significand, including at the zero/subnormal and subnormal/normal boundaries. Overflow returns signed infinity; underflow is gradual, not flushed to zero.
- Two negative zeros sum to negative zero. Other exact-zero sums, including cancellation of nonzero operands, return positive zero.

Apply this rule at all seven nodes, not only to the final mathematical sum. NaN payloads are not preserved, and no exception flags are outputs.

The canonical arithmetic NaN is a project convention. Compare outputs by their exact 32-bit encodings. For example, eight inputs equal to `0x00000001` produce `0x00000008`.

The module is functionally stateless: every output depends only on the current accepted input transaction.

## Reset Behavior

Reset is active-high and may be implemented synchronously or asynchronously.

Reset cancels any pending response. Because the module is functionally stateless, it initializes no arithmetic state.

After reset, the output handshake signals shall be in the idle state until driven by the implementation's normal handshake behavior:
- `out_valid = 0`
- `out_y = 32'h00000000`

`in_ready` is not architectural state; it may be high whenever the implementation can accept an input transaction.

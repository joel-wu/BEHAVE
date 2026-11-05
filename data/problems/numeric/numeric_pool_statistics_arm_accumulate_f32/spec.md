# FP32 Vector Accumulate 4

FP32 Vector Accumulate 4 computes the sum of four IEEE-754 single-precision inputs using a fixed left-fold reduction. One accepted input transaction contains exactly four FP32 elements and produces exactly one FP32 result. The arithmetic contract is the `fp_lib` FP32 addition operation applied in the order `((x0 + x1) + x2) + x3`, so rounding occurs after each add and all NaN, infinity, zero, and subnormal behavior is the behavior of those three `fp32_add` operations.

## Interface

### RTL interface

```systemverilog
module fp32_vector_accumulate4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sum
);
```

### Behavior interface

```python
class Fp32VectorAccumulate4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fp32_vector_accumulate4`: top-level module that accepts four FP32 inputs and returns one FP32 accumulated sum.

## Functional Behavior

Each input port `in_x0` through `in_x3` carries one IEEE-754 binary32 bit pattern as an unsigned 32-bit integer.

For each accepted input transaction, the module computes:

1. `sum0 = fp32_add(in_x0, in_x1)`
2. `sum1 = fp32_add(sum0, in_x2)`
3. `sum2 = fp32_add(sum1, in_x3)`

The module emits one output transaction with `out_sum = sum2`.

The reduction order is fixed and is not interchangeable with any balanced tree or any other reassociation.

Each addition rounds its exact result separately to binary32 using round-to-nearest, ties-to-even. Subnormal operands retain their numerical value; underflow is gradual, including rounding at zero/subnormal and subnormal/normal boundaries. Overflow returns signed infinity. Compare output encodings exactly, without a tolerance.

A NaN operand or addition of opposite infinities returns `0x7FC00000`; this canonical sign/payload is a project convention. Otherwise an infinity determines the result. Adding two negative zeros yields negative zero; other exact cancellation yields positive zero. No step is fused or reassociated. For example, four inputs equal to `0x00000001` produce `0x00000004`.

Worked example of operation order: if one transaction carries `in_x0 = a`, `in_x1 = b`, `in_x2 = c`, `in_x3 = d`, the result is `fp32_add(fp32_add(fp32_add(a, b), c), d)`. It is not `fp32_add(fp32_add(a, b), fp32_add(c, d))`.

Output cardinality is 1:1. Every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state after reset:
- No buffered input is pending.
- No output is pending.

Output port values after reset:
- `out_valid = 0`
- `out_sum = 32'h00000000`

The module may accept a new input transaction immediately after reset is deasserted.

# Matrix Add F32x4

This block performs elementwise addition of two fixed-size 2×2 matrices whose elements are IEEE-754 single-precision values carried as raw 32-bit bit patterns. One accepted input transaction contains all four elements of matrix A and all four elements of matrix B; the block produces exactly one output transaction containing the four elementwise sums. The arithmetic contract is the IEEE-754 binary32 addition implemented by `fp32_add` on each corresponding element independently.

## Interface

### RTL interface

```systemverilog
module matrix_add_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,
    input  logic [31:0] in_b00,
    input  logic [31:0] in_b01,
    input  logic [31:0] in_b10,
    input  logic [31:0] in_b11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_c00,
    output logic [31:0] out_c01,
    output logic [31:0] out_c10,
    output logic [31:0] out_c11
);
```

### Behavior interface

```python
class MatrixAddF32x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_a00, in_a01, in_a10, in_a11,
            in_b00, in_b01, in_b10, in_b11):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c10`, `out_c11`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `matrix_add_f32x4`: top-level module implementing one 2×2 floating-point matrix addition per accepted input transaction.

## Functional Behavior

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Each input port `in_aXY` and `in_bXY` is a `uint32` carrying the raw bit pattern of one IEEE-754 binary32 operand.

Each output port `out_cXY` is a `uint32` carrying the raw bit pattern of one IEEE-754 binary32 result.

For every accepted input transaction, the block computes exactly one output transaction with:

- `out_c00 = fp32_add(in_a00, in_b00)`
- `out_c01 = fp32_add(in_a01, in_b01)`
- `out_c10 = fp32_add(in_a10, in_b10)`
- `out_c11 = fp32_add(in_a11, in_b11)`

The four additions are independent. There is no cross-element reduction, no saturation beyond IEEE-754 overflow behavior, and no size-mismatch status output because the matrix shape is fixed at 2×2 by the interface.

`fp32_add` is the binary32 addition primitive from `fp_lib`, including its rounding and special-case behavior. The contract therefore includes:

- rounding mode: round to nearest, ties to even
- NaN handling: if either operand is NaN, the result is the `fp32_add` NaN result
- infinity handling: follows `fp32_add`, including NaN result for `+Inf + -Inf`
- zero handling: follows `fp32_add`, including the sign of zero selected by `fp32_add`
- subnormal handling: follows `fp32_add` exactly

Worked examples:

- If `in_a00 = 0x3F800000` and `in_b00 = 0x40000000`, then `out_c00 = 0x40400000`.
- If `in_a01 = 0x7F800000` and `in_b01 = 0xFF800000`, then `out_c01` is the `fp32_add(0x7F800000, 0xFF800000)` result.
- If `in_a10 = 0x80000000` and `in_b10 = 0x00000000`, then `out_c10` is the `fp32_add(0x80000000, 0x00000000)` result.

Output cardinality is 1:1. Every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

On reset:

- `out_valid = 0`
- `out_c00 = 0x00000000`
- `out_c01 = 0x00000000`
- `out_c10 = 0x00000000`
- `out_c11 = 0x00000000`

The block has no other architectural state.

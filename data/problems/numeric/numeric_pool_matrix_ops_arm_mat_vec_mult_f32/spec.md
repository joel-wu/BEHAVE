# MatrixVectorMultiplyF32_4x4

This module computes the product of one 4×4 single-precision floating-point matrix and one 4-element single-precision floating-point vector, producing one 4-element single-precision floating-point result vector per accepted input transaction. The computation is a row-wise dot product with fixed left-fold accumulation order per row: each output is `(((a[r][0] * v[0]) + (a[r][1] * v[1])) + (a[r][2] * v[2])) + (a[r][3] * v[3])`. All arithmetic uses IEEE-754 binary32 behavior as provided by `fp_lib`: round-to-nearest-even, NaN propagation, infinity handling, signed zero handling, and preserved subnormal inputs and gradual underflow.

## Interface

### RTL interface

```systemverilog
module matrix_vector_multiply_f32_4x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a02,
    input  logic [31:0] in_a03,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,
    input  logic [31:0] in_a12,
    input  logic [31:0] in_a13,
    input  logic [31:0] in_a20,
    input  logic [31:0] in_a21,
    input  logic [31:0] in_a22,
    input  logic [31:0] in_a23,
    input  logic [31:0] in_a30,
    input  logic [31:0] in_a31,
    input  logic [31:0] in_a32,
    input  logic [31:0] in_a33,
    input  logic [31:0] in_v0,
    input  logic [31:0] in_v1,
    input  logic [31:0] in_v2,
    input  logic [31:0] in_v3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class MatrixVectorMultiplyF32_4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_v0, in_v1, in_v2, in_v3
    ):
        pass
```

The `process(...)` method returns exactly one dict per accepted input transaction, with keys `out_y0`, `out_y1`, `out_y2`, and `out_y3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `matrix_vector_multiply_f32_4x4`: top-level module implementing one 4×4 matrix-vector multiply transaction.

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

All matrix elements `in_aij`, vector elements `in_vk`, and outputs `out_yl` are 32-bit IEEE-754 binary32 bit patterns carried as unsigned 32-bit ports.

One accepted input transaction consumes:
- matrix row 0: `in_a00`, `in_a01`, `in_a02`, `in_a03`
- matrix row 1: `in_a10`, `in_a11`, `in_a12`, `in_a13`
- matrix row 2: `in_a20`, `in_a21`, `in_a22`, `in_a23`
- matrix row 3: `in_a30`, `in_a31`, `in_a32`, `in_a33`
- vector: `in_v0`, `in_v1`, `in_v2`, `in_v3`

It produces exactly one output transaction containing:
- `out_y0`: row 0 dot product
- `out_y1`: row 1 dot product
- `out_y2`: row 2 dot product
- `out_y3`: row 3 dot product

Per row, multiplication and accumulation order is fixed and shall not be reassociated. Define:

- `p00 = fp32_mul(in_a00, in_v0)`
- `p01 = fp32_mul(in_a01, in_v1)`
- `p02 = fp32_mul(in_a02, in_v2)`
- `p03 = fp32_mul(in_a03, in_v3)`
- `s00 = fp32_add(p00, p01)`
- `s01 = fp32_add(s00, p02)`
- `out_y0 = fp32_add(s01, p03)`

- `p10 = fp32_mul(in_a10, in_v0)`
- `p11 = fp32_mul(in_a11, in_v1)`
- `p12 = fp32_mul(in_a12, in_v2)`
- `p13 = fp32_mul(in_a13, in_v3)`
- `s10 = fp32_add(p10, p11)`
- `s11 = fp32_add(s10, p12)`
- `out_y1 = fp32_add(s11, p13)`

- `p20 = fp32_mul(in_a20, in_v0)`
- `p21 = fp32_mul(in_a21, in_v1)`
- `p22 = fp32_mul(in_a22, in_v2)`
- `p23 = fp32_mul(in_a23, in_v3)`
- `s20 = fp32_add(p20, p21)`
- `s21 = fp32_add(s20, p22)`
- `out_y2 = fp32_add(s21, p23)`

- `p30 = fp32_mul(in_a30, in_v0)`
- `p31 = fp32_mul(in_a31, in_v1)`
- `p32 = fp32_mul(in_a32, in_v2)`
- `p33 = fp32_mul(in_a33, in_v3)`
- `s30 = fp32_add(p30, p31)`
- `s31 = fp32_add(s30, p32)`
- `out_y3 = fp32_add(s31, p33)`

The arithmetic primitives are the `fp_lib` operations `fp32_mul` and `fp32_add`. Their bit-level behavior is the contract for this module:
- rounding mode: round-to-nearest-even
- NaN inputs: quiet NaN propagates per `fp_lib`
- `+Inf` and `-Inf`: handled per `fp_lib`
- `+0` and `-0`: handled per `fp_lib`
- subnormal inputs: preserved without flushing
- underflow results: gradual underflow with nearest-even rounding

No saturation, clipping, integer reinterpretation, or special-case simplification is applied beyond what `fp_lib` defines.

Worked example for operation order: if row 0 products are `p00`, `p01`, `p02`, `p03`, the result is `fp32_add(fp32_add(fp32_add(p00, p01), p02), p03)`. A balanced tree such as `fp32_add(fp32_add(p00, p01), fp32_add(p02, p03))` is not permitted.

## Reset Behavior

Reset is synchronous and active high.

Architectural state: none.

After reset:
- `out_y0 = 32'h00000000`
- `out_y1 = 32'h00000000`
- `out_y2 = 32'h00000000`
- `out_y3 = 32'h00000000`

Since the block is stateless at the transaction level, reset does not alter any internal accumulated value or history beyond clearing output registers if an implementation uses them. The first accepted input after reset is processed independently of all prior activity.

# Cholesky4x4Fp32

This module performs one 4×4 floating-point Cholesky decomposition transaction on a single-precision input matrix and returns a 4×4 lower-triangular result matrix. The computation is the column-oriented Cholesky algorithm specialized to matrix order 4: for each column `i`, elements `L[j][i]` for `j >= i` are formed by subtracting the dot product of previously computed columns from `A[j][i]`, the diagonal candidate is checked for strict positivity, and the whole column segment is scaled by `1 / sqrt(diagonal)`. Arithmetic uses IEEE-754 binary32 behavior through `fp32_mul`, `fp32_sub`, `fp32_div`, `fp32_sqrt`, and `fp32_cmp`.

## Interface

### RTL interface

```systemverilog
module cholesky4x4_fp32 (
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

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_l00,
    output logic [31:0] out_l01,
    output logic [31:0] out_l02,
    output logic [31:0] out_l03,
    output logic [31:0] out_l10,
    output logic [31:0] out_l11,
    output logic [31:0] out_l12,
    output logic [31:0] out_l13,
    output logic [31:0] out_l20,
    output logic [31:0] out_l21,
    output logic [31:0] out_l22,
    output logic [31:0] out_l23,
    output logic [31:0] out_l30,
    output logic [31:0] out_l31,
    output logic [31:0] out_l32,
    output logic [31:0] out_l33,
    output logic [1:0]  out_status
);
```

`in_aXY` and `out_lXY` are raw FP32 bit patterns.

### Behavior interface

```python
class Cholesky4x4Fp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33
    ):
        pass
```

`process()` returns exactly one output transaction as a dict with keys:
`out_l00`, `out_l01`, `out_l02`, `out_l03`,
`out_l10`, `out_l11`, `out_l12`, `out_l13`,
`out_l20`, `out_l21`, `out_l22`, `out_l23`,
`out_l30`, `out_l31`, `out_l32`, `out_l33`,
and `out_status`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_l00`, `out_l01`, `out_l02`, `out_l03`, `out_l10`, `out_l11`, `out_l12`,
  `out_l13`, `out_l20`, `out_l21`, `out_l22`, `out_l23`, `out_l30`, `out_l31`,
  `out_l32`, `out_l33`: 32.
- `out_status`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cholesky4x4_fp32`: top-level 4×4 FP32 Cholesky decomposition kernel.

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

The accepted input represents a 4×4 matrix `A` in row-major port naming:
- row 0: `A[0][0]=in_a00`, `A[0][1]=in_a01`, `A[0][2]=in_a02`, `A[0][3]=in_a03`
- row 1: `A[1][0]=in_a10`, `A[1][1]=in_a11`, `A[1][2]=in_a12`, `A[1][3]=in_a13`
- row 2: `A[2][0]=in_a20`, `A[2][1]=in_a21`, `A[2][2]=in_a22`, `A[2][3]=in_a23`
- row 3: `A[3][0]=in_a30`, `A[3][1]=in_a31`, `A[3][2]=in_a32`, `A[3][3]=in_a33`

Only the lower-triangular input elements `A[j][i]` with `j >= i` participate in the computation. The upper-triangular input elements are ignored.

The result matrix `L` is lower triangular. Every output transaction contains all 16 matrix entries. Entries above the diagonal are always exactly `0x00000000`:
- `out_l01 = out_l02 = out_l03 = 0x00000000`
- `out_l12 = out_l13 = 0x00000000`
- `out_l23 = 0x00000000`

The algorithm computes temporary lower-triangular values `G[j][i]` in column order and then scales each finished column. Operation order is fixed as written below.

Initialization:
- `G[0][0] = A[0][0]`
- `G[1][0] = A[1][0]`
- `G[2][0] = A[2][0]`
- `G[3][0] = A[3][0]`

Column 0:
- Check `fp32_cmp(G[0][0], 0x00000000) > 0`.
- If false, terminate with `out_status = 1` and all 16 `out_lXY = 0x00000000`.
- If true, compute:
  - `R0 = fp32_div(0x3F800000, fp32_sqrt(G[0][0]))`
  - `L[0][0] = fp32_mul(G[0][0], R0)`
  - `L[1][0] = fp32_mul(G[1][0], R0)`
  - `L[2][0] = fp32_mul(G[2][0], R0)`
  - `L[3][0] = fp32_mul(G[3][0], R0)`

Column-1 unscaled candidates:
- `G[1][1] = fp32_sub(A[1][1], fp32_mul(L[1][0], L[1][0]))`
- `G[2][1] = fp32_sub(A[2][1], fp32_mul(L[1][0], L[2][0]))`
- `G[3][1] = fp32_sub(A[3][1], fp32_mul(L[1][0], L[3][0]))`

Column 1:
- Check `fp32_cmp(G[1][1], 0x00000000) > 0`.
- If false, terminate with `out_status = 1` and all 16 `out_lXY = 0x00000000`.
- If true, compute:
  - `R1 = fp32_div(0x3F800000, fp32_sqrt(G[1][1]))`
  - `L[1][1] = fp32_mul(G[1][1], R1)`
  - `L[2][1] = fp32_mul(G[2][1], R1)`
  - `L[3][1] = fp32_mul(G[3][1], R1)`

Column-2 unscaled candidates:
- `T20 = fp32_sub(A[2][2], fp32_mul(L[2][0], L[2][0]))`
- `G[2][2] = fp32_sub(T20, fp32_mul(L[2][1], L[2][1]))`
- `T30 = fp32_sub(A[3][2], fp32_mul(L[2][0], L[3][0]))`
- `G[3][2] = fp32_sub(T30, fp32_mul(L[2][1], L[3][1]))`

Column 2:
- Check `fp32_cmp(G[2][2], 0x00000000) > 0`.
- If false, terminate with `out_status = 1` and all 16 `out_lXY = 0x00000000`.
- If true, compute:
  - `R2 = fp32_div(0x3F800000, fp32_sqrt(G[2][2]))`
  - `L[2][2] = fp32_mul(G[2][2], R2)`
  - `L[3][2] = fp32_mul(G[3][2], R2)`

Column-3 unscaled candidate:
- `T31 = fp32_sub(A[3][3], fp32_mul(L[3][0], L[3][0]))`
- `T32 = fp32_sub(T31, fp32_mul(L[3][1], L[3][1]))`
- `G[3][3] = fp32_sub(T32, fp32_mul(L[3][2], L[3][2]))`

Column 3:
- Check `fp32_cmp(G[3][3], 0x00000000) > 0`.
- If false, terminate with `out_status = 1` and all 16 `out_lXY = 0x00000000`.
- If true, compute:
  - `R3 = fp32_div(0x3F800000, fp32_sqrt(G[3][3]))`
  - `L[3][3] = fp32_mul(G[3][3], R3)`

Success result:
- `out_status = 0`
- `out_l00 = L[0][0]`
- `out_l10 = L[1][0]`
- `out_l20 = L[2][0]`
- `out_l30 = L[3][0]`
- `out_l11 = L[1][1]`
- `out_l21 = L[2][1]`
- `out_l31 = L[3][1]`
- `out_l22 = L[2][2]`
- `out_l32 = L[3][2]`
- `out_l33 = L[3][3]`
- all above-diagonal outputs are `0x00000000`

Failure behavior is all-or-nothing: if any diagonal check fails, no partially computed column is reported. The module returns `out_status = 1` and every matrix output is `0x00000000`.

Worked example of the required failure rule:
- If column 0 succeeds and column 1 fails, `out_l00`, `out_l10`, `out_l20`, and `out_l30` are still returned as `0x00000000`, not as the successfully computed partial column.

Edge-case handling follows the `fp_lib` operations named above:
- `fp32_cmp` is the ordering predicate used for every diagonal check. NaN operands do not compare greater than zero, so any NaN diagonal candidate causes failure.
- `fp32_sqrt`, `fp32_div`, `fp32_mul`, and `fp32_sub` use `fp_lib` IEEE-754 binary32 semantics with round-to-nearest-even.
- Subnormal FP32 inputs retain their values; outputs use gradual underflow. Positive subnormal diagonal candidates pass the strict positivity check.
- `+0` and `-0` both fail the strict `> 0` diagonal test.
- `+Inf` diagonal candidates pass the positivity check; resulting arithmetic follows the `fp_lib` rules.
- `-Inf` diagonal candidates fail the positivity check.
- Because the algorithm ignores `A[0][1]`, `A[0][2]`, `A[0][3]`, `A[1][2]`, `A[1][3]`, and `A[2][3]`, those inputs do not affect any output.

Output cardinality is exactly 1:1. Every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high through `rst`.

Architectural state:
- The module is stateless with respect to the mathematical computation. No matrix contents, partial sums, or history are retained across transactions.

Output port values after reset:
- `out_valid = 0`
- `out_l00 = 0x00000000`
- `out_l01 = 0x00000000`
- `out_l02 = 0x00000000`
- `out_l03 = 0x00000000`
- `out_l10 = 0x00000000`
- `out_l11 = 0x00000000`
- `out_l12 = 0x00000000`
- `out_l13 = 0x00000000`
- `out_l20 = 0x00000000`
- `out_l21 = 0x00000000`
- `out_l22 = 0x00000000`
- `out_l23 = 0x00000000`
- `out_l30 = 0x00000000`
- `out_l31 = 0x00000000`
- `out_l32 = 0x00000000`
- `out_l33 = 0x00000000`
- `out_status = 0`

`in_ready` may be asserted or deasserted during reset; immediately after reset is released, the module shall be able to accept a new input transaction and eventually produce one output transaction for it.

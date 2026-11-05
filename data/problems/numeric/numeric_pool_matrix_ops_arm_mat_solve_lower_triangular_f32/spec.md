# LowerTriangularSolveF32_4x1

This block solves one 4x1 linear system `LT * X = A` by forward substitution, where `LT` is a 4x4 lower-triangular FP32 matrix stored in external memory and `A` is a 4-element FP32 right-hand-side vector stored in external memory. One accepted input transaction names the base addresses of one matrix and one vector; the block reads the required coefficients, computes `X[0]` through `X[3]` in order using the standard lower-triangular recurrence, and produces exactly one output transaction containing the four FP32 solution elements and a singular flag.

## Interface

### RTL interface

```systemverilog
module lower_triangular_solve_f32_4x1 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_lt_base,
    input  logic [3:0]  in_a_base,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_x0,
    output logic [31:0] out_x1,
    output logic [31:0] out_x2,
    output logic [31:0] out_x3,
    output logic        out_singular,

    output logic        lt_req_valid,
    output logic [3:0]  lt_req_addr,
    input  logic        lt_req_ready,
    input  logic        lt_resp_valid,
    input  logic [31:0] lt_resp_rdata,

    output logic        a_req_valid,
    output logic [3:0]  a_req_addr,
    input  logic        a_req_ready,
    input  logic        a_resp_valid,
    input  logic [31:0] a_resp_rdata
);
```

### Behavior interface

```python
class LowerTriangularSolveF32_4x1:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_lt_base, in_a_base): ...
```

The `process()` return value is one dict with keys:
`out_x0`, `out_x1`, `out_x2`, `out_x3`, `out_singular`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_singular`: 1.
- `out_x0`, `out_x1`, `out_x2`, `out_x3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.lt and self.a are read-only views, each containing 16 unsigned 32-bit words.
Default construction uses zero-filled placeholders. The environment supplies the
configured images before processing and across resets. Memory-request pins are not
return fields.

## Modules

- `lower_triangular_solve_f32_4x1`: top-level solver. No helper modules are required.

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

The block is single-clock, single-transaction, and 1:1: each accepted input transaction produces exactly one output transaction.

`lt` is a read-only external memory of 16 words, each word a raw IEEE-754 binary32 bit pattern. `a` is a read-only external memory of 16 words, each word a raw IEEE-754 binary32 bit pattern.

For one accepted input transaction:

- `in_lt_base` is a 4-bit unsigned base address into `lt`.
- `in_a_base` is a 4-bit unsigned base address into `a`.

The solver reads these ten matrix elements from `lt`, in row-major positions of the lower-triangular 4x4 matrix:

- `LT[0][0] = lt[in_lt_base + 0]`
- `LT[1][0] = lt[in_lt_base + 4]`
- `LT[1][1] = lt[in_lt_base + 5]`
- `LT[2][0] = lt[in_lt_base + 8]`
- `LT[2][1] = lt[in_lt_base + 9]`
- `LT[2][2] = lt[in_lt_base + 10]`
- `LT[3][0] = lt[in_lt_base + 12]`
- `LT[3][1] = lt[in_lt_base + 13]`
- `LT[3][2] = lt[in_lt_base + 14]`
- `LT[3][3] = lt[in_lt_base + 15]`

The solver reads these four vector elements from `a`:

- `A[0] = a[in_a_base + 0]`
- `A[1] = a[in_a_base + 1]`
- `A[2] = a[in_a_base + 2]`
- `A[3] = a[in_a_base + 3]`

All address arithmetic is modulo 16 on the 4-bit memory address ports.

The singular flag is derived only from the four diagonal elements. Define `fp32_is_zero(x)` as true when bits `[30:0]` of `x` are all zero, so both `0x00000000` and `0x80000000` are zero. Then:

`out_singular = fp32_is_zero(LT[0][0]) || fp32_is_zero(LT[1][1]) || fp32_is_zero(LT[2][2]) || fp32_is_zero(LT[3][3])`

The solution datapath is evaluated in the same way for every accepted input transaction, in this exact operation order, using `fp32_mul`, `fp32_sub`, and `fp32_div` on FP32 bit patterns:

1. `X0_calc = fp32_div(A[0], LT[0][0])`
2. `T1 = fp32_sub(A[1], fp32_mul(LT[1][0], X0_calc))`
3. `X1_calc = fp32_div(T1, LT[1][1])`
4. `T2A = fp32_sub(A[2], fp32_mul(LT[2][0], X0_calc))`
5. `T2 = fp32_sub(T2A, fp32_mul(LT[2][1], X1_calc))`
6. `X2_calc = fp32_div(T2, LT[2][2])`
7. `T3A = fp32_sub(A[3], fp32_mul(LT[3][0], X0_calc))`
8. `T3B = fp32_sub(T3A, fp32_mul(LT[3][1], X1_calc))`
9. `T3 = fp32_sub(T3B, fp32_mul(LT[3][2], X2_calc))`
10. `X3_calc = fp32_div(T3, LT[3][3])`

The output transaction is formed after the datapath evaluation:

- if `out_singular = 1`, then `out_x0 = 32'h00000000`, `out_x1 = 32'h00000000`, `out_x2 = 32'h00000000`, `out_x3 = 32'h00000000`
- if `out_singular = 0`, then `out_x0 = X0_calc`, `out_x1 = X1_calc`, `out_x2 = X2_calc`, `out_x3 = X3_calc`

Floating-point arithmetic contract:

- Every matrix element, vector element, intermediate, and output data field is an IEEE-754 binary32 bit pattern.
- `fp32_mul`, `fp32_sub`, and `fp32_div` are the `fp_lib` operations with their default behavior.
- NaN propagation, infinities, signed zeros, and subnormal handling follow `fp_lib`.
- The singular test depends only on exact zero diagonals as defined above; NaN and infinity on the diagonal do not set `out_singular` unless the bit pattern is `+0` or `-0`.
- Even when `out_singular = 1`, the internal arithmetic sequence above is still the defined datapath evaluation order; only the reported outputs are forced to zero.
- If the matrix is not singular and any operand or intermediate is NaN, infinity, signed zero, or subnormal, the corresponding outputs are whatever the specified `fp_lib` operation sequence produces.

The block performs no matrix-size checking and supports only this fixed problem shape: one 4x4 lower-triangular matrix times one 4x1 right-hand side.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_x0 = 32'h00000000`
- `out_x1 = 32'h00000000`
- `out_x2 = 32'h00000000`
- `out_x3 = 32'h00000000`
- `out_singular = 1'b0`
- `lt_req_valid = 0`
- `lt_req_addr = 4'h0`
- `a_req_valid = 0`
- `a_req_addr = 4'h0`

Architectural state after reset:

- no transaction is in flight
- stored base addresses are `4'h0`
- any internal counters are `0`
- any internal registers holding fetched matrix or vector elements are `32'h00000000`
- any internal registers holding computed solution values are `32'h00000000`

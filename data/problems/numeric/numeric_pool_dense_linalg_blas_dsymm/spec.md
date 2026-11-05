# Symmetric Matrix Multiply 2x2

This module performs one 2×2 real matrix update corresponding to a fixed-size instance of symmetric matrix-matrix multiply. One accepted input transaction computes a complete 2×2 output matrix according to either `C := alpha*A*B + beta*C` or `C := alpha*B*A + beta*C`, where `A` is symmetric and only one triangular half of `A` is considered valid. Arithmetic is on 64-bit integer bit patterns with wraparound unsigned addition and multiplication modulo 2^64; no floating-point interpretation is used. The computation order is fixed to the direct 2×2 matrix formulas derived from the selected side and the symmetric off-diagonal element selected by `uplo`.

## Interface

### RTL interface

```systemverilog
module symmetric_matrix_multiply_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,
    input  logic        in_uplo,
    input  logic [63:0] in_alpha,
    input  logic [63:0] in_beta,

    input  logic [63:0] in_a00,
    input  logic [63:0] in_a01,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,

    input  logic [63:0] in_b00,
    input  logic [63:0] in_b10,
    input  logic [63:0] in_b01,
    input  logic [63:0] in_b11,

    input  logic [63:0] in_c00,
    input  logic [63:0] in_c10,
    input  logic [63:0] in_c01,
    input  logic [63:0] in_c11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_c00,
    output logic [63:0] out_c10,
    output logic [63:0] out_c01,
    output logic [63:0] out_c11
);
```

Signal meanings:
- `in_side = 0`: left-sided update `alpha*A*B + beta*C`.
- `in_side = 1`: right-sided update `alpha*B*A + beta*C`.
- `in_uplo = 0`: use the upper-triangular storage view of `A`, so the symmetric off-diagonal element is `a01`.
- `in_uplo = 1`: use the lower-triangular storage view of `A`, so the symmetric off-diagonal element is `a10`.

Matrix element packing:
- `B = [[b00, b01], [b10, b11]]`
- `C = [[c00, c01], [c10, c11]]`
- outputs are packed identically:
  - `out_c00` = row 0, column 0
  - `out_c10` = row 1, column 0
  - `out_c01` = row 0, column 1
  - `out_c11` = row 1, column 1

### Behavior interface

```python
class SymmetricMatrixMultiply2x2:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_uplo,
        in_alpha,
        in_beta,
        in_a00,
        in_a01,
        in_a10,
        in_a11,
        in_b00,
        in_b10,
        in_b01,
        in_b11,
        in_c00,
        in_c10,
        in_c01,
        in_c11,
    ):
        pass
```

`process(...)` returns one dict with keys:
- `out_c00`
- `out_c10`
- `out_c01`
- `out_c11`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c10`, `out_c11`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `symmetric_matrix_multiply_2x2`: top-level module that accepts one input transaction and produces exactly one output transaction.

## Functional Behavior

All ports carrying matrix elements or scalars are 64-bit unsigned integers. Every addition and multiplication is performed modulo 2^64. Define:
- `U64(x) = x mod 2^64`
- `add(x, y) = U64(x + y)`
- `mul(x, y) = U64(x * y)`

Define the effective symmetric matrix `Asym` from the input triangular view:
- If `in_uplo = 0`, `Asym = [[a00, a01], [a01, a11]]`
- If `in_uplo = 1`, `Asym = [[a00, a10], [a10, a11]]`

Equivalently, let:
- `s = in_a01` when `in_uplo = 0`
- `s = in_a10` when `in_uplo = 1`

Then:
- `Asym[0][0] = in_a00`
- `Asym[0][1] = s`
- `Asym[1][0] = s`
- `Asym[1][1] = in_a11`

Define:
- `B = [[in_b00, in_b01], [in_b10, in_b11]]`
- `Cin = [[in_c00, in_c01], [in_c10, in_c11]]`

There is no quick-return special case. Every accepted input transaction produces one output transaction whose matrix is computed by the formulas below.

If `in_side = 0`, compute `P = Asym * B` with the fixed elementwise formulas:
- `p00 = add(mul(in_a00, in_b00), mul(s,      in_b10))`
- `p01 = add(mul(in_a00, in_b01), mul(s,      in_b11))`
- `p10 = add(mul(s,      in_b00), mul(in_a11, in_b10))`
- `p11 = add(mul(s,      in_b01), mul(in_a11, in_b11))`

Then scale and accumulate:
- `r00 = add(mul(in_alpha, p00), mul(in_beta, in_c00))`
- `r01 = add(mul(in_alpha, p01), mul(in_beta, in_c01))`
- `r10 = add(mul(in_alpha, p10), mul(in_beta, in_c10))`
- `r11 = add(mul(in_alpha, p11), mul(in_beta, in_c11))`

If `in_side = 1`, compute `P = B * Asym` with the fixed elementwise formulas:
- `p00 = add(mul(in_b00, in_a00), mul(in_b01, s))`
- `p01 = add(mul(in_b00, s),      mul(in_b01, in_a11))`
- `p10 = add(mul(in_b10, in_a00), mul(in_b11, s))`
- `p11 = add(mul(in_b10, s),      mul(in_b11, in_a11))`

Then scale and accumulate:
- `r00 = add(mul(in_alpha, p00), mul(in_beta, in_c00))`
- `r01 = add(mul(in_alpha, p01), mul(in_beta, in_c01))`
- `r10 = add(mul(in_alpha, p10), mul(in_beta, in_c10))`
- `r11 = add(mul(in_alpha, p11), mul(in_beta, in_c11))`

The single output transaction is:
- `out_c00 = r00`
- `out_c10 = r10`
- `out_c01 = r01`
- `out_c11 = r11`

Worked example for the `uplo` selection rule:
- If `in_uplo = 0`, `in_a10` is ignored and both off-diagonal uses take `in_a01`.
- If `in_uplo = 1`, `in_a01` is ignored and both off-diagonal uses take `in_a10`.

Worked example for left side with `in_uplo = 0`:
- `out_c10 = alpha * (a01*b00 + a11*b10) + beta*c10`, with each multiply and add reduced modulo 2^64 after that operation.

## Reset Behavior

The design has no architectural state beyond output holding state required by the handshake wrapper.

On active-high synchronous reset:
- `out_valid = 0`
- `in_ready = 1`
- `out_c00 = 0`
- `out_c10 = 0`
- `out_c01 = 0`
- `out_c11 = 0`

No matrix contents or counters persist between transactions.

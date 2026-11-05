# PackedTriangularSolve4

PackedTriangularSolve4 solves a 4×4 triangular linear system by in-place forward or backward substitution using a matrix stored in packed column-major form. Each accepted transaction provides the packed matrix, the right-hand-side vector, and mode controls selecting upper or lower triangular form, transpose or non-transpose solve, and unit or non-unit diagonal. The kernel performs the same substitution order and arithmetic order as the BLAS triangular packed solve algorithm, restricted to fixed size `N = 4` and unit-stride vectors.

## Interface

### RTL interface

```systemverilog
module PackedTriangularSolve4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,    // 1 = upper triangular, 0 = lower triangular
    input  logic         in_trans,   // 0 = solve A*x=b, 1 = solve A^T*x=b
    input  logic         in_diag,    // 0 = non-unit diagonal, 1 = unit diagonal
    input  logic [63:0]  in_ap0,
    input  logic [63:0]  in_ap1,
    input  logic [63:0]  in_ap2,
    input  logic [63:0]  in_ap3,
    input  logic [63:0]  in_ap4,
    input  logic [63:0]  in_ap5,
    input  logic [63:0]  in_ap6,
    input  logic [63:0]  in_ap7,
    input  logic [63:0]  in_ap8,
    input  logic [63:0]  in_ap9,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3
);
```

### Behavior interface

```python
class PackedTriangularSolve4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_diag,
        in_ap0,
        in_ap1,
        in_ap2,
        in_ap3,
        in_ap4,
        in_ap5,
        in_ap6,
        in_ap7,
        in_ap8,
        in_ap9,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
    ):
        pass
```

The return value is one dictionary with keys `out_x0`, `out_x1`, `out_x2`, and `out_x3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0`, `out_x1`, `out_x2`, `out_x3`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `PackedTriangularSolve4`: top-level single-transaction triangular packed solve kernel.

## Functional Behavior

All data ports carrying matrix and vector elements are signed two's-complement fixed-point values in Q32.32 format, stored in 64-bit containers.

For a 64-bit port value `p`, the represented fixed-point integer is `to_signed(p, 64)`. The numerical value is that signed integer divided by `2^32`.

All arithmetic inside the solve is fixed-point arithmetic on these Q32.32 values:
- addition and subtraction are signed 64-bit two's-complement operations with truncation to 64 bits after each operation.
- multiplication of two Q32.32 values forms the signed integer product of the two signed 64-bit operands, then performs an arithmetic right shift by 32 bit positions, then truncates to 64 bits.
- division of one Q32.32 value by another Q32.32 value forms `(numerator << 32)` and then divides by the denominator using signed integer division truncated toward zero, then truncates the quotient to 64 bits.
- if a non-unit diagonal division has denominator zero, the quotient is defined as zero.

The packed matrix input `in_ap0..in_ap9` represents a 4×4 triangular matrix in BLAS packed column-major layout.

For `in_uplo = 1` (upper triangular), the packed entries are:
- `in_ap0 = A[0][0]`
- `in_ap1 = A[0][1]`, `in_ap2 = A[1][1]`
- `in_ap3 = A[0][2]`, `in_ap4 = A[1][2]`, `in_ap5 = A[2][2]`
- `in_ap6 = A[0][3]`, `in_ap7 = A[1][3]`, `in_ap8 = A[2][3]`, `in_ap9 = A[3][3]`

For `in_uplo = 0` (lower triangular), the packed entries are:
- `in_ap0 = A[0][0]`, `in_ap1 = A[1][0]`, `in_ap2 = A[2][0]`, `in_ap3 = A[3][0]`
- `in_ap4 = A[1][1]`, `in_ap5 = A[2][1]`, `in_ap6 = A[3][1]`
- `in_ap7 = A[2][2]`, `in_ap8 = A[3][2]`
- `in_ap9 = A[3][3]`

The input vector is `b = [in_x0, in_x1, in_x2, in_x3]`. The output vector is the solved vector `x`, returned as `out_x0..out_x3`.

`in_diag` selects diagonal handling:
- `in_diag = 0`: non-unit diagonal. Diagonal elements stored in the packed matrix are used as divisors.
- `in_diag = 1`: unit diagonal. Every diagonal element is treated as fixed-point value `1.0`, which is bit pattern `64'h0000000100000000`; stored diagonal bit patterns are ignored.

`in_trans` selects the solved system:
- `in_trans = 0`: solve `A * x = b`
- `in_trans = 1`: solve `A^T * x = b`

The substitution order is fixed as follows, and each multiply, subtract, and divide is performed in the stated left-to-right nesting order.

For `in_trans = 0` and `in_uplo = 1`, solve `A * x = b` by backward substitution:
- `x3_pre = b3`
- `x3 = x3_pre` if `in_diag = 1`, else `x3_pre / A[3][3]`
- `x2_pre = b2 - (x3 * A[2][3])`
- `x2 = x2_pre` if `in_diag = 1`, else `x2_pre / A[2][2]`
- `x1_pre = (b1 - (x3 * A[1][3])) - (x2 * A[1][2])`
- `x1 = x1_pre` if `in_diag = 1`, else `x1_pre / A[1][1]`
- `x0_pre = ((b0 - (x3 * A[0][3])) - (x2 * A[0][2])) - (x1 * A[0][1])`
- `x0 = x0_pre` if `in_diag = 1`, else `x0_pre / A[0][0]`

For `in_trans = 0` and `in_uplo = 0`, solve `A * x = b` by forward substitution:
- `x0_pre = b0`
- `x0 = x0_pre` if `in_diag = 1`, else `x0_pre / A[0][0]`
- `x1_pre = b1 - (x0 * A[1][0])`
- `x1 = x1_pre` if `in_diag = 1`, else `x1_pre / A[1][1]`
- `x2_pre = (b2 - (x0 * A[2][0])) - (x1 * A[2][1])`
- `x2 = x2_pre` if `in_diag = 1`, else `x2_pre / A[2][2]`
- `x3_pre = ((b3 - (x0 * A[3][0])) - (x1 * A[3][1])) - (x2 * A[3][2])`
- `x3 = x3_pre` if `in_diag = 1`, else `x3_pre / A[3][3]`

For `in_trans = 1` and `in_uplo = 1`, solve `A^T * x = b`. Since `A` is upper triangular, `A^T` is lower triangular, so forward substitution is used:
- `x0_pre = b0`
- `x0 = x0_pre` if `in_diag = 1`, else `x0_pre / A[0][0]`
- `x1_pre = b1 - (A[0][1] * x0)`
- `x1 = x1_pre` if `in_diag = 1`, else `x1_pre / A[1][1]`
- `x2_pre = (b2 - (A[0][2] * x0)) - (A[1][2] * x1)`
- `x2 = x2_pre` if `in_diag = 1`, else `x2_pre / A[2][2]`
- `x3_pre = ((b3 - (A[0][3] * x0)) - (A[1][3] * x1)) - (A[2][3] * x2)`
- `x3 = x3_pre` if `in_diag = 1`, else `x3_pre / A[3][3]`

For `in_trans = 1` and `in_uplo = 0`, solve `A^T * x = b`. Since `A` is lower triangular, `A^T` is upper triangular, so backward substitution is used:
- `x3_pre = b3`
- `x3 = x3_pre` if `in_diag = 1`, else `x3_pre / A[3][3]`
- `x2_pre = b2 - (A[3][2] * x3)`
- `x2 = x2_pre` if `in_diag = 1`, else `x2_pre / A[2][2]`
- `x1_pre = (b1 - (A[3][1] * x3)) - (A[2][1] * x2)`
- `x1 = x1_pre` if `in_diag = 1`, else `x1_pre / A[1][1]`
- `x0_pre = ((b0 - (A[3][0] * x3)) - (A[2][0] * x2)) - (A[1][0] * x1)`
- `x0 = x0_pre` if `in_diag = 1`, else `x0_pre / A[0][0]`

All outputs are the 64-bit two's-complement Q32.32 bit patterns of the solved vector elements.

## Reset Behavior

Reset is synchronous active-high on `rst`.

The kernel has no architectural state affecting the solved values. After reset, `in_ready` shall be high whenever the kernel can accept a transaction. `out_valid` shall be low until a result is available for an accepted transaction. `out_x0`, `out_x1`, `out_x2`, and `out_x3` shall reset to `64'h0000000000000000`.

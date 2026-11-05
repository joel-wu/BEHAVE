# SymmetricRankK4x4

SymmetricRankK4x4 performs one symmetric rank-k matrix update on a fixed 4×4 output tile using the DSYRK algorithm. The block computes either `C := alpha * A * A^T + beta * C` or `C := alpha * A^T * A + beta * C` for `k = 4`, updates only one selected triangular half of the symmetric output matrix, and leaves the opposite half unchanged. The computation uses signed 32-bit integer arithmetic on all matrix and scalar operands, with the multiply-accumulate order fixed to the loop nesting defined below.

## Interface

### RTL interface

```systemverilog
module symmetric_rank_k4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic signed [31:0] in_alpha,
    input  logic signed [31:0] in_beta,

    input  logic signed [31:0] in_a00,
    input  logic signed [31:0] in_a01,
    input  logic signed [31:0] in_a02,
    input  logic signed [31:0] in_a03,
    input  logic signed [31:0] in_a10,
    input  logic signed [31:0] in_a11,
    input  logic signed [31:0] in_a12,
    input  logic signed [31:0] in_a13,
    input  logic signed [31:0] in_a20,
    input  logic signed [31:0] in_a21,
    input  logic signed [31:0] in_a22,
    input  logic signed [31:0] in_a23,
    input  logic signed [31:0] in_a30,
    input  logic signed [31:0] in_a31,
    input  logic signed [31:0] in_a32,
    input  logic signed [31:0] in_a33,

    input  logic signed [31:0] in_c00,
    input  logic signed [31:0] in_c01,
    input  logic signed [31:0] in_c02,
    input  logic signed [31:0] in_c03,
    input  logic signed [31:0] in_c10,
    input  logic signed [31:0] in_c11,
    input  logic signed [31:0] in_c12,
    input  logic signed [31:0] in_c13,
    input  logic signed [31:0] in_c20,
    input  logic signed [31:0] in_c21,
    input  logic signed [31:0] in_c22,
    input  logic signed [31:0] in_c23,
    input  logic signed [31:0] in_c30,
    input  logic signed [31:0] in_c31,
    input  logic signed [31:0] in_c32,
    input  logic signed [31:0] in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_c00,
    output logic signed [31:0] out_c01,
    output logic signed [31:0] out_c02,
    output logic signed [31:0] out_c03,
    output logic signed [31:0] out_c10,
    output logic signed [31:0] out_c11,
    output logic signed [31:0] out_c12,
    output logic signed [31:0] out_c13,
    output logic signed [31:0] out_c20,
    output logic signed [31:0] out_c21,
    output logic signed [31:0] out_c22,
    output logic signed [31:0] out_c23,
    output logic signed [31:0] out_c30,
    output logic signed [31:0] out_c31,
    output logic signed [31:0] out_c32,
    output logic signed [31:0] out_c33
);
```

`in_uplo` selects which triangular half is updated:
- `1'b1`: update upper triangle, entries with `i <= j`
- `1'b0`: update lower triangle, entries with `i >= j`

`in_trans` selects the DSYRK form:
- `1'b0`: `C := alpha * A * A^T + beta * C`
- `1'b1`: `C := alpha * A^T * A + beta * C`

`A` and `C` are presented as 4×4 matrices in row-major scalar ports, where `aRC` and `cRC` denote row `R`, column `C`.

### Behavior interface

```python
class SymmetricRankK4x4:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_uplo,
        in_trans,
        in_alpha,
        in_beta,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_c00, in_c01, in_c02, in_c03,
        in_c10, in_c11, in_c12, in_c13,
        in_c20, in_c21, in_c22, in_c23,
        in_c30, in_c31, in_c32, in_c33
    ): ...
```

Return one dictionary with the sixteen matrix fields listed below.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c10`, `out_c11`, `out_c12`,
  `out_c13`, `out_c20`, `out_c21`, `out_c22`, `out_c23`, `out_c30`, `out_c31`,
  `out_c32`, `out_c33`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `symmetric_rank_k4x4`: top-level module implementing one 4×4, `k = 4` DSYRK transaction.

## Functional Behavior

All scalar ports are signed 32-bit two's-complement integers. Every matrix element and scalar is interpreted as a signed 32-bit integer. Every output element is the low 32 bits of the mathematically computed signed result, returned as a 32-bit two's-complement bit pattern.

Define matrices:

- `A[r][c]` from the `in_aRC` ports for `r,c in {0,1,2,3}`
- `C_in[r][c]` from the `in_cRC` ports for `r,c in {0,1,2,3}`

The block produces one 4×4 output matrix `C_out`.

The operation is parameterized by:
- `uplo = in_uplo`
- `trans = in_trans`
- `alpha = signed(in_alpha)`
- `beta = signed(in_beta)`

The update domain is:
- if `uplo = 1`, update only coordinates `(i,j)` with `0 <= i <= j < 4`
- if `uplo = 0`, update only coordinates `(i,j)` with `0 <= j <= i < 4`

All coordinates outside the selected triangular half are copied through unchanged:
- if `(i,j)` is not in the selected triangular half, `C_out[i][j] = C_in[i][j]`

For each updated coordinate `(i,j)`, the computation follows one of the two DSYRK forms.

If `trans = 0`, compute `alpha * A * A^T + beta * C` using the fixed accumulation order
`((A[i][0]*A[j][0] + A[i][1]*A[j][1]) + A[i][2]*A[j][2]) + A[i][3]*A[j][3]`.
Equivalently, for each updated `(i,j)`:
- `temp0 = A[i][0] * A[j][0]`
- `temp1 = temp0 + A[i][1] * A[j][1]`
- `temp2 = temp1 + A[i][2] * A[j][2]`
- `temp3 = temp2 + A[i][3] * A[j][3]`
- `C_out[i][j] = alpha * temp3 + beta * C_in[i][j]`

If `trans = 1`, compute `alpha * A^T * A + beta * C` using the fixed accumulation order
`((A[0][i]*A[0][j] + A[1][i]*A[1][j]) + A[2][i]*A[2][j]) + A[3][i]*A[3][j]`.
Equivalently, for each updated `(i,j)`:
- `temp0 = A[0][i] * A[0][j]`
- `temp1 = temp0 + A[1][i] * A[1][j]`
- `temp2 = temp1 + A[2][i] * A[2][j]`
- `temp3 = temp2 + A[3][i] * A[3][j]`
- `C_out[i][j] = alpha * temp3 + beta * C_in[i][j]`

Special cases match the DSYRK control flow for the fixed-size tile:

1. If `alpha = 0`, the selected triangular entries do not reference `A`.
2. If `alpha = 0` and `beta = 0`, every selected triangular entry is set to zero.
3. If `alpha = 0` and `beta != 0`, every selected triangular entry becomes `beta * C_in[i][j]`.
4. No quick-return suppression is performed for this fixed-size transaction because the tile size is always 4×4 and `k` is always 4; each accepted input produces one output transaction.

Arithmetic rules:
- Signed multiplication and addition are mathematical integer operations.
- After the full expression for each output entry is formed, the result is truncated modulo `2^32`.
- The output port value is that truncated 32-bit two's-complement bit pattern.
- Unselected matrix entries are passed through by copying the corresponding input port bit pattern unchanged.

No symmetry repair is performed. If only the upper triangle is selected, the lower triangle outputs are the original lower triangle inputs; if only the lower triangle is selected, the upper triangle outputs are the original upper triangle inputs.

## Reset Behavior

Reset is synchronous active-high via `rst`.

Architectural state:
- The block has no persistent architectural state.

Output interface state after reset:
- `out_valid = 0`
- `out_c00` through `out_c33` = `0`

After reset is deasserted, the block may accept a new input transaction whenever it is not holding an unconsumed output transaction.

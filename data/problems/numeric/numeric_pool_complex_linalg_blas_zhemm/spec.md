# HermitianMatrixMultiplyTile

This module performs one fixed-size complex Hermitian matrix-matrix multiply-and-accumulate tile matching the BLAS ZHEMM operation on a 2×2 output block. One accepted input transaction computes either `C := alpha*A*B + beta*C` or `C := alpha*B*A + beta*C`, where `A` is Hermitian and only one triangular half of `A` is supplied logically through the `uplo` selector. Complex numbers use signed 32-bit real and 32-bit imaginary components. The Hermitian rule is enforced exactly as in ZHEMM: off-diagonal elements from the unstored half are obtained by conjugating the stored half, and diagonal elements are treated as purely real by discarding their imaginary part.

## Interface

### RTL interface

```systemverilog
module hermitian_matrix_multiply_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_uplo,

    input  logic signed [31:0] in_alpha_re,
    input  logic signed [31:0] in_alpha_im,
    input  logic signed [31:0] in_beta_re,
    input  logic signed [31:0] in_beta_im,

    input  logic signed [31:0] in_a00_re,
    input  logic signed [31:0] in_a00_im,
    input  logic signed [31:0] in_a01_re,
    input  logic signed [31:0] in_a01_im,
    input  logic signed [31:0] in_a10_re,
    input  logic signed [31:0] in_a10_im,
    input  logic signed [31:0] in_a11_re,
    input  logic signed [31:0] in_a11_im,

    input  logic signed [31:0] in_b00_re,
    input  logic signed [31:0] in_b00_im,
    input  logic signed [31:0] in_b01_re,
    input  logic signed [31:0] in_b01_im,
    input  logic signed [31:0] in_b10_re,
    input  logic signed [31:0] in_b10_im,
    input  logic signed [31:0] in_b11_re,
    input  logic signed [31:0] in_b11_im,

    input  logic signed [31:0] in_c00_re,
    input  logic signed [31:0] in_c00_im,
    input  logic signed [31:0] in_c01_re,
    input  logic signed [31:0] in_c01_im,
    input  logic signed [31:0] in_c10_re,
    input  logic signed [31:0] in_c10_im,
    input  logic signed [31:0] in_c11_re,
    input  logic signed [31:0] in_c11_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_c00_re,
    output logic signed [31:0] out_c00_im,
    output logic signed [31:0] out_c01_re,
    output logic signed [31:0] out_c01_im,
    output logic signed [31:0] out_c10_re,
    output logic signed [31:0] out_c10_im,
    output logic signed [31:0] out_c11_re,
    output logic signed [31:0] out_c11_im
);
```

Port encodings:

- `in_side = 0`: left-side operation, `C := alpha*A*B + beta*C`.
- `in_side = 1`: right-side operation, `C := alpha*B*A + beta*C`.
- `in_uplo = 1`: `A` is supplied in upper-triangular form.
- `in_uplo = 0`: `A` is supplied in lower-triangular form.

### Behavior interface

```python
class HermitianMatrixMultiplyTile:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_side,
        in_uplo,
        in_alpha_re,
        in_alpha_im,
        in_beta_re,
        in_beta_im,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_b00_re,
        in_b00_im,
        in_b01_re,
        in_b01_im,
        in_b10_re,
        in_b10_im,
        in_b11_re,
        in_b11_im,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
    ): ...
```

The returned dict contains keys `out_c00_re`, `out_c00_im`, `out_c01_re`, `out_c01_im`, `out_c10_re`, `out_c10_im`, `out_c11_re`, and `out_c11_im`.

Each `process` call represents one accepted request and returns exactly these
eight fields. `in_side` and `in_uplo` are unsigned one-bit values; all other
arguments and results are unsigned 32-bit words encoding signed two's-complement
components. Every request, including `alpha=0` or a zero matrix, returns the
full dictionary, not `None` or a list. Matrices are supplied anew on each call;
no previous result is retained. Construction and `reset()` have no numerical
effect; `reset()` returns `None`. Idle and output-stall cycles require no calls.

## Modules

- `hermitian_matrix_multiply_tile`: top-level module implementing one 2×2 complex ZHEMM tile.

## Functional Behavior

All real and imaginary input fields are signed 32-bit two's-complement integers. A complex value `(x_re, x_im)` represents `x_re + i*x_im`. Every scalar addition, subtraction, and multiplication is performed in unbounded mathematical integer precision, and each output component is truncated to signed 32-bit two's-complement by keeping the least-significant 32 bits of the final component result.

Let:

- `alpha = (alpha_re, alpha_im)`
- `beta = (beta_re, beta_im)`
- `B[r][c]` be the 2×2 complex matrix assembled from `in_b**`
- `C[r][c]` be the 2×2 complex matrix assembled from `in_c**`

Let `A_in[0][0]`, `A_in[0][1]`, `A_in[1][0]`, `A_in[1][1]` be assembled from the four complex `in_a**` inputs. The effective Hermitian matrix `A` is derived from `A_in` and `in_uplo` as follows:

- Diagonal entries are purely real:
  - `A[0][0] = (real(A_in[0][0]), 0)`
  - `A[1][1] = (real(A_in[1][1]), 0)`
- If `in_uplo = 1`:
  - `A[0][1] = A_in[0][1]`
  - `A[1][0] = conj(A_in[0][1])`
  - `A_in[1][0]` is ignored.
- If `in_uplo = 0`:
  - `A[1][0] = A_in[1][0]`
  - `A[0][1] = conj(A_in[1][0])`
  - `A_in[0][1]` is ignored.

Complex conjugation is `(x_re, x_im) -> (x_re, -x_im)`.

Complex multiplication uses:
- `(a_re + i*a_im) * (b_re + i*b_im) = (a_re*b_re - a_im*b_im, a_re*b_im + a_im*b_re)`

Complex addition and subtraction are component-wise.

If `in_side = 0`, the module computes the left-side product:
- `P[0][0] = A[0][0]*B[0][0] + A[0][1]*B[1][0]`
- `P[0][1] = A[0][0]*B[0][1] + A[0][1]*B[1][1]`
- `P[1][0] = A[1][0]*B[0][0] + A[1][1]*B[1][0]`
- `P[1][1] = A[1][0]*B[0][1] + A[1][1]*B[1][1]`

If `in_side = 1`, the module computes the right-side product:
- `P[0][0] = B[0][0]*A[0][0] + B[0][1]*A[1][0]`
- `P[0][1] = B[0][0]*A[0][1] + B[0][1]*A[1][1]`
- `P[1][0] = B[1][0]*A[0][0] + B[1][1]*A[1][0]`
- `P[1][1] = B[1][0]*A[0][1] + B[1][1]*A[1][1]`

The output matrix is then:
- `C_out[r][c] = alpha*P[r][c] + beta*C[r][c]` for `r,c ∈ {0,1}`

No special-case shortcuts are required for `alpha = 0`, `beta = 0`, or `beta = 1`; the arithmetic definition above is the complete contract and yields the same result.

Output mapping:
- `out_c00_* = C_out[0][0]`
- `out_c01_* = C_out[0][1]`
- `out_c10_* = C_out[1][0]`
- `out_c11_* = C_out[1][1]`

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state:
- This module has no architectural state.

Output ports after reset and until the first completed input transaction:
- `out_valid = 0`
- `out_c00_re = 0`
- `out_c00_im = 0`
- `out_c01_re = 0`
- `out_c01_im = 0`
- `out_c10_re = 0`
- `out_c10_im = 0`
- `out_c11_re = 0`
- `out_c11_im = 0`

# HermitianMatrixVectorStep4

This module performs one complete 4-element complex Hermitian matrix-vector multiply-and-accumulate transaction, computing `y := alpha*A*x + beta*y` for a fixed 4×4 matrix. The matrix is supplied in packed triangular form selected by `in_uplo`, and the computation follows the Hermitian BLAS access rule exactly: off-diagonal elements from the stored triangle are used directly, their mirrored counterparts are the complex conjugates, and diagonal elements contribute only through their real part with imaginary part forced to zero.

## Interface

### RTL interface

```systemverilog
module HermitianMatrixVectorStep4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,

    input  logic [31:0]  in_alpha_re,
    input  logic [31:0]  in_alpha_im,

    input  logic [31:0]  in_beta_re,
    input  logic [31:0]  in_beta_im,

    input  logic [31:0]  in_x0_re,
    input  logic [31:0]  in_x0_im,
    input  logic [31:0]  in_x1_re,
    input  logic [31:0]  in_x1_im,
    input  logic [31:0]  in_x2_re,
    input  logic [31:0]  in_x2_im,
    input  logic [31:0]  in_x3_re,
    input  logic [31:0]  in_x3_im,

    input  logic [31:0]  in_y0_re,
    input  logic [31:0]  in_y0_im,
    input  logic [31:0]  in_y1_re,
    input  logic [31:0]  in_y1_im,
    input  logic [31:0]  in_y2_re,
    input  logic [31:0]  in_y2_im,
    input  logic [31:0]  in_y3_re,
    input  logic [31:0]  in_y3_im,

    input  logic [31:0]  in_a00_re,
    input  logic [31:0]  in_a00_im,
    input  logic [31:0]  in_a01_re,
    input  logic [31:0]  in_a01_im,
    input  logic [31:0]  in_a02_re,
    input  logic [31:0]  in_a02_im,
    input  logic [31:0]  in_a03_re,
    input  logic [31:0]  in_a03_im,
    input  logic [31:0]  in_a11_re,
    input  logic [31:0]  in_a11_im,
    input  logic [31:0]  in_a12_re,
    input  logic [31:0]  in_a12_im,
    input  logic [31:0]  in_a13_re,
    input  logic [31:0]  in_a13_im,
    input  logic [31:0]  in_a22_re,
    input  logic [31:0]  in_a22_im,
    input  logic [31:0]  in_a23_re,
    input  logic [31:0]  in_a23_im,
    input  logic [31:0]  in_a33_re,
    input  logic [31:0]  in_a33_im,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [31:0]  out_y0_re,
    output logic [31:0]  out_y0_im,
    output logic [31:0]  out_y1_re,
    output logic [31:0]  out_y1_im,
    output logic [31:0]  out_y2_re,
    output logic [31:0]  out_y2_im,
    output logic [31:0]  out_y3_re,
    output logic [31:0]  out_y3_im
);
```

### Behavior interface

```python
class HermitianMatrixVectorStep4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_alpha_re, in_alpha_im,
        in_beta_re, in_beta_im,
        in_x0_re, in_x0_im,
        in_x1_re, in_x1_im,
        in_x2_re, in_x2_im,
        in_x3_re, in_x3_im,
        in_y0_re, in_y0_im,
        in_y1_re, in_y1_im,
        in_y2_re, in_y2_im,
        in_y3_re, in_y3_im,
        in_a00_re, in_a00_im,
        in_a01_re, in_a01_im,
        in_a02_re, in_a02_im,
        in_a03_re, in_a03_im,
        in_a11_re, in_a11_im,
        in_a12_re, in_a12_im,
        in_a13_re, in_a13_im,
        in_a22_re, in_a22_im,
        in_a23_re, in_a23_im,
        in_a33_re, in_a33_im
    ):
        pass
```

Call `process(...)` once per accepted transaction. `in_uplo` is an unsigned
one-bit selector; every real and imaginary argument is an unsigned 32-bit port
encoding of a signed integer. Every call returns one dictionary with exactly
`out_y0_re`, `out_y0_im`, `out_y1_re`, `out_y1_im`, `out_y2_re`, `out_y2_im`,
`out_y3_re`, and `out_y3_im`. Each value is an unsigned 32-bit result encoding.
The complete dictionary is returned even when the result is zero, never a
list or `None`.

The input vectors and matrix are supplied anew for every call. Construction
and `reset()` leave no persistent computational state; `reset()` returns `None`
and does not produce a record.

## Modules

- `HermitianMatrixVectorStep4`: top-level single-transaction kernel that accepts one 4×4 Hermitian matrix, two 4-element complex vectors, and two complex scalars, then produces one updated 4-element complex output vector.
- No helper submodules are required by the contract.

## Functional Behavior

All real and imaginary fields are signed two's-complement 32-bit integers. A complex number `(re, im)` represents the pair of signed integers `(re, im)`. All arithmetic is exact integer arithmetic with no saturation and no intermediate truncation; each returned field is reduced modulo `2^32` to its 32-bit output port.

The transaction computes:

- `alpha = (in_alpha_re, in_alpha_im)`
- `beta  = (in_beta_re,  in_beta_im)`
- `x[0] = (in_x0_re, in_x0_im)`, `x[1] = (in_x1_re, in_x1_im)`, `x[2] = (in_x2_re, in_x2_im)`, `x[3] = (in_x3_re, in_x3_im)`
- `y_in[0] = (in_y0_re, in_y0_im)`, `y_in[1] = (in_y1_re, in_y1_im)`, `y_in[2] = (in_y2_re, in_y2_im)`, `y_in[3] = (in_y3_re, in_y3_im)`

The 10 matrix input elements are interpreted as triangular storage of a 4×4 Hermitian matrix.

Stored elements are:
- diagonal: `a00`, `a11`, `a22`, `a33`
- upper/lower off-diagonal positions by index pair: `a01`, `a02`, `a03`, `a12`, `a13`, `a23`

Complex conjugation is defined as:
- `conj(re, im) = (re, -im)`

Diagonal handling is:
- matrix element `(k,k)` is `(real(diag_k), 0)` regardless of the value on the corresponding `in_a**_im` port.
- Thus `A[k][k].re = diag_k_re` and `A[k][k].im = 0`.

If `in_uplo == 1`, the supplied off-diagonal elements are the upper triangle:
- `A[0][1] = a01`, `A[0][2] = a02`, `A[0][3] = a03`
- `A[1][2] = a12`, `A[1][3] = a13`
- `A[2][3] = a23`
- mirrored lower-triangle elements are their conjugates:
  - `A[1][0] = conj(a01)`
  - `A[2][0] = conj(a02)`
  - `A[3][0] = conj(a03)`
  - `A[2][1] = conj(a12)`
  - `A[3][1] = conj(a13)`
  - `A[3][2] = conj(a23)`

If `in_uplo == 0`, the supplied off-diagonal elements are the lower triangle using the same port names as index pairs:
- `A[1][0] = a01`, `A[2][0] = a02`, `A[3][0] = a03`
- `A[2][1] = a12`, `A[3][1] = a13`
- `A[3][2] = a23`
- mirrored upper-triangle elements are their conjugates:
  - `A[0][1] = conj(a01)`
  - `A[0][2] = conj(a02)`
  - `A[0][3] = conj(a03)`
  - `A[1][2] = conj(a12)`
  - `A[1][3] = conj(a13)`
  - `A[2][3] = conj(a23)`

Complex multiplication is:
- `(ar, ai) * (br, bi) = (ar*br - ai*bi, ar*bi + ai*br)`

Complex addition is componentwise:
- `(ar, ai) + (br, bi) = (ar+br, ai+bi)`

For each output element `j` in `{0,1,2,3}`:
1. Compute the Hermitian matrix-vector product term
   - `mv[j] = A[j][0]*x[0] + A[j][1]*x[1] + A[j][2]*x[2] + A[j][3]*x[3]`
   with the additions evaluated left-to-right as
   - `(((A[j][0]*x[0]) + (A[j][1]*x[1])) + (A[j][2]*x[2])) + (A[j][3]*x[3])`
2. Compute the scaled terms
   - `p[j] = alpha * mv[j]`
   - `q[j] = beta * y_in[j]`
3. Compute the result
   - `y_out[j] = p[j] + q[j]`

The returned ports are:
- `out_y0_re = y_out[0].re mod 2^32`
- `out_y0_im = y_out[0].im mod 2^32`
- `out_y1_re = y_out[1].re mod 2^32`
- `out_y1_im = y_out[1].im mod 2^32`
- `out_y2_re = y_out[2].re mod 2^32`
- `out_y2_im = y_out[2].im mod 2^32`
- `out_y3_re = y_out[3].re mod 2^32`
- `out_y3_im = y_out[3].im mod 2^32`

Special cases are not treated separately. In particular:
- `alpha = 0` still uses the same arithmetic definition and yields `p[j] = 0`.
- `beta = 0` still uses the same arithmetic definition and yields `q[j] = 0`.
- The ignored diagonal imaginary input fields have no effect on the result.
- `in_uplo` values are interpreted only by their single bit: `1` selects upper-triangle storage, `0` selects lower-triangle storage.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst == 1`, the module returns to its idle state.

Architectural state after reset:
- no pending output transaction
- no buffered output data

Output port values after reset and until the first accepted input produces an output:
- `in_ready = 1`
- `out_valid = 0`
- `out_y0_re = 0`
- `out_y0_im = 0`
- `out_y1_re = 0`
- `out_y1_im = 0`
- `out_y2_re = 0`
- `out_y2_im = 0`
- `out_y3_re = 0`
- `out_y3_im = 0`

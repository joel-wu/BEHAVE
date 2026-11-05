# ComplexLuPivotStep4x4

This module performs one complete unblocked LU factorization with partial pivoting on a fixed 4×4 complex matrix. The algorithm is the right-looking Gaussian-elimination procedure used by ZGETF2: for each column, select the pivot row by maximum 1-norm magnitude over the active column segment, swap full rows, divide the entries below the pivot by the pivot, and apply a rank-1 update to the trailing submatrix. The matrix elements are represented as signed fixed-point complex numbers, and the output matrix stores the combined `L` and `U` factors in the standard packed form with the unit diagonal of `L` omitted.

## Interface

### RTL interface

```systemverilog
module ComplexLuPivotStep4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a00_re,
    input  logic [31:0]  in_a00_im,
    input  logic [31:0]  in_a01_re,
    input  logic [31:0]  in_a01_im,
    input  logic [31:0]  in_a02_re,
    input  logic [31:0]  in_a02_im,
    input  logic [31:0]  in_a03_re,
    input  logic [31:0]  in_a03_im,
    input  logic [31:0]  in_a10_re,
    input  logic [31:0]  in_a10_im,
    input  logic [31:0]  in_a11_re,
    input  logic [31:0]  in_a11_im,
    input  logic [31:0]  in_a12_re,
    input  logic [31:0]  in_a12_im,
    input  logic [31:0]  in_a13_re,
    input  logic [31:0]  in_a13_im,
    input  logic [31:0]  in_a20_re,
    input  logic [31:0]  in_a20_im,
    input  logic [31:0]  in_a21_re,
    input  logic [31:0]  in_a21_im,
    input  logic [31:0]  in_a22_re,
    input  logic [31:0]  in_a22_im,
    input  logic [31:0]  in_a23_re,
    input  logic [31:0]  in_a23_im,
    input  logic [31:0]  in_a30_re,
    input  logic [31:0]  in_a30_im,
    input  logic [31:0]  in_a31_re,
    input  logic [31:0]  in_a31_im,
    input  logic [31:0]  in_a32_re,
    input  logic [31:0]  in_a32_im,
    input  logic [31:0]  in_a33_re,
    input  logic [31:0]  in_a33_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a00_re,
    output logic [31:0]  out_a00_im,
    output logic [31:0]  out_a01_re,
    output logic [31:0]  out_a01_im,
    output logic [31:0]  out_a02_re,
    output logic [31:0]  out_a02_im,
    output logic [31:0]  out_a03_re,
    output logic [31:0]  out_a03_im,
    output logic [31:0]  out_a10_re,
    output logic [31:0]  out_a10_im,
    output logic [31:0]  out_a11_re,
    output logic [31:0]  out_a11_im,
    output logic [31:0]  out_a12_re,
    output logic [31:0]  out_a12_im,
    output logic [31:0]  out_a13_re,
    output logic [31:0]  out_a13_im,
    output logic [31:0]  out_a20_re,
    output logic [31:0]  out_a20_im,
    output logic [31:0]  out_a21_re,
    output logic [31:0]  out_a21_im,
    output logic [31:0]  out_a22_re,
    output logic [31:0]  out_a22_im,
    output logic [31:0]  out_a23_re,
    output logic [31:0]  out_a23_im,
    output logic [31:0]  out_a30_re,
    output logic [31:0]  out_a30_im,
    output logic [31:0]  out_a31_re,
    output logic [31:0]  out_a31_im,
    output logic [31:0]  out_a32_re,
    output logic [31:0]  out_a32_im,
    output logic [31:0]  out_a33_re,
    output logic [31:0]  out_a33_im,
    output logic [1:0]   out_ipiv0,
    output logic [1:0]   out_ipiv1,
    output logic [1:0]   out_ipiv2,
    output logic [1:0]   out_ipiv3,
    output logic [2:0]   out_info
);
endmodule
```

### Behavior interface

```python
class ComplexLuPivotStep4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00_re, in_a00_im, in_a01_re, in_a01_im, in_a02_re, in_a02_im, in_a03_re, in_a03_im,
        in_a10_re, in_a10_im, in_a11_re, in_a11_im, in_a12_re, in_a12_im, in_a13_re, in_a13_im,
        in_a20_re, in_a20_im, in_a21_re, in_a21_im, in_a22_re, in_a22_im, in_a23_re, in_a23_im,
        in_a30_re, in_a30_im, in_a31_re, in_a31_im, in_a32_re, in_a32_im, in_a33_re, in_a33_im
    ):
        pass
```

The return value is one dictionary with keys:
`out_a00_re`, `out_a00_im`, `out_a01_re`, `out_a01_im`, `out_a02_re`, `out_a02_im`, `out_a03_re`, `out_a03_im`,
`out_a10_re`, `out_a10_im`, `out_a11_re`, `out_a11_im`, `out_a12_re`, `out_a12_im`, `out_a13_re`, `out_a13_im`,
`out_a20_re`, `out_a20_im`, `out_a21_re`, `out_a21_im`, `out_a22_re`, `out_a22_im`, `out_a23_re`, `out_a23_im`,
`out_a30_re`, `out_a30_im`, `out_a31_re`, `out_a31_im`, `out_a32_re`, `out_a32_im`, `out_a33_re`, `out_a33_im`,
`out_ipiv0`, `out_ipiv1`, `out_ipiv2`, `out_ipiv3`, `out_info`.

One call represents one accepted complete matrix. Pass each input component
as an unsigned 32-bit two's-complement encoding, not a Python float or complex
number. All 32 matrix result components are unsigned 32-bit encodings, each
pivot is an unsigned 2-bit index, and `out_info` is an unsigned 3-bit integer.
Every call returns all 37 fields, including for zero or singular matrices;
never return `None` or a list. Calls retain no factorization state.
Construction and `reset()` establish the same stateless model; `reset()`
returns `None` without emitting a record.

## Modules

- `ComplexLuPivotStep4x4`: top-level module that accepts one 4×4 complex matrix, performs complete LU factorization with partial pivoting, and returns the packed `LU` matrix, pivot indices, and singularity indicator.

## Functional Behavior

Each matrix element is a complex signed fixed-point value with a 32-bit real part and a 32-bit imaginary part. Each part is interpreted as a two's-complement integer in Q16.16 format. A represented real value is `stored_integer / 2^16`.

Let the input matrix be `A`, indexed as `A[row][col]` for `row,col ∈ {0,1,2,3}`. The computation modifies a working copy of `A` in place over four factorization steps `j = 0,1,2,3`.

A complex value `x = xr + i*xi` is zero iff `xr == 0` and `xi == 0`.

The pivot selection metric for a complex value is the 1-norm magnitude
`|xr| + |xi|`,
where `|k|` is the absolute value of the signed 32-bit integer interpreted mathematically, not saturating. For each step `j`, the pivot row `jp` is the smallest row index in `j..3` that attains the maximum pivot metric among `A[j][j], A[j+1][j], ... , A[3][j]`. This matches the first-index tie break of the BLAS `IZAMAX` search on the active column segment.

For each step `j`:

1. Set `ipiv[j] = jp`.

2. If `A[jp][j]` is nonzero, then:
   - If `jp != j`, swap the entirety of rows `j` and `jp`, over all four columns.
   - For each row `r` in `j+1 .. 3`, replace `A[r][j]` by `A[r][j] / A[j][j]`.
   - If `j < 3`, update the trailing submatrix by
     `A[r][c] = A[r][c] - A[r][j] * A[j][c]`
     for every `r` in `j+1 .. 3` and every `c` in `j+1 .. 3`.

3. If `A[jp][j]` is zero, then:
   - If `out_info` has not previously been set to a nonzero value, set `out_info = j + 1`.
   - Do not swap rows.
   - Do not divide the entries below the pivot in column `j`.
   - If `j < 3`, still perform the same trailing-submatrix update
     `A[r][c] = A[r][c] - A[r][j] * A[j][c]`
     for every `r` in `j+1 .. 3` and every `c` in `j+1 .. 3`.

The factorization always runs all four steps. `out_info` is zero if every selected pivot is nonzero; otherwise it is the 1-based index of the first step whose selected pivot is zero.

The packed output matrix stores `U` on and above the diagonal and stores the multipliers of `L` strictly below the diagonal. The unit diagonal of `L` is implicit and is not written into the matrix.

### Fixed-point arithmetic

All stored matrix elements remain in Q16.16 format.

For complex multiplication:
- If `x = xr + i*xi` and `y = yr + i*yi`, the exact intermediate products are computed as signed mathematical integers:
  - `p_rr = xr * yr`
  - `p_ii = xi * yi`
  - `p_ri = xr * yi`
  - `p_ir = xi * yr`
- The Q16.16 product result is
  - real part `mul_re = trunc_zero((p_rr - p_ii) / 2^16)`
  - imaginary part `mul_im = trunc_zero((p_ri + p_ir) / 2^16)`
where `trunc_zero` means division by `2^16` rounded toward zero.

For complex division used in the multiplier computation:
- `x / y` is evaluated as
  - denominator `den = yr*yr + yi*yi`
  - numerator real `num_re = xr*yr + xi*yi`
  - numerator imaginary `num_im = xi*yr - xr*yi`
- If `den == 0`, the division result is defined as complex zero.
- Otherwise the Q16.16 quotient is
  - `div_re = trunc_zero((num_re * 2^16) / den)`
  - `div_im = trunc_zero((num_im * 2^16) / den)`
with `trunc_zero` rounding toward zero.

For complex subtraction and row swapping, arithmetic is exact over mathematical integers before final truncation to the 32-bit stored format.

After every assignment to a matrix element part, the assigned real and imaginary integers are truncated to 32 bits by two's-complement wraparound. No saturation is performed.

### Output encoding

`out_ipiv0` through `out_ipiv3` are the selected pivot row indices for steps 0 through 3, encoded as unsigned 2-bit integers in the range 0 to 3.

`out_info` is a 3-bit unsigned integer in the range 0 to 4.

### Edge cases

- A zero pivot is detected only when both real and imaginary parts of the selected pivot element are exactly zero.
- When a zero pivot is selected, `out_info` records the first such step, but later steps still execute pivot search and trailing updates.
- For a step with a zero pivot, the multipliers below the pivot are left unchanged from their pre-step values.
- If a denominator in the complex division formula is zero, the quotient used for the corresponding multiplier is complex zero. In this design that case can only arise when dividing by a zero pivot, and that division is skipped anyway.
- Input values are arbitrary 32-bit patterns and are interpreted only through two's-complement Q16.16 rules above.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `in_ready` is `1`.
- `out_valid` is `0`.
- Every output data port `out_a00_re` through `out_a33_im` is `0`.
- `out_ipiv0`, `out_ipiv1`, `out_ipiv2`, and `out_ipiv3` are `0`.
- `out_info` is `0`.

The module has no architectural state beyond any internal registers required to buffer an in-flight transaction. After reset, no prior input affects any future output.

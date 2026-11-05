# TriangularBandMatrixVectorStep

TriangularBandMatrixVectorStep performs one in-place triangular band matrix-vector multiply over a fixed-size 4-element vector using the DTBMV update order. The matrix is stored in band format with one sub- or super-diagonal (`K=1`). Each accepted input transaction supplies the matrix shape controls, the eight matrix-storage words, and the four input vector elements; the block returns the four overwritten vector elements after executing exactly the same dependency-preserving in-place algorithm as the reference DTBMV routine for `x := A*x` or `x := A**T*x`, including unit-diagonal handling.

## Interface

### RTL interface

```systemverilog
module triangular_band_matrix_vector_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_uplo,
    input  logic        in_trans,
    input  logic        in_diag,
    input  logic [63:0] in_a00,
    input  logic [63:0] in_a01,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,
    input  logic [63:0] in_a20,
    input  logic [63:0] in_a21,
    input  logic [63:0] in_a30,
    input  logic [63:0] in_a31,
    input  logic [63:0] in_x0,
    input  logic [63:0] in_x1,
    input  logic [63:0] in_x2,
    input  logic [63:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_x0,
    output logic [63:0] out_x1,
    output logic [63:0] out_x2,
    output logic [63:0] out_x3
);
```

Signal meanings:

- `in_uplo`: `1` selects upper-triangular band storage, `0` selects lower-triangular band storage.
- `in_trans`: `0` selects `x := A*x`, `1` selects `x := A**T*x`.
- `in_diag`: `1` selects unit diagonal, `0` selects non-unit diagonal.
- `in_a*` are the eight stored matrix coefficients for a `(K+1) x N = 2 x 4` band array. The first decimal digit in the signal name is the column index `j` in `[0,3]`; the second decimal digit is the band-row index `r` in `[0,1]`. Thus column `j` contains `in_aj0` and `in_aj1`.
- `in_x0..in_x3` are the four input vector elements.
- `out_x0..out_x3` are the four output vector elements after the in-place overwrite.

All matrix and vector data ports are signed 64-bit two's-complement integers.

### Behavior interface

```python
class TriangularBandMatrixVectorStep:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_uplo,
        in_trans,
        in_diag,
        in_a00,
        in_a01,
        in_a10,
        in_a11,
        in_a20,
        in_a21,
        in_a30,
        in_a31,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
    ): ...
```

`process(...)` returns one dictionary with keys `out_x0`, `out_x1`, `out_x2`, and `out_x3`.

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

- `triangular_band_matrix_vector_step`: top-level module implementing one DTBMV transaction for fixed `N=4`, fixed `K=1`, fixed unit stride.

## Functional Behavior

Constants for this block are fixed:

- Vector length `N = 4`.
- Bandwidth `K = 1`.
- Effective leading dimension `LDA = K + 1 = 2`.
- Vector increment `INCX = 1`.

Arithmetic is signed 64-bit two's-complement with wraparound after every addition and multiplication result written back to the architectural vector state. Let `s64(v)` denote interpreting a 64-bit pattern as a signed integer in `[-2^63, 2^63-1]`, and let `wrap64(v)` denote reduction modulo `2^64` returned as a 64-bit port value. Every input data port is first interpreted as `s64(...)`. Every output port is `wrap64(...)` of the final signed result.

The 2x4 band-storage array is reconstructed as:

- Column 0: `A(0,0)=in_a00`, `A(1,0)=in_a01`
- Column 1: `A(0,1)=in_a10`, `A(1,1)=in_a11`
- Column 2: `A(0,2)=in_a20`, `A(1,2)=in_a21`
- Column 3: `A(0,3)=in_a30`, `A(1,3)=in_a31`

Index formulas use zero-based indices. For a logical matrix entry `M[i,j]`:

- Upper-triangular, `in_uplo=1`:
  - Only entries with `max(0, j-1) <= i <= j` are referenced.
  - The diagonal entry for column `j` is stored in band row `1`: `A(1,j)`.
  - The first super-diagonal entry `M[j-1,j]` is stored in band row `0`: `A(0,j)`.
  - For referenced entries, the storage row is `r = 1 - j + i`, so the stored value is `A(r,j)`.

- Lower-triangular, `in_uplo=0`:
  - Only entries with `j <= i <= min(3, j+1)` are referenced.
  - The diagonal entry for column `j` is stored in band row `0`: `A(0,j)`.
  - The first sub-diagonal entry `M[j+1,j]` is stored in band row `1`: `A(1,j)`.
  - For referenced entries, the storage row is `r = i - j`, so the stored value is `A(r,j)`.

If `in_diag=1`, the matrix is unit triangular: every diagonal element is treated as the constant signed integer `1`, and the stored diagonal words `A(1,j)` for upper or `A(0,j)` for lower are ignored. If `in_diag=0`, the stored diagonal words are used.

The vector update is in-place and order-dependent. Let the working vector initially be:

- `x[0] = s64(in_x0)`
- `x[1] = s64(in_x1)`
- `x[2] = s64(in_x2)`
- `x[3] = s64(in_x3)`

Then execute exactly one of the four cases below.

### Case 1: `in_trans=0`, `in_uplo=1`  (`x := A*x`, upper)

For `j = 0, 1, 2, 3` in ascending order:

1. If the current `x[j]` equals zero, do nothing for this `j`.
2. Otherwise set `temp = x[j]`.
3. For `i = max(0, j-1)` to `j-1` in ascending order:
   - `x[i] = wrap64_signed(x[i] + temp * M[i,j])`
4. If `in_diag=0`, update the diagonal:
   - `x[j] = wrap64_signed(x[j] * M[j,j])`

`wrap64_signed(...)` means perform the arithmetic in mathematical integers, reduce modulo `2^64`, then reinterpret the reduced 64-bit pattern as a signed 64-bit state value for subsequent operations.

With `K=1`, step 3 touches at most one element:
- `j=0`: no off-diagonal update
- `j=1`: may update `x[0]` using `A(0,1)`
- `j=2`: may update `x[1]` using `A(0,2)`
- `j=3`: may update `x[2]` using `A(0,3)`

### Case 2: `in_trans=0`, `in_uplo=0`  (`x := A*x`, lower)

For `j = 3, 2, 1, 0` in descending order:

1. If the current `x[j]` equals zero, do nothing for this `j`.
2. Otherwise set `temp = x[j]`.
3. For `i = min(3, j+1)` down to `j+1` in descending order:
   - `x[i] = wrap64_signed(x[i] + temp * M[i,j])`
4. If `in_diag=0`, update the diagonal:
   - `x[j] = wrap64_signed(x[j] * M[j,j])`

With `K=1`, step 3 touches at most one element:
- `j=3`: no off-diagonal update
- `j=2`: may update `x[3]` using `A(1,2)`
- `j=1`: may update `x[2]` using `A(1,1)`
- `j=0`: may update `x[1]` using `A(1,0)`

### Case 3: `in_trans=1`, `in_uplo=1`  (`x := A**T*x`, upper)

For `j = 3, 2, 1, 0` in descending order:

1. Set `temp = x[j]`.
2. If `in_diag=0`, set `temp = wrap64_signed(temp * M[j,j])`.
3. For `i = j-1` down to `max(0, j-1)` in descending order:
   - `temp = wrap64_signed(temp + M[i,j] * x[i])`
4. Set `x[j] = temp`.

With `K=1`, step 3 touches at most one element:
- `j=3`: may add `A(0,3) * x[2]`
- `j=2`: may add `A(0,2) * x[1]`
- `j=1`: may add `A(0,1) * x[0]`
- `j=0`: no off-diagonal contribution

### Case 4: `in_trans=1`, `in_uplo=0`  (`x := A**T*x`, lower)

For `j = 0, 1, 2, 3` in ascending order:

1. Set `temp = x[j]`.
2. If `in_diag=0`, set `temp = wrap64_signed(temp * M[j,j])`.
3. For `i = j+1` to `min(3, j+1)` in ascending order:
   - `temp = wrap64_signed(temp + M[i,j] * x[i])`
4. Set `x[j] = temp`.

With `K=1`, step 3 touches at most one element:
- `j=0`: may add `A(1,0) * x[1]`
- `j=1`: may add `A(1,1) * x[2]`
- `j=2`: may add `A(1,2) * x[3]`
- `j=3`: no off-diagonal contribution

After the selected case completes, outputs are:

- `out_x0 = wrap64(x[0])`
- `out_x1 = wrap64(x[1])`
- `out_x2 = wrap64(x[2])`
- `out_x3 = wrap64(x[3])`

Edge-case rules:

- Zero-valued vector elements affect only the non-transposed cases through the explicit skip rule above. In the transposed cases there is no zero-skip; every `j` executes.
- No error reporting ports are provided. All 1-bit control encodings are valid by construction: `in_uplo` selects upper/lower, `in_trans` selects no-transpose/transpose, and `in_diag` selects unit/non-unit.
- All arithmetic overflow wraps modulo `2^64`. There is no saturation and no exception flagging.

## Reset Behavior

Reset is synchronous active-high through port `rst`.

Architectural state consists only of the output holding registers. On any cycle with `rst=1`:

- `out_valid = 0`
- `out_x0 = 64'h0000000000000000`
- `out_x1 = 64'h0000000000000000`
- `out_x2 = 64'h0000000000000000`
- `out_x3 = 64'h0000000000000000`

The block has no other architectural state. After reset, `in_ready` shall indicate the block can accept a new input transaction whenever it is not holding an unaccepted output transaction.

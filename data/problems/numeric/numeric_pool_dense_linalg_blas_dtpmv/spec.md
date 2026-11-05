# TriangularPackedMatrixVector4

TriangularPackedMatrixVector4 computes one in-place triangular packed matrix-vector multiply on a fixed 4×4 matrix using the BLAS DTPMV update order. The matrix is supplied in packed column-major triangular storage, the vector is supplied as four scalar elements, and the operation is one of `x := A*x` or `x := A^T*x`. The matrix may be upper or lower triangular and either unit-diagonal or non-unit-diagonal. The architectural behavior follows the reference DTPMV loop ordering exactly for `N = 4` and `INCX = 1`, so intermediate updates to `x` are visible to later iterations in the same transaction exactly as in the BLAS routine.

## Interface

### RTL interface

```systemverilog
module triangular_packed_matrix_vector4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_uplo,
    input  logic        in_trans,
    input  logic        in_diag,
    input  logic [63:0] in_ap0,
    input  logic [63:0] in_ap1,
    input  logic [63:0] in_ap2,
    input  logic [63:0] in_ap3,
    input  logic [63:0] in_ap4,
    input  logic [63:0] in_ap5,
    input  logic [63:0] in_ap6,
    input  logic [63:0] in_ap7,
    input  logic [63:0] in_ap8,
    input  logic [63:0] in_ap9,
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

Signal definitions:

- `in_uplo`: `0` = upper triangular, `1` = lower triangular.
- `in_trans`: `0` = no transpose (`x := A*x`), `1` = transpose (`x := A^T*x`).
- `in_diag`: `0` = non-unit diagonal, `1` = unit diagonal.
- `in_ap0`..`in_ap9`: packed triangular matrix elements, each a 64-bit signed two's-complement integer.
- `in_x0`..`in_x3`: input vector elements, each a 64-bit signed two's-complement integer.
- `out_x0`..`out_x3`: output vector elements, each a 64-bit signed two's-complement integer.

Packed storage mapping for the 10 matrix inputs:

For `in_uplo = 0` (upper triangular), packed column-major storage is:
- `in_ap0 = A[0][0]`
- `in_ap1 = A[0][1]`, `in_ap2 = A[1][1]`
- `in_ap3 = A[0][2]`, `in_ap4 = A[1][2]`, `in_ap5 = A[2][2]`
- `in_ap6 = A[0][3]`, `in_ap7 = A[1][3]`, `in_ap8 = A[2][3]`, `in_ap9 = A[3][3]`

For `in_uplo = 1` (lower triangular), packed column-major storage is:
- `in_ap0 = A[0][0]`
- `in_ap1 = A[1][0]`, `in_ap2 = A[2][0]`, `in_ap3 = A[3][0]`
- `in_ap4 = A[1][1]`, `in_ap5 = A[2][1]`, `in_ap6 = A[3][1]`
- `in_ap7 = A[2][2]`, `in_ap8 = A[3][2]`
- `in_ap9 = A[3][3]`

### Behavior interface

```python
class TriangularPackedMatrixVector4:
    def __init__(self): ...
    def reset(self): ...
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

- `triangular_packed_matrix_vector4`: top-level module implementing one 4×4 triangular packed matrix-vector multiply transaction.

## Functional Behavior

All matrix and vector elements are 64-bit signed two's-complement integers. Every addition and multiplication is performed in the mathematical integer domain and truncated to 64 bits after each assignment to a vector element or temporary accumulation target. Truncation is wraparound modulo `2^64`, interpreted as signed two's-complement on the next read.

Let `sx(v)` denote interpretation of a 64-bit input bit pattern as a signed integer in `[-2^63, 2^63-1]`. Let `wrap64(v)` denote truncation to 64 bits. A stored vector element always holds `wrap64(v)`, and when subsequently read it is interpreted as `sx(wrap64(v))`.

The input vector is initialized as:
- `x[0] = sx(in_x0)`
- `x[1] = sx(in_x1)`
- `x[2] = sx(in_x2)`
- `x[3] = sx(in_x3)`

The packed matrix values are interpreted as signed 64-bit integers:
- `ap[k] = sx(in_apk)` for `k = 0..9`.

Diagonal handling:
- If `in_diag = 1`, the matrix is unit triangular. Diagonal elements are treated as mathematical value `1` and the packed diagonal entries are ignored.
- If `in_diag = 0`, the diagonal elements are read from packed storage.

The transaction computes the result by the exact update order below.

### Case 1: `in_trans = 0`, `in_uplo = 0`  (`x := A*x`, upper triangular)

Using the upper-triangular packed mapping above, perform the following in order:

1. `j = 0`  
   If `x[0] != 0`:
   - if `in_diag = 0`: `x[0] = wrap64(x[0] * ap[0])`

2. `j = 1`  
   If `x[1] != 0`:
   - `temp = x[1]`
   - `x[0] = wrap64(x[0] + temp * ap[1])`
   - if `in_diag = 0`: `x[1] = wrap64(x[1] * ap[2])`

3. `j = 2`  
   If `x[2] != 0`:
   - `temp = x[2]`
   - `x[0] = wrap64(x[0] + temp * ap[3])`
   - `x[1] = wrap64(x[1] + temp * ap[4])`
   - if `in_diag = 0`: `x[2] = wrap64(x[2] * ap[5])`

4. `j = 3`  
   If `x[3] != 0`:
   - `temp = x[3]`
   - `x[0] = wrap64(x[0] + temp * ap[6])`
   - `x[1] = wrap64(x[1] + temp * ap[7])`
   - `x[2] = wrap64(x[2] + temp * ap[8])`
   - if `in_diag = 0`: `x[3] = wrap64(x[3] * ap[9])`

This ordering means later columns see vector elements already modified by earlier columns.

### Case 2: `in_trans = 0`, `in_uplo = 1`  (`x := A*x`, lower triangular)

Using the lower-triangular packed mapping above, perform the following in order:

1. `j = 3`  
   If `x[3] != 0`:
   - if `in_diag = 0`: `x[3] = wrap64(x[3] * ap[9])`

2. `j = 2`  
   If `x[2] != 0`:
   - `temp = x[2]`
   - `x[3] = wrap64(x[3] + temp * ap[8])`
   - if `in_diag = 0`: `x[2] = wrap64(x[2] * ap[7])`

3. `j = 1`  
   If `x[1] != 0`:
   - `temp = x[1]`
   - `x[3] = wrap64(x[3] + temp * ap[6])`
   - `x[2] = wrap64(x[2] + temp * ap[5])`
   - if `in_diag = 0`: `x[1] = wrap64(x[1] * ap[4])`

4. `j = 0`  
   If `x[0] != 0`:
   - `temp = x[0]`
   - `x[3] = wrap64(x[3] + temp * ap[3])`
   - `x[2] = wrap64(x[2] + temp * ap[2])`
   - `x[1] = wrap64(x[1] + temp * ap[1])`
   - if `in_diag = 0`: `x[0] = wrap64(x[0] * ap[0])`

### Case 3: `in_trans = 1`, `in_uplo = 0`  (`x := A^T*x`, upper triangular)

Using the upper-triangular packed mapping above, perform the following in order:

1. `j = 3`
   - `temp = x[3]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[9])`
   - `temp = wrap64(temp + ap[8] * x[2])`
   - `temp = wrap64(temp + ap[7] * x[1])`
   - `temp = wrap64(temp + ap[6] * x[0])`
   - `x[3] = temp`

2. `j = 2`
   - `temp = x[2]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[5])`
   - `temp = wrap64(temp + ap[4] * x[1])`
   - `temp = wrap64(temp + ap[3] * x[0])`
   - `x[2] = temp`

3. `j = 1`
   - `temp = x[1]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[2])`
   - `temp = wrap64(temp + ap[1] * x[0])`
   - `x[1] = temp`

4. `j = 0`
   - `temp = x[0]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[0])`
   - `x[0] = temp`

Each step reads `x[i]` values that may already have been updated by earlier steps with larger `j`, matching the reference loop order.

### Case 4: `in_trans = 1`, `in_uplo = 1`  (`x := A^T*x`, lower triangular)

Using the lower-triangular packed mapping above, perform the following in order:

1. `j = 0`
   - `temp = x[0]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[0])`
   - `temp = wrap64(temp + ap[1] * x[1])`
   - `temp = wrap64(temp + ap[2] * x[2])`
   - `temp = wrap64(temp + ap[3] * x[3])`
   - `x[0] = temp`

2. `j = 1`
   - `temp = x[1]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[4])`
   - `temp = wrap64(temp + ap[5] * x[2])`
   - `temp = wrap64(temp + ap[6] * x[3])`
   - `x[1] = temp`

3. `j = 2`
   - `temp = x[2]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[7])`
   - `temp = wrap64(temp + ap[8] * x[3])`
   - `x[2] = temp`

4. `j = 3`
   - `temp = x[3]`
   - if `in_diag = 0`: `temp = wrap64(temp * ap[9])`
   - `x[3] = temp`

The output ports are the final wrapped 64-bit values of `x[0]` through `x[3]`, returned as unsigned 64-bit bit patterns.

Input constraints:
- `in_uplo`, `in_trans`, and `in_diag` shall each be either `0` or `1`.
- All 64-bit data ports may carry any bit pattern.
- This fixed-size kernel always operates on `N = 4` and does not support other sizes or non-unit vector strides.

## Reset Behavior

Reset is synchronous active-high or asynchronous active-high at the implementer's choice. After reset, the module holds no architectural data state. `in_ready` shall be `1`. `out_valid` shall be `0`. `out_x0`, `out_x1`, `out_x2`, and `out_x3` shall be `0`.

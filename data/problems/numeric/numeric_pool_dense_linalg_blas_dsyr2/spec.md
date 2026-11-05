# SymmetricRank2Update4x4

SymmetricRank2Update4x4 performs one in-place symmetric rank-2 update on a 4×4 matrix tile using the BLAS DSYR2 algorithm restricted to a fixed-size hardware transaction. The operation is `A := alpha*x*y^T + alpha*y*x^T + A`, updating only the selected triangular half of `A` exactly as stored. The kernel uses unit-stride vectors, a fixed matrix order of 4, and emits the 10 updated triangular elements as one output transaction.

## Interface

### RTL interface

```systemverilog
module symmetric_rank2_update4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic [63:0]  in_alpha,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,
    input  logic [63:0]  in_y0,
    input  logic [63:0]  in_y1,
    input  logic [63:0]  in_y2,
    input  logic [63:0]  in_y3,
    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a00,
    output logic [63:0]  out_a01,
    output logic [63:0]  out_a02,
    output logic [63:0]  out_a03,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a33
);
```

`in_uplo` selects which triangular storage convention is interpreted on the input and produced on the output:
- `in_uplo = 1'b1`: upper-triangular storage
- `in_uplo = 1'b0`: lower-triangular storage

For `in_uplo = 1'b1`, the input/output elements represent:
- `a00 = A(0,0)`
- `a01 = A(0,1)`
- `a02 = A(0,2)`
- `a03 = A(0,3)`
- `a11 = A(1,1)`
- `a12 = A(1,2)`
- `a13 = A(1,3)`
- `a22 = A(2,2)`
- `a23 = A(2,3)`
- `a33 = A(3,3)`

For `in_uplo = 1'b0`, the same ports carry the lower-triangular elements in this order:
- `a00 = A(0,0)`
- `a01 = A(1,0)`
- `a02 = A(2,0)`
- `a03 = A(3,0)`
- `a11 = A(1,1)`
- `a12 = A(2,1)`
- `a13 = A(3,1)`
- `a22 = A(2,2)`
- `a23 = A(3,2)`
- `a33 = A(3,3)`

All 64-bit data ports are opaque 64-bit payloads representing one stored matrix/vector/scalar element. The functional contract below defines the update over these element values as a fixed-point integer arithmetic specialization of the DSYR2 loop structure.

### Behavior interface

```python
class SymmetricRank2Update4x4:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_uplo,
        in_alpha,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
        in_y0,
        in_y1,
        in_y2,
        in_y3,
        in_a00,
        in_a01,
        in_a02,
        in_a03,
        in_a11,
        in_a12,
        in_a13,
        in_a22,
        in_a23,
        in_a33,
    ): ...
```

`process(...)` returns one dict with keys:
`out_a00`, `out_a01`, `out_a02`, `out_a03`, `out_a11`, `out_a12`, `out_a13`, `out_a22`, `out_a23`, `out_a33`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a00`, `out_a01`, `out_a02`, `out_a03`, `out_a11`, `out_a12`, `out_a13`,
  `out_a22`, `out_a23`, `out_a33`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `symmetric_rank2_update4x4`: top-level module implementing one 4×4 symmetric rank-2 update transaction.

## Functional Behavior

The transaction size is fixed at `N = 4`.

Each input element is interpreted as a signed 64-bit two's-complement integer:
- `alpha = s64(in_alpha)`
- `x[k] = s64(in_xk)` for `k = 0..3`
- `y[k] = s64(in_yk)` for `k = 0..3`
- each stored matrix element is `s64(...)`

Unit stride is fixed: `INCX = 1` and `INCY = 1`.

The stored matrix is symmetric, but only one triangular half is provided and updated. The non-stored half is never referenced.

Quick-return behavior:
- If `alpha == 0`, the output triangular elements equal the corresponding input triangular elements bit-for-bit.
- There is no other quick return because `N` is fixed at 4.

For each column index `j` in ascending order `0, 1, 2, 3`, the algorithm follows the DSYR2 update order:

1. Read `xj = x[j]` and `yj = y[j]`.
2. If `xj == 0` and `yj == 0`, the column is skipped.
3. Otherwise compute:
   - `temp1 = trunc64(alpha * yj)`
   - `temp2 = trunc64(alpha * xj)`
4. Update the selected triangular elements for that column:
   - If `in_uplo = 1'b1` (upper), update rows `i = 0..j`:
     - `p1 = trunc64(x[i] * temp1)`
     - `p2 = trunc64(y[i] * temp2)`
     - `s1 = trunc64(A(i,j) + p1)`
     - `A(i,j) = trunc64(s1 + p2)`
   - If `in_uplo = 1'b0` (lower), update rows `i = j..3`:
     - `p1 = trunc64(x[i] * temp1)`
     - `p2 = trunc64(y[i] * temp2)`
     - `s1 = trunc64(A(i,j) + p1)`
     - `A(i,j) = trunc64(s1 + p2)`

`trunc64(v)` means wraparound to 64 bits, then reinterpret as signed 64-bit two's-complement before the next arithmetic step. This truncation occurs after every multiply and every add listed above.

The triangular traversal order is exact and matches DSYR2:
- Upper-triangular mode updates in this sequence:
  - column 0: `(0,0)`
  - column 1: `(0,1)`, `(1,1)`
  - column 2: `(0,2)`, `(1,2)`, `(2,2)`
  - column 3: `(0,3)`, `(1,3)`, `(2,3)`, `(3,3)`
- Lower-triangular mode updates in this sequence:
  - column 0: `(0,0)`, `(1,0)`, `(2,0)`, `(3,0)`
  - column 1: `(1,1)`, `(2,1)`, `(3,1)`
  - column 2: `(2,2)`, `(3,2)`
  - column 3: `(3,3)`

Input and output packing of the 10 stored elements:
- Upper mode:
  - `out_a00 = updated A(0,0)`
  - `out_a01 = updated A(0,1)`
  - `out_a02 = updated A(0,2)`
  - `out_a03 = updated A(0,3)`
  - `out_a11 = updated A(1,1)`
  - `out_a12 = updated A(1,2)`
  - `out_a13 = updated A(1,3)`
  - `out_a22 = updated A(2,2)`
  - `out_a23 = updated A(2,3)`
  - `out_a33 = updated A(3,3)`
- Lower mode:
  - `out_a00 = updated A(0,0)`
  - `out_a01 = updated A(1,0)`
  - `out_a02 = updated A(2,0)`
  - `out_a03 = updated A(3,0)`
  - `out_a11 = updated A(1,1)`
  - `out_a12 = updated A(2,1)`
  - `out_a13 = updated A(3,1)`
  - `out_a22 = updated A(2,2)`
  - `out_a23 = updated A(3,2)`
  - `out_a33 = updated A(3,3)`

All returned output ports are the 64-bit unsigned bit patterns of the updated signed 64-bit values.

## Reset Behavior

Reset is synchronous active-high.

There is no architectural state other than handshake/output holding state required by the interface. After reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_a00 = 64'h0000000000000000`
- `out_a01 = 64'h0000000000000000`
- `out_a02 = 64'h0000000000000000`
- `out_a03 = 64'h0000000000000000`
- `out_a11 = 64'h0000000000000000`
- `out_a12 = 64'h0000000000000000`
- `out_a13 = 64'h0000000000000000`
- `out_a22 = 64'h0000000000000000`
- `out_a23 = 64'h0000000000000000`
- `out_a33 = 64'h0000000000000000`

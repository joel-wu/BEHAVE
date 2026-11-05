# TriangularSolve2x2

TriangularSolve2x2 performs one fixed-size triangular solve derived from the BLAS `TRSM` operation on 2×2 matrices using signed fixed-point arithmetic. One accepted input transaction provides a 2×2 triangular matrix `A`, a 2×2 right-hand-side matrix `B`, control bits selecting left/right side, upper/lower triangular form, transpose/no-transpose, and unit/non-unit diagonal, plus a scalar `alpha`. The module computes the 2×2 solution matrix `X` satisfying either `op(A) * X = alpha * B` or `X * op(A) = alpha * B`, and returns `X` in one output transaction. Arithmetic uses Q16.16 signed fixed-point numbers in 32-bit two’s-complement format. The solve order, conditional skips on zero data, and divide/multiply truncation follow the reference triangular-substitution algorithm exactly.

## Interface

### RTL interface

```systemverilog
module triangular_solve_2x2 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_uplo,
    input  logic         in_transa,
    input  logic         in_diag,
    input  logic [31:0]  in_alpha,

    input  logic [31:0]  in_a00,
    input  logic [31:0]  in_a01,
    input  logic [31:0]  in_a10,
    input  logic [31:0]  in_a11,

    input  logic [31:0]  in_b00,
    input  logic [31:0]  in_b01,
    input  logic [31:0]  in_b10,
    input  logic [31:0]  in_b11,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_b00,
    output logic [31:0]  out_b01,
    output logic [31:0]  out_b10,
    output logic [31:0]  out_b11
);
```

Bit encodings:
- `in_side = 0`: left-side solve, `op(A) * X = alpha * B`
- `in_side = 1`: right-side solve, `X * op(A) = alpha * B`
- `in_uplo = 0`: upper triangular
- `in_uplo = 1`: lower triangular
- `in_transa = 0`: `op(A) = A`
- `in_transa = 1`: `op(A) = A^T`
- `in_diag = 0`: non-unit diagonal, diagonal entries are used
- `in_diag = 1`: unit diagonal, diagonal entries are treated as exactly 1.0 and must not be referenced by the algorithm

The transaction cardinality is exactly 1:1: each accepted input transaction produces exactly one output transaction containing `out_b00..out_b11`.

### Behavior interface

```python
class TriangularSolve2x2:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_side,
            in_uplo,
            in_transa,
            in_diag,
            in_alpha,
            in_a00,
            in_a01,
            in_a10,
            in_a11,
            in_b00,
            in_b01,
            in_b10,
            in_b11):
        pass
```

`process(...)` returns one dict with keys:
`out_b00`, `out_b01`, `out_b10`, `out_b11`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b00`, `out_b01`, `out_b10`, `out_b11`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `triangular_solve_2x2`: top-level fixed-point 2×2 triangular solve kernel.

## Functional Behavior

All matrix elements and `alpha` are signed Q16.16 values stored in 32-bit two’s-complement format. Inputs are interpreted as signed 32-bit integers. Outputs are returned as 32-bit unsigned bit patterns carrying the signed Q16.16 result.

Let:
- `A = [[a00, a01], [a10, a11]]`
- `B = [[b00, b01], [b10, b11]]`

The strictly non-triangular half of `A` is ignored according to `in_uplo`. When `in_diag = 1`, diagonal values are not used and are treated as exact multiplicative identity.

The computation is in-place on the four `B` elements and follows the exact substitution order below. Each listed assignment updates the current matrix state before subsequent steps read it.

Define Q16.16 arithmetic primitives:

1. `mul_q16(x, y)`:
   - Interpret `x` and `y` as signed 32-bit integers.
   - Form the signed 64-bit product `p = x * y`.
   - The result is truncation toward zero of `p / 2^16`.
   - Equivalently:
     - if `p >= 0`, result = `p >> 16`
     - if `p < 0`, result = `-(((-p) >> 16))`
   - The returned value is truncated to 32 bits two’s-complement.

2. `div_q16(x, y)`:
   - Interpret `x` and `y` as signed 32-bit integers.
   - If `y == 0`, result = 0.
   - Otherwise compute truncation toward zero of `(x * 2^16) / y`.
   - The returned value is truncated to 32 bits two’s-complement.

3. All additions and subtractions are signed integer operations with wraparound to 32 bits after each assignment.

Special case:
- If `in_alpha == 0`, the result is the all-zero 2×2 matrix, and no element of `A` affects the result.

Otherwise, initialize working variables from the input `B`:
- `b00`, `b01`, `b10`, `b11`

Then apply exactly one of the following eight algorithms.

### 1. Left side, no transpose, upper triangular
Condition: `in_side=0`, `in_transa=0`, `in_uplo=0`

This computes `B := alpha * inv(A) * B` by back substitution independently for each column.

For column 0:
1. If `alpha != 1.0` (`0x00010000`), set:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`
2. If `b10 != 0`:
   - if `in_diag=0`, `b10 = div_q16(b10, a11)`
   - `b00 = b00 - mul_q16(b10, a01)`
3. If `b00 != 0` and `in_diag=0`, `b00 = div_q16(b00, a00)`

For column 1:
1. If `alpha != 1.0`, set:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`
2. If `b11 != 0`:
   - if `in_diag=0`, `b11 = div_q16(b11, a11)`
   - `b01 = b01 - mul_q16(b11, a01)`
3. If `b01 != 0` and `in_diag=0`, `b01 = div_q16(b01, a00)`

### 2. Left side, no transpose, lower triangular
Condition: `in_side=0`, `in_transa=0`, `in_uplo=1`

This computes `B := alpha * inv(A) * B` by forward substitution independently for each column.

For column 0:
1. If `alpha != 1.0`, set:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`
2. If `b00 != 0`:
   - if `in_diag=0`, `b00 = div_q16(b00, a00)`
   - `b10 = b10 - mul_q16(b00, a10)`
3. If `b10 != 0` and `in_diag=0`, `b10 = div_q16(b10, a11)`

For column 1:
1. If `alpha != 1.0`, set:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`
2. If `b01 != 0`:
   - if `in_diag=0`, `b01 = div_q16(b01, a00)`
   - `b11 = b11 - mul_q16(b01, a10)`
3. If `b11 != 0` and `in_diag=0`, `b11 = div_q16(b11, a11)`

### 3. Left side, transpose, upper triangular
Condition: `in_side=0`, `in_transa=1`, `in_uplo=0`

This computes `B := alpha * inv(A^T) * B` by forward substitution independently for each column.

For column 0:
1. `temp0 = mul_q16(alpha, b00)`
2. For `b00`: if `in_diag=0`, `temp0 = div_q16(temp0, a00)`
3. Set `b00 = temp0`
4. `temp1 = mul_q16(alpha, b10) - mul_q16(a01, b00)`
5. If `in_diag=0`, `temp1 = div_q16(temp1, a11)`
6. Set `b10 = temp1`

For column 1:
1. `temp0 = mul_q16(alpha, b01)`
2. For `b01`: if `in_diag=0`, `temp0 = div_q16(temp0, a00)`
3. Set `b01 = temp0`
4. `temp1 = mul_q16(alpha, b11) - mul_q16(a01, b01)`
5. If `in_diag=0`, `temp1 = div_q16(temp1, a11)`
6. Set `b11 = temp1`

### 4. Left side, transpose, lower triangular
Condition: `in_side=0`, `in_transa=1`, `in_uplo=1`

This computes `B := alpha * inv(A^T) * B` by back substitution independently for each column.

For column 0:
1. `temp1 = mul_q16(alpha, b10)`
2. If `in_diag=0`, `temp1 = div_q16(temp1, a11)`
3. Set `b10 = temp1`
4. `temp0 = mul_q16(alpha, b00) - mul_q16(a10, b10)`
5. If `in_diag=0`, `temp0 = div_q16(temp0, a00)`
6. Set `b00 = temp0`

For column 1:
1. `temp1 = mul_q16(alpha, b11)`
2. If `in_diag=0`, `temp1 = div_q16(temp1, a11)`
3. Set `b11 = temp1`
4. `temp0 = mul_q16(alpha, b01) - mul_q16(a10, b11)`
5. If `in_diag=0`, `temp0 = div_q16(temp0, a00)`
6. Set `b01 = temp0`

### 5. Right side, no transpose, upper triangular
Condition: `in_side=1`, `in_transa=0`, `in_uplo=0`

This computes `B := alpha * B * inv(A)` by progressing left to right across columns.

For result column 0:
1. If `alpha != 1.0`, set:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`
2. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a00)`
   - `b00 = mul_q16(temp, b00)`
   - `b10 = mul_q16(temp, b10)`

For result column 1:
1. If `alpha != 1.0`, set:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`
2. If `a01 != 0`:
   - `b01 = b01 - mul_q16(a01, b00)`
   - `b11 = b11 - mul_q16(a01, b10)`
3. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a11)`
   - `b01 = mul_q16(temp, b01)`
   - `b11 = mul_q16(temp, b11)`

### 6. Right side, no transpose, lower triangular
Condition: `in_side=1`, `in_transa=0`, `in_uplo=1`

This computes `B := alpha * B * inv(A)` by progressing right to left across columns.

For result column 1:
1. If `alpha != 1.0`, set:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`
2. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a11)`
   - `b01 = mul_q16(temp, b01)`
   - `b11 = mul_q16(temp, b11)`

For result column 0:
1. If `alpha != 1.0`, set:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`
2. If `a10 != 0`:
   - `b00 = b00 - mul_q16(a10, b01)`
   - `b10 = b10 - mul_q16(a10, b11)`
3. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a00)`
   - `b00 = mul_q16(temp, b00)`
   - `b10 = mul_q16(temp, b10)`

### 7. Right side, transpose, upper triangular
Condition: `in_side=1`, `in_transa=1`, `in_uplo=0`

This computes `B := alpha * B * inv(A^T)` by progressing right to left across columns.

For source/result column 1:
1. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a11)`
   - `b01 = mul_q16(temp, b01)`
   - `b11 = mul_q16(temp, b11)`
2. If `a01 != 0`:
   - `b00 = b00 - mul_q16(a01, b01)`
   - `b10 = b10 - mul_q16(a01, b11)`
3. If `alpha != 1.0`:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`

For source/result column 0:
1. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a00)`
   - `b00 = mul_q16(temp, b00)`
   - `b10 = mul_q16(temp, b10)`
2. If `alpha != 1.0`:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`

### 8. Right side, transpose, lower triangular
Condition: `in_side=1`, `in_transa=1`, `in_uplo=1`

This computes `B := alpha * B * inv(A^T)` by progressing left to right across columns.

For source/result column 0:
1. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a00)`
   - `b00 = mul_q16(temp, b00)`
   - `b10 = mul_q16(temp, b10)`
2. If `a10 != 0`:
   - `b01 = b01 - mul_q16(a10, b00)`
   - `b11 = b11 - mul_q16(a10, b10)`
3. If `alpha != 1.0`:
   - `b00 = mul_q16(alpha, b00)`
   - `b10 = mul_q16(alpha, b10)`

For source/result column 1:
1. If `in_diag=0`:
   - `temp = div_q16(0x00010000, a11)`
   - `b01 = mul_q16(temp, b01)`
   - `b11 = mul_q16(temp, b11)`
2. If `alpha != 1.0`:
   - `b01 = mul_q16(alpha, b01)`
   - `b11 = mul_q16(alpha, b11)`

The output transaction contains the final working values:
- `out_b00 = b00`
- `out_b01 = b01`
- `out_b10 = b10`
- `out_b11 = b11`

Worked example for operation ordering:
- For `in_side=1`, `in_transa=1`, `in_uplo=0`, `in_diag=0`, the update of `b00` and `b10` caused by subtracting `a01 * b01` and `a01 * b11` must use `b01` and `b11` after the diagonal scaling of column 1 but before the optional multiplication of column 1 by `alpha`. This ordering is mandatory.

## Reset Behavior

Reset is synchronous active-high via `rst`.

Architectural state:
- The module has no persistent mathematical state between transactions.

Output and handshake state after reset:
- `out_valid = 0`
- `in_ready = 1`
- `out_b00 = 0`
- `out_b01 = 0`
- `out_b10 = 0`
- `out_b11 = 0`

After reset, the first accepted input transaction is processed independently of all prior activity.

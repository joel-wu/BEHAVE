# LuSolve4x4

LuSolve4x4 solves one 4-by-4 real linear system `A * x = b` using LU factorization with partial pivoting followed by forward and backward substitution. The kernel is scoped to a single right-hand side and uses fixed-point signed integer arithmetic instead of floating-point: all matrix and vector elements are 16-bit signed Q8.8 values, all divisions truncate toward zero, and the factorization is performed in place on a 4-by-4 internal matrix image with a 4-entry pivot record.

## Interface

### RTL interface

```systemverilog
module lu_solve4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a00,
    input  logic [15:0]  in_a01,
    input  logic [15:0]  in_a02,
    input  logic [15:0]  in_a03,
    input  logic [15:0]  in_a10,
    input  logic [15:0]  in_a11,
    input  logic [15:0]  in_a12,
    input  logic [15:0]  in_a13,
    input  logic [15:0]  in_a20,
    input  logic [15:0]  in_a21,
    input  logic [15:0]  in_a22,
    input  logic [15:0]  in_a23,
    input  logic [15:0]  in_a30,
    input  logic [15:0]  in_a31,
    input  logic [15:0]  in_a32,
    input  logic [15:0]  in_a33,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0,
    output logic [15:0]  out_x1,
    output logic [15:0]  out_x2,
    output logic [15:0]  out_x3,
    output logic [2:0]   out_info
);
```

### Behavior interface

```python
class LuSolve4x4:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_b0, in_b1, in_b2, in_b3,
    ): ...
```

The `process(...)` method returns one dictionary with keys `out_x0`, `out_x1`, `out_x2`, `out_x3`, and `out_info`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_info`: 3.
- `out_x0`, `out_x1`, `out_x2`, `out_x3`: 16.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `lu_solve4x4`: top-level kernel. Accepts one 4-by-4 matrix and one 4-element right-hand side vector, performs fixed-point LU factorization with partial pivoting, and returns either the solved vector or a singular indication.

## Functional Behavior

All input and output data ports except `out_info` carry signed 16-bit Q8.8 values encoded as two's-complement integers. A bit pattern `v` represents the real value `to_signed(v,16) / 256`.

The kernel processes one transaction independently of all others. There is no retained numerical state across accepted input transactions.

The algorithm operates on a local matrix `M[4][4]` and vector `rhs[4]` initialised from the input ports in row-major order:

- `M[0] = [a00, a01, a02, a03]`
- `M[1] = [a10, a11, a12, a13]`
- `M[2] = [a20, a21, a22, a23]`
- `M[3] = [a30, a31, a32, a33]`
- `rhs = [b0, b1, b2, b3]`

Each `M[i][j]` and `rhs[i]` is interpreted as a signed integer `to_signed(port,16)` during arithmetic.

A helper fixed-point division is defined for all signed integer operands `n` and `d` with `d != 0`:

- `q = trunc_toward_zero((n * 256) / d)`

`q` is the Q8.8 encoding of the exact real quotient `n/256 ÷ d/256`.
`trunc_toward_zero` means round toward zero after the rational division.
No saturation is applied to internal arithmetic. After every matrix update, vector update, and final solution component, the stored value is truncated to signed 16-bit two's-complement by keeping the low 16 bits.

A helper product term for multiplying two Q8.8 signed integers `p` and `q` is defined as:

- `mul_q8_8(p, q) = trunc_toward_zero((p * q) / 256)`

Because `p` and `q` are integers encoding Q8.8 numbers, this is implemented as signed truncation toward zero of the exact rational product divided by 256. The stored result is then truncated to signed 16-bit two's-complement.

Pivot selection and factorization follow fixed-order LU decomposition with partial pivoting:

1. For pivot column `k = 0, 1, 2, 3`, search rows `k..3` for the row index `p` whose entry `M[p][k]` has the largest absolute signed magnitude.
2. Absolute value for pivot comparison is mathematical magnitude of the signed 16-bit value. Ties are resolved by keeping the first row encountered, i.e. the smallest row index among equal magnitudes.
3. Record pivot index `ipiv[k] = p`.
4. If `p != k`, swap rows `k` and `p` of `M`, and swap `rhs[k]` with `rhs[p]`.
5. If `M[k][k] == 0`, factorization stops immediately. `out_info` is set to `k + 1`, and `out_x0..out_x3` are all zero. No forward or backward substitution is performed.
6. Otherwise, for each row `i = k+1 .. 3`:
   - Replace `M[i][k]` with the multiplier `L[i][k] = div_q8_8(M[i][k], M[k][k])`.
   - For each column `j = k+1 .. 3`, update
     `M[i][j] = M[i][j] - mul_q8_8(M[i][k], M[k][j])`
     and truncate the stored result to signed 16 bits.

After successful completion of all four pivot columns, `M` contains the combined LU factors in standard packed form: unit diagonal `L` is implicit, strict lower triangle stores multipliers, and upper triangle including diagonal stores `U`.

If no zero pivot was encountered, the kernel solves the permuted factored system using the already row-swapped `rhs`.

Forward substitution computes `y` for `L * y = rhs` in increasing row order:

- `y[0] = rhs[0]`
- `y[1] = rhs[1] - mul_q8_8(M[1][0], y[0])`
- `y[2] = rhs[2] - mul_q8_8(M[2][0], y[0]) - mul_q8_8(M[2][1], y[1])`
- `y[3] = rhs[3] - mul_q8_8(M[3][0], y[0]) - mul_q8_8(M[3][1], y[1]) - mul_q8_8(M[3][2], y[2])`

After each subtraction step, the stored `y[i]` is truncated to signed 16-bit two's-complement.

Backward substitution computes `x` for `U * x = y` in decreasing row order:

Let `wrap16(v)` retain the low 16 bits and interpret them as a signed integer. Each residual is stored at this width before division:

- `x[3] = div_q8_8(wrap16(y[3]), M[3][3])`
- `x[2] = div_q8_8(wrap16(y[2] - mul_q8_8(M[2][3], x[3])), M[2][2])`
- `x[1] = div_q8_8(wrap16(y[1] - mul_q8_8(M[1][2], x[2]) - mul_q8_8(M[1][3], x[3])), M[1][1])`
- `x[0] = div_q8_8(wrap16(y[0] - mul_q8_8(M[0][1], x[1]) - mul_q8_8(M[0][2], x[2]) - mul_q8_8(M[0][3], x[3])), M[0][0])`

If any `M[i][i]` used by backward substitution is zero, `out_info` is set to `i + 1` and `out_x0..out_x3` are all zero. For this 4-by-4 algorithm, this condition can only occur if that zero diagonal was already present as a pivot at factorization step `i`, but the backward-substitution zero-divisor rule is still part of the contract.

On successful solution, `out_info = 0` and:

- `out_x0 = x[0]` encoded as unsigned 16-bit bit pattern
- `out_x1 = x[1]` encoded as unsigned 16-bit bit pattern
- `out_x2 = x[2]` encoded as unsigned 16-bit bit pattern
- `out_x3 = x[3]` encoded as unsigned 16-bit bit pattern

On singular input, `out_info` is the 1-based pivot index of the first zero diagonal element, in the range 1 to 4.

Edge cases:
- All arithmetic uses signed two's-complement interpretation of 16-bit inputs.
- Every intermediate value written back into `M`, `rhs`, `y`, or `x` is truncated to 16 bits by wraparound; there is no saturation.
- Division always truncates toward zero.
- Pivot comparisons use exact signed magnitudes of current truncated matrix entries.
- If the selected pivot element is zero, the output vector is exactly all zeros and `out_info` reports that pivot position.

## Reset Behavior

Reset is synchronous active-high. On reset, the module returns to the idle accept state with no retained matrix, vector, pivot, or partial-solution contents from any previous transaction.

Architectural state after reset:
- no transaction is in flight
- any internal matrix storage is zero
- any internal right-hand side storage is zero
- any internal pivot storage is zero
- any internal forward-substitution and backward-substitution storage is zero
- any internal status register is zero

Output port values after reset and before the first completed output handshake:
- `in_ready = 1`
- `out_valid = 0`
- `out_x0 = 16'h0000`
- `out_x1 = 16'h0000`
- `out_x2 = 16'h0000`
- `out_x3 = 16'h0000`
- `out_info = 3'b000`

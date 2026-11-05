# SymmetricTridiagonalStep4x4

This module performs a complete unblocked Householder reduction of one real symmetric 4×4 matrix, stored in lower-triangular form, to real symmetric tridiagonal form. The computation is the fixed-size `UPLO='L'` specialization of symmetric tridiagonal reduction: it generates two Householder reflectors, applies each reflector from both sides to the active trailing principal submatrix, and returns the tridiagonal diagonal `D`, off-diagonal `E`, reflector scalars `TAU`, and the reflector-vector storage values that overwrite the strictly lower-triangular entries below the first subdiagonal. Arithmetic uses signed 32-bit Q16.16 fixed-point with truncation after each multiply and division result.

## Interface

### RTL interface

```systemverilog
module symmetric_tridiagonal_step4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [31:0] in_a00,
    input  logic signed [31:0] in_a10,
    input  logic signed [31:0] in_a11,
    input  logic signed [31:0] in_a20,
    input  logic signed [31:0] in_a21,
    input  logic signed [31:0] in_a22,
    input  logic signed [31:0] in_a30,
    input  logic signed [31:0] in_a31,
    input  logic signed [31:0] in_a32,
    input  logic signed [31:0] in_a33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_d0,
    output logic signed [31:0] out_d1,
    output logic signed [31:0] out_d2,
    output logic signed [31:0] out_d3,
    output logic signed [31:0] out_e0,
    output logic signed [31:0] out_e1,
    output logic signed [31:0] out_e2,
    output logic signed [31:0] out_tau0,
    output logic signed [31:0] out_tau1,
    output logic signed [31:0] out_tau2,
    output logic signed [31:0] out_v10,
    output logic signed [31:0] out_v20,
    output logic signed [31:0] out_v30,
    output logic signed [31:0] out_v21,
    output logic signed [31:0] out_v31,
    output logic signed [31:0] out_v32
);
```

### Behavior interface

```python
class SymmetricTridiagonalStep4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00,
        in_a10,
        in_a11,
        in_a20,
        in_a21,
        in_a22,
        in_a30,
        in_a31,
        in_a32,
        in_a33,
    ):
        pass
```

The `process(...)` method returns exactly one output transaction as a `dict` with keys:
`out_d0`, `out_d1`, `out_d2`, `out_d3`, `out_e0`, `out_e1`, `out_e2`, `out_tau0`, `out_tau1`, `out_tau2`, `out_v10`, `out_v20`, `out_v30`, `out_v21`, `out_v31`, `out_v32`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_d0`, `out_d1`, `out_d2`, `out_d3`, `out_e0`, `out_e1`, `out_e2`, `out_tau0`,
  `out_tau1`, `out_tau2`, `out_v10`, `out_v20`, `out_v21`, `out_v30`, `out_v31`,
  `out_v32`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `symmetric_tridiagonal_step4x4`: top-level module implementing the complete 4×4 lower-triangular symmetric-to-tridiagonal reduction in one accepted input transaction and one output transaction.

## Functional Behavior

All input and output matrix elements are signed Q16.16 values in two's-complement 32-bit representation.

The accepted input matrix is the symmetric matrix

- `A[0][0] = in_a00`
- `A[1][0] = A[0][1] = in_a10`
- `A[1][1] = in_a11`
- `A[2][0] = A[0][2] = in_a20`
- `A[2][1] = A[1][2] = in_a21`
- `A[2][2] = in_a22`
- `A[3][0] = A[0][3] = in_a30`
- `A[3][1] = A[1][3] = in_a31`
- `A[3][2] = A[2][3] = in_a32`
- `A[3][3] = in_a33`

The module performs the `UPLO='L'` tridiagonal reduction for `N=4`. It computes reflectors `H(1)` and `H(2)` exactly in this order, updates the trailing submatrices after each reflector, and then emits the final tridiagonal data and reflector-storage data. For `N=4`, `H(3)` is degenerate and is encoded with `out_tau2 = 0` and `out_v32 = out_e2`.

Arithmetic primitives are defined as follows.

- `to_signed(x, 32)` means truncate to 32 bits and interpret as signed two's-complement.
- `mul_q16(a, b)` means signed multiplication followed by arithmetic right shift by 16 bits, then truncation to signed 32 bits:
  `mul_q16(a, b) = to_signed((a * b) >> 16, 32)`.
- `div_q16(a, b)` means signed fixed-point division with a Q16.16 result:
  if `b == 0`, the result is `0`; otherwise compute `to_signed(floor((a << 16) / b), 32)`, where `floor` is mathematical floor on the signed real quotient. This is division rounded toward negative infinity, not truncation toward zero.
- `sqrt_q16(x)` means the non-negative Q16.16 square root:
  if `x <= 0`, the result is `0`; otherwise it is the largest non-negative integer `r` such that `r * r <= (x << 16)`. Equivalently, it is `floor(sqrt(real(x / 2^16)) * 2^16)`, returned as signed 32-bit Q16.16.
- Every sum or difference whose result is named below is truncated with `to_signed(..., 32)` immediately after the stated operation sequence.

The fixed-point constant `ONE_Q16` is `0x00010000`. The fixed-point constant `NEG_HALF_Q16` is the signed 32-bit value `to_signed(0xFFFF8000, 32) = -32768`.

### Step 0: first Householder reflector on rows/columns 1..3

Set

- `alpha0_in = A[1][0]`
- `x0 = A[2][0]`
- `x1 = A[3][0]`
- `sigma0 = to_signed(mul_q16(x0, x0) + mul_q16(x1, x1), 32)`

If `sigma0 == 0`:

- `tau0 = 0`
- `beta0 = alpha0_in`
- `v0_tail0 = x0`
- `v0_tail1 = x1`

Else:

- `norm0 = sqrt_q16(to_signed(mul_q16(alpha0_in, alpha0_in) + sigma0, 32))`
- if `alpha0_in >= 0`, `beta0 = to_signed(-norm0, 32)`, else `beta0 = norm0`
- `tau0 = div_q16(to_signed(beta0 - alpha0_in, 32), beta0)`
- `scale0 = div_q16(ONE_Q16, to_signed(alpha0_in - beta0, 32))`
- `v0_tail0 = mul_q16(x0, scale0)`
- `v0_tail1 = mul_q16(x1, scale0)`

Then

- `E0 = beta0`
- the stored reflector-vector components for column 0 are
  - `V10 = E0`
  - `V20 = v0_tail0`
  - `V30 = v0_tail1`

If `tau0 != 0`, form the Householder vector on the active trailing 3×3 block as

- `hv = [ONE_Q16, v0_tail0, v0_tail1]`

and the active symmetric block

- `S = [[A[1][1], A[2][1], A[3][1]],
        [A[2][1], A[2][2], A[3][2]],
        [A[3][1], A[3][2], A[3][3]]]`

Then compute

- `t0 = to_signed(mul_q16(S00, hv0) + mul_q16(S01, hv1) + mul_q16(S02, hv2), 32)`
- `t1 = to_signed(mul_q16(S10, hv0) + mul_q16(S11, hv1) + mul_q16(S12, hv2), 32)`
- `t2 = to_signed(mul_q16(S20, hv0) + mul_q16(S21, hv1) + mul_q16(S22, hv2), 32)`

where `S10 = S01`, `S20 = S02`, `S21 = S12`.

Compute

- `xv0 = mul_q16(tau0, t0)`
- `xv1 = mul_q16(tau0, t1)`
- `xv2 = mul_q16(tau0, t2)`

Compute the reflector correction scalar

- `dot0 = to_signed(mul_q16(xv0, hv0) + mul_q16(xv1, hv1) + mul_q16(xv2, hv2), 32)`
- `alpha_corr0 = mul_q16(mul_q16(NEG_HALF_Q16, tau0), dot0)`

Compute

- `w0 = to_signed(xv0 + mul_q16(alpha_corr0, hv0), 32)`
- `w1 = to_signed(xv1 + mul_q16(alpha_corr0, hv1), 32)`
- `w2 = to_signed(xv2 + mul_q16(alpha_corr0, hv2), 32)`

Apply the symmetric rank-2 update

- `S00 = to_signed(S00 - mul_q16(hv0, w0) - mul_q16(w0, hv0), 32)`
- `S01 = to_signed(S01 - mul_q16(hv0, w1) - mul_q16(w0, hv1), 32)`
- `S02 = to_signed(S02 - mul_q16(hv0, w2) - mul_q16(w0, hv2), 32)`
- `S11 = to_signed(S11 - mul_q16(hv1, w1) - mul_q16(w1, hv1), 32)`
- `S12 = to_signed(S12 - mul_q16(hv1, w2) - mul_q16(w1, hv2), 32)`
- `S22 = to_signed(S22 - mul_q16(hv2, w2) - mul_q16(w2, hv2), 32)`

and write back

- `A[1][1] = S00`
- `A[2][1] = S01`
- `A[3][1] = S02`
- `A[2][2] = S11`
- `A[3][2] = S12`
- `A[3][3] = S22`

If `tau0 == 0`, the matrix is not updated in this step.

After step 0,

- `D0 = A[0][0]`

### Step 1: second Householder reflector on rows/columns 2..3

Using the updated matrix from step 0, set

- `alpha1_in = A[2][1]`
- `y0 = A[3][1]`
- `sigma1 = mul_q16(y0, y0)`

If `sigma1 == 0`:

- `tau1 = 0`
- `beta1 = alpha1_in`
- `v1_tail0 = y0`

Else:

- `norm1 = sqrt_q16(to_signed(mul_q16(alpha1_in, alpha1_in) + sigma1, 32))`
- if `alpha1_in >= 0`, `beta1 = to_signed(-norm1, 32)`, else `beta1 = norm1`
- `tau1 = div_q16(to_signed(beta1 - alpha1_in, 32), beta1)`
- `scale1 = div_q16(ONE_Q16, to_signed(alpha1_in - beta1, 32))`
- `v1_tail0 = mul_q16(y0, scale1)`

Then

- `E1 = beta1`
- the stored reflector-vector components for column 1 are
  - `V21 = E1`
  - `V31 = v1_tail0`

If `tau1 != 0`, form

- `gv = [ONE_Q16, v1_tail0]`

and the active symmetric block

- `T = [[A[2][2], A[3][2]],
        [A[3][2], A[3][3]]]`

Then compute

- `u0 = to_signed(mul_q16(T00, gv0) + mul_q16(T01, gv1), 32)`
- `u1 = to_signed(mul_q16(T10, gv0) + mul_q16(T11, gv1), 32)`

where `T10 = T01`.

Compute

- `yv0 = mul_q16(tau1, u0)`
- `yv1 = mul_q16(tau1, u1)`

Compute

- `dot1 = to_signed(mul_q16(yv0, gv0) + mul_q16(yv1, gv1), 32)`
- `alpha_corr1 = mul_q16(mul_q16(NEG_HALF_Q16, tau1), dot1)`

Compute

- `z0 = to_signed(yv0 + mul_q16(alpha_corr1, gv0), 32)`
- `z1 = to_signed(yv1 + mul_q16(alpha_corr1, gv1), 32)`

Apply the symmetric rank-2 update

- `T00 = to_signed(T00 - mul_q16(gv0, z0) - mul_q16(z0, gv0), 32)`
- `T01 = to_signed(T01 - mul_q16(gv0, z1) - mul_q16(z0, gv1), 32)`
- `T11 = to_signed(T11 - mul_q16(gv1, z1) - mul_q16(z1, gv1), 32)`

and write back

- `A[2][2] = T00`
- `A[3][2] = T01`
- `A[3][3] = T11`

If `tau1 == 0`, the matrix is not updated in this step.

After step 1,

- `D1 = A[1][1]`

### Step 2: final 1×1 remainder

No non-trivial reflector is generated for the final remainder. The outputs are

- `tau2 = 0`
- `E2 = A[3][2]`
- `D2 = A[2][2]`
- `D3 = A[3][3]`
- `V32 = E2`

### Output mapping

The single output transaction is

- `out_d0 = D0`
- `out_d1 = D1`
- `out_d2 = D2`
- `out_d3 = D3`
- `out_e0 = E0`
- `out_e1 = E1`
- `out_e2 = E2`
- `out_tau0 = tau0`
- `out_tau1 = tau1`
- `out_tau2 = tau2`
- `out_v10 = V10`
- `out_v20 = V20`
- `out_v30 = V30`
- `out_v21 = V21`
- `out_v31 = V31`
- `out_v32 = V32`

There is exactly one output transaction for each accepted input transaction.

## Reset Behavior

The module uses active-high reset `rst`. Reset clears all architectural state and deasserts any pending output. Because the computation is fully determined by one accepted input transaction, the only architectural state is the output-holding state required by the handshake wrapper.

Immediately after reset:

- `out_valid = 0`
- `in_ready = 1`
- `out_d0 = 0`
- `out_d1 = 0`
- `out_d2 = 0`
- `out_d3 = 0`
- `out_e0 = 0`
- `out_e1 = 0`
- `out_e2 = 0`
- `out_tau0 = 0`
- `out_tau1 = 0`
- `out_tau2 = 0`
- `out_v10 = 0`
- `out_v20 = 0`
- `out_v30 = 0`
- `out_v21 = 0`
- `out_v31 = 0`
- `out_v32 = 0`

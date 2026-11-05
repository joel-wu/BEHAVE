# BlockReflectorTriangularFactor4x4

This module forms the 4×4 triangular factor `T` for a real block reflector generated from four elementary reflectors using the recursive divide-and-conquer construction used by DLARFT. The transaction is fixed to `n = 4`, `k = 4`, real signed integer data, forward direction, and columnwise storage, so the reflector has the form `H = I - V * T * V^T` and `T` is upper triangular. The implementation contract is the recursive QR-case algorithm specialized to 4×4 matrices, including the same block partitioning, recursive subproblems, and off-diagonal update `T12 = -T11 * (V1^T * V2) * T22`.

## Interface

### RTL interface

```systemverilog
module block_reflector_triangular_factor4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_v00,
    input  logic signed [15:0] in_v01,
    input  logic signed [15:0] in_v02,
    input  logic signed [15:0] in_v03,
    input  logic signed [15:0] in_v10,
    input  logic signed [15:0] in_v11,
    input  logic signed [15:0] in_v12,
    input  logic signed [15:0] in_v13,
    input  logic signed [15:0] in_v20,
    input  logic signed [15:0] in_v21,
    input  logic signed [15:0] in_v22,
    input  logic signed [15:0] in_v23,
    input  logic signed [15:0] in_v30,
    input  logic signed [15:0] in_v31,
    input  logic signed [15:0] in_v32,
    input  logic signed [15:0] in_v33,
    input  logic signed [15:0] in_tau0,
    input  logic signed [15:0] in_tau1,
    input  logic signed [15:0] in_tau2,
    input  logic signed [15:0] in_tau3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_t00,
    output logic signed [15:0] out_t01,
    output logic signed [15:0] out_t02,
    output logic signed [15:0] out_t03,
    output logic signed [15:0] out_t10,
    output logic signed [15:0] out_t11,
    output logic signed [15:0] out_t12,
    output logic signed [15:0] out_t13,
    output logic signed [15:0] out_t20,
    output logic signed [15:0] out_t21,
    output logic signed [15:0] out_t22,
    output logic signed [15:0] out_t23,
    output logic signed [15:0] out_t30,
    output logic signed [15:0] out_t31,
    output logic signed [15:0] out_t32,
    output logic signed [15:0] out_t33
);
```

### Behavior interface

```python
class BlockReflectorTriangularFactor4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_v00, in_v01, in_v02, in_v03,
        in_v10, in_v11, in_v12, in_v13,
        in_v20, in_v21, in_v22, in_v23,
        in_v30, in_v31, in_v32, in_v33,
        in_tau0, in_tau1, in_tau2, in_tau3,
    ):
        pass
```

The returned dictionary contains the sixteen matrix fields listed below.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_t00`, `out_t01`, `out_t02`, `out_t03`, `out_t10`, `out_t11`, `out_t12`,
  `out_t13`, `out_t20`, `out_t21`, `out_t22`, `out_t23`, `out_t30`, `out_t31`,
  `out_t32`, `out_t33`: 16.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `block_reflector_triangular_factor4x4`: top-level single-transaction kernel that computes the full 4×4 triangular factor.
- Internal recursive subproblems are part of the algorithmic contract: one two-reflector subproblem on all four rows and columns 0..1, one 2×2 forward-columnwise subproblem on rows/columns 2..3, and four 1×1 base cases.

## Functional Behavior

All inputs and outputs are 16-bit two's-complement signed integers. Every scalar multiplication and addition/subtraction is performed in mathematical integer arithmetic, then truncated to 16 bits by taking the low 16 bits of the two's-complement result. This truncation occurs after every scalar add, subtract, and multiply used to build intermediate matrix elements. Returned outputs are the 16-bit unsigned bit patterns corresponding to those signed 16-bit results.

The transaction computes `T` for the fixed case `DIRECT = 'F'`, `STOREV = 'C'`, `N = 4`, `K = 4`. The input matrix `V` is

- row 0: `(v00, v01, v02, v03)`
- row 1: `(v10, v11, v12, v13)`
- row 2: `(v20, v21, v22, v23)`
- row 3: `(v30, v31, v32, v33)`

with `vij = in_vij`, and the reflector scalars are
`tau0 = in_tau0`, `tau1 = in_tau1`, `tau2 = in_tau2`, `tau3 = in_tau3`.

The recursive split uses `l = floor(k/2)`, so for the top-level 4×4 problem `l = 2`. The matrix is partitioned as in the forward-columnwise QR case:

- `V11` = rows 0..1, cols 0..1
- `V21` = rows 2..3, cols 0..1
- `V22` = rows 2..3, cols 2..3

and the result is
`T = [[T11, T12], [0, T22]]`
with each block 2×2.

### Base case: 1×1 subproblem

A 1×1 subproblem ignores its `V` element and returns a 1×1 matrix whose only entry is the corresponding `tau` value:
`T(0,0) = tau`.

### 2×2 forward-columnwise subproblem

For any 2×2 subproblem with local matrix
`W = [[w00, w01], [w10, w11]]`
and local tau vector `(a0, a1)`, the result is upper triangular:

- `U00 = a0`
- `U11 = a1`
- `U10 = 0`

The off-diagonal term follows the same recursive QR formula with `l = 1`:

1. Form the local cross term `x = w10`.
2. Multiply by the unit-lower-triangular trailing block `[[1]]`, so `x` is unchanged.
3. There is no GEMM contribution because `n - k = 0`.
4. Left-multiply by `-U00`: `x = trunc16(-U00 * x)`.
5. Right-multiply by `U11`: `U01 = trunc16(x * U11)`.

Therefore
`U01 = trunc16(trunc16(-a0 * w10) * a1)`.

### Top-level 4×4 construction

Compute `T11` from the leading 2×2 subproblem using

- local reflector columns `[[1, 0], [v10, 1], [v20, v21], [v30, v31]]`
- local tau vector `(tau0, tau1)`

so:

- `T11(0,0) = tau0`
- `T11(1,1) = tau1`
- `T11(1,0) = 0`
- `T11(0,1) = trunc16(-tau0 * (v10 + v20*v21 + v30*v31) * tau1)`

Compute `T22` from the trailing 2×2 subproblem using

- local matrix `[[v22, v23], [v32, v33]]`
- local tau vector `(tau2, tau3)`

so:

- `T22(0,0) = tau2`
- `T22(1,1) = tau3`
- `T22(1,0) = 0`
- `T22(0,1) = trunc16(trunc16(-tau2 * v32) * tau3)`

Compute the top-right block `T12` as the QR-case formula
`T12 = -T11 * (V1^T * V2) * T22`,
using the same operation order as the recursive code:

1. Initialize `S` with `V21^T`:
   - `S00 = v20`
   - `S01 = v30`
   - `S10 = v21`
   - `S11 = v31`

2. Right-multiply `S` by the unit-lower-triangular matrix `V22`:
   `V22 = [[v22, v23], [v32, v33]]`, interpreted as unit lower triangular for this step, so only `v32` participates and the diagonal is treated as ones.
   The result after this step is:
   - `R00 = S00 + trunc16(S01 * v32)`
   - `R01 = S01`
   - `R10 = S10 + trunc16(S11 * v32)`
   - `R11 = S11`
   Each addition is truncated after addition.

3. There is no GEMM contribution because `n - k = 0`.

4. Left-multiply by `-T11`, where `T11` is upper triangular and non-unit:
   - `L00 = trunc16(-(trunc16(T11(0,0) * R00) + trunc16(T11(0,1) * R10)))`
   - `L01 = trunc16(-(trunc16(T11(0,0) * R01) + trunc16(T11(0,1) * R11)))`
   - `L10 = trunc16(-(trunc16(T11(1,1) * R10)))`
   - `L11 = trunc16(-(trunc16(T11(1,1) * R11)))`
   Each product is truncated to 16 bits before the summed row result is formed, and each row sum is truncated to 16 bits before negation truncation.

5. Right-multiply by `T22`, where `T22` is upper triangular and non-unit:
   - `T12(0,0) = trunc16(L00 * T22(0,0))`
   - `T12(1,0) = trunc16(L10 * T22(0,0))`
   - `T12(0,1) = trunc16(trunc16(L00 * T22(0,1)) + trunc16(L01 * T22(1,1)))`
   - `T12(1,1) = trunc16(trunc16(L10 * T22(0,1)) + trunc16(L11 * T22(1,1)))`

### Final 4×4 output matrix

The output matrix is upper triangular by construction:

- row 0: `out_t00 = T11(0,0)`, `out_t01 = T11(0,1)`, `out_t02 = T12(0,0)`, `out_t03 = T12(0,1)`
- row 1: `out_t10 = 0`,      `out_t11 = T11(1,1)`, `out_t12 = T12(1,0)`, `out_t13 = T12(1,1)`
- row 2: `out_t20 = 0`,      `out_t21 = 0`,        `out_t22 = T22(0,0)`, `out_t23 = T22(0,1)`
- row 3: `out_t30 = 0`,      `out_t31 = 0`,        `out_t32 = 0`,        `out_t33 = T22(1,1)`

All zero entries are exact integer zero.

Edge cases:
- No special handling exists for any specific `V` or `tau` values.
- Inputs are arbitrary 16-bit patterns interpreted as signed two's-complement integers.
- Overflow wraps modulo `2^16` at every stated truncation point.

## Reset Behavior

Reset is active-high. The module has no architectural state other than any implementation-specific buffering required by the handshake. After reset, `in_ready` shall be high whenever the module is idle and able to accept a transaction, `out_valid` shall be low, and all output payload ports `out_t00` through `out_t33` shall be driven to zero until a valid output transaction is presented. No computational state persists between transactions.

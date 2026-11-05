# HouseholderQrApply4x4

This module applies the orthogonal factor represented by four stored Householder reflectors to a 4×4 real matrix. One accepted input transaction supplies a compact reflector matrix `A`, four scalar reflector factors `tau[0..3]`, a 4×4 operand matrix `C`, and a transpose control. The module computes the unblocked left-side LAPACK `DORM2R` update for the fixed case `SIDE='L'`, `M=N=K=4`: it overwrites `C` with `Q*C` when `in_trans=0` or with `Q^T*C` when `in_trans=1`, where `Q = H(0) H(1) H(2) H(3)` and each `H(i) = I - tau[i] * v_i * v_i^T`. Arithmetic is pure 64-bit integer bit-pattern arithmetic on the input ports; there is no floating-point interpretation.

## Interface

### RTL interface

```systemverilog
module householder_qr_apply4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_trans,

    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a30,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a31,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a32,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a33,

    input  logic [63:0]  in_tau0,
    input  logic [63:0]  in_tau1,
    input  logic [63:0]  in_tau2,
    input  logic [63:0]  in_tau3,

    input  logic [63:0]  in_c00,
    input  logic [63:0]  in_c10,
    input  logic [63:0]  in_c20,
    input  logic [63:0]  in_c30,
    input  logic [63:0]  in_c01,
    input  logic [63:0]  in_c11,
    input  logic [63:0]  in_c21,
    input  logic [63:0]  in_c31,
    input  logic [63:0]  in_c02,
    input  logic [63:0]  in_c12,
    input  logic [63:0]  in_c22,
    input  logic [63:0]  in_c32,
    input  logic [63:0]  in_c03,
    input  logic [63:0]  in_c13,
    input  logic [63:0]  in_c23,
    input  logic [63:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [63:0]  out_c00,
    output logic [63:0]  out_c10,
    output logic [63:0]  out_c20,
    output logic [63:0]  out_c30,
    output logic [63:0]  out_c01,
    output logic [63:0]  out_c11,
    output logic [63:0]  out_c21,
    output logic [63:0]  out_c31,
    output logic [63:0]  out_c02,
    output logic [63:0]  out_c12,
    output logic [63:0]  out_c22,
    output logic [63:0]  out_c32,
    output logic [63:0]  out_c03,
    output logic [63:0]  out_c13,
    output logic [63:0]  out_c23,
    output logic [63:0]  out_c33
);
endmodule
```

### Behavior interface

```python
class HouseholderQrApply4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_trans,
            in_a00, in_a10, in_a20, in_a30,
            in_a01, in_a11, in_a21, in_a31,
            in_a02, in_a12, in_a22, in_a32,
            in_a03, in_a13, in_a23, in_a33,
            in_tau0, in_tau1, in_tau2, in_tau3,
            in_c00, in_c10, in_c20, in_c30,
            in_c01, in_c11, in_c21, in_c31,
            in_c02, in_c12, in_c22, in_c32,
            in_c03, in_c13, in_c23, in_c33):
        pass
```

The return value is always exactly one output transaction, represented by one dict containing the sixteen matrix fields listed below.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c10`, `out_c11`, `out_c12`,
  `out_c13`, `out_c20`, `out_c21`, `out_c22`, `out_c23`, `out_c30`, `out_c31`,
  `out_c32`, `out_c33`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `householder_qr_apply4x4`: top-level module that accepts one 4×4 reflector description and one 4×4 matrix, applies four left-side Householder reflectors in the required order, and returns one transformed 4×4 matrix.

## Functional Behavior

All matrix elements and scalar factors are 64-bit unsigned integers on the interface. Every add, subtract, and multiply is performed as integer arithmetic modulo `2^64`. A product is truncated to its low 64 bits before any subsequent addition or subtraction. Every sum or difference is truncated to its low 64 bits. No values are treated as signed.

The input matrix `A` is interpreted only through the strictly lower-triangular Householder storage used by the fixed 4-reflector QR form. The only `A` elements that participate in the computation are:

- reflector 0 tail: `a10 = in_a10`, `a20 = in_a20`, `a30 = in_a30`
- reflector 1 tail: `a21 = in_a21`, `a31 = in_a31`
- reflector 2 tail: `a32 = in_a32`
- reflector 3 tail: no stored tail elements

The ports `in_a00`, `in_a01`, `in_a11`, `in_a02`, `in_a12`, `in_a22`, `in_a03`, `in_a13`, `in_a23`, and `in_a33` are ignored by the computation.

The four Householder vectors are fixed to:

- `v0 = [1, a10, a20, a30]^T`
- `v1 = [0, 1, a21, a31]^T`
- `v2 = [0, 0, 1, a32]^T`
- `v3 = [0, 0, 0, 1]^T`

The four elementary reflectors are:

- `H0 = I - tau0 * v0 * v0^T`
- `H1 = I - tau1 * v1 * v1^T`
- `H2 = I - tau2 * v2 * v2^T`
- `H3 = I - tau3 * v3 * v3^T`

where `tau0 = in_tau0`, `tau1 = in_tau1`, `tau2 = in_tau2`, and `tau3 = in_tau3`, and every multiply and subtract uses the modulo-`2^64` arithmetic rule above.

The input matrix `C` is assembled in column-major notation from the ports:

- column 0: `[in_c00, in_c10, in_c20, in_c30]^T`
- column 1: `[in_c01, in_c11, in_c21, in_c31]^T`
- column 2: `[in_c02, in_c12, in_c22, in_c32]^T`
- column 3: `[in_c03, in_c13, in_c23, in_c33]^T`

The output matrix is produced by applying the four reflectors from the left. The application order depends only on `in_trans`:

- if `in_trans == 0`, compute `C_out = H0(H1(H2(H3(C))))`
- if `in_trans != 0`, compute `C_out = H3(H2(H1(H0(C))))`

The execution order is `[3,2,1,0]` for `in_trans == 0` and `[0,1,2,3]` for `in_trans != 0`. Each reflector updates the whole 4×4 matrix before the next is applied.

Applying one reflector `Hi = I - tau[i] * v_i * v_i^T` to the current 4×4 matrix `R` is defined column by column. For each column vector `r_j` of `R`, compute:

- `dot = v_i^T * r_j`
- `w = tau[i] * dot`
- new column `r_j' = r_j - v_i * w`

with all scalar operations modulo `2^64`.

For the fixed vectors above, the exact dot products and updates are:

For reflector 0 on column `j` with elements `[r0j, r1j, r2j, r3j]^T`:

- `dot = (((r0j + (a10 * r1j)) + (a20 * r2j)) + (a30 * r3j)) mod 2^64`
- `w = (tau0 * dot) mod 2^64`
- `r0j' = (r0j - w) mod 2^64`
- `r1j' = (r1j - (a10 * w)) mod 2^64`
- `r2j' = (r2j - (a20 * w)) mod 2^64`
- `r3j' = (r3j - (a30 * w)) mod 2^64`

For reflector 1 on column `j`:

- `dot = ((r1j + (a21 * r2j)) + (a31 * r3j)) mod 2^64`
- `w = (tau1 * dot) mod 2^64`
- `r0j' = r0j`
- `r1j' = (r1j - w) mod 2^64`
- `r2j' = (r2j - (a21 * w)) mod 2^64`
- `r3j' = (r3j - (a31 * w)) mod 2^64`

For reflector 2 on column `j`:

- `dot = (r2j + (a32 * r3j)) mod 2^64`
- `w = (tau2 * dot) mod 2^64`
- `r0j' = r0j`
- `r1j' = r1j`
- `r2j' = (r2j - w) mod 2^64`
- `r3j' = (r3j - (a32 * w)) mod 2^64`

For reflector 3 on column `j`:

- `dot = r3j`
- `w = (tau3 * dot) mod 2^64`
- `r0j' = r0j`
- `r1j' = r1j`
- `r2j' = r2j`
- `r3j' = (r3j - w) mod 2^64`

After all four reflectors are applied in the selected order, the 16 result elements are returned on:

- `out_c00, out_c10, out_c20, out_c30` for column 0
- `out_c01, out_c11, out_c21, out_c31` for column 1
- `out_c02, out_c12, out_c22, out_c32` for column 2
- `out_c03, out_c13, out_c23, out_c33` for column 3

There are no exceptional cases. Every 64-bit input pattern is legal. `in_trans` is treated as a 1-bit control for the test `in_trans == 0`; any nonzero value selects the transpose order.

## Reset Behavior

Reset is synchronous active-high through port `rst`.

Architectural state consists only of the registered output transaction payload, if the implementation chooses to register it for handshake purposes. After reset:

- `out_valid = 0`
- `out_c00 = 0`
- `out_c10 = 0`
- `out_c20 = 0`
- `out_c30 = 0`
- `out_c01 = 0`
- `out_c11 = 0`
- `out_c21 = 0`
- `out_c31 = 0`
- `out_c02 = 0`
- `out_c12 = 0`
- `out_c22 = 0`
- `out_c32 = 0`
- `out_c03 = 0`
- `out_c13 = 0`
- `out_c23 = 0`
- `out_c33 = 0`

The module has no other architectural state. Each accepted input transaction is independent of all previous transactions.

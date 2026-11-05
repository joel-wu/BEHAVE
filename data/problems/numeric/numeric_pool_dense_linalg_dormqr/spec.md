# OrmqrReflectorStep

This module performs one elementary Householder-reflector update from the orthogonal-matrix application used by QR-based routines. Each accepted input transaction supplies a single reflector in compact form and a fixed-size 4×4 real matrix block. The module applies the reflector either from the left or from the right, producing exactly one updated 4×4 matrix block. The implemented algorithm is the rank-1 Householder update `H = I - tau * v * v^T`, applied as `H*C` for left side or `C*H` for right side. Arithmetic uses 32-bit two's-complement signed integers on all matrix and reflector elements; every add, subtract, and multiply result is mathematically exact and then truncated to 32 bits only at the final output ports.

## Interface

### RTL interface

```systemverilog
module ormqr_reflector_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,
    input  logic [31:0] in_tau,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,
    input  logic [31:0] in_c00,
    input  logic [31:0] in_c01,
    input  logic [31:0] in_c02,
    input  logic [31:0] in_c03,
    input  logic [31:0] in_c10,
    input  logic [31:0] in_c11,
    input  logic [31:0] in_c12,
    input  logic [31:0] in_c13,
    input  logic [31:0] in_c20,
    input  logic [31:0] in_c21,
    input  logic [31:0] in_c22,
    input  logic [31:0] in_c23,
    input  logic [31:0] in_c30,
    input  logic [31:0] in_c31,
    input  logic [31:0] in_c32,
    input  logic [31:0] in_c33,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_c00,
    output logic [31:0] out_c01,
    output logic [31:0] out_c02,
    output logic [31:0] out_c03,
    output logic [31:0] out_c10,
    output logic [31:0] out_c11,
    output logic [31:0] out_c12,
    output logic [31:0] out_c13,
    output logic [31:0] out_c20,
    output logic [31:0] out_c21,
    output logic [31:0] out_c22,
    output logic [31:0] out_c23,
    output logic [31:0] out_c30,
    output logic [31:0] out_c31,
    output logic [31:0] out_c32,
    output logic [31:0] out_c33
);
```

`in_side` selects the side of application:
- `in_side = 0`: apply from the left, `C_out = H * C_in`
- `in_side = 1`: apply from the right, `C_out = C_in * H`

The reflector vector is fixed to length 4 and is formed as:
- `v = [1, in_a1, in_a2, in_a3]^T`

Each accepted input transaction produces exactly one output transaction. There are no legal input transactions that produce zero outputs or more than one output.

### Behavior interface

```python
class OrmqrReflectorStep:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_tau,
        in_a1,
        in_a2,
        in_a3,
        in_c00,
        in_c01,
        in_c02,
        in_c03,
        in_c10,
        in_c11,
        in_c12,
        in_c13,
        in_c20,
        in_c21,
        in_c22,
        in_c23,
        in_c30,
        in_c31,
        in_c32,
        in_c33,
    ):
        pass
```

The returned dictionary contains exactly these keys:
`out_c00`, `out_c01`, `out_c02`, `out_c03`,
`out_c10`, `out_c11`, `out_c12`, `out_c13`,
`out_c20`, `out_c21`, `out_c22`, `out_c23`,
`out_c30`, `out_c31`, `out_c32`, `out_c33`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c10`, `out_c11`, `out_c12`,
  `out_c13`, `out_c20`, `out_c21`, `out_c22`, `out_c23`, `out_c30`, `out_c31`,
  `out_c32`, `out_c33`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ormqr_reflector_step`: top-level module that accepts one reflector and one 4×4 matrix block, computes one Householder update, and emits one updated 4×4 matrix block.

## Functional Behavior

All scalar inputs except `in_side` are interpreted as signed 32-bit two's-complement integers.

Define:
- `tau = s32(in_tau)`
- `v0 = 1`
- `v1 = s32(in_a1)`
- `v2 = s32(in_a2)`
- `v3 = s32(in_a3)`

Define the input matrix `C` by signed interpretation of the 16 matrix ports:
- row 0: `c00, c01, c02, c03`
- row 1: `c10, c11, c12, c13`
- row 2: `c20, c21, c22, c23`
- row 3: `c30, c31, c32, c33`

The reflector matrix is:
- `H = I - tau * v * v^T`
- where `I` is the 4×4 identity matrix and `v = [v0, v1, v2, v3]^T`

The update is computed as follows.

If `in_side = 0`:
- compute `w = v^T * C`, a row vector of length 4
- for each column `j`:
  - `w[j] = v0*c0j + v1*c1j + v2*c2j + v3*c3j`
- compute `C_out = C - tau * v * w`
- for each row `r` and column `j`:
  - `out[r][j] = c[r][j] - tau * v[r] * w[j]`

If `in_side = 1`:
- compute `w = C * v`, a column vector of length 4
- for each row `i`:
  - `w[i] = ci0*v0 + ci1*v1 + ci2*v2 + ci3*v3`
- compute `C_out = C - tau * w * v^T`
- for each row `i` and column `c`:
  - `out[i][c] = c[i][c] - tau * w[i] * v[c]`

All intermediate arithmetic is performed in unbounded mathematical integer precision for specification purposes. After the final matrix element values are obtained, each output element is reduced modulo `2^32` and driven on its corresponding output port as a 32-bit unsigned bit pattern.

Equivalent signed-to-output mapping:
- `out_cXY = (final_signed_value of element XY) mod 2^32`

`in_side` values other than 0 are treated as 1.

Worked examples:

1. Left application with `tau = 0`:
- `H = I`
- output matrix equals input matrix exactly on all 16 elements

2. Left application with `v = [1,0,0,0]^T`:
- `H = I - tau * e0 * e0^T`
- only row 0 changes
- `out_0j = c0j - tau * c0j`
- rows 1, 2, and 3 are unchanged

3. Right application with `v = [1,0,0,0]^T`:
- only column 0 changes
- `out_i0 = ci0 - tau * ci0`
- columns 1, 2, and 3 are unchanged

## Reset Behavior

The module uses synchronous active-high reset `rst`.

On reset:
- there is no architectural state
- output storage, if any, is initialized so that `out_valid = 0`
- all output data ports are initialized to `0`
- the module is ready to accept the next input transaction after reset is deasserted

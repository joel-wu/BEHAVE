# CholeskySolve4x2

CholeskySolve4x2 solves one 4×2 linear system `A * X = B` using a precomputed 4×4 Cholesky triangular factor of the symmetric positive-definite matrix `A`. The transaction computes exactly the two triangular solves used by LAPACK `DPOTRS`: for an upper-stored factor it solves `U^T * Y = B` followed by `U * X = Y`; for a lower-stored factor it solves `L * Y = B` followed by `L^T * X = Y`. All values are 16-bit signed integers, all arithmetic inside the solve is performed in signed 32-bit integer arithmetic, and each division is signed truncation toward zero.

## Interface

### RTL interface

```systemverilog
module cholesky_solve4x2 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,

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

    input  logic [15:0]  in_b00,
    input  logic [15:0]  in_b01,
    input  logic [15:0]  in_b10,
    input  logic [15:0]  in_b11,
    input  logic [15:0]  in_b20,
    input  logic [15:0]  in_b21,
    input  logic [15:0]  in_b30,
    input  logic [15:0]  in_b31,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x00,
    output logic [15:0]  out_x01,
    output logic [15:0]  out_x10,
    output logic [15:0]  out_x11,
    output logic [15:0]  out_x20,
    output logic [15:0]  out_x21,
    output logic [15:0]  out_x30,
    output logic [15:0]  out_x31
);
```

### Behavior interface

```python
class CholeskySolve4x2:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_b00, in_b01,
        in_b10, in_b11,
        in_b20, in_b21,
        in_b30, in_b31,
    ):
        pass
```

`process()` returns one output transaction per accepted input transaction as a dict with keys:
`out_x00`, `out_x01`, `out_x10`, `out_x11`, `out_x20`, `out_x21`, `out_x30`, `out_x31`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x00`, `out_x01`, `out_x10`, `out_x11`, `out_x20`, `out_x21`, `out_x30`,
  `out_x31`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cholesky_solve4x2`: top-level module implementing one 4×2 Cholesky backsolve transaction.

## Functional Behavior

All input matrix elements are interpreted as signed 16-bit two's-complement integers.

Define the 4×4 input factor matrix `T` from the ports:

- Row 0: `t00=in_a00`, `t01=in_a01`, `t02=in_a02`, `t03=in_a03`
- Row 1: `t10=in_a10`, `t11=in_a11`, `t12=in_a12`, `t13=in_a13`
- Row 2: `t20=in_a20`, `t21=in_a21`, `t22=in_a22`, `t23=in_a23`
- Row 3: `t30=in_a30`, `t31=in_a31`, `t32=in_a32`, `t33=in_a33`

Define the 4×2 right-hand-side matrix `B` from the ports:

- Column 0: `b00=in_b00`, `b10=in_b10`, `b20=in_b20`, `b30=in_b30`
- Column 1: `b01=in_b01`, `b11=in_b11`, `b21=in_b21`, `b31=in_b31`

The transaction is always accepted as valid data; there is no argument checking, no error output, and no quick-return mode. One accepted input produces exactly one output transaction.

Every multiply, add, subtract, and divide in the equations below is performed in signed 32-bit integer arithmetic. Signed division uses truncation toward zero. For a division `n / d`, if `d == 0`, the quotient is defined to be `0`. After the full solve, each output is the low 16 bits of the signed 32-bit result, returned as a 16-bit output bit pattern.

`in_uplo` selects which triangular factor interpretation is used:

- `in_uplo == 1`: the stored factor is upper triangular `U`, and the solve order is `U^T * Y = B` then `U * X = Y`.
- `in_uplo == 0`: the stored factor is lower triangular `L`, and the solve order is `L * Y = B` then `L^T * X = Y`.

### Upper-factor case (`in_uplo == 1`)

Only the diagonal and upper-triangular entries are mathematically used:
`u00=t00`, `u01=t01`, `u02=t02`, `u03=t03`,
`u11=t11`, `u12=t12`, `u13=t13`,
`u22=t22`, `u23=t23`,
`u33=t33`.

First solve `U^T * Y = B` independently for each right-hand-side column.

For column 0:
- `y00 = div0(b00, u00)`
- `y10 = div0(b10 - u01*y00, u11)`
- `y20 = div0(b20 - u02*y00 - u12*y10, u22)`
- `y30 = div0(b30 - u03*y00 - u13*y10 - u23*y20, u33)`

For column 1:
- `y01 = div0(b01, u00)`
- `y11 = div0(b11 - u01*y01, u11)`
- `y21 = div0(b21 - u02*y01 - u12*y11, u22)`
- `y31 = div0(b31 - u03*y01 - u13*y11 - u23*y21, u33)`

Then solve `U * X = Y` independently for each column.

For column 0:
- `x30 = div0(y30, u33)`
- `x20 = div0(y20 - u23*x30, u22)`
- `x10 = div0(y10 - u12*x20 - u13*x30, u11)`
- `x00 = div0(y00 - u01*x10 - u02*x20 - u03*x30, u00)`

For column 1:
- `x31 = div0(y31, u33)`
- `x21 = div0(y21 - u23*x31, u22)`
- `x11 = div0(y11 - u12*x21 - u13*x31, u11)`
- `x01 = div0(y01 - u01*x11 - u02*x21 - u03*x31, u00)`

### Lower-factor case (`in_uplo == 0`)

Only the diagonal and lower-triangular entries are mathematically used:
`l00=t00`,
`l10=t10`, `l11=t11`,
`l20=t20`, `l21=t21`, `l22=t22`,
`l30=t30`, `l31=t31`, `l32=t32`, `l33=t33`.

First solve `L * Y = B` independently for each right-hand-side column.

For column 0:
- `y00 = div0(b00, l00)`
- `y10 = div0(b10 - l10*y00, l11)`
- `y20 = div0(b20 - l20*y00 - l21*y10, l22)`
- `y30 = div0(b30 - l30*y00 - l31*y10 - l32*y20, l33)`

For column 1:
- `y01 = div0(b01, l00)`
- `y11 = div0(b11 - l10*y01, l11)`
- `y21 = div0(b21 - l20*y01 - l21*y11, l22)`
- `y31 = div0(b31 - l30*y01 - l31*y11 - l32*y21, l33)`

Then solve `L^T * X = Y` independently for each column.

For column 0:
- `x30 = div0(y30, l33)`
- `x20 = div0(y20 - l32*x30, l22)`
- `x10 = div0(y10 - l21*x20 - l31*x30, l11)`
- `x00 = div0(y00 - l10*x10 - l20*x20 - l30*x30, l00)`

For column 1:
- `x31 = div0(y31, l33)`
- `x21 = div0(y21 - l32*x31, l22)`
- `x11 = div0(y11 - l21*x21 - l31*x31, l11)`
- `x01 = div0(y01 - l10*x11 - l20*x21 - l30*x31, l00)`

### Output mapping

The returned output transaction is:
- `out_x00 = x00[15:0]`
- `out_x01 = x01[15:0]`
- `out_x10 = x10[15:0]`
- `out_x11 = x11[15:0]`
- `out_x20 = x20[15:0]`
- `out_x21 = x21[15:0]`
- `out_x30 = x30[15:0]`
- `out_x31 = x31[15:0]`

Worked example for the lower-factor case: if `l00=l11=l22=l33=1`, `l10=l20=l21=l30=l31=l32=0`, then `X=B`, so each `out_xij` equals the corresponding `in_bij[15:0]`. The upper-factor case has the same property when `u00=u11=u22=u33=1` and all strictly upper-triangular entries are `0`.

## Reset Behavior

The module uses synchronous active-high reset `rst`.

Architectural state consists only of the output holding registers and the output-valid/busy state.

On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_x00 = 16'h0000`
- `out_x01 = 16'h0000`
- `out_x10 = 16'h0000`
- `out_x11 = 16'h0000`
- `out_x20 = 16'h0000`
- `out_x21 = 16'h0000`
- `out_x30 = 16'h0000`
- `out_x31 = 16'h0000`

No other architectural state is retained across reset.

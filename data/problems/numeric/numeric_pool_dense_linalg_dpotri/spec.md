# CholeskyInverse3x3

This module computes the inverse of a 3×3 real symmetric positive-definite matrix from a previously computed Cholesky factor. The accepted input transaction supplies either the upper-triangular factor `U` such that `A = U^T * U` or the lower-triangular factor `L` such that `A = L * L^T`. The module first inverts the triangular factor, then forms `inv(U) * inv(U)^T` for upper mode or `inv(L)^T * inv(L)` for lower mode. The computation is fixed to 3×3 and uses signed integer fixed-point arithmetic with exact operation order and truncation defined below.

## Interface

### RTL interface

```systemverilog
module cholesky_inverse_3x3 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic [31:0]  in_a00,
    input  logic [31:0]  in_a01,
    input  logic [31:0]  in_a02,
    input  logic [31:0]  in_a10,
    input  logic [31:0]  in_a11,
    input  logic [31:0]  in_a12,
    input  logic [31:0]  in_a20,
    input  logic [31:0]  in_a21,
    input  logic [31:0]  in_a22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_info,
    output logic [31:0]  out_a00,
    output logic [31:0]  out_a01,
    output logic [31:0]  out_a02,
    output logic [31:0]  out_a10,
    output logic [31:0]  out_a11,
    output logic [31:0]  out_a12,
    output logic [31:0]  out_a20,
    output logic [31:0]  out_a21,
    output logic [31:0]  out_a22
);
```

`in_uplo = 1'b1` selects upper-triangular input.  
`in_uplo = 1'b0` selects lower-triangular input.

All matrix ports carry signed Q16.16 two's-complement values in 32-bit containers.

### Behavior interface

```python
class CholeskyInverse3x3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_uplo,
            in_a00, in_a01, in_a02,
            in_a10, in_a11, in_a12,
            in_a20, in_a21, in_a22):
        pass
```

The return dictionary keys are `out_info`, `out_a00`, `out_a01`, `out_a02`, `out_a10`, `out_a11`, `out_a12`, `out_a20`, `out_a21`, and `out_a22`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a00`, `out_a01`, `out_a02`, `out_a10`, `out_a11`, `out_a12`, `out_a20`,
  `out_a21`, `out_a22`, `out_info`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cholesky_inverse_3x3`: top-level module that accepts one 3×3 triangular Cholesky factor, computes its inverse, and forms the symmetric inverse matrix.

## Functional Behavior

All input matrix elements are interpreted as signed 32-bit Q16.16 values. Let `S(x)` denote conversion from a 32-bit port value to a signed integer in the range `[-2^31, 2^31-1]`. Let `Q = 16`.

The transaction is a 1:1 mapping: one accepted input transaction produces one output transaction.

### Input interpretation

If `in_uplo = 1`, the meaningful input elements are the upper triangle:
- `u00 = S(in_a00)`
- `u01 = S(in_a01)`
- `u02 = S(in_a02)`
- `u11 = S(in_a11)`
- `u12 = S(in_a12)`
- `u22 = S(in_a22)`

The lower-triangle input elements `in_a10`, `in_a20`, `in_a21` are ignored.

If `in_uplo = 0`, the meaningful input elements are the lower triangle:
- `l00 = S(in_a00)`
- `l10 = S(in_a10)`
- `l11 = S(in_a11)`
- `l20 = S(in_a20)`
- `l21 = S(in_a21)`
- `l22 = S(in_a22)`

The upper-triangle input elements `in_a01`, `in_a02`, `in_a12` are ignored.

### Fixed-point primitives

Signed fixed-point multiplication:
- `mul_q(a, b) = trunc_toward_zero((a * b) / 2^Q)`

Signed fixed-point division:
- `div_q(a, b) = trunc_toward_zero((a * 2^Q) / b)`

`trunc_toward_zero` means quotient rounding toward zero. Division by zero is not allowed by the factor-invertible path and is handled by `out_info` as defined below.

All additions and subtractions are exact mathematical integer additions and subtractions before final output conversion. Final outputs are converted to 32-bit two's-complement by modulo `2^32`.

### Status output

`out_info` is a 32-bit unsigned integer status code.

- `out_info = 0` if the triangular factor is invertible.
- `out_info = 1` if `in_uplo = 1` and `u00 = 0`.
- `out_info = 2` if `in_uplo = 1`, `u00 != 0`, and `u11 = 0`.
- `out_info = 3` if `in_uplo = 1`, `u00 != 0`, `u11 != 0`, and `u22 = 0`.
- `out_info = 1` if `in_uplo = 0` and `l00 = 0`.
- `out_info = 2` if `in_uplo = 0`, `l00 != 0`, and `l11 = 0`.
- `out_info = 3` if `in_uplo = 0`, `l00 != 0`, `l11 != 0`, and `l22 = 0`.

If `out_info != 0`, the inverse is not computed and every matrix output is `0`.

### Upper-triangular mode (`in_uplo = 1`)

The input triangular factor is

- row 0: `[u00, u01, u02]`
- row 1: `[0,   u11, u12]`
- row 2: `[0,   0,   u22]`

Its inverse `R = inv(U)` is upper triangular and is computed in this exact order:

1. `r22 = div_q(1 << Q, u22)`
2. `r11 = div_q(1 << Q, u11)`
3. `r00 = div_q(1 << Q, u00)`
4. `r12 = -div_q(mul_q(u12, r22), u11)`
5. `r01 = -div_q(mul_q(u01, r11), u00)`
6. `t0  = mul_q(u01, r12)`
7. `t1  = mul_q(u02, r22)`
8. `r02 = -div_q(t0 + t1, u00)`

The symmetric inverse matrix is then `C = R * R^T`, with entries computed in this exact order:

1. `c00 = mul_q(r00, r00) + mul_q(r01, r01) + mul_q(r02, r02)`
2. `c01 =                mul_q(r01, r11) + mul_q(r02, r12)`
3. `c02 =                                   mul_q(r02, r22)`
4. `c11 =                    mul_q(r11, r11) + mul_q(r12, r12)`
5. `c12 =                                       mul_q(r12, r22)`
6. `c22 =                                           mul_q(r22, r22)`

The full 3×3 output matrix is symmetric:
- `out_a00 = c00`
- `out_a01 = c01`
- `out_a02 = c02`
- `out_a10 = c01`
- `out_a11 = c11`
- `out_a12 = c12`
- `out_a20 = c02`
- `out_a21 = c12`
- `out_a22 = c22`

### Lower-triangular mode (`in_uplo = 0`)

The input triangular factor is

- row 0: `[l00, 0,   0]`
- row 1: `[l10, l11, 0]`
- row 2: `[l20, l21, l22]`

Its inverse `R = inv(L)` is lower triangular and is computed in this exact order:

1. `r22 = div_q(1 << Q, l22)`
2. `r11 = div_q(1 << Q, l11)`
3. `r00 = div_q(1 << Q, l00)`
4. `r21 = -div_q(mul_q(l21, r22), l11)`
5. `r10 = -div_q(mul_q(l10, r11), l00)`
6. `t0  = mul_q(l21, r10)`
7. `t1  = mul_q(l20, r00)`
8. `r20 = -div_q(t0 + t1, l22)`

The symmetric inverse matrix is then `C = R^T * R`, with entries computed in this exact order:

1. `c00 = mul_q(r00, r00) + mul_q(r10, r10) + mul_q(r20, r20)`
2. `c01 =                mul_q(r10, r11) + mul_q(r20, r21)`
3. `c02 =                                   mul_q(r20, r22)`
4. `c11 =                    mul_q(r11, r11) + mul_q(r21, r21)`
5. `c12 =                                       mul_q(r21, r22)`
6. `c22 =                                           mul_q(r22, r22)`

The full 3×3 output matrix is symmetric:
- `out_a00 = c00`
- `out_a01 = c01`
- `out_a02 = c02`
- `out_a10 = c01`
- `out_a11 = c11`
- `out_a12 = c12`
- `out_a20 = c02`
- `out_a21 = c12`
- `out_a22 = c22`

### Output encoding

Each matrix output is the corresponding signed integer result converted to a 32-bit unsigned port value by modulo `2^32`.

## Reset Behavior

The module has synchronous active-high reset `rst`.

Architectural state: none.

After reset, the output interface is idle:
- `out_valid = 0`

Because there is no architectural state, reset does not preserve any pending transaction. When the module is not presenting a completed output transaction, all output payload ports may be driven as zero:
- `out_info = 0`
- `out_a00 = 0`
- `out_a01 = 0`
- `out_a02 = 0`
- `out_a10 = 0`
- `out_a11 = 0`
- `out_a12 = 0`
- `out_a20 = 0`
- `out_a21 = 0`
- `out_a22 = 0`

# Hermitian Rank-K Matrix Update Tile

This module performs one complete Hermitian rank-k update on a fixed 2×2 complex matrix tile using the ZHERK operation. It computes either `C := alpha*A*A^H + beta*C` or `C := alpha*A^H*A + beta*C`, but only the selected stored triangle of `C` is referenced and produced, and diagonal outputs are forced real. The transaction is a single 2×2 tile with fixed `N=2` and `K=2`, using signed fixed-point Q4.12 arithmetic and reproducing the branch structure of the ZHERK reference algorithm for the selected triangle.

## Interface

### RTL interface

```systemverilog
module hermitian_rankk_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,          // 1 = upper, 0 = lower
    input  logic         in_trans,         // 0 = N, 1 = C
    input  logic [15:0]  in_alpha,         // signed Q4.12
    input  logic [15:0]  in_beta,          // signed Q4.12

    input  logic [15:0]  in_a00_re,        // signed Q4.12
    input  logic [15:0]  in_a00_im,        // signed Q4.12
    input  logic [15:0]  in_a01_re,        // signed Q4.12
    input  logic [15:0]  in_a01_im,        // signed Q4.12
    input  logic [15:0]  in_a10_re,        // signed Q4.12
    input  logic [15:0]  in_a10_im,        // signed Q4.12
    input  logic [15:0]  in_a11_re,        // signed Q4.12
    input  logic [15:0]  in_a11_im,        // signed Q4.12

    input  logic [15:0]  in_c00_re,        // signed Q4.12
    input  logic [15:0]  in_c00_im,        // signed Q4.12, ignored
    input  logic [15:0]  in_c01_re,        // signed Q4.12
    input  logic [15:0]  in_c01_im,        // signed Q4.12
    input  logic [15:0]  in_c10_re,        // signed Q4.12
    input  logic [15:0]  in_c10_im,        // signed Q4.12
    input  logic [15:0]  in_c11_re,        // signed Q4.12
    input  logic [15:0]  in_c11_im,        // signed Q4.12, ignored

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00_re,       // signed Q4.12
    output logic [15:0]  out_c00_im,       // signed Q4.12, always 0
    output logic [15:0]  out_c01_re,       // signed Q4.12
    output logic [15:0]  out_c01_im,       // signed Q4.12
    output logic [15:0]  out_c10_re,       // signed Q4.12
    output logic [15:0]  out_c10_im,       // signed Q4.12
    output logic [15:0]  out_c11_re,       // signed Q4.12
    output logic [15:0]  out_c11_im        // signed Q4.12, always 0
);
```

### Behavior interface

```python
class HermitianRankkTile:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_alpha,
        in_beta,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
    ):
        pass
```

Each `process(...)` call consumes one accepted matrix-tile request and returns
one dictionary with exactly the keys `out_c00_re`, `out_c00_im`, `out_c01_re`,
`out_c01_im`, `out_c10_re`, `out_c10_im`, `out_c11_re`, and `out_c11_im`.
Every numeric argument and result is an unsigned 16-bit port bit pattern
carrying the signed Q4.12 value; `in_uplo` and `in_trans` are one-bit controls.
The diagonal imaginary fields and non-stored triangle remain present with
their specified zero values. The result is never `None` or a list.

No matrix state persists between calls: the complete input tile is supplied
each time. `reset()` returns `None` and emits no record. Calls correspond to
accepted transactions, not clock cycles, stalls or output consumption.

## Modules

- `hermitian_rankk_tile`: top-level module implementing one 2×2 Hermitian rank-k update transaction.

## Functional Behavior

All numeric ports other than `in_uplo` and `in_trans` are 16-bit two's-complement signed fixed-point values in Q4.12 format.

A port value `x` represents the signed integer `to_signed(x, 16)`. The represented real value is `to_signed(x, 16) / 2^12`.

The tile size is fixed:
- `N = 2`
- `K = 2`

The complex matrices are:
- `A[0][0] = a00 = in_a00_re + j*in_a00_im`
- `A[0][1] = a01 = in_a01_re + j*in_a01_im`
- `A[1][0] = a10 = in_a10_re + j*in_a10_im`
- `A[1][1] = a11 = in_a11_re + j*in_a11_im`

- `C[0][0] = c00 = in_c00_re + j*in_c00_im`
- `C[0][1] = c01 = in_c01_re + j*in_c01_im`
- `C[1][0] = c10 = in_c10_re + j*in_c10_im`
- `C[1][1] = c11 = in_c11_re + j*in_c11_im`

`alpha` and `beta` are real Q4.12 scalars from `in_alpha` and `in_beta`.

The Q4.12 constant `1.0` is the signed value `4096`, encoded on the port as `16'h1000`.

### Fixed-point arithmetic

Define:

- `qmul(x, y)`:
  1. interpret `x` and `y` as signed 16-bit integers
  2. compute signed integer product `p = x * y`
  3. arithmetic right shift `p_shift = p >> 12`
  4. wrap to signed 16-bit two's-complement

- `wrap16(x)`: wrap signed integer `x` to signed 16-bit two's-complement

All additions and subtractions in the algorithm are performed as signed integers followed by `wrap16` at the assignment point named below.

Complex conjugation:
- `conj(xr + j*xi) = xr - j*xi`, with the negated imaginary part wrapped by `wrap16`

Complex multiplication of `x = xr + j*xi` and `y = yr + j*yi`:
- `real(x*y) = wrap16(qmul(xr, yr) - qmul(xi, yi))`
- `imag(x*y) = wrap16(qmul(xr, yi) + qmul(xi, yr))`

Complex addition of `x` and `y`:
- `real(x+y) = wrap16(real(x) + real(y))`
- `imag(x+y) = wrap16(imag(x) + imag(y))`

Real self-inner-product term:
- `real(conj(x)*x) = wrap16(qmul(xr, xr) + qmul(xi, xi))`

Complex zero test:
- a complex value is zero iff both wrapped 16-bit signed components equal zero

### Triangle selection and output fill

`in_uplo` selects the stored triangle:
- `in_uplo = 1`: update and output only `(0,0)`, `(0,1)`, `(1,1)`
- `in_uplo = 0`: update and output only `(0,0)`, `(1,0)`, `(1,1)`

The non-stored output element is forced to complex zero:
- if `in_uplo = 1`, `out_c10_re = 0`, `out_c10_im = 0`
- if `in_uplo = 0`, `out_c01_re = 0`, `out_c01_im = 0`

The diagonal imaginary inputs `in_c00_im` and `in_c11_im` are ignored. The diagonal imaginary outputs are always zero:
- `out_c00_im = 0`
- `out_c11_im = 0`

No Hermitian mirroring is performed onto the non-stored triangle.

### Operation selection

`in_trans = 0` selects:
- `C := alpha * A * A^H + beta * C`

`in_trans = 1` selects:
- `C := alpha * A^H * A + beta * C`

The algorithm below is the contract. A reimplementation shall follow these exact operand orders and branch conditions.

### `in_trans = 0`, `in_uplo = 1`

1. Element `(0,0)`:
   - if `beta == 0`, set `acc00 = 0`
   - else set `acc00 = qmul(beta, in_c00_re)`
   - if `a00 != 0`, set `acc00 = wrap16(acc00 + qmul(alpha, real(conj(a00)*a00)))`
   - if `a01 != 0`, set `acc00 = wrap16(acc00 + qmul(alpha, real(conj(a01)*a01)))`
   - `out_c00_re = acc00`

2. Element `(0,1)`:
   - if `beta == 0`, set `acc01 = 0 + j*0`
   - else if `beta == 1.0`, set `acc01 = c01`
   - else set `acc01 = qmul(beta, in_c01_re) + j*qmul(beta, in_c01_im)`
   - if `a10 != 0`:
     - `temp = qmul(alpha, real(conj(a10))) + j*qmul(alpha, imag(conj(a10)))`
     - `prod = temp * a00`
     - `acc01 = acc01 + prod`
   - if `a11 != 0`:
     - `temp = qmul(alpha, real(conj(a11))) + j*qmul(alpha, imag(conj(a11)))`
     - `prod = temp * a01`
     - `acc01 = acc01 + prod`
   - `out_c01_re = real(acc01)`
   - `out_c01_im = imag(acc01)`

3. Element `(1,1)`:
   - if `beta == 0`, set `acc11 = 0`
   - else if `beta == 1.0`, set `acc11 = in_c11_re`
   - else set `acc11 = qmul(beta, in_c11_re)`
   - if `a10 != 0`, set `acc11 = wrap16(acc11 + qmul(alpha, real(conj(a10)*a10)))`
   - if `a11 != 0`, set `acc11 = wrap16(acc11 + qmul(alpha, real(conj(a11)*a11)))`
   - `out_c11_re = acc11`

### `in_trans = 0`, `in_uplo = 0`

1. Element `(0,0)`:
   - if `beta == 0`, set `acc00 = 0`
   - else if `beta == 1.0`, set `acc00 = in_c00_re`
   - else set `acc00 = qmul(beta, in_c00_re)`
   - if `a00 != 0`, set `acc00 = wrap16(acc00 + qmul(alpha, real(conj(a00)*a00)))`
   - if `a01 != 0`, set `acc00 = wrap16(acc00 + qmul(alpha, real(conj(a01)*a01)))`
   - `out_c00_re = acc00`

2. Element `(1,0)`:
   - if `beta == 0`, set `acc10 = 0 + j*0`
   - else if `beta == 1.0`, set `acc10 = c10`
   - else set `acc10 = qmul(beta, in_c10_re) + j*qmul(beta, in_c10_im)`
   - if `a00 != 0`:
     - `temp = qmul(alpha, real(conj(a00))) + j*qmul(alpha, imag(conj(a00)))`
     - `prod = temp * a10`
     - `acc10 = acc10 + prod`
   - if `a01 != 0`:
     - `temp = qmul(alpha, real(conj(a01))) + j*qmul(alpha, imag(conj(a01)))`
     - `prod = temp * a11`
     - `acc10 = acc10 + prod`
   - `out_c10_re = real(acc10)`
   - `out_c10_im = imag(acc10)`

3. Element `(1,1)`:
   - if `beta == 0`, set `acc11 = 0`
   - else if `beta == 1.0`, set `acc11 = in_c11_re`
   - else set `acc11 = qmul(beta, in_c11_re)`
   - if `a10 != 0`, set `acc11 = wrap16(acc11 + qmul(alpha, real(conj(a10)*a10)))`
   - if `a11 != 0`, set `acc11 = wrap16(acc11 + qmul(alpha, real(conj(a11)*a11)))`
   - `out_c11_re = acc11`

### `in_trans = 1`, `in_uplo = 1`

1. Compute:
   - `rtemp00 = wrap16(real(conj(a00)*a00) + real(conj(a10)*a10))`
   - `m0 = conj(a00) * a01`
   - `m1 = conj(a10) * a11`
   - `temp01 = m0 + m1`
   - `rtemp11 = wrap16(real(conj(a01)*a01) + real(conj(a11)*a11))`

2. Outputs:
   - if `beta == 0`, `out_c00_re = qmul(alpha, rtemp00)`, else `out_c00_re = wrap16(qmul(alpha, rtemp00) + qmul(beta, in_c00_re))`
   - let `s01 = qmul(alpha, real(temp01)) + j*qmul(alpha, imag(temp01))`
   - if `beta == 0`:
     - `out_c01_re = real(s01)`
     - `out_c01_im = imag(s01)`
   - else:
     - `out_c01_re = wrap16(real(s01) + qmul(beta, in_c01_re))`
     - `out_c01_im = wrap16(imag(s01) + qmul(beta, in_c01_im))`
   - if `beta == 0`, `out_c11_re = qmul(alpha, rtemp11)`, else `out_c11_re = wrap16(qmul(alpha, rtemp11) + qmul(beta, in_c11_re))`

### `in_trans = 1`, `in_uplo = 0`

1. Compute:
   - `rtemp00 = wrap16(real(conj(a00)*a00) + real(conj(a10)*a10))`
   - `m0 = conj(a01) * a00`
   - `m1 = conj(a11) * a10`
   - `temp10 = m0 + m1`
   - `rtemp11 = wrap16(real(conj(a01)*a01) + real(conj(a11)*a11))`

2. Outputs:
   - if `beta == 0`, `out_c00_re = qmul(alpha, rtemp00)`, else `out_c00_re = wrap16(qmul(alpha, rtemp00) + qmul(beta, in_c00_re))`
   - let `s10 = qmul(alpha, real(temp10)) + j*qmul(alpha, imag(temp10))`
   - if `beta == 0`:
     - `out_c10_re = real(s10)`
     - `out_c10_im = imag(s10)`
   - else:
     - `out_c10_re = wrap16(real(s10) + qmul(beta, in_c10_re))`
     - `out_c10_im = wrap16(imag(s10) + qmul(beta, in_c10_im))`
   - if `beta == 0`, `out_c11_re = qmul(alpha, rtemp11)`, else `out_c11_re = wrap16(qmul(alpha, rtemp11) + qmul(beta, in_c11_re))`

### Output encoding

Every output field is returned as a 16-bit unsigned integer bit pattern carrying the signed Q4.12 result.

## Reset Behavior

Reset is synchronous active-high. The module has no architectural state. After reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_c00_re = 16'h0000`
- `out_c00_im = 16'h0000`
- `out_c01_re = 16'h0000`
- `out_c01_im = 16'h0000`
- `out_c10_re = 16'h0000`
- `out_c10_im = 16'h0000`
- `out_c11_re = 16'h0000`
- `out_c11_im = 16'h0000`

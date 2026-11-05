# ComplexGemv4x4

ComplexGemv4x4 performs one complex signed-64-bit integer matrix-vector update on a fixed 4×4 matrix using the BLAS ZGEMV algorithm restricted to unit-stride contiguous vectors. For each accepted command, it computes either `y := alpha*A*x + beta*y`, `y := alpha*A**T*x + beta*y`, or `y := alpha*A**H*x + beta*y`, where `A` is a 4×4 complex matrix, `x` and `y` are length-4 complex vectors, `A**T` is transpose, and `A**H` is conjugate transpose. The kernel uses the same operation ordering as the reference BLAS routine: scale `y` by `beta` first, return early when `alpha = 0`, then accumulate matrix-vector products in column-major order.

## Interface

### RTL interface

```systemverilog
module complex_gemv4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_trans,
    input  logic [63:0]  in_alpha_re,
    input  logic [63:0]  in_alpha_im,
    input  logic [63:0]  in_beta_re,
    input  logic [63:0]  in_beta_im,

    input  logic [63:0]  in_a00_re,
    input  logic [63:0]  in_a00_im,
    input  logic [63:0]  in_a10_re,
    input  logic [63:0]  in_a10_im,
    input  logic [63:0]  in_a20_re,
    input  logic [63:0]  in_a20_im,
    input  logic [63:0]  in_a30_re,
    input  logic [63:0]  in_a30_im,
    input  logic [63:0]  in_a01_re,
    input  logic [63:0]  in_a01_im,
    input  logic [63:0]  in_a11_re,
    input  logic [63:0]  in_a11_im,
    input  logic [63:0]  in_a21_re,
    input  logic [63:0]  in_a21_im,
    input  logic [63:0]  in_a31_re,
    input  logic [63:0]  in_a31_im,
    input  logic [63:0]  in_a02_re,
    input  logic [63:0]  in_a02_im,
    input  logic [63:0]  in_a12_re,
    input  logic [63:0]  in_a12_im,
    input  logic [63:0]  in_a22_re,
    input  logic [63:0]  in_a22_im,
    input  logic [63:0]  in_a32_re,
    input  logic [63:0]  in_a32_im,
    input  logic [63:0]  in_a03_re,
    input  logic [63:0]  in_a03_im,
    input  logic [63:0]  in_a13_re,
    input  logic [63:0]  in_a13_im,
    input  logic [63:0]  in_a23_re,
    input  logic [63:0]  in_a23_im,
    input  logic [63:0]  in_a33_re,
    input  logic [63:0]  in_a33_im,

    input  logic [63:0]  in_x0_re,
    input  logic [63:0]  in_x0_im,
    input  logic [63:0]  in_x1_re,
    input  logic [63:0]  in_x1_im,
    input  logic [63:0]  in_x2_re,
    input  logic [63:0]  in_x2_im,
    input  logic [63:0]  in_x3_re,
    input  logic [63:0]  in_x3_im,

    input  logic [63:0]  in_y0_re,
    input  logic [63:0]  in_y0_im,
    input  logic [63:0]  in_y1_re,
    input  logic [63:0]  in_y1_im,
    input  logic [63:0]  in_y2_re,
    input  logic [63:0]  in_y2_im,
    input  logic [63:0]  in_y3_re,
    input  logic [63:0]  in_y3_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_y0_re,
    output logic [63:0]  out_y0_im,
    output logic [63:0]  out_y1_re,
    output logic [63:0]  out_y1_im,
    output logic [63:0]  out_y2_re,
    output logic [63:0]  out_y2_im,
    output logic [63:0]  out_y3_re,
    output logic [63:0]  out_y3_im
);
```

`in_trans` encoding:
- `2'b00`: normal, `A`
- `2'b01`: transpose, `A**T`
- `2'b10`: conjugate transpose, `A**H`
- `2'b11`: reserved; treated identically to `2'b00`

Each real or imaginary field is a signed 64-bit two's-complement integer. A complex number `(re, im)` represents `re + j*im`.

### Behavior interface

```python
class ComplexGemv4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_trans,
        in_alpha_re, in_alpha_im,
        in_beta_re, in_beta_im,
        in_a00_re, in_a00_im, in_a10_re, in_a10_im, in_a20_re, in_a20_im, in_a30_re, in_a30_im,
        in_a01_re, in_a01_im, in_a11_re, in_a11_im, in_a21_re, in_a21_im, in_a31_re, in_a31_im,
        in_a02_re, in_a02_im, in_a12_re, in_a12_im, in_a22_re, in_a22_im, in_a32_re, in_a32_im,
        in_a03_re, in_a03_im, in_a13_re, in_a13_im, in_a23_re, in_a23_im, in_a33_re, in_a33_im,
        in_x0_re, in_x0_im, in_x1_re, in_x1_im, in_x2_re, in_x2_im, in_x3_re, in_x3_im,
        in_y0_re, in_y0_im, in_y1_re, in_y1_im, in_y2_re, in_y2_im, in_y3_re, in_y3_im
    ):
        pass
```

Each `process` call represents one accepted matrix-vector command. Pass
`in_trans` as an unsigned 2-bit integer and all other arguments as unsigned
64-bit integers encoding signed two's-complement components. It always
returns one dictionary with exactly `out_y0_re`, `out_y0_im`, `out_y1_re`,
`out_y1_im`, `out_y2_re`, `out_y2_im`, `out_y3_re`, and `out_y3_im`, all
unsigned 64-bit encodings of the wrapped results. Include every field even
when alpha is zero or the result vector is zero; never return `None` or a
list. Matrix and input/output vectors are supplied anew on each call, not
retained internally. Construction and `reset()` have no numerical effect;
`reset()` returns `None`. Physical idle and stall cycles require no calls.

## Modules

- `complex_gemv4x4`: top-level kernel implementing one 4×4 complex matrix-vector update.

## Functional Behavior

All input and output fields are 64-bit signed integers interpreted as exact mathematical integers. Every complex multiply and add is performed over mathematical integers, and each final real and imaginary result is reduced modulo `2^64` onto the corresponding output port. This is equivalent to two's-complement wraparound on the final outputs. There is no intermediate saturation.

Define signed interpretation of any 64-bit port `p` as:
- if `p[63] = 0`, value is `p`
- if `p[63] = 1`, value is `p - 2^64`

Define complex arithmetic for `(ar, ai)` and `(br, bi)`:
- complex multiply:
  - real part = `ar*br - ai*bi`
  - imaginary part = `ar*bi + ai*br`
- complex add:
  - real part = sum of real parts
  - imaginary part = sum of imaginary parts
- complex conjugate:
  - `conj(ar, ai) = (ar, -ai)`

Let:
- `alpha = (alpha_re, alpha_im)`
- `beta  = (beta_re, beta_im)`
- `x[k]` for `k=0..3`
- `y_in[k]` for `k=0..3`
- `A[i][j]` for rows `i=0..3`, columns `j=0..3`

The matrix ports are column-major by name:
- column 0: `A[0][0]=a00`, `A[1][0]=a10`, `A[2][0]=a20`, `A[3][0]=a30`
- column 1: `A[0][1]=a01`, `A[1][1]=a11`, `A[2][1]=a21`, `A[3][1]=a31`
- column 2: `A[0][2]=a02`, `A[1][2]=a12`, `A[2][2]=a22`, `A[3][2]=a32`
- column 3: `A[0][3]=a03`, `A[1][3]=a13`, `A[2][3]=a23`, `A[3][3]=a33`

The operation follows these steps.

1. Decode operation mode from `in_trans`.
   - `00`: normal mode.
   - `01`: transpose mode.
   - `10`: conjugate-transpose mode.
   - `11`: normal mode.

2. Form the initial output working vector `y_work` by scaling `y_in` by `beta`.
   - If `beta = (1, 0)`, `y_work[k] = y_in[k]`.
   - Else if `beta = (0, 0)`, `y_work[k] = (0, 0)`.
   - Else `y_work[k] = beta * y_in[k]`.
   This matches the BLAS ordering where `y := beta*y` is completed before any `alpha*A*x` contribution.

3. If `alpha = (0, 0)`, no matrix-vector term is added. The final outputs are `y_work`.

4. Otherwise add the matrix-vector product in BLAS loop order.

   Normal mode (`in_trans = 00` or `11`):
   - For each column `j = 0,1,2,3` in increasing order:
     - `temp = alpha * x[j]`
     - For each row `i = 0,1,2,3` in increasing order:
       - `y_work[i] = y_work[i] + temp * A[i][j]`

   Transpose mode (`in_trans = 01`):
   - For each output index `j = 0,1,2,3` in increasing order:
     - `temp = (0, 0)`
     - For each row index `i = 0,1,2,3` in increasing order:
       - `temp = temp + A[i][j] * x[i]`
     - `y_work[j] = y_work[j] + alpha * temp`

   Conjugate-transpose mode (`in_trans = 10`):
   - For each output index `j = 0,1,2,3` in increasing order:
     - `temp = (0, 0)`
     - For each row index `i = 0,1,2,3` in increasing order:
       - `temp = temp + conj(A[i][j]) * x[i]`
     - `y_work[j] = y_work[j] + alpha * temp`

5. Output mapping.
   - `out_y0_*` is `y_work[0]`
   - `out_y1_*` is `y_work[1]`
   - `out_y2_*` is `y_work[2]`
   - `out_y3_*` is `y_work[3]`

6. Output truncation.
   - Each final real and imaginary component is converted to a 64-bit unsigned port value by taking the low 64 bits of the mathematical signed result.
   - The port therefore carries the two's-complement bit pattern of the wrapped signed result.

There are no invalid-input traps, no exceptional codes, and no side effects beyond producing the output vector.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst = 1`, the module returns to its idle state.

Architectural state:
- The module has no algorithmic state between transactions.

Output ports after reset and while no output transaction is pending:
- `out_valid = 0`
- `out_y0_re = 0`
- `out_y0_im = 0`
- `out_y1_re = 0`
- `out_y1_im = 0`
- `out_y2_re = 0`
- `out_y2_im = 0`
- `out_y3_re = 0`
- `out_y3_im = 0`

Input readiness after reset:
- `in_ready = 1`

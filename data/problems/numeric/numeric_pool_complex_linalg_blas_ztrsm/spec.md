# ComplexTriangularSolve2x2

ComplexTriangularSolve2x2 solves one 2×2 complex triangular-system transaction using the BLAS TRSM update order specialized to fixed matrix size. The kernel overwrites a 2×2 right-hand-side matrix `B` with the solution matrix `X` of either `op(A) * X = alpha * B` or `X * op(A) = alpha * B`, where `A` is a 2×2 upper or lower triangular complex matrix, `alpha` is a complex scalar, and `op(A)` is `A`, `A^T`, or `A^H`. The arithmetic is exact integer arithmetic on signed real and imaginary parts; complex multiply, subtract, and divide follow the same operation ordering as the reference triangular solve, including unit-diagonal handling and conjugation for the Hermitian case.

## Interface

### RTL interface

```systemverilog
module complex_triangular_solve_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,      // 0 = left  (op(A) * X = alpha * B), 1 = right (X * op(A) = alpha * B)
    input  logic        in_uplo,      // 0 = upper, 1 = lower
    input  logic [1:0]  in_transa,    // 0 = A, 1 = A^T, 2 = A^H
    input  logic        in_diag,      // 0 = unit diagonal, 1 = non-unit diagonal

    input  logic signed [15:0] in_alpha_re,
    input  logic signed [15:0] in_alpha_im,

    input  logic signed [15:0] in_a00_re,
    input  logic signed [15:0] in_a00_im,
    input  logic signed [15:0] in_a01_re,
    input  logic signed [15:0] in_a01_im,
    input  logic signed [15:0] in_a10_re,
    input  logic signed [15:0] in_a10_im,
    input  logic signed [15:0] in_a11_re,
    input  logic signed [15:0] in_a11_im,

    input  logic signed [15:0] in_b00_re,
    input  logic signed [15:0] in_b00_im,
    input  logic signed [15:0] in_b01_re,
    input  logic signed [15:0] in_b01_im,
    input  logic signed [15:0] in_b10_re,
    input  logic signed [15:0] in_b10_im,
    input  logic signed [15:0] in_b11_re,
    input  logic signed [15:0] in_b11_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [15:0] out_b00_re,
    output logic signed [15:0] out_b00_im,
    output logic signed [15:0] out_b01_re,
    output logic signed [15:0] out_b01_im,
    output logic signed [15:0] out_b10_re,
    output logic signed [15:0] out_b10_im,
    output logic signed [15:0] out_b11_re,
    output logic signed [15:0] out_b11_im
);
```

### Behavior interface

```python
class ComplexTriangularSolve2x2:
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
        in_alpha_re,
        in_alpha_im,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_b00_re,
        in_b00_im,
        in_b01_re,
        in_b01_im,
        in_b10_re,
        in_b10_im,
        in_b11_re,
        in_b11_im,
    ):
        pass
```

One `process(...)` call represents one accepted matrix transaction and returns
one dictionary with exactly `out_b00_re`, `out_b00_im`, `out_b01_re`,
`out_b01_im`, `out_b10_re`, `out_b10_im`, `out_b11_re`, and `out_b11_im`.
Component values represent the corresponding 16-bit two's-complement ports,
not Python complex numbers or a nested matrix. Signed integers and their
unsigned 16-bit encodings denote the same port value. Control arguments use
their declared port widths. A zero matrix still returns all eight fields.
`reset()` returns `None`; no matrix history affects subsequent requests.
Calls do not represent pipeline or handshake wait cycles.

## Modules

- `complex_triangular_solve_2x2`: top-level single-transaction triangular solve kernel.

## Functional Behavior


All real and imaginary input ports are signed 16-bit two's-complement integers. Each complex number is formed as `(re, im)` from its component ports. Internal arithmetic is defined over mathematical signed integers with no intermediate truncation. Each output component is converted to signed 16-bit two's-complement by wrapping modulo `2^16`.

The input matrices are

- `alpha = in_alpha_re + j*in_alpha_im`
- `A = [[a00, a01], [a10, a11]]`
- `B = [[b00, b01], [b10, b11]]`

with each `aXY` and `bXY` formed from the corresponding real and imaginary ports.

Control encoding:

- `in_side = 0`: left solve, solve `op(A) * X = alpha * B`
- `in_side = 1`: right solve, solve `X * op(A) = alpha * B`
- `in_uplo = 0`: `A` is upper triangular
- `in_uplo = 1`: `A` is lower triangular
- `in_transa = 0`: `op(A) = A`
- `in_transa = 1`: `op(A) = A^T`
- `in_transa = 2`: `op(A) = A^H`
- `in_diag = 0`: unit diagonal, diagonal entries of `A` are treated as exactly `1 + j*0` and the provided diagonal ports are ignored
- `in_diag = 1`: non-unit diagonal, diagonal entries of `A` are read from the input ports

`A^H` is conjugate transpose. Conjugation changes `(x, y)` to `(x, -y)`.

Complex operations:

- Addition/subtraction are component-wise.
- Multiplication: `(ar + j ai) * (br + j bi) = (ar*br - ai*bi) + j(ar*bi + ai*br)`.
- Division by a complex denominator `d = dr + j di` is
  - numerator `n = nr + j ni`
  - denominator magnitude `den = dr*dr + di*di`
  - quotient real part `q_re = trunc_toward_zero((nr*dr + ni*di) / den)`
  - quotient imaginary part `q_im = trunc_toward_zero((ni*dr - nr*di) / den)`

`trunc_toward_zero` is integer division rounded toward zero. If a non-unit diagonal division encounters `den = 0`, the quotient is defined as `0 + j*0`.

Zero tests used by the algorithm compare both components to zero exactly.

If `alpha = 0 + j*0`, the output matrix is all zeros and no element of `A` affects the result.

Otherwise the result is computed by the same in-place overwrite order as a 2×2 TRSM update. The formulas below define the exact operation order.

Let `Bwork` denote a mutable 2×2 complex matrix initialized from the input `B`.

### 1. Left solve, no transpose: `in_side = 0`, `in_transa = 0`

This computes `Bwork := alpha * inv(A) * Bwork`.

#### 1a. Upper triangular: `in_uplo = 0`

For each column independently:

For column 0:
1. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.
2. `k = 1`:
   - If `Bwork[1][0] != 0`, then if `in_diag = 1`, divide `Bwork[1][0]` by `a11`.
   - Update `Bwork[0][0] := Bwork[0][0] - Bwork[1][0] * a01`.
3. `k = 0`:
   - If `Bwork[0][0] != 0`, then if `in_diag = 1`, divide `Bwork[0][0]` by `a00`.

For column 1:
1. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.
2. `k = 1`:
   - If `Bwork[1][1] != 0`, then if `in_diag = 1`, divide `Bwork[1][1]` by `a11`.
   - Update `Bwork[0][1] := Bwork[0][1] - Bwork[1][1] * a01`.
3. `k = 0`:
   - If `Bwork[0][1] != 0`, then if `in_diag = 1`, divide `Bwork[0][1]` by `a00`.

The strictly lower entry `a10` is ignored.

#### 1b. Lower triangular: `in_uplo = 1`

For each column independently:

For column 0:
1. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.
2. `k = 0`:
   - If `Bwork[0][0] != 0`, then if `in_diag = 1`, divide `Bwork[0][0]` by `a00`.
   - Update `Bwork[1][0] := Bwork[1][0] - Bwork[0][0] * a10`.
3. `k = 1`:
   - If `Bwork[1][0] != 0`, then if `in_diag = 1`, divide `Bwork[1][0]` by `a11`.

For column 1:
1. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.
2. `k = 0`:
   - If `Bwork[0][1] != 0`, then if `in_diag = 1`, divide `Bwork[0][1]` by `a00`.
   - Update `Bwork[1][1] := Bwork[1][1] - Bwork[0][1] * a10`.
3. `k = 1`:
   - If `Bwork[1][1] != 0`, then if `in_diag = 1`, divide `Bwork[1][1]` by `a11`.

The strictly upper entry `a01` is ignored.

### 2. Left solve, transpose or conjugate-transpose: `in_side = 0`, `in_transa = 1 or 2`

This computes `Bwork := alpha * inv(A^T) * Bwork` when `in_transa = 1`, or `Bwork := alpha * inv(A^H) * Bwork` when `in_transa = 2`.

For `in_transa = 1`, matrix elements are used as stored. For `in_transa = 2`, every referenced matrix element is conjugated before use.

#### 2a. Upper triangular: `in_uplo = 0`

For each column independently:

For row 0:
1. `temp := alpha * Bwork[0][j]`.
2. If `in_diag = 1`, divide `temp` by `op_diag(a00)`.
3. Write `Bwork[0][j] := temp`.

For row 1:
1. `temp := alpha * Bwork[1][j]`.
2. `temp := temp - op_off(a01) * Bwork[0][j]`.
3. If `in_diag = 1`, divide `temp` by `op_diag(a11)`.
4. Write `Bwork[1][j] := temp`.

Here `op_off(a01)` is `a01` for transpose and `conj(a01)` for conjugate-transpose; `op_diag` follows the same rule on the diagonal.

The entries `a10` are ignored.

#### 2b. Lower triangular: `in_uplo = 1`

For each column independently:

For row 1:
1. `temp := alpha * Bwork[1][j]`.
2. If `in_diag = 1`, divide `temp` by `op_diag(a11)`.
3. Write `Bwork[1][j] := temp`.

For row 0:
1. `temp := alpha * Bwork[0][j]`.
2. `temp := temp - op_off(a10) * Bwork[1][j]`.
3. If `in_diag = 1`, divide `temp` by `op_diag(a00)`.
4. Write `Bwork[0][j] := temp`.

Here `op_off(a10)` is `a10` for transpose and `conj(a10)` for conjugate-transpose. The entries `a01` are ignored.

### 3. Right solve, no transpose: `in_side = 1`, `in_transa = 0`

This computes `Bwork := alpha * Bwork * inv(A)`.

#### 3a. Upper triangular: `in_uplo = 0`

Column 0:
1. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.
2. If `in_diag = 1`, divide `Bwork[0][0]` and `Bwork[1][0]` directly by `a00`, rounding each quotient toward zero.

Column 1:
1. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.
2. If `a01 != 0`, update
   - `Bwork[0][1] := Bwork[0][1] - a01 * Bwork[0][0]`
   - `Bwork[1][1] := Bwork[1][1] - a01 * Bwork[1][0]`
3. If `in_diag = 1`, divide `Bwork[0][1]` and `Bwork[1][1]` directly by `a11`, rounding each quotient toward zero.

The entry `a10` is ignored.

#### 3b. Lower triangular: `in_uplo = 1`

Column 1:
1. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.
2. If `in_diag = 1`, divide `Bwork[0][1]` and `Bwork[1][1]` directly by `a11`, rounding each quotient toward zero.

Column 0:
1. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.
2. If `a10 != 0`, update
   - `Bwork[0][0] := Bwork[0][0] - a10 * Bwork[0][1]`
   - `Bwork[1][0] := Bwork[1][0] - a10 * Bwork[1][1]`
3. If `in_diag = 1`, divide `Bwork[0][0]` and `Bwork[1][0]` directly by `a00`, rounding each quotient toward zero.

The entry `a01` is ignored.

### 4. Right solve, transpose or conjugate-transpose: `in_side = 1`, `in_transa = 1 or 2`

This computes `Bwork := alpha * Bwork * inv(A^T)` when `in_transa = 1`, or `Bwork := alpha * Bwork * inv(A^H)` when `in_transa = 2`.

For `in_transa = 1`, matrix elements are used as stored. For `in_transa = 2`, every referenced matrix element is conjugated before use.

#### 4a. Upper triangular: `in_uplo = 0`

Process `k = 1` then `k = 0`.

For `k = 1`:
1. If `in_diag = 1`, divide `Bwork[0][1]` and `Bwork[1][1]` directly by `op_diag(a11)`, rounding each quotient toward zero.
2. If `op_off(a01) != 0`, update
   - `Bwork[0][0] := Bwork[0][0] - op_off(a01) * Bwork[0][1]`
   - `Bwork[1][0] := Bwork[1][0] - op_off(a01) * Bwork[1][1]`
3. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.

For `k = 0`:
1. If `in_diag = 1`, divide `Bwork[0][0]` and `Bwork[1][0]` directly by `op_diag(a00)`, rounding each quotient toward zero.
2. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.

`op_off(a01)` is `a01` for transpose and `conj(a01)` for conjugate-transpose. The entry `a10` is ignored.

#### 4b. Lower triangular: `in_uplo = 1`

Process `k = 0` then `k = 1`.

For `k = 0`:
1. If `in_diag = 1`, divide `Bwork[0][0]` and `Bwork[1][0]` directly by `op_diag(a00)`, rounding each quotient toward zero.
2. If `op_off(a10) != 0`, update
   - `Bwork[0][1] := Bwork[0][1] - op_off(a10) * Bwork[0][0]`
   - `Bwork[1][1] := Bwork[1][1] - op_off(a10) * Bwork[1][0]`
3. If `alpha != 1 + j*0`, scale `Bwork[0][0]` and `Bwork[1][0]` by `alpha`.

For `k = 1`:
1. If `in_diag = 1`, divide `Bwork[0][1]` and `Bwork[1][1]` directly by `op_diag(a11)`, rounding each quotient toward zero.
2. If `alpha != 1 + j*0`, scale `Bwork[0][1]` and `Bwork[1][1]` by `alpha`.

`op_off(a10)` is `a10` for transpose and `conj(a10)` for conjugate-transpose. The entry `a01` is ignored.

At the end of the selected case, `Bwork` is returned on the output ports:

- `out_b00_* = Bwork[0][0]`
- `out_b01_* = Bwork[0][1]`
- `out_b10_* = Bwork[1][0]`
- `out_b11_* = Bwork[1][1]`

Any `in_transa` value other than `0`, `1`, or `2` is treated as `2`.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst = 1`, the module returns to its idle state.

Architectural state consists only of the output holding registers and the output-valid flag. After reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_b00_re = 0`
- `out_b00_im = 0`
- `out_b01_re = 0`
- `out_b01_im = 0`
- `out_b10_re = 0`
- `out_b10_im = 0`
- `out_b11_re = 0`
- `out_b11_im = 0`

No other architectural state is retained across transactions.

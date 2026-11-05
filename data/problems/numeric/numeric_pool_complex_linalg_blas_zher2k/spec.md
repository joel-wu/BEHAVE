# Hermitian Rank-2k Update Tile

This module performs one complete Hermitian rank-2k update on a fixed 2×2 complex matrix tile using the BLAS ZHER2K algorithm specialized to `N = 2`. It computes either `C := alpha*A*B^H + conj(alpha)*B*A^H + beta*C` or `C := alpha*A^H*B + conj(alpha)*B^H*A + beta*C`, with complex values represented as signed 32-bit real and imaginary parts. The implementation stores only one triangular half of the Hermitian output matrix, forces both diagonal imaginary parts to zero on output, and accumulates across `K` streaming input beats before emitting one updated tile.

## Interface

### RTL interface

```systemverilog
module hermitian_rank2k_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic         in_last,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic signed [31:0] in_alpha_re,
    input  logic signed [31:0] in_alpha_im,
    input  logic signed [31:0] in_beta,
    input  logic signed [31:0] in_c00_re,
    input  logic signed [31:0] in_c00_im,
    input  logic signed [31:0] in_c01_re,
    input  logic signed [31:0] in_c01_im,
    input  logic signed [31:0] in_c10_re,
    input  logic signed [31:0] in_c10_im,
    input  logic signed [31:0] in_c11_re,
    input  logic signed [31:0] in_c11_im,
    input  logic signed [31:0] in_a0_re,
    input  logic signed [31:0] in_a0_im,
    input  logic signed [31:0] in_a1_re,
    input  logic signed [31:0] in_a1_im,
    input  logic signed [31:0] in_b0_re,
    input  logic signed [31:0] in_b0_im,
    input  logic signed [31:0] in_b1_re,
    input  logic signed [31:0] in_b1_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_uplo,
    output logic signed [31:0] out_c00_re,
    output logic signed [31:0] out_c00_im,
    output logic signed [31:0] out_c01_re,
    output logic signed [31:0] out_c01_im,
    output logic signed [31:0] out_c10_re,
    output logic signed [31:0] out_c10_im,
    output logic signed [31:0] out_c11_re,
    output logic signed [31:0] out_c11_im
);
```

### Behavior interface

```python
class HermitianRank2kTile:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_start,
        in_last,
        in_uplo,
        in_trans,
        in_alpha_re,
        in_alpha_im,
        in_beta,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
        in_a0_re,
        in_a0_im,
        in_a1_re,
        in_a1_im,
        in_b0_re,
        in_b0_im,
        in_b1_re,
        in_b1_im,
    ):
        pass
```

The return dictionary keys are:
`out_uplo`, `out_c00_re`, `out_c00_im`, `out_c01_re`, `out_c01_im`, `out_c10_re`, `out_c10_im`, `out_c11_re`, `out_c11_im`.

Keep one instance across the input sequence and call `process()` once per
accepted slice. Arguments encode the declared port values as Python integers;
the four one-bit controls are `0` or `1`. A call with `in_last=0` updates state
and returns `None`. A call with `in_last=1` returns exactly one dictionary with
the nine keys above, after including that slice. `out_uplo` is `0` or `1`;
matrix components are Python integer encodings of the low 32 result bits,
interpreted as two's-complement values. Zero components remain present, and
both diagonal imaginary fields are zero. Do not return one record per matrix
element or make extra calls for latency and stalls. `reset()` returns `None`
and restores the architectural state listed below.

## Modules

- `hermitian_rank2k_tile`: top-level streaming Hermitian rank-2k tile engine for a fixed 2×2 output matrix.
- No helper submodules are required by the contract.

## Functional Behavior

All scalar ports are integer two's-complement values. Each complex number is represented by one signed 32-bit real part and one signed 32-bit imaginary part. `beta` is real-only and uses one signed 32-bit port.

One accepted input transaction supplies one `L` slice of the matrices `A` and `B`. The module accumulates those slices across multiple accepted input transactions. A transaction with `in_start = 1` starts a new operation and loads the control fields and the initial matrix `C`. A transaction with `in_last = 1` is the final slice of that operation and causes one output transaction to be emitted after that input is accepted. The sequence length is therefore the number of accepted input transactions from a beat with `in_start = 1` through the beat with `in_last = 1`, inclusive. `in_start` and `in_last` may both be `1` for a single-slice operation.

The control field encoding is:
- `in_uplo = 1`: only the upper triangle of `C` is referenced and updated.
- `in_uplo = 0`: only the lower triangle of `C` is referenced and updated.
- `in_trans = 0`: operation form `C := alpha*A*B^H + conj(alpha)*B*A^H + beta*C`.
- `in_trans = 1`: operation form `C := alpha*A^H*B + conj(alpha)*B^H*A + beta*C`.

The fixed output order is matrix indices `(0,0)`, `(0,1)`, `(1,0)`, `(1,1)`.

A complex value `x = xr + j*xi` uses the following arithmetic:
- `conj(x) = xr - j*xi`
- `x + y = (xr + yr) + j*(xi + yi)`
- `x * y = (xr*yr - xi*yi) + j*(xr*yi + xi*yr)`

All intermediate arithmetic is exact unbounded signed integer arithmetic in the specification. Each returned output field is truncated to signed 32-bit two's-complement by keeping the least-significant 32 bits.

A diagonal Hermitian element is always real on output. Therefore:
- `out_c00_im = 0`
- `out_c11_im = 0`

### Operation state initialized by `in_start`

On the accepted input transaction with `in_start = 1`, the module captures:
- `uplo := in_uplo`
- `trans := in_trans`
- `alpha := in_alpha_re + j*in_alpha_im`
- `alpha_conj := in_alpha_re - j*in_alpha_im`
- `beta := in_beta`

It also loads the initial 2×2 matrix state from the `in_c**_*` ports, with diagonal imaginary parts forced to zero immediately:
- `c00 := (in_c00_re) + j*0`
- `c01 := (in_c01_re) + j*(in_c01_im)`
- `c10 := (in_c10_re) + j*(in_c10_im)`
- `c11 := (in_c11_re) + j*0`

Only the selected triangle is architecturally referenced:
- if `uplo = 1`, only `c00`, `c01`, and `c11` participate in the update;
- if `uplo = 0`, only `c00`, `c10`, and `c11` participate in the update.

The opposite strictly off-diagonal element is ignored during accumulation and reconstructed on output as the complex conjugate of the stored element.

### Per-input slice interpretation

For each accepted input beat, define the four complex slice values:
- `a0 := in_a0_re + j*in_a0_im`
- `a1 := in_a1_re + j*in_a1_im`
- `b0 := in_b0_re + j*in_b0_im`
- `b1 := in_b1_re + j*in_b1_im`

If `trans = 0`, this beat represents one column `L` of `A` and `B`:
- `A(0,L) = a0`
- `A(1,L) = a1`
- `B(0,L) = b0`
- `B(1,L) = b1`

If `trans = 1`, this beat represents one row `L` of `A` and `B`:
- `A(L,0) = a0`
- `A(L,1) = a1`
- `B(L,0) = b0`
- `B(L,1) = b1`

### First-beat scaling by `beta`

The BLAS algorithm applies the `beta*C` term once per output element before the sum over `L`. In this streamed tile kernel, that scaling is performed only on the accepted input beat with `in_start = 1`.

For `uplo = 1`:
- if `beta = 0`, set `c00 = 0 + j*0`, `c01 = 0 + j*0`, `c11 = 0 + j*0`;
- else if `beta != 1`, set `c00 = (beta * real(c00)) + j*0`, `c01 = beta * c01`, `c11 = (beta * real(c11)) + j*0`;
- else set `c00 = real(c00) + j*0`, `c11 = real(c11) + j*0`, and leave `c01` unchanged.

For `uplo = 0`:
- if `beta = 0`, set `c00 = 0 + j*0`, `c10 = 0 + j*0`, `c11 = 0 + j*0`;
- else if `beta != 1`, set `c00 = (beta * real(c00)) + j*0`, `c10 = beta * c10`, `c11 = (beta * real(c11)) + j*0`;
- else set `c00 = real(c00) + j*0`, `c11 = real(c11) + j*0`, and leave `c10` unchanged.

### Accumulation for `trans = 0`

For each accepted input beat with `trans = 0`, define:
- `temp1_j = alpha * conj(bj)`
- `temp2_j = conj(alpha * aj) = alpha_conj * conj(aj)`

For `j = 0`, using `(aj, bj) = (a0, b0)`:
- if `a0 != 0 + j*0` or `b0 != 0 + j*0`, then:
  - update diagonal `c00 := real(c00) + real(a0*temp1_0 + b0*temp2_0) + j*0`
  - if `uplo = 0`, update `c10 := c10 + a1*temp1_0 + b1*temp2_0`
  - if `uplo = 1`, no off-diagonal update for `j = 0`

For `j = 1`, using `(aj, bj) = (a1, b1)`:
- if `a1 != 0 + j*0` or `b1 != 0 + j*0`, then:
  - update diagonal `c11 := real(c11) + real(a1*temp1_1 + b1*temp2_1) + j*0`
  - if `uplo = 1`, update `c01 := c01 + a0*temp1_1 + b0*temp2_1`
  - if `uplo = 0`, no off-diagonal update for `j = 1`

Equivalently, for `trans = 0`, the updated stored triangle after all beats is the BLAS rank-2k sum restricted to the selected triangle:
- upper:
  - `c00 := beta*c00 + sum_L real(alpha*A(0,L)*conj(B(0,L)) + conj(alpha)*B(0,L)*conj(A(0,L)))`
  - `c01 := beta*c01 + sum_L (alpha*A(0,L)*conj(B(1,L)) + conj(alpha)*B(0,L)*conj(A(1,L)))`
  - `c11 := beta*c11 + sum_L real(alpha*A(1,L)*conj(B(1,L)) + conj(alpha)*B(1,L)*conj(A(1,L)))`
- lower:
  - `c00 := beta*c00 + sum_L real(alpha*A(0,L)*conj(B(0,L)) + conj(alpha)*B(0,L)*conj(A(0,L)))`
  - `c10 := beta*c10 + sum_L (alpha*A(1,L)*conj(B(0,L)) + conj(alpha)*B(1,L)*conj(A(0,L)))`
  - `c11 := beta*c11 + sum_L real(alpha*A(1,L)*conj(B(1,L)) + conj(alpha)*B(1,L)*conj(A(1,L)))`

### Accumulation for `trans = 1`

For each accepted input beat with `trans = 1`, define:
- `aL0 = a0`, `aL1 = a1`
- `bL0 = b0`, `bL1 = b1`

For `uplo = 1`, accumulate:
- `c00 := c00 + (alpha*conj(aL0)*bL0 + alpha_conj*conj(bL0)*aL0)`
- `c01 := c01 + (alpha*conj(aL0)*bL1 + alpha_conj*conj(bL0)*aL1)`
- `c11 := c11 + (alpha*conj(aL1)*bL1 + alpha_conj*conj(bL1)*aL1)`

For `uplo = 0`, accumulate:
- `c00 := c00 + (alpha*conj(aL0)*bL0 + alpha_conj*conj(bL0)*aL0)`
- `c10 := c10 + (alpha*conj(aL1)*bL0 + alpha_conj*conj(bL1)*aL0)`
- `c11 := c11 + (alpha*conj(aL1)*bL1 + alpha_conj*conj(bL1)*aL1)`

After every such accumulation step, both diagonals are forced real:
- `c00 := real(c00) + j*0`
- `c11 := real(c11) + j*0`

This exactly matches the BLAS reference algorithm for `TRANS = 'C'` restricted to the selected triangle.

### Output formation on `in_last`

When an accepted input beat has `in_last = 1`, the module emits one output transaction containing the completed 2×2 Hermitian matrix.

If `uplo = 1`:
- `out_uplo = 1`
- `out_c00 = c00`
- `out_c01 = c01`
- `out_c10 = conj(c01)`
- `out_c11 = c11`

If `uplo = 0`:
- `out_uplo = 0`
- `out_c00 = c00`
- `out_c10 = c10`
- `out_c01 = conj(c10)`
- `out_c11 = c11`

Diagonal imaginary outputs are zero regardless of the input diagonal imaginary fields.

If a sequence has one beat with both `in_start = 1` and `in_last = 1`, the module performs the `beta` scaling, the single-slice accumulation, and the output emission for that beat.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst = 1`, the architectural state is set to:
- `uplo = 0`
- `trans = 0`
- `alpha = 0 + j*0`
- `alpha_conj = 0 + j*0`
- `beta = 0`
- `c00 = 0 + j*0`
- `c01 = 0 + j*0`
- `c10 = 0 + j*0`
- `c11 = 0 + j*0`

After reset and before the first accepted input transaction:
- `in_ready = 1`
- `out_valid = 0`
- output payload ports may hold zero:
  - `out_uplo = 0`
  - `out_c00_re = 0`
  - `out_c00_im = 0`
  - `out_c01_re = 0`
  - `out_c01_im = 0`
  - `out_c10_re = 0`
  - `out_c10_im = 0`
  - `out_c11_re = 0`
  - `out_c11_im = 0`

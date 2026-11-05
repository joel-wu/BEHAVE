# BlockReflectorApply4x4

This module applies one 2-reflector real block reflector to one signed fixed-point 4×4 matrix transaction. It is a concrete fixed-size kernel derived from the blocked reflector update in DLARFB with `M = N = 4` and `K = 2`. One accepted input transaction provides the mode bits `side`, `trans`, `direct`, and `storev`, the reflector data `V` and `T`, and the input matrix `C`. One output transaction returns the updated matrix `C'` computed by the same branch structure as DLARFB: build an intermediate 4×2 workspace `W`, post-multiply it by `T` or `T^T`, then subtract the corresponding rank-2 update from `C`. Arithmetic is signed two's-complement with modulo-2^16 wrap after every scalar add, subtract, and multiply-accumulate that corresponds to a BLAS operation in the fixed-size expansion.

## Interface

### RTL interface

```systemverilog
module block_reflector_apply4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_trans,
    input  logic         in_direct,
    input  logic         in_storev,

    input  logic [15:0]  in_v00,
    input  logic [15:0]  in_v01,
    input  logic [15:0]  in_v10,
    input  logic [15:0]  in_v11,
    input  logic [15:0]  in_v20,
    input  logic [15:0]  in_v21,
    input  logic [15:0]  in_v30,
    input  logic [15:0]  in_v31,

    input  logic [15:0]  in_t00,
    input  logic [15:0]  in_t01,
    input  logic [15:0]  in_t10,
    input  logic [15:0]  in_t11,

    input  logic [15:0]  in_c00,
    input  logic [15:0]  in_c01,
    input  logic [15:0]  in_c02,
    input  logic [15:0]  in_c03,
    input  logic [15:0]  in_c10,
    input  logic [15:0]  in_c11,
    input  logic [15:0]  in_c12,
    input  logic [15:0]  in_c13,
    input  logic [15:0]  in_c20,
    input  logic [15:0]  in_c21,
    input  logic [15:0]  in_c22,
    input  logic [15:0]  in_c23,
    input  logic [15:0]  in_c30,
    input  logic [15:0]  in_c31,
    input  logic [15:0]  in_c32,
    input  logic [15:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00,
    output logic [15:0]  out_c01,
    output logic [15:0]  out_c02,
    output logic [15:0]  out_c03,
    output logic [15:0]  out_c10,
    output logic [15:0]  out_c11,
    output logic [15:0]  out_c12,
    output logic [15:0]  out_c13,
    output logic [15:0]  out_c20,
    output logic [15:0]  out_c21,
    output logic [15:0]  out_c22,
    output logic [15:0]  out_c23,
    output logic [15:0]  out_c30,
    output logic [15:0]  out_c31,
    output logic [15:0]  out_c32,
    output logic [15:0]  out_c33
);
```

Bit encodings of the mode inputs:
- `in_side = 0`: `SIDE = 'L'`; `in_side = 1`: `SIDE = 'R'`
- `in_trans = 0`: `TRANS = 'N'`; `in_trans = 1`: `TRANS = 'T'`
- `in_direct = 0`: `DIRECT = 'F'`; `in_direct = 1`: `DIRECT = 'B'`
- `in_storev = 0`: `STOREV = 'C'`; `in_storev = 1`: `STOREV = 'R'`

`in_v**`, `in_t**`, `in_c**`, and `out_c**` are signed 16-bit two's-complement scalars transported as raw 16-bit bit patterns.

### Behavior interface

```python
class BlockReflectorApply4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_trans,
        in_direct,
        in_storev,
        in_v00,
        in_v01,
        in_v10,
        in_v11,
        in_v20,
        in_v21,
        in_v30,
        in_v31,
        in_t00,
        in_t01,
        in_t10,
        in_t11,
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

`process(...)` returns one dict per accepted input transaction. The dictionary contains the sixteen matrix fields listed below.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c10`, `out_c11`, `out_c12`,
  `out_c13`, `out_c20`, `out_c21`, `out_c22`, `out_c23`, `out_c30`, `out_c31`,
  `out_c32`, `out_c33`: 16.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `block_reflector_apply4x4`: top-level block reflector application kernel.

## Functional Behavior

The kernel is fixed to `M = 4`, `N = 4`, `K = 2`. Every accepted input transaction produces exactly one output transaction.

All input matrix elements are interpreted as signed 16-bit integers. Every scalar multiply uses signed 16×16 multiplication. Every scalar sum, subtraction, and dot-product accumulation is reduced modulo `2^16` after each complete scalar result is formed. The returned outputs are the resulting 16-bit two's-complement bit patterns.

Define:
- `s16(x)`: interpret 16-bit pattern `x` as signed in `[-32768, 32767]`
- `wrap16(x)`: reduce integer `x` modulo `2^16` and return the 16-bit pattern
- `add16(a, b) = wrap16(s16(a) + s16(b))`
- `sub16(a, b) = wrap16(s16(a) - s16(b))`
- `dot2(a0, a1, b0, b1) = wrap16(s16(a0)*s16(b0) + s16(a1)*s16(b1))`

The 4×4 input matrix `C` is

- row 0: `c00 c01 c02 c03`
- row 1: `c10 c11 c12 c13`
- row 2: `c20 c21 c22 c23`
- row 3: `c30 c31 c32 c33`

where each `cij` is the corresponding `in_cij` bit pattern.

The 2×2 input matrix `T` is

- row 0: `t00 t01`
- row 1: `t10 t11`

where `tij` is the corresponding `in_tij` bit pattern.

The eight `V` input ports carry the referenced nontrivial entries of the reflector matrix storage. The implied unit-triangular entries are not supplied on ports and must be inserted exactly as described below.

First derive the effective 2×2 matrix `TopT` used in the `W := W*T` or `W := W*T^T` step:
- if `in_direct = 0` (`DIRECT='F'`), `TopT` is upper triangular:
  - `TopT[0,0] = in_t00`
  - `TopT[0,1] = in_t01`
  - `TopT[1,0] = 0`
  - `TopT[1,1] = in_t11`
- if `in_direct = 1` (`DIRECT='B'`), `TopT` is lower triangular:
  - `TopT[0,0] = in_t00`
  - `TopT[0,1] = 0`
  - `TopT[1,0] = in_t10`
  - `TopT[1,1] = in_t11`

Then define `Tmul`:
- for `SIDE='L'`, `W` is formed from `C^T * V` or `C^T * V^T`; therefore the post-multiply uses `T^T` when `TRANS='N'` and `T` when `TRANS='T'`
- for `SIDE='R'`, `W` is formed from `C * V` or `C * V^T`; therefore the post-multiply uses `T` when `TRANS='N'` and `T^T` when `TRANS='T'`

Equivalently:
- if `(in_side == 0 and in_trans == 0) or (in_side == 1 and in_trans == 1)`, `Tmul = transpose(TopT)`
- otherwise, `Tmul = TopT`

Next construct the effective 4×2 matrix `Veff` used in the rank-2 update. `Veff` is always interpreted as a 4-row, 2-column matrix regardless of storage mode. The mapping from the eight `in_v**` ports to `Veff` is:

For `in_storev = 0` (`STOREV='C'`):
- `in_direct = 0` (`DIRECT='F'`):
  - `Veff = [[1, 0],
             [in_v10, 1],
             [in_v20, in_v21],
             [in_v30, in_v31]]`
- `in_direct = 1` (`DIRECT='B'`):
  - `Veff = [[in_v00, in_v01],
             [in_v10, in_v11],
             [1, in_v21],
             [0, 1]]`

For `in_storev = 1` (`STOREV='R'`):
- `in_direct = 0` (`DIRECT='F'`):
  - `Veff = [[1, in_v10],
             [0, 1],
             [in_v20, in_v30],
             [in_v21, in_v31]]`
- `in_direct = 1` (`DIRECT='B'`):
  - `Veff = [[in_v00, in_v10],
             [in_v01, in_v11],
             [1, 0],
             [in_v21, 1]]`

This `Veff` definition is the only interpretation of the `in_v**` ports.

The updated matrix is then computed by one of four fixed formulas.

### 1. `SIDE='L'`

Partition `C` by rows:
- `C1` = first two rows of `C`
- `C2` = last two rows of `C`

If `DIRECT='F'`, form:
1. `W0 = C1^T`
2. `W1 = W0 * V1`, where `V1` is the 2×2 unit-lower block `[[1,0],[in_v10,1]]` for `STOREV='C'` or the 2×2 unit-upper-transposed action embodied by the corresponding first two rows of `Veff` for `STOREV='R'`
3. `X = C2^T * V2`, where `V2` is the lower 2×2 block of `Veff`
4. `W2 = W1 + X`, elementwise with `add16`
5. `W3 = W2 * Tmul`
6. `C2' = C2 - V2 * W3^T`
7. `W4 = W3 * V1^T`
8. `C1' = C1 - W4^T`

If `DIRECT='B'`, form:
1. `W0 = C2^T`
2. `W1 = W0 * V2`, where `V2` is the lower 2×2 unit-upper or unit-lower-derived block according to `STOREV`
3. `X = C1^T * V1`, where `V1` is the upper 2×2 block of `Veff`
4. `W2 = W1 + X`
5. `W3 = W2 * Tmul`
6. `C1' = C1 - V1 * W3^T`
7. `W4 = W3 * V2^T`
8. `C2' = C2 - W4^T`

In all four matrix products above, every scalar output element is computed as a two-term `dot2`.

The output matrix for `SIDE='L'` is
- rows 0..1 = `C1'`
- rows 2..3 = `C2'`

### 2. `SIDE='R'`

Partition `C` by columns:
- `C1` = first two columns of `C`
- `C2` = last two columns of `C`

If `DIRECT='F'`, form:
1. `W0 = C1`
2. `W1 = W0 * V1`
3. `X = C2 * V2`
4. `W2 = W1 + X`
5. `W3 = W2 * Tmul`
6. `C2' = C2 - W3 * V2^T`
7. `W4 = W3 * V1^T`
8. `C1' = C1 - W4`

If `DIRECT='B'`, form:
1. `W0 = C2`
2. `W1 = W0 * V2`
3. `X = C1 * V1`
4. `W2 = W1 + X`
5. `W3 = W2 * Tmul`
6. `C1' = C1 - W3 * V1^T`
7. `W4 = W3 * V2^T`
8. `C2' = C2 - W4`

Again, every scalar output element of every 4×2 or 2×4 product is a two-term `dot2`.

The output matrix for `SIDE='R'` is
- columns 0..1 = `C1'`
- columns 2..3 = `C2'`

### 3. Explicit scalar expansion required for implementation

For conformance, the behavior is exactly the scalar fixed-size expansion below.

If `in_side = 0` and `in_direct = 0`, define:
- `A = [[1,0],[in_v10,1]]` when `in_storev = 0`, else `A = [[1,in_v10],[0,1]]`
- `B = [[in_v20,in_v21],[in_v30,in_v31]]` when `in_storev = 0`, else `B = [[in_v20,in_v30],[in_v21,in_v31]]`

For each column `j = 0..3`:
- `w1j0 = dot2(C[0,j], C[1,j], A[0,0], A[1,0])`
- `w1j1 = dot2(C[0,j], C[1,j], A[0,1], A[1,1])`
- `xj0  = dot2(C[2,j], C[3,j], B[0,0], B[1,0])`
- `xj1  = dot2(C[2,j], C[3,j], B[0,1], B[1,1])`
- `w2j0 = add16(w1j0, xj0)`
- `w2j1 = add16(w1j1, xj1)`
- `w3j0 = dot2(w2j0, w2j1, Tmul[0,0], Tmul[1,0])`
- `w3j1 = dot2(w2j0, w2j1, Tmul[0,1], Tmul[1,1])`

Then:
- for each column `j`, `y0j = dot2(B[0,0], B[0,1], w3j0, w3j1)` and `y1j = dot2(B[1,0], B[1,1], w3j0, w3j1)`
- `C'[2,j] = sub16(C[2,j], y0j)`
- `C'[3,j] = sub16(C[3,j], y1j)`

And:
- for each column `j`, `z0j = dot2(w3j0, w3j1, A[0,0], A[0,1])` and `z1j = dot2(w3j0, w3j1, A[1,0], A[1,1])`
- `C'[0,j] = sub16(C[0,j], z0j)`
- `C'[1,j] = sub16(C[1,j], z1j)`

If `in_side = 0` and `in_direct = 1`, define:
- `A = [[in_v00,in_v01],[in_v10,in_v11]]` when `in_storev = 0`, else `A = [[in_v00,in_v10],[in_v01,in_v11]]`
- `B = [[1,in_v21],[0,1]]` when `in_storev = 0`, else `B = [[1,0],[in_v21,1]]`

For each column `j = 0..3`:
- `w1j0 = dot2(C[2,j], C[3,j], B[0,0], B[1,0])`
- `w1j1 = dot2(C[2,j], C[3,j], B[0,1], B[1,1])`
- `xj0  = dot2(C[0,j], C[1,j], A[0,0], A[1,0])`
- `xj1  = dot2(C[0,j], C[1,j], A[0,1], A[1,1])`
- `w2j0 = add16(w1j0, xj0)`
- `w2j1 = add16(w1j1, xj1)`
- `w3j0 = dot2(w2j0, w2j1, Tmul[0,0], Tmul[1,0])`
- `w3j1 = dot2(w2j0, w2j1, Tmul[0,1], Tmul[1,1])`

Then:
- for each column `j`, `y0j = dot2(A[0,0], A[0,1], w3j0, w3j1)` and `y1j = dot2(A[1,0], A[1,1], w3j0, w3j1)`
- `C'[0,j] = sub16(C[0,j], y0j)`
- `C'[1,j] = sub16(C[1,j], y1j)`

And:
- for each column `j`, `z0j = dot2(w3j0, w3j1, B[0,0], B[0,1])` and `z1j = dot2(w3j0, w3j1, B[1,0], B[1,1])`
- `C'[2,j] = sub16(C[2,j], z0j)`
- `C'[3,j] = sub16(C[3,j], z1j)`

If `in_side = 1` and `in_direct = 0`, define:
- `A = [[1,0],[in_v10,1]]` when `in_storev = 0`, else `A = [[1,in_v10],[0,1]]`
- `B = [[in_v20,in_v21],[in_v30,in_v31]]` when `in_storev = 0`, else `B = [[in_v20,in_v30],[in_v21,in_v31]]`

For each row `i = 0..3`:
- `w1i0 = dot2(C[i,0], C[i,1], A[0,0], A[1,0])`
- `w1i1 = dot2(C[i,0], C[i,1], A[0,1], A[1,1])`
- `xi0  = dot2(C[i,2], C[i,3], B[0,0], B[1,0])`
- `xi1  = dot2(C[i,2], C[i,3], B[0,1], B[1,1])`
- `w2i0 = add16(w1i0, xi0)`
- `w2i1 = add16(w1i1, xi1)`
- `w3i0 = dot2(w2i0, w2i1, Tmul[0,0], Tmul[1,0])`
- `w3i1 = dot2(w2i0, w2i1, Tmul[0,1], Tmul[1,1])`

Then:
- for each row `i`, `y0i = dot2(w3i0, w3i1, B[0,0], B[0,1])` and `y1i = dot2(w3i0, w3i1, B[1,0], B[1,1])`
- `C'[i,2] = sub16(C[i,2], y0i)`
- `C'[i,3] = sub16(C[i,3], y1i)`

And:
- for each row `i`, `z0i = dot2(w3i0, w3i1, A[0,0], A[0,1])` and `z1i = dot2(w3i0, w3i1, A[1,0], A[1,1])`
- `C'[i,0] = sub16(C[i,0], z0i)`
- `C'[i,1] = sub16(C[i,1], z1i)`

If `in_side = 1` and `in_direct = 1`, define:
- `A = [[in_v00,in_v01],[in_v10,in_v11]]` when `in_storev = 0`, else `A = [[in_v00,in_v10],[in_v01,in_v11]]`
- `B = [[1,in_v21],[0,1]]` when `in_storev = 0`, else `B = [[1,0],[in_v21,1]]`

For each row `i = 0..3`:
- `w1i0 = dot2(C[i,2], C[i,3], B[0,0], B[1,0])`
- `w1i1 = dot2(C[i,2], C[i,3], B[0,1], B[1,1])`
- `xi0  = dot2(C[i,0], C[i,1], A[0,0], A[1,0])`
- `xi1  = dot2(C[i,0], C[i,1], A[0,1], A[1,1])`
- `w2i0 = add16(w1i0, xi0)`
- `w2i1 = add16(w1i1, xi1)`
- `w3i0 = dot2(w2i0, w2i1, Tmul[0,0], Tmul[1,0])`
- `w3i1 = dot2(w2i0, w2i1, Tmul[0,1], Tmul[1,1])`

Then:
- for each row `i`, `y0i = dot2(A[0,0], A[0,1], w3i0, w3i1)` and `y1i = dot2(A[1,0], A[1,1], w3i0, w3i1)`
- `C'[i,0] = sub16(C[i,0], y0i)`
- `C'[i,1] = sub16(C[i,1], y1i)`

And:
- for each row `i`, `z0i = dot2(w3i0, w3i1, B[0,0], B[0,1])` and `z1i = dot2(w3i0, w3i1, B[1,0], B[1,1])`
- `C'[i,2] = sub16(C[i,2], z0i)`
- `C'[i,3] = sub16(C[i,3], z1i)`

A worked example of the `Tmul` rule: if `in_side=0`, `in_trans=0`, `in_direct=0`, then `TopT=[[t00,t01],[0,t11]]` and `Tmul=[[t00,0],[t01,t11]]`.

## Reset Behavior

The design uses synchronous active-high reset `rst`.

Architectural state consists only of the output holding registers for the single pending output transaction and any internal busy flag required by the handshake implementation.

On reset:
- `out_valid = 0`
- `out_c00` through `out_c33` = `16'h0000`

There is no other architectural state.

After reset is deasserted, each accepted input transaction eventually produces exactly one output transaction carrying the computed `C'` matrix.

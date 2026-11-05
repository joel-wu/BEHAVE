# HouseholderReflectorApply10

HouseholderReflectorApply10 applies one real elementary reflector to a small real matrix using the explicitly unrolled formulas for nonempty reflector orders 1 through 10. The reflector is \(H = I - \tau v v^T\). One transaction consumes a complete matrix tile, a reflector vector, and a side selector, and produces the overwritten matrix tile. The transaction has a fixed 10x10 storage shape, with effective dimensions `m=min(in_m,10)` and `n=min(in_n,10)`. An empty submatrix is passed through; otherwise the reflector order is `m` for left application or `n` for right application. Arithmetic uses signed 32-bit integers with exact two's-complement wrap modulo \(2^{32}\) after every multiply, add, and subtract.

## Interface

### RTL interface

```systemverilog
module householder_reflector_apply10 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic [3:0]   in_m,
    input  logic [3:0]   in_n,
    input  logic [31:0]  in_tau,
    input  logic [31:0]  in_v0,
    input  logic [31:0]  in_v1,
    input  logic [31:0]  in_v2,
    input  logic [31:0]  in_v3,
    input  logic [31:0]  in_v4,
    input  logic [31:0]  in_v5,
    input  logic [31:0]  in_v6,
    input  logic [31:0]  in_v7,
    input  logic [31:0]  in_v8,
    input  logic [31:0]  in_v9,
    input  logic [31:0]  in_c00,
    input  logic [31:0]  in_c01,
    input  logic [31:0]  in_c02,
    input  logic [31:0]  in_c03,
    input  logic [31:0]  in_c04,
    input  logic [31:0]  in_c05,
    input  logic [31:0]  in_c06,
    input  logic [31:0]  in_c07,
    input  logic [31:0]  in_c08,
    input  logic [31:0]  in_c09,
    input  logic [31:0]  in_c10,
    input  logic [31:0]  in_c11,
    input  logic [31:0]  in_c12,
    input  logic [31:0]  in_c13,
    input  logic [31:0]  in_c14,
    input  logic [31:0]  in_c15,
    input  logic [31:0]  in_c16,
    input  logic [31:0]  in_c17,
    input  logic [31:0]  in_c18,
    input  logic [31:0]  in_c19,
    input  logic [31:0]  in_c20,
    input  logic [31:0]  in_c21,
    input  logic [31:0]  in_c22,
    input  logic [31:0]  in_c23,
    input  logic [31:0]  in_c24,
    input  logic [31:0]  in_c25,
    input  logic [31:0]  in_c26,
    input  logic [31:0]  in_c27,
    input  logic [31:0]  in_c28,
    input  logic [31:0]  in_c29,
    input  logic [31:0]  in_c30,
    input  logic [31:0]  in_c31,
    input  logic [31:0]  in_c32,
    input  logic [31:0]  in_c33,
    input  logic [31:0]  in_c34,
    input  logic [31:0]  in_c35,
    input  logic [31:0]  in_c36,
    input  logic [31:0]  in_c37,
    input  logic [31:0]  in_c38,
    input  logic [31:0]  in_c39,
    input  logic [31:0]  in_c40,
    input  logic [31:0]  in_c41,
    input  logic [31:0]  in_c42,
    input  logic [31:0]  in_c43,
    input  logic [31:0]  in_c44,
    input  logic [31:0]  in_c45,
    input  logic [31:0]  in_c46,
    input  logic [31:0]  in_c47,
    input  logic [31:0]  in_c48,
    input  logic [31:0]  in_c49,
    input  logic [31:0]  in_c50,
    input  logic [31:0]  in_c51,
    input  logic [31:0]  in_c52,
    input  logic [31:0]  in_c53,
    input  logic [31:0]  in_c54,
    input  logic [31:0]  in_c55,
    input  logic [31:0]  in_c56,
    input  logic [31:0]  in_c57,
    input  logic [31:0]  in_c58,
    input  logic [31:0]  in_c59,
    input  logic [31:0]  in_c60,
    input  logic [31:0]  in_c61,
    input  logic [31:0]  in_c62,
    input  logic [31:0]  in_c63,
    input  logic [31:0]  in_c64,
    input  logic [31:0]  in_c65,
    input  logic [31:0]  in_c66,
    input  logic [31:0]  in_c67,
    input  logic [31:0]  in_c68,
    input  logic [31:0]  in_c69,
    input  logic [31:0]  in_c70,
    input  logic [31:0]  in_c71,
    input  logic [31:0]  in_c72,
    input  logic [31:0]  in_c73,
    input  logic [31:0]  in_c74,
    input  logic [31:0]  in_c75,
    input  logic [31:0]  in_c76,
    input  logic [31:0]  in_c77,
    input  logic [31:0]  in_c78,
    input  logic [31:0]  in_c79,
    input  logic [31:0]  in_c80,
    input  logic [31:0]  in_c81,
    input  logic [31:0]  in_c82,
    input  logic [31:0]  in_c83,
    input  logic [31:0]  in_c84,
    input  logic [31:0]  in_c85,
    input  logic [31:0]  in_c86,
    input  logic [31:0]  in_c87,
    input  logic [31:0]  in_c88,
    input  logic [31:0]  in_c89,
    input  logic [31:0]  in_c90,
    input  logic [31:0]  in_c91,
    input  logic [31:0]  in_c92,
    input  logic [31:0]  in_c93,
    input  logic [31:0]  in_c94,
    input  logic [31:0]  in_c95,
    input  logic [31:0]  in_c96,
    input  logic [31:0]  in_c97,
    input  logic [31:0]  in_c98,
    input  logic [31:0]  in_c99,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_c00,
    output logic [31:0]  out_c01,
    output logic [31:0]  out_c02,
    output logic [31:0]  out_c03,
    output logic [31:0]  out_c04,
    output logic [31:0]  out_c05,
    output logic [31:0]  out_c06,
    output logic [31:0]  out_c07,
    output logic [31:0]  out_c08,
    output logic [31:0]  out_c09,
    output logic [31:0]  out_c10,
    output logic [31:0]  out_c11,
    output logic [31:0]  out_c12,
    output logic [31:0]  out_c13,
    output logic [31:0]  out_c14,
    output logic [31:0]  out_c15,
    output logic [31:0]  out_c16,
    output logic [31:0]  out_c17,
    output logic [31:0]  out_c18,
    output logic [31:0]  out_c19,
    output logic [31:0]  out_c20,
    output logic [31:0]  out_c21,
    output logic [31:0]  out_c22,
    output logic [31:0]  out_c23,
    output logic [31:0]  out_c24,
    output logic [31:0]  out_c25,
    output logic [31:0]  out_c26,
    output logic [31:0]  out_c27,
    output logic [31:0]  out_c28,
    output logic [31:0]  out_c29,
    output logic [31:0]  out_c30,
    output logic [31:0]  out_c31,
    output logic [31:0]  out_c32,
    output logic [31:0]  out_c33,
    output logic [31:0]  out_c34,
    output logic [31:0]  out_c35,
    output logic [31:0]  out_c36,
    output logic [31:0]  out_c37,
    output logic [31:0]  out_c38,
    output logic [31:0]  out_c39,
    output logic [31:0]  out_c40,
    output logic [31:0]  out_c41,
    output logic [31:0]  out_c42,
    output logic [31:0]  out_c43,
    output logic [31:0]  out_c44,
    output logic [31:0]  out_c45,
    output logic [31:0]  out_c46,
    output logic [31:0]  out_c47,
    output logic [31:0]  out_c48,
    output logic [31:0]  out_c49,
    output logic [31:0]  out_c50,
    output logic [31:0]  out_c51,
    output logic [31:0]  out_c52,
    output logic [31:0]  out_c53,
    output logic [31:0]  out_c54,
    output logic [31:0]  out_c55,
    output logic [31:0]  out_c56,
    output logic [31:0]  out_c57,
    output logic [31:0]  out_c58,
    output logic [31:0]  out_c59,
    output logic [31:0]  out_c60,
    output logic [31:0]  out_c61,
    output logic [31:0]  out_c62,
    output logic [31:0]  out_c63,
    output logic [31:0]  out_c64,
    output logic [31:0]  out_c65,
    output logic [31:0]  out_c66,
    output logic [31:0]  out_c67,
    output logic [31:0]  out_c68,
    output logic [31:0]  out_c69,
    output logic [31:0]  out_c70,
    output logic [31:0]  out_c71,
    output logic [31:0]  out_c72,
    output logic [31:0]  out_c73,
    output logic [31:0]  out_c74,
    output logic [31:0]  out_c75,
    output logic [31:0]  out_c76,
    output logic [31:0]  out_c77,
    output logic [31:0]  out_c78,
    output logic [31:0]  out_c79,
    output logic [31:0]  out_c80,
    output logic [31:0]  out_c81,
    output logic [31:0]  out_c82,
    output logic [31:0]  out_c83,
    output logic [31:0]  out_c84,
    output logic [31:0]  out_c85,
    output logic [31:0]  out_c86,
    output logic [31:0]  out_c87,
    output logic [31:0]  out_c88,
    output logic [31:0]  out_c89,
    output logic [31:0]  out_c90,
    output logic [31:0]  out_c91,
    output logic [31:0]  out_c92,
    output logic [31:0]  out_c93,
    output logic [31:0]  out_c94,
    output logic [31:0]  out_c95,
    output logic [31:0]  out_c96,
    output logic [31:0]  out_c97,
    output logic [31:0]  out_c98,
    output logic [31:0]  out_c99
);
```

`in_side = 0` selects left application (`H * C`), and `in_side = 1` selects right application (`C * H`).

`in_m` and `in_n` accept every unsigned 4-bit value. Values 11 through 15 select the maximum dimension 10; zero selects an empty dimension.

Each `in_cRC` and `out_cRC` element is the matrix element at row `R`, column `C` in a 10x10 row-major naming scheme.

### Behavior interface

```python
class HouseholderReflectorApply10:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_m,
        in_n,
        in_tau,
        in_v0, in_v1, in_v2, in_v3, in_v4,
        in_v5, in_v6, in_v7, in_v8, in_v9,
        in_c00, in_c01, in_c02, in_c03, in_c04, in_c05, in_c06, in_c07, in_c08, in_c09,
        in_c10, in_c11, in_c12, in_c13, in_c14, in_c15, in_c16, in_c17, in_c18, in_c19,
        in_c20, in_c21, in_c22, in_c23, in_c24, in_c25, in_c26, in_c27, in_c28, in_c29,
        in_c30, in_c31, in_c32, in_c33, in_c34, in_c35, in_c36, in_c37, in_c38, in_c39,
        in_c40, in_c41, in_c42, in_c43, in_c44, in_c45, in_c46, in_c47, in_c48, in_c49,
        in_c50, in_c51, in_c52, in_c53, in_c54, in_c55, in_c56, in_c57, in_c58, in_c59,
        in_c60, in_c61, in_c62, in_c63, in_c64, in_c65, in_c66, in_c67, in_c68, in_c69,
        in_c70, in_c71, in_c72, in_c73, in_c74, in_c75, in_c76, in_c77, in_c78, in_c79,
        in_c80, in_c81, in_c82, in_c83, in_c84, in_c85, in_c86, in_c87, in_c88, in_c89,
        in_c90, in_c91, in_c92, in_c93, in_c94, in_c95, in_c96, in_c97, in_c98, in_c99
    ):
        pass
```

The return value is one dictionary containing keys `out_c00` through `out_c99`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c04`, `out_c05`, `out_c06`,
  `out_c07`, `out_c08`, `out_c09`, `out_c10`, `out_c11`, `out_c12`, `out_c13`,
  `out_c14`, `out_c15`, `out_c16`, `out_c17`, `out_c18`, `out_c19`, `out_c20`,
  `out_c21`, `out_c22`, `out_c23`, `out_c24`, `out_c25`, `out_c26`, `out_c27`,
  `out_c28`, `out_c29`, `out_c30`, `out_c31`, `out_c32`, `out_c33`, `out_c34`,
  `out_c35`, `out_c36`, `out_c37`, `out_c38`, `out_c39`, `out_c40`, `out_c41`,
  `out_c42`, `out_c43`, `out_c44`, `out_c45`, `out_c46`, `out_c47`, `out_c48`,
  `out_c49`, `out_c50`, `out_c51`, `out_c52`, `out_c53`, `out_c54`, `out_c55`,
  `out_c56`, `out_c57`, `out_c58`, `out_c59`, `out_c60`, `out_c61`, `out_c62`,
  `out_c63`, `out_c64`, `out_c65`, `out_c66`, `out_c67`, `out_c68`, `out_c69`,
  `out_c70`, `out_c71`, `out_c72`, `out_c73`, `out_c74`, `out_c75`, `out_c76`,
  `out_c77`, `out_c78`, `out_c79`, `out_c80`, `out_c81`, `out_c82`, `out_c83`,
  `out_c84`, `out_c85`, `out_c86`, `out_c87`, `out_c88`, `out_c89`, `out_c90`,
  `out_c91`, `out_c92`, `out_c93`, `out_c94`, `out_c95`, `out_c96`, `out_c97`,
  `out_c98`, `out_c99`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `householder_reflector_apply10`: top-level module that accepts one 10x10 matrix tile, one reflector vector, and one side selector, and returns the overwritten 10x10 matrix tile.

## Functional Behavior

All ports carry 32-bit two's-complement integer bit patterns. Every input element is interpreted as a signed 32-bit integer. Every output element is returned as a 32-bit unsigned bit pattern representing the final two's-complement result.

Let `s32(x)` denote interpretation of a 32-bit port value as a signed integer in the range `[-2^31, 2^31-1]`. Let `wrap32(x)` denote reduction modulo \(2^{32}\) followed by signed reinterpretation into the same range. Every multiply, add, and subtract in this specification is performed as `wrap32` on the mathematical result before the next arithmetic operation uses it.

Construct:
- `tau = s32(in_tau)`
- `v[0..9] = s32(in_v0..in_v9)`
- `c[r][k] = s32(in_cRK)` for `0 <= r < 10`, `0 <= k < 10`

Set `m=min(in_m,10)` and `n=min(in_n,10)`. Only rows `0 .. m-1` and columns `0 .. n-1` are active. If either dimension is zero, return the whole input matrix unchanged. Otherwise, elements outside the active submatrix pass through unchanged to the corresponding output ports.

If `tau == 0`, every matrix element is passed through unchanged, including all active and inactive elements.

If `in_side == 0`, apply the reflector from the left. The reflector order is `m`. For each active column `j` from `0` to `n-1`, compute one `sum_j` using exactly the first `m` vector entries and the active rows of that column:
- order 1: `sum_j = v[0] * c[0][j]`
- order 2: `sum_j = (v[0] * c[0][j]) + (v[1] * c[1][j])`
- order 3: `sum_j = ((v[0] * c[0][j]) + (v[1] * c[1][j])) + (v[2] * c[2][j])`
- continue the same left-associated accumulation pattern up to order 10.

Define `t[i] = tau * v[i]` for `i = 0 .. m-1`, each multiplication wrapped to 32 bits.

Then, for each active column `j` and each active row `i` in `0 .. m-1`,
`c'[i][j] = c[i][j] - (sum_j * t[i])`,
with the multiplication and subtraction each wrapped to 32 bits.

If `in_side == 1`, apply the reflector from the right. The reflector order is `n`. For each active row `j` from `0` to `m-1`, compute one `sum_j` using exactly the first `n` vector entries and the active columns of that row:
- order 1: `sum_j = v[0] * c[j][0]`
- order 2: `sum_j = (v[0] * c[j][0]) + (v[1] * c[j][1])`
- order 3: `sum_j = ((v[0] * c[j][0]) + (v[1] * c[j][1])) + (v[2] * c[j][2])`
- continue the same left-associated accumulation pattern up to order 10.

Define `t[i] = tau * v[i]` for `i = 0 .. n-1`, each multiplication wrapped to 32 bits.

Then, for each active row `j` and each active column `i` in `0 .. n-1`,
`c'[j][i] = c[j][i] - (sum_j * t[i])`,
with the multiplication and subtraction each wrapped to 32 bits.

The update order is mathematically simultaneous with respect to the input matrix: every `sum_j` is computed from the original input `c`, not from already updated elements of the same row or column.

The unrolled order-specific formulas are the contract for effective orders 1 through 10. Empty dimensions and dimension clipping are handled as specified above; no larger matrix or fallback arithmetic is implied.

## Reset Behavior

Reset is synchronous active-high. On reset, the module holds no architectural state. `in_ready` is high whenever the module is able to accept an input transaction, and `out_valid` is low until a post-reset input transaction has been accepted and its corresponding output is available. The output data ports `out_c00` through `out_c99` are all zero immediately after reset and remain unconstrained by the specification except when `out_valid` is high for a completed transaction.

# LuInverse4x4

LuInverse4x4 computes the inverse of a 4×4 real matrix from a previously computed LU factorization with partial pivoting. The input matrix storage contains the combined unit-lower-triangular factor `L` below the diagonal and upper-triangular factor `U` on and above the diagonal, with row-pivot information in `ipiv`. The kernel follows the LAPACK DGETRI algorithm specialized to `N = 4`: solve against each identity column using the immutable packed L/U factors, then apply the recorded pivot interchanges in reverse order as column swaps. Factor and result entries use signed Q32.32.

## Interface

### RTL interface

```systemverilog
module lu_inverse_4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a30,
    input  logic [63:0]  in_a31,
    input  logic [63:0]  in_a32,
    input  logic [63:0]  in_a33,
    input  logic [2:0]   in_ipiv0,
    input  logic [2:0]   in_ipiv1,
    input  logic [2:0]   in_ipiv2,
    input  logic [2:0]   in_ipiv3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a00,
    output logic [63:0]  out_a01,
    output logic [63:0]  out_a02,
    output logic [63:0]  out_a03,
    output logic [63:0]  out_a10,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a20,
    output logic [63:0]  out_a21,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a30,
    output logic [63:0]  out_a31,
    output logic [63:0]  out_a32,
    output logic [63:0]  out_a33,
    output logic [2:0]   out_info
);
```

### Behavior interface

```python
class LuInverse4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3
    ):
        pass
```

The return dictionary contains the sixteen matrix fields listed below and `out_info`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a00`, `out_a01`, `out_a02`, `out_a03`, `out_a10`, `out_a11`, `out_a12`,
  `out_a13`, `out_a20`, `out_a21`, `out_a22`, `out_a23`, `out_a30`, `out_a31`,
  `out_a32`, `out_a33`: 64.
- `out_info`: 3.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

The 64-bit matrix ports carry signed two's-complement integers. `in_ipivk` are 1-based pivot indices. Any pivot value outside the valid range 1..4 is treated as a no-op in the final column-swap stage for the corresponding `k`.

## Modules

- `lu_inverse_4x4`: top-level kernel implementing the complete 4×4 LU-inverse transaction.

## Functional Behavior

All factor, right-hand-side and result values are signed 64-bit Q32.32
numbers, transported as raw bit patterns. This is a fixed-point adaptation,
not IEEE binary64. Let `wrap` retain a signed result's low 64 bits,
`qm(a,b)=wrap(floor(a*b/2^32))`, and
`qd(a,b)=wrap(trunc0((a*2^32)/b))`, with `qd(a,0)=0`.
Products and quotient numerators are formed before narrowing. Each subtraction
in a triangular solve wraps separately. All bit patterns have defined behavior.

The input packs unit-lower `L` below the diagonal and upper `U` on/above it.
The factorization convention is `P*A_original=L*U`; `in_ipiv` uses one-based
sequential row swaps. For each column `c`, solve `L*y=e_c` then `U*x=y`,
using increasing row order for `L` and decreasing order for `U`, summing each
row's preceding terms in increasing column order. Store `x` as column `c`.
Then swap output columns `j` and `ipiv[j]-1` for `j=2,1,0`; pivots outside
1..4 are ignored. The last pivot is unused for a 4x4 LU factorization.
If any diagonal of U is zero, return the original packed input unchanged and
the first zero diagonal's one-based index in `out_info`; otherwise `out_info=0`.
This preserves fractional inverses, e.g. diagonal 2.0 inverts to 0.5.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst = 1`, the module returns to its idle state.

Architectural state after reset:

- no transaction is buffered
- no partial computation is retained
- output-valid state is cleared

Output ports after reset:

- `out_valid = 0`
- `in_ready = 1`
- `out_a00` through `out_a33` = `64'h0000000000000000`
- `out_info = 3'b000`

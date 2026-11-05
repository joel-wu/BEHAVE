# LuSolve4x1

LuSolve4x1 solves one 4-element linear system right-hand side using a previously computed LU factorization with partial pivoting. The stored matrix factorization is interpreted as `P * A = L * U`, where `P` is the permutation implied by the pivot vector, `L` is unit lower triangular, and `U` is upper triangular. Each accepted input transaction computes either `A * x = b` or `A^T * x = b` for one right-hand side vector, using the same operation order as LAPACK DGETRS specialized to `N = 4` and `NRHS = 1`: row swaps, triangular solve with `L`, triangular solve with `U` for the non-transpose case; triangular solve with `U^T`, triangular solve with `L^T`, then reverse row swaps for the transpose case.

## Interface

### RTL interface

```systemverilog
module lu_solve_4x1 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_trans,

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

    input  logic [1:0]   in_ipiv0,
    input  logic [1:0]   in_ipiv1,
    input  logic [1:0]   in_ipiv2,
    input  logic [1:0]   in_ipiv3,

    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3
);
```

### Behavior interface

```python
class LuSolve4x1:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_trans,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3,
        in_b0, in_b1, in_b2, in_b3
    ):
        pass
```

The return value is one dictionary with keys `out_x0`, `out_x1`, `out_x2`, and `out_x3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0`, `out_x1`, `out_x2`, `out_x3`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `lu_solve_4x1`: top-level module implementing one accepted solve transaction for a 4x4 LU-factorized system and one 4-element right-hand side.

## Functional Behavior

All factor, right-hand-side and result values are signed 64-bit Q32.32
numbers, transported as raw bit patterns. This is a fixed-point adaptation,
not IEEE binary64. Let `wrap` retain a signed result's low 64 bits,
`qm(a,b)=wrap(floor(a*b/2^32))`, and
`qd(a,b)=wrap(trunc0((a*2^32)/b))`, with `qd(a,0)=0`.
Products and quotient numerators are formed before narrowing. Each subtraction
in a triangular solve wraps separately. All bit patterns have defined behavior.

The input contains unit-lower `L`, upper `U`, and zero-based 2-bit sequential
row-swap indices, with `P*A_original=L*U`. For `in_trans=0`, apply the swaps
in order 0..3 to `b`, solve `L*y=P*b`, then `U*x=y`. For `in_trans=1`, solve
`U^T*y=b`, then `L^T*x=y`, and undo the swaps in order 3..0. Unit diagonals
are implicit; other diagonal divisions use qd, including its zero rule.
Each triangular row subtracts contributing columns in increasing column order.
One input produces one output record containing x; no transaction state persists.

## Reset Behavior

Reset is synchronous active-high through `rst`. After reset, the module contains no architectural state from prior transactions. `in_ready` is high whenever the module can accept an input transaction; a correct implementation may accept immediately after reset. `out_valid` is low after reset until an output transaction is produced. `out_x0`, `out_x1`, `out_x2`, and `out_x3` are all `64'h0000000000000000` while `out_valid` is low after reset.

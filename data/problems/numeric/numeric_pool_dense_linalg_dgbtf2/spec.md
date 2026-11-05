# BandedLUFactor4x4

BandedLUFactor4x4 factors a 4x4 tridiagonal matrix using adjacent partial
pivoting (KL=KU=1), preserving the pivot-induced second superdiagonal.
Inputs use compact three-row storage; outputs use the required four-row LU
band storage. Entries use signed Q32.32, including fractional multipliers.

## Interface

### RTL interface

```systemverilog
module banded_lu_factor4x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_ab_0_0,
    input  logic [63:0] in_ab_0_1,
    input  logic [63:0] in_ab_0_2,
    input  logic [63:0] in_ab_0_3,
    input  logic [63:0] in_ab_1_0,
    input  logic [63:0] in_ab_1_1,
    input  logic [63:0] in_ab_1_2,
    input  logic [63:0] in_ab_1_3,
    input  logic [63:0] in_ab_2_0,
    input  logic [63:0] in_ab_2_1,
    input  logic [63:0] in_ab_2_2,
    input  logic [63:0] in_ab_2_3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_ab_0_0,
    output logic [63:0] out_ab_0_1,
    output logic [63:0] out_ab_0_2,
    output logic [63:0] out_ab_0_3,
    output logic [63:0] out_ab_1_0,
    output logic [63:0] out_ab_1_1,
    output logic [63:0] out_ab_1_2,
    output logic [63:0] out_ab_1_3,
    output logic [63:0] out_ab_2_0,
    output logic [63:0] out_ab_2_1,
    output logic [63:0] out_ab_2_2,
    output logic [63:0] out_ab_2_3,
    output logic [63:0] out_ab_3_0,
    output logic [63:0] out_ab_3_1,
    output logic [63:0] out_ab_3_2,
    output logic [63:0] out_ab_3_3,
    output logic [2:0]  out_ipiv_0,
    output logic [2:0]  out_ipiv_1,
    output logic [2:0]  out_ipiv_2,
    output logic [2:0]  out_ipiv_3,
    output logic [2:0]  out_info
);
```

The 3x4 input array is the fixed LAPACK band storage for KL = 1, KU = 1, LDAB = 3:

- `ab[0][j]` stores row `i = j - 1` of column `j` when that element exists.
- `ab[1][j]` stores row `i = j` of column `j`.
- `ab[2][j]` stores row `i = j + 1` of column `j` when that element exists.

Using 1-based mathematical indices and 0-based port suffixes, this is `AB(ku+1+i-j, j) = A(i,j)` with `ku = 1`.

Each matrix element port carries a 64-bit two's-complement integer bit pattern. Arithmetic interprets each element as a signed 64-bit integer. Every add, subtract, and multiply result is truncated to signed 64-bit two's-complement before being written back to the working array.

### Behavior interface

```python
class BandedLUFactor4x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_ab_0_0, in_ab_0_1, in_ab_0_2, in_ab_0_3,
        in_ab_1_0, in_ab_1_1, in_ab_1_2, in_ab_1_3,
        in_ab_2_0, in_ab_2_1, in_ab_2_2, in_ab_2_3
    ):
        pass
```

`process(...)` returns exactly one dict per accepted input. The dict keys are:
`out_ab_r_c` for `r,c = 0..3`, `out_ipiv_0` .. `out_ipiv_3`, and `out_info`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_ab_0_0`, `out_ab_0_1`, `out_ab_0_2`, `out_ab_0_3`, `out_ab_1_0`, `out_ab_1_1`,
  `out_ab_1_2`, `out_ab_1_3`, `out_ab_2_0`, `out_ab_2_1`, `out_ab_2_2`, `out_ab_2_3`,
  `out_ab_3_0`, `out_ab_3_1`, `out_ab_3_2`, `out_ab_3_3`: 64.
- `out_info`, `out_ipiv_0`, `out_ipiv_1`, `out_ipiv_2`, `out_ipiv_3`: 3.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `banded_lu_factor4x4`: top-level fixed-size LU factorization kernel for one 4x4 tridiagonal-band transaction.

## Functional Behavior

All matrix values are signed 64-bit Q32.32. Input `in_ab_1_j` holds A[j,j],
`in_ab_0_j` holds A[j-1,j] for j>0, and `in_ab_2_j` holds A[j+1,j] for j<3.
Other input slots are ignored. Initially every other dense entry is zero.

Let wrap retain the low 64 bits as a signed integer,
qm(x,y)=wrap(floor(x*y/2^32)), and qd(x,y)=wrap(trunc0((x*2^32)/y)).
Division by zero returns zero. Products and division numerators are widened
before narrowing. The elimination proceeds as follows, with zero-based indices:

1. Start info=0 and ju=0. For j=0..3 choose p in {j,min(j+1,3)} with
   largest absolute A[p,j], breaking ties in favor of j. Return ipiv[j]=p+1.
2. If the pivot is zero, set info=j+1 only if info was zero; continue.
3. Otherwise set ju=max(ju,min(3,p+1)). Swap row entries j and p only in
   columns j..ju. Previous stored multipliers are not swapped.
4. If j<3, replace A[j+1,j] by qd(A[j+1,j],A[j,j]). For c=j+1..ju,
   update A[j+1,c]=wrap(A[j+1,c]-qm(A[j+1,j],A[j,c])).

Return `out_ab_r_c=A[c+r-2,c]` for r=0..3 when the row index is in 0..3,
otherwise zero. Thus output rows 0,1,2,3 contain the second superdiagonal,
first superdiagonal, diagonal of U, and L multipliers respectively. The
second superdiagonal may be nonzero after a row pivot; it must not be dropped.
This is LAPACK's four-row factor storage for KL=KU=1, not its three-row input
storage. info is the first singular pivot index (one-based), or zero.
One accepted input returns one factor record; no factor state persists.

## Reset Behavior

The added `out_ab_3_0` through `out_ab_3_3` outputs reset to zero.

Reset is synchronous active-high. On any cycle with `rst = 1`, the module clears all architectural output registers to zero and deasserts any internally stored pending output-valid state.

Initial values after reset:
- `out_valid = 0`
- `out_ab_0_0` .. `out_ab_2_3` = `64'h0000000000000000`
- `out_ipiv_0` .. `out_ipiv_3` = `3'b000`
- `out_info = 3'b000`

The kernel has no persistent matrix state, pivot state, or accumulator state across transactions.

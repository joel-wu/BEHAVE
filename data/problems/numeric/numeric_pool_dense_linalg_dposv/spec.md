# CholeskySolve2x2

CholeskySolve2x2 solves one 2-by-2 real symmetric positive-definite system
from its lower triangle using Cholesky factorization and two triangular solves.
Inputs, returned factors and solutions use signed Q32.32. Positive pivots use
floor-rounded fixed-point square roots; they need not be perfect squares.
The quantized pivot tests and overflow behavior are specified below.
## Interface

### RTL interface

```systemverilog
module cholesky_solve_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_a00,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,
    input  logic [63:0] in_b0,
    input  logic [63:0] in_b1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_info,
    output logic [63:0] out_x0,
    output logic [63:0] out_x1,
    output logic [63:0] out_l00,
    output logic [63:0] out_l10,
    output logic [63:0] out_l11
);
```

### Behavior interface

```python
class CholeskySolve2x2:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a00, in_a10, in_a11, in_b0, in_b1):
        pass
```

The `process(...)` method returns exactly one output transaction for every accepted input transaction. The returned dictionary has keys `out_info`, `out_x0`, `out_x1`, `out_l00`, `out_l10`, and `out_l11`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_info`: 32.
- `out_l00`, `out_l10`, `out_l11`, `out_x0`, `out_x1`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cholesky_solve_2x2`: top-level module that accepts one 2-by-2 system, computes the integer Cholesky factorization from the lower triangle, and returns either the solved vector and factor or a failure code and partial factor.

## Functional Behavior

All factor, right-hand-side and result values are signed 64-bit Q32.32
numbers, transported as raw bit patterns. This is a fixed-point adaptation,
not IEEE binary64. Let `wrap` retain a signed result's low 64 bits,
`qm(a,b)=wrap(floor(a*b/2^32))`, and
`qd(a,b)=wrap(trunc0((a*2^32)/b))`, with `qd(a,0)=0`.
Products and quotient numerators are formed before narrowing. Each subtraction
in a triangular solve wraps separately. All bit patterns have defined behavior.

Interpret `A=[[a00,a10],[a10,a11]]` and solve `A*x=b` by lower Cholesky.
For positive raw x, `sqrtq(x)=floor(sqrt(x*2^32))`, without a perfect-square
restriction. Compute l00=sqrtq(a00), l10=qd(a10,l00),
pivot=wrap(a11-qm(l10,l10)), and l11=sqrtq(pivot). Then compute
y0=qd(b0,l00), y1=qd(wrap(b1-qm(l10,y0)),l11),
x1=qd(y1,l11), x0=qd(wrap(y0-qm(l10,x1)),l00).
If a00<=0 return info=1 and zero factors/solution. If the quantized pivot<=0,
return info=2, retain l00/l10, and return l11=x0=x1=0. Otherwise info=0.
These are quantized pivot tests; no claim of exact real arithmetic is made for
overflowing or ill-conditioned inputs. In particular non-square positive
pivots are supported, not converted into failures merely for being non-square.

## Reset Behavior

Reset is active-high and may be implemented synchronously or asynchronously. After reset:

- `out_valid = 0`
- `out_info = 0`
- `out_x0 = 0`
- `out_x1 = 0`
- `out_l00 = 0`
- `out_l10 = 0`
- `out_l11 = 0`

The block has no architectural state other than any internal registers needed to hold one pending 1:1 output transaction. Any such internal state is reset to the idle condition with no pending output.

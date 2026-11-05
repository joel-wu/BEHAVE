# CubicSplineInterpolatorF32

CubicSplineInterpolatorF32 evaluates a precomputed piecewise cubic spline at one query point per accepted input transaction. The knot locations, sample values, and cubic coefficients are provided as scalar ports for a fixed 4-point spline with 3 intervals. For each query `xq`, the module selects the interval exactly as the CMSIS-DSP scalar routine does for monotone query streams: it scans intervals from left to right and uses the first interval whose upper bound is greater than or equal to `xq`; queries below the first knot use the first interval; queries above the last knot use the last interval. The selected polynomial is then evaluated in FP32 as `y[i] + b[i]*dx + c[i]*dx^2 + d[i]*dx^3`, where `dx = xq - x[i]`.

## Interface

### RTL interface

```systemverilog
module cubic_spline_interpolator_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_xq,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y,

    input  logic [31:0] x0,
    input  logic [31:0] x1,
    input  logic [31:0] x2,
    input  logic [31:0] x3,

    input  logic [31:0] y0,
    input  logic [31:0] y1,
    input  logic [31:0] y2,
    input  logic [31:0] y3,

    input  logic [31:0] b0,
    input  logic [31:0] b1,
    input  logic [31:0] b2,

    input  logic [31:0] c0,
    input  logic [31:0] c1,
    input  logic [31:0] c2,

    input  logic [31:0] d0,
    input  logic [31:0] d1,
    input  logic [31:0] d2
);
```

### Behavior interface

```python
class CubicSplineInterpolatorF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_xq,
        x0, x1, x2, x3,
        y0, y1, y2, y3,
        b0, b1, b2,
        c0, c1, c2,
        d0, d1, d2,
    ):
        pass
```

`process(...)` returns one dict per accepted input transaction:

```python
{"out_y": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cubic_spline_interpolator_f32`: top-level spline evaluator for one FP32 query point and one FP32 output point.
  
## Functional Behavior

All ports carrying spline data or query/result values are IEEE-754 binary32 bit patterns in `uint32` form.

This is a 1:1 transaction. Each accepted input query produces exactly one output transaction.

The spline has exactly four knot positions `x0`, `x1`, `x2`, `x3`, four sample values `y0`, `y1`, `y2`, `y3`, and three cubic segments:

- interval 0 uses base point `x0` and coefficients `y0`, `b0`, `c0`, `d0`
- interval 1 uses base point `x1` and coefficients `y1`, `b1`, `c1`, `d1`
- interval 2 uses base point `x2` and coefficients `y2`, `b2`, `c2`, `d2`

The intended valid operating region is a strictly increasing knot sequence in FP32 numeric order:

- `x0 < x1 < x2 < x3`

Under this constraint, interval selection is:

1. If `xq <= x1`, select interval 0.
2. Else if `xq <= x2`, select interval 1.
3. Else select interval 2.

This rule also defines behavior outside the knot range:

- `xq < x0` uses interval 0.
- `xq > x3` uses interval 2.

The upper endpoint belongs to the interval on its left, matching the scalar CMSIS loop structure:

- `xq == x1` uses interval 0.
- `xq == x2` uses interval 1.
- `xq == x3` uses interval 2.

Worked examples:

- For `xq = x0 = +0`, `y0 = 0x00000001`, and finite zero coefficients, the module returns `0x00000001`. Endpoint equality does not bypass the arithmetic below: for example, an infinite selected coefficient multiplied by zero produces NaN.
- If `xq = x1`, the module uses interval 0 with `dx = fp32_sub(x1, x0)`.
- If `xq = x2`, the module uses interval 1 with `dx = fp32_sub(x2, x1)`.
- If `xq = x3`, the module uses interval 2 with `dx = fp32_sub(x3, x2)`.

After interval selection, let the selected tuple be `(xi, yi, bi, ci, di)`. The output is evaluated in this exact FP32 operation order:

1. `dx = fp32_sub(in_xq, xi)`
2. `dx2 = fp32_mul(dx, dx)`
3. `dx3 = fp32_mul(dx2, dx)`
4. `t1 = fp32_mul(bi, dx)`
5. `t2 = fp32_mul(ci, dx2)`
6. `t3 = fp32_mul(di, dx3)`
7. `s1 = fp32_add(yi, t1)`
8. `s2 = fp32_add(s1, t2)`
9. `out_y = fp32_add(s2, t3)`

`fp32_sub(a, b)` is defined as `fp32_add(a, fp32_neg(b))`.

No Horner-form reassociation is permitted. The arithmetic must follow the nine-step order above because FP32 rounding is part of the contract.

Each add, subtract and multiply rounds its exact result separately to binary32 using round-to-nearest, ties-to-even. Subnormal inputs retain their numerical values and underflow is gradual, including rounding at zero/subnormal and subnormal/normal boundaries. Overflow produces signed infinity. Output comparison is exact, not tolerance-based.

- Arithmetic with a NaN, opposite infinities added, or zero multiplied by infinity returns `0x7FC00000`. This canonical sign/payload is a project convention. Sign negation only toggles the sign bit, preserving every other bit.
- Multiplication uses the XOR of operand signs for zero and infinity. Adding two negative zeros returns negative zero; other exact cancellation returns positive zero.
- Interval selection uses ordered `<=`: `fp32_cmp` returns -1, 0, 1 or -2 (unordered), and only -1 or 0 selects that interval. An unordered comparison falls through; a NaN query therefore selects interval 2 and the arithmetic returns canonical NaN.
- `+0` and `-0` compare equal. Subnormals participate in comparisons by numerical value. For knots outside the intended increasing order, the same first-matching ordered comparison still defines selection; no sorting is performed.

The module has no architectural state affecting numerical results across transactions. Each accepted input is computed independently from the current port values.

## Reset Behavior

Reset is synchronous active-high.

Architectural state after reset:

- no retained spline state
- no retained interval pointer
- no retained query history

Output/reset-visible state after reset:

- `out_y = 32'h00000000`
- `out_valid = 1'b0`

After reset deasserts, the module may accept a new input transaction immediately.

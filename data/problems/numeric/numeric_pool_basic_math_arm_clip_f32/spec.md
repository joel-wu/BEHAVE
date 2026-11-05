# FP32 Clip4

FP32 Clip4 performs elementwise clipping of four independent IEEE-754 single-precision inputs against a shared lower bound and upper bound. Each accepted input transaction contains four FP32 values and two FP32 bounds; the module produces exactly one output transaction containing the four clipped results. For each lane, the result is selected by ordered floating-point comparisons in the sequence “greater than high”, then “less than low”, else pass through unchanged.

## Interface

### RTL interface

```systemverilog
module fp32_clip4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_low,
    input  logic [31:0] in_high,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class Fp32Clip4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_low, in_high):
        pass
```

Each call consumes one accepted input transaction and returns one `dict` with
exactly `out_y0`, `out_y1`, `out_y2`, and `out_y3`. All arguments and field
values are Python integers in 0..4294967295 carrying raw FP32 bits, not Python
floats. All four selected bit patterns belong to one response, including zero
and NaN results; do not return `None` or an empty list. There is no algorithmic
history between calls; `reset()` produces no response.

## Modules

- `fp32_clip4`: top-level module implementing one 4-lane clipping transaction per accepted input handshake.

## Functional Behavior

All `in_*` and `out_*` data ports carry raw FP32 bit patterns as unsigned 32-bit integers.

One accepted input transaction produces exactly one output transaction.

Each output lane is computed independently from the corresponding input lane and the shared bounds:
- `out_y0 = clip(in_x0, in_low, in_high)`
- `out_y1 = clip(in_x1, in_low, in_high)`
- `out_y2 = clip(in_x2, in_low, in_high)`
- `out_y3 = clip(in_x3, in_low, in_high)`

The lane clipping function is:

1. If `x > high`, return `high`.
2. Else if `x < low`, return `low`.
3. Else return `x`.

The comparisons `>` and `<` are IEEE-754 ordered FP32 comparisons over the raw FP32 operands:
- Comparison is by the numerical FP32 values represented by the bit patterns.
- `+0` and `-0` compare equal, so neither `+0 < -0` nor `-0 < +0` is true.
- Any comparison with a NaN operand is false.
- Subnormal operands retain their numerical values; they are not flushed to zero. For example, clipping `0x00000001` to bounds `0x00000000` and `0x00000000` returns zero, not the subnormal input.

Because both comparisons are ordered and evaluated in the sequence above, the required edge-case behavior is:

| Condition | Result |
|---|---|
| `x` is NaN | return `x` unchanged |
| `low` is NaN and `x > high` is false | return `x` unchanged |
| `high` is NaN | `x > high` is false; result is `low` if `x < low`, else `x` |
| `x` and a bound are both zeros of either sign | both comparisons against that zero bound are false unless the other comparison is true |
| `x` is `+Inf` and `high` is not NaN and less than `+Inf` | return `high` |
| `x` is `-Inf` and `low` is not NaN and greater than `-Inf` | return `low` |

No normalization, rounding, saturation, denormal flushing, or arithmetic operation is performed on the selected result. The output bit pattern is exactly one of the three input bit patterns for that lane: `x`, `low`, or `high`.

The module does not reorder or modify the bounds. If `low > high`, the same decision sequence still applies exactly as written. For example, with non-NaN operands, if `x > high` then the result is `high`; otherwise if `x < low` then the result is `low`; otherwise the result is `x`.

Worked example with non-NaN operands:
- `x = 7.0`, `low = 1.0`, `high = 5.0` => `x > high` is true => result `5.0`
- `x = -2.0`, `low = 1.0`, `high = 5.0` => `x > high` is false, `x < low` is true => result `1.0`
- `x = 3.0`, `low = 1.0`, `high = 5.0` => both comparisons false => result `3.0`

Worked example with NaN:
- `x = qNaN`, `low = 1.0`, `high = 5.0` => both comparisons false => result is the original NaN bit pattern from `x`

## Reset Behavior

Reset is synchronous active-high on `rst`.

Architectural state: none.

After reset, there is no retained computational state. Output payload ports are not architecturally significant until an output transaction occurs. A valid implementation may drive `out_y0`, `out_y1`, `out_y2`, and `out_y3` to zero during reset. `out_valid` must be low after reset until a post-reset input transaction has been accepted and the corresponding output transaction is presented.

# VectorNegateF32x4

VectorNegateF32x4 negates a fixed 4-element vector of IEEE-754 single-precision values. Each accepted input transaction contains four FP32 lanes and produces one output transaction containing the lane-wise arithmetic negation of those four values. The computation is elementwise and independent per lane; the required operation is IEEE-754 sign inversion equivalent to unary negation on each FP32 operand.

## Interface

### RTL interface

```systemverilog
module vector_negate_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,

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
class VectorNegateF32x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a0, in_a1, in_a2, in_a3):
        pass
```

Each call represents one accepted four-lane vector and returns exactly one
dictionary with keys `out_y0`, `out_y1`, `out_y2`, and `out_y3`. All arguments
and returned values are Python integers in [0, 2**32 - 1] encoding FP32 words,
not Python floating-point values. The four fields form one response, not four
records. Return the dictionary for all bit patterns, not `None` or a list.
There is no persistent state; `reset()` returns no response.

## Modules

- `vector_negate_f32x4`: top-level module. Accepts one 4-lane FP32 vector and returns the lane-wise negated 4-lane FP32 vector.

## Functional Behavior

Each input lane `in_aN` is a 32-bit unsigned integer carrying one IEEE-754 binary32 bit pattern. Each output lane `out_yN` is a 32-bit unsigned integer carrying one IEEE-754 binary32 bit pattern.

For each lane independently, the output is the floating-point unary negation of the input:
- `out_y0 = fp32_neg(in_a0)`
- `out_y1 = fp32_neg(in_a1)`
- `out_y2 = fp32_neg(in_a2)`
- `out_y3 = fp32_neg(in_a3)`

`fp32_neg` is the IEEE-754 sign-negation primitive from `fp_lib`. It preserves exponent and fraction fields and inverts the sign of the represented value. The required per-lane behavior is therefore equivalent to toggling bit 31 of the 32-bit FP32 encoding.

Per-lane edge-case behavior:
- Positive finite numbers become the corresponding negative finite numbers.
- Negative finite numbers become the corresponding positive finite numbers.
- `+0` (`0x00000000`) becomes `-0` (`0x80000000`).
- `-0` (`0x80000000`) becomes `+0` (`0x00000000`).
- `+Inf` (`0x7F800000`) becomes `-Inf` (`0xFF800000`).
- `-Inf` (`0xFF800000`) becomes `+Inf` (`0x7F800000`).
- NaNs remain NaNs with the same exponent and fraction payload bits; only the sign bit is inverted.
- Subnormal encodings are treated as FP32 bit patterns by `fp32_neg`; only the sign bit is inverted.

The four lanes are independent. There is no cross-lane reduction, saturation, rounding, exception flagging, or shared state.

The transaction mapping is 1:1: each accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high on `rst`.

Architectural state:
- The module has no architectural computation state.

Output interface state after reset:
- `out_valid = 0`
- `out_y0 = 0x00000000`
- `out_y1 = 0x00000000`
- `out_y2 = 0x00000000`
- `out_y3 = 0x00000000`

Input interface state after reset:
- `in_ready = 1`

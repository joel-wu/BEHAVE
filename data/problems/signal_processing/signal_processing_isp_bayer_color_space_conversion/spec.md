# Bayer Color Space Conversion

Convert one RGB sample to one 8-bit YUV sample using either of two integer coefficient matrices. Matrix rounding, sensor-domain offsets and clipping precede normalization to 8-bit output codes.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_color_space_conversion (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_conv_standard,
    input  logic [4:0]  in_bit_depth,
    input  logic [15:0] in_r,
    input  logic [15:0] in_g,
    input  logic [15:0] in_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_y,
    output logic [7:0]  out_u,
    output logic [7:0]  out_v
);
```

Input and output transfers occur on rising clock edges with the corresponding valid and ready both high. Each accepted input produces one ordered response. An asserted output remains valid with unchanged data until accepted. Latency is not fixed.

### Behavior interface

```python
class SignalProcessingIspBayerColorSpaceConversion:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_conv_standard, in_bit_depth, in_r, in_g, in_b): ...
```

Each call returns one dictionary with `out_y`, `out_u`, and `out_v`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_u`, `out_v`, `out_y`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_color_space_conversion`: per-pixel matrix conversion and range normalization.

## Functional Behavior

All inputs are unsigned. Let `d = in_bit_depth` (0..31) and mask each RGB input to its lowest `min(d,16)` bits. This defines `r,g,b`; at depth 0 all are zero.

For `in_conv_standard == 1`, the three numerators are:

- `Y = 47*r + 157*g + 16*b`
- `U = -26*r - 86*g + 112*b`
- `V = 112*r - 102*g - 10*b`

For codes 0, 2 and 3, use:

- `Y = 77*r + 150*g + 29*b`
- `U = -44*r - 87*g + 138*b`
- `V = 131*r - 110*g - 21*b`

These exact matrices define the task; they are not replaced by floating-point color-standard coefficients.

Define `round(x)` as nearest integer, with ties away from zero, and `clip(x,a,b) = min(b,max(a,x))`. For each channel:

1. Divide its numerator by 256 and round.
2. Add the offset: `2**(d/2)` for Y, `2**(d-1)` for U and V. Here `d/2` is real division, including odd depths.
3. Clip to `[0, 2**d-1]`.
4. Multiply by `2**(8-d)`, round again, then clip to `[0,255]`.

All intermediate arithmetic has sufficient range; only the stated rounding and clipping change values. Depths below 8 upscale rather than leaving the sample unchanged. Depth 0 produces three zeros. For a black input at depth 9, Y is 11 and U,V are 128. At depth 7, black gives Y=23 and U=V=128.

The real-valued offset specifies the mapping, not a required implementation. For `d>=8`, replacing the Y offset by `floor(sqrt(2**d))` gives the same final integer output. For `d<8`, the rounded output-domain Y offsets for depths 0..7 are `[256,181,128,91,64,45,32,23]`; apply the scaled sensor-range clipping before the final 8-bit clipping.

## Reset Behavior

Reset is asynchronous, active-high. It cancels pending responses, clears output data and deasserts `out_valid`. There is no pixel-history state. The next accepted input starts an independent conversion.

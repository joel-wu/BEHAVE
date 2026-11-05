# Bayer Black Level Correction

Subtract the black offset of a Bayer sample's color plane, optionally
linearize with that plane's saturation level, round and clip to the sensor
range. Each command supplies its complete configuration and produces one pixel.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_black_level_correction (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic        in_row_lsb,
    input  logic        in_col_lsb,
    input  logic [1:0]  in_bayer_pattern,
    input  logic        in_is_linear,
    input  logic [4:0]  in_bit_depth,

    input  logic [15:0] in_r_offset,
    input  logic [15:0] in_gr_offset,
    input  logic [15:0] in_gb_offset,
    input  logic [15:0] in_b_offset,

    input  logic [15:0] in_r_sat,
    input  logic [15:0] in_gr_sat,
    input  logic [15:0] in_gb_sat,
    input  logic [15:0] in_b_sat,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel
);
```

Pixel, offsets and saturation levels are unsigned 16-bit values.
The depth code is unsigned 5-bit; the two position bits select a Bayer phase.
Transfers occur on rising edges with the respective valid and ready high.
Output valid and payload are held under backpressure; responses retain order.

### Behavior interface

```python
class BayerBlackLevelCorrection:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_pixel,
        in_row_lsb,
        in_col_lsb,
        in_bayer_pattern,
        in_is_linear,
        in_bit_depth,
        in_r_offset,
        in_gr_offset,
        in_gb_offset,
        in_b_offset,
        in_r_sat,
        in_gr_sat,
        in_gb_sat,
        in_b_sat,
    ):
        pass
```

The method returns one dictionary with key `out_pixel`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pixel`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_black_level_correction`: plane selection,
  black subtraction and optional fixed-point linearization.

## Functional Behavior

The Bayer encoding and color-plane order at phases (0,0),(0,1),(1,0),(1,1) are:

| Code | Pattern | Plane order |
| --- | --- | --- |
| 0 | RGGB | R, Gr, Gb, B |
| 1 | BGGR | B, Gb, Gr, R |
| 2 | GRBG | Gr, R, B, Gb |
| 3 | GBRG | Gb, B, R, Gr |

Let `full_scale = 2^min(in_bit_depth,16) - 1`. Thus depth0 yields zero,
and every depth code16..31 uses the 16-bit maximum65535.
Select `offset` and `sat` from the plane-specific inputs and compute
`corrected=in_pixel-offset` as a signed integer.

If `in_is_linear=0`, the pre-clipped result is `corrected`.
Otherwise the linearization factor has **16 total unsigned bits, including
14 fractional bits**. Its integer code is:

```
denom = sat-offset
factor = min(65535, floor(full_scale*16384/denom))  if denom>0
         0                                       otherwise
value = corrected*factor
rounded = floor((value+8192)/16384)                if value>=0
          -floor((-value+8192)/16384)             otherwise
```

Factor quantization truncates and saturates; it does not round to nearest
or permit a signed factor. The final pixel rounding is nearest with ties
away from zero. A zero or negative denominator produces factor0, explicitly
defining this degenerate configuration. Nonlinear mode ignores the factor.

In both modes, `out_pixel=min(full_scale,max(0,rounded))`, taking
`rounded=corrected` in nonlinear mode. All intermediate arithmetic is exact.
For depth12, offset64, sat4064 and pixel2064, factor16773 and output2047.
For depth12, offset0, sat1 and pixel1, factor saturates at65535 and output4;
it is not an unlimited linearization gain.

## Reset Behavior

Reset is active-high and asynchronous. It cancels any pending response and
clears output valid and pixel to zero. No per-pixel data state is retained.

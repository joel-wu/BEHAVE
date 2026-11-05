# Bayer RGB Conversion

This block converts one 8-bit YUV pixel to one 8-bit RGB pixel using a fixed 3×3 integer matrix and fixed input offsets. Two conversion standards are supported: BT.709 and BT.601/407. The computation is performed entirely in signed integer arithmetic: subtract the standard YUV offsets, multiply by the selected integer coefficient matrix, arithmetic-right-shift each channel by 6 bits, then clamp each channel into the unsigned 8-bit range [0, 255].

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_rgb_conversion (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_y,
    input  logic [7:0]  in_u,
    input  logic [7:0]  in_v,
    input  logic        in_conv_std,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_r,
    output logic [7:0]  out_g,
    output logic [7:0]  out_b
);
```

`in_conv_std` selects the conversion matrix:
- `1'b1`: BT.709
- `1'b0`: BT.601/407

### Behavior interface

```python
class SignalProcessingIspBayerRgbConversion:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_y, in_u, in_v, in_conv_std): ...
```

`process(...)` returns:

```python
{
    "out_r": ...,
    "out_g": ...,
    "out_b": ...,
}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b`, `out_g`, `out_r`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_rgb_conversion`: top-level single-pixel YUV-to-RGB converter.

## Functional Behavior

For each accepted input transaction, the block consumes one YUV pixel and produces one RGB pixel.

Transfers occur on rising clock edges when valid and ready are both high. Responses preserve input order, and an asserted output remains valid with unchanged data until accepted. Latency is not fixed.

Inputs `in_y`, `in_u`, and `in_v` are unsigned 8-bit integers in the range 0 to 255. Internally they are converted to signed integers for offset subtraction.

Define offset-corrected signed inputs:
- `y_off = in_y - 16`
- `u_off = in_u - 128`
- `v_off = in_v - 128`

The conversion matrix is selected by `in_conv_std`.

If `in_conv_std == 1`, use the BT.709 matrix:
- `r_mul = 74*y_off + 0*u_off + 114*v_off`
- `g_mul = 74*y_off - 13*u_off - 34*v_off`
- `b_mul = 74*y_off + 135*u_off + 0*v_off`

If `in_conv_std == 0`, use the BT.601/407 matrix:
- `r_mul = 64*y_off + 0*u_off + 87*v_off`
- `g_mul = 64*y_off - 20*u_off - 44*v_off`
- `b_mul = 61*y_off + 105*u_off + 0*v_off`

Each multiplied sum is then narrowed by arithmetic right shift by 6 bits:
- `r_shift = r_mul >>> 6`
- `g_shift = g_mul >>> 6`
- `b_shift = b_mul >>> 6`

The arithmetic right shift is signed and rounds toward negative infinity, matching two's-complement arithmetic shift behavior.

Each shifted result is then clipped independently to the unsigned 8-bit range:
- if `r_shift < 0`, `out_r = 0`
- else if `r_shift > 255`, `out_r = 255`
- else `out_r = r_shift`

- if `g_shift < 0`, `out_g = 0`
- else if `g_shift > 255`, `out_g = 255`
- else `out_g = g_shift`

- if `b_shift < 0`, `out_b = 0`
- else if `b_shift > 255`, `out_b = 255`
- else `out_b = b_shift`

Outputs are unsigned 8-bit integers.

Worked BT.709 example:
- `in_y = 16`, `in_u = 128`, `in_v = 128`
- `y_off = 0`, `u_off = 0`, `v_off = 0`
- `r_mul = 0`, `g_mul = 0`, `b_mul = 0`
- `r_shift = 0`, `g_shift = 0`, `b_shift = 0`
- output is `out_r = 0`, `out_g = 0`, `out_b = 0`

Worked BT.601/407 example:
- `in_y = 235`, `in_u = 128`, `in_v = 128`
- `y_off = 219`, `u_off = 0`, `v_off = 0`
- `r_mul = 64*219 = 14016`, `g_mul = 64*219 = 14016`, `b_mul = 61*219 = 13359`
- `r_shift = 219`, `g_shift = 219`, `b_shift = 208`
- output is `out_r = 219`, `out_g = 219`, `out_b = 208`

No special cases exist for out-of-range YUV combinations beyond the clipping above. Negative intermediate values clip to 0. Intermediate values above 255 after shifting clip to 255.

## Reset Behavior

Reset is synchronous active-high `rst`.

The block has no architectural state affecting the numerical conversion result.

After reset:
- `out_valid = 0`
- `out_r = 0`
- `out_g = 0`
- `out_b = 0`

The block retains no computational state between transactions.

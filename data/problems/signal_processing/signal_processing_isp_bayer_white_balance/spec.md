# Bayer White Balance

This module applies per-color white-balance gains to a Bayer RAW pixel stream. Each accepted input transaction contains one pixel sample, its row and column coordinates, the Bayer pattern selection, the active sensor bit depth, and the red and blue gain coefficients. The module identifies whether the addressed Bayer site is red, green, or blue, multiplies only red or blue samples by the corresponding unsigned 16-bit gain with eight fractional bits, leaves green samples unchanged, clips the result to the selected sensor range, and emits one output pixel for each accepted input.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_white_balance (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic [15:0] in_row,
    input  logic [15:0] in_col,
    input  logic [1:0]  in_bayer_pattern,
    input  logic [4:0]  in_bit_depth,
    input  logic [15:0] in_r_gain_u16p8,
    input  logic [15:0] in_b_gain_u16p8,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel
);
```

`in_bayer_pattern` encoding:
- `2'd0`: RGGB
- `2'd1`: BGGR
- `2'd2`: GRBG
- `2'd3`: GBRG

`in_bit_depth` selects the clipping range: `2**min(in_bit_depth,16)-1`.
Depth0 produces zero; codes16..31 all use maximum65535. Every input bit pattern is defined.

`in_r_gain_u16p8` and `in_b_gain_u16p8` have 16 total unsigned bits, including eight fractional bits. A coefficient value `g` represents the real gain `g / 256`.

### Behavior interface

```python
class BayerWhiteBalance:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_pixel, in_row, in_col, in_bayer_pattern, in_bit_depth,
                in_r_gain_u16p8, in_b_gain_u16p8): ...
```

`process(...)` returns:

```python
{"out_pixel": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pixel`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_white_balance`: white-balance datapath for one Bayer RAW pixel per transaction.

## Functional Behavior

Inputs and outputs transfer on rising edges when their respective valid and
ready signals are high. One accepted input produces one ordered output;
output valid and payload remain stable under backpressure.

For each accepted input transaction, the module performs the following computation.

Let:
- `pixel = in_pixel`, interpreted as an unsigned integer.
- `row_parity = in_row & 1`.
- `col_parity = in_col & 1`.
- `max_value = (1 << min(in_bit_depth,16)) - 1`.

The Bayer color at the addressed site is determined only by `in_bayer_pattern`, `row_parity`, and `col_parity`.

For `in_bayer_pattern = 2'd0` (RGGB):
- `row_parity = 0`, `col_parity = 0`: red
- `row_parity = 0`, `col_parity = 1`: green
- `row_parity = 1`, `col_parity = 0`: green
- `row_parity = 1`, `col_parity = 1`: blue

For `in_bayer_pattern = 2'd1` (BGGR):
- `row_parity = 0`, `col_parity = 0`: blue
- `row_parity = 0`, `col_parity = 1`: green
- `row_parity = 1`, `col_parity = 0`: green
- `row_parity = 1`, `col_parity = 1`: red

For `in_bayer_pattern = 2'd2` (GRBG):
- `row_parity = 0`, `col_parity = 0`: green
- `row_parity = 0`, `col_parity = 1`: red
- `row_parity = 1`, `col_parity = 0`: blue
- `row_parity = 1`, `col_parity = 1`: green

For `in_bayer_pattern = 2'd3` (GBRG):
- `row_parity = 0`, `col_parity = 0`: green
- `row_parity = 0`, `col_parity = 1`: blue
- `row_parity = 1`, `col_parity = 0`: red
- `row_parity = 1`, `col_parity = 1`: green

The unbounded pre-clamp result `scaled` is:
- red site: `scaled = (pixel * in_r_gain_u16p8) >> 8`
- blue site: `scaled = (pixel * in_b_gain_u16p8) >> 8`
- green site: `scaled = pixel`

The right shift by 8 bits is the required U16.8 fixed-point truncation. It discards the fractional 8 least-significant bits of the non-negative product. No rounding is applied.

The final output is clipped to the sensor range:
- if `scaled > max_value`, `clipped = max_value`
- otherwise, `clipped = scaled`

`out_pixel` is `clipped` represented as an unsigned 16-bit value.

For legal inputs, `pixel`, both gains, and `scaled` are non-negative, so there is no lower-bound saturation step below zero.

## Reset Behavior

Reset is synchronous and active-high on `rst`.

This block has no architectural state.

While reset is asserted:
- `out_valid = 0`
- `out_pixel = 16'd0`

After reset is deasserted, output values are determined only by accepted input transactions.

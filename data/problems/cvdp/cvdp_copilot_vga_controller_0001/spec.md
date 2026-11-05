# cvdp_copilot_vga_controller_0001

Implement an autonomous VGA timing and color generator.

## Interface

### RTL interface

```systemverilog
module vga_controller #(
    parameter int H_ACTIVE=640, parameter int H_FRONT=16,
    parameter int H_PULSE=96, parameter int H_BACK=48,
    parameter int V_ACTIVE=480, parameter int V_FRONT=10,
    parameter int V_PULSE=2, parameter int V_BACK=33
)(input logic clock_level, reset_level, input logic [7:0] color_in,
  output wire hsync, vsync, output wire [7:0] red, green, blue,
  output wire [9:0] next_x, next_y, output wire sync, clk, blank);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, H_ACTIVE=640, H_FRONT=16, H_PULSE=96, H_BACK=48, V_ACTIVE=480, V_FRONT=10, V_PULSE=2, V_BACK=33): ...
    def reset(self): ...
    def process(self, clock_level, reset_level, color_in): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `blank`, `clk`, `hsync`, `sync`, `vsync`: 1.
- `blue`, `green`, `red`: 8.
- `next_x`, `next_y`: 10.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

reset() establishes fresh storage and prior input levels of zero. It is not called per
observation; physical reset_level is supplied explicitly.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `vga_controller`: the stateful digital circuit below.

## Functional Behavior

Generate autonomous VGA raster timing: every rising clock_level edge
advances one pixel, regardless of input color changes. No valid/ready or
backpressure exists. clock_level is the original physical clock, renamed for
explicit level observation; the clk output always equals that level, including
while reset is asserted. sync is always zero.

| Region | Active | Front porch | Sync pulse | Back porch | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Horizontal, pixels | 640 | 16 | 96 | 48 | 800 |
| Vertical, lines | 480 | 10 | 2 | 33 | 525 |

These are the default H_ACTIVE/H_FRONT/H_PULSE/H_BACK and corresponding V_
parameters. Parameterizing the timing is an explicit extension: each region
is positive and each axis total is at most 1024, retaining ten-bit coordinates.
The original 640x480 default is unchanged. Horizontal position runs from zero
to H_TOTAL-1; its wrap advances the vertical position modulo V_TOTAL.

Outputs describe the current settled raster position, including the advance
on a rising edge. They are combinational, not delayed a pixel. hsync is low
only during the horizontal pulse interval (default 656..751); vsync is low
only during the vertical pulse interval (default 490..491). Both are high
otherwise. The prompt's detailed active-low pulse rules take precedence over
its contradictory active-high interface labels.

Active video requires horizontal position < H_ACTIVE and vertical position
< V_ACTIVE, with reset deasserted. blank=0 then and 1 otherwise, including
ALL porches, not merely sync pulses. For active video, next_x/next_y give
the current position. Expand RRRGGGBB color_in to red=RRR<<5, green=GGG<<5,
blue=BB<<6. Outside active video all colors and both coordinates are zero.
Changing color without a clock edge immediately changes active-region RGB,
but cannot advance the raster. The ambiguous 'next' signal names do not
introduce look-ahead; coordinates and RGB refer to the same current pixel.

reset_level (the original reset pin, renamed only) is asynchronous active high:
positions become zero without a clock
edge; RGB/coordinates are zero, blank=1, and hsync=vsync=1. Release at a held
clock reveals position (0,0); the first subsequent rise advances to (1,0).
Fresh positions and prior clock level are zero. Records change their pins
together then observe settled outputs; no intermediate analog timing is
claimed. The source has no answer RTL and its frame-length TB contains no
output assertions. The polarity, blanking, output phase, reset output and
coordinate interpretations above are explicit source completions, not an
unchanged-TB correctness claim.

## Reset Behavior

The physical reset pin has the polarity and state effects specified above. Fresh execution initializes all internal storage and prior input levels to zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.

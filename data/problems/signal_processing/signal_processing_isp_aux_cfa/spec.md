# signal_processing_isp_aux_cfa

Malvar demosaicing of a 2×2 Bayer cell. Each command supplies a complete 6×6
raw-sample neighborhood and produces four RGB pixels in raster order.
The two-sample halo preserves every 5×5 interpolation stencil; no samples
are synthesized inside the block.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_cfa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_bayer_pattern,
    input  logic [15:0] in_clip,

    input  logic [15:0] in_p00,
    input  logic [15:0] in_p01,
    input  logic [15:0] in_p02,
    input  logic [15:0] in_p03,
    input  logic [15:0] in_p04,
    input  logic [15:0] in_p05,
    input  logic [15:0] in_p10,
    input  logic [15:0] in_p11,
    input  logic [15:0] in_p12,
    input  logic [15:0] in_p13,
    input  logic [15:0] in_p14,
    input  logic [15:0] in_p15,
    input  logic [15:0] in_p20,
    input  logic [15:0] in_p21,
    input  logic [15:0] in_p22,
    input  logic [15:0] in_p23,
    input  logic [15:0] in_p24,
    input  logic [15:0] in_p25,
    input  logic [15:0] in_p30,
    input  logic [15:0] in_p31,
    input  logic [15:0] in_p32,
    input  logic [15:0] in_p33,
    input  logic [15:0] in_p34,
    input  logic [15:0] in_p35,
    input  logic [15:0] in_p40,
    input  logic [15:0] in_p41,
    input  logic [15:0] in_p42,
    input  logic [15:0] in_p43,
    input  logic [15:0] in_p44,
    input  logic [15:0] in_p45,

    input  logic [15:0] in_p50,
    input  logic [15:0] in_p51,
    input  logic [15:0] in_p52,
    input  logic [15:0] in_p53,
    input  logic [15:0] in_p54,
    input  logic [15:0] in_p55,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_pixel_index,
    output logic [15:0] out_r,
    output logic [15:0] out_g,
    output logic [15:0] out_b
);
```

All samples, the clipping limit, and RGB outputs are unsigned 16-bit values.
The Bayer selector encodes RGGB=0, BGGR=1, GBRG=2, GRBG=3.
The 36 ports `in_pij` supply row `i`, column `j`, with both indices in 0..5.
The four output centers are (2,2), (2,3), (3,2), (3,3);
`out_pixel_index` is respectively 0,1,2,3.

Inputs and outputs transfer on rising clock edges when their respective
valid and ready signals are both high. Each accepted command produces
exactly four ordered outputs. Valid and payload remain stable under
backpressure. Frames from successive commands must not interleave.
The caller supplies any image-boundary padding in the input neighborhood.

### Behavior interface

```python
class SignalProcessingIspAuxCfa:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_bayer_pattern, in_clip,
        in_p00, in_p01, in_p02, in_p03, in_p04, in_p05,
        in_p10, in_p11, in_p12, in_p13, in_p14, in_p15,
        in_p20, in_p21, in_p22, in_p23, in_p24, in_p25,
        in_p30, in_p31, in_p32, in_p33, in_p34, in_p35,
        in_p40, in_p41, in_p42, in_p43, in_p44, in_p45,
        in_p50, in_p51, in_p52, in_p53, in_p54, in_p55):
        ...
```

The method returns four dictionaries, each containing
`out_pixel_index`, `out_r`, `out_g`, and `out_b`.

Return a list of exactly four dictionaries, ordered by out_pixel_index 0, 1, 2, 3 for
centers (2,2), (2,3), (3,2), (3,3). Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b`, `out_g`, `out_r`: 16.
- `out_pixel_index`: 2.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_aux_cfa`: complete-neighborhood Malvar interpolation
  and ordered four-pixel response.

## Functional Behavior

For center (y,x), let C=P[y,x] and define these exact unsigned sums:

- V=P[y−2,x]+P[y+2,x], H=P[y,x−2]+P[y,x+2].
- D=P[y−1,x−1]+P[y−1,x+1]+P[y+1,x−1]+P[y+1,x+1].
- Nv=P[y−1,x]+P[y+1,x], Nh=P[y,x−1]+P[y,x+1].

The signed interpolation numerators, all with denominator 16, are:

- G=8C−2(V+H)+4(Nv+Nh).
- O=12C−3(V+H)+4D.
- A=10C−2H−2D+V+8Nh.
- B=10C−2V−2D+H+8Nv.

The sample kind selects RGB numerators as follows:

| Kind | R numerator | G numerator | B numerator |
| --- | --- | --- | --- |
| r | 16C | G | O |
| gr (green on a red row) | A | 16C | B |
| gb (green on a blue row) | B | 16C | A |
| b | O | G | 16C |

For output indices 0..3, the kinds are respectively:

- RGGB: r, gr, gb, b.
- BGGR: b, gb, gr, r.
- GBRG: gb, b, r, gr.
- GRBG: gr, r, b, gb.

For each channel numerator n, output
`min(in_clip, max(0, floor(n/16)))`.
All intermediates are exact; there is no division or truncation before this
final quantization, and no signed-16 storage wrap before clipping.
The measured color sample at each center is preserved, subject to clipping.
A constant neighborhood of value k produces all channels equal to
`min(k,in_clip)`. Nonconstant neighborhoods depend on the complete halo,
including row and column 5 for the bottom/right centers.

## Reset Behavior

Reset is active-high and synchronous. A rising edge with `rst=1` cancels
all pending output pixels, clears `out_valid` and all output fields to zero.
After reset, the next command starts a new frame at index 0. No pixel history
is retained between commands.

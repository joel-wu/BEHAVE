# Luminance Auto-Exposure Statistics

Compute an exposure-feedback decision from a cropped 4×4 luma tile using centered second and third moments and the source's fixed-point skewness approximation. This task is the statistics operator; sensor rescaling, RGB-to-luma conversion and the outer exposure-control loop belong to the caller.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_auto_exposure (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_top_offset,
    input  logic [15:0]  in_bottom_offset,
    input  logic [15:0]  in_left_offset,
    input  logic [15:0]  in_right_offset,
    input  logic [7:0]   in_center_illuminance,
    input  logic [15:0]  in_histogram_skewness,
    input  logic [127:0] in_pixels,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [1:0]   out_feedback,
    output logic signed [63:0] out_skewness
);
```

Each input accepted with `in_valid && in_ready` at a rising clock edge produces one ordered response. Output transfer uses `out_valid && out_ready`; data remains stable while stalled. Latency is not fixed.

`in_pixels[8*(4*row+col)+:8]` contains unsigned luma at tile position `(row,col)`. Samples and center illuminance are unsigned 8-bit integers. The threshold is **unsigned Q8**: its real value is `in_histogram_skewness/256`. `out_skewness` is a signed 64-bit **Q8** value, also divided by 256 when interpreted numerically.

### Behavior interface

```python
class signal_processing_isp_bayer_auto_exposure:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_top_offset, in_bottom_offset,
                in_left_offset, in_right_offset, in_center_illuminance,
                in_histogram_skewness, in_pixels): ...
```

A call returns one dictionary with `out_feedback` and `out_skewness`. The latter carries the raw 64-bit two's-complement bit pattern.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_feedback`: 2.
- `out_skewness`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_auto_exposure`: cropped-tile moments, fixed-point skewness and feedback.

## Functional Behavior

### Crop and moments

Offsets discard rows from the top/bottom and columns from the left/right. Include exactly positions satisfying

```
in_top_offset <= row < 4 - in_bottom_offset
in_left_offset <= col < 4 - in_right_offset
```

within the 4×4 tile. Comparisons are mathematical integers, without unsigned wrap. A zero offset discards nothing. Offsets that eliminate the tile produce an empty crop.

For each included pixel set `d=p-in_center_illuminance`. With `n` included samples, compute `m2=trunc(sum(d*d)/n)` and `m3=trunc(sum(d*d*d)/n)`, where `trunc` rounds toward zero. Empty crops return zero skewness and neutral feedback.

### Fixed-point skewness

Set `a=m2//64`, `b=abs(m3)//512`. The square-root approximation uses exactly thirteen integer Newton updates:

```
r = a//2
repeat 13 times:
    r = (r + a//r)//2
```

For `a>=2`, every denominator is positive. The fixed iteration count is part of the mapping: it must not be replaced by floor square root, since an iteration may alternate between two adjacent integers. For `a<2`, where the initial divisor would be zero, this task defines zero skewness and neutral feedback.

Otherwise compute

```
q = (b*256)//(a*r)
Q = -q if m3 < 0 else q
```

Return `Q` as `out_skewness`. It represents the estimate `Q/256`; **do not divide the integer code by 256 again**. Accumulators do not overflow.

### Feedback

Compare `Q` against the complete unsigned threshold code `T=in_histogram_skewness`:

| Condition | out_feedback |
| --- | --- |
| `Q < -T` | `2'b11` (−1, underexposed) |
| `-T <= Q <= T` | `2'b00` (neutral) |
| `Q > T` | `2'b01` (+1, overexposed) |

Code `2'b10` is never emitted. Equality at either threshold is neutral. For a tile of value 50, center 0 and no crop, `a=39`, `b=244`, `r=6` and `Q=266`. Threshold 265 gives +1; threshold 266 gives neutral.

## Reset Behavior

Reset is synchronous, active-high. At the reset edge it cancels pending responses, deasserts `out_valid`, and clears both output fields. There is no inter-tile statistics state. A post-reset input starts a new independent calculation.

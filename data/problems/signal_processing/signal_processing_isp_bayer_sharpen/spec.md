# Luma Unsharp Masking

Sharpen a framed luma image by subtracting a 9×9 low-pass reconstruction, applying a configurable strength, and adding the signed correction back to each pixel. The caller supplies the quantized filter coefficients; zero padding, fixed-point rounding and output clipping are defined below.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_sharpen (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_luma,
    input  logic        in_frame_start,
    input  logic        in_line_start,
    input  logic        in_line_end,
    input  logic        in_frame_end,
    input  logic [9:0]  in_strength,
    input  logic [19:0] in_kernel_0_0,
    input  logic [19:0] in_kernel_0_1,
    input  logic [19:0] in_kernel_0_2,
    input  logic [19:0] in_kernel_0_3,
    input  logic [19:0] in_kernel_0_4,
    input  logic [19:0] in_kernel_0_5,
    input  logic [19:0] in_kernel_0_6,
    input  logic [19:0] in_kernel_0_7,
    input  logic [19:0] in_kernel_0_8,
    input  logic [19:0] in_kernel_1_0,
    input  logic [19:0] in_kernel_1_1,
    input  logic [19:0] in_kernel_1_2,
    input  logic [19:0] in_kernel_1_3,
    input  logic [19:0] in_kernel_1_4,
    input  logic [19:0] in_kernel_1_5,
    input  logic [19:0] in_kernel_1_6,
    input  logic [19:0] in_kernel_1_7,
    input  logic [19:0] in_kernel_1_8,
    input  logic [19:0] in_kernel_2_0,
    input  logic [19:0] in_kernel_2_1,
    input  logic [19:0] in_kernel_2_2,
    input  logic [19:0] in_kernel_2_3,
    input  logic [19:0] in_kernel_2_4,
    input  logic [19:0] in_kernel_2_5,
    input  logic [19:0] in_kernel_2_6,
    input  logic [19:0] in_kernel_2_7,
    input  logic [19:0] in_kernel_2_8,
    input  logic [19:0] in_kernel_3_0,
    input  logic [19:0] in_kernel_3_1,
    input  logic [19:0] in_kernel_3_2,
    input  logic [19:0] in_kernel_3_3,
    input  logic [19:0] in_kernel_3_4,
    input  logic [19:0] in_kernel_3_5,
    input  logic [19:0] in_kernel_3_6,
    input  logic [19:0] in_kernel_3_7,
    input  logic [19:0] in_kernel_3_8,
    input  logic [19:0] in_kernel_4_0,
    input  logic [19:0] in_kernel_4_1,
    input  logic [19:0] in_kernel_4_2,
    input  logic [19:0] in_kernel_4_3,
    input  logic [19:0] in_kernel_4_4,
    input  logic [19:0] in_kernel_4_5,
    input  logic [19:0] in_kernel_4_6,
    input  logic [19:0] in_kernel_4_7,
    input  logic [19:0] in_kernel_4_8,
    input  logic [19:0] in_kernel_5_0,
    input  logic [19:0] in_kernel_5_1,
    input  logic [19:0] in_kernel_5_2,
    input  logic [19:0] in_kernel_5_3,
    input  logic [19:0] in_kernel_5_4,
    input  logic [19:0] in_kernel_5_5,
    input  logic [19:0] in_kernel_5_6,
    input  logic [19:0] in_kernel_5_7,
    input  logic [19:0] in_kernel_5_8,
    input  logic [19:0] in_kernel_6_0,
    input  logic [19:0] in_kernel_6_1,
    input  logic [19:0] in_kernel_6_2,
    input  logic [19:0] in_kernel_6_3,
    input  logic [19:0] in_kernel_6_4,
    input  logic [19:0] in_kernel_6_5,
    input  logic [19:0] in_kernel_6_6,
    input  logic [19:0] in_kernel_6_7,
    input  logic [19:0] in_kernel_6_8,
    input  logic [19:0] in_kernel_7_0,
    input  logic [19:0] in_kernel_7_1,
    input  logic [19:0] in_kernel_7_2,
    input  logic [19:0] in_kernel_7_3,
    input  logic [19:0] in_kernel_7_4,
    input  logic [19:0] in_kernel_7_5,
    input  logic [19:0] in_kernel_7_6,
    input  logic [19:0] in_kernel_7_7,
    input  logic [19:0] in_kernel_7_8,
    input  logic [19:0] in_kernel_8_0,
    input  logic [19:0] in_kernel_8_1,
    input  logic [19:0] in_kernel_8_2,
    input  logic [19:0] in_kernel_8_3,
    input  logic [19:0] in_kernel_8_4,
    input  logic [19:0] in_kernel_8_5,
    input  logic [19:0] in_kernel_8_6,
    input  logic [19:0] in_kernel_8_7,
    input  logic [19:0] in_kernel_8_8,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_luma,
    output logic        out_frame_start,
    output logic        out_line_start,
    output logic        out_line_end,
    output logic        out_frame_end
);
```

Input transfer occurs on a rising edge with `in_valid && in_ready`. Output transfer uses `out_valid && out_ready`; output data and frame flags remain stable while stalled. No fixed latency is required.

### Behavior interface

```python
class SignalProcessingIspBayerSharpen:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_luma,
        in_frame_start,
        in_line_start,
        in_line_end,
        in_frame_end,
        in_strength,
        in_kernel_0_0,
        in_kernel_0_1,
        in_kernel_0_2,
        in_kernel_0_3,
        in_kernel_0_4,
        in_kernel_0_5,
        in_kernel_0_6,
        in_kernel_0_7,
        in_kernel_0_8,
        in_kernel_1_0,
        in_kernel_1_1,
        in_kernel_1_2,
        in_kernel_1_3,
        in_kernel_1_4,
        in_kernel_1_5,
        in_kernel_1_6,
        in_kernel_1_7,
        in_kernel_1_8,
        in_kernel_2_0,
        in_kernel_2_1,
        in_kernel_2_2,
        in_kernel_2_3,
        in_kernel_2_4,
        in_kernel_2_5,
        in_kernel_2_6,
        in_kernel_2_7,
        in_kernel_2_8,
        in_kernel_3_0,
        in_kernel_3_1,
        in_kernel_3_2,
        in_kernel_3_3,
        in_kernel_3_4,
        in_kernel_3_5,
        in_kernel_3_6,
        in_kernel_3_7,
        in_kernel_3_8,
        in_kernel_4_0,
        in_kernel_4_1,
        in_kernel_4_2,
        in_kernel_4_3,
        in_kernel_4_4,
        in_kernel_4_5,
        in_kernel_4_6,
        in_kernel_4_7,
        in_kernel_4_8,
        in_kernel_5_0,
        in_kernel_5_1,
        in_kernel_5_2,
        in_kernel_5_3,
        in_kernel_5_4,
        in_kernel_5_5,
        in_kernel_5_6,
        in_kernel_5_7,
        in_kernel_5_8,
        in_kernel_6_0,
        in_kernel_6_1,
        in_kernel_6_2,
        in_kernel_6_3,
        in_kernel_6_4,
        in_kernel_6_5,
        in_kernel_6_6,
        in_kernel_6_7,
        in_kernel_6_8,
        in_kernel_7_0,
        in_kernel_7_1,
        in_kernel_7_2,
        in_kernel_7_3,
        in_kernel_7_4,
        in_kernel_7_5,
        in_kernel_7_6,
        in_kernel_7_7,
        in_kernel_7_8,
        in_kernel_8_0,
        in_kernel_8_1,
        in_kernel_8_2,
        in_kernel_8_3,
        in_kernel_8_4,
        in_kernel_8_5,
        in_kernel_8_6,
        in_kernel_8_7,
        in_kernel_8_8,
    ): ...
```

Each call consumes one pixel. It returns `None` until the final pixel of a frame, then a list of output dictionaries in raster order. Dictionary keys match the five output payload signals.

For a legal frame, return None on each accepted pixel before in_frame_end. The final
pixel returns a list with one dictionary per received pixel, in raster order: exactly
sum(W[y]) records across the H lines. Never return a bare dictionary. Reset discards a
partial frame.
Every returned dictionary contains exactly these fields (bit widths):

- `out_frame_end`, `out_frame_start`, `out_line_end`, `out_line_start`: 1.
- `out_luma`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_sharpen`: framed luma storage, filtering and ordered output.

## Functional Behavior

### Frames and configuration

A legal frame has 1..8 lines, each containing 1..8 pixels. Pixels arrive in raster order. `in_frame_start` and `in_frame_end` mark exactly the first and last pixel of the frame; `in_line_start` and `in_line_end` mark exactly the first and last pixel of each line. A one-pixel line sets both line flags. The final pixel also sets `in_line_end`.

These constraints define the admissible input sequences for functional comparison, stimulus generation and bounded analysis. Frame flags are not independent unconstrained payload bits. Malformed framing is outside this contract; no output behavior is prescribed for it. Reset may cancel a partial frame as defined below.

Let `L[y,x]` be the received unsigned 8-bit luma, `H` the line count, and `W[y]` each line's width. The input and output counts are both `sum(W)`. Different line widths are permitted. No output precedes acceptance of the final input pixel. Starting a new frame clears the previous frame's storage.

Only the accepted final pixel supplies the kernel and strength used for that frame; their values on earlier pixels are ignored. Each `in_kernel_dy_dx` is an unsigned 20-bit coefficient representing `K[dy,dx]/2^20`. The unsigned 10-bit `in_strength` represents `S/2^10`, so the supported strength range is 0..1023/1024. The block does not normalize the kernel. Gaussian coefficients may be computed by the caller as `floor(2^20*exp(-((dx-4)^2+(dy-4)^2)/(2*sigma^2))/(2*pi*sigma^2))`, provided each coefficient fits its port.

### Filtering and sharpening

For each received pixel `(y,x)`, compute

```
correlation = sum(K[dy,dx] * P(y+dy-4,x+dx-4)
                  for dy in 0..8, dx in 0..8)
smoothened  = floor(correlation / 2^20)
edge        = L[y,x] - smoothened
correction  = floor(S * edge / 2^10)
out_luma    = min(255, max(0, L[y,x] + correction))
```

Here `P(sy,sx)=L[sy,sx]` inside the received frame and zero otherwise, including missing columns of shorter lines. Correlation is not circular and does not replicate edge pixels. Accumulation and multiplication do not overflow; divisions round down, including negative corrections. For example, `S=512` and `edge=-3` give `correction=-2`. A zero strength returns the input pixels unchanged.

Each output carries the corresponding frame/line boundary flags: `out_frame_start` only at `(0,0)`, `out_line_start` at `x=0`, `out_line_end` at `x=W[y]-1`, and `out_frame_end` only at `(H-1,W[H-1]-1)`. All received pixels, including the final line, produce exactly one ordered output. The completed frame does not influence the next frame.

## Reset Behavior

Reset is asynchronous, active-high. It clears the partial frame, line widths, counters and pending responses, and deasserts `out_valid`. All output payload and boundary-flag signals are zero during reset. Input after reset starts a new frame; canceled pixels produce no later output.

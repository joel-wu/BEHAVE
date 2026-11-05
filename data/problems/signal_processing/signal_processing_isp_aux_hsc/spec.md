# Hue and Saturation Control

This block rotates a two-channel chroma sample around neutral code 128 and scales its saturation. The hue is an integer angle in degrees; the rotation uses fixed Q8 sine/cosine coefficients. The complete rotated-and-scaled result is quantized once and clipped to the supplied output limit. Every accepted input produces one output transaction in order.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_hsc (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_c0,
    input  logic [7:0]  in_c1,
    input  logic [8:0]  in_hue,
    input  logic [8:0]  in_saturation,
    input  logic [7:0]  in_clip,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_c0,
    output logic [7:0]  out_c1
);
```

### Behavior interface

```python
class SignalProcessingIspAuxHsc:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_c0, in_c1, in_hue, in_saturation, in_clip): ...
```

`process` returns `{"out_c0": ..., "out_c1": ...}`.
Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c0`, `out_c1`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

An RTL input transfers when `in_valid && in_ready`; an output transfers when `out_valid && out_ready`. An unconsumed valid output must remain stable. No fixed internal latency is required.

## Modules

- `signal_processing_isp_aux_hsc`: chroma rotation, saturation scaling and clipping.

## Functional Behavior

All ports are unsigned bit patterns. The two channels and clip limit are in [0,255]. `in_hue` is in [0,511] and is reduced modulo 360. `in_saturation` is a Q8 gain in [0,511]; 256 is unity and zero produces neutral chroma before clipping.

Let `Q[a]`, for integer `a=0..90`, be this exact quarter-wave sine table:

```text
0, 4, 9, 13, 18, 22, 27, 31, 36, 40, 44, 49, 53
58, 62, 66, 71, 75, 79, 83, 88, 92, 96, 100, 104, 108
112, 116, 120, 124, 128, 132, 136, 139, 143, 147, 150, 154, 158
161, 165, 168, 171, 175, 178, 181, 184, 187, 190, 193, 196, 199
202, 204, 207, 210, 212, 215, 217, 219, 222, 224, 226, 228, 230
232, 234, 236, 237, 239, 241, 242, 243, 245, 246, 247, 248, 249
250, 251, 252, 253, 254, 254, 255, 255, 255, 256, 256, 256, 256
```

The table represents sine multiplied by 256. Define `S(a)` for `a=0..359`:

- if `a <= 90`, use `Q[a]`;
- else if `a <= 180`, use `Q[180-a]`;
- else if `a <= 270`, use `-Q[a-180]`;
- otherwise use `-Q[360-a]`.

Set `a = in_hue % 360`, `s = S(a)`, and `c = S((a+90) % 360)`. For signed centered channels `u = in_c0-128` and `v = in_c1-128`, compute:

```text
ru = u*c + v*s
rv = v*c - u*s
qu = 128 + floor(in_saturation*ru / 65536)
qv = 128 + floor(in_saturation*rv / 65536)
out_c0 = min(in_clip, max(0, qu))
out_c1 = min(in_clip, max(0, qv))
```

All intermediates are exact signed integers: do not truncate the rotation before applying saturation and do not wrap before clipping. The denominator contains both Q8 scales. Floor division rounds toward negative infinity; for example `floor(-1/65536)=-1`.

At unity saturation and clip 255, input (192,128) gives (192,128), (128,64), (64,128), and (128,192) at hue 0, 90, 180, and 270 respectively. Hue 360 is identical to hue 0. At hue 0, input (127,128) and saturation 1 gives (127,128). Clip zero makes both outputs zero.

## Reset Behavior

Reset is synchronous active-high. On a rising clock edge with `rst=1`, discard any pending output and clear `out_valid`, `out_c0`, and `out_c1` to zero. There is no inter-transaction algorithmic state. After reset is deasserted, the next input is processed independently of all previous samples.

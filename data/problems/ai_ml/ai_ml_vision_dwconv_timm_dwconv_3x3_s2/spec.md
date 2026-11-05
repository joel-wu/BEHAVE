# Stride-Two Depthwise 3x3 Frame Convolution

Compute a three-channel depthwise convolution of a 4x4 image, using one learned
3x3 kernel per channel, stride two and one-pixel zero padding. A request contains
the complete image and all kernels; one response contains the 2x2x3 output image.
The computation is integer cross-correlation without kernel reversal. There is
no bias, normalization, activation, anti-alias filter or channel mixing.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_dwconv_timm_dwconv_3x3_s2 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [383:0] in_x,
    input logic [215:0] in_weight,
    output logic out_valid,
    input logic out_ready,
    output logic [239:0] out_y
);
```

### Behavior interface

```python
class AiMlVisionDwconvTimmDwconv3x3S2:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_weight): ...
```

`process` returns one dictionary `{"out_y": packed_output}` per request.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 240.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

Packed
port values are nonnegative integers, with signed two's-complement lanes:

| Port | Lanes and order, least-significant lane first |
| --- | --- |
| `in_x` | 48 signed 8-bit values; lane `(y*4+x)*3+c`, y,x=0..3 and c=0..2 |
| `in_weight` | 27 signed 8-bit values; lane `(c*3+ky)*3+kx`, c,ky,kx=0..2 |
| `out_y` | 12 signed 20-bit values; lane `(oy*2+ox)*3+c`, oy,ox=0..1 and c=0..2 |

At a rising edge outside reset, a request transfers iff `in_valid && in_ready`;
a response transfers iff `out_valid && out_ready`. Inputs must remain stable
while offered and not accepted. Responses are ordered and remain stable while
`out_valid && !out_ready`. Latency is not fixed. Every accepted request produces
exactly one response unless reset discards it.

## Modules

`ai_ml_vision_dwconv_timm_dwconv_3x3_s2` implements the complete frame operation
and its request/response interface. Internal buffering and arithmetic reuse are
implementation choices.

## Functional Behavior

Decode input lanes to integers in [-128,127]. Let `X[y,x,c]` be the image and
`W[c,ky,kx]` the kernel. Define X to be zero when y or x is outside 0..3.
For oy,ox in 0..1 and channel c in 0..2, compute

```text
Y[oy,ox,c] = sum over ky,kx in 0..2 of
            X[2*oy+ky-1, 2*ox+kx-1, c] * W[c,ky,kx].
```

The sums are exact: no intermediate rounding, saturation or narrowing occurs.
Each output fits in signed 20 bits; even nine terms lie in [-146304,147456].
Pack each result as its value modulo 2^20 in the specified lane. All input bit
patterns are legal. Each channel uses only its own pixels and kernel; there is
no accumulation across frames or channels.

For example, if every input and weight lane is 1, each channel's 2x2 output is
`[[4,6],[6,9]]`. For a kernel whose center alone is 1, output `(oy,ox,c)` equals
input `(2*oy,2*ox,c)`, demonstrating the stride and padding origin.

## Reset Behavior

`rst` is synchronous active-high. While asserted, `in_ready=0`. At a reset edge,
discard unfinished and unconsumed responses, set `out_valid=0` and `out_y=0`.
After release, the next accepted frame is independent of pre-reset work.
Computation has no persistent state. Output data is
unspecified when `out_valid=0`, except for the reset value just stated.

# Fir2dSameReflectS16

This block computes a selected output pixel of a same-sized 2-D convolution
with a programmable 3x3 signed kernel. It reads a bounded external image and
performs symmetric reflection itself, including at corners and one-pixel dimensions.

## Interface

### RTL interface

```systemverilog
module fir2d_same_reflect_s16 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [3:0] in_width_m1,
    input logic [3:0] in_height_m1,
    input logic [3:0] in_x,
    input logic [3:0] in_y,
    input logic [15:0] in_h00,
    input logic [15:0] in_h01,
    input logic [15:0] in_h02,
    input logic [15:0] in_h10,
    input logic [15:0] in_h11,
    input logic [15:0] in_h12,
    input logic [15:0] in_h20,
    input logic [15:0] in_h21,
    input logic [15:0] in_h22,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y,
    output logic image_req_valid,
    output logic [7:0] image_req_addr,
    input logic image_req_ready,
    input logic image_resp_valid,
    input logic [15:0] image_resp_rdata
);
```

### Behavior interface

```python
class Fir2dSameReflectS16:
    def __init__(self):
        pass
    def reset(self):
        pass
    def process(self, in_width_m1, in_height_m1, in_x, in_y,
                in_h00, in_h01, in_h02, in_h10, in_h11, in_h12,
                in_h20, in_h21, in_h22):
        pass
```

`process` returns one dictionary containing `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.image is the external read-only view of 256 unsigned 16-bit words, interpreted as
signed pixels by the computation. Standalone construction initializes zero words; the
environment supplies the run image and reset() preserves the view. Memory-request pins
are not return fields.

## Modules

- `fir2d_same_reflect_s16`: reflected image addressing, signed convolution, and transport.

## Functional Behavior

The image width `W = in_width_m1+1` and height `H = in_height_m1+1` are in
1..16. The image uses a fixed row stride of 16 words: pixel `(row,col)` is
`image[16*row+col]`. Coordinates `x=in_x`, `y=in_y` are unsigned. If `x>=W`
or `y>=H`, return zero without issuing any memory request. Otherwise:

```text
sum = 0
for j = 0,1,2:
    for k = 0,1,2:
        row = reflect(y+1-j, H)
        col = reflect(x+1-k, W)
        sum += signed16(in_h[j,k]) * signed16(image[16*row+col])
out_y = sum modulo 2^32
```

`in_h[j,k]` names `in_h00` through `in_h22` in row-major order. Reflection
duplicates edge pixels: for this one-pixel halo, `reflect(i,n)` is `-i-1`
when `i<0`, `2*n-i-1` when `i>=n`, and `i` otherwise. The minus signs in
`y+1-j` and `x+1-k` implement convolution, not correlation. Compute all nine
products even when boundary reflection repeats an address; issue exactly nine
reads in the shown row-major kernel order.

Pixels and coefficients use signed two's-complement 16-bit arithmetic.
Products and their sum are mathematical integers; only the final result wraps
to a 32-bit bit pattern, without saturation. All port values are defined.

The read-only external memory `image` has 256 signed 16-bit words,
whose image is supplied by the environment;
contents are not restricted to zero. A read request transfers when
`image_req_valid && image_req_ready`. The module holds its request valid and
address until accepted. Each request receives exactly one later response,
with `image_resp_valid` and `image_resp_rdata`, in request order; the earliest
response is the following rising edge. The environment may delay requests
and responses. No response-ready signal is used.

Inputs transfer on rising edges with `in_valid && in_ready`, and
outputs with `out_valid && out_ready`. Every accepted command produces exactly
one output, in command order. Both sides hold valid and payload stable while
stalled. Latency is implementation-dependent; accepted work must complete when
the environment continues servicing memory and outputs.

## Reset Behavior

Reset is synchronous active-high. It cancels pending commands and outputs;
`out_valid`, `out_y`, and `image_req_valid` become zero, and `in_ready` becomes
one. The environment cancels pending responses with reset. The module neither
clears nor initializes external image storage; its contents are an environment
configuration.

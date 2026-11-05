# Block FIR Filter

This block applies a fixed four-tap FIR filter to a stream of eight-sample blocks. Each accepted block produces the next eight convolution samples, including the previous block's tail. Coefficients are fixed at construction and preserved by reset.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_filter_fftfilt #(
    parameter logic [127:0] COEFFS = {32'sd1, 32'sd3, -32'sd2, 32'sd1}
) (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic [31:0]        in_x0,
    input  logic [31:0]        in_x1,
    input  logic [31:0]        in_x2,
    input  logic [31:0]        in_x3,
    input  logic [31:0]        in_x4,
    input  logic [31:0]        in_x5,
    input  logic [31:0]        in_x6,
    input  logic [31:0]        in_x7,

    output logic               out_valid,
    input  logic               out_ready,
    output logic [31:0]        out_y0,
    output logic [31:0]        out_y1,
    output logic [31:0]        out_y2,
    output logic [31:0]        out_y3,
    output logic [31:0]        out_y4,
    output logic [31:0]        out_y5,
    output logic [31:0]        out_y6,
    output logic [31:0]        out_y7
);
```

### Behavior interface

```python
class signal_processing_comm_filter_fftfilt:
    def __init__(self, coef=None): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3,
                in_x4, in_x5, in_x6, in_x7): ...
```

The constructor accepts four signed-32-bit coefficient bit patterns; the default is [1,-2,3,1].  Each call returns one dictionary with out_y0 through out_y7 as unsigned 32-bit port patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`, `out_y4`, `out_y5`, `out_y6`, `out_y7`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Coefficient packing

RTL COEFFS packs coefficient j in bits [32*j +: 32], with coefficient zero least significant.

## Modules

- signal_processing_comm_filter_fftfilt: block FIR convolution and ordered ready/valid transport.

## Functional Behavior

All input samples and coefficients represent signed 32-bit two's-complement integers, and all sample bit patterns are legal. Let h[0..3] denote the fixed coefficients. Number samples across accepted blocks: x[8*b+k] is input in_xk of accepted block b. Define x[n]=0 for n<0.

For k=0,...,7, output out_yk of block b is the low 32 bits of

y[8*b+k] = sum(h[j] * x[8*b+k-j], j=0..3).

h[0] multiplies the current sample, and h[1..3] multiply its three predecessors. Carry history across block boundaries; each block is not an independent zero-padded convolution. Compute the signed products and sum exactly, then wrap the result modulo 2^32. There is no saturation, fractional scaling or coefficient reload port.

With default coefficients, a first block [1,2,3,4,5,6,7,8] produces [1,0,2,5,8,11,14,17]. An impulse at the last position of an otherwise zero block produces a final output of 1; the following all-zero block begins with [-2,3,1,0], encoded modulo 2^32.

One accepted input block produces exactly one ordered output block. Outside reset, transfers occur on rising edges with the corresponding valid && ready. Hold out_valid and the entire output block stable under backpressure; the sender holds a pending input block stable as well. No fixed latency or internal algorithm is prescribed.

## Reset Behavior

Reset is synchronous active-high and takes priority over transfers. It clears sample history, out_valid, all output payloads and any pending response. It does not change h. After reset the first accepted block uses zero prehistory and the same construction-time coefficients.

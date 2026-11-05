# QLinearConv3x3PerChannel

Three-output-channel affine INT8 3x3 convolution with per-channel requantization.

## Interface

### RTL interface

```systemverilog
module QLinearConv3x3PerChannel (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [431:0] in_weights,
    input logic [47:0] in_bias,
    input logic [7:0] in_zero_x,
    input logic [23:0] in_zero_w,
    input logic [11:0] in_shifts,
    output logic [95:0] out_values
);
endmodule
```

### Behavior interface

```python
class QLinearConv3x3PerChannel:
    def reset(self): ...
    def process(self, in_data, in_weights, in_bias, in_zero_x, in_zero_w, in_shifts): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 96.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Any unused output slots are zero as defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`QLinearConv3x3PerChannel` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Three-output-channel affine INT8 3x3 convolution with per-channel requantization.

X is NCHW [1,2,4,4]; W is [3,2,3,3], both signed bytes. Bias contains three signed INT16 values. For Y[o,h,w], h,w=0..1, accumulate bias[o]+sum(c,i,j,(X[c,h+i,w+j]-zx)*(W[o,c,i,j]-zw[o])) exactly in INT32. Each zw is signed INT8, and zx is shared. Return Clip8(RNE(acc/2^shift[o])) with nearest-even ties. Output is NCHW [1,3,2,2], twelve signed byte lanes. Per-channel shifts are unsigned four-bit values 0..15; the output zero-point is fixed at zero. No padding, stride one, dilation one, no activation other than final saturation. Unlike a depthwise convolution, every output mixes both input channels. This is QLinearConv with x_scale=y_scale=1 and w_scale[o]=2^-shift[o].

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

All X=1, all W=1, zero bias/zero-points and shifts [0,1,2] produce four copies of 18, then four of 9, then four of 4 (4.5 rounds to even 4).

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

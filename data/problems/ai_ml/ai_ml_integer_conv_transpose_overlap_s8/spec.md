# ConvTransposeOverlapS8

Two-input/two-output-channel signed transposed convolution. Input X is NCHW [1,2,2,2] INT8, weights W are [2,2,3,3] INT8 indexed [input_channel,output_channel,kh,kw], and B is two INT16 biases. Stride is 2 in both axes; dilation is 1; padding and output_padding are zero; groups=1. Output Y is NCHW [1,2,5,5], all fifty signed INT32 values. Initialize each Y[c,y,x] to B[c]. Each X[k,h,w] contributes X[k,h,w]*W[k,c,i,j] to Y[c,2*h+i,2*w+j] for every output channel c and kernel index i,j. Overlaps add; kernels are not flipped, and biases are added once, not per contribution. No activation/requantization; all sums fit INT32. This fixed integer specialization retains the source transpose-convolution scatter and channel mixing.

## Interface

### RTL interface

```systemverilog
module ConvTransposeOverlapS8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [287:0] in_weights,
    input logic [31:0] in_bias,
    output logic [1599:0] out_values
);
endmodule
```

### Behavior interface

```python
class ConvTransposeOverlapS8:
    def reset(self): ...
    def process(self, in_data, in_weights, in_bias): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 1600.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Unused
padding bits do not exist. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`ConvTransposeOverlapS8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Two-input/two-output-channel signed transposed convolution. Input X is NCHW [1,2,2,2] INT8, weights W are [2,2,3,3] INT8 indexed [input_channel,output_channel,kh,kw], and B is two INT16 biases. Stride is 2 in both axes; dilation is 1; padding and output_padding are zero; groups=1. Output Y is NCHW [1,2,5,5], all fifty signed INT32 values. Initialize each Y[c,y,x] to B[c]. Each X[k,h,w] contributes X[k,h,w]*W[k,c,i,j] to Y[c,2*h+i,2*w+j] for every output channel c and kernel index i,j. Overlaps add; kernels are not flipped, and biases are added once, not per contribution. No activation/requantization; all sums fit INT32. This fixed integer specialization retains the source transpose-convolution scatter and channel mixing.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked packing example

A byte sequence `[1,2,3,4]` is packed as `0x04030201`; the low byte is element
zero. Signed byte `-1` is `0xff`. This example specifies packing, not a
restriction to small positive inputs.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

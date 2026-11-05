# ConvWeightBackward2x4

Weight and bias gradients for valid 3x3 convolution: one sample, two input and two output channels, 4x4 input, 2x2 output.

## Interface

### RTL interface

```systemverilog
module ConvWeightBackward2x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_x,
    input logic [63:0] in_grad,
    output logic [1183:0] out_values
);
endmodule
```

### Behavior interface

```python
class ConvWeightBackward2x4:
    def reset(self): ...
    def process(self, in_x, in_grad): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 1184.

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

`ConvWeightBackward2x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Weight and bias gradients for valid 3x3 convolution: one sample, two input and two output channels, 4x4 input, 2x2 output.

X[c,h,w] and upstream G[o,h,w] are signed INT8, flattened CHW. Return dW[o,c,kh,kw]=sum(h,w=0..1) G[o,h,w]*X[c,h+kh,w+kw], flattened OIHW, in 36 signed INT32 lanes. Bits 1152..1183 hold two signed INT16 db[o]=sum G[o,h,w]. There is no batch averaging, stride, padding, quantization or optimizer step. This is the full weight-gradient operator, not forward convolution.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

All X and G entries equal to 1 give every dW entry 4 and both bias gradients 4.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# DenseBackward2x4x3

Complete input, weight and bias gradients of a small dense layer.

## Interface

### RTL interface

```systemverilog
module DenseBackward2x4x3 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [95:0] in_weights,
    input logic [47:0] in_gradient,
    output logic [687:0] out_values
);
endmodule
```

### Behavior interface

```python
class DenseBackward2x4x3:
    def reset(self): ...
    def process(self, in_data, in_weights, in_gradient): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 688.

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

`DenseBackward2x4x3` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete input, weight and bias gradients of a small dense layer.

Forward Y=X*W^T+b has X[2,4], W[3,4], Y[2,3]. X,W and supplied upstream G=dL/dY are signed INT8. Return exact dX=G*W ([2,4] INT32), dW=G^T*X ([3,4] INT32) and db=sum_batch G ([3] INT16). All tensors are row-major. dX occupies bits 0..255, dW 256..639 and db 640..687. No averaging over batch, transpose omission, saturation or optimizer update is permitted. This is the linear backward chain rule evaluated on integer operands; gradients are exact integers, not INT8-quantized derivatives.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

All X, W and G entries one give every dX entry 3, every dW entry 2 and every db entry 2. No batch division by two is performed.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

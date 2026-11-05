# BinaryDense16x8

Eight-neuron bipolar binary dense layer with explicit scores and thresholds.

## Interface

### RTL interface

```systemverilog
module BinaryDense16x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_data,
    input logic [127:0] in_weights,
    input logic [63:0] in_thresholds,
    output logic [71:0] out_values
);
endmodule
```

### Behavior interface

```python
class BinaryDense16x8:
    def reset(self): ...
    def process(self, in_data, in_weights, in_thresholds): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 72.

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

`BinaryDense16x8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Eight-neuron bipolar binary dense layer with explicit scores and thresholds.

Each activation/weight bit represents +1 when one and -1 when zero. Weights are [8,16], row-major. Neuron o computes score=sum(k=0..15,X[k]*W[o,k])=2*popcount(X XNOR W[o,:])-16. Return eight signed INT8 score lanes in bits 0..63, and activation bit o at 64+o iff score>=signed_INT8(threshold[o]). Equality activates the neuron; no absolute value or unsigned score comparison is permitted. This specializes FINN bipolar interpretation and matrix-vector accumulation; dynamic weights/thresholds and diagnostic scores are explicit interface adaptations.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

All activation bits and weights zero mean all bipolar values are -1, so each score is +16, not zero. Threshold 16 activates; threshold 17 does not.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

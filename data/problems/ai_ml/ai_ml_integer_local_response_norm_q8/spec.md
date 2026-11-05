# LocalResponseNormQ8

Cross-channel local response normalization on NCHW [1,5,2,2] signed INT8 feature values.

## Interface

### RTL interface

```systemverilog
module LocalResponseNormQ8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [159:0] in_data,
    output logic [319:0] out_values
);
endmodule
```

### Behavior interface

```python
class LocalResponseNormQ8:
    def reset(self): ...
    def process(self, in_data): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 320.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

`LocalResponseNormQ8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Cross-channel local response normalization on NCHW [1,5,2,2] signed INT8 feature values.

For each channel c and spatial location p independently, S[c,p] is the sum of X[j,p]^2 for j=max(0,c-1)..min(4,c+1), inclusive. Missing edge channels contribute zero; the normalization constant does not change at channel edges. Return Y[c,p]=round_nearest_ties_away(256*16384*X[c,p]/(16384+S[c,p])). Output has the same NCHW order, twenty signed INT16 lanes in units 1/256. This is ONNX LRN with size=3, bias=1, alpha=3/16384 and beta=1, followed by explicit fixed-point rounding. There is no square root, subtraction of a channel mean, or spatial pooling. All twenty outputs are required. The denominator is always positive and all outputs fit INT16. The pinned source implementation has a loop-bound defect (batch count used instead of channel count); this task implements all channels according to the LRN operator definition, not that defect.

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

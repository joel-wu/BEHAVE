# ReverseSequence4x8

Reverse variable-length prefixes independently for four sequences of eight two-byte feature vectors.

## Interface

### RTL interface

```systemverilog
module ReverseSequence4x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [511:0] in_data,
    input logic [15:0] in_lengths,
    output logic [511:0] out_values
);
endmodule
```

### Behavior interface

```python
class ReverseSequence4x8:
    def reset(self): ...
    def process(self, in_data, in_lengths): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

`ReverseSequence4x8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Reverse variable-length prefixes independently for four sequences of eight two-byte feature vectors.

Data shape is [batch=4,time=8,channels=2], row-major. Four unsigned four-bit length fields are supplied. Effective length L[b]=min(in_lengths[b],8). For t<L[b], Y[b,t,c]=X[b,L[b]-1-t,c]; otherwise Y[b,t,c]=X[b,t,c]. Channel pairs never swap internally. Length zero is the identity. The clipping of encoded lengths 9..15 is an explicit total-domain extension of ONNX ReverseSequence; batch_axis=0 and time_axis=1. The pinned reference mishandles zero length; the task follows empty-prefix semantics, not that indexing defect.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For one sequence of feature pairs [A,B,C,D,E,F,G,H], length 3 produces [C,B,A,D,E,F,G,H]; length 0 leaves all eight pairs unchanged.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# KMeansCentroids8x4

Update four integer centroids from eight assigned four-dimensional INT8 observations.

## Interface

### RTL interface

```systemverilog
module KMeansCentroids8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_points,
    input logic [15:0] in_labels,
    input logic [7:0] in_mask,
    input logic [255:0] in_previous,
    output logic [271:0] out_values
);
endmodule
```

### Behavior interface

```python
class KMeansCentroids8x4:
    def reset(self): ...
    def process(self, in_points, in_labels, in_mask, in_previous): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 272.

Arguments and returned fields are nonnegative integers of their declared port
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

`KMeansCentroids8x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Update four integer centroids from eight assigned four-dimensional INT8 observations.

Each active mask bit selects one point; its two-bit label is the assigned cluster. Sum every active observation in its assigned cluster and divide each coordinate by the cluster count, rounding nearest with ties away from zero. Return sixteen signed INT16 centroid coordinates, cluster-major, then four unsigned four-bit counts at 256+4*c. An empty cluster retains its supplied previous INT16 centroid, not zero or a relocated point. Assignments are input, so this is the M-step/centroid reduction rather than nearest-center search. The empty-cluster retention and integer rounding are explicit adaptations of sklearn Lloyd center averaging; the source may relocate empty clusters and works in floating point.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Active points [0,1,-1,4] and [1,2,-2,7] assigned to cluster 0 yield [1,2,-2,6], count 2. Empty clusters retain their previous coordinates.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

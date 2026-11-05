# KNN3Vote8x4

Masked three-nearest-neighbor classification of eight four-dimensional INT8 points.

## Interface

### RTL interface

```systemverilog
module KNN3Vote8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_points,
    input logic [31:0] in_query,
    input logic [15:0] in_labels,
    input logic [7:0] in_mask,
    output logic [78:0] out_values
);
endmodule
```

### Behavior interface

```python
class KNN3Vote8x4:
    def reset(self): ...
    def process(self, in_points, in_query, in_labels, in_mask): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 79.

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

`KNN3Vote8x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Masked three-nearest-neighbor classification of eight four-dimensional INT8 points.

Use exact squared Euclidean distance to the signed INT8 query. Only points selected by mask participate. Select min(3,active_count) neighbors by increasing distance, ties by smaller original point index. Labels are unsigned two-bit classes 0..3. Uniform votes choose the greatest count, ties by smaller class label. Return three three-bit neighbor indices at 3*j, count at bits 9..10, four three-bit vote counts at 11+3*c, winning label at bits 23..24, and three unsigned 18-bit squared distances at 25+18*j. Unused slots are zero. An empty mask returns all zero, including count zero (not a valid zero-neighbor classification). This specializes sklearn KNeighborsClassifier uniform voting and makes equal-distance ordering deterministic.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

If all points/query are zero, all points active and labels start [2,1,2], indices [0,1,2] win equal-distance ties. Votes are [0,1,2,0], winner 2, and distances all zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

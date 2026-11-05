# SegmentSumMax8x4

Masked unsorted segment aggregation of eight four-channel signed INT8 records into four segments.

## Interface

### RTL interface

```systemverilog
module SegmentSumMax8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [15:0] in_segments,
    input logic [7:0] in_mask,
    output logic [399:0] out_values
);
endmodule
```

### Behavior interface

```python
class SegmentSumMax8x4:
    def reset(self): ...
    def process(self, in_data, in_segments, in_mask): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 400.

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

`SegmentSumMax8x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Masked unsorted segment aggregation of eight four-channel signed INT8 records into four segments.

Record i participates iff mask bit i is one, and its unsigned two-bit segment id selects segment 0..3. For every segment and channel return the exact signed sum (INT16) and signed maximum (INT8), plus record count. No participating record may be overwritten by a duplicate segment id. An empty segment has zero sums, zero maxima and count zero. Each segment occupies 100 output bits: four sum lanes at 100*s+16*c, four maximum lanes at 100*s+64+8*c, and its four-bit count at 100*s+96. Input flattening is 4*i+c. This composes ScatterElements add/max reductions and a count reduction, with explicit masking and empty-segment output rules.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Two active records [-3,1,4,2] and [-5,7,0,2] in segment 2 yield sum [-8,8,4,4], max [-3,7,4,2], count 2. All other segments are zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

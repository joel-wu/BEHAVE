# Forest3Depth2

Complete inference through three depth-two regression trees, including missing-feature branches.

## Interface

### RTL interface

```systemverilog
module Forest3Depth2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_features,
    input logic [3:0] in_missing,
    input logic [17:0] in_node_features,
    input logic [71:0] in_thresholds,
    input logic [8:0] in_default_left,
    input logic [191:0] in_leaves,
    output logic [63:0] out_values
);
endmodule
```

### Behavior interface

```python
class Forest3Depth2:
    def reset(self): ...
    def process(self, in_features, in_missing, in_node_features, in_thresholds, in_default_left, in_leaves): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 64.

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

`Forest3Depth2` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete inference through three depth-two regression trees, including missing-feature branches.

Features are four signed INT8 values. Each tree has internal nodes root=0, left child=1, right child=2, then leaves 0..3 in left-to-right order. Per internal node supply a two-bit feature index, signed INT8 threshold and one default-left bit; flatten node metadata as 3*tree+node. If the selected feature is marked missing, take the declared default branch, ignoring its payload. Otherwise go left iff feature<=threshold. Leaves are twelve signed INT16 values, index 4*tree+leaf. Return each selected leaf in lanes 0..2, followed by their mean rounded nearest, ties away from zero, in lane 3 (all lanes INT16). This is full tree traversal plus uniform forest aggregation, not a single tree-node test or training procedure.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Zero-valued present features and zero thresholds take both left branches. Leaves [1,2,3,4], [5,6,7,8], [9,10,11,12] therefore return [1,5,9,5].

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

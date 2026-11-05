# WeightedEmbeddingBags2

Weighted embedding-bag sums with repeated indices and independent variable-length bags.

## Interface

### RTL interface

```systemverilog
module WeightedEmbeddingBags2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_table,
    input logic [23:0] in_indices,
    input logic [63:0] in_weights,
    input logic [5:0] in_lengths,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class WeightedEmbeddingBags2:
    def reset(self): ...
    def process(self, in_table, in_indices, in_weights, in_lengths): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 256.

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

`WeightedEmbeddingBags2` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Weighted embedding-bag sums with repeated indices and independent variable-length bags.

The table has eight rows of four signed INT8 values. There are two bags, each with four stored slots. Each slot has a three-bit row index and a signed INT8 integer sample weight. Length b is min(4, the three-bit field at 3*b). Only its leading slots participate. Return sum_j weight[b,j]*table[index[b,j],c] in eight signed INT32 lanes, index 4*b+c. Repeated indices contribute repeatedly; empty bags produce zero. This specializes EmbeddingBag sum with per_sample_weights; lengths encode fixed-capacity offsets, not mean/max mode.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

With every table value 1, every weight 2, and lengths [4,2], bag outputs are [8,8,8,8] and [4,4,4,4].

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

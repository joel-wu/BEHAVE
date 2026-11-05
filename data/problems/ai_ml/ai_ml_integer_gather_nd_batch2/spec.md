# GatherNDBatch2

Batched coordinate gather from a [2,4,4,2] tensor of raw bytes.

## Interface

### RTL interface

```systemverilog
module GatherNDBatch2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [511:0] in_data,
    input logic [35:0] in_indices,
    output logic [95:0] out_values
);
endmodule
```

### Behavior interface

```python
class GatherNDBatch2:
    def reset(self): ...
    def process(self, in_data, in_indices): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 96.

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

`GatherNDBatch2` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Batched coordinate gather from a [2,4,4,2] tensor of raw bytes.

Input uses row-major indices [batch,row,column,channel]. Indices have shape [2,3,2]: for each batch and each of three requests, two signed three-bit values specify row and column. Every value -4..3 is legal: a negative coordinate adds four, independently for each axis. Return shape [2,3,2], preserving both channels, batch ownership, request order and repeated coordinates. Formally Y[b,q,c]=X[b,wrap(I[b,q,0]),wrap(I[b,q,1]),c]. No coordinate is broadcast across batches. This is ONNX GatherND with batch_dims=1 and two indexed axes; all element bits are preserved.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

If input bytes are 0..63, batch-0 coordinate (-1,-1) returns [30,31], while the same coordinate in batch 1 returns [62,63].

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

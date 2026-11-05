# LoRARank2Linear4

A complete rank-two low-rank adaptation added to a 4x4 base linear layer.

## Interface

### RTL interface

```systemverilog
module LoRARank2Linear4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_x,
    input logic [127:0] in_w,
    input logic [63:0] in_a,
    input logic [63:0] in_b,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class LoRARank2Linear4:
    def reset(self): ...
    def process(self, in_x, in_w, in_a, in_b): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 128.

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

`LoRARank2Linear4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

A complete rank-two low-rank adaptation added to a 4x4 base linear layer.

All input lanes are signed INT8. W is 4x4, A is 2x4, B is 4x2, row-major. Compute y=W*x+B*(A*x) with exact intermediates and return four signed INT32 lanes. LoRA rank r=2 and alpha=2, so alpha/r=1. Bias and dropout are absent. Neither the base path nor the residual may be omitted. This follows PEFT Linear.forward, specialized to integer inputs and one enabled adapter, not adapter training.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

If every x, W, A and B entry is 1, each hidden value is 4 and each output is 4+8=12.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# NonZeroCoordinates3x4

Compact all nonzero coordinates of a row-major [3,4] byte tensor.

## Interface

### RTL interface

```systemverilog
module NonZeroCoordinates3x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [95:0] in_data,
    output logic [51:0] out_values
);
endmodule
```

### Behavior interface

```python
class NonZeroCoordinates3x4:
    def reset(self): ...
    def process(self, in_data): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 52.

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

`NonZeroCoordinates3x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Compact all nonzero coordinates of a row-major [3,4] byte tensor.

Scan increasing row, then column; zero bytes are excluded and every nonzero bit pattern is included. Let n be the number found. The result reserves twelve unsigned two-bit row slots at bit 2*i, then twelve two-bit column slots at bit 24+2*i, then unsigned count n at bits 48..51. Slots i>=n are zero. This fixed-capacity representation of ONNX NonZero preserves its [2,n] axis-first coordinate layout. Coordinates, not flattened indices or values, are returned; empty input returns zero.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Nonzeros only at (0,3) and (2,1) yield rows [0,2], columns [3,1], count 2; all remaining coordinate slots are zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# PackedS4S8Dot16

Sixteen signed two's-complement INT4 weights w[i] occupy bits 4*i; sixteen signed INT8 activations x[i] occupy bits 8*i. Return the signed INT32 result bias+sum(w[i]*x[i]), bias is signed INT16. No overflow is possible. Low nibble precedes high nibble; all sixteen products contribute. This is the packed-weight dot-product core, not a full requantized layer.

## Interface

### RTL interface

```systemverilog
module PackedS4S8Dot16 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_weights,
    input logic [127:0] in_data,
    input logic [15:0] in_bias,
    output logic [31:0] out_values
);
endmodule
```

### Behavior interface

```python
class PackedS4S8Dot16:
    def reset(self): ...
    def process(self, in_weights, in_data, in_bias): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

`PackedS4S8Dot16` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Sixteen signed two's-complement INT4 weights w[i] occupy bits 4*i; sixteen signed INT8 activations x[i] occupy bits 8*i. Return the signed INT32 result bias+sum(w[i]*x[i]), bias is signed INT16. No overflow is possible. Low nibble precedes high nibble; all sixteen products contribute. This is the packed-weight dot-product core, not a full requantized layer.

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

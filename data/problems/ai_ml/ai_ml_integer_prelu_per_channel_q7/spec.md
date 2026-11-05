# PReLUPerChannelQ7

Apply PReLU to four pixels with four signed INT8 channels each, packed pixel-major. Channel c has its own unsigned 8-bit slope A[c]/128. For X>=0 return X. For X<0, round X*A[c]/128 to nearest integer with ties away from zero, then saturate to [-128,127]. The same four supplied slopes apply to every pixel. This retains PReLU sign selection and channel broadcasting; Q1.7 slopes, signed-byte saturation and explicit rounding replace source floating-point arithmetic.

## Interface

### RTL interface

```systemverilog
module PReLUPerChannelQ7 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    input logic [31:0] in_slopes,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class PReLUPerChannelQ7:
    def reset(self): ...
    def process(self, in_data, in_slopes): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 128.

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

`PReLUPerChannelQ7` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Apply PReLU to four pixels with four signed INT8 channels each, packed pixel-major. Channel c has its own unsigned 8-bit slope A[c]/128. For X>=0 return X. For X<0, round X*A[c]/128 to nearest integer with ties away from zero, then saturate to [-128,127]. The same four supplied slopes apply to every pixel. This retains PReLU sign selection and channel broadcasting; Q1.7 slopes, signed-byte saturation and explicit rounding replace source floating-point arithmetic.

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

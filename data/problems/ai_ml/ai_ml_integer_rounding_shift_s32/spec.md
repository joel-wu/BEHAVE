# RoundingShiftS32

Eight signed INT32 lanes, shared unsigned exponent 0..31. Output lane i is R(x[i],in_shift), not a plain arithmetic shift.

## Interface

### RTL interface

```systemverilog
module RoundingShiftS32 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [4:0] in_shift,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class RoundingShiftS32:
    def reset(self): ...
    def process(self, in_data, in_shift): ...
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

`RoundingShiftS32` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Eight signed INT32 lanes, shared unsigned exponent 0..31. Output lane i is R(x[i],in_shift), not a plain arithmetic shift.

Define H(a,b) for signed 32-bit a,b as follows: if both equal
-2^31, H=2^31-1. Otherwise set p=a*b in signed 64-bit precision and
n=2^30 for p>=0, or n=1-2^30 for p<0; H=trunc_toward_zero((p+n)/2^31).
Define R(x,e)=floor(x/2^e)+[r>t], with r=x mod 2^e in [0,2^e),
t=floor((2^e-1)/2)+[x<0]. This is nearest-integer rounding, ties away
from zero (including e=0 as the identity). These are two separate rounding
steps, not a fused division. Clip8 saturates to [-128,127].

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

# Adagrad4Q8

One four-parameter Adagrad update with an observable squared-gradient accumulator.

## Interface

### RTL interface

```systemverilog
module Adagrad4Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_params,
    input logic [63:0] in_gradient,
    input logic [127:0] in_accumulator,
    output logic [191:0] out_values
);
endmodule
```

### Behavior interface

```python
class Adagrad4Q8:
    def reset(self): ...
    def process(self, in_params, in_gradient, in_accumulator): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 192.

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

`Adagrad4Q8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

One four-parameter Adagrad update with an observable squared-gradient accumulator.

RNE is nearest integer with ties to even. Clip16 saturates to [-32768,32767]. All state is explicitly passed in and returned, not hidden between requests. p and g are signed INT16 Q8. v is unsigned INT32 in units 1/65536. Compute vnew=min(2^32-1,v+g*g), d=floor(sqrt(vnew))+1, delta=RNE(16*g/d), pnew=Clip16(p-delta). Output pnew[4] as INT16 at bits 0..63, then vnew[4] as UINT32 at 64+32*i. The learning rate is 1/16 and epsilon is 1/256. Integer-root flooring and saturating accumulator storage are explicit hardware adaptations. No weight decay, learning-rate decay or sparse-gradient duplicate coalescing is required.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For p=256, g=16, v=0, vnew=256, d=17 and delta=RNE(256/17)=15, so pnew=241. Zero gradient never divides by zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

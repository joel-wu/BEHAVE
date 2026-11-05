# MomentumSGD4Q8

One four-parameter momentum SGD update in Q8 storage.

## Interface

### RTL interface

```systemverilog
module MomentumSGD4Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_params,
    input logic [63:0] in_gradient,
    input logic [63:0] in_momentum,
    input logic [0:0] in_first,
    input logic [0:0] in_nesterov,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class MomentumSGD4Q8:
    def reset(self): ...
    def process(self, in_params, in_gradient, in_momentum, in_first, in_nesterov): ...
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

`MomentumSGD4Q8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

One four-parameter momentum SGD update in Q8 storage.

RNE is nearest integer with ties to even. Clip16 saturates to [-32768,32767]. All state is explicitly passed in and returned, not hidden between requests. Parameter p, gradient g and existing momentum m are signed INT16 raw values in units 1/256. Momentum coefficient is 3/4, learning rate 1/16, dampening and weight decay zero. If first=1 set mnew=g; otherwise mnew=Clip16(RNE((3*m+4*g)/4)). For ordinary momentum delta=RNE(mnew/16). For Nesterov delta=RNE((4*g+3*mnew)/64). Then pnew=Clip16(p-delta). Output pnew[4] followed by mnew[4], all INT16. Only the stated storage rounding/saturation differs from floating-point SGD; Nesterov uses the updated momentum, and first ignores the supplied old buffer.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

With p=256, g=16, first=1, ordinary momentum returns pnew=255 and mnew=16. Nesterov instead rounds 28/16 to 2 and returns pnew=254.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

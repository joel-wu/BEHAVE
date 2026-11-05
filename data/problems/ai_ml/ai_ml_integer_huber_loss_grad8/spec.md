# HuberLossGrad8

Summed Huber loss and all eight prediction gradients, in exact finite fixed-point form.

## Interface

### RTL interface

```systemverilog
module HuberLossGrad8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_prediction,
    input logic [127:0] in_target,
    input logic [7:0] in_delta_minus1,
    output logic [159:0] out_values
);
endmodule
```

### Behavior interface

```python
class HuberLossGrad8:
    def reset(self): ...
    def process(self, in_prediction, in_target, in_delta_minus1): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 160.

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

`HuberLossGrad8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Summed Huber loss and all eight prediction gradients, in exact finite fixed-point form.

Prediction/target are signed INT16 Q8; D=1+delta_minus1 is a positive raw Q8 threshold 1..256. For raw error e=prediction-target, twice-loss raw value is e^2 when |e|<=D, otherwise 2*D*|e|-D^2. Sum these eight UINT32 values without overflow and place the total in output bits 0..31. Its scale is twice the real summed loss times 65536. Prediction gradient raw value is clamp(e,-D,D), representing a Q8 gradient, returned as eight INT16 lanes at 32+16*i. There is no mean reduction. Exactly at |e|=D the formulas agree; target gradients would be negations but are not outputs. This is the ordinary Huber objective, not SmoothL1 rescaled by delta.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Raw errors [0,1,-1,2,-2,3,-3,0] with D=2 give twice-loss raw sum 26 and gradients [0,1,-1,2,-2,2,-2,0].

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# AlphaBetaTrackerQ16

A fixed-point alpha-beta tracker estimates position and velocity from successive
position measurements. It uses a constant-velocity prediction followed by two
residual corrections, with configurable gains on every accepted sample.

## Interface

### RTL interface

```systemverilog
module AlphaBetaTrackerQ16 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_measurement,
    input  logic [15:0] in_alpha,
    input  logic [15:0] in_beta,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_position,
    output logic [31:0] out_velocity
);
endmodule
```

### Behavior interface

```python
class AlphaBetaTrackerQ16:
    def reset(self): ...
    def process(self, in_measurement, in_alpha, in_beta): ...
```

Construct the model without arguments. Call `process` once per accepted input.
It returns one dictionary `{"out_position": ..., "out_velocity": ...}`.
Arguments and returned fields are Python integers holding unsigned port bit
patterns. Their numerical interpretation is specified below.

### Transaction protocol

An input transfers on a rising clock edge when `in_valid && in_ready`.
An output transfers when `out_valid && out_ready`. Each accepted input produces
exactly one output record, in acceptance order. The producer holds valid and
payload stable while stalled. The implementation may stall inputs and choose
its latency, but must not drop or duplicate results. Gaps and backpressure do
not advance the tracker: one accepted input represents one logical sample
interval, independently of the number of intervening hardware cycles.

## Modules

`AlphaBetaTrackerQ16` is the only required public module. No parameters,
external memories, floating-point units or shared numeric libraries are needed.

## Functional Behavior

The persistent state consists of signed 32-bit integers `p` and `v`, initially
zero. Position, measurement and velocity use 16 fractional bits: a raw value
of 65536 represents one position unit, or one position unit per sample for
velocity. Interpret `in_measurement` as a signed two's-complement integer `z`.
The unsigned gains `a = in_alpha` and `b = in_beta` represent `a/32768` and
`b/32768`, respectively. Thus 32768 is exactly one, and all 16-bit gain
patterns, including gains above one, are defined.

For each accepted sample, compute the following in order, using the state
from before that sample:

```text
predicted = p + v
residual  = z - predicted
p_raw     = predicted + floor(a * residual / 32768)
v_raw     = v         + floor(b * residual / 32768)
p_next    = max(-2147483648, min(2147483647, p_raw))
v_next    = max(-2147483648, min(2147483647, v_raw))
```

Only the final state updates saturate. Do not narrow or saturate the prediction,
residual, products or corrections. Both corrections use the same residual;
the velocity correction does not use the corrected position. Division rounds
toward negative infinity, so an arithmetic right shift by 15 implements it.
Signed 64-bit intermediates suffice for every input and reachable state.

Store `p_next` and `v_next`, and return their two's-complement bit patterns
in `out_position` and `out_velocity`. The sample interval is fixed at one.
There is no covariance update, adaptive gain computation or physical-clock
time integration. This is a finite-width adaptation of the scalar g-h filter
prediction/correction, also known as an alpha-beta tracker.

### Worked examples

Starting from reset, set `in_alpha=16384` and `in_beta=8192` (gains 1/2 and
1/4). Measurements 65536 and 131072 produce `(position, velocity)` raw values
`(32768, 16384)` and `(90112, 36864)`, respectively.

In a separate run from reset, measurement `0xFFFFFFFF` means raw value -1.
With the same gains, both outputs are `0xFFFFFFFF`: both negative fractional
corrections floor to -1, rather than truncate to zero.

## Reset Behavior

`rst` is synchronous and active high. A reset edge takes priority over all
transfers, restores both state values to zero, cancels in-flight results, and
clears `out_valid`. No pre-reset result may reappear after reset. The next
accepted input is processed from zero position and zero velocity.

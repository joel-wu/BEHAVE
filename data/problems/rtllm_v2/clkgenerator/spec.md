# ReferenceClockGenerator

Generate a continuous 50%-duty-cycle square wave from an external reference clock. This synthesizable adaptation of RTLLM's delay-based clock generator preserves alternating half-periods but measures time in reference-clock cycles instead of simulation delay units.

## Interface

### RTL interface

```systemverilog
module reference_clock_generator #(
    parameter int PERIOD = 10
) (
    input logic clk,
    input logic rst,
    output logic clk_out
);
```

### Behavior interface

```python
class ReferenceClockGenerator:
    def __init__(self, PERIOD=10): ...
    def reset(self): ...
    def process(self): ...
```

Construct the model with the RTL `PERIOD` value. Each `process()` call has no
arguments and represents one non-reset rising edge of `clk`: return exactly
`{"clk_out": level}`, with `level` an unsigned one-bit integer sampled immediately
before that edge, then advance state. An unchanged or zero output still returns
the dictionary, not `None` or an empty list.

Construction and `reset()` set phase and output level to zero. `reset()` returns
`None` without emitting a record; ordinary calls advance the retained divider
state rather than resetting it.

## Modules

- `reference_clock_generator`: reference-clock divider and square-wave output.

## Functional Behavior

There are no data inputs, valid/ready signals, or output backpressure.

`PERIOD` is an elaboration-time even integer in [2, 65536], default 10. A half-period lasts `PERIOD/2` reference-clock cycles. Starting with phase zero and output zero, increment the phase each non-reset rising edge. When the old phase equals `PERIOD/2 - 1`, clear it and toggle the output. Hold the output between rising edges. For `PERIOD=10`, pre-edge samples are five zeros, five ones, five zeros, and so on. The output runs continuously; observing or ignoring it does not enable, pause, or acknowledge the divider.

The upstream generator uses `#(PERIOD/2)` and has no input clock or reset. The external reference clock, synchronous reset, and even-period restriction are explicit adaptation choices. No equivalence to the upstream simulation-time interface or its same-time testbench sampling is claimed.

## Reset Behavior

`rst` is synchronous active high. Each rising edge with `rst=1` clears phase and `clk_out` to zero. Counting resumes from phase zero after deassertion.

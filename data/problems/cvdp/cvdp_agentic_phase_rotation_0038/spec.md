# control_fsm

Native five-phase signal-processing controller with capture and physical-cycle wait counts.

## Interface

### RTL interface

```systemverilog
module control_fsm #(parameter int NBW_WAIT=32)(
    input logic clk, rst_async_n,
    input logic i_enable, i_subsampling, i_valid, i_calc_valid, i_calc_fail,
    input logic [NBW_WAIT-1:0] i_wait,
    output logic o_subsampling, o_valid, o_start_calc
);
```

### Behavior interface

```python
class ControlFsm:
    def __init__(self, NBW_WAIT=32): ...
    def reset(self): ...
    def process(self, i_enable, i_subsampling, i_valid, i_calc_valid, i_calc_fail, i_wait): ...
```

Constructor `NBW_WAIT` matches the RTL parameter. Call `process` with all six
arguments on every released rising edge, including idle/invalid cycles. Return
exactly one dictionary with `o_subsampling`, `o_valid`, and `o_start_calc`, each
integer 0 or 1. Arguments are unsigned port encodings, including the
`NBW_WAIT`-bit wait count. The record observes pre-edge outputs, then the call
advances state. It is never `None` or a list. Reuse the instance across edges;
`reset()` clears state, returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each released rising edge
of `clk`, then advance that edge. Inputs and outputs are unsigned port bit
patterns. Idle cycles are clock edges too. Reset is controlled separately and
produces no logical records.

## Modules

- `control_fsm`: the complete native module above.

## Functional Behavior

NBW_WAIT > 3, default 32. All state transitions occur on each physical
rising edge, not on synthetic input/output transactions. The five phases are
CONTROL, CAPTURE, START, CALCULATE and WAITING. In CONTROL, an edge with i_enable
captures i_subsampling and i_wait and enters CAPTURE. Otherwise hold. Neither
configuration input is recaptured in later phases.

CAPTURE accepts exactly 8 samples, or 256 when the saved subsampling flag is one.
The combinational o_valid is one exactly in CAPTURE with i_valid=1. Invalid edges
hold the remaining count. The final valid edge enters START; all valid samples,
including that final sample, have o_valid=1 before their edge. Use at least nine
bits to represent the count 256. i_enable does not abort capture.

START lasts exactly 16 physical edges regardless of all inputs. o_start_calc is
one only during its first cycle. The 16th START edge enters CALCULATE. There,
i_calc_fail has priority and returns to CONTROL; otherwise i_calc_valid=1 enters
WAITING with the saved wait value N. With i_enable=1, WAITING consumes N+1 edges
(pre-edge counts N,...,0) then returns to CONTROL. i_enable=0 exits immediately.
o_subsampling continuously reports the saved flag. All outputs are zero in reset.

Source completions: success is active-HIGH, consistent with the port definition
and source checker rather than the contradictory word 'deasserted'. Exactly
256/8 valid captures and a one-cycle start pulse follow the prose; the original
checker instead uses 128 samples and a multi-cycle start signal. That checker is
not adopted as an oracle. The zero-count transition has no extra lost-valid
cycle. Native pins and asynchronous active-low reset are preserved.

## Reset Behavior

`rst_async_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

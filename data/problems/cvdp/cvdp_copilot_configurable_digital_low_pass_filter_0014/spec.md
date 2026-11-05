# fsm_linear_reg

A three-state controller with delayed signed arithmetic outputs and a single-cycle completion pulse.

## Interface

### RTL interface

```systemverilog
module fsm_linear_reg #(parameter int DATA_WIDTH = 16) (
    input logic clk, reset, start,
    input logic signed [DATA_WIDTH-1:0] x_in, w_in, b_in,
    output logic signed [2*DATA_WIDTH-1:0] result1,
    output logic signed [DATA_WIDTH:0] result2,
    output logic done
);
```

### Behavior interface

```python
class FsmLinearReg:
    def __init__(self, DATA_WIDTH=16): ...
    def reset(self): ...
    def process(self, start, x_in, w_in, b_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `done`: 1.
- `result1`: 2*DATA_WIDTH.
- `result2`: DATA_WIDTH+1.

Arguments x_in, w_in and b_in are signed Python integers in the DATA_WIDTH-bit range.
Returned result1 and result2 are signed Python integers of the listed widths, not
unsigned encodings. start and done are integers 0 or 1.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `fsm_linear_reg`: the complete native module above.

## Functional Behavior

`DATA_WIDTH >= 2`. Interpret x_in, w_in and b_in as DATA_WIDTH-bit
two's-complement integers. Compute `r1 = (w*x) >> 1` and `r2 = b + (x >> 2)`;
both shifts are arithmetic (round toward minus infinity). The result widths
are respectively 2*DATA_WIDTH and DATA_WIDTH+1, which retain the exact results.

An edge E0 in IDLE with start=1 enters COMPUTE. At E1, sample the CURRENT
operand pins and store the arithmetic results in internal buffers; start is
ignored. At E2, publish both results and return the controller to IDLE; done
is still zero. At E3, assert done for one cycle while retaining the results.
At E4, clear both results and done. All these timings refer to values AFTER
the named edge. Operands need only be stable for E1; they are not captured at E0.

Only edges whose pre-edge controller state is IDLE accept start. Therefore
E3 may start another computation, including when start is held high. It uses
E4 operands, publishes at E5 and pulses done at E6. A start in COMPUTE or DONE
is ignored, not queued. Without another start, outputs stay zero after E4.
There are no ready/valid pins, output backpressure or latency freedom.

## Reset Behavior

`reset` is asynchronous, active high. Assertion immediately restores the initial state. The state is IDLE and all arithmetic buffers, output registers and done are zero.
After reset release, resume the edge-by-edge behavior above.

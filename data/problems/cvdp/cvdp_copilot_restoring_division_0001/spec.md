# restoring_division

restoring_division with its native clocked interface.

## Interface

### RTL interface

```systemverilog
module restoring_division #(parameter int WIDTH = 6) (
    input logic clk, rst, start,
    input logic [WIDTH-1:0] dividend, divisor,
    output logic [WIDTH-1:0] quotient, remainder,
    output logic valid
);
```

### Behavior interface

```python
class RestoringDivision:
    def __init__(self, WIDTH=6): ...
    def reset(self): ...
    def process(self, start, dividend, divisor): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `quotient`, `remainder`: WIDTH.
- `valid`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

- `restoring_division`: the complete native module above.

## Functional Behavior

`WIDTH>=1`, default 6. At an idle rising edge with start=1, capture both
operands, clear internal quotient/remainder and begin WIDTH restoring iterations.
On each of the next WIDTH edges, shift the partial remainder left, append the
next dividend bit MSB first, and subtract the unsigned divisor only if it fits.
Append 1 to the working quotient when it fits, otherwise append 0. Use WIDTH+1
bits for the trial remainder; do not use a truncated sign bit as unsigned compare.

The final iteration publishes quotient/remainder and asserts valid for one
cycle. Both outputs hold the previous result until publication. Busy start
pulses and changing operands are ignored; the next idle edge can accept a new
start. Completion is exactly WIDTH edges AFTER capture, regardless of whether
WIDTH is a power of two; this resolves the source's inconsistent cycle wording.
Width one is supported without invalid slices.

Original inputs were nonzero with dividend>=divisor. Extend explicitly to all
bit patterns: ordinary unsigned division for nonzero divisor, including zero
dividend and dividend<divisor; divisor zero returns an all-ones quotient and
the dividend as remainder after the same WIDTH iterations. This is the natural
result of the specified unsigned restoring recurrence, not a divide exception.

## Reset Behavior

`rst` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

# TopModule

Overlapping Mealy 101 detector.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input aresetn,
  input x,
  output reg z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `z`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Overlapping Mealy 101 detector. z is combinationally high when the preceding two sampled bits were 10 and the current x is 1. Thus 10101 produces two detections. Reset discards the prefix; no output register is added.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`aresetn` is asynchronous, active low. Assertion immediately restores the initial state. No prefix; z=0.
After reset release, resume the edge-by-edge behavior above.

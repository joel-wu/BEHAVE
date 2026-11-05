# TopModule

Four-stage shift register.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input resetn,
  input value,
  output out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Four-stage shift register. Shift input value into the low bit each rising edge; out continuously exposes the oldest bit, q[3]. Thus a sampled bit propagates through four flip-flops. Active-low synchronous reset clears all stages.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`resetn` is synchronous, active low. Only a rising edge of `clk` with reset asserted restores the initial state. All four stages zero.
After reset release, resume the edge-by-edge behavior above.

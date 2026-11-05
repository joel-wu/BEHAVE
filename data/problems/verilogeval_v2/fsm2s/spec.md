# TopModule

Two-state Moore FSM: OFF outputs 0 and enters ON iff j=1; ON outputs 1 and enters OFF iff k=1.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input j,
  input k,
  input reset,
  output out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, j, k): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

Two-state Moore FSM: OFF outputs 0 and enters ON iff j=1; ON outputs 1 and enters OFF iff k=1. With j=k=1 it toggles each edge.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The state is OFF; out=0.
After reset release, resume the edge-by-edge behavior above.

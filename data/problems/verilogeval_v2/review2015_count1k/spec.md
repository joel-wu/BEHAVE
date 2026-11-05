# TopModule

Count 0 through 999 inclusive, then wrap to 0, with period 1000 rising edges.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  output reg [9:0] q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 10.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
A no-argument process() call still advances one clock edge.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Count 0 through 999 inclusive, then wrap to 0, with period 1000 rising edges. `q` continuously exposes the ten-bit current count. Reset has priority. No input event or output readiness gates the counter.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The complete register and output reset to zero.
After reset release, resume the edge-by-edge behavior above.

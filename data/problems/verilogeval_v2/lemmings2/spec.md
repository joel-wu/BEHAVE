# TopModule

Start walking left.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input areset,
  input bump_left,
  input bump_right,
  input ground,
  output walk_left,
  output walk_right,
  output aaah
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, bump_left, bump_right, ground): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `aaah`, `walk_left`, `walk_right`: 1.

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

Start walking left. Bumping in the walking direction reverses direction; simultaneous bumps still reverse. Loss of ground takes priority and starts falling, retaining direction. Falling ignores bumps, including at landing; ground restores walking in the remembered direction. Outputs are Moore indicators of current activity.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`areset` is asynchronous, active high. Assertion immediately restores the initial state. Walking left; all other activity outputs are zero.
After reset release, resume the edge-by-edge behavior above.

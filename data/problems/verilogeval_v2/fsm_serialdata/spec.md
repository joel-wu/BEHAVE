# TopModule

Sample serial bits, idle 1, start 0, eight data bits least-significant first, then stop 1.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input value,
  input reset,
  output [7:0] out_byte,
  output done
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

- `done`: 1.
- `out_byte`: 8.

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

Sample serial bits, idle 1, start 0, eight data bits least-significant first, then stop 1. Pulse done in the cycle following a valid stop. A bad stop enters error recovery: ignore zeros until a one, then search for a NEW start; that resynchronizing one does not emit done. Back-to-back frames are supported, including a start sampled during done. out_byte is the captured data when done=1; explicitly zero otherwise, completing the source don't-care.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. Idle search; done=0. Invalid out_byte=0; hidden shift history is overwritten before the next valid frame.
After reset release, resume the edge-by-edge behavior above.

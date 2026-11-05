# TopModule

Capture sticky falling edges while preserving input history across a partial clear.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, clear, input logic [31:0] value, output logic [31:0] out);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clear, value): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 32.

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

For each of 32 bits independently, remember whether a falling
transition (previous input=1, current input=0) has occurred. On every rising
edge, capture the current value as the new previous sample. If `clear=1`, clear
all sticky output bits; otherwise OR in the falling-transition bits computed
from OLD history. `out` directly exposes the sticky register and holds between
edges. A rise cannot set a bit; an ordinary input cannot clear a captured bit.

Importantly, history is captured EVEN WHEN clear=1. For example, an edge with
clear=1,value=0x20 clears out but records 0x20; the next edge with clear=0,value=0
sets out bit5. Clearing history too would violate the supplied reference.
Clear has priority over detecting a falling edge at that same edge.

### Explicit boundary adaptation

The source's synchronous `reset` clears only the sticky result, not the input
history. It is renamed `clear` and remains an ordinary per-edge input, preserving
that partial-reset behavior. Source port `in` is renamed `value` in the adapted interface.
The added synchronous high `init_reset` clears both registers for deterministic
initialization and overrides clear/value at that edge. With init_reset=0,
the transition is exactly the source reference with reset=clear. There is no
ready/valid gate or extra output register. Before initialization, stored values
are unspecified.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

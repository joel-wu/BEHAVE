# TopModule

One bit Q updates on every rising edge.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, w, R, E, L, output logic Q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, w, R, E, L): ...
```

Each `process(w, R, E, L)` call returns exactly `{"Q": stored_bit}` with the
settled output immediately BEFORE one rising edge of `clk`, then advances
that edge. All arguments and `stored_bit` are integers 0 or 1. Every call,
including a hold cycle, returns this dictionary, never `None` or a list.
The stored bit persists between calls. `reset()` initializes it to zero,
returns `None` and emits no output record; reset is controlled separately
from the process arguments.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

One bit Q updates on every rising edge. L=1 loads R, otherwise E=1 captures w, otherwise Q holds. Load wins even when L=E=1. The output is the stored bit itself; there is no extra result register.

### Explicit initialization and source fidelity

The source has no reset pin and leaves its power-up state unspecified (the
DFF reference explicitly uses X). This task adds synchronous active-high
`init_reset`: a rising edge with init_reset=1 clears ALL stored state to zero.
Before the first initialization edge, stored values are unspecified. This
chooses a deterministic startup convention; it is not an original zero-state
requirement or preservation of every possible upstream initial state.
Reasserting init_reset begins a new initialized execution segment. On released
edges, retain the original transition equations, widths and live outputs.
No ready/valid pins, acceptance-gated clocking or output queue are present.


## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All stored state is zero. Stored output registers are zero; live outputs follow their stated equations.
After reset release, resume the edge-by-edge behavior above.

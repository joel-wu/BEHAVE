# TopModule

A ten-bit unsigned countdown register updates every rising edge: load=1 replaces it with data, otherwise a nonzero count decrements by one, otherwise it stays zero.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, load, input logic [9:0] data, output logic tc);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, load, data): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `tc`: 1.

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

A ten-bit unsigned countdown register updates every rising edge: load=1 replaces it with data, otherwise a nonzero count decrements by one, otherwise it stays zero. Loading always wins and can interrupt an active countdown. The live output tc is one exactly when the stored count is zero, including after initialization. data has no effect when load=0. Preserve the full 0..1023 load range. The original stray one-bit-register sentence does not change this explicitly described ten-bit counter.

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

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All stored state is zero. tc is 1.
After reset release, resume the edge-by-edge behavior above.

# TopModule

A 64-bit shift register loads data whenever load=1.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, load, input logic ena, input logic [1:0] amount, input logic [63:0] data, output logic [63:0] q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, load, ena, amount, data): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 64.

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

A 64-bit shift register loads data whenever load=1. Otherwise ena=1 selects: amount=00 left by 1, 01 left by 8, 10 arithmetic right by 1, 11 arithmetic right by 8. ena=0 holds. Left shifts discard overflow and zero-fill; right shifts replicate OLD bit 63 into every vacated high position. Ports carry unsigned bit patterns even when that sign bit is one. Load overrides shifting. q is the stored register itself.

### Explicit initialization adaptation

The source defines parallel load but no reset or deterministic power-up state.
This version adds synchronous active-high init_reset, clearing the state to
zero and taking priority over load. Before the first initialization edge,
state is unspecified. Original load, every-clock evolution, dimensions and
output timing are retained after initialization. Reassertion starts a new
initialized segment. This is a disclosed interface/startup completion, not
unchanged upstream pin compatibility. No ready/valid wrapper is introduced.


## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The full-width q register is zero.
After reset release, resume the edge-by-edge behavior above.

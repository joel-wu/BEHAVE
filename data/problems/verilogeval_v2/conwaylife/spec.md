# TopModule

Maintain a 16 by 16 toroidal Game of Life grid, bit 16*r+c for row r and column c.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, load, input logic [255:0] data, output logic [255:0] q);
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

- `q`: 256.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

Maintain a 16 by 16 toroidal Game of Life grid, bit 16*r+c for row r and column c. Both row and column coordinates wrap modulo 16. Each cell has the eight distinct surrounding neighbors. On each rising edge with load=0, compute ALL next cells from the old grid: exactly three live neighbors gives 1, exactly two retains the old cell, all other counts (including eight) give 0. load=1 replaces all 256 bits with data instead of evolving. q directly exposes the grid; every released non-load edge advances one generation.

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

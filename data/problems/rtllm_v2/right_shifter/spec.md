# right_shifter

Original physical-clock computation, with an explicitly added initialization reset.

## Interface

### RTL interface

```systemverilog
module right_shifter(input logic clk, init_reset, d, output logic [7:0] q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, d): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 8.

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

- `right_shifter`: the complete native module above.

## Functional Behavior

An eight-bit shift register starts at zero. At every rising edge insert d into bit 7 and move old bits 7..1 into bits 6..0. Discard old bit 0. The output q is the stored byte, continuously visible; no extra output register or enable. After eight captures, q contains the last eight bits with the newest at the MSB.

The original initializes its register(s) to zero with `initial`, with no reset
port. This version explicitly replaces that initializer with synchronous
active-high `init_reset`, establishing the same state at an initialization
edge. The extra pin is a disclosed BEHAVE interface adaptation; values before
the first initialization edge are unspecified. Each released execution segment
keeps the original per-clock computation and output timing. Reassertion starts
another segment. There is no ready/valid, input acceptance condition or output
buffer that can pause clock progress. Unchanged upstream pin compatibility is
not claimed; archived-reference replay uses only the same initializer-to-reset
edit (and the documented top-module name).


## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All state registers and outputs are zero.
After reset release, resume the edge-by-edge behavior above.

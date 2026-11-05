# square_wave

Original physical-clock computation, with an explicitly added initialization reset.

## Interface

### RTL interface

```systemverilog
module square_wave(input logic clk, init_reset, input logic [7:0] freq, output logic wave_out);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, freq): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `wave_out`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `square_wave`: the complete native module above.

## Functional Behavior

Both the eight-bit counter and wave_out start at zero. At each
rising edge, compare the OLD counter with the current unsigned freq minus one.
If freq>0 and count==freq-1, clear count and invert wave_out. Otherwise increment
count modulo 256, retaining wave_out. Frequency changes take effect on that
edge WITHOUT resetting or saturating the count. Equality is required, not >=.

For constant freq=n>0 starting at zero, toggle every n clocks. For freq=0,
never toggle; the counter still wraps every 256 clocks. This preserves the
original RTL's unsized `freq - 1` comparison: the zero case produces a 32-bit
all-ones target, which an eight-bit count cannot reach. The prior adaptation's
eight-bit threshold255 and resulting zero-frequency toggles were incorrect.
If freq becomes nonzero after such an interval, use the accumulated count;
do not silently restart its period. Output changes only at active edges.


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

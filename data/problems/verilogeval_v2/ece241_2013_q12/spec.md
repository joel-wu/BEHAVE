# TopModule

An eight-bit register q is a serially loaded truth table.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, enable, S, A, B, C, output logic Z);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, enable, S, A, B, C): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `Z`: 1.

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

- `TopModule`: the complete native module above.

## Functional Behavior

An eight-bit register q is a serially loaded truth table. At each rising edge with enable=1, shift q left one position and insert S at bit 0; otherwise retain all eight bits. The live combinational output is Z=q[(A<<2)|(B<<1)|C]; A is the most-significant address bit. Changing A/B/C changes the selected output even when enable=0. Do not add an output register. All eight stored bits initialize to zero.

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

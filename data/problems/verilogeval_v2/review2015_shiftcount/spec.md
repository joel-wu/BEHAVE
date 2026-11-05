# TopModule

A four-bit register updates on each rising edge.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, shift_ena, count_ena, data, output logic [3:0] q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, shift_ena, count_ena, data): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 4.

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

A four-bit register updates on each rising edge. shift_ena=1 shifts left and inserts data at bit 0, discarding the former bit 3. Otherwise count_ena=1 decrements modulo 16; 0 decrements to 15. Otherwise hold. If both enables are high, shifting wins, following the supplied reference and completing the prompt's unspecified simultaneous case. q exposes the register directly.

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

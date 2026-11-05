# TopModule

Source-faithful every-clock register circuit with an explicitly added initialization reset.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, input logic [7:0] d, output logic [7:0] q);
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

Eight independent D flip-flops capture d[7:0] at every rising edge and hold q between edges. No enable, output stall or extra register is present. The original reference explicitly initializes q to zero.

### Explicit initialization-interface adaptation

The original circuit has no reset pin and specifies initial zero state in its
prompt or supplied reference. This version replaces that startup initializer
with synchronous active-high `init_reset`. A rising edge with init_reset=1
establishes exactly the same zero state; subsequent released edges retain the
original transition equations and observable timing. Reassertion begins a new
initialized execution segment. Values before the first initialization edge
are unspecified. This is an intentional interface extension for the current
BEHAVE-Sim clock contract, not a claim that the source contained reset or that
the unchanged upstream testbench is directly compatible. No ready/valid pins,
acceptance-gated clock updates or transaction-result registers are introduced.


## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All flip-flops are zero; the output is zero.
After reset release, resume the edge-by-edge behavior above.

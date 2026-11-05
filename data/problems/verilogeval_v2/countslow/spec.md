# TopModule

On a rising edge, increment `q` modulo 10 only if `slowena=1`; otherwise hold.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input slowena,
  input reset,
  output reg [3:0] q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, slowena): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 4.

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

On a rising edge, increment `q` modulo 10 only if `slowena=1`; otherwise hold. The output continuously exposes the current count. Reset clears to zero even if `slowena=0`, and has priority over increment.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The complete register and output reset to zero.
After reset release, resume the edge-by-edge behavior above.

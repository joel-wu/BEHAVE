# TopModule

Free-running four-digit BCD counter.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  output [3:1] ena,
  output reg [15:0] q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ena`: 3.
- `q`: 16.

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

Free-running four-digit BCD counter. Every non-reset rising edge increments the decimal count, wrapping 9999 to 0000. q holds the CURRENT count; ena[1], ena[2], ena[3] are combinationally high when the current ones, low two, or low three digits are all 9, respectively. Thus q=0009 has ena=001; after the next edge q=0010 and ena=000. There is no input increment transaction or output backpressure.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. q=0000 and ena=000 after the reset edge.
After reset release, resume the edge-by-edge behavior above.

# TopModule

12-hour clock: increment one second per edge with ena=1; hold otherwise.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input ena,
  output reg pm,
  output reg [7:0] hh,
  output reg [7:0] mm,
  output reg [7:0] ss
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, ena): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `hh`, `mm`, `ss`: 8.
- `pm`: 1.

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

12-hour clock: increment one second per edge with ena=1; hold otherwise. Outputs hh,mm,ss are two-digit BCD, with hours 01..12 and minutes/seconds 00..59. Toggle pm at 11:59:59 -> 12:00:00, not at 12:59:59 -> 01:00:00. Reset has priority over enable.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. 12:00:00 AM: pm=0, hh=0x12, mm=ss=0.
After reset release, resume the edge-by-edge behavior above.

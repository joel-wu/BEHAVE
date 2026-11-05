# TopModule

On each rising edge with `resetn=1`, replace `q[7:0]` with `d[7:0]` iff `byteena[0]=1`, and independently replace `q[15:8]` with `d[15:8]` iff `byteena[1]=1`.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input resetn,
  input [1:0] byteena,
  input [15:0] d,
  output reg [15:0] q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, byteena, d): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

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

On each rising edge with `resetn=1`, replace `q[7:0]` with `d[7:0]` iff `byteena[0]=1`, and independently replace `q[15:8]` with `d[15:8]` iff `byteena[1]=1`. Disabled bytes retain their previous contents. `byteena=00` holds the whole word; `11` replaces both bytes. Active-low synchronous reset dominates both enables.

## Reset Behavior

`resetn` is synchronous, active low. Only a rising edge of `clk` with reset asserted restores the initial state. The complete register and output reset to zero.
After reset release, resume the edge-by-edge behavior above.

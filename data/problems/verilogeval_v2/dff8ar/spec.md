# TopModule

Capture all eight bits of `d` on each rising edge while `areset=0`.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input [7:0] d,
  input areset,
  output reg [7:0] q
);
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

Capture all eight bits of `d` on each rising edge while `areset=0`. Otherwise retain the stored byte between clock edges. Asserting `areset` clears the byte immediately, without waiting for an edge, and dominates capture.

## Reset Behavior

`areset` is asynchronous, active high. Assertion immediately restores the initial state. The complete register and output reset to zero.
After reset release, resume the edge-by-edge behavior above.

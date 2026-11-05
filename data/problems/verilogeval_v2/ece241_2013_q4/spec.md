# TopModule

Reservoir controller with three ordered level sensors s[0] (low), s[1], s[2] (high).

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input [2:0] s,
  output reg fr2,
  output reg fr1,
  output reg fr0,
  output reg dfr
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, s): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `dfr`, `fr0`, `fr1`, `fr2`: 1.

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

Reservoir controller with three ordered level sensors s[0] (low), s[1], s[2] (high). Six Moore states are L=0, ML-rising=1, ML-falling=2, MH-rising=3, MH-falling=4, H=5. Their (fr2,fr1,fr0,dfr) outputs are 1111,0110,0111,0010,0011,0000. Transitions: L goes to ML-rising iff s[0]. Either ML state goes to MH-rising if s[1], else holds if s[0], else L. Either MH state goes to H if s[2], else holds if s[1], else ML-falling. H holds if s[2], else goes to MH-falling. Conditions are ordered as written, defining skipped levels and non-thermometer patterns too. The source prose reverses the history comparison: follow its reference implementation, enabling supplemental flow after FALLING into an intermediate band, not after rising. Stay in that history state while the level is unchanged.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. State L, all four flow outputs 1.
After reset release, resume the edge-by-edge behavior above.

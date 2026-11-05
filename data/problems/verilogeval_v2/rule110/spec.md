# TopModule

Maintain all 512 cells q[511:0].

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, load, input logic [511:0] data, output logic [511:0] q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, load, data): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 512.

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

Maintain all 512 cells q[511:0]. On every rising edge, load=1 replaces q with data; otherwise advance one Rule 110 generation. For cell i, left=q[i+1], center=q[i], right=q[i-1], with q[512]=q[-1]=0 (fixed boundaries, NOT wraparound). Neighborhoods 111,110,101,100,011,010,001,000 map respectively to 0,1,1,0,1,1,1,0. All cells use the same old state; q exposes the current grid directly.

### Explicit initialization adaptation

The source defines parallel load but no reset or deterministic power-up state.
This version adds synchronous active-high init_reset, clearing the state to
zero and taking priority over load. Before the first initialization edge,
state is unspecified. Original load, every-clock evolution, dimensions and
output timing are retained after initialization. Reassertion starts a new
initialized segment. This is a disclosed interface/startup completion, not
unchanged upstream pin compatibility. No ready/valid wrapper is introduced.


## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The full-width q register is zero.
After reset release, resume the edge-by-edge behavior above.

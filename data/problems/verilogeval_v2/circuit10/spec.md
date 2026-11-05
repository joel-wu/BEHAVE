# TopModule

One carry state bit c updates on each rising edge to (a AND b) OR (a AND c) OR (b AND c), using the old c.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, a, b, output logic q, state);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Call `process(a, b)` on each released rising edge. Return exactly one dictionary
with `q` and `state`, each integer 0 or 1; both arguments are also one-bit
integers. The record uses the current inputs and pre-edge carry, then the call
updates carry. It is never `None` or a list. Reuse the instance across edges.
`reset()` clears carry, returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each released rising edge
of `clk`, then advance that edge. Inputs and outputs are unsigned port bit
patterns. Idle cycles are clock edges too. Reset is controlled separately and
produces no logical records.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

One carry state bit c updates on each rising edge to (a AND b) OR (a AND c) OR (b AND c), using the old c. The live outputs are q=a XOR b XOR c and state=c. Neither output has an extra register. Initialize c=0; then q still follows a XOR b, including during reset.

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

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All stored state is zero. state is 0; q still equals a XOR b.
After reset release, resume the edge-by-edge behavior above.

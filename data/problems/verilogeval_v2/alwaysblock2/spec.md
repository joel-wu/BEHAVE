# TopModule

out_assign and out_always_comb both continuously equal the current a XOR b, including while init_reset is high.

## Interface

### RTL interface

```systemverilog
module TopModule (input logic clk, init_reset, a, b, output logic out_assign, out_always_comb, out_always_ff);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Each call returns one dictionary with exactly `out_assign`, `out_always_comb`,
and `out_always_ff`, immediately BEFORE one non-reset rising edge of `clk`,
then advances that edge. Arguments and results are unsigned one-bit integers.
The first two fields use the current `a` and `b`; the third observes the stored
value before the update. Every call returns all three fields, even for repeated
inputs or zero outputs, and represents an edge rather than a valid-qualified
transaction. Keep the same instance between edges. `reset()` returns `None`
and clears the stored flip-flop; it produces no logical output record.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

out_assign and out_always_comb both continuously equal the current a XOR b, including while init_reset is high. out_always_ff captures a XOR b on each released rising edge and holds between edges. Only that flip-flop is reset to zero; the combinational outputs are not reset or clocked.

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

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All stored state is zero. The two combinational outputs still equal a XOR b.
After reset release, resume the edge-by-edge behavior above.

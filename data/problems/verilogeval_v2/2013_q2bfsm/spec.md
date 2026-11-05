# TopModule

After the first released edge, pulse f for one cycle.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input resetn,
  input x,
  input y,
  output f,
  output g
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x, y): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Supply `x` and `y` as Python integers
0 or 1 on every call. Return exactly one dictionary with keys `f` and `g`,
both integers 0 or 1, including when both are zero; do not return `None` or a
list. State persists between calls. `reset()` restores state A and returns no
response. Reset edges are controlled separately and produce no logical records.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

After the first released edge, pulse f for one cycle. Then search x for consecutive 101 (extra leading ones retain the 1 prefix). On detection, assert g for the next two cycles. If y is sampled high at either of those two edges, retain g=1 until reset; otherwise retain g=0 until reset. x is ignored after detection. f and g are Moore outputs.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`resetn` is synchronous, active low. Only a rising edge of `clk` with reset asserted restores the initial state. Reset state A; f=g=0.
After reset release, resume the edge-by-edge behavior above.

# TopModule

Four-state Moore arbiter.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input resetn,
  input  [2:0] r,
  output [2:0] g
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, r): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Arguments and outputs are unsigned port bit patterns.
Every call represents a clock edge, including edges with `r=0`.
Reset is controlled separately and produces no logical records.

Return one `dict` with exactly `g`. The argument `r` is a Python integer in
0..7 encoding the three request bits; `g` is an integer in {0, 1, 2, 4}
encoding the current grant bits. No grant is `{"g": 0}`, never `None` or an
empty list. State persists across calls. `reset()` restores idle state A and
produces no record. The returned grant precedes the transition driven by `r`.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Four-state Moore arbiter. In idle A, g=000; choose B for r[0], else C for r[1], else D for r[2], else remain A. B/C/D assert only g[0]/g[1]/g[2] respectively and retain ownership while that same request remains asserted. When it falls, return to A; do not grant another requester on the release edge. This completes the source diagram: its second A-to-D label incorrectly duplicated the no-request condition and its D transitions were missing. Use separate combinational next-state/output logic and a clocked state-register block.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`resetn` is synchronous, active low. Only a rising edge of `clk` with reset asserted restores the initial state. State A, no grant.
After reset release, resume the edge-by-edge behavior above.

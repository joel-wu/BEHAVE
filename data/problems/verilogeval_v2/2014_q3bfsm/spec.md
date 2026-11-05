# TopModule

The Moore output describes the CURRENT state, not the state selected by the current input.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input x,
  output reg z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Supply `x` as the Python integer 0 or 1
on every call. Return exactly one dictionary with key `z`, also an integer
0 or 1, even when zero; do not return `None` or a list. State persists across
calls. `reset()` restores state 0 and returns no response. Reset edges are
controlled separately and produce no logical records.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

The Moore output describes the CURRENT state, not the state selected by the current input. State encodings in this table specify behavior, not a required hardware encoding.

| Present state | x=0 | x=1 | z |
| --- | --- | --- | --- |
| 0 | 0 | 1 | 0 |
| 1 | 1 | 4 | 0 |
| 2 | 2 | 1 | 0 |
| 3 | 1 | 2 | 1 |
| 4 | 3 | 4 | 1 |

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The FSM starts in state 0; z=0.
After reset release, resume the edge-by-edge behavior above.

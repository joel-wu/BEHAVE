# TopModule

The Moore output describes the CURRENT state, not the state selected by the current input.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input w,
  output z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, w): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Arguments and outputs are unsigned port bit patterns.
Every call represents a clock edge, including edges with `w=0`.
Reset is controlled separately and produces no logical records.

Return one `dict` with exactly `z`, a Python integer in 0..1. A zero output is
`{"z": 0}`, never `None` or an empty list. The argument `w` is an integer in
0..1. State persists between calls; `reset()` restores state 0 without returning
a record. In particular, the return value precedes the transition driven by `w`.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

The Moore output describes the CURRENT state, not the state selected by the current input. State encodings in this table specify behavior, not a required hardware encoding.

| Present state | w=0 | w=1 | z |
| --- | --- | --- | --- |
| 0 | 0 | 1 | 0 |
| 1 | 3 | 2 | 0 |
| 2 | 3 | 4 | 0 |
| 3 | 0 | 5 | 0 |
| 4 | 3 | 4 | 1 |
| 5 | 3 | 2 | 1 |

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

Use separate combinational next-state/output logic and a clocked state-register block.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The FSM starts in state 0; z=0.
After reset release, resume the edge-by-edge behavior above.

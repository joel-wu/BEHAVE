# always_if

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  input sel_b1,
  input sel_b2,
  output out_assign,
  output reg out_always
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, sel_b1, sel_b2): ...
```

Each `process` call returns exactly `{"out_assign": value, "out_always": value}`,
with both fields present even when zero. All four arguments and both results
are unsigned one-bit integers. The return is never `None` or a list, and no
state persists across calls. Construction and `reset()` have no effect;
`reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

Both outputs equal b exactly when `sel_b1=sel_b2=1`; otherwise they equal a. Use a continuous assignment for `out_assign` and a fully assigned combinational always block for `out_always`.

## Reset Behavior

There is no RTL reset or persistent state.

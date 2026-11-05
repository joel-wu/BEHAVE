# 7458

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input p1a,
  input p1b,
  input p1c,
  input p1d,
  input p1e,
  input p1f,
  output p1y,
  input p2a,
  input p2b,
  input p2c,
  input p2d,
  output p2y
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, p1a, p1b, p1c, p1d, p1e, p1f, p2a, p2b, p2c, p2d): ...
```

Each call evaluates one input assignment after combinational settling and
returns exactly `{"p1y": first_output, "p2y": second_output}`. All arguments
and returned values are integers in 0..1. Repeated identical inputs still
produce complete dictionaries, never `None` or a list. There is no model state;
construction and `reset()` have no effect on the function, and `reset()`
returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

`p1y=(p1a & p1b & p1c) | (p1d & p1e & p1f)`;
`p2y=(p2a & p2b) | (p2c & p2d)`. The two groups are independent.

## Reset Behavior

There is no RTL reset or persistent state.

# 2014_q3c

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input x,
  input [2:0] y,
  output reg Y0,
  output reg z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x, y): ...
```

Each `process(x, y)` call evaluates one input assignment after combinational
settling. `x` is integer `0` or `1` and `y` is an unsigned 3-bit integer.
Return one dictionary with exactly these keys:

```python
{"Y0": ..., "z": ...}
```

Both values are integers `0` or `1`. Return both keys even for unused state
codes or repeated inputs, not `None` or a list. There is no persistent
state: `y` is supplied on each call. `reset()` has no effect and returns
`None` without producing an output sample.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

This is the combinational next-state/output logic of an FSM,
not the state register or a clocked FSM. The present-state code y is an input;
Y0 is bit 0 of the next-state code below. z depends only on the present state.

| y | Next state when x=0 | Next state when x=1 | z |
| --- | --- | --- | --- |
| 000 | 000 | 001 | 0 |
| 001 | 001 | 100 | 0 |
| 010 | 010 | 001 | 0 |
| 011 | 001 | 010 | 1 |
| 100 | 011 | 100 | 1 |

For y=5,6,7 both outputs are defined as zero, an explicit two-state completion
of the source reference's X/don't-care outputs. The original `clk` input is
never used by any expression or process in the reference; this task omits that
irrelevant pin rather than treating it as a timing clock. The x/y function and
all defined source table rows are unchanged. Outputs settle on each input
change and do not depend on earlier observations.

## Reset Behavior

There is no RTL reset or persistent state.

# always_case

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [2:0] sel,
  input [3:0] data0,
  input [3:0] data1,
  input [3:0] data2,
  input [3:0] data3,
  input [3:0] data4,
  input [3:0] data5,
  output reg [3:0] out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, sel, data0, data1, data2, data3, data4, data5): ...
```

Each call returns exactly `{"out": value}`. `sel` is an unsigned 3-bit
integer; each data argument and `value` is an unsigned 4-bit integer.
Selectors 6 and 7 still return `{"out": 0}`, never `None` or a list.
There is no call-to-call state. Construction and `reset()` leave this
stateless mapping unchanged; `reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

For sel=0 through 5, out equals the correspondingly numbered data input. For sel=6 or 7, out is zero. Use a combinational case statement with a default branch.

## Reset Behavior

There is no RTL reset or persistent state.

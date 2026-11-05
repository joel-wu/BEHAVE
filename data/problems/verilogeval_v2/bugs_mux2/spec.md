# bugs_mux2

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input sel,
  input [7:0] a,
  input [7:0] b,
  output reg [7:0] out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, sel, a, b): ...
```

Each call represents one settled input observation and returns exactly
`{"out": value}`, with `value` an unsigned 8-bit integer. Arguments `sel`,
`a`, and `b` are unsigned integers of 1, 8, and 8 bits respectively. The return
is never `None` or a list. The model has no state; `reset()` has no effect,
returns `None`, and emits no record.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Consider the following implementation of an 8-bit 2-to-1 mux:

  module TopModule (
      input        sel,
      input  [7:0] a,
      input  [7:0] b,
      output       out
  );

      assign out = (~sel & a) | (sel & b);

  endmodule

Unfortunately, this module has a bug. Implement a new version of this
module that fixes the bug.

The corrected selector convention is `sel=0: out=a`, `sel=1: out=b`, applied independently to all eight bits. The output width is eight bits.

## Reset Behavior

There is no RTL reset or persistent state.

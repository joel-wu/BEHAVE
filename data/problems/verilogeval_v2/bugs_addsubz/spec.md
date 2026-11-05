# bugs_addsubz

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input do_sub,
  input [7:0] a,
  input [7:0] b,
  output reg [7:0] out,
  output reg result_is_zero
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, do_sub, a, b): ...
```

Pass `do_sub` as 0 or 1 and `a`/`b` as unsigned 8-bit integers. Every call
returns exactly one dictionary `{"out": value, "result_is_zero": flag}`, with
an unsigned 8-bit value and a flag of 0 or 1. Return both fields even for zero,
never `None` or a list. Calls retain no state. `reset()` has no effect and
returns `None`; construction establishes the same stateless model.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Consider the following adder-subtractor with a zero flag:

  synthesis verilog_input_version verilog_2001
  module TopModule (
      input do_sub,
      input [7:0] a,
      input [7:0] b,
      output reg [7:0] out,
      output reg result_is_zero
  );

      always @(*) begin
          case (do_sub)
            0: out = a+b;
            1: out = a-b;
          endcase

          if (~out)
              result_is_zero = 1;
      end

  endmodule

Unfortunately, this module has a bug. Implement a new version of this
module that fixes the bug.

Compute `result_is_zero` from the truncated eight-bit result: 255+1 returns out=0 and result_is_zero=1. Both modes wrap modulo 256.

## Reset Behavior

There is no RTL reset or persistent state.

# adder_bcd

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [3:0] A,
    input wire [3:0] B,
    input wire Cin,
    output wire [3:0] Sum,
    output wire Cout
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, A, B, Cin): ...
```

Each call evaluates one input assignment after combinational settling and
returns exactly `{"Sum": sum_bits, "Cout": carry_bit}`. `A`, `B` and `Sum`
are unsigned 4-bit integers; `Cin` and `Cout` are integers in 0..1. This includes
the nondecimal input codes defined below. Repeated identical inputs still
produce complete dictionaries, never `None` or a list. There is no model state;
construction and `reset()` have no effect on the function, and `reset()`
returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

Let `t=A+B+Cin` without truncation. `Cout=int(t>9)` and `Sum=(t+6*Cout) mod 16`. Inputs 0..9 produce a decimal digit and decimal carry. For codes 10..15, apply exactly the same bit-level rule (t may be as large as 31); these extended results need not be decimal digits.

## Reset Behavior

There is no RTL reset or persistent state.

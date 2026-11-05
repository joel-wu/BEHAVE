# adder_8bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [7:0] a,
    input wire [7:0] b,
    input wire cin,
    output wire [7:0] sum,
    output wire cout
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, cin): ...
```

Each `process(a, b, cin)` call returns exactly `{"sum": sum_bits, "cout": carry}`.
`a`, `b` and `sum_bits` are unsigned 8-bit integers; `cin` and `carry` are
integers 0 or 1. Every call, including an unchanged or all-zero input, returns
one complete dictionary, never `None` or a list. No state persists between
calls; `reset()` returns `None` and emits no result.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

`{cout,sum}=a+b+cin` as a nine-bit unsigned sum. Implement eight connected one-bit full adders, with carry propagation from bit 0 to bit 7.

## Reset Behavior

There is no RTL reset or persistent state.

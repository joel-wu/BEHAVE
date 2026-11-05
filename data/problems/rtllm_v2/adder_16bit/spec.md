# adder_16bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [15:0] a,
    input wire [15:0] b,
    input wire Cin,
    output wire [15:0] y,
    output wire Co
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, Cin): ...
```

Each `process(a, b, Cin)` call evaluates one input assignment after
combinational settling. `a` and `b` are unsigned 16-bit integers and `Cin`
is integer `0` or `1`. Return one dictionary with exactly these keys:

```python
{"y": ..., "Co": ...}
```

`y` is an unsigned 16-bit integer and `Co` is integer `0` or `1`. Return the
complete dictionary even for repeated or all-zero inputs, not `None` or a
list. No state persists between calls; `reset()` has no effect and returns
`None` without producing an output sample.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

`{Co,y}=a+b+Cin` as a seventeen-bit unsigned sum. Instantiate two eight-bit adders, low half first; the lower carry drives the upper carry input. Each eight-bit block is itself built from smaller full-adder blocks.

## Reset Behavior

There is no RTL reset or persistent state.

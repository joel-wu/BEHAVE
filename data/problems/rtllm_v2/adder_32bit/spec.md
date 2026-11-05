# adder_32bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [32:1] A,
    input wire [32:1] B,
    output wire [32:1] S,
    output wire C32
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, A, B): ...
```

Each `process(A, B)` call evaluates one combinational input assignment.
Both arguments are unsigned 32-bit integers; integer bit zero corresponds
to RTL index 1. Return one dictionary with exactly these keys:

```python
{"S": ..., "C32": ...}
```

`S` is an unsigned 32-bit integer with the same index mapping and `C32` is
integer `0` or `1`. Return both fields for every call, including repeated or
all-zero inputs, not `None` or a list. No state persists between calls.
`reset()` has no effect and returns `None` without producing an output sample.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

`{C32,S}=A+B` as a 33-bit unsigned sum with no carry input. Preserve the original one-based vector indices [32:1]. Build two 16-bit carry-lookahead blocks using four-bit group generate/propagate signals and parallel inter-group carry equations, not a ripple chain relabeled CLA.

## Reset Behavior

There is no RTL reset or persistent state.

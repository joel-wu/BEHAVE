# comparator_4bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [3:0] A,
    input wire [3:0] B,
    output wire A_greater,
    output wire A_equal,
    output wire A_less
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, A, B): ...
```

Each `process(A, B)` call returns one dictionary with exactly the keys
`A_greater`, `A_equal`, and `A_less`. Both arguments are unsigned 4-bit port
patterns, and each result is an unsigned one-bit value. Return all three
fields on every call, including repeated identical inputs; do not return
`None` or a list. The instance is stateless. `reset()` has no effect and
returns `None` without an output record.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

Unsigned comparison: A_greater=(A>B), A_equal=(A==B), A_less=(A<B). Exactly one flag is high. Implement the comparison using A-B, the subtraction borrow and the zero difference.

## Reset Behavior

There is no RTL reset or persistent state.

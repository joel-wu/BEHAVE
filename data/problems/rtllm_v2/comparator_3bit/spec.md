# comparator_3bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [2:0] A,
    input wire [2:0] B,
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

Call `process()` once per supplied input record with unsigned three-bit Python
integers `A` and `B`. It returns one dictionary with exactly `A_greater`,
`A_equal`, and `A_less`, each a Python integer `0` or `1`, including repeated
identical inputs. Do not return a scalar or an empty sequence. `reset()`
returns `None` and has no effect on the stateless computation.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

Unsigned comparison: A_greater=(A>B), A_equal=(A==B), A_less=(A<B). Exactly one flag is high for every input pair.

## Reset Behavior

There is no RTL reset or persistent state.

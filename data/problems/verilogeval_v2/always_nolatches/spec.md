# always_nolatches

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [15:0] scancode,
  output reg left,
  output reg down,
  output reg right,
  output reg up
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, scancode): ...
```

Call `process()` once per supplied input record with an unsigned 16-bit Python
integer `scancode`. It returns one dictionary with exactly `left`, `down`,
`right`, and `up`, each a Python integer `0` or `1`. Unknown scan codes return
all four zero fields, not `None` or an empty sequence. Repeated identical
inputs still produce a full record. `reset()` returns `None` and has no effect
on the stateless computation.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

Decode the 16-bit scan code: `0xE06B` asserts left, `0xE072` down, `0xE074` right, and `0xE075` up. All other outputs are zero, and every output is zero for any other code. Use a fully assigned combinational block without latches.

## Reset Behavior

There is no RTL reset or persistent state.

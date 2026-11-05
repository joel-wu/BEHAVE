# barrel_shifter

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [7:0] value,
    input wire [2:0] ctrl,
    output wire [7:0] out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value, ctrl): ...
```

Pass `value` and `ctrl` as unsigned 8-bit and 3-bit integers. Each `process`
call returns exactly `{"out": result}`, where `result` is an unsigned 8-bit
integer. A zero or unchanged result still returns this dictionary, never
`None` or a list. Repeated inputs give the same result; no state persists.
Construction and `reset()` have no numerical effect; `reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

`out=value >> ctrl`, a zero-filled logical RIGHT shift by 0..7. It is not a rotation. Build three mux2X1 stages selecting shifts by 4, 2 and 1; no clock or registered output is added.

## Reset Behavior

There is no RTL reset or persistent state.

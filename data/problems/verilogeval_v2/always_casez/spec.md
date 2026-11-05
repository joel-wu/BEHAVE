# always_casez

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [7:0] value,
  output reg [2:0] pos
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Pass `value` as an unsigned 8-bit integer. Each `process` call returns exactly
`{"pos": index}`, where `index` is an integer in 0..7. Zero input returns
`{"pos": 0}`, never `None` or a list. Repeated inputs give the same result.
There is no persistent state; construction and `reset()` have no numerical
effect, and `reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

If value is nonzero, pos is the index of its least-significant set bit (0 through 7); zero input gives pos=0. Implement a combinational priority encoder with casez patterns. Input values are binary; pattern don't-cares do not introduce unknown input bits.

## Reset Behavior

There is no RTL reset or persistent state.

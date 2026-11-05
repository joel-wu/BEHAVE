# always_case2

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [3:0] value,
  output reg [1:0] pos
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Pass `value` as an unsigned 4-bit integer. Each `process` call returns exactly
`{"pos": index}`, where `index` is an integer in 0..3. Zero input returns
`{"pos": 0}`, never `None` or a list. Repeated identical inputs return the same
dictionary values. There is no persistent state; construction and `reset()`
have no numerical effect, and `reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

If value is nonzero, pos is the index of its least-significant set bit (0 through 3). If value is zero, pos is zero. Multiple set bits are legal; the lowest index wins.

## Reset Behavior

There is no RTL reset or persistent state.

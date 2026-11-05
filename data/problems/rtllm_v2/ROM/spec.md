# ROM

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [7:0] addr,
    output wire [15:0] dout
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, addr): ...
```

Each call returns one dictionary of all output ports. Arguments and
results are unsigned bit patterns of the declared widths. All such inputs are
valid. Outputs are observed after combinational settling, including repeated
identical inputs; there is no clock or handshake.
The dictionary has exactly one key, `dout`, whose value is a Python integer
in [0, 65535]. `addr` is an integer in [0, 255]. Return the dictionary even
for a zero-filled address, not `None` or a list. `reset()` has no effect on
the fixed table and returns no response.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Read-only 256x16 table. Addresses 0,1,2,3 hold A0A0,B1B1,C2C2,D3D3 (hex); addresses 4..255 hold zero. The latter explicitly completes the original unspecified cells, retaining the existing sensible zero-fill policy. dout changes after combinational settling when addr changes. There is no write port, clock or reset; initialization is fixed content, not a reset-time rewrite.

## Reset Behavior

There is no RTL reset or persistent state.

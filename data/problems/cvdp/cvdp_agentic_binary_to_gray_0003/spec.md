# cvdp_agentic_binary_to_gray_0003

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module binary_to_gray #(parameter int WIDTH = 6) (
    input logic [WIDTH-1:0] binary_in,
    output logic [WIDTH-1:0] gray_out
);
```

### Behavior interface

```python
class BinaryToGray:
    def __init__(self, WIDTH=6): ...
    def reset(self): ...
    def process(self, binary_in): ...
```

Each `process(binary_in)` call evaluates one combinational input assignment.
The argument is an unsigned `WIDTH`-bit integer. Return one dictionary with
exactly the key `gray_out` and an unsigned `WIDTH`-bit integer value:

```python
{"gray_out": ...}
```

Return this shape for every call, including repeated or zero inputs, not
`None` or a list. Calls do not advance a clock or persistent state.
`reset()` has no effect and returns `None` without producing an output sample.

## Modules

- `binary_to_gray`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared width are valid, including repeated
identical inputs.

`WIDTH>=1`, default 6. Copy binary_in[WIDTH-1] to gray_out[WIDTH-1].
For i=0 through WIDTH-2, gray_out[i]=binary_in[i+1] XOR binary_in[i].
Equivalently gray_out=binary_in XOR (binary_in>>1), with logical right shift.
Width one is the identity. Output follows the current input continuously;
there is no clock, reset, buffering, latency or handshake.

## Reset Behavior

There is no RTL reset or persistent state.

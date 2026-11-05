# qam16_mapper_interpolated

Map packed four-bit symbols to signed 16-QAM I/Q levels and insert an arithmetic-midpoint sample between each consecutive pair.

## Interface

### RTL interface

```systemverilog
module qam16_mapper_interpolated #(
    parameter int N = 4, parameter int IN_WIDTH = 4, parameter int OUT_WIDTH = 3
) (
    input logic [N*IN_WIDTH-1:0] bits,
    output logic [(N+N/2)*OUT_WIDTH-1:0] I, Q
);
```

### Behavior interface

```python
class Qam16Mapper:
    def __init__(self, N=4, IN_WIDTH=4, OUT_WIDTH=3): ...
    def reset(self): ...
    def process(self, bits): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `I`, `Q`: (N+N/2)*OUT_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `qam16_mapper_interpolated`: the native combinational module shown above.

## Functional Behavior

`N` is even and at least 2; `IN_WIDTH=4` and `OUT_WIDTH=3` are fixed.
Read the N nibbles of `bits` from most significant to least significant. Each
nibble's high two bits select I and low two bits select Q using
`00:-3, 01:-1, 10:1, 11:3`. For every consecutive pair, output the first
mapped sample, their arithmetic mean, and the second mapped sample, on each
axis. Pack those samples most significant first as signed 3-bit two's-complement
lanes. Widen addition before averaging; sums of these odd levels are always even.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

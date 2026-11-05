# Unpack One Hot

Expand packed signed or unsigned lanes from a 256-bit word into a 512-bit word.

## Interface

### RTL interface

```systemverilog
module unpack_one_hot (
    input logic sign, size,
    input logic [2:0] one_hot_selector,
    input logic [255:0] source_reg,
    output logic [511:0] destination_reg
);
```

### Behavior interface

```python
class UnpackOneHot:
    def __init__(self): ...
    def reset(self): ...
    def process(self, sign, size, one_hot_selector, source_reg): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `destination_reg`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input patterns of the declared widths are valid. Observe the complete
output after combinational settling. There is no clock, handshake or retained state.

## Modules

- `unpack_one_hot`: the complete combinational unpacker.

## Functional Behavior

| Selector | Size | Source lane width | Destination lane width | Lane count |
| --- | --- | --- | --- | --- |
| 1 | either | 1 | 8 | 64 |
| 2 | either | 2 | 8 | 64 |
| 4 | 0 | 4 | 8 | 64 |
| 4 | 1 | 8 | 16 | 32 |

Lane i starts at bit `i*width` in each word, with lane zero least significant.
For `sign=0`, zero-extend each selected source lane. For `sign=1`, extend its
most-significant bit. Thus signed nibble 9 becomes byte `0xF9`.
Exactly 512 destination bits are filled. Selector 1 reads only source bits
63:0; selector 2 reads only bits 127:0. Higher source bits are ignored in those
two modes.
Selectors 0, 3, 5, 6 and 7 instead zero-extend the entire source word to 512 bits,
independent of sign and size. Outputs have no dependence on earlier inputs.

## Reset Behavior

There is no RTL reset or persistent state.

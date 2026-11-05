# nbit_swizzling

Reverse bits independently within a selected number of equal contiguous word sections while preserving each section position.

## Interface

### RTL interface

```systemverilog
module nbit_swizzling #(
    parameter int DATA_WIDTH = 64
) (
    input logic [DATA_WIDTH-1:0] data_in,
    input logic [1:0] sel,
    output logic [DATA_WIDTH-1:0] data_out
);
```

### Behavior interface

```python
class NbitSwizzling:
    def __init__(self, DATA_WIDTH=64): ...
    def reset(self): ...
    def process(self, data_in, sel): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: DATA_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `nbit_swizzling`: the native combinational module shown above.

## Functional Behavior

`DATA_WIDTH` is at least 16 and a multiple of 8. For selectors
0,1,2,3 divide the word into 1,2,4,8 equal contiguous sections, respectively.
Reverse bits within each section, leaving the section in its original position.
For section width S, output bit `k*S+j` equals input bit `k*S+S-1-j`.
All four selector values are defined; no two-state input takes a default branch.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

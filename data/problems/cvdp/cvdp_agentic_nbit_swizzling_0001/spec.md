# cvdp_agentic_nbit_swizzling_0001

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module nbit_swizzling #(parameter int DATA_WIDTH = 64) (
    input logic [DATA_WIDTH-1:0] data_in,
    input logic [1:0] sel,
    output logic [DATA_WIDTH-1:0] data_out, gray_out
);
```

### Behavior interface

```python
class NbitSwizzling:
    def __init__(self, DATA_WIDTH=64): ...
    def reset(self): ...
    def process(self, data_in, sel): ...
```

Constructor `DATA_WIDTH` matches the RTL parameter. Each call returns exactly
one dictionary with `data_out` and `gray_out`, both unsigned `DATA_WIDTH`-bit
integers. Arguments are an unsigned `DATA_WIDTH`-bit word and a 2-bit selector.
The return is never `None` or a list. The model has no state; `reset()` has no
effect, returns `None`, and emits no record.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `nbit_swizzling`: the complete combinational circuit.

## Functional Behavior

`DATA_WIDTH` is a positive multiple of eight, default 64. The equal-size
partition requirement makes the source's unspecified valid-width condition
explicit. Interpret data_in and both outputs as unsigned bit vectors.

For sel=0,1,2,3, split the word into 1,2,4,8 equal contiguous segments,
respectively. Preserve segment order and reverse the bits WITHIN each segment.
For segment length L=DATA_WIDTH/(2**sel), output bit i equals input bit
`(i//L)*L + L-1-(i%L)`. All four two-bit selector values are defined.
Then gray_out=data_out XOR (data_out>>1) over the complete word, NOT separately
within each segment. Both outputs follow the current inputs without a clock,
reset, state or handshake.

## Reset Behavior

There is no RTL reset or persistent state.

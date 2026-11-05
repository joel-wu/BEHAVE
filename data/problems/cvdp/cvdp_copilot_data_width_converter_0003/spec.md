# data_width_converter

Collect four valid 32-bit words into one registered 128-bit word.

## Interface

### RTL interface

```systemverilog
module data_width_converter (
    input logic clk, input logic reset,
    input logic [31:0] data_in, input logic data_valid,
    output logic [127:0] o_data_out, output logic o_data_out_valid
);
```

### Behavior interface

```python
class DataWidthConverter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_valid, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_data_out`: 128.
- `o_data_out_valid`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `data_width_converter`: the complete native module with the ports and parameters above.

## Functional Behavior

Each edge with `data_valid=1` contributes one 32-bit word. Ignore data on
invalid edges and retain any partial group. "Consecutive valid inputs" means
successive valid samples, not necessarily adjacent clock cycles; only reset
discards an incomplete group.

The fourth word completes `{word0,word1,word2,word3}` with the first word in bits
127:96. Register that value and assert `o_data_out_valid` for one clock period.
At the next edge clear the valid pulse and start collecting the next group.
Retain the last completed output data between groups. The first next-group word
may arrive on that very next edge. There is no ready signal or output stall.

Example: valid inputs A1B2C3D4, 55667788, 11223344, 99AABBCC yield
`128'hA1B2C3D4_55667788_11223344_99AABBCC`. Reset must be LOW during these inputs.

## Reset Behavior

`reset` is asynchronous and active high. Assertion immediately clears all state and output pins to zero.
Reset discards incomplete work. The next released edge resumes from zero.

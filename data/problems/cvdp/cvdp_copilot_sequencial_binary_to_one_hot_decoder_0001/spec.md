# binary_to_one_hot_decoder_sequencial

Register a parameterized binary-to-one-hot decode on every rising edge.

## Interface

### RTL interface

```systemverilog
module binary_to_one_hot_decoder_sequencial #(
    parameter int BINARY_WIDTH = 5, parameter int OUTPUT_WIDTH = 32
) (
    input logic i_clk, i_rstb,
    input logic [BINARY_WIDTH-1:0] i_binary_in,
    output logic [OUTPUT_WIDTH-1:0] o_one_hot_out
);
```

### Behavior interface

```python
class BinaryToOneHotDecoderSequential:
    def __init__(self, BINARY_WIDTH=5, OUTPUT_WIDTH=32): ...
    def reset(self): ...
    def process(self, i_binary_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_one_hot_out`: OUTPUT_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `i_clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `binary_to_one_hot_decoder_sequencial`: the complete native module above.

## Functional Behavior

`BINARY_WIDTH >= 1` and `OUTPUT_WIDTH >= 1`, independently configurable.
At every rising edge, register the vector whose bit at the unsigned input index
is one and whose other bits are zero. If the index is at least OUTPUT_WIDTH,
register zero. When OUTPUT_WIDTH exceeds the input index range, extra high bits
remain zero; do not truncate bit-position comparisons. The new vector appears
after the capture edge and holds until the next edge. No handshake or extra
pipeline stage is present. The source's explicitly named top-level spelling
`sequencial` is retained.

## Reset Behavior

`i_rstb` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

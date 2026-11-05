# SetBitStreamCalculator

Count set bits in successive enabled serial windows, with saturation.

## Interface

### RTL interface

```systemverilog
module SetBitStreamCalculator #(parameter int p_max_set_bit_count_width = 8) (
    input logic i_clk, i_rst_n, i_ready, i_bit_in,
    output logic [p_max_set_bit_count_width-1:0] o_set_bit_count
);
```

### Behavior interface

```python
class SetBitStreamCalculator:
    def __init__(self, p_max_set_bit_count_width=8): ...
    def reset(self): ...
    def process(self, i_ready, i_bit_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_set_bit_count`: p_max_set_bit_count_width.

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

- `SetBitStreamCalculator`: the complete native module above.

## Functional Behavior

`p_max_set_bit_count_width >= 1`. On an edge where `i_ready` rises from
its previously sampled zero to one, clear the count and ignore `i_bit_in`.
On subsequent edges while ready stays high, add one for each asserted input
bit, saturating at `2**p_max_set_bit_count_width-1`. Hold the count while ready
is low. A later low-to-high transition starts a new window. Inputs are already
synchronous to `i_clk`; do not add synchronization latency. New count values
appear after the updating edge and hold between edges.

## Reset Behavior

`i_rst_n` is asynchronous, active low. Assertion immediately restores the initial state. Count and previous-ready are zero.
After reset release, resume the edge-by-edge behavior above.

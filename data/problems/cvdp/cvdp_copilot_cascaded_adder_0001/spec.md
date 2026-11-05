# cascaded_adder

A two-stage registered sum of a packed vector of unsigned integers.

## Interface

### RTL interface

```systemverilog
module cascaded_adder #(
    parameter int IN_DATA_WIDTH = 16,
    parameter int IN_DATA_NS = 4
) (
    input logic clk, input logic rst_n,
    input logic i_valid,
    input logic [IN_DATA_WIDTH*IN_DATA_NS-1:0] i_data,
    output logic o_valid,
    output logic [IN_DATA_WIDTH+$clog2(IN_DATA_NS)-1:0] o_data
);
```

### Behavior interface

```python
class CascadedAdder:
    def __init__(self, IN_DATA_WIDTH=16, IN_DATA_NS=4): ...
    def reset(self): ...
    def process(self, i_valid, i_data): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_data`: IN_DATA_WIDTH+$clog2(IN_DATA_NS).
- `o_valid`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `cascaded_adder`: the complete native module with the ports and parameters above.

## Functional Behavior

`IN_DATA_WIDTH >= 1` and `IN_DATA_NS >= 1`. Input elements are unsigned;
element k occupies `i_data[k*IN_DATA_WIDTH +: IN_DATA_WIDTH]`. Sum all elements
without overflow using `IN_DATA_WIDTH + ceil(log2(IN_DATA_NS))` bits.

There are exactly two register stages. On an edge, capture `i_data` only if
`i_valid=1`, and always capture `i_valid` into the first valid register. On the
same edge, the second stage captures the sum of the OLD first-stage data and
its OLD valid bit. For an input captured at edge n, the new output is available
after edge n+1 and is sampled at edge n+2. Consecutive valid inputs can arrive
every edge. An invalid input holds the first data register and propagates a
valid bubble; the output data register still captures its sum. There is no
ready signal or backpressure.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion immediately clears all state and output pins to zero.
Reset discards incomplete work. The next released edge resumes from zero.

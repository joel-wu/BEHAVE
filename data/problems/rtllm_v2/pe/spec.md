# Multiply-Accumulate Processing Element

Accumulate the product of two 32-bit inputs on every rising edge of `clk`. This is a continuously clocked MAC, not a handshake-gated transaction engine.

## Interface

### RTL interface

```systemverilog
module pe (
    input logic clk,
    input logic rst,
    input logic [31:0] a,
    input logic [31:0] b,
    output logic [31:0] c
);
```

### Behavior interface

```python
class MultiplyAccumulate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

One call represents one non-reset edge. It returns `{"c": old_accumulator}` from before that edge and then accumulates the current product. For pairs `(1,1),(2,2),(3,3),(0,0)`, the observed values are `0,1,5,14`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `c`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `pe`: multiply-accumulate processing element.

## Functional Behavior

The state is a 32-bit unsigned accumulator. Each rising edge performs `c_next = (c + a * b) mod 2^32`, using unsigned 32-bit operands. Both multiplication overflow and addition overflow are discarded above bit 31; there is no saturation. The output `c` continuously exposes the accumulator.

Every clock contributes a product, even when consecutive operand pairs are identical. A zero operand contributes zero and leaves the sum unchanged.

## Reset Behavior

`rst` is asynchronous and active high. It clears the accumulator and `c` to zero.

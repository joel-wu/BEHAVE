# Pipelined Unsigned Multiplier

A two-register-stage unsigned multiplier samples operands on every rising clock edge. It accepts one pair per cycle and advances even when operands are unchanged. There are no valid, enable, or ready signals.

## Interface

### RTL interface

```systemverilog
module multi_pipe_4bit #(
    parameter integer size = 4
) (
    input logic clk,
    input logic rst_n,
    input logic [size-1:0] mul_a,
    input logic [size-1:0] mul_b,
    output logic [2*size-1:0] mul_out
);
```

`size` is a positive integer; the default benchmark configuration is 4. Operands and product are unsigned. The full `2*size`-bit product is exact, without saturation or truncation.

### Behavior interface

```python
class MultiPipe4Bit:
    def __init__(self, size=4): ...
    def reset(self): ...
    def process(self, mul_a, mul_b): ...
```

One Behavior call represents one non-reset rising edge. The returned `{"mul_out": value}` is the output immediately before that edge; the call then advances the two stages. Every edge has one input and output record, including initial zeros.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `mul_out`: 2*size.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `multi_pipe_4bit`: unsigned multiplication with two sequential register stages.

## Functional Behavior

For operands sampled at edge `k`, the product appears immediately after edge `k+1` and remains until edge `k+2`. Equivalently, at each edge the first stage takes the current product and `mul_out` takes the old first-stage value. Both updates occur every clock, without waiting for another transaction or an output consumer.

Pre-edge observations show the product from two edges earlier. Its first two observations after reset are zero. For consecutive operand pairs `(3,2), (5,7), (0,0), (0,0)`, the pre-edge outputs are `0, 0, 6, 35`. Continued clocks with zero operands drain earlier products; they are not discarded.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears both stages, canceling pre-reset products. The first edge after release fills the first stage; the second edge makes its product visible at the output.

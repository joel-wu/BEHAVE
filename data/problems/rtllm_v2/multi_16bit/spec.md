# Restartable 16-bit Shift-and-add Multiplier

Multiply two unsigned 16-bit operands using one capture edge and sixteen shift-and-add edges. start is a level enable that must remain high for a complete operation, not a one-cycle request pulse.

## Interface

### RTL interface

```systemverilog
module multi_16bit (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic [15:0] ain,
    input logic [15:0] bin,
    output logic [31:0] yout,
    output logic done
);
```

### Behavior interface

```python
class Multi16Bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, start, ain, bin): ...
```

One call represents one non-reset rising edge. It returns `{"yout": old_yout, "done": old_done}` sampled immediately before that edge, then updates the state. Reset edges produce no observation records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `done`: 1.
- `yout`: 32.

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

- `multi_16bit`: the native clocked top module shown above.

## Functional Behavior

The state comprises captured operands, a 32-bit product and a counter `i` from 0 to 17. On each non-reset rising edge, clear `done` and apply the following first matching rule using the old counter:

1. If `start=0`, set `i=0`. Retain the operand and product registers; an unfinished multiplication is aborted.
2. If `start=1` and `i=0`, capture `ain` and `bin`, clear the product to zero, and set `i=1`.
3. If `start=1` and `1 <= i <= 16`, test bit `i-1` of the captured `ain`. If set, add the captured `bin` shifted left by `i-1` to the product. Increment `i`; on the iteration with old `i=16`, also assert `done`.
4. At `i=17`, hold the product and counter until `start` goes low. A continuously high start does not launch another operation.

`yout` continuously exposes the product register, including partial results. Arithmetic is modulo 2^32; a complete 16-by-16 unsigned product fits exactly. Later changes of `ain` and `bin` do not affect a captured operation. Each new capture starts an independent product, without requiring chip reset.

With start continuously high from an idle counter, the seventeenth edge completes the product and asserts done. The next edge clears done. The pre-edge observation therefore first shows the completed product with `done=1` at the eighteenth edge. Dropping start before the last addition aborts without a done pulse; dropping it after completion rearms the next operation. No ready or result-consumption pin is present.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately clears the operands, product, counter and done flag.

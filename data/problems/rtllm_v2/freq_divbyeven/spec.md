# Divide-by-six Clock Divider

Generate a divide-by-six clock with 50% duty cycle. This task fixes the original divider parameter NUM_DIV to its default value 6; it is not a runtime input.

## Interface

### RTL interface

```systemverilog
module freq_divbyeven (
    input logic clk,
    input logic rst_n,
    output logic clk_div
);
```

### Behavior interface

```python
class EvenClockDivider:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Call `process()` with no arguments once per non-reset rising edge. It returns
exactly `{"clk_div": value}`, with `value` integer 0 or 1 sampled before the
edge, then advances state. The return is never `None` or a list. Reuse the
instance across edges. `reset()` clears state, returns `None`, and emits no record.

### Clock-edge observations

Observe the settled output level immediately before each non-reset rising edge,
then advance that edge. Reset edges produce no observation records.

## Modules

- `freq_divbyeven`: the native clocked top module shown above.

## Functional Behavior

A four-bit counter starts at zero. On each rising edge of `clk`, increment it if it is less than 2. Otherwise clear it to zero and toggle `clk_div`. The output holds between rising edges. Starting from reset, the sampled levels are `0,0,0,1,1,1,0,0,0,...`; one full output period spans six input periods. There is no input-data transaction, output backpressure or valid/ready handshake.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately clears the counter and `clk_div`.

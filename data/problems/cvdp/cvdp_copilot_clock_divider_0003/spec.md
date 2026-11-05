# clock_divider

Select a divide-by-2, divide-by-4 or divide-by-8 clock, or a constant-low output.

## Interface

### RTL interface

```systemverilog
module clock_divider (
    input logic clk, input logic rst_n, input logic [1:0] sel,
    output logic clk_out
);
```

### Behavior interface

```python
class ClockDivider:
    def __init__(self): ...
    def reset(self): ...
    def process(self, sel): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `clk_out`: 1.

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

- `clock_divider`: the complete native module with the ports and parameters above.

## Functional Behavior

A three-bit counter starts at zero and increments modulo eight at every
rising edge. `sel=0/1/2` selects counter bit 0/1/2 as `clk_out`, respectively;
`sel=3` drives zero. For a selector held constant outside reset, the output is
a 50%-duty clock at input frequency divided by 2, 4 or 8. Its first rising edge
occurs after 1, 2 or 4 input edges from reset.

Configure `sel` while reset is asserted for regular divided-clock operation.
The combinational selection equation also
defines the pin for other selector changes, but such changes are not promised
glitch-free. No extra output register, input clock or handshake is introduced.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion immediately clears all state and output pins to zero.
Reset discards incomplete work. The next released edge resumes from zero.

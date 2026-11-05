# digital_dice_roller

Count through values one to six while a synchronous button is held and display the completed count on release; the result is deterministic for each button history.

## Interface

### RTL interface

```systemverilog
module digital_dice_roller (
    input logic clk, reset_n, button,
    output logic [2:0] dice_value
);
```

### Behavior interface

```python
class DigitalDiceRoller:
    def __init__(self): ...
    def reset(self): ...
    def process(self, button): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `dice_value`: 3.

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

- `digital_dice_roller`: the complete native module with the ports and parameters above.

## Functional Behavior

Use IDLE and ROLLING states and a counter cycling through 1..6.
On the first rising edge with `button=1` after IDLE, enter ROLLING with counter
1. Each further high edge advances the counter, wrapping 6 to 1. On the first
edge with `button=0` in ROLLING, latch the counter to `dice_value`, clear the
counter and return to IDLE. Further low edges hold the displayed result.

The display retains its PREVIOUS completed result throughout a new roll; it
does not expose the running counter. A new press starts counting from 1 again.
A press lasting n high edges yields `1+((n-1) mod 6)` on release. Reset displays
0 until the first completed roll; subsequent completed results are always 1..6.
The design is deterministic for a given button history, not a random generator.
The button is a synchronous input; external switch synchronization/debouncing
is outside this module.

## Reset Behavior

`reset_n` is asynchronous and active low. Assertion immediately clears all state, pending work and output pins to zero.

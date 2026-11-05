# perfect_squares_generator

Generate successive unsigned 32-bit perfect squares, saturating on overflow.

## Interface

### RTL interface

```systemverilog
module perfect_squares_generator (
    input logic clk, reset,
    output logic [31:0] sqr_o
);
```

### Behavior interface

```python
class PerfectSquaresGenerator:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `sqr_o`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
A no-argument process() call still advances one clock edge.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `perfect_squares_generator`: the complete native module above.

## Functional Behavior

The output is 1 during reset, following the source's explicit example.
Each released rising edge advances to the next square: 4, 9, 16, and so on.
Equivalently, add successive odd increments 3, 5, 7, ... to the current square.
Compute the addition without losing overflow. The last exact representable
square is `65535**2 = 4294836225`. At the following edge, saturate to
`32'hFFFFFFFF`; retain that value indefinitely until reset. Do not wrap to zero.
There is no enable or data input: every non-reset edge advances the sequence.

## Reset Behavior

`reset` is asynchronous, active high. Assertion immediately restores the initial state. Square=1 and next odd increment=3; the output is 1.
After reset release, resume the edge-by-edge behavior above.

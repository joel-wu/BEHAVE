# GP

Compute the generate, OR-propagate and carry-out signals of one carry-lookahead GP cell, not a complete multi-bit adder.

## Interface

### RTL interface

```systemverilog
module GP (
    input logic i_A, i_B, i_Cin,
    output logic o_generate, o_propagate, o_Cout
);
```

### Behavior interface

```python
class Gp:
    def __init__(self): ...
    def reset(self): ...
    def process(self, i_A, i_B, i_Cin): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_Cout`, `o_generate`, `o_propagate`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `GP`: the native combinational module shown above.

## Functional Behavior

All ports are one bit. Compute `o_generate=i_A & i_B`,
`o_propagate=i_A | i_B`, and
`o_Cout=o_generate | (o_propagate & i_Cin)`. Propagate uses OR, **not XOR**.
This is the single-bit GP cell, not a complete multi-bit adder.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

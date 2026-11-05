# morse_encoder

Encode uppercase ASCII letters and digits as packed Morse symbols and their lengths, returning an empty code for unsupported bytes.

## Interface

### RTL interface

```systemverilog
module morse_encoder (
    input logic [7:0] ascii_in,
    output logic [9:0] morse_out,
    output logic [3:0] morse_length
);
```

### Behavior interface

```python
class MorseEncoder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, ascii_in): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `morse_length`: 4.
- `morse_out`: 10.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `morse_encoder`: the native combinational module shown above.

## Functional Behavior

Support only uppercase ASCII A–Z and digits 0–9. In the table below,
dot is bit 0 and dash is bit 1. Pack the sequence right-aligned in `morse_out`,
first symbol at bit `morse_length-1`; unused high bits are zero. All other
bytes produce `morse_out=0,morse_length=0`. In particular lowercase does not
alias uppercase, and valid all-dot codes still have nonzero length.

| Character | Morse | Character | Morse |
| --- | --- | --- | --- |
| A | .- | N | -. |
| B | -... | O | --- |
| C | -.-. | P | .--. |
| D | -.. | Q | --.- |
| E | . | R | .-. |
| F | ..-. | S | ... |
| G | --. | T | - |
| H | .... | U | ..- |
| I | .. | V | ...- |
| J | .--- | W | .-- |
| K | -.- | X | -..- |
| L | .-.. | Y | -.-- |
| M | -- | Z | --.. |
| 0 | ----- | 5 | ..... |
| 1 | .---- | 6 | -.... |
| 2 | ..--- | 7 | --... |
| 3 | ...-- | 8 | ---.. |
| 4 | ....- | 9 | ----. |

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

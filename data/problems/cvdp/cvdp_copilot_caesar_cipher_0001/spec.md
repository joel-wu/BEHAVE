# caesar_cipher

Apply a case-preserving Caesar shift to ASCII letters while leaving all other byte values unchanged.

## Interface

### RTL interface

```systemverilog
module caesar_cipher (
    input logic [7:0] input_char,
    input logic [3:0] key,
    output logic [7:0] output_char
);
```

### Behavior interface

```python
class CaesarCipher:
    def __init__(self): ...
    def reset(self): ...
    def process(self, input_char, key): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `output_char`: 8.

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

- `caesar_cipher`: the native combinational module shown above.

## Functional Behavior

`key` is unsigned 0 through 15. Shift ASCII A–Z and a–z forward by `key`
modulo 26 within the same case. Every other byte, including values 128–255,
passes through unchanged. For example `y` with key 8 becomes `g`.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

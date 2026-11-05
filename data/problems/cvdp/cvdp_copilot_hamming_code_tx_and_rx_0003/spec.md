# hamming_code_receiver

Decode a Hamming(7,4) word by syndrome-based single-bit correction and return its four data bits; no double-error detection is provided.

## Interface

### RTL interface

```systemverilog
module hamming_code_receiver (
    input logic [7:0] data_in,
    output logic [3:0] data_out
);
```

### Behavior interface

```python
class HammingCodeReceiver:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 4.

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

- `hamming_code_receiver`: the native combinational module shown above.

## Functional Behavior

Compute syndrome bits s0 from input positions (1,3,5,7), s1 from
(2,3,6,7), and s2 from (4,5,6,7), using XOR. Let `s={s2,s1,s0}`.
If s is nonzero, invert input bit s; otherwise leave the word unchanged.
Return corrected bits `{7,6,5,3}`. Bit 0 never affects decoding. This
corrects one flipped significant bit and tolerates a padding-bit flip.
The same deterministic rule applies to every input word, but recovery of
the originally transmitted data is not promised for two or more errors;
there is no double-error detection flag.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

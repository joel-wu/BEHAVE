# hamming_code_tx_for_4bit

Encode four data bits as an even-parity Hamming(7,4) codeword with a fixed padding bit, not an overall SECDED parity bit.

## Interface

### RTL interface

```systemverilog
module hamming_code_tx_for_4bit (
    input logic [3:0] data_in,
    output logic [7:0] data_out
);
```

### Behavior interface

```python
class HammingCodeTxFor4bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 8.

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

- `hamming_code_tx_for_4bit`: the native combinational module shown above.

## Functional Behavior

For `d0=data_in[0]` through `d3=data_in[3]`, output bits 7 down to 0 are
`{d3,d2,d1,d1^d2^d3,d0,d0^d2^d3,d0^d1^d3,0}`. Thus parity is even at
positions 1, 2 and 4; data occupies 3, 5, 6 and 7. Bit 0 is fixed padding,
**not overall parity**. This is Hamming(7,4), not SECDED.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

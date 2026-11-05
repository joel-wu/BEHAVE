# gf_multiplier

Multiply two four-bit binary polynomials in GF(16), reducing their carry-less product modulo x^4+x+1.

## Interface

### RTL interface

```systemverilog
module gf_multiplier (
    input logic [3:0] A, B,
    output logic [3:0] result
);
```

### Behavior interface

```python
class GfMultiplier:
    def __init__(self): ...
    def reset(self): ...
    def process(self, A, B): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `result`: 4.

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

- `gf_multiplier`: the native combinational module shown above.

## Functional Behavior

Inputs encode binary polynomials (bit i is the coefficient of x^i).
Output their carry-less product modulo `x^4+x+1` (`5'b10011`). For four
iterations from B's LSB, conditionally XOR the running multiplicand into the
result, then multiply it by x: shift left, discard the fifth bit, and XOR 3
if the **pre-shift bit 3** was 1. No integer carries participate.
For example A=3, B=7 gives result=9.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

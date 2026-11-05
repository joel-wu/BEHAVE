# signed_unsigned_comparator

Compare two configurable-width words in signed or unsigned mode, with an enable input controlling the greater-than, less-than and equality flags.

## Interface

### RTL interface

```systemverilog
module signed_unsigned_comparator #(
    parameter int WIDTH = 5
) (
    input logic [WIDTH-1:0] i_A, i_B,
    input logic i_enable, i_mode,
    output logic o_greater, o_less, o_equal
);
```

### Behavior interface

```python
class SignedComparator:
    def __init__(self, WIDTH=5): ...
    def reset(self): ...
    def process(self, i_A, i_B, i_enable, i_mode): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_equal`, `o_greater`, `o_less`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `signed_unsigned_comparator`: the native combinational module shown above.

## Functional Behavior

`WIDTH` is positive. If `i_enable=0`, all flags are 0. Otherwise exactly
one flag indicates A greater than, less than, or equal to B. With `i_mode=1`,
interpret the complete WIDTH-bit patterns as two's-complement signed integers.
With `i_mode=0`, compare the complete patterns as unsigned integers: the MSB
is an ordinary magnitude bit, not discarded. This resolves the source's
ambiguous “ignores the sign bit” phrase as “no signed interpretation.”

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.

# sub_64bit

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [63:0] A,
    input wire [63:0] B,
    output wire [63:0] result,
    output wire overflow
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, A, B): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `overflow`: 1.
- `result`: 64.

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

- `TopModule`: the complete combinational circuit.

## Functional Behavior

`result=(A-B) mod 2^64`. `overflow=1` iff the signed two's-complement difference is outside [-2^63,2^63-1]. Equivalently, A and B have different signs and the wrapped result has a different sign from A. No saturation.

## Reset Behavior

There is no RTL reset or persistent state.

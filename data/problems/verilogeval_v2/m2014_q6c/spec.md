# m2014_q6c

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input  [5:0] y,
  input        w,
  output       Y1,
  output       Y3
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, y, w): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `Y1`, `Y3`: 1.

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

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Consider the state machine shown below:

  A (0) --0--> B
  A (0) --1--> A
  B (0) --0--> C
  B (0) --1--> D
  C (0) --0--> E
  C (0) --1--> D
  D (0) --0--> F
  D (0) --1--> A
  E (1) --0--> E
  E (1) --1--> D
  F (1) --0--> C
  F (1) --1--> D

For this combinational part, assume that a one-hot code is used
with the state assignment y[5:0] = 000001, 000010, 000100, 001000,
010000, 100000 for states A, B,..., F, respectively.

Implement only next-state bits 1 and 3, named Y1 and Y3. Derive their equations by inspection of the one-hot transition graph.

The named ports `Y1` and `Y3` are next-state bits 1 and 3 (B and D), respectively; these names control the interface. For zero-hot or multi-hot inputs, OR the contributions of every active state, without priority selection. The block is combinational; the current-state input is not an internal register.

## Reset Behavior

There is no RTL reset or persistent state.

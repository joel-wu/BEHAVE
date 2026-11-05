# kmap3

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  input c,
  input d,
  output reg out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, c, d): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 1.

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

The module should implement the Karnaugh map below. Cells marked d are completed as specified below.

              ab
   cd   01  00  10  11
   00 | d | 0 | 1 | 1 |
   01 | 0 | 0 | d | d |
   11 | 0 | 1 | 1 | 1 |
   10 | 0 | 1 | 1 | 1 |

Fix the don't-care entries at `abcd=0100,1001,1101` to 0,1,1 respectively. All other map cells remain unchanged. This gives a deterministic interface while retaining the minimized function.

## Reset Behavior

There is no RTL reset or persistent state.

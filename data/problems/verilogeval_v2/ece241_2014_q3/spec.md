# ece241_2014_q3

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input c,
  input d,
  output [3:0] mux_in
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, c, d): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `mux_in`: 4.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

For the following Karnaugh map, give the circuit implementation using one
4-to-1 multiplexer and as many 2-to-1 multiplexers as required, but using
as few as possible. You are not allowed to use any other logic gate and
you must use _a_ and _b_ as the multiplexer selector inputs, as shown on
the 4-to-1 multiplexer below.

      ab
  cd  00  01  11  10
  00 | 0 | 0 | 0 | 1 |
  01 | 1 | 0 | 0 | 0 |
  11 | 1 | 0 | 1 | 1 |
  10 | 1 | 0 | 0 | 1 |

Consider a block diagram with inputs 'c' and 'd' going into a module
called "TopModule". This "TopModule" has four outputs, mux_in[3:0], that
connect to a four input mux. The mux takes as input {a,b} and ab = 00 is
connected to mux_in[0], ab=01 is connected to mux_in[1], and so in. You
are implementing in Verilog just the portion labelled "TopModule", such
that the entire circuit (including the 4-to-1 mux) implements the K-map.

Column selection is `ab=00,01,10,11` for bits 0,1,2,3. For `cd=00,01,10,11`, the required `mux_in` vectors are `0100,0001,0101,1101`. In particular, bit 2 is `c | ~d`. Implement the data-input network with 2:1 muxes and constants; no extra external ports are needed.

## Reset Behavior

There is no RTL reset or persistent state.

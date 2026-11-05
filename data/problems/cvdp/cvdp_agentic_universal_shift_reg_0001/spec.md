# Universal Shift Register

An N-bit register with hold, serial shift, circular rotation and parallel load.
It updates on every rising clock edge, without ready/valid flow control.

## Interface

### RTL interface

```systemverilog
module universal_shift_register #(parameter int N = 8) (
    input logic clk, rst,
    input logic [1:0] mode_sel,
    input logic shift_dir, serial_in,
    input logic [N-1:0] parallel_in,
    output logic [N-1:0] q,
    output wire serial_out
);
```

### Behavior interface

```python
class UniversalShiftRegister:
    def __init__(self, N=8): ...
    def reset(self): ...
    def process(self, mode_sel, shift_dir, serial_in, parallel_in): ...
```

Each call returns `{"q": value, "serial_out": bit}` for the settled pins
immediately before one non-reset rising edge, then advances the register.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: N.
- `serial_out`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

### Parameter specialization

N is a positive integer, default 8. Runtime field widths must match the
chosen RTL parameter value.

## Modules

- `universal_shift_register`: the native register and combinational serial tap.

## Functional Behavior

Inputs and outputs are unsigned bit patterns of the declared widths. `q` is
the current register. At all times, `serial_out` equals `q[0]` for direction 0
(right) or `q[N-1]` for direction 1 (left), including hold and load modes.
It is not a latched copy of the bit from the previous operation. Changing
direction changes this tap without modifying q.

On each rising edge, use the old q and apply `mode_sel`:

- 0: retain q.
- 1: shift one bit in the selected direction, inserting `serial_in` at the
  opposite end. Left shifts discard the old most-significant bit; right
  shifts discard the old least-significant bit.
- 2: rotate one bit in the selected direction. The outgoing bit reenters at
  the opposite end; `serial_in` is ignored.
- 3: load `parallel_in`; direction and serial input do not affect q.

For N=1, shifting replaces the bit with `serial_in`, rotation and hold retain
it, and both directions select the same serial output. All updates retain
exactly N bits. Consecutive operations need no bubbles or acknowledgements.

## Reset Behavior

`rst` is asynchronous and active high, as in the original specification.
Assertion clears q immediately, hence serial_out=0 for either direction;
q remains zero while reset is high. Reset cancels prior register contents.
Reset edges do not produce logical observation records. After deassertion,
the next rising edge performs the selected operation normally.

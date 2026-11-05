# serial_in_parallel_out_8bit

Shift a continuous serial bitstream into an eight-bit parallel sliding window.

## Interface

### RTL interface

```systemverilog
module serial_in_parallel_out_8bit (
    input logic clock, init_reset, serial_in,
    output logic [7:0] parallel_out
);
```

### Behavior interface

```python
class SerialInParallelOut8bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, serial_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `parallel_out`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clock`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `serial_in_parallel_out_8bit`: the complete native module above.

## Functional Behavior

On EVERY rising edge of `clock`, shift the eight-bit register left,
discard its old bit 7 and insert `serial_in` at bit 0. The output is the register
itself, holds between edges, and exposes each successive sliding window.
There is no enable, ready/valid, framing signal, or extra output register.
Starting from zero, the source example 1,0,1,1,0,0,1,0 gives post-edge values
1,2,5,11,22,44,89,178 (0xB2). Later inputs continue shifting, not just every eighth bit.

### Explicit initialization adaptation

The source permits unknown startup and uses zero only in its example. We add
synchronous active-high `init_reset`, clearing the register to zero. This is a
disclosed deterministic initialization convention, not a source requirement.
The original `clock`, `serial_in`, `parallel_out` pins and released-edge equation
are preserved; before the first initialization edge the state is unspecified.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clock` with reset asserted restores the initial state. All stored state, including memory and pipeline registers when present, is zero.
After reset release, resume the edge-by-edge behavior above.

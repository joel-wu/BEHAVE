# piso_8bit

Serialize successive eight-bit values, MSB first, starting at one.

## Interface

### RTL interface

```systemverilog
module piso_8bit (
    input logic clk, rst,
    output logic serial_out
);
```

### Behavior interface

```python
class Piso8Bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `serial_out`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
A no-argument process() call still advances one clock edge.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `piso_8bit`: the complete native module above.

## Functional Behavior

Starting from the byte 1, emit its eight bits MSB first, one registered
bit per rising edge; then emit byte 2, byte 3, ... modulo 256. The first eight
post-reset edge results are 0,0,0,0,0,0,0,1. After byte 255, emit byte 0 and then
byte 1 again. A reset in the middle of a byte discards that partial byte and
restarts at the MSB of byte 1. Reset clears the output and bit position but
initializes the byte to 1, as the source's design considerations and tests require.
No handshake, load port or output stalling is introduced.

## Reset Behavior

`rst` is asynchronous, active low. Assertion immediately restores the initial state. Byte=1, bit position=0 and serial output=0.
After reset release, resume the edge-by-edge behavior above.

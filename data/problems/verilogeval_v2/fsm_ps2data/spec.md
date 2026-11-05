# TopModule

Capture one byte per edge.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input [7:0] value,
  input reset,
  output [23:0] out_bytes,
  output done
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `done`: 1.
- `out_bytes`: 24.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Capture one byte per edge. Discard bytes until bit 3 is one, then accept that byte and the following two bytes unconditionally as one packet. done is high for the cycle after byte three. The next byte, sampled during done, may immediately start the next packet. Reset discards a partial packet. out_bytes packs the first byte in bits 23:16, second in 15:8 and third in 7:0. The source leaves out_bytes unspecified when done=0; this task explicitly defines it as zero then.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. Search for byte one; done=0. Invalid out_bytes=0; hidden old bytes are irrelevant until a complete new packet arrives.
After reset release, resume the edge-by-edge behavior above.

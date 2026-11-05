# TopModule

Implement a five-bit maximal-length right-shifting Galois LFSR with tap positions 5 and 3.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  output reg [4:0] q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 5.

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

- `TopModule`: the complete native module above.

## Functional Behavior

Implement a five-bit maximal-length right-shifting Galois LFSR with tap positions 5 and 3. From old bits q[4:0], the next bits [4:0] are `{q[0],q[4],q[3]^q[0],q[2],q[1]}`. Advance on every rising edge; reset has priority. Starting at seed 1, all 31 nonzero states occur once before the sequence repeats. The output continuously exposes the current state.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. The complete register and output reset to 1.
After reset release, resume the edge-by-edge behavior above.

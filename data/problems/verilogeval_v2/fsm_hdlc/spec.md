# TopModule

Detect HDLC runs of ones.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input value,
  output disc,
  output flag,
  output err
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

- `disc`, `err`, `flag`: 1.

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

Detect HDLC runs of ones. A zero following exactly five consecutive ones produces a one-cycle disc pulse; after six ones it produces a one-cycle flag pulse. Seven or more consecutive ones assert err, which remains high until a zero is captured. These are Moore outputs for the cycle after the decisive edge. A terminating zero begins a new run; reset acts as an initial zero.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. Zero consecutive ones; disc=flag=err=0.
After reset release, resume the edge-by-edge behavior above.

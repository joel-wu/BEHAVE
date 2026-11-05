# TopModule

Expose the current 32-bit history.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input areset,
  input predict_valid,
  input predict_taken,
  output logic [31:0] predict_history,

  input train_mispredicted,
  input train_taken,
  input [31:0] train_history
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, predict_valid, predict_taken, train_mispredicted, train_taken, train_history): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `predict_history`: 32.

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

Expose the current 32-bit history. At an edge, train_mispredicted replaces it with {train_history[30:0],train_taken}, taking priority over a simultaneous prediction. Otherwise predict_valid appends predict_taken and drops the oldest bit. Hold when neither condition applies.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`areset` is asynchronous, active high. Assertion immediately restores the initial state. History=0.
After reset release, resume the edge-by-edge behavior above.

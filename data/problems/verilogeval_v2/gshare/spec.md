# TopModule

Seven-bit gshare predictor: index the 128-entry two-bit saturating-counter PHT with predict_pc XOR current history.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input areset,

  input predict_valid,
  input [6:0] predict_pc,
  output predict_taken,
  output [6:0] predict_history,

  input train_valid,
  input train_taken,
  input train_mispredicted,
  input [6:0] train_history,
  input [6:0] train_pc
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, predict_valid, predict_pc, train_valid, train_taken, train_mispredicted, train_history, train_pc): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `predict_history`: 7.
- `predict_taken`: 1.

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

Seven-bit gshare predictor: index the 128-entry two-bit saturating-counter PHT with predict_pc XOR current history. For predict_valid=1, combinationally return the old history and the counter MSB, then append that prediction at the edge. Training independently indexes train_pc XOR train_history and saturates toward taken (3) or not-taken (0). Training with a misprediction rolls history back to {train_history[5:0],train_taken}, overriding a simultaneous speculative update. On a same-entry prediction/training collision, prediction uses the OLD counter. When predict_valid=0 explicitly output zeros (the source leaves these outputs unspecified). Fixed 7-bit configuration; source internal state-label/size parameters are not task configuration knobs.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`areset` is asynchronous, active high. Assertion immediately restores the initial state. History=0; all 128 counters=1 (weakly not taken).
After reset release, resume the edge-by-edge behavior above.

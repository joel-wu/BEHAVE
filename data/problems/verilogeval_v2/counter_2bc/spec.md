# TopModule

On every rising edge, hold if `train_valid=0`; otherwise increment toward 3 if `train_taken=1`, or decrement toward 0 if it is 0.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input areset,
  input train_valid,
  input train_taken,
  output logic [1:0] state
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, train_valid, train_taken): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `state`: 2.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

On every rising edge, hold if `train_valid=0`; otherwise increment toward 3 if `train_taken=1`, or decrement toward 0 if it is 0. Saturate at both endpoints; do not wrap. `state` continuously exposes the current two-bit value. Asynchronous reset sets weakly-not-taken, value 1, and has priority.

## Reset Behavior

`areset` is asynchronous, active high. Assertion immediately restores the initial state. The complete register and output reset to 1.
After reset release, resume the edge-by-edge behavior above.

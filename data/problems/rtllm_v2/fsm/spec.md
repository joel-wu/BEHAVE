# Mealy Sequence Detector

Detect the bit pattern `10011` with a Mealy finite-state detector. Every rising edge of `CLK` samples `IN`; detections may overlap. There is no handshake or clock enable.

## Interface

### RTL interface

```systemverilog
module fsm (
    input logic IN,
    input logic CLK,
    input logic RST,
    output logic MATCH
);
```

### Behavior interface

```python
class Fsm:
    def __init__(self): ...
    def reset(self): ...
    def process(self, IN): ...
```

Each call returns `{"MATCH": bit}` for the current non-reset edge and then updates the history. `IN` and `MATCH` are unsigned one-bit values. Port spelling and case are significant.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `MATCH`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `CLK`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `fsm`: mealy sequence detector.

## Functional Behavior

`MATCH` is 1 before a rising edge exactly when the previous sampled history ends in `1001` and the current `IN` is 1. The current bit then joins the history at that edge. Inputs `100110011` produce matches on samples 5 and 9. A match does not clear the history: its final `1` can start the next occurrence. All other input samples produce 0.

## Reset Behavior

`RST` is asynchronous and active high. It clears the history and forces `MATCH` to zero. After release, no bits from before reset participate in a match.

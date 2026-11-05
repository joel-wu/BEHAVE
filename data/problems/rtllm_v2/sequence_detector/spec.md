# Moore Sequence Detector

Detect the serial pattern `1001` with a clocked Moore detector. Input sampling continues on every rising edge of `clk`, without a handshake or enable.

## Interface

### RTL interface

```systemverilog
module sequence_detector (
    input logic clk,
    input logic rst_n,
    input logic data_in,
    output logic sequence_detected
);
```

### Behavior interface

```python
class SequenceDetector:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

One call represents one non-reset rising edge. It first returns `{"sequence_detected": bit}` from the previous sampled history, then incorporates `data_in`. The input and output are unsigned one-bit values. The first observation after reset is zero.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `sequence_detected`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `sequence_detector`: moore sequence detector.

## Functional Behavior

After an edge captures the final bit of `1001`, `sequence_detected` is high until the next edge. It depends only on sampled history, not on the current unsampled input. Overlapping matches are allowed: `1001001` completes matches at edges 4 and 7. The signal is not sticky. A pre-edge observation sees each detection at the following edge: input samples `10010010` yield observations `00001001`.

## Reset Behavior

`rst_n` is asynchronous and active low, as in the original RTL and testbench. Assertion clears the sampled history and output. No records are compared during reset; the first subsequent call starts with an empty history.

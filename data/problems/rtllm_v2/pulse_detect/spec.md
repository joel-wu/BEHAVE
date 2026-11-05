# Pulse Detector

Detect the pattern `010` in a continuous one-bit stream, including overlapping occurrences. The native interface samples on every rising edge and has no valid, enable, or ready signals.

## Interface

### RTL interface

```systemverilog
module pulse_detect (
    input logic clk,
    input logic rst_n,
    input logic data_in,
    output logic data_out
);
```

### Behavior interface

```python
class PulseDetect:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

One call represents one non-reset rising edge. It returns `{"data_out": pulse}` from the previous history and current input, then records that input. All data fields are unsigned one-bit values.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `pulse_detect`: pulse detector.

## Functional Behavior

`data_out` is a Mealy output: before each rising edge it is 1 exactly when two previously sampled bits are `01` and the current `data_in` is 0. The first two samples after reset cannot match: reset zeros are not input samples. For inputs `01010`, the pre-edge output sequence is `00101`; inputs `10` produce `00`. State advances on every rising edge, whether or not a match occurs.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears the input history and its sample count, and forces `data_out` low. No records are compared during reset. Detection resumes with an empty history.

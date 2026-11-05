# Positive and Negative Edge Detector

Detect changes between consecutive samples of a glitch-free, debounced input. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module sync_pos_neg_edge_detector (
    input logic i_clk,
    input logic i_rstb,
    input logic i_detection_signal,
    output logic o_positive_edge_detected,
    output logic o_negative_edge_detected
);
```

### Behavior interface

```python
class SyncPosNegEdgeDetector:
    def __init__(self): ...
    def reset(self): ...
    def process(self, i_detection_signal): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_negative_edge_detected`, `o_positive_edge_detected`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `i_clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `sync_pos_neg_edge_detector`: the native clocked datapath and its state.

## Functional Behavior

Let `previous` be the input sampled at the preceding rising edge, initially zero. At the current edge register `o_positive_edge_detected = !previous && i_detection_signal` and `o_negative_edge_detected = previous && !i_detection_signal`, then remember the current input. A steady level clears both pulses at the next edge. The outputs cannot both be one. The first high input after reset produces a positive-edge pulse. No debounce filter or extra synchronizer latency is introduced.

## Reset Behavior

`i_rstb` is asynchronous and active low. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `i_clk` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.

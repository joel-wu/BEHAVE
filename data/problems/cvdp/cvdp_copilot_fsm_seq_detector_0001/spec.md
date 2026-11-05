# Overlapping Sequence Detector

Detect the serial pattern `10110001` with an eight-state, three-bit FSM. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module fsm_seq_detector (
    input logic clk_in,
    input logic rst_in,
    input logic seq_in,
    output logic seq_detected
);
```

### Behavior interface

```python
class FsmSeqDetector:
    def __init__(self): ...
    def reset(self): ...
    def process(self, seq_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `seq_detected`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk_in`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `fsm_seq_detector`: the native clocked datapath and its state.

## Functional Behavior

Consume `seq_in` at every rising edge. State S0 through S7 represents the length of the longest suffix of earlier samples matching a proper prefix of `10110001`. On the edge consuming the final 1 in state S7, register `seq_detected=1` and return to S1, retaining the overlapping final 1. Otherwise register zero. Thus `101100010110001` produces two pulses, after samples 8 and 15. The output is a one-cycle pulse, not a persistent notification waiting for acknowledgement. The state transitions for input 0 / 1 are: S0→S0/S1, S1→S2/S1, S2→S0/S3, S3→S2/S4, S4→S5/S1, S5→S6/S3, S6→S7/S1, S7→S0/S1.

## Reset Behavior

`rst_in` is asynchronous and active high. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `clk_in` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.

# palindrome_detect

Detect overlapping three-bit palindromes in a continuous bit stream.

## Interface

### RTL interface

```systemverilog
module palindrome_detect #(parameter int N = 3) (
    input logic clk, reset, bit_stream,
    output logic palindrome_detected
);
```

### Behavior interface

```python
class PalindromeDetector:
    def __init__(self, N=3): ...
    def reset(self): ...
    def process(self, bit_stream): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `palindrome_detected`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `palindrome_detect`: the complete native module above.

## Functional Behavior

The supported parameter is `N=3`, as in the source task. Capture one bit
on every rising edge. After at least three post-reset samples, assert the output
exactly when the oldest and newest bits of the last three samples match.
Thus 000, 010, 101 and 111 are palindromes. Test overlapping windows on every
edge, including consecutive detections. The output reflects the captured
window after that edge. As an explicit startup completion, output zero until
three samples have been captured; reset padding does not count as input.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

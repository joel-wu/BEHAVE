# FP8 Delayed-Scaling Metadata Update

Update a four-slot absolute-maximum history and the scale used for subsequent
E4M3 quantization. Each transaction combines a new observation with the current
epoch's accumulated maximum, computes a scale from the complete history, and
advances the history. History and previous scale are explicit inputs; the block
does not quantize tensor elements or retain hidden algorithmic state.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_fp8_fp8_delayed_scaling #(
    parameter int unsigned HISTORY_LEN = 4,
    parameter int unsigned AMAX_WIDTH = 32,
    parameter int unsigned SCALE_WIDTH = 64
) (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [AMAX_WIDTH-1:0] in_amax,
    input logic [SCALE_WIDTH-1:0] in_scale,
    input logic [AMAX_WIDTH-1:0] in_history_0, in_history_1,
    input logic [AMAX_WIDTH-1:0] in_history_2, in_history_3,
    output logic out_valid,
    input logic out_ready,
    output logic [SCALE_WIDTH-1:0] out_scale, out_scale_inv,
    output logic [AMAX_WIDTH-1:0] out_history_0, out_history_1,
    output logic [AMAX_WIDTH-1:0] out_history_2, out_history_3
);
```

The supported configuration is `HISTORY_LEN=4`, `AMAX_WIDTH=32`,
`SCALE_WIDTH=64`. Maxima use unsigned Q16.16: a word `a` represents `a/2^16`.
Scale and reciprocal use unsigned Q32.32: a word `s` represents `s/2^32`.
All input bit patterns are defined, including zero. These are fixed-point
values, not IEEE floating-point encodings.

### Behavior interface

```python
class AiMlQuantFp8Fp8DelayedScaling:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_amax, in_scale, in_history_0,
                in_history_1, in_history_2, in_history_3): ...
```

Return one dictionary containing `out_scale`, `out_scale_inv`, and
`out_history_0` through `out_history_3`, as unsigned words of the declared widths.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_history_0`, `out_history_1`, `out_history_2`, `out_history_3`: AMAX_WIDTH.
- `out_scale`, `out_scale_inv`: SCALE_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The declared top-level module implements the metadata transform. Its internal
arithmetic schedule and latency are not prescribed.

## Functional Behavior

`in_history_0` accumulates the current epoch; slots 1, 2, 3 hold previous
epochs, oldest first. `in_amax` is an additional observation for the current
epoch. In raw integer arithmetic, define

```
h0 = max(in_amax, in_history_0)
A  = max(h0, in_history_1, in_history_2, in_history_3)
S  = in_scale                         if A == 0
     floor((448 * 2^48) / A)          otherwise
I  = 0                                if S == 0
     min(2^64 - 1, floor(2^64 / S))   otherwise
```

Emit `(out_scale, out_scale_inv) = (S, I)`. Thus positive-history scaling
approximates `448 / amax` with margin zero, rounding down to Q32.32. The
reciprocal is computed from the **emitted** scale, not the unrounded ratio.
Widen intermediate arithmetic; do not wrap before division. All-zero history
preserves the previous scale. A preserved zero scale has reciprocal zero;
reciprocal overflow saturates instead of wrapping. These rules make those
otherwise exceptional metadata inputs explicit.

Only after selecting `A`, rotate left and clear the new current slot:

```
(out_history_0, out_history_1, out_history_2, out_history_3)
    = (0, in_history_2, in_history_3, h0)
```

The discarded oldest entry still contributes to this transaction's maximum.
The caller can feed the returned history and scale into the next transaction;
there is no implicit feedback. For example, with zero history and
`in_amax=896*2^16`, the emitted scale is `2^31` (0.5), its reciprocal is `2^33`
(2.0), and the new history is `[0,0,0,896*2^16]`. If instead only the oldest
entry is `896*2^16`, it yields the same scale before being discarded.

Transfers occur on rising clock edges with `valid && ready` and reset inactive.
Each accepted input produces one output, in order. Input valid and payload stay
stable until accepted; output valid and payload stay stable while stalled.
Gaps, backpressure and simultaneous input/output transfers do not alter results.

## Reset Behavior

Asynchronous active-high `rst` clears all output words and `out_valid`, cancels
pending output, and restores `in_ready=1`. No transfer occurs while reset is
asserted. A post-reset transaction depends only on its explicit inputs.

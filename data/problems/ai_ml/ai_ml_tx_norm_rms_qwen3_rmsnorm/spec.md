# ai_ml_tx_norm_rms_qwen3_rmsnorm

A 4-element fixed-point adaptation of RMSNorm: normalize by the root-mean-square magnitude, then apply a per-element weight. This retains the source RMSNorm operation, not its tensor size or floating-point format. Every accepted vector produces one ordered output vector.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_norm_rms_qwen3_rmsnorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_x0,
    input logic [15:0] in_x1,
    input logic [15:0] in_x2,
    input logic [15:0] in_x3,
    input logic [15:0] in_w0,
    input logic [15:0] in_w1,
    input logic [15:0] in_w2,
    input logic [15:0] in_w3,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
);
```

### Behavior interface

```python
class ai_ml_tx_norm_rms_qwen3_rmsnorm:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_w0, in_w1, in_w2, in_w3): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`ai_ml_tx_norm_rms_qwen3_rmsnorm` is the only required module. There are no parameters or external memories.

## Functional Behavior

Each data port carries a signed two's-complement 16-bit Q15 number (integer code divided by 32768). Every bit pattern is legal.

Let `x[i]` and `w[i]` be the signed integer codes from `in_xi` and `in_wi`. Define `TZ(a/b)` as integer division toward zero, `isqrt(a)` as the greatest nonnegative integer whose square is at most `a`, and `sat16(a) = min(32767,max(-32768,a))`. Compute with exact integer intermediates:

1. `d = floor(sum(x[i]*x[i], i=0..3)/4) + 1074`.
2. `r = isqrt(floor(2^60/d))`.
3. `n[i] = TZ(x[i]*r/2^15)`.
4. `y[i] = sat16(TZ(n[i]*w[i]/2^15))`.

Return `y[i] mod 65536` on `out_yi`. Epsilon is exactly `1074/2^30`; therefore `d` is positive even for a zero vector. The internal Q15 scale `r` is **not** restricted to a 16-bit data code: small input magnitudes require values greater than 32767. Only the final output saturates. For example, equal positive input codes near 8192 with weight codes 16384 yield output codes near 16384, not near 4096. A zero vector yields all zero outputs.

Transfers occur on rising clock edges with `in_valid && in_ready` or `out_valid && out_ready`. Hold input valid and payload until accepted; the implementation must hold an unaccepted output stable. Latency is implementation-defined within the task run budget; there is no data dependence across vectors.

## Reset Behavior

Active-high synchronous reset discards any pending output, clears all output data to zero, sets `out_valid=0`, and makes `in_ready=1`. Reset has priority over transfers.

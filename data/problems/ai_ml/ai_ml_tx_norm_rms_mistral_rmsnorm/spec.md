# ai_ml_tx_norm_rms_mistral_rmsnorm

A 8-element fixed-point adaptation of RMSNorm: normalize by the root-mean-square magnitude, then apply a per-element weight. This retains the source RMSNorm operation, not its tensor size or floating-point format. Every accepted vector produces one ordered output vector.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_norm_rms_mistral_rmsnorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_hidden_0,
    input logic [15:0] in_hidden_1,
    input logic [15:0] in_hidden_2,
    input logic [15:0] in_hidden_3,
    input logic [15:0] in_hidden_4,
    input logic [15:0] in_hidden_5,
    input logic [15:0] in_hidden_6,
    input logic [15:0] in_hidden_7,
    input logic [15:0] in_weight_0,
    input logic [15:0] in_weight_1,
    input logic [15:0] in_weight_2,
    input logic [15:0] in_weight_3,
    input logic [15:0] in_weight_4,
    input logic [15:0] in_weight_5,
    input logic [15:0] in_weight_6,
    input logic [15:0] in_weight_7,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_hidden_0,
    output logic [15:0] out_hidden_1,
    output logic [15:0] out_hidden_2,
    output logic [15:0] out_hidden_3,
    output logic [15:0] out_hidden_4,
    output logic [15:0] out_hidden_5,
    output logic [15:0] out_hidden_6,
    output logic [15:0] out_hidden_7
);
```

### Behavior interface

```python
class AiMlTxNormRmsMistralRmsnorm:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hidden_0, in_hidden_1, in_hidden_2, in_hidden_3, in_hidden_4, in_hidden_5, in_hidden_6, in_hidden_7, in_weight_0, in_weight_1, in_weight_2, in_weight_3, in_weight_4, in_weight_5, in_weight_6, in_weight_7): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_hidden_0`, `out_hidden_1`, `out_hidden_2`, `out_hidden_3`, `out_hidden_4`,
  `out_hidden_5`, `out_hidden_6`, `out_hidden_7`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`ai_ml_tx_norm_rms_mistral_rmsnorm` is the only required module. There are no parameters or external memories.

## Functional Behavior

Each data port carries a signed two's-complement 16-bit Q15 number (integer code divided by 32768). Every bit pattern is legal.

Let `x[i]` and `w[i]` be the signed integer codes from `in_hidden_i` and `in_weight_i`. Define `TZ(a/b)` as integer division toward zero, `isqrt(a)` as the greatest nonnegative integer whose square is at most `a`, and `sat16(a) = min(32767,max(-32768,a))`. Compute with exact integer intermediates:

1. `d = floor(sum(x[i]*x[i], i=0..7)/8) + 1074`.
2. `r = isqrt(floor(2^60/d))`.
3. `n[i] = TZ(x[i]*r/2^15)`.
4. `y[i] = sat16(TZ(n[i]*w[i]/2^15))`.

Return `y[i] mod 65536` on `out_hidden_i`. Epsilon is exactly `1074/2^30`; therefore `d` is positive even for a zero vector. The internal Q15 scale `r` is **not** restricted to a 16-bit data code: small input magnitudes require values greater than 32767. Only the final output saturates. For example, equal positive input codes near 8192 with weight codes 16384 yield output codes near 16384, not near 4096. A zero vector yields all zero outputs.

Transfers occur on rising clock edges with `in_valid && in_ready` or `out_valid && out_ready`. Hold input valid and payload until accepted; the implementation must hold an unaccepted output stable. Latency is implementation-defined within the task run budget; there is no data dependence across vectors.

## Reset Behavior

Active-high asynchronous reset discards any pending output, clears all output data to zero, sets `out_valid=0`, and makes `in_ready=1`. Reset has priority over transfers.

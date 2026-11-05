# GLA Log-Sigmoid Decay Gate

Compute a fixed-point version of `log(sigmoid(x))/16`, followed by an optional lower clamp. The negative tail remains approximately x/16; it is not a constant bucket.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_gla_gla_decay_gating (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic signed [15:0] in_gk_logit,
    input  logic signed [15:0] in_clamp_min,
    input  logic        in_clamp_en,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [15:0] out_gk
);
```

### Behavior interface

```python
class GLADecayGating:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_gk_logit, in_clamp_min, in_clamp_en):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_gk`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

`in_gk_logit`, `in_clamp_min` and `out_gk` encode signed Q8.8 values. `in_clamp_en` is a Boolean, not a Q8.8 value.

The following integer algorithm specifies the exact answer. Let `x` be the signed input code and `a=abs(x)`. The fixed table is `T[j]=RNE(65536*ln(1+exp(-j/16)))`, for j=0..128; RNE means nearest integer with ties to even. Its literal values are:

```text
45426, 43410, 41458, 39570, 37745, 35983, 34283, 32646, 31069, 29553, 28095, 26696, 25354, 24068, 22836, 21657, 20530, 19453, 18425, 17445, 16510, 15620, 14773, 13966, 13200, 12471, 11780, 11123, 10500, 9910, 9350, 8820, 8318, 7843, 7394, 6969, 6567, 6187, 5829, 5490, 5170, 4868, 4583, 4315, 4061, 3822, 3597, 3384, 3184, 2996, 2818, 2651, 2493, 2345, 2205, 2074, 1950, 1833, 1724, 1620, 1523, 1432, 1346, 1265, 1189, 1118, 1051, 988, 928, 872, 820, 770, 724, 680, 639, 601, 565, 530, 498, 468, 440, 414, 389, 365, 343, 322, 303, 284, 267, 251, 236, 222, 208, 196, 184, 173, 162, 152, 143, 135, 126, 119, 112, 105, 98, 92, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22
```

For `a<2048`, let `j=a//16`, `r=a%16`, and `C=(16-r)*T[j]+r*T[j+1]`. Otherwise `C=0`. Compute

```
magnitude = max(-x, 0)*4096 + C
y = -RNE(magnitude / 65536)
if in_clamp_en: y = max(y, signed(in_clamp_min))
out_gk = y mod 65536
```

All intermediates are exact integers. The optional clamp is applied after rounding. All 65536 input codes are admitted; no saturation is required. This interpolation approximates the real function while defining deterministic bitwise results; it is not a call to a host floating-point logarithm. Examples without clamp: x=0 gives -11, x=-2048 gives -128, x=-32768 gives -2048, and x=2048 gives 0 (all raw Q8.8 codes).

## Reset Behavior

Synchronous active-high `rst` clears pending output validity and all output payload fields to zero. At a reset clock edge, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.

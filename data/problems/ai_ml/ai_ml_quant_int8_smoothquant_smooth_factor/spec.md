# Per-channel SmoothQuant smoothing factor

Compute the per-channel scale `(A + epsilon)^alpha / (W + epsilon)^(1-alpha)`
used to move quantization difficulty from activations to weights. `A` and `W`
are already-collected activation and weight absolute maxima. This task is the
scale operator, not collection of calibration statistics or weight quantization.
It supports seventeen alpha values and fixes the floating-point evaluation
order so outputs are reproducible without a platform-dependent `pow` routine.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_int8_smoothquant_smooth_factor (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_act_absmax,
    input  logic [31:0] in_weight_absmax,
    input  logic [4:0]  in_alpha,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_smoothing_factor,
    output logic        out_error
);
```

### Behavior interface

```python
class AiMlQuantInt8SmoothquantSmoothFactor:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_act_absmax, in_weight_absmax, in_alpha): ...
```

Return one dictionary with `out_smoothing_factor` and `out_error` for each call.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_smoothing_factor`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`ai_ml_quant_int8_smoothquant_smooth_factor` is the top level; it has no
external memory or retained calibration state.

## Functional Behavior

The two maxima and the output carry raw IEEE-754 binary32 words. `in_alpha`
is an unsigned integer `a`: valid values 0 through 16 mean `alpha = a/16`.
It is not itself a floating-point word.

Negative nonzero maxima, NaNs, infinities, or `a > 16` return
`out_smoothing_factor = 0, out_error = 1`. Either sign of zero is valid and
is treated as positive zero. Positive subnormal maxima retain their numerical value.
All remaining requests return `out_error = 0` and use this exact algorithm:

1. Let `A0 = add(A, epsilon)` and `W0 = add(W, epsilon)`, with
   `epsilon = 2^-23` (binary32 word `0x34000000`).
2. If `a = 0`, return `div(1, W0)`. If `a = 16`, return `A0`.
3. Otherwise take four successive `sqrt` operations on each of `A0` and `W0`,
   obtaining `rA` and `rW`. Start `p = q = 1`. In increasing order of `k`
   from 0 through 14, replace `p` by `mul(p, rA)` when `k < a` and replace
   `q` by `mul(q, rW)` when `k < 16-a`. Return `div(p, q)`.

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.
The four roots implement the sixteenth root and the repeated products implement
the two powers. This operation order, rather than a library `pow` call,
defines bit-exact matching. For example, `a=8` balances activation and weight
scales; it does not return a constant. With `A=W=0`, it returns exactly 1.

Accept inputs and outputs on rising edges when their `valid` and `ready` are
both high. Return exactly one response per accepted request, in order. Hold
`out_valid`, `out_smoothing_factor`, and `out_error` while output is stalled.
Latency and initiation interval are implementation choices; a serial design
is permitted.

## Reset Behavior

Reset is asynchronous, active high, and suppresses input acceptance. It clears
all pending work and output, setting `out_valid`, `out_smoothing_factor`, and
`out_error` to zero. On release the module is idle and ready for a new request.

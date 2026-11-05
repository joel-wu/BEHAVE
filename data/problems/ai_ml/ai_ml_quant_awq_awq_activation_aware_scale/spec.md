# AWQ Activation Pre-Scale Reciprocal

Compute the reciprocal of one supplied equalization scale, as used to pre-scale activations during AWQ inference. Scale search and weight equalization are not part of this scalar stage.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_awq_awq_activation_aware_scale (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_equalization_scale,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_act_pre_scale
);
```

### Behavior interface

```python
class AWQActivationAwareScale:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_equalization_scale):
        pass
```

The `process()` method returns one dict per accepted input transaction with this key:
- `out_act_pre_scale`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_act_pre_scale`: 32.

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

Input and output are binary32 words. Return the correctly rounded reciprocal
`div(0x3F800000, in_equalization_scale)` under the rules below. Signed zero
returns same-sign infinity; signed infinity returns same-sign zero; a NaN
returns `0x7FC00000`.

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

Examples: +1 -> +1, +2 -> +0.5, -1 -> -1. The smallest positive
subnormal (`0x00000001`) overflows to +infinity, but the largest positive
subnormal (`0x007FFFFF`) returns finite `0x7E800001`. Maximal finite positive
(`0x7F7FFFFF`) returns subnormal `0x00200000`, not zero. The source uses positive
learned scales; negative and special words are explicit total-domain task
extensions. No numerical state persists. Scale search is not part of this task.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.

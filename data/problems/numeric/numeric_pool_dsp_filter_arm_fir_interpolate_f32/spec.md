# FIR Interpolator F32 L4 P4

A fourfold FIR interpolator upsamples a stream with a fixed nonzero sixteen-tap
binomial low-pass filter. Every input produces four ordered FP32 samples;
three previous inputs contribute to the next group.

## Interface

### RTL interface

```systemverilog
module fir_interpolator_f32_l4_p4 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_sample
);
```

### Behavior interface

```python
class FirInterpolatorF32L4P4:
    def __init__(self):
        pass
    def reset(self):
        pass
    def process(self, in_sample):
        pass
```

Each word is a raw binary32 bit pattern. `process` returns four dictionaries,
each containing `out_sample`, in the order specified below.

Return a list of exactly four dictionaries in the specified polyphase output order for
every accepted input sample. Never return None or a bare dictionary. Filter history
persists across calls and is cleared by reset.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fir_interpolator_f32_l4_p4`: stateful interpolation and output transport.

## Functional Behavior

### Filter and state

The chronological impulse response is `b[j] = binomial(15,j)/8192`, for
`j = 0..15`. Its exact numerators in increasing index order are
`[1,15,105,455,1365,3003,5005,6435,6435,5005,3003,1365,455,105,15,1]`.
All coefficients are exactly representable in binary32; their sum is 4.
This short filter's steady DC phase gains are `[129,127,127,129]/128`.

Let `history[0..3]` hold samples from oldest to newest. On acceptance of `x`,
replace it by `[history[1], history[2], history[3], x]`. For each phase
`p = 0,1,2,3`, compute using the updated history:

```text
s = +0
for k = 0,1,2,3:
    s = add(s, mul(history[k], b[4*(3-k)+p]))
emit {out_sample: s}
```

This is zero insertion by four followed by FIR filtering, with the products
accumulated oldest-to-newest. An impulse `1,0,0,0` produces the sixteen
coefficients in chronological order. Do not reverse the four output phases.

### Arithmetic

`add` and `mul` round separately to binary32 using round-to-nearest,
ties-to-even; no fused multiply-add or reassociation is permitted.
Subnormal operands retain their numerical value; underflow is gradual,
including nearest-even rounding at zero/subnormal and subnormal/normal
boundaries. Overflow yields signed infinity. Compare the output encodings
exactly, without a tolerance.
Any NaN operand, zero times infinity, or addition of opposite infinities
returns canonical NaN `0x7FC00000` (a project sign/payload convention). Multiplication uses the XOR sign for zero
and infinity. Addition of two negative zeros gives negative zero; other exact
cancellation gives positive zero. All 32-bit input patterns are defined.

### Transfers

Inputs transfer on rising edges with `in_valid && in_ready`; outputs transfer
with `out_valid && out_ready`. The producer holds input valid and payload
stable while stalled, and the module holds each pending output likewise.
Every accepted input produces exactly four outputs, with no interleaving or
reordering between input groups. Latency is implementation-dependent; work
must complete when the receiver continues servicing outputs.

## Reset Behavior

Reset is synchronous active-high. It clears all history samples to positive
zero and cancels unfinished work and buffered output; `out_valid` and
`out_sample` become zero. The module is ready for a new input after reset.

# BiquadCascadeDf1Q15Sample

This module implements a fixed-configuration Q15 direct-form-I biquad cascade filter that processes exactly one input sample per accepted transaction and produces exactly one output sample. The datapath is a cascade of two second-order sections evaluated in stage order. Each section uses the CMSIS-DSP Q15 DF1 arithmetic contract: 16-bit signed input samples, 16-bit signed coefficients and state, 64-bit accumulation of five 2.30 products, arithmetic right shift by 15 bits, and final saturation to signed 1.15 format.

## Interface

### RTL interface

```systemverilog
module biquad_cascade_df1_q15_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);
```

### Behavior interface

```python
class BiquadCascadeDf1Q15Sample:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x):
        pass
```

The `process()` return value is one dict per call with key `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `biquad_cascade_df1_q15_sample`: top-level single-sample two-stage Q15 DF1 biquad cascade.

## Functional Behavior

The module has one input sample port `in_x` and one output sample port `out_y`, both carrying 16-bit two's-complement signed Q15 values as raw bit patterns.

The filter contains exactly two cascaded biquad stages. The coefficients are fixed constants:

- Stage 0:
  - `b0 = 0x2000`
  - `b1 = 0x1000`
  - `b2 = 0x0800`
  - `a1 = 0x0400`
  - `a2 = 0x0200`
- Stage 1:
  - `b0 = 0x1800`
  - `b1 = 0x0C00`
  - `b2 = 0x0600`
  - `a1 = 0x0300`
  - `a2 = 0x0100`

These values are interpreted as signed 16-bit integers. In Q15 notation they represent positive fractional coefficients.

Each stage owns four 16-bit signed state registers:

- `Xn1`: previous input sample `x[n-1]`
- `Xn2`: input sample `x[n-2]`
- `Yn1`: previous output sample `y[n-1]`
- `Yn2`: output sample `y[n-2]`

For one accepted input transaction, the module performs the following sequence.

1. Interpret `in_x` as a signed 16-bit integer. This is the input to stage 0.
2. For stage 0, compute:
   - `acc0 = b0*x0 + b1*Xn1_0 + b2*Xn2_0 + a1*Yn1_0 + a2*Yn2_0`
3. `acc0` is formed with exact signed integer arithmetic. Each multiplication is signed 16-bit by signed 16-bit, producing a signed 32-bit 2.30 product. The five products are summed exactly into a signed 64-bit accumulator. No intermediate saturation or truncation is applied before the final shift.
4. Compute `shifted0 = acc0 >> 15` using arithmetic right shift.
5. Saturate `shifted0` to the signed 16-bit range `[-32768, 32767]`. Call the saturated result `y0`.
6. Update stage-0 state:
   - `Xn2_0 := Xn1_0`
   - `Xn1_0 := x0`
   - `Yn2_0 := Yn1_0`
   - `Yn1_0 := y0`
7. Use `y0` as the input to stage 1, named `x1`.
8. For stage 1, compute:
   - `acc1 = b0*x1 + b1*Xn1_1 + b2*Xn2_1 + a1*Yn1_1 + a2*Yn2_1`
   with the stage-1 coefficients and the old stage-1 state values.
9. Compute `shifted1 = acc1 >> 15` using arithmetic right shift.
10. Saturate `shifted1` to the signed 16-bit range `[-32768, 32767]`. Call the saturated result `y1`.
11. Update stage-1 state:
    - `Xn2_1 := Xn1_1`
    - `Xn1_1 := x1`
    - `Yn2_1 := Yn1_1`
    - `Yn1_1 := y1`
12. Produce one output transaction with `out_y = y1` as a 16-bit unsigned bit pattern carrying the two's-complement encoding of the signed result.

State reads for a stage use the values stored before the current transaction. State writes from stage 0 do not affect stage 1 except through the explicit cascade signal `x1 = y0`.

The module is strictly 1:1: every accepted input transaction produces exactly one output transaction.

Edge cases:
- Positive overflow after the `>> 15` shift saturates to `0x7FFF`.
- Negative overflow after the `>> 15` shift saturates to `0x8000`.
- `in_x = 0` is processed normally through the recurrence; output may be non-zero if stored state is non-zero.
- No coefficient reload, stage-count change, bypass mode, or post-shift programming is supported.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `out_y` is set to `0x0000`.
- Stage-0 state registers `Xn1_0`, `Xn2_0`, `Yn1_0`, `Yn2_0` are all set to `0x0000`.
- Stage-1 state registers `Xn1_1`, `Xn2_1`, `Yn1_1`, `Yn2_1` are all set to `0x0000`.

After reset, the first accepted input sample is processed with all state values equal to zero.

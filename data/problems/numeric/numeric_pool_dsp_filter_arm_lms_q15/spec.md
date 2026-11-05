# Q15 LMS Adaptive Filter Step

This module performs one sample update of a Q15 least-mean-squares adaptive FIR filter with a fixed 4-tap datapath. Each accepted input transaction supplies one new input sample and one reference sample. The module computes the filter output as a 4-term multiply-accumulate using Q15 coefficients and Q15 state samples, forms the Q15 error, computes the Q15 adaptation factor `alpha = (error * mu) >> 15`, updates all four coefficients with saturated Q15 arithmetic, and advances the state history by one sample.

## Interface

### RTL interface

```systemverilog
module q15_lms_adaptive_filter_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,
    input  logic [15:0] in_ref,
    input  logic [15:0] in_mu,
    input  logic [3:0]  in_post_shift,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample,
    output logic [15:0] out_error
);
```

### Behavior interface

```python
class Q15LmsAdaptiveFilterStep:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample, in_ref, in_mu, in_post_shift):
        pass
```

The return dictionary keys are `out_sample` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`, `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_lms_adaptive_filter_step`: top-level single-sample 4-tap Q15 LMS adaptive filter kernel.

## Functional Behavior

The module is stateful. Architectural state consists of:

- Four Q15 filter coefficients `coeff[0..3]`
- Four Q15 state samples `state[0..3]`

All ports carry unsigned bit patterns. Every Q15 quantity is interpreted as a signed 16-bit two's-complement integer in 1.15 format when used arithmetically.

This kernel fixes `numTaps = 4`. On each accepted input transaction, the module performs the following steps in order.

1. **Capture the new input sample into the state history**

   Let `x_new` be `in_sample` interpreted as signed Q15.

   Form a temporary sample window:
   - `x[0] = state[0]`
   - `x[1] = state[1]`
   - `x[2] = state[2]`
   - `x[3] = x_new`

   These four values are the samples used for the current output computation.

2. **Filter output accumulation**

   Let `b[i] = coeff[i]` interpreted as signed Q15.

   Compute a signed 64-bit accumulator:
   - `acc = x[0]*b[0] + x[1]*b[1] + x[2]*b[2] + x[3]*b[3]`

   Each product is a signed 2.30 fixed-point value formed by 16-bit × 16-bit multiplication. The sum is exact in signed 64-bit arithmetic.

3. **Accumulator shift and Q15 output conversion**

   Let `post_shift` be the unsigned 4-bit value `in_post_shift`. Valid transactions satisfy `0 <= post_shift <= 15`.

   Define:
   - `lShift = 15 - post_shift`

   Convert the accumulator to Q15 by arithmetic right shift:
   - `y_pre_sat = acc >> lShift`

   Then saturate `y_pre_sat` to the signed 16-bit range `[-32768, 32767]`.

   The saturated result is the output sample `y`, returned on `out_sample` as a 16-bit two's-complement bit pattern.

   This is equivalent to truncating the 2.30 accumulation toward negative infinity by discarding `lShift` low bits, then saturating to Q15.

4. **Error computation**

   Let `ref` be `in_ref` interpreted as signed Q15.

   Compute the signed 16-bit wraparound subtraction:
   - `e = ref - y`

   The subtraction result is reduced modulo `2^16` and then reinterpreted as signed 16-bit two's complement. No saturation is applied.

   Return `e` on `out_error` as a 16-bit two's-complement bit pattern.

5. **Adaptation factor**

   Let `mu` be `in_mu` interpreted as signed Q15.

   Compute:
   - `alpha = (e * mu) >> 15`

   The multiplication is signed 16-bit × 16-bit, producing a signed intermediate wide enough to hold the exact product. The right shift is arithmetic. The final `alpha` value is reduced to signed 16-bit two's complement by keeping the low 16 bits.

6. **Coefficient update**

   Update each coefficient in order `i = 0, 1, 2, 3` using the corresponding sample `x[i]` from step 1:

   - `delta_i = (alpha * x[i]) >> 15`
   - `coef_i_pre_sat = coeff[i] + delta_i`
   - `coeff_next[i] = saturate_signed_16(coef_i_pre_sat)`

   `alpha * x[i]` is an exact signed product before the arithmetic right shift by 15. Each updated coefficient is saturated independently to the signed 16-bit range `[-32768, 32767]`.

7. **State update**

   After coefficient update, advance the state history by one sample:

   - `state_next[0] = x[1]`
   - `state_next[1] = x[2]`
   - `state_next[2] = x[3]`
   - `state_next[3] = 0`

   The stored zero in `state_next[3]` represents the empty write slot for the next transaction. On the next accepted input, step 1 again forms the current sample window as `[state[0], state[1], state[2], in_sample]`.

### Arithmetic definitions

- `saturate_signed_16(v)` returns:
  - `32767` if `v > 32767`
  - `-32768` if `v < -32768`
  - `v` otherwise
- Arithmetic right shift preserves sign.
- Signed 16-bit reinterpretation means masking to 16 bits and interpreting bit 15 as the sign bit.
- All outputs are returned as 16-bit unsigned bit patterns corresponding to signed two's-complement values.

### Edge cases

- `in_post_shift = 15` gives `lShift = 0`, so `y_pre_sat = acc`.
- `in_post_shift = 0` gives `lShift = 15`, matching the standard Q15 truncation of a 2.30 accumulator.
- `out_error` is not saturated; overflow wraps modulo `2^16`.
- Coefficient updates are saturated per tap after addition.
- The filter output is saturated after the accumulator shift.

## Reset Behavior

Reset is asynchronous active-high. On reset, all architectural state is cleared to zero:

- `coeff[0] = coeff[1] = coeff[2] = coeff[3] = 0`
- `state[0] = state[1] = state[2] = state[3] = 0`

Output interface state after reset:

- `out_valid = 0`
- `out_sample = 16'h0000`
- `out_error = 16'h0000`

The module may accept the first input transaction immediately after reset deassertion.

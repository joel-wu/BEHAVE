# Atan2Q31

Atan2Q31 computes the two-argument arctangent of a signed Q1.31 input pair `(y, x)` and returns an angle in signed Q2.29 format over the range `[-π, +π]`. The implementation shall use the same structure as the CMSIS-DSP Q31 kernel: quadrant selection around a first-quadrant core, a ratio reduction to the interval `[0, 1]`, and a 12th-order polynomial evaluated by Horner's rule on that reduced ratio.

## Interface

### RTL interface

```systemverilog
module atan2_q31 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_y,
    input  logic [31:0]  in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_result,
    output logic [1:0]   out_status
);
```

`in_y` and `in_x` are signed 32-bit Q1.31 operands carried as raw two's-complement bit patterns.  
`out_result` is a signed 32-bit Q2.29 angle carried as a raw two's-complement bit pattern.  
`out_status` encoding:
- `2'b00`: success
- `2'b01`: naninf error (`x == 0` and `y == 0`)

### Behavior interface

```python
class Atan2Q31:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_y, in_x):
        pass
```

The returned dict keys are `out_result` and `out_status`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_result`: 32.
- `out_status`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `atan2_q31`: top-level single-transaction atan2 kernel.
- `arm_atan_limited_q31`: first-quadrant polynomial core for a non-negative reduced ratio in `[0, 1]`.
- `arm_atan_q31`: first-/fourth-quadrant kernel using absolute values of `x` and `y`, ratio selection, and sign restoration.

## Functional Behavior

All signed quantities are interpreted as 32-bit two's-complement integers when read from ports. Arithmetic right shift is used for signed right shifts. All saturating negations and saturating left shifts clamp to the signed 32-bit range `[-2^31, 2^31-1]`.

Constants, encoded as signed 32-bit integers in Q2.29:
- `ATANHALF_Q29 = 0x0ED63383`
- `PIHALF_Q29   = 0x3243F6A9`
- `PIQ29        = 0x6487ED51`

Polynomial coefficient table `atan2_coefs_q31[0..12]`, each a signed 32-bit integer:
- `[0]  = 0x00000000`
- `[1]  = 0x7FFFFFFE`
- `[2]  = 0x000001B6`
- `[3]  = 0xD555158E`
- `[4]  = 0x00036463`
- `[5]  = 0x1985F617`
- `[6]  = 0x001992AE`
- `[7]  = 0xEED53A7F`
- `[8]  = 0xF8F15245`
- `[9]  = 0x2215A3A4`
- `[10] = 0xE0FAB004`
- `[11] = 0x0CDD4825`
- `[12] = 0xFDDBC054`

### 1. Top-level special-case handling

For each accepted input transaction:

1. Interpret `x = in_x` and `y = in_y` as signed 32-bit integers.
2. If `x > 0`, compute `result = arm_atan_q31(y, x)` and return success.
3. If `x < 0`:
   - if `y > 0`, compute `result = arm_atan_q31(y, x) + PIQ29`
   - else if `y < 0`, compute `result = arm_atan_q31(y, x) - PIQ29`
   - else (`y == 0`), set `result = PIQ29`
   Return success. The add or subtract is ordinary signed integer addition in mathematical integers; the resulting value fits within signed 32 bits for all reachable cases.
4. If `x == 0`:
   - if `y > 0`, set `result = PIHALF_Q29` and return success
   - else if `y < 0`, set `result = -PIHALF_Q29` and return success
   - else (`y == 0`), set `result = 0` and return `naninf` error status.

### 2. Saturating negation primitive

Whenever this specification says "negate with saturation" for a signed 32-bit value `v`:
- if `v == -2^31`, the result is `2^31 - 1`
- otherwise the result is `-v`

This rule is used when taking absolute values of `x` and `y`, and when restoring the final sign of the first-/fourth-quadrant core result.

### 3. `arm_atan_q31(y, x)`

This function returns a signed Q2.29 angle in `[-π/2, +π/2]`.

1. Initialize `sign = 0`.
2. If `y < 0`:
   - replace `y` by saturating negation of `y`
   - set `sign = 1 - sign`
3. If `x < 0`:
   - replace `x` by saturating negation of `x`
   - set `sign = 1 - sign`
4. After these steps, `x >= 0` and `y >= 0`.

Then choose the reduced ratio:
- If `y > x`, form `ratio = reduced_divide_q31(x, y)` and set
  `res = PIHALF_Q29 - arm_atan_limited_q31(ratio)`.
- Otherwise, form `ratio = reduced_divide_q31(y, x)` and set
  `res = arm_atan_limited_q31(ratio)`.

Finally, if `sign == 1`, replace `res` by its saturating negation. Return `res`.

The branch condition is exactly `y > x`; equality uses the second branch.

### 4. `reduced_divide_q31(num, den)`

This block is the framework-compatible equivalent of the CMSIS pair `arm_divide_q31(..., &ratio, &shift)` followed by the immediate shift application. It shall compute the same normalized quotient that those two steps are intended to produce: a signed Q1.31 approximation of `num / den`.

Inputs:
- `num` and `den` are signed 32-bit integers
- all reachable calls satisfy `0 <= num <= den` and `den > 0`

Computation:
1. Form the non-negative integer numerator `N = num << 31` in mathematical integer precision.
2. Compute the truncated non-negative integer quotient
   `Q = floor(N / den)`.
3. Saturate `Q` to the signed 32-bit range:
   - if `Q > 2^31 - 1`, use `2^31 - 1`
   - else use `Q`
4. Return the saturated value as a signed 32-bit integer.

Because `num <= den`, the returned ratio is in `[0, 0x7FFFFFFF]`. For `num == den`, the result is `0x7FFFFFFF`.

### 5. `arm_atan_limited_q31(x)`

Input `x` is a non-negative signed 32-bit Q1.31 value in `[0, 0x7FFFFFFF]`, representing a real argument in `[0, 1]`.

The polynomial shall be evaluated exactly by this Horner sequence:

- `res = atan2_coefs_q31[12]`
- For `i = 1` to `12` inclusive:
  - `res = ((x * res) >> 31) + atan2_coefs_q31[12 - i]`

Here:
- `x` is treated as a signed 32-bit integer,
- `res` is carried in signed mathematical integer precision across the loop,
- `>> 31` is an arithmetic right shift on the signed product.

After the loop, compute `out = clip_q63_to_q31(res >> 2)` and return it, where:
- `res >> 2` is arithmetic right shift,
- `clip_q63_to_q31(v)` returns:
  - `2^31 - 1` if `v > 2^31 - 1`
  - `-2^31` if `v < -2^31`
  - otherwise `v`

The returned value is a signed 32-bit Q2.29 approximation of `atan(x)`.

### 6. Output encoding

`out_result` shall be the 32-bit two's-complement encoding of `result`.  
`out_status` shall be:
- `0` for every success case listed above
- `1` only for the single input pair `(x, y) = (0, 0)`

No other status values are produced.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst == 1`, the module shall return to its idle state.

Architectural state:
- no persistent computational state is required beyond the input/output handshake state of a 1:1 transaction engine.

Output ports immediately after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_result = 32'h00000000`
- `out_status = 2'b00`

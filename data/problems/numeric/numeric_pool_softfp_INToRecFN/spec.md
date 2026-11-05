# IntToRecFN

IntToRecFN converts one integer input into one recoded floating-point output using the HardFloat integer-to-floating-point conversion algorithm. The input integer is interpreted as either signed two's-complement or unsigned magnitude according to `in_signedIn`. The integer is first transformed into an exact intermediate raw floating-point value, then rounded to a recoded floating-point format with exponent width 8 and significand width 24, using the selected IEEE-754 rounding mode. For the concrete kernel specified here, the output format is recoded IEEE-754 binary32, the integer input width is 32 bits, underflow is impossible, overflow is impossible, and only the inexact exception flag can be asserted.

## Interface

### RTL interface

```systemverilog
module IntToRecFN (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_signedIn,
    input  logic [31:0] in_in,
    input  logic [2:0]  in_roundingMode,
    input  logic        in_detectTininess,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [32:0] out_out,
    output logic [4:0]  out_exceptionFlags
);
```

### Behavior interface

```python
class IntToRecFN:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_signedIn, in_in, in_roundingMode, in_detectTininess): ...
```

The `process()` return value is one dictionary with keys:
- `out_out`
- `out_exceptionFlags`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_exceptionFlags`: 5.
- `out_out`: 33.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `IntToRecFN`: top-level integer-to-recoded-float converter for a 32-bit integer input and recoded binary32 output.

## Functional Behavior

One accepted input transaction produces exactly one output transaction.

Transfers occur on rising edges when valid and ready are both high, outside reset. Output data and flags remain stable with out_valid asserted under backpressure. Results preserve input order; no fixed processing latency is required.

The input integer width is 32 bits.

The output recoded floating-point width is 33 bits, laid out as:

- bit 32: sign
- bits 31:23: recoded exponent, 9 bits
- bits 22:0: trailing significand field, 23 bits

This is the HardFloat recoded representation for an IEEE-754 binary32 datum, with `expWidth = 8` and `sigWidth = 24`.

`in_detectTininess` is accepted but does not affect the result, because this conversion never underflows.

`in_roundingMode` uses the following encodings:

- `000`: round_near_even
- `001`: round_minMag
- `010`: round_min
- `011`: round_max
- `100`: round_near_maxMag
- `110`: round_odd (truncate, then set the retained least-significant bit if inexact)

Reserved encodings `101` and `111` are treated identically to `000` by this task's explicit extension.

The conversion proceeds as follows.

1. Interpret the integer input.

   Let `x` be the 32-bit input bit pattern `in_in`.

   If `in_signedIn = 0`, the integer value is the unsigned integer in the range 0 to 2^32 - 1:
   - `int_value = x`

   If `in_signedIn = 1`, the integer value is the signed two's-complement integer in the range -2^31 to 2^31 - 1:
   - if bit 31 of `x` is 0, `int_value = x`
   - if bit 31 of `x` is 1, `int_value = x - 2^32`

2. Form sign and magnitude.

   - If `int_value < 0`, `sign = 1` and `magnitude = -int_value`
   - Otherwise, `sign = 0` and `magnitude = int_value`

   `magnitude` is therefore an integer in the range 0 to 2^32 - 1.

   The special signed input `0x80000000` with `in_signedIn = 1` yields `int_value = -2147483648`, `sign = 1`, and `magnitude = 2147483648`.

3. Handle zero.

   If `magnitude = 0`, the result is the recoded zero:
   - `out_out = 33'b0`
   - `out_exceptionFlags = 5'b00000`

4. Normalize the nonzero magnitude.

   For `magnitude != 0`, let `p` be the index of the most significant 1 bit in the binary representation of `magnitude`, with bit 0 as the least significant bit. Equivalently:
   - `2^p <= magnitude < 2^(p+1)`

   The reachable range is `0 <= p <= 31`. Rounding can carry into `p_rounded = 32`.

   The exact normalized significand is `magnitude / 2^p`, which lies in `[1, 2)`.

5. Round to a 24-bit significand, including the hidden leading 1.

   The rounded significand is represented as an integer `sig` in the range `2^23` to `2^24 - 1`, unless rounding overflows it to `2^24`, in which case it is renormalized as described below.

   If `p <= 23`, the integer is exactly representable:
   - `sig = magnitude << (23 - p)`
   - `inexact = 0`

   If `p > 23`, let:
   - `shift = p - 23`
   - `trunc = magnitude >> shift`
   - `remainder = magnitude - (trunc << shift)`

   Then `trunc` contains the top 24 bits of the exact significand, including the leading 1.

   Define:
   - `half = 1 << (shift - 1)`
   - `greater_than_half = (remainder > half)`
   - `exactly_half = (remainder == half)`
   - `inexact = (remainder != 0)`

   The increment decision is:

   - round_near_even (`000`): increment iff `greater_than_half` is true, or `exactly_half` is true and bit 0 of `trunc` is 1.
   - round_minMag (`001`): increment never.
   - round_min (`010`): increment iff `sign = 1` and `inexact = 1`.
   - round_max (`011`): increment iff `sign = 0` and `inexact = 1`.
   - round_near_maxMag (`100`): increment iff `greater_than_half` or `exactly_half`.
   - round_odd (`110`): do not increment; instead set bit 0 of `trunc` when inexact.
   - `101`, `111`: same rule as round_near_even.

   Then:
   - `sig_pre = trunc + increment`

   For round_odd, use `sig_pre = trunc | 1` when inexact, and `trunc` otherwise.

   If `sig_pre = 2^24`, renormalize:
   - `sig = 2^23`
   - `p_rounded = p + 1`

   Otherwise:
   - `sig = sig_pre`
   - `p_rounded = p`

   If `p <= 23`, then:
   - `p_rounded = p`

6. Encode the finite IEEE exponent and fraction.

   For `magnitude != 0`, define the standard IEEE binary32 biased exponent:
   - `exp_ieee = p_rounded + 127`

   Define the 23-bit trailing fraction:
   - `frac = sig - 2^23`

   For this kernel, `exp_ieee` is in the range 127 to 159. Rounding the largest unsigned inputs upward can produce exactly 2^32, still a finite normal number.

7. Convert the normal IEEE exponent to HardFloat recoded exponent.

   For a finite normal value, the 9-bit recoded exponent is:
   - `rec_exp = exp_ieee + 129`

   Equivalently:
   - `rec_exp = p_rounded + 256`

   The 33-bit recoded output is:
   - `out_out = (sign << 32) | (rec_exp << 23) | frac`

8. Exception flags.

   `out_exceptionFlags` is a 5-bit field ordered as:
   - bit 4: invalid
   - bit 3: infinite
   - bit 2: overflow
   - bit 1: underflow
   - bit 0: inexact

   This kernel always sets bits 4:1 to zero.

   Therefore:
   - if `magnitude = 0`, `out_exceptionFlags = 5'b00000`
   - otherwise, `out_exceptionFlags = 5'b0000x`, where `x = inexact`

Edge cases:
- Unsigned zero converts to recoded positive zero.
- Signed zero converts to recoded positive zero.
- Signed negative inputs set the sign bit and round using the negative-number rules for directed rounding.
- The signed input `0x80000000` with `in_signedIn = 1` converts exactly to `-2^31`, which is a finite normal value with `inexact = 0`.
- No input produces NaN, infinity, overflow, or underflow.

Worked examples:
- `in_signedIn = 0`, `in_in = 0x00000001`, any rounding mode: `magnitude = 1`, `p = 0`, `sig = 0x800000`, `exp_ieee = 127`, `rec_exp = 256`, `frac = 0`, so `out_out = 0x80000000`, `out_exceptionFlags = 0x00`.
- `in_signedIn = 1`, `in_in = 0xFFFFFFFF`, any rounding mode: `int_value = -1`, `sign = 1`, `magnitude = 1`, so `out_out = 0x180000000`, `out_exceptionFlags = 0x00`.
- `in_signedIn = 0`, `in_in = 0x01000001`, round_minMag: `magnitude = 16777217`, `p = 24`, `shift = 1`, `trunc = 0x800000`, `remainder = 1`, `increment = 0`, `sig = 0x800000`, `inexact = 1`.
- `in_signedIn = 0`, `in_in = 0x01000001`, round_max: same input as above, `sign = 0`, `increment = 1`, `sig = 0x800001`, `inexact = 1`.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- no architectural state is retained
- `in_ready = 1`
- `out_valid = 0`
- `out_out = 33'b0`
- `out_exceptionFlags = 5'b00000`

The block has no internal architectural state beyond the handshake state needed to hold one pending 1:1 output transaction.

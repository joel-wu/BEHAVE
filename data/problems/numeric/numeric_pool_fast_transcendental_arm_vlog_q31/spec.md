# Q31NaturalLog

Q31NaturalLog computes the natural logarithm of one unsigned Q1.31 input sample per accepted transaction and returns the result in signed Q5.26 format. The computation uses a fixed 31-iteration Clay Turner binary-logarithm algorithm: the input is normalized into the range `[1.0, 2.0)` in Q30, a fractional `log2` value is accumulated bit by bit by repeated squaring, and the final value is converted from `log2` to natural log by multiplication with a fixed Q31 reciprocal constant.

## Interface

### RTL interface

```systemverilog
module q31_natural_log (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_src,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_dst
);
```

### Behavior interface

```python
class Q31NaturalLog:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_src):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_dst`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q31_natural_log`: top-level module implementing one scalar logarithm transaction with the 31-step Clay Turner algorithm and one output sample per accepted input sample.

## Functional Behavior

`in_src` is interpreted as an unsigned Q1.31 bit pattern. All 32-bit inputs
have defined results: nonzero inputs represent values in `(0, 2)`, including
the original positive signed-Q31 range `(0, 1)`. A set high bit therefore
represents a value at least one, not a negative logarithm argument. Zero uses
the finite sentinel defined below, not mathematical negative infinity.

`out_dst` is a 32-bit two's-complement signed integer in Q5.26 format representing `ln(x)`.

The computation for one accepted input transaction is:

1. **Leading-zero count and normalization**
   - Let `c` be the 32-bit leading-zero count of `in_src`, with `c` in `[0, 32]`.
   - Let `x` be a 32-bit unsigned working value initialized from `in_src`.
   - If `(c - 1) < 0`, set `x = in_src >> (1 - c)`.
   - Otherwise set `x = (in_src << (c - 1)) mod 2^32`.
   - Set `normalization = c`.
   - For every nonzero valid input, this normalization places `x` in Q30 format with `0x40000000 <= x < 0x80000000`, representing a value in `[1.0, 2.0)`.

2. **Fractional binary logarithm**
   - Define constants:
     - `LOG_Q31_ACCURACY = 31`
     - `LOG_Q31_INTEGER_PART = 5`
     - `LOQ_Q31_THRESHOLD = 0x80000000`
     - Initial `inc = 0x02000000`
   - `y` is a 32-bit unsigned Q0.26 accumulator initialized to `0`.
   - Repeat exactly 31 iterations for `i = 0..30`:
     - Compute `x = floor((x * x) / 2^30)`.
       Equivalently, `x = ((uint64_t)x * x) >> 30`.
     - If `x >= 0x80000000`:
       - `y = y + inc`
       - `x = x >> 1`
     - `inc = inc >> 1`
   - After the loop, `y` is the fractional part of `log2` in unsigned Q0.26 format.

3. **Apply normalization and convert from `log2` to natural log**
   - Compute a signed intermediate
     - `tmp = signed(y) - (normalization << 26)`
   - Multiply by the fixed Q31 constant
     - `LOG_Q31_INVLOG2EXP = 0x58B90BFB`
   - Compute
     - `result = floor((tmp * LOG_Q31_INVLOG2EXP) / 2^31)`
       with a signed 64-bit product and arithmetic right shift by 31.
   - `result` is returned on `out_dst` as a 32-bit two's-complement Q5.26 value.

4. **Special case**
   - If `in_src == 0`, define `c = 32`, the normalization step yields `x = 0`, the 31-iteration loop keeps `y = 0`, and the returned value is
     - `tmp = -(32 << 26)`
     - `out_dst = ((tmp * 0x58B90BFB) >> 31) mod 2^32`
   - This is the fixed Q5.26 code `0xA746F405`, approximately `-22.180709764`.

5. **Signedness and truncation rules**
   - `in_src`, `x`, `y`, and `inc` are 32-bit unsigned quantities.
   - `normalization` and `tmp` are signed integers.
   - The `x * x` product is formed in 64 bits and truncated by logical right shift.
   - The `tmp * LOG_Q31_INVLOG2EXP` product is formed in signed 64 bits and truncated by arithmetic right shift.
   - The returned `out_dst` bit pattern is the 32-bit two's-complement representation of `result`.

## Reset Behavior

Reset is active-high and may be implemented synchronously or asynchronously. On reset:
- `in_ready` shall be `1`.
- `out_valid` shall be `0`.
- `out_dst` shall be `0`.
- No architectural state is required beyond any implementation pipeline or control registers; all such state shall be initialized so that no output transaction is pending after reset.

Inputs and outputs transfer on rising clock edges with their respective
valid and ready both asserted. Each input produces one output, in order;
output data and valid remain stable while blocked. Latency is not fixed.

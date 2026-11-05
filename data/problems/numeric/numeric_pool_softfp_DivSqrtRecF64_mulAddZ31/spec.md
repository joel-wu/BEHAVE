# Recoded binary64 division and square root

Compute rounded binary64 division or square root from recFN operands. This is
a numerical, ready/valid derivative of the HardFloat division/square-root
operator. The external shared-multiplier schedule is not exposed: there is no
mulAddResult feedback input or placeholder multiplier-control output.

## Interface

### RTL interface

```systemverilog
module DivSqrtRecF64MulAddZ31 (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_sqrtOp,
    input logic [64:0] in_a, in_b,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [64:0] out_out,
    output logic [4:0] out_exceptionFlags
);
```

Every rising-edge input handshake produces one ordered output record. Output
valid and payload remain stable under backpressure; latency is not fixed.
Payload is zero while output valid is low.

### Behavior interface

```python
class DivSqrtRecF64MulAddZ31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sqrtOp, in_a, in_b, in_roundingMode, in_detectTininess): ...
```

Returns one mapping with `out_out` and `out_exceptionFlags`. All arguments
are unsigned raw words of the declared width.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_exceptionFlags`: 5.
- `out_out`: 65.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`DivSqrtRecF64MulAddZ31` evaluates one complete numerical operation and buffers
its response. Its historical name does not specify an internal MAC schedule.

## Functional Behavior

### Encoding and operations

A recFN word contains sign bit 64, a 12-bit exponent E in bits 63:52 and a
52-bit fraction F. Let T=E>>9. T=0 means signed zero, T=6 infinity and T=7
NaN. In other cases its exact value is
`(-1)^sign * (1 + F/2^52) * 2^(E-2048)`. This formula also defines raw finite
patterns outside the usual binary64 exponent range. A NaN is signaling iff
fraction bit 51 is zero. The fraction of zero and infinity is ignored.

For sqrtOp=0 compute a/b. For sqrtOp=1 compute sqrt(b); operand a is completely
ignored, including any NaN in it. Division sign is sign(a) XOR sign(b).
Square root preserves signed zero and otherwise returns a nonnegative value.

NaN inputs produce positive canonical quiet NaN 0x0e008000000000000; signaling
NaN raises invalid. Division 0/0 or infinity/infinity raises invalid. Square
root of any negative nonzero non-NaN value, including negative infinity, raises
invalid. A finite nonzero numerator divided by zero returns signed infinity
and raises the infinite (division-by-zero) flag. Infinity/zero returns infinity
without that flag. Infinity/finite, finite/infinity and zero/finite otherwise
follow their signed infinity/zero results. NaNs take precedence.

### Exact rounding contract

Modes 0,1,2,3,4,6 are nearest-even, toward zero, toward minus infinity, toward
plus infinity, nearest with ties away, and round-to-odd; 5/7 mean toward zero.
Round-to-odd truncates magnitude and sets its retained least-significant bit
when inexact. For an ordinary exact positive result x, e=floor(log2(x)).
Let u be x rounded to quantum 2^(e-52) without exponent limits. If u>=2^1024,
raise overflow and inexact and return infinity in nearest modes or directed
rounding away from zero; otherwise return maximum finite of the result sign.

Without overflow, round x once to quantum max(2^(e-52),2^-1074), obtaining y.
Inexact means y!=x. Underflow means inexact and tiny: x<2^-1022 before rounding
(detectTininess=0), or u<2^-1022 after rounding (=1). These mathematical rules
apply to the exact irrational square root too, not a host-precision estimate.
`out_exceptionFlags` is invalid,infinite,overflow,underflow,inexact. Special
cases do not additionally raise the last three flags.

Outputs are canonical: zero has E=F=0; infinity has E=3072,F=0; NaN has the
fixed word above. For a nonzero finite magnitude y, p=floor(log2(y)),
E=p+2048 and F=(y/2^p-1)*2^52. Thus 1.0 is 0x08000000000000000 and minimum
subnormal is 0x03ce0000000000000. Subnormals are not flushed.

## Reset Behavior

Synchronous active-high rst cancels the pending response, sets out_valid=0
and both output payloads to zero. Numeric operations have no persistent state.

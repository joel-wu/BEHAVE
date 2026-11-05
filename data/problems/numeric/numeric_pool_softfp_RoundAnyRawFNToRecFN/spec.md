# Raw binary32 rounder

Round an unpacked signed binary value to binary32 precision with gradual
underflow, selectable rounding and IEEE-style exception flags. Output uses
canonical 33-bit recFN encoding. This fixed-format specialization retains
HardFloat raw rounding, with a total mathematical definition for every input
bit pattern rather than unspecified non-normalized raw encodings.

## Interface

### RTL interface

```systemverilog
module RawFloatToRecFNRounder (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_invalidExc, in_infiniteExc, in_isNaN, in_isInf, in_isZero,
    input logic in_sign,
    input logic signed [9:0] in_sExp,
    input logic [26:0] in_sig,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [32:0] out_out,
    output logic [4:0] out_exceptionFlags
);
```

Records transfer on rising edges with valid && ready. Responses remain ordered,
with exactly one response per accepted input. A stalled response holds valid
and both output fields. Physical latency is not prescribed. Payload is zero
when out_valid=0.

### Behavior interface

```python
class RawFloatToRecFnRounder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_invalidExc, in_infiniteExc, in_isNaN, in_isInf,
                in_isZero, in_sign, in_sExp, in_sig, in_roundingMode,
                in_detectTininess): ...
```

Returns one mapping with `out_out` and `out_exceptionFlags`. Integers carry the
declared raw words; in_sExp is interpreted as signed two's complement.

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

`RawFloatToRecFNRounder` is a stateless arithmetic rounder with a response buffer.

## Functional Behavior

### Raw value and classifications

Binary32 precision is **24 bits including its implicit leading bit**. The raw
input has 27 significand bits (an extra leading bit plus two rounding bits).
When no classification or exception override applies, its exact magnitude is
`x = in_sig * 2^(in_sExp - 281)`. Its sign is in_sign. In the usual normalized
raw form the leading bit is bit 25 or 26; other significands use the same exact
formula, including sig=0. This definition does not interpret bit 0 as an
unobserved tail; a jammed sticky-bit value gives the usual normalized rounding
decision when the discarded tail is represented by that bit.

Selection precedence is: invalidExc or isNaN -> positive quiet NaN;
otherwise infiniteExc or isInf -> signed infinity; otherwise isZero or sig=0
-> signed zero; otherwise round the finite exact value. Classification flags
may coexist. The two high exception bits always equal invalidExc,infiniteExc,
even when both are asserted. No additional signaling-NaN inference is made.

### Rounding and finite exceptions

Modes 0/1/2/3/4/6 mean nearest-even, toward zero, toward minus infinity,
toward plus infinity, nearest with ties away, and round-to-odd. Reserved modes
5 and 7 mean toward zero. Round-to-odd truncates the positive magnitude and
sets the retained least-significant bit whenever discarded bits are nonzero.

For finite nonzero x, let e=floor(log2(x)). First round x to the precision
quantum `2^(e-23)` without an exponent limit, obtaining u. If u >= 2^128,
assert overflow and inexact. Return signed infinity for nearest modes or
rounding away from zero in the sign's direction; otherwise return signed
maximum finite (2-2^-23)*2^127.

Otherwise round x once using quantum `max(2^(e-23),2^-149)` to obtain the
binary32 magnitude y. Inexact means y != x. Underflow means inexact and tiny:
when detectTininess=0, tiny means x < 2^-126; when it is 1, tiny means
u < 2^-126. In particular, after-rounding tininess uses the unbounded-exponent
precision-rounded value, not simply whether final y became minimum normal.
Finite flags are zero on exact results. Special/zero cases above do not raise
finite overflow, underflow or inexact. `out_exceptionFlags` is the five-bit
concatenation invalid,infinite,overflow,underflow,inexact.

### Canonical recFN output

`out_out = sign<<32 | exponent<<23 | fraction`, with 9 exponent bits and 23
fraction bits. Signed zero has exponent=fraction=0. Infinity has exponent=384
and fraction=0; quiet NaN is the fixed word 0x0e0400000. For nonzero finite y,
let p=floor(log2(y)); exponent=p+256 and
fraction=(y/2^p - 1)*2^23 (an exact integer). Thus 1.0 is 0x080000000,
minimum normal is 0x041000000 and minimum subnormal is 0x035800000.
These constants also define normalization of subnormal results; no noncanonical
zero, infinity or NaN payload bits are retained.

## Reset Behavior

Synchronous active-high rst cancels any pending response. It sets out_valid=0,
both payload fields to zero and in_ready=1. There is no persistent numeric state.

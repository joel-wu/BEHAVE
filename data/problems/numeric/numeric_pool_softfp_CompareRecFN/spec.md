# Recoded Floating-Point Comparator

This module compares two recoded floating-point operands of the same format and reports the ordered comparison result (`lt`, `eq`, `gt`) plus the IEEE invalid-operation flag. The comparison algorithm operates on the decoded operand classes and fields: NaN, infinity, zero, sign, signed exponent, and significand. NaN operands make the comparison unordered; ordered results are then all zero, and the invalid flag is asserted for any signaling NaN or for any unordered signaling compare request.

## Interface

### RTL interface

```systemverilog
module recoded_fp_compare #(
    parameter int EXP_WIDTH = 8,
    parameter int SIG_WIDTH = 24
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic [EXP_WIDTH+SIG_WIDTH:0]     in_a,
    input  logic [EXP_WIDTH+SIG_WIDTH:0]     in_b,
    input  logic                             in_signaling,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic                             out_lt,
    output logic                             out_eq,
    output logic                             out_gt,
    output logic [4:0]                       out_exceptionFlags
);
```

`in_a` and `in_b` are recoded floating-point bit patterns with total width `EXP_WIDTH + SIG_WIDTH + 1`. `out_exceptionFlags[4]` is the invalid-operation flag, and `out_exceptionFlags[3:0]` are always zero.

### Behavior interface

```python
class RecodedFpCompare:
    def __init__(self, exp_width=8, sig_width=24):
        pass

    def reset(self):
        pass

    def process(self, in_a, in_b, in_signaling):
        pass
```

The return value is one dict with keys:
`out_lt`, `out_eq`, `out_gt`, `out_exceptionFlags`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_eq`, `out_gt`, `out_lt`: 1.
- `out_exceptionFlags`: 5.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor exp_width and sig_width match RTL EXP_WIDTH and SIG_WIDTH.

## Modules

- `recoded_fp_compare`: top-level comparator with one input transaction accepted per comparison and one output transaction produced per accepted input.
- `rawFloatFromRecFN`: combinational recoded-floating-point decoder used to derive operand class, sign, signed exponent, and significand from each input operand.
- `isSigNaNRawFloat`: combinational detector for signaling NaN on a decoded operand.

## Functional Behavior

Each accepted input transaction compares `in_a` and `in_b` as recoded floating-point numbers using format parameters `EXP_WIDTH` and `SIG_WIDTH`.

Let `rawA = rawFloatFromRecFN(EXP_WIDTH, SIG_WIDTH, in_a)` and `rawB = rawFloatFromRecFN(EXP_WIDTH, SIG_WIDTH, in_b)`. Each decoded operand provides these fields:

- `sign`: 1-bit sign.
- `isNaN`: 1 if the operand is any NaN.
- `isInf`: 1 if the operand is infinity.
- `isZero`: 1 if the operand is zero.
- `sExp`: signed exponent value used for magnitude ordering.
- `sig`: significand field used for magnitude ordering.
- `isSigNaNRawFloat(rawX)`: 1 if the decoded operand is a signaling NaN.

The decoder contract is:

- `sign` is the top bit of the recoded input.
- Let `expRec` be the next `EXP_WIDTH + 1` bits below the sign bit.
- Let `fract` be the low `SIG_WIDTH - 1` bits.
- Let `expCode` be the top 3 bits of `expRec`.
- `isZero` is 1 when `expCode == 3'b000`.
- `isInf` is 1 when `expCode == 3'b110`.
- `isNaN` is 1 when `expCode == 3'b111`.
- `isSigNaNRawFloat(rawX)` is 1 when `rawX.isNaN == 1` and the top bit of `fract` is 0.
- `sExp` is `expRec` zero-extended to a signed `EXP_WIDTH + 2`-bit value. Its magnitude-ordering value is nonnegative; the top bit of `expRec` is not a sign bit.
- `sig` is the `SIG_WIDTH`-bit value formed by concatenating `1'b0` above `fract`.

The comparison logic uses the following intermediate predicates:

- `ordered = !rawA.isNaN && !rawB.isNaN`
- `bothInfs = rawA.isInf && rawB.isInf`
- `bothZeros = rawA.isZero && rawB.isZero`
- `eqExps = (rawA.sExp == rawB.sExp)`
- `common_ltMags = (rawA.sExp < rawB.sExp) || (eqExps && (rawA.sig < rawB.sig))`
- `common_eqMags = eqExps && (rawA.sig == rawB.sig)`

For ordered operands, the less-than relation is:

- `ordered_lt = !bothZeros && ((rawA.sign && !rawB.sign) || (!bothInfs && ((rawA.sign && !common_ltMags && !common_eqMags) || (!rawB.sign && common_ltMags))))`

This gives the following required behaviors:
- `-x < +y` for any non-NaN values except when both are zeros.
- `-0` and `+0` compare equal.
- Among finite positive numbers, smaller magnitude is less.
- Among finite negative numbers, larger magnitude is less.
- `-Inf` is less than every ordered non-NaN value except `-Inf`.
- `+Inf` is greater than every ordered non-NaN value except `+Inf`.
- `-Inf` and `+Inf` are not equal.
- `-Inf` equals `-Inf`; `+Inf` equals `+Inf`.

For ordered operands, equality is:

- `ordered_eq = bothZeros || ((rawA.sign == rawB.sign) && (bothInfs || common_eqMags))`

This gives the following required behaviors:
- Any two zeros compare equal regardless of sign.
- Nonzero finite values compare equal only when sign, signed exponent, and significand all match through `common_eqMags`.
- Infinities compare equal only when both operands are infinities and their signs match.
- NaNs never compare equal.

The ordered outputs are:

- `out_lt = ordered && ordered_lt`
- `out_eq = ordered && ordered_eq`
- `out_gt = ordered && !ordered_lt && !ordered_eq`

For any ordered input pair, exactly one of `out_lt`, `out_eq`, `out_gt` is 1.

For unordered input pairs, all three outputs are 0:
- If `rawA.isNaN == 1` or `rawB.isNaN == 1`, then `out_lt = 0`, `out_eq = 0`, and `out_gt = 0`.

The invalid-operation flag is:

- `invalid = isSigNaNRawFloat(rawA) || isSigNaNRawFloat(rawB) || (in_signaling && !ordered)`

The output exception flags are:

- `out_exceptionFlags[4] = invalid`
- `out_exceptionFlags[3:0] = 4'b0000`

Edge-case requirements:
- Quiet NaN compared with any value is unordered; ordered outputs are all 0; invalid is 1 only if `in_signaling` is 1.
- Signaling NaN compared with any value is unordered; ordered outputs are all 0; invalid is 1 regardless of `in_signaling`.
- `+0` and `-0` compare equal.
- Any NaN is not less than, equal to, or greater than any operand.
- No rounding, normalization, or arithmetic datapath is performed; the result depends only on decoded classification bits, signed exponent comparison, significand comparison, operand signs, and `in_signaling`.

This is a 1:1 transaction mapping: every accepted input produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state consists only of the output-holding registers required by the handshake wrapper. On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_lt = 0`
- `out_eq = 0`
- `out_gt = 0`
- `out_exceptionFlags = 5'b00000`

There is no other architectural state.

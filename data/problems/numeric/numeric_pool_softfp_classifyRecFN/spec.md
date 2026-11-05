# Recoded Floating-Point Classifier

This module classifies a 33-bit HardFloat recoded binary32 operand into ten categories. The format is fixed to exponent width 8 and significand width 24; there are no configurable RTL parameters. Each accepted input produces one 10-bit one-hot class vector.

## Interface

### RTL interface

```systemverilog
module recoded_fp32_classifier (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_recfn,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [9:0]  out_class
);
```

### Behavior interface

```python
class RecodedFp32Classifier:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_recfn): ...
```

`process()` returns a dict with key `out_class`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_class`: 10.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `recoded_fp32_classifier`: top-level classifier for one recoded binary32 operand.

## Functional Behavior

The input `in_recfn` is a 33-bit recoded floating-point value with:
- bit `32` as sign `sign`
- bits `31:23` as recoded exponent field `exp_rec`
- bits `22:0` as trailing significand field `fract`

The module classifies the operand according to HardFloat recoded-format conventions for `expWidth = 8` and `sigWidth = 24`.

Define:
- `sign = in_recfn[32]`
- `exp_rec = in_recfn[31:23]` interpreted as an unsigned 9-bit integer
- `fract = in_recfn[22:0]` interpreted as an unsigned 23-bit integer

Define the three most-significant bits of the recoded exponent:
- `exp_top3 = exp_rec[8:6]`

The operand category predicates are:

- `isZero` is true iff `exp_top3 == 3'b000`.
- `isInf` is true iff `exp_top3 == 3'b110`.
- `isNaN` is true iff `exp_top3 == 3'b111`.
- `isFiniteNonzero` is true iff `isZero == 0`, `isInf == 0`, and `isNaN == 0`.

For NaN inputs, define:
- `isSigNaN` is true iff `isNaN == 1` and `fract[22] == 0`.
- A NaN is quiet iff `isNaN == 1` and `isSigNaN == 0`.

For finite nonzero inputs, the operand is subnormal exactly when the unsigned exponent `exp_rec < 130`. The raw exponent is zero-extended, not sign-extended: for example, recoded `+1.0` has `exp_rec = 256` and is normal.

Define `isSubnormal = (exp_rec < 130)` for the predicates below.

The 10 output bits of `out_class[9:0]` are generated exactly as:

- `out_class[9]  = isNaN && !isSigNaN`                       : quiet NaN
- `out_class[8]  = isSigNaN`                                 : signaling NaN
- `out_class[7]  = (!sign) && isInf`                         : positive infinity
- `out_class[6]  = (!sign) && isFiniteNonzero && !isSubnormal` : positive normal
- `out_class[5]  = (!sign) && isFiniteNonzero && isSubnormal`  : positive subnormal
- `out_class[4]  = (!sign) && isZero`                        : positive zero
- `out_class[3]  = sign && isZero`                           : negative zero
- `out_class[2]  = sign && isFiniteNonzero && isSubnormal`   : negative subnormal
- `out_class[1]  = sign && isFiniteNonzero && !isSubnormal`  : negative normal
- `out_class[0]  = sign && isInf`                            : negative infinity

`out_class` is the concatenation of those bits in the listed order, with `out_class[9]` as the most significant bit and `out_class[0]` as the least significant bit.

Exactly one output bit shall be `1` for every legal recoded input. For illegal recoded encodings, the module shall still apply the predicate definitions above directly to the raw bit fields with no extra correction logic.

This kernel is stateless and 1:1: each accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high.

Architectural state: none.

While `rst == 1`, input and output controls are:
- `in_ready = 1`
- `out_valid = 0`

On a rising clock edge with `rst == 1`, discard any pending response and set `out_class = 0`. No transfer occurs during reset. Outside reset, a transfer occurs on a rising edge when its valid and ready are both high. Outputs remain valid and stable under backpressure; results preserve input order. No fixed processing latency is required.

After reset deasserts, the module remains stateless; each accepted input is classified independently.

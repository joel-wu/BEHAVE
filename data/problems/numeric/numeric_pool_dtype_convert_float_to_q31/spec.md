# Float to Q31 Converter

This module converts one IEEE-754 single-precision floating-point input value into one signed Q31 fixed-point output value. The conversion implements the CMSIS-DSP `float32 -> q31_t` mapping for one sample per transaction: the input value is multiplied by 2^31, converted to a signed integer with truncation toward zero, and then saturated into the Q31 range. The block is a 1:1 transaction kernel: each accepted input produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module float_to_q31 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_q31
);
```

`in_x` is the raw 32-bit IEEE-754 binary32 bit pattern.
`out_q31` is the 32-bit two's-complement Q31 result bit pattern.

### Behavior interface

```python
class FloatToQ31:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x):
        pass
```

`process()` returns one dict per accepted input transaction:

```python
{"out_q31": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_q31`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `float_to_q31`: top-level converter implementing one float32-to-Q31 conversion per accepted input.

## Functional Behavior

For each accepted input transaction, decode `in_x` as an IEEE-754 binary32 value `x` with sign bit `s`, exponent field `E`, and fraction field `F`.

The output is defined by the scalar CMSIS-DSP conversion rule

- `q = trunc_toward_zero(x * 2147483648.0)`
- `out_q31 = saturate_q31(q)`

where:

- `2147483648.0 = 2^31`
- `trunc_toward_zero` discards any fractional part toward zero
- `saturate_q31(v)` clamps to the signed 32-bit Q31 range:
  - if `v > 2147483647`, output `0x7FFFFFFF`
  - if `v < -2147483648`, output `0x80000000`
  - otherwise output the 32-bit two's-complement representation of `v`

The conversion is specified bit-exactly by IEEE-754 class:

1. **NaN input**: output `0x00000000`.

2. **Positive infinity**: output `0x7FFFFFFF`.

3. **Negative infinity**: output `0x80000000`.

4. **Zero or subnormal input** (`E == 0`): output `0x00000000`.
   This includes both `+0`, `-0`, and all positive or negative subnormal encodings.

5. **Finite normal input** (`1 <= E <= 254`): compute from the decoded sign, unbiased exponent, and significand:
   - unbiased exponent `e = E - 127`
   - significand integer `m = 2^23 + F`  
     (`m` is the 24-bit integer representing `1.F`)
   - scaled magnitude before sign is
     - `M = m << (e + 8)` when `e + 8 >= 0`
     - `M = m >> -(e + 8)` when `e + 8 < 0`
   - this `M` equals `trunc_toward_zero(|x| * 2^31)` for normal finite inputs

   Then apply sign and saturation:
   - if `s == 0` and `M >= 2147483648`, output `0x7FFFFFFF`
   - if `s == 0` and `M < 2147483648`, output the unsigned 32-bit encoding of `M`
   - if `s == 1` and `M >= 2147483648`, output `0x80000000`
   - if `s == 1` and `M < 2147483648`, output the 32-bit two's-complement encoding of `-M`

This definition gives the following required examples:

- `in_x = 0x3F000000` (`+0.5`) -> `out_q31 = 0x40000000`
- `in_x = 0xBF000000` (`-0.5`) -> `out_q31 = 0xC0000000`
- `in_x = 0x3F800000` (`+1.0`) -> `out_q31 = 0x7FFFFFFF`
- `in_x = 0xBF800000` (`-1.0`) -> `out_q31 = 0x80000000`
- `in_x = 0x7FC00000` (quiet NaN) -> `out_q31 = 0x00000000`
- `in_x = 0x00000001` (smallest positive subnormal) -> `out_q31 = 0x00000000`

Conversion uses truncation toward zero followed by Q31 saturation, with no rounding offset. Every 32-bit input pattern is legal; exceptional inputs follow the explicit rules above.

Each accepted input transaction produces exactly one output transaction. No accepted input is dropped and no input produces multiple outputs.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst == 1` at the rising edge, the architectural state is set to:
- no pending output transaction
- `out_q31 = 0x00000000`

Immediately after reset, the module is able to accept a new input transaction. While no output transaction is pending, `out_valid` is low. `in_ready` is high whenever the module can accept the next input transaction.

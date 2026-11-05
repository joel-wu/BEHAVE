# FloatToQ15

FloatToQ15 converts one IEEE-754 single-precision floating-point sample into one signed Q15 sample using the CMSIS-DSP scalar conversion rule. The input sample is multiplied by 32768, converted to a signed integer by truncation toward zero, and saturated to the signed 16-bit range. This kernel implements the non-rounding conversion path.

## Interface

### RTL interface

```systemverilog
module float_to_q15 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_f32,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_q15
);
```

### Behavior interface

```python
class FloatToQ15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_f32): ...
```

`process()` returns a dict with key `out_q15`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_q15`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `float_to_q15`: top-level module that accepts one FP32 input transaction and produces one saturated Q15 output transaction.

## Functional Behavior

`in_f32` is a 32-bit IEEE-754 single-precision input bit pattern. `out_q15` is a 16-bit two's-complement signed Q15 output bit pattern.

For each accepted input transaction, the module computes one output transaction as follows:

1. Interpret `in_f32` as an FP32 value `x`.
2. Compute the FP32 product `p = fp32_mul(in_f32, 0x47000000)`, where `0x47000000` is the FP32 encoding of 32768.0.
3. Convert `p` to a signed mathematical integer by truncation toward zero.
4. Saturate that integer to the closed interval [-32768, 32767].
5. Encode the saturated result as a 16-bit two's-complement value on `out_q15`.

Equivalent scalar formula:

- `out_q15 = sat_s16(trunc_to_zero(x * 32768.0))`

where `sat_s16(v)` clamps to [-32768, 32767].

The conversion is the non-rounding path. No `+0.5` or `-0.5` adjustment is applied before integer conversion.

Exact corner-case behavior:

- If `x >= 1.0`, the result saturates to `0x7FFF`.
- If `x <= -1.0`, the result is `0x8000`.
- If `x` is `+0.0` or `-0.0`, the result is `0x0000`.
- If `x` is positive and `0.0 < x < 1.0/32768.0`, the result is `0x0000`.
- If `x` is negative and `-1.0/32768.0 < x < 0.0`, the result is `0x0000`.
- If `in_f32` encodes `+Inf`, the result is `0x7FFF`.
- If `in_f32` encodes `-Inf`, the result is `0x8000`.
- If `in_f32` encodes NaN, the result is `0x7FFF`.
- Subnormal FP32 inputs are interpreted as their IEEE-754 values before the multiply; any product whose magnitude is less than 1.0 after scaling truncates to zero.

Every 32-bit input pattern is legal; exceptional inputs follow the explicit rules above. The module is a 1:1 transaction mapping: every accepted input produces exactly one output.

## Reset Behavior

`rst` is active-high and synchronous.

On any cycle with `rst == 1` at the rising edge:

- `out_valid` is cleared to `0`.
- `out_q15` is set to `16'h0000`.

There is no other architectural state.

After reset, the module may accept a new input transaction immediately whenever it is not holding an unaccepted output. `in_ready` is `1` whenever no output transaction is pending, and `0` whenever `out_valid` is `1` and `out_ready` is `0`.

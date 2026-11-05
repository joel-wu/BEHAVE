# ComplexConjugateF32

ComplexConjugateF32 computes the complex conjugate of one FP32 complex sample per accepted input transaction. Each sample is presented as two IEEE-754 single-precision bit patterns, `in_real` and `in_imag`, representing the real and imaginary parts of `a + jb`. The output is `a - jb`, implemented as pass-through of the real part and IEEE-754 sign inversion of the imaginary part.

## Interface

### RTL interface

```systemverilog
module complex_conjugate_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_real,
    input  logic [31:0] in_imag,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);
```

### Behavior interface

```python
class ComplexConjugateF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_real, in_imag):
        pass
```

Each call represents one accepted sample and returns exactly
`{"out_real": real_bits, "out_imag": imag_bits}`. Both arguments and both
fields are unsigned 32-bit integers carrying raw FP32 encodings, not Python
float or complex values. The return is never `None` or a list, including for
NaNs and infinities. No state persists between calls; `reset()` returns
`None` without emitting a sample.

## Modules

- `complex_conjugate_f32`: top-level single-sample FP32 complex conjugate kernel.

## Functional Behavior

Each accepted input transaction contains one complex sample:
- `in_real`: 32-bit IEEE-754 FP32 bit pattern for the real component.
- `in_imag`: 32-bit IEEE-754 FP32 bit pattern for the imaginary component.

Each output transaction contains one complex sample:
- `out_real`: 32-bit IEEE-754 FP32 bit pattern for the conjugated real component.
- `out_imag`: 32-bit IEEE-754 FP32 bit pattern for the conjugated imaginary component.

The output values are computed as follows, in order:

1. `out_real = in_real`.
2. `out_imag` is the IEEE-754 sign-negated form of `in_imag`.

Sign negation is defined on the raw FP32 bit pattern by inverting bit 31 and leaving bits `[30:0]` unchanged:
- `out_imag = in_imag ^ 32'h80000000`.

This is the complete functional contract. No arithmetic other than sign inversion is performed.

Edge-case behavior follows directly from bitwise sign inversion of the FP32 encoding:
- `+0` becomes `-0`; `-0` becomes `+0`.
- Positive finite values become negative finite values with identical exponent and fraction fields; negative finite values become positive finite values with identical exponent and fraction fields.
- `+Inf` becomes `-Inf`; `-Inf` becomes `+Inf`.
- NaNs remain NaNs with the same payload and quiet/signaling encoding; only the sign bit is toggled.

The module is stateless at the transaction level and produces exactly one output transaction for each accepted input transaction.

## Reset Behavior

Reset is synchronous active-high.

Architectural state: none.

Output port values during reset and immediately after reset until the first accepted input are:
- `in_ready = 1`
- `out_valid = 0`
- `out_real = 32'h00000000`
- `out_imag = 32'h00000000`

# Q31ToFloat32

Q31ToFloat32 converts a signed Q1.31 sample to IEEE-754 binary32. Each accepted input produces one output representing the signed input divided by 2^31, rounded to nearest with ties to even. Inputs requiring more than 24 significant binary digits are rounded; the conversion is not generally exact.

## Interface

### RTL interface

```systemverilog
module q31_to_float32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q31,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_fp32
);
```

### Behavior interface

```python
class Q31ToFloat32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_q31): ...
```

`process()` returns a dict with key `out_fp32`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_fp32`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q31_to_float32`: top-level handshake-wrapped Q1.31 to binary32 conversion kernel.

## Functional Behavior

`in_q31` is interpreted as a 32-bit two's-complement signed integer `x` in the range [-2147483648, 2147483647].

The output `out_fp32` is the IEEE-754 binary32 encoding of the real number:

- `x / 2147483648`

Equivalently:

- `out_fp32 = encode_fp32(x * 2^-31)`

where `encode_fp32` rounds the exact real value to nearest binary32, with ties resolved to an even least-significant significand bit. Rounding may carry into the exponent.

The exact result classification is:

- If `x = 0`, output is `0x00000000`.
- Every nonzero result is finite and normalized: its magnitude is at least 2^-31.
- If `x < 0`, output is the sign-negated binary32 encoding of `|x| / 2^31`.
- No NaN or infinity can be produced.
- No negative-zero output is produced for any input.

All inputs are legal. The output range is [-1.0, +1.0]; the largest positive Q31 inputs round to +1.0. No truncation toward zero or saturation below +1.0 is applied.

Representative required mappings:

- `0x80000000` -> `0xBF800000`
- `0x40000000` -> `0x3F000000`
- `0x20000000` -> `0x3E800000`
- `0x00000001` -> `0x30000000`
- `0xFFFFFFFF` -> `0xB0000000`
- `0x7FFFFFFF` -> `0x3F800000` (rounding carry)
- `0x01000001` -> `0x3C000000` (halfway, even lower neighbor)
- `0x01000003` -> `0x3C000002` (halfway, even upper neighbor)

This is a 1:1 transaction mapping: each accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst = 1` at the rising edge, the architectural state is set to:

- `in_ready = 1`
- `out_valid = 0`
- `out_fp32 = 0x00000000`

There is no additional architectural state.

# Q15ToQ31Converter

Q15ToQ31Converter converts one signed 16-bit fixed-point sample in Q15 format into one signed 32-bit fixed-point sample in Q31 format by sign-extending the input to 32 bits and shifting it left by 16 bit positions. The conversion is exact and has no rounding, saturation, or stateful accumulation.

## Interface

### RTL interface

```systemverilog
module q15_to_q31_converter (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sample
);
```

### Behavior interface

```python
class Q15ToQ31Converter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample): ...
```

The `process()` return value is a dict with key `out_sample`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_to_q31_converter`: top-level module implementing one-sample Q15-to-Q31 conversion with 1:1 input/output transaction mapping.

## Functional Behavior

`in_sample` is a 16-bit two's-complement signed integer bit pattern representing a Q15 value.

For each accepted input transaction, the module produces exactly one output transaction with:

1. Interpret `in_sample[15:0]` as a signed 16-bit integer `s`.
2. Sign-extend `s` to 32 bits.
3. Shift the 32-bit signed value left by 16 bits.
4. Present the 32-bit two's-complement result on `out_sample[31:0]`.

The exact equation is:

- `out_sample = ((signed(in_sample)) <<< 16) mod 2^32`

Equivalent numerical mapping:

- `0x0000 -> 0x00000000`
- `0x0001 -> 0x00010000`
- `0x7FFF -> 0x7FFF0000`
- `0x8000 -> 0x80000000`
- `0xFFFF -> 0xFFFF0000`

There is no saturation and no rounding. Every possible 16-bit input has exactly one 32-bit output defined by the sign-extension-and-shift rule above.

The conversion has no persistent arithmetic state. Internal latency and buffering are implementation choices. Accepted inputs produce ordered outputs; a presented output and `out_valid` remain stable while `out_ready` is low, unless reset discards the pending work.

## Reset Behavior

Reset is synchronous active-high using `rst`.

On any cycle with `rst == 1` at the rising edge:

- `out_valid` is cleared to `0`.
- `out_sample` is set to `32'h00000000`.
- No architectural state is retained.

After reset, the module contains no pending output transaction. `in_ready` shall indicate ready to accept a new input transaction whenever no output transaction is pending.

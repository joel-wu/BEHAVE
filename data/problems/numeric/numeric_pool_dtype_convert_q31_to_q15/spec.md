# Q31ToQ15Converter

Q31ToQ15Converter converts one signed 32-bit fixed-point sample in Q31 format into one signed 16-bit fixed-point sample in Q15 format by discarding the least-significant 16 bits and keeping the upper 16 bits. The conversion algorithm is an arithmetic right shift by 16 followed by narrowing to 16 bits; there is no rounding and no saturation.

## Interface

### RTL interface

```systemverilog
module q31_to_q15_converter (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);
```

### Behavior interface

```python
class Q31ToQ15Converter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_sample": ...}`, whose value is a Python integer holding the 16-bit
output port pattern, including for a negative fixed-point result.

## Modules

- `q31_to_q15_converter`: top-level single-sample Q31 to Q15 conversion kernel.

## Functional Behavior

`in_sample` is a 32-bit two's-complement signed integer bit pattern representing a Q31 value.

For each accepted input transaction, the module produces exactly one output transaction.

The output value is computed as follows:

1. Interpret `in_sample` as a signed 32-bit integer `x`.
2. Compute the arithmetic right shift `y = x >> 16`.
3. Encode `y` as a 16-bit two's-complement bit pattern on `out_sample`.

This is equivalent to selecting bits `[31:16]` of `in_sample` and presenting them unchanged on `out_sample`.

There is no rounding. Bits `[15:0]` of `in_sample` are discarded.

There is no saturation. All possible 32-bit input patterns map directly to a 16-bit result by arithmetic right shift and truncation.

Examples:
- `in_sample = 0x7FFFFFFF` produces `out_sample = 0x7FFF`.
- `in_sample = 0x00010000` produces `out_sample = 0x0001`.
- `in_sample = 0x0000FFFF` produces `out_sample = 0x0000`.
- `in_sample = 0xFFFF0000` produces `out_sample = 0xFFFF`.
- `in_sample = 0x80000000` produces `out_sample = 0x8000`.

The block is stateless with respect to data values: each transaction is independent of all previous transactions.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

On any cycle with `rst == 1`:
- `out_valid` shall be `0`.
- `out_sample` shall be `16'h0000`.
- The block shall contain no queued input or output data.

After reset deasserts, the block may accept a new input transaction immediately. Since the block has no architectural state beyond handshake/output holding, reset initializes no additional data state.

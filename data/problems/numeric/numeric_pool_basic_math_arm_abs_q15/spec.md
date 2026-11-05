# Q15 Absolute Value Stream

This module computes the saturating absolute value of one signed Q15 sample per accepted input transaction. Each transaction applies the scalar operation used for Q15 vector absolute value: positive inputs pass through unchanged, negative inputs are negated, and the unique non-representable case `-1.0` in Q15 (`0x8000`) saturates to the maximum positive Q15 value (`0x7FFF`).

## Interface

### RTL interface

```systemverilog
module q15_abs_stream (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);
```

### Behavior interface

```python
class Q15AbsStream:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample):
        pass
```

Call `process` once per accepted request, passing `in_sample` as an integer in
0..65535 encoding the signed Q15 input bits. It returns exactly
`{"out_sample": value}`, where `value` is an integer in 0..32767 encoding the
nonnegative Q15 result, not a Python float. Zero is returned in this dictionary,
not as `None`. No algorithmic history carries between requests.

## Modules

- `q15_abs_stream`: top-level module implementing one-sample Q15 saturating absolute value.

## Functional Behavior

`in_sample` is a 16-bit two's-complement signed integer representing a Q15 value in the range `[-1.0, 1.0 - 2^-15]`.

For each accepted input transaction, the module produces exactly one output transaction with `out_sample` computed from `in_sample` as follows:

1. Interpret `in_sample` as a signed 16-bit value `x`.
2. If `x > 0`, then `out_sample = x`.
3. If `x == 0`, then `out_sample = 0`.
4. If `x < 0` and `x != -32768`, then `out_sample = -x`.
5. If `x == -32768` (bit pattern `16'h8000`), then `out_sample = 32767` (bit pattern `16'h7FFF`).

This is saturating arithmetic. No other saturation cases exist because every other negative Q15 value has a representable positive negation in signed 16-bit range.

The output is returned as a 16-bit unsigned bit pattern on `out_sample`. Therefore:
- `in_sample = 16'h0000` produces `out_sample = 16'h0000`
- `in_sample = 16'h0001` produces `out_sample = 16'h0001`
- `in_sample = 16'hFFFF` produces `out_sample = 16'h0001`
- `in_sample = 16'h8001` produces `out_sample = 16'h7FFF`
- `in_sample = 16'h8000` produces `out_sample = 16'h7FFF`

The module produces one ordered output transaction per accepted input. Internal latency and buffering are implementation choices. An output presented with `out_valid && !out_ready` must remain stable, with valid asserted, until accepted or reset.

## Reset Behavior

Reset is synchronous active-high on `rst`.

On any rising clock edge with `rst == 1`:
- the pending-output flag is cleared to `0`
- the output data register is set to `16'h0000`

Immediately after reset:
- `out_valid = 0`
- `out_sample = 16'h0000`
- `in_ready = 1`

There is no other architectural state.

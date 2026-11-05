# Compression Utility DPCM INT16 Coder

This block encodes a stream of signed 16-bit samples into first-order differential PCM residuals. It implements the order-1 LPC residual formula with one fixed quantized predictor coefficient equal to 1 and predictor shift equal to 0, so each accepted input sample is converted to the difference between the current sample and the immediately previous reconstructed sample. The first sample after reset or after an explicit seed load uses a programmable signed 16-bit history value.

## Interface

### RTL interface

```systemverilog
module compression_utility_dpcm_int16_coder (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_load_history,
    input  logic [15:0]  in_history,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_residual
);
```

### Behavior interface

```python
class CompressionUtilityDpcmInt16Coder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_load_history, in_history, in_sample): ...
```

Each call represents one accepted sample and returns exactly
`{"out_residual": value}`, where `value` is an unsigned 16-bit integer.
The arguments are raw unsigned port encodings: one bit for `in_load_history`
and 16 bits each for `in_history` and `in_sample`. Negative samples and
residuals use two's-complement encodings, not negative Python return values.
The return is never `None` or a list. Use the same instance across samples so
history persists. `reset()` restores zero history, returns `None`, and emits
no residual record.

## Modules

- `compression_utility_dpcm_int16_coder`: top-level DPCM residual encoder.

## Functional Behavior

All data values are signed 16-bit two's-complement integers on the ports.

The block maintains one architectural state element:

- `history_sample`: signed 16-bit two's-complement, the previous reconstructed sample used by the order-1 predictor.

For each accepted input transaction, interpret:
- `load_history = in_load_history[0]`
- `seed = signed(in_history)`
- `sample = signed(in_sample)`

The residual computation uses the order-1 LPC residual form derived from:

- predictor order = 1
- `qlp_coeff[0] = 1`
- `lp_quantization = 0`

Therefore the predictor is:

- `predicted = history_for_this_sample`

and the emitted residual is:

- `residual_wide = sample - predicted`

where `history_for_this_sample` is selected as follows:

- if `load_history == 1`, `history_for_this_sample = seed`
- if `load_history == 0`, `history_for_this_sample = history_sample`

The subtraction is mathematically exact over signed integers, then narrowed to the 16-bit output port by two's-complement wrap modulo `2^16`:

- `out_residual = unsigned(residual_wide mod 2^16)`

No saturation is performed.

After computing the residual, the internal history is updated to the current input sample:

- `history_sample_next = sample`

This update happens for every accepted input transaction, regardless of `load_history`.

Equivalent per-transaction behavior:

1. Select predictor source: seeded history when `in_load_history=1`, otherwise stored history.
2. Compute `sample - predictor`.
3. Emit the 16-bit wrapped result on `out_residual`.
4. Replace stored history with `sample`.

Worked examples:
- After reset, `history_sample = 0`. Input `sample = 100`, `load_history = 0` produces residual `100 - 0 = 100`.
- With stored `history_sample = 100`, input `sample = 97`, `load_history = 0` produces residual `-3`, encoded as `16'hFFFD`.
- With stored `history_sample = 100`, input `load_history = 1`, `history = -20`, `sample = 7` produces residual `27`, then stored history becomes `7`.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `history_sample` is set to signed zero.
- `out_valid` is deasserted.
- `out_residual` is set to `16'h0000`.
- `in_ready` is asserted whenever the block is not holding an unaccepted output; the reset state is ready to accept a new input.

# FLAC Fixed Predictor Residual Generator (Orders 1-4)

This block computes one FLAC fixed-predictor residual sample per accepted input sample using predictor order 1, 2, 3, or 4 selected by a control input. The predictor is the standard FLAC finite-difference form: the current sample is predicted from the previous reconstructed samples with binomial coefficients, and the residual is the current sample minus that prediction. The block is streaming and stateful: it stores the four most recent input samples and updates that history after each accepted transaction.

## Interface

### RTL interface

```systemverilog
module compression_flac_predict_flac_fixed_predictor_order1_4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [2:0]   in_order,
    input  logic [31:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_residual
);
```

### Behavior interface

```python
class CompressionFlacPredictFlacFixedPredictorOrder1_4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_order, in_sample): ...
```

Each call consumes one accepted sample and returns one `dict` with exactly
`out_residual`, a Python integer in 0..4294967295 encoding the signed 32-bit
result. `in_sample` uses the same bit encoding and `in_order` is in 0..7.
A zero residual or unsupported order still returns `{"out_residual": 0}`,
never `None` or an empty list. The four-sample history persists across calls
and advances even for unsupported orders, as specified below. `reset()` clears
that history without producing a response.

## Modules

- `compression_flac_predict_flac_fixed_predictor_order1_4`: top-level streaming residual generator for FLAC fixed predictor orders 1 through 4.

## Functional Behavior

`in_sample` is a signed 32-bit two's-complement PCM sample. `out_residual` is a signed 32-bit two's-complement residual represented on the port as its 32-bit unsigned bit pattern.

The block maintains four architectural history registers holding the most recent accepted input samples:

- `sample_z1`: previous sample, corresponding to `data[i-1]`
- `sample_z2`: sample from two transactions earlier, corresponding to `data[i-2]`
- `sample_z3`: sample from three transactions earlier, corresponding to `data[i-3]`
- `sample_z4`: sample from four transactions earlier, corresponding to `data[i-4]`

On each accepted input transaction, let:

- `x  = in_sample` interpreted as signed 32-bit
- `s1 = sample_z1` interpreted as signed 32-bit
- `s2 = sample_z2` interpreted as signed 32-bit
- `s3 = sample_z3` interpreted as signed 32-bit
- `s4 = sample_z4` interpreted as signed 32-bit

The predictor order is taken from `in_order` as follows:

- `in_order = 3'b001`: order 1
- `in_order = 3'b010`: order 2
- `in_order = 3'b011`: order 3
- `in_order = 3'b100`: order 4

For these four legal values, the residual is computed exactly as:

- order 1: `residual = x - s1`
- order 2: `residual = x - 2*s1 + s2`
- order 3: `residual = x - 3*s1 + 3*s2 - s3`
- order 4: `residual = x - 4*s1 + 6*s2 - 4*s3 + s4`

Arithmetic is signed integer arithmetic with final wrap to 32 bits modulo `2^32`. Equivalently, the mathematically exact integer expression is evaluated and the output is the low 32 bits of that result. When those 32 bits are interpreted as signed two's-complement, they are the residual sample.

For any `in_order` value other than `3'b001`, `3'b010`, `3'b011`, or `3'b100`, the output residual shall be `0x00000000`.

After computing the residual for the accepted transaction, the sample history is updated from the old state using shift-register behavior:

- `sample_z4_next = sample_z3`
- `sample_z3_next = sample_z2`
- `sample_z2_next = sample_z1`
- `sample_z1_next = x`

The residual for a transaction always uses the history values from before this update.

The block is a 1:1 streamer: each accepted input transaction produces exactly one output transaction.

Worked examples with zero-initialized history:

1. If the first accepted input is `in_order=1`, `in_sample=7`, then `s1=s2=s3=s4=0`, so `residual = 7 - 0 = 7`.
2. If the next accepted input is `in_order=2`, `in_sample=10`, then `s1=7`, `s2=0`, so `residual = 10 - 14 + 0 = -4`, and `out_residual = 0xFFFFFFFC`.
3. If `x = 0x80000000` and `s1 = 1` with order 1, the exact integer result is `-2147483648 - 1 = -2147483649`; the low 32 bits are `0x7FFFFFFF`, so `out_residual = 0x7FFFFFFF`.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset assertion, the architectural state is set to:

- `sample_z1 = 0`
- `sample_z2 = 0`
- `sample_z3 = 0`
- `sample_z4 = 0`

After reset and before any accepted input transaction, the output-side architectural contents are:

- `out_residual = 0x00000000` whenever no completed transaction is being presented

The input history contains only zeros until updated by accepted input transactions.

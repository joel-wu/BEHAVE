# Streaming FLAC LPC Residual

This block computes the residual of a sample against a quantized linear prediction from earlier samples. It retains the 32 most recent samples, supports prediction orders 1–32, and emits no residual until enough history has been accepted.

## Interface

### RTL interface

```systemverilog
module compression_flac_predict_flac_residual_compute (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [5:0]  in_order,
    input  logic [5:0]  in_lp_quantization,
    input  logic [31:0] in_qlp_coeff_0,
    input  logic [31:0] in_qlp_coeff_1,
    input  logic [31:0] in_qlp_coeff_2,
    input  logic [31:0] in_qlp_coeff_3,
    input  logic [31:0] in_qlp_coeff_4,
    input  logic [31:0] in_qlp_coeff_5,
    input  logic [31:0] in_qlp_coeff_6,
    input  logic [31:0] in_qlp_coeff_7,
    input  logic [31:0] in_qlp_coeff_8,
    input  logic [31:0] in_qlp_coeff_9,
    input  logic [31:0] in_qlp_coeff_10,
    input  logic [31:0] in_qlp_coeff_11,
    input  logic [31:0] in_qlp_coeff_12,
    input  logic [31:0] in_qlp_coeff_13,
    input  logic [31:0] in_qlp_coeff_14,
    input  logic [31:0] in_qlp_coeff_15,
    input  logic [31:0] in_qlp_coeff_16,
    input  logic [31:0] in_qlp_coeff_17,
    input  logic [31:0] in_qlp_coeff_18,
    input  logic [31:0] in_qlp_coeff_19,
    input  logic [31:0] in_qlp_coeff_20,
    input  logic [31:0] in_qlp_coeff_21,
    input  logic [31:0] in_qlp_coeff_22,
    input  logic [31:0] in_qlp_coeff_23,
    input  logic [31:0] in_qlp_coeff_24,
    input  logic [31:0] in_qlp_coeff_25,
    input  logic [31:0] in_qlp_coeff_26,
    input  logic [31:0] in_qlp_coeff_27,
    input  logic [31:0] in_qlp_coeff_28,
    input  logic [31:0] in_qlp_coeff_29,
    input  logic [31:0] in_qlp_coeff_30,
    input  logic [31:0] in_qlp_coeff_31,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_residual,
    output logic        out_error
);
```

Samples, coefficients, and residuals use signed 32-bit two's-complement representation. Order and quantization shift are unsigned six-bit values.

### Behavior interface

```python
class CompressionFlacPredictFlacResidualCompute:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self, in_sample, in_order, in_lp_quantization,
        in_qlp_coeff_0, in_qlp_coeff_1, in_qlp_coeff_2, in_qlp_coeff_3,
        in_qlp_coeff_4, in_qlp_coeff_5, in_qlp_coeff_6, in_qlp_coeff_7,
        in_qlp_coeff_8, in_qlp_coeff_9, in_qlp_coeff_10, in_qlp_coeff_11,
        in_qlp_coeff_12, in_qlp_coeff_13, in_qlp_coeff_14, in_qlp_coeff_15,
        in_qlp_coeff_16, in_qlp_coeff_17, in_qlp_coeff_18, in_qlp_coeff_19,
        in_qlp_coeff_20, in_qlp_coeff_21, in_qlp_coeff_22, in_qlp_coeff_23,
        in_qlp_coeff_24, in_qlp_coeff_25, in_qlp_coeff_26, in_qlp_coeff_27,
        in_qlp_coeff_28, in_qlp_coeff_29, in_qlp_coeff_30, in_qlp_coeff_31,
    ): ...
```

Each call represents one accepted input. A valid-order warm-up call returns
`None` and still updates history. Otherwise return exactly one dictionary with
keys `out_residual` and `out_error`: the residual is a Python integer in
[0, 2**32 - 1] encoding its signed port word, and the error flag is 0 or 1.
An invalid order returns `{"out_residual": 0, "out_error": 1}` without changing
history, even during warm-up. Do not return a list or omit either field.
Samples and coefficients use unsigned 32-bit encodings; order and shift are
integers in [0, 63]. History persists across calls. `reset()` clears history
and count, restarts warm-up and returns no response.

## Modules

- `compression_flac_predict_flac_residual_compute`: sample history and quantized LPC residual computation.

## Functional Behavior

Inputs transfer on rising edges with `in_valid && in_ready`, outside reset. Output responses transfer with `out_valid && out_ready`. A stalled response retains its valid flag and payload. Input backpressure is allowed; no fixed latency is required.

Let `history[j]` be the `j+1`-th most recently accepted valid-order sample, and let `count` be the number available, saturated at 32. Each input carries its own coefficients, order, and shift.

- If `in_order` is outside 1–32, emit `out_residual=0, out_error=1` and leave history and count unchanged.
- Otherwise, if `count < in_order`, emit no response.
- Otherwise, compute the following, using only samples accepted before this input:

```text
sum = signed32(sum(coeff[j] * history[j] for j = 0 .. in_order-1))
prediction = sum arithmetic-right-shifted by in_lp_quantization
out_residual = (in_sample - prediction) modulo 2^32
out_error = 0
```

`signed32` reduces modulo 2^32 and interprets the result as signed. Shifts of 32 or more produce 0 for a nonnegative sum and -1 for a negative sum. Coefficients at indices greater than or equal to the order are ignored.

After processing a valid-order input, insert its sample at history index 0, discard the oldest sample if necessary, and increment count up to 32. This also happens during warm-up. Invalid-order inputs do not contribute history. Changing coefficients, order, or shift does not clear history.

For example, with order 1, coefficient 0 equal to 1, and shift 0, samples `1, 3, 6` emit no response, then residual 2, then residual 3.

## Reset Behavior

Reset is synchronous active-high and overrides handshakes. Clear all history samples and count, discard any pending response, and set `out_valid=0, out_residual=0, out_error=0`. Warm-up starts again.

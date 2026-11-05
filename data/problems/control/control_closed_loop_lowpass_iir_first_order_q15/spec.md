# Control Closed-Loop Lowpass IIR First Order Q15

The complement uses the widened Q15 integer 32768, not the largest signed16 value. Thus alpha=0 gives exactly the current sample. Alpha remains a signed16 input, including its explicitly defined extrapolation region; the final result saturates.

This module implements a first-order infinite-impulse-response low-pass filter in fixed-point Q15 format. Each accepted input sample is filtered against one stored previous output sample using the recurrence `y[n] = alpha*y[n-1] + (1-alpha)*x[n]`. The coefficient `alpha` is supplied explicitly on every transaction as a Q15 value, so the module does not compute adaptive sampling time or derive `alpha` from time constants internally.

## Interface

### RTL interface

```systemverilog
module control_closed_loop_lowpass_iir_first_order_q15 (
    input  logic              clk,
    input  logic              rst,

    input  logic              in_valid,
    output logic              in_ready,
    input  logic signed [15:0] in_x,
    input  logic signed [15:0] in_alpha,

    output logic              out_valid,
    input  logic              out_ready,
    output logic signed [15:0] out_y
);
```

### Behavior interface

```python
class ControlClosedLoopLowpassIirFirstOrderQ15:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x, in_alpha):
        pass
```

Call `process` once per accepted input. It returns one dictionary
`{"out_y": ...}`, whose value is a Python integer holding the 16-bit output
port pattern, including for negative fixed-point results.

## Modules

- `control_closed_loop_lowpass_iir_first_order_q15`: top-level first-order Q15 low-pass filter with one-sample internal state.

## Functional Behavior

`in_x`, `in_alpha`, and `out_y` are signed 16-bit Q15 values. Their mathematical value is `raw * 2^-15`. Examples: `0x4000` represents `+0.5`, `0x2000` represents `+0.25`, and `0x8000` represents `-1.0`.

The module stores one architectural state variable, `y_prev`, also a signed 16-bit Q15 value.

For each accepted input transaction, the module computes exactly one output transaction.

The filter equation is:
- `one_minus_alpha = 32768 - signed(in_alpha)` (a widened integer, not a signed16 literal)
- `term_prev_q30 = y_prev * in_alpha`
- `term_x_q30 = in_x * one_minus_alpha`
- `sum_q30 = term_prev_q30 + term_x_q30`

`term_prev_q30`, `term_x_q30`, and `sum_q30` are signed integer quantities interpreted as Q30.

The Q30-to-Q15 narrowing uses arithmetic right shift by 15 bits with truncation toward negative infinity, matching a signed hardware arithmetic shift:
- `y_raw = sum_q30 >>> 15`

The result is then saturated to the signed 16-bit range:
- if `y_raw > 32767`, `y_sat = 32767`
- else if `y_raw < -32768`, `y_sat = -32768`
- else `y_sat = y_raw`

The output is `out_y = y_sat`, encoded as a 16-bit two's-complement Q15 value.

After computing the output, the module updates its state:
- `y_prev := y_sat`

This state update happens once per accepted input transaction. The next transaction uses the updated `y_prev`.

No restrictions are imposed on `in_alpha`. The computation uses the exact signed 16-bit input bit pattern. Therefore:
- `in_alpha = 0x0000` gives `one_minus_alpha = 32768`, so the output equals the current input exactly.
- `in_alpha = 0x7FFF` represents `1 - 2^-15`, not exactly one; the current input still has weight `2^-15`.
- `in_alpha = 0x8000` represents minus one and gives `one_minus_alpha = 65536`. Negative coefficients are legal extrapolation cases, with wrap-free subtraction before final saturation.

Worked example:
- If `y_prev = 0x2000` (`+0.25`), `in_x = 0x6000` (`+0.75`), and `in_alpha = 0x4000` (`+0.5`),
- `one_minus_alpha = 16384`
- `term_prev_q30 = 8192 * 16384 = 134217728`
- `term_x_q30 = 24576 * 16384 = 402653184`
- `sum_q30 = 536870912`
- `y_raw = 536870912 >>> 15 = 16384`
- `out_y = 0x4000`, exactly `+0.5`
- `y_prev` becomes `0x4000`

## Reset Behavior

Reset is synchronous active-high. On any rising clock edge where `rst == 1`, the architectural state is set to:
- `y_prev = 16'sh0000`

After reset, before any input transaction is accepted, the stored previous output sample is zero.

All output and handshake signals follow the framework handshake contract. There is no additional architectural state beyond `y_prev`.

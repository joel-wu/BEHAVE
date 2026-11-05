# BPSKModem

BPSKModem maps one-bit symbols to the real constellation {+1, -1} and makes hard decisions from signed input samples. Demodulation records the received sample and its re-modulated decision; modulation preserves those two state values. Each accepted request produces one ordered response.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_modem_modem_bpsk (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_op,
    input  logic [0:0]  in_sym,
    input  logic [31:0] in_x_real,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [0:0]  out_sym,
    output logic [31:0] out_y_real,
    output logic [31:0] out_x_hat_real,
    output logic [31:0] out_r_real
);
```

### Behavior interface

```python
class BPSKModem:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_op, in_sym, in_x_real):
        pass
```

`process()` returns a dictionary with keys `out_sym`, `out_y_real`, `out_x_hat_real`, `out_r_real`. Integer results use their unsigned port bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_r_real`, `out_x_hat_real`, `out_y_real`: 32.
- `out_sym`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_modem_modem_bpsk`: the modem and its ready/valid transaction interface.

## Functional Behavior

All 32-bit data ports carry two's-complement integers, not floating-point encodings. The constellation is +1 (`0x00000001`) for symbol 0 and -1 (`0xFFFFFFFF`) for symbol 1. The state comprises the last demodulated sample `r_real` and decision `x_hat_real`.

- `in_op = 0` modulates `in_sym`. Return `out_sym = 0` and the corresponding `out_y_real`; leave both state values unchanged and return them in `out_r_real` and `out_x_hat_real`. Ignore `in_x_real`.
- `in_op = 1` interprets `in_x_real` as signed integer `x`. Choose symbol 0 if `x > 0`, otherwise symbol 1. Store `r_real = x` and `x_hat_real = +1` or `-1` according to that decision. Return the chosen symbol, `out_y_real = x_hat_real`, and both updated state values. Ignore `in_sym`.

Zero therefore demodulates to symbol 1. Every signed 32-bit input is legal. Only hard decisions are provided; there is no soft-bit output.

Outside reset, an input transfers on a rising edge with `in_valid && in_ready`; an output transfers with `out_valid && out_ready`. Responses are one-to-one and in request order. Hold `out_valid` and all output payloads stable while stalled. The sender likewise holds a pending input stable until accepted. Reset cancels pending work; no particular pipeline latency is required.

## Reset Behavior

Reset is asynchronous active-high. Assertion clears both state values, every output payload and `out_valid`, and cancels any pending response. Reset takes priority over transfers. The first request after reset starts with `r_real = x_hat_real = 0`.

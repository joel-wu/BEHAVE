# OOKModem

This block implements binary32 on/off keying (OOK). Symbol 0 maps to +sqrt(2), symbol 1 to zero, and hard demodulation compares the real sample against sqrt(1/2). Each accepted request produces one ordered response.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_modem_modem_ook (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_op,
    input  logic        in_sym,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_sym,
    output logic [31:0] out_y_re,
    output logic [31:0] out_y_im
);
```

### Behavior interface

```python
class OOKModem:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_op, in_sym, in_x_re, in_x_im):
        pass
```

`process()` returns a dictionary with keys `out_sym`, `out_y_re`, `out_y_im`. Integer results use their unsigned port bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sym`: 1.
- `out_y_im`, `out_y_re`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_modem_modem_ook`: the modem and its ready/valid transaction interface.

## Functional Behavior

All 32-bit data ports carry IEEE-754 binary32 bit patterns, not integer amplitudes. The constants are `SQRT2 = 0x3FB504F3`, `THRESHOLD = 0x3F3504F3`, and positive zero `0x00000000`.

- `in_op = 0`: return `out_sym = in_sym`. For symbol 0 return `out_y_re = SQRT2`; for symbol 1 return positive zero. Ignore both input sample components.
- `in_op = 1`: return symbol 0 if the binary32 value in `in_x_re` is strictly greater than `THRESHOLD`, otherwise symbol 1. Re-modulate this symbol into `out_y_re` using the same mapping. Ignore `in_sym` and `in_x_im` for the decision.

`out_y_im` is always positive zero. Equality with the threshold selects symbol 1. Ordered comparison makes every NaN select symbol 1; positive infinity selects 0, negative infinity and both signed zeros select 1. Subnormal inputs are compared by their binary32 values. Every input bit pattern is legal.

Demodulation records the raw received components `r_re, r_im` and the re-modulated components `x_hat_re, x_hat_im`. Modulation leaves them unchanged. These diagnostic state values are not exposed by the output ports and do not affect future outputs.

Outside reset, an input transfers on a rising edge with `in_valid && in_ready`; an output transfers with `out_valid && out_ready`. Responses are one-to-one and in request order. Hold `out_valid` and all output payloads stable while stalled. The sender likewise holds a pending input stable until accepted. Reset cancels pending work; no particular pipeline latency is required.

## Reset Behavior

Reset is synchronous active-high. At a rising edge with `rst = 1`, clear every output payload, all four diagnostic state values and `out_valid`, and cancel any pending response. Reset takes priority over transfers. After that edge, `in_ready = 1`.

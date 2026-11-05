# SignalProcessingCommModemModemAsk

This block performs fixed eight-level amplitude-shift keying (8-ASK) with Gray-coded labels. It maps a label to an amplitude or chooses the nearest amplitude to a signed sample, returning one ordered response per accepted request.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_modem_modem_ask (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_demod,
    input  logic [2:0]  in_symbol,
    input  logic signed [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_symbol,
    output logic signed [15:0] out_y,
    output logic signed [15:0] out_x_hat,
    output logic signed [15:0] out_r
);
```

### Behavior interface

```python
class SignalProcessingCommModemModemAsk:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_demod, in_symbol, in_x):
        pass
```

`process()` returns a dictionary with keys `out_symbol`, `out_y`, `out_x_hat`, `out_r`. Integer results use their unsigned port bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_r`, `out_x_hat`, `out_y`: 16.
- `out_symbol`: 3.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_modem_modem_ask`: the modem and its ready/valid transaction interface.

## Functional Behavior

The three-bit symbols are Gray-coded labels. All 16-bit data ports carry two's-complement integers. Natural indices `s = 0,...,7` map to `a(s) = 2*s - 7`: amplitudes -7, -5, -3, -1, +1, +3, +5, +7. The amplitude scale is one integer unit and adjacent points are two units apart.

Gray encoding is `g = s ^ (s >> 1)`. Gray decoding uses `s[2] = g[2]`, `s[1] = s[2] ^ g[1]`, and `s[0] = s[1] ^ g[0]`.

- `in_demod = 0`: decode `in_symbol` to `s`; return `out_symbol = in_symbol`, `out_y = out_x_hat = a(s)`, and `out_r = 0`. Ignore `in_x`.
- `in_demod = 1`: choose the natural index `s` minimizing `|in_x - a(s)|`, using mathematical integer distances without 16-bit overflow. Break ties toward the smaller index. Return `out_symbol = s ^ (s >> 1)`, `out_y = out_x_hat = a(s)`, and `out_r = in_x`. Ignore `in_symbol`.

For example, modulation labels 000, 111 and 100 produce -7, +3 and +7 respectively. Demodulation samples -6, 0 and +6 select -7, -1 and +5 respectively. Samples outside [-7,+7] select the nearest endpoint. Every signed 16-bit sample is legal. The output fields describe the current operation; no modem state is retained between requests.

Outside reset, an input transfers on a rising edge with `in_valid && in_ready`; an output transfers with `out_valid && out_ready`. Responses are one-to-one and in request order. Hold `out_valid` and all output payloads stable while stalled. The sender likewise holds a pending input stable until accepted. Reset cancels pending work; no particular pipeline latency is required.

## Reset Behavior

Reset is asynchronous active-high. Assertion clears every output payload and `out_valid`, cancels any pending response and takes priority over transfers. There is no other architectural state.

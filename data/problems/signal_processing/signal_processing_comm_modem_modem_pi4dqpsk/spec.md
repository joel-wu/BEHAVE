# Pi4DQPSKModem

This block performs π/4 differential QPSK modulation and hard demodulation with a shared phase state. Each accepted request either advances that phase by a symbol-dependent increment and emits a unit complex sample, or compares an incoming sample's phase with the stored phase and records the incoming phase. Reception does not snap phases to the axes or to eight constellation points.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_modem_modem_pi4dqpsk (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [1:0]  in_sym,
    input  logic [31:0] in_i,
    input  logic [31:0] in_q,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_sym,
    output logic [31:0] out_i,
    output logic [31:0] out_q
);
```

All complex components carry signed 32-bit two's-complement Q30 values: `0x40000000` is +1. Every input bit pattern is legal. `in_mode=0` modulates `in_sym`; `in_mode=1` demodulates `in_i + j*in_q`.

### Behavior interface

```python
class signal_processing_comm_modem_modem_pi4dqpsk:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_mode, in_sym, in_i, in_q): ...
```

The result dictionary has keys `out_sym`, `out_i`, and `out_q`; signed results are returned as unsigned port bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_i`, `out_q`: 32.
- `out_sym`: 2.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_modem_modem_pi4dqpsk`: the shared-state modem and ordered ready/valid interface.

## Functional Behavior

Let `theta` denote the stored phase, initially zero. Define `wrap(a)` by repeatedly subtracting 2π when `a > π` or adding 2π when `a < -π`. Thus both endpoints of [-π,+π] are retained. Define `arg(x+j*y)` in (-π,+π], with zero input assigned phase zero.

### Modulation

Symbols 0, 1, 2, 3 select increments +π/4, +3π/4, -π/4, -3π/4 respectively. Update `theta = wrap(theta + increment)`. Return `out_sym=0`, with `out_i` and `out_q` equal to the signed Q30 encodings of cos(theta) and sin(theta), rounded to the nearest integer with ties away from zero. Ignore the input sample ports.

Starting from reset, the eight possible directions use components 0, ±1073741824 and ±759250125. Following demodulation, modulation preserves the received direction as its phase origin; its outputs need not lie at those eight absolute phases.

### Demodulation

Let `phi = arg(in_i + j*in_q)` and `d = wrap(phi - theta)`. Decide:

- `d > π/2`: symbol 1;
- otherwise `d > 0`: symbol 0;
- otherwise `d < -π/2`: symbol 3;
- otherwise: symbol 2.

Return this symbol and `out_i=out_q=0`, then set `theta=phi`. Ignore `in_sym`. The state follows the received phase, not the ideal phase of the decided symbol. Exact antipodal differences retain the sign determined by the closed-interval wrapping rule.

For example, from reset, receiving (1,1) decides symbol 0 and stores π/4; receiving (2,1) next decides symbol 2. Nonzero amplitude scaling does not affect decisions when it is positive and does not overflow the input representation.

### Exact numeric interpretation

The phase operations above are mathematical, not binary32 angle arithmetic. They do not require an angle approximation: retain the last nonzero received vector (or (1,0) at reset/zero input) and a modulo-eight rotation count. Rotations by odd multiples of π/4 may omit their common positive scale. Dot and cross products then determine the exact differential quadrant. Preserve whether the stored negative-axis endpoint is -π or +π for the antipodal tie.

For a rotated direction (a,b), each modulation component is the nearest integer to `2^30*a/sqrt(a*a+b*b)` or its b counterpart. Use exact intermediate integers; only these final output components are rounded. The state is not reconstructed from rounded outputs, so consecutive modulation does not accumulate rounding drift. This interpretation defines results for arbitrarily small, large and non-axis input vectors.

### Transactions

Outside reset, transfers occur on rising edges where the corresponding `valid && ready` is true. Every accepted request produces one ordered response. The sender holds a pending input stable; the block holds `out_valid` and its payload stable while stalled. No particular pipeline latency is required.

## Reset Behavior

Reset is synchronous active-high. At a rising edge with `rst=1`, set the shared phase to zero, clear all output payloads and `out_valid`, and cancel pending work. Reset takes priority over transfers.

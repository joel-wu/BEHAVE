# FSKModulator

This block performs continuous-phase M-ary frequency-shift keying with a fixed bandwidth parameter of 0.25 cycles per sample. Each accepted symbol selects a frequency between -0.25 and +0.25 and produces K ordered complex samples. The oscillator phase persists across symbols and advances once for each emitted sample.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_modem_fskmod #(
    parameter int M_BITS = 1,
    parameter int K = 2,
    parameter int PHASE_W = 32,
    parameter int OUT_W = 16
) (
    input  logic                    clk,
    input  logic                    rst,

    input  logic                    in_valid,
    output logic                    in_ready,
    input  logic [M_BITS-1:0]       in_symbol,

    output logic                    out_valid,
    input  logic                    out_ready,
    output logic [OUT_W-1:0]        out_i,
    output logic [OUT_W-1:0]        out_q
);
```

Supported configurations are `1 <= M_BITS <= 4`, `2^M_BITS <= K <= 64`, `8 <= PHASE_W <= 32`, and `8 <= OUT_W <= 16`. Outputs are signed Q1.(OUT_W-1) two's-complement bit patterns. Defaults are binary FSK, two samples per symbol, a 32-bit phase accumulator and 16-bit outputs.

### Behavior interface

```python
class FSKModulator:
    def __init__(self, M_BITS=1, K=2, PHASE_W=32, OUT_W=16): ...
    def reset(self): ...
    def process(self, in_symbol): ...
```

`process()` returns K dictionaries, each with keys `out_i` and `out_q`. Each dictionary represents one output transaction; signed values use their unsigned port encodings.

Return a list of exactly K dictionaries in sample order. Never return None or a bare
dictionary. Phase state persists between symbols and is reinitialized by reset.
Every returned dictionary contains exactly these fields (bit widths):

- `out_i`, `out_q`: OUT_W.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

## Modules

- `signal_processing_comm_modem_fskmod`: symbol-to-frequency mapping, persistent phase accumulator and sample stream.

## Functional Behavior

### Symbol frequency and phase

Let `M = 2^M_BITS`, `s = in_symbol`, and `W = PHASE_W`. Symbols 0 through M-1 select evenly spaced frequencies

`f(s) = (2*s - (M-1)) / (4*(M-1))` cycles per sample.

The signed phase increment is `round_away(f(s)*2^W)`, where `round_away` rounds to the nearest integer, with halfway cases away from zero. Store it modulo `2^W`. Compute the quotient before modular reduction, without intermediate overflow.

For default binary FSK, symbol 0 uses increment `0xC0000000` (-1/4 turn) and symbol 1 uses `0x40000000` (+1/4 turn). They are distinct tones. The phase accumulator starts at zero, is not cleared or advanced by symbol acceptance, and advances by the current symbol's increment after each output sample is consumed.

### Sample conversion

For accumulator value `p`, use the top four bits as index `i = floor(16*p/2^W)`. This is a sixteen-direction NCO lookup, with truncation of phase within a lookup bin.

The following signed Q1.31 cosine coefficients C define the exact numeric conversion:

```
[2147483648, 1984016189, 1518500250, 821806413,
 0, -821806413, -1518500250, -1984016189,
 -2147483648, -1984016189, -1518500250, -821806413,
 0, 821806413, 1518500250, 1984016189]
```

For output width B, define `Q(v) = clamp(round_away(v/2^(32-B)), -2^(B-1), 2^(B-1)-1)`. Emit `out_i = Q(C[i])` and `out_q = Q(C[(i-4) mod 16])`. Rounding occurs before saturation.

At the default width, from reset, symbol 0 produces (32767,0), (0,-32768); symbol 1 produces (32767,0), (0,32767). The next symbol starts at the phase left by the previous symbol, not at zero.

### Transactions

An input transfers on a rising edge with `in_valid && in_ready`; each output sample transfers with `out_valid && out_ready`. One input produces exactly K samples in order. Preserve phase and output payload while stalled. The sender holds a pending input stable until accepted.

The reference may accept a new symbol when the previous symbol's last output transfers. That edge advances the phase using the old increment, then starts the new symbol with its new increment. There is no requirement to use a particular internal pipeline.

## Reset Behavior

Reset is synchronous active-high and takes priority over all transfers. At a rising edge with reset asserted, clear oscillator phase, pending symbol/sample progress and `out_valid`; cancel any pending samples. While idle the output payload is zero. The first sample after reset uses phase zero.

# Streaming mel filterbank application

Apply a precomputed triangular mel filterbank to a spectrum frame. Each frame
contains 16 frequency-bin samples and produces four mel-band outputs. The
default bank is an HTK mel bank for 16 uniformly spaced frequencies from 0 to
8000 Hz (16 kHz sample rate). Coefficients can also be replaced at runtime;
coefficient generation, waveform framing, FFT and logarithms are outside this
matrix-application operator.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_mel_mel_filterbank_apply (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_spectrum,
    input  logic        cfg_valid,
    input  logic        cfg_we,
    input  logic [5:0]  cfg_addr,
    input  logic [31:0] cfg_wdata,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_mel
);
```

### Behavior interface

```python
class MelFilterbankApply:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_spectrum, cfg_valid, cfg_we, cfg_addr, cfg_wdata): ...
```

One call represents one accepted frequency-bin input, not one clock cycle.
Pass `in_spectrum` and `cfg_wdata` as unsigned 32-bit encodings, `cfg_addr` as
an unsigned 6-bit integer, and `cfg_valid`/`cfg_we` as 0 or 1. Calls 0 through
14 of a frame return `None`; call 15 returns exactly four dictionaries
`[{"out_mel": y0}, {"out_mel": y1}, {"out_mel": y2}, {"out_mel": y3}]` in band
order. Each value is an unsigned 64-bit encoding, including for a negative
sum; an all-zero frame still returns four records. Reuse the instance to
retain partial sums and coefficient writes. Construction and `reset()` restore
the specified initial state; `reset()` returns `None`, not an output record.

## Modules

`ai_ml_audio_mel_mel_filterbank_apply` is the top level. Its dimensions are
fixed at 16 frequency bins and four mel bands; it has no external memory.

## Functional Behavior

Input payload and configuration are sampled together on a rising edge with
`in_valid && in_ready`. `cfg_*` are fields of that transaction, not an
independent write port. They have no effect on idle cycles or while the input
is stalled. If both `cfg_valid` and `cfg_we` are one, first write `cfg_wdata`
to coefficient address `cfg_addr`. Addresses are row-major: `4*f + m`. The new
coefficient is visible to the multiply for this same accepted input.

The spectrum word and coefficient words are signed two's-complement Q16.16.
The four output words are signed Q32.32 encoded in 64 bits. Nonnegative power
spectra are the normal application; signed samples and signed replacement
coefficients follow the same specified arithmetic.

A reset starts at frequency index 0 with four zero sums. For accepted input
index `f`, sign-extend `in_spectrum` to `x`, and for each `m=0,...,3` add
`x * signed(fb[4*f+m])` to sum `m`. These are exact integer products and sums;
there is no fractional rescaling or intermediate saturation. After bin 15,
emit the low 64 bits of each sum, in band order, and start the next frame with
zero sums and frequency index 0. A prefix shorter than 16 samples produces no
output and retains its partial sums. Sixteen accepted inputs always produce
exactly four output transactions, not sixteen.

The following table defines the reset coefficient words exactly:

| Frequency index | Band 0 | Band 1 | Band 2 | Band 3 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0 | 0 | 0 | 0 |
| 1 | 59097 | 6439 | 0 | 0 |
| 2 | 13068 | 52468 | 0 | 0 |
| 3 | 0 | 45623 | 19913 | 0 |
| 4 | 0 | 17816 | 47720 | 0 |
| 5 | 0 | 0 | 59501 | 6035 |
| 6 | 0 | 0 | 42702 | 22834 |
| 7 | 0 | 0 | 25904 | 39632 |
| 8 | 0 | 0 | 9105 | 56431 |
| 9 | 0 | 0 | 0 | 60889 |
| 10 | 0 | 0 | 0 | 50740 |
| 11 | 0 | 0 | 0 | 40592 |
| 12 | 0 | 0 | 0 | 30444 |
| 13 | 0 | 0 | 0 | 20296 |
| 14 | 0 | 0 | 0 | 10148 |
| 15 | 0 | 0 | 0 | 0 |

These are Q16.16 triangular weights with no area normalization. For context,
their unquantized band boundaries are
`b[j] = 700 * ((1 + 8000/700)^(j/5) - 1)`, `j=0,...,5`; at frequency
`u = 8000*f/15`, band `m` has weight
`max(0, min((u-b[m])/(b[m+1]-b[m]), (b[m+2]-u)/(b[m+2]-b[m+1])))`.
Multiplication by 65536 and rounding to the nearest integer gives the table.
The table is authoritative; implementations need not evaluate logarithms or
powers to initialize it. Runtime writes persist across frames until overwritten
or reset; they do not automatically restore the default bank.

Output transfers occur on rising edges with `out_valid && out_ready`.
Preserve output order and hold `out_valid` and `out_mel` during a stall.
Backpressure and serial implementations are permitted; no fixed latency or
initiation interval is required.

## Reset Behavior

`rst` is synchronous and active high. Each reset edge discards any partial
frame and pending outputs, clears all sums and indices, deasserts `out_valid`,
sets `out_mel` to zero, and reloads all 64 coefficients from the table.
No input is accepted while reset is asserted. After release the next accepted
sample is frequency bin 0 of a new frame.

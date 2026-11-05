# Q15 FIR Decimator

An eight-tap Q1.15 moving-average FIR followed by factor-two decimation, preserving source sample phase zero. Every pair of accepted inputs produces one ordered output.

## Interface

### RTL interface

```systemverilog
module q15_fir_decimator (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);
```

All transfers occur on rising `clk` edges with valid and ready both high. Samples are 16-bit two's-complement Q1.15 values. Input backpressure is permitted; a stalled output retains its value and valid flag.

### Behavior interface

```python
class Q15FirDecimator:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample): ...
```

`process()` returns `None` for the first sample of a pair and `{"out_sample": bits}` for the second.

Return None for the first accepted sample of each pair, then one dictionary for the
second. Never return a list. Reset clears filter history and discards a partial pair.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_fir_decimator`: fixed eight-tap filter, decimation phase and output transport.

## Functional Behavior

Number accepted samples `x[0], x[1], ...`, with `x[n]=0` for negative indices. All eight coefficients are `4096` (Q1.15 value 1/8). Output number `j` is:

```text
y[j] = clamp(floor(sum(4096 * x[2*j-i] for i=0..7) / 32768), -32768, 32767)
```

Emit `y[j]` only after both `x[2*j]` and `x[2*j+1]` have been accepted. The odd-indexed sample updates history but does not shift the retained phase: the first result filters `x[0]`, not `x[1]`. Accumulate products without overflow; shift right arithmetically by 15, with no rounding, then saturate to signed 16 bits. Encode that result in `out_sample`. Every complete input pair produces exactly one output; an incomplete pair produces none. Bubbles do not advance the sample index. Latency and pipelining are implementation choices, subject to ordered output and backpressure.

## Reset Behavior

Active-high synchronous `rst` clears history, phase, saved result and pending output. After a reset edge, `in_ready=1`, `out_valid=0`, and `out_sample=0`. Reset discards a partial pair and any unaccepted output. The next input starts a new pair with zero history.

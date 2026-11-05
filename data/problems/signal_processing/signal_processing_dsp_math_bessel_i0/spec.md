# Fixed-point modified Bessel I0

Evaluate the modified Bessel function of the first kind, order zero, for a
nonnegative scalar. This bounded fixed-point instance retains the numerical
function of `libdspl`'s `bessel_i0`: every input fraction contributes to the
result. It uses a specified finite power series rather than the source's
binary64 piecewise polynomial approximation.

## Interface

### RTL interface

```systemverilog
module signal_processing_dsp_math_bessel_i0 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_y,
    output logic        out_error
);
```

### Behavior interface

```python
class SignalProcessingDspMathBesselI0:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x): ...
```

Each call returns one dictionary containing `out_y` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_y`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`signal_processing_dsp_math_bessel_i0` is the top level. No external memory or
other modules are required.

## Functional Behavior

`in_x` is an unsigned Q8.8 word: its real value is `in_x / 256`.
For words 0 through 4096 inclusive, compute the following nonnegative integers
exactly, without intermediate overflow:

```text
q = in_x * in_x
t[0] = 2^80
t[k] = floor(t[k-1] * q / (2^18 * k^2)),  k = 1,...,32
A = t[0] + t[1] + ... + t[32]
out_y = floor(A / 2^48)
out_error = 0
```

`out_y` is unsigned Q32.32. The recurrence evaluates the terms of
`I0(x) = sum(k=0..infinity) ((x*x/4)^k / (k!)^2)` with 80 fractional
bits internally; the exact integer recurrence above defines the required
output, including its rounding. This is not binary64 bit equivalence or a
lookup table indexed by magnitude. For `in_x = 0`, `out_y = 4294967296`
(exactly 1.0). Values above 4096 are outside the numerical domain and return
`out_y = 0, out_error = 1`; they still produce a response.

Input and output transfers occur on rising clock edges when the corresponding
`valid` and `ready` are both high. Produce exactly one response for every
accepted input, in order. While `out_valid && !out_ready`, retain `out_valid`,
`out_y`, and `out_error`. Latency and initiation interval are implementation
choices; the reference computes one series term per cycle.

## Reset Behavior

`rst` is asynchronous and active high. Reset clears any pending computation and
response, sets `out_valid`, `out_y`, and `out_error` to zero, and suppresses
input acceptance. On release the module is idle and ready for a new request.
There is no retained numerical state between requests.

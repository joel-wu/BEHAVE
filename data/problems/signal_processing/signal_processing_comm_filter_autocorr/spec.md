# ComplexAutocorrelator

This block computes the complex lag-four autocorrelation of the most recent eight accepted samples and their exact total energy. Every accepted complex sample updates the history and produces one ordered result. Missing pre-reset samples are zero.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_filter_autocorr (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [67:0] out_rxx_re,
    output logic [67:0] out_rxx_im,
    output logic [66:0] out_energy
);
```

Input components carry signed 32-bit two's-complement integers. Correlation components carry signed 68-bit results; energy is an unsigned 67-bit result. All input bit patterns are legal.

### Behavior interface

```python
class SignalProcessingCommFilterAutocorr:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x_re, in_x_im): ...
```

`process()` returns a dictionary with `out_rxx_re`, `out_rxx_im`, and `out_energy`. Signed results are encoded as unsigned port bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_energy`: 67.
- `out_rxx_im`, `out_rxx_re`: 68.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_filter_autocorr`: the fixed eight-sample, four-sample-delay correlator and ready/valid transport.

## Functional Behavior

Number accepted samples from n=0 after reset, and let `x[n] = a[n] + j*b[n]`. Define `x[n]=0` for n<0. For the output associated with input n:

`r[n] = sum(x[n-k] * conj(x[n-k-4]), k=0..7)`

`E[n] = sum(a[n-k]^2 + b[n-k]^2, k=0..7)`.

Compute both sums after incorporating the new sample. Return real(r[n]), imaginary(r[n]) and E[n]. Complex multiplication by the conjugate is `(a+j*b)*conj(c+j*d) = (a*c+b*d) + j*(b*c-a*d)`. The lag and window advance on accepted samples, not elapsed clock cycles.

All arithmetic is exact: do not truncate input conjugation, products or accumulated sums. In particular, negating -2^31 internally produces +2^31. A sample contributes at most 2^63 energy, so eight samples can reach 2^66 and require 67 unsigned bits. There is no saturation, modulo energy reduction or narrower operating-range assumption. Each correlation component fits in its signed 68-bit output.

From reset, input (3,4) produces correlation (0,0) and energy 25. After inputs (1,2), (3,4), (5,6), (7,8), (9,10), the fifth result has correlation (29,-8) and energy 385.

Outside reset, transfers occur on rising edges with the corresponding `valid && ready`. Each accepted input has exactly one output in input order. Hold `out_valid` and all payloads stable while stalled; the sender likewise holds a pending input stable. No fixed pipeline latency is required.

## Reset Behavior

Reset is asynchronous active-high. Assertion clears all history and accumulated energy, clears output payloads and `out_valid`, and cancels any pending response. Reset takes priority over transfers. The first accepted input after reset uses zero prior history.

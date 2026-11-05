# Decimator and Signed Peak Detector

Select every `DEC_FACTOR`-th sample from a packed frame and report the maximum
signed selected value. Preserve the native one-clock input-register pipeline.

## Interface

### RTL interface

```systemverilog
module advanced_decimator_with_adaptive_peak_detection #(
    parameter int N = 8,
    parameter int DATA_WIDTH = 16,
    parameter int DEC_FACTOR = 4
) (
    input logic clk,
    input logic reset,
    input logic valid_in,
    input logic [DATA_WIDTH*N-1:0] data_in,
    output logic valid_out,
    output logic [DATA_WIDTH*(N/DEC_FACTOR)-1:0] data_out,
    output logic [DATA_WIDTH-1:0] peak_value
);
```

`N > 1`, `DATA_WIDTH > 1`, `DEC_FACTOR >= 1`, and `N % DEC_FACTOR == 0`.

### Behavior interface

```python
class DecimatorAndPeakDetector:
    def __init__(self, N=8, DATA_WIDTH=16, DEC_FACTOR=4): ...
    def reset(self): ...
    def process(self, valid_in, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: DATA_WIDTH*(N/DEC_FACTOR).
- `peak_value`: DATA_WIDTH.
- `valid_out`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `advanced_decimator_with_adaptive_peak_detection`: input register, combinational
  sample selection and signed maximum, with a matching valid register.

## Functional Behavior

Number input samples from the most-significant end: sample `i` occupies
`data_in[DATA_WIDTH*(N-i)-1 -: DATA_WIDTH]`. Let `M=N/DEC_FACTOR`.
Selected sample `j` is input sample `j*DEC_FACTOR`, for `0 <= j < M`, and
occupies `data_out[DATA_WIDTH*(M-j)-1 -: DATA_WIDTH]`. The first selected sample
is therefore the most-significant sample in both input and output.

`peak_value` is the signed maximum over those selected samples, returned in
`DATA_WIDTH` bits. Negative-only frames must return a negative sample, not zero.
For the default parameters, input `{10,20,30,40,50,60,70,80}` (16-bit lanes)
produces `{10,50}` and peak `50`.

At every rising edge register `data_in` and `valid_in`. The output data and peak
are combinational functions of that registered frame, while `valid_out` equals
the registered valid bit. Thus the native latency is one clock and there is no
backpressure. For completeness, data is captured even when valid is zero;
outputs still reflect that frame, but `valid_out=0` marks it invalid.

## Reset Behavior

`reset` is asynchronous and active high. Assertion clears the input frame and
valid register; all three output pins become zero. Reset clock observations do
not produce logical transactions. The first released edge captures a new frame.

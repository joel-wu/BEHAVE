# BiquadCascadeDf1FastQ15

BiquadCascadeDf1FastQ15 implements a direct-form-I cascade of two second-order IIR sections on signed Q15 samples. Each accepted input transaction is one input sample. The module updates all stages in sample order and emits one output sample for that input. Arithmetic follows the CMSIS-DSP fast Q15 biquad algorithm: each section uses five signed Q15 coefficients, four signed Q15 state values, a 32-bit wrapping accumulator for the sum of five Q15×Q15 products, an arithmetic right shift by `(15 - postShift)`, and final saturation to signed 16-bit output.

## Interface

### RTL interface

```systemverilog
module biquad_cascade_df1_fast_q15 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y
);
```

### Behavior interface

```python
class BiquadCascadeDf1FastQ15:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x):
        pass
```

The returned dict contains key `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `biquad_cascade_df1_fast_q15`: top-level single-sample Q15 biquad cascade with two sections, fixed coefficients, fixed `postShift`, and persistent per-section state.

## Functional Behavior

`in_x` and `out_y` carry signed Q1.15 values as 16-bit two's-complement
patterns. All input patterns are valid. The cascade has two stages with fixed
coefficients, in the order `(b0,b1,b2,a1,a2)`:

- Stage 0: `(8192,4096,2048,-4096,1024)`.
- Stage 1: `(8192,-2048,1024,-3072,512)`.

Each stage retains four signed 16-bit values `(x1,x2,y1,y2)`, initially zero.
Its fixed `postShift=1` gives a right shift of 14. For current sample `x`,
compute from the old state:

```text
acc = wrap32(b0*x + b1*x1 + b2*x2 + a1*y1 + a2*y2)
y = sat16(acc >> 14)
(x1,x2,y1,y2) = (x,old_x1,y,old_y1)
```

`wrap32` keeps the low 32 bits and interprets them as signed two's-complement.
Products are exact signed 16-by-16-bit products; accumulation wraps without
intermediate saturation. The right shift is arithmetic, rounding toward
negative infinity. `sat16(v)=min(32767,max(-32768,v))`; values already in that
range are unchanged.

Stage 0 receives the current input. Stage 1 receives the stage-0 output from
the same transaction, and its output becomes `out_y`. No coefficient reload,
bypass, or variable stage count is exposed. Zero input still advances the
recurrence and may produce nonzero output from stored history.

Each rising edge with `in_valid && in_ready` accepts one sample and produces
one ordered output. Input valid and payload must be held until acceptance;
output valid and payload must be held while `out_ready` is low. Stalls do not
repeat state updates. Latency is not fixed by the contract. Reset discards any
pending output and clears both stages' history.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_y = 16'h0000`

Architectural state is initialized to zero for both stages:

- Stage 0:
  - `x[n-1] = 0`
  - `x[n-2] = 0`
  - `y[n-1] = 0`
  - `y[n-2] = 0`
- Stage 1:
  - `x[n-1] = 0`
  - `x[n-2] = 0`
  - `y[n-1] = 0`
  - `y[n-2] = 0`

The fixed coefficients and `postShift` configuration are constant and unchanged by reset.

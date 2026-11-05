# Trapezoidal Integer Integrator

This block integrates a stream of signed integer sample points using the
trapezoidal rule. It stores the previous point and emits the cumulative area
after every subsequent point. A one-bit fractional representation preserves
half-integer areas exactly; no per-segment rounding is performed.

## Interface

### RTL interface

```systemverilog
module signal_processing_dsp_math_trapezoidal_int (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [31:0] in_x,
    input  logic [31:0] in_y,
    output logic out_valid,
    input  logic out_ready,
    output logic [95:0] out_sum
);
```

- `in_x,in_y` encode signed two's-complement 32-bit coordinates and values.
- `out_sum` is a signed 96-bit two's-complement fixed-point value with **one
  fractional bit**. Its represented area is `signed96(out_sum)/2`.
  Thus raw 1 means +0.5, raw 2 means +1, and raw all-ones means -0.5.
- Inputs transfer at rising edges with `in_valid && in_ready`; outputs
  transfer with `out_valid && out_ready`. Valid payloads remain stable while
  stalled. Latency is not fixed.
- The first accepted point after reset produces no output. Each subsequent
  point produces one ordered output, so N points produce max(0,N-1) outputs.

### Behavior interface

```python
class SignalProcessingDspMathTrapezoidalInt:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_y): ...
```

Input arguments are raw unsigned 32-bit encodings. `process` returns `None`
for the first point after reset, otherwise `{"out_sum": raw_uint96}`.

Return None for the first accepted point after reset, then one dictionary on each
subsequent accepted point. Never return a list. Reset clears the prior point and
accumulated sum.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 96.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_dsp_math_trapezoidal_int`: adjacent-point trapezoidal
  accumulation with exact half-unit resolution.

## Functional Behavior

The architectural state consists of the previous signed coordinates
`prev_x,prev_y`, a first-point flag and the unsigned 96-bit accumulator
`A` holding twice the integral modulo 2^96.

The first accepted point stores its coordinates and sets the flag without
changing A or emitting an output. For every later accepted point `(x,y)`:

```text
dx = x - prev_x
ys = y + prev_y
A = (A + dx * ys) mod 2^96
prev_x = x
prev_y = y
out_sum = A
```

The difference, sum and product are exact integers; in particular, `dx`
and `ys` must not be truncated to 32 bits. Only the final accumulator wraps.
Before wrap, the reported area after point k equals

`sum((x[i]-x[i-1]) * (y[i]+y[i-1]) / 2 for i=1,...,k)`.

Coordinates need not be monotonic. Equal x coordinates add zero; decreasing
x reverses the segment's orientation. Negative and half-integer areas retain
their sign and fraction. For points (0,0), (1,1), (2,0), output raw values
are 1 and 2, representing cumulative areas 0.5 and 1.0.

For more than one integration series, reset between series. There is no
implicit frame boundary. Neither input bubbles nor output stalls change the
accepted point order or the accumulated value. Over arbitrarily long streams,
overflow is defined by modulo-2^96 accumulation, not saturation.

## Reset Behavior

Reset is synchronous active-high. A reset edge clears the previous
coordinates, first-point flag, accumulator, output validity and output
bits; `in_ready=1` in the reset state. Pending outputs are cancelled and
cannot reappear. The next accepted point starts a new series and again
produces no output.

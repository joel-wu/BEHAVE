# Single-element complex integer AXPY

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module complex_axpy_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_alpha_re,
    input  logic [63:0]  in_alpha_im,
    input  logic [63:0]  in_x_re,
    input  logic [63:0]  in_x_im,
    input  logic [63:0]  in_y_re,
    input  logic [63:0]  in_y_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_y_re,
    output logic [63:0]  out_y_im
);
```

### Behavior interface

```python
class ComplexAxpyStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_alpha_re, in_alpha_im, in_x_re, in_x_im, in_y_re, in_y_im): ...
```

Each call represents one accepted complex-element request and returns exactly
`{"out_y_re": real_bits, "out_y_im": imag_bits}`. All six arguments and both
returned values are unsigned 64-bit port encodings of signed integers, not
Python complex numbers or floating-point values. An all-zero result or zero
alpha still returns the complete dictionary, never `None` or a list. No
handshake signals are returned, and latency is not represented by extra calls.
There is no state between requests; construction and `reset()` establish this
empty state, and `reset()` returns `None`.

## Modules

`complex_axpy_step` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

Interpret each real/imaginary input component as a signed 64-bit integer. For complex alpha, x and y return y+alpha*x: real=yr+ar*xr-ai*xi, imag=yi+ar*xi+ai*xr, each reduced modulo 2^64. This is one complex AXPY element, with no memory address or stride interface. It specializes the source BLAS operation to wrapping integers, not IEEE complex double or an unspecified fixed-point scale. alpha=0 returns y.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.

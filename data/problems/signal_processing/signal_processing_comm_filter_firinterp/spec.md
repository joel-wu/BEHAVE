# FIR Interpolator

This block upsamples a signed input stream by M and applies a fixed FIR prototype. Each accepted input generates exactly M ordered phase outputs, including contributions from earlier inputs. The default nonzero prototype has seven coefficients and interpolation factor two.

## Interface

### RTL interface

```systemverilog
module firinterp #(
    parameter int unsigned M = 2,
    parameter int unsigned COEF_WIDTH = 16,
    parameter int unsigned DATA_WIDTH = 16,
    parameter int unsigned COEF_LEN = 7,
    parameter logic signed [COEF_WIDTH*COEF_LEN-1:0] COEF =
        {COEF_WIDTH'(1), COEF_WIDTH'(2), COEF_WIDTH'(3), COEF_WIDTH'(4),
         COEF_WIDTH'(3), COEF_WIDTH'(2), COEF_WIDTH'(1)}
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic signed [DATA_WIDTH-1:0]     in_x,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic signed [DATA_WIDTH-1:0]     out_y
);
```

### Behavior interface

```python
class FIRInterp:
    def __init__(self, m=2, data_width=16, coef_width=16, coef=None): ...
    def reset(self): ...
    def process(self, in_x): ...
```

Constructor m, data_width and coef_width match M, DATA_WIDTH and COEF_WIDTH. The length of coef is COEF_LEN. The default coefficient list is [1,2,3,4,3,2,1].  For another length, specify both COEF_LEN and the complete list. process returns M dictionaries {"out_y": value} in output order, with unsigned port bit patterns.

Return a list of exactly M dictionaries in the specified phase order, including when
M=1. Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: DATA_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Coefficient packing

RTL COEF packs coefficient j in bits [j*COEF_WIDTH +: COEF_WIDTH]; coefficient zero is least significant.

## Modules

- firinterp: polyphase interpolation with history and ordered output serialization.

## Functional Behavior

M >= 2, COEF_LEN >= M, DATA_WIDTH >= 1 and COEF_WIDTH >= 1. Samples and coefficients are signed two's-complement integers; all sample bit patterns are legal. Coefficients are fixed for the instance.

Let L=ceil(COEF_LEN/M). Pad the coefficient list h with trailing zeros to length M*L. Number accepted inputs x[n] from n=0 after reset; x[n]=0 for n<0. For each input, emit phases p=0,...,M-1:

y[M*n+p] = sum(h[p+k*M] * x[n-k], k=0..L-1), modulo 2^DATA_WIDTH.

This is convolution of the prototype with the stream formed by inserting M-1 zeros after every input. Compute signed integer products and sums and retain the low DATA_WIDTH bits of each result. There is no rounding, saturation or implicit gain normalization.

The default impulse followed by three zeros produces the eight outputs [1,2,3,4,3,2,1,0]. For M=3 and coefficients [2,-1,4,3,5], zero padding gives [2,-1,4,3,5,0]. Inputs [7,11] then produce [14,-7,28,43,24,44], encoded at DATA_WIDTH bits. This distinguishes phase order, padding and history contributions.

Outside reset, transfers occur on rising edges with valid && ready. All M outputs of one input precede the next input's outputs. Backpressure must preserve out_valid and the pending payload, and must not skip a phase. The input sender holds a stalled transaction stable. Idle clock cycles do not advance filter history. No fixed latency or internal buffering scheme is required.

## Reset Behavior

Reset is synchronous active-high and takes priority over transfers. It clears input history, all pending output phases, out_valid and out_y. After reset the block is ready for a new input, which is processed against zero history. Static coefficients and interpolation parameters are retained.

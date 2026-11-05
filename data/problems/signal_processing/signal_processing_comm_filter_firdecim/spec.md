# FIR Decimator

This block applies a fixed FIR filter to a signed sample stream and emits one result every M accepted samples, starting with the first after reset. Filtering retains the complete sample history between output-producing transactions.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_filter_firdecim #(
    parameter int DATA_W = 16,
    parameter int COEF_W = 16,
    parameter int SCALE_W = 16,
    parameter int ACC_W = 48,
    parameter int H_LEN = 8,
    parameter int M = 2,
    parameter logic signed [COEF_W*H_LEN-1:0] COEFFS =
        {COEF_W'(0), COEF_W'(1), COEF_W'(2), COEF_W'(3),
         COEF_W'(4), COEF_W'(3), COEF_W'(2), COEF_W'(1)},
    parameter logic signed [SCALE_W-1:0] SCALE = 'd1
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic signed [DATA_W-1:0]     in_x,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic signed [DATA_W-1:0]     out_y
);
```

### Behavior interface

```python
class SignalProcessingCommFilterFirdecim:
    def __init__(self, data_w=16, coef_w=16, scale_w=16, acc_w=48,
                 m=2, coeffs=None, scale=1): ...
    def reset(self): ...
    def process(self, in_x): ...
```

The lowercase constructor widths, m and scale match the corresponding RTL parameters. The length of coeffs is H_LEN. The default list is [1,2,3,4,3,2,1,0]. For a different tap count, specify both H_LEN and the complete coefficient list. A call returns {"out_y": value} on an output-producing input, otherwise None. Returned values are unsigned port bit patterns.

Return one dictionary on input indices 1, 1+M, 1+2*M, ... since reset; return None
otherwise. Never return a list.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: DATA_W.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Coefficient packing

COEFFS is packed with coefficient j in bits [j*COEF_W +: COEF_W], so coefficient zero occupies the least significant slice.

## Modules

- signal_processing_comm_filter_firdecim: FIR decimation and ordered ready/valid transport.

## Functional Behavior

DATA_W, COEF_W and SCALE_W are positive; ACC_W >= DATA_W; H_LEN >= 1 and M >= 1. Inputs, coefficients and scale are signed two's-complement values of their respective widths. All sample bit patterns are legal.

Number accepted samples n=0,1,... after reset, and set x[n]=0 for n<0. Let c[j] be coefficient j, in the declared list order. On inputs n divisible by M, emit

y[n] = SCALE * sum(c[j] * x[n-j], j=0..H_LEN-1), modulo 2^DATA_W.

All other inputs update history but emit no output. Thus c[0] multiplies the newest sample, c[1] the previous sample, and so on. The first output occurs at n=0, not n=M-1. An incomplete final group still contains its phase-zero output; there is no extra flush output.

Compute signed products and sums with two's-complement wrap to ACC_W, then take the low DATA_W bits. This gives the same output as the displayed mathematical sum because ACC_W >= DATA_W. There is no saturation or fractional scaling. Coefficients and SCALE remain fixed across inputs and reset.

The default impulse input [1,0,0,0,0,0,0,0,0] produces [1,3,3,1,0] at indices [0,2,4,6,8]. With M=1, coefficients [2,-3,5], scale=1 and inputs [7,11,13], the first three outputs encode [14,1,28].

Outside reset, input and output transfers occur on rising edges with valid && ready. Output order is preserved. Hold out_valid and out_y stable during backpressure; the sender likewise holds pending input data stable. No fixed latency or internal pipeline is required, and elapsed idle cycles do not advance the decimation phase.

## Reset Behavior

Reset is synchronous active-high. A rising edge with rst=1 clears sample history, restores decimation phase zero, clears out_valid and out_y, and cancels pending responses. Reset takes priority over transfers. After reset the receiver can accept input and the first accepted sample produces an output. Static coefficients and scale are retained.

# SignalProcessingCommLoopEqlms

An eight-tap real normalized-LMS equalizer. Each input supplies a sample and
desired output; the block filters the updated delay line and then trains its
coefficients. This integer-arithmetic configuration uses learning rate 1/2.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_loop_eqlms #(
    parameter int W = 32,
    parameter int H_LEN = 8
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic signed [W-1:0]  in_x,
    input  logic signed [W-1:0]  in_d,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic signed [W-1:0]  out_y,
    output logic                 out_updated
);
```

The benchmark configuration is W=32 and H_LEN=8. Other parameter values are
outside this task. Samples and outputs are signed two's-complement integers.
Input and output transfers occur on rising edges with valid and ready both
high. Each input produces one ordered output. An unaccepted output and its
valid signal remain stable; stalls do not repeat training updates.

### Behavior interface

```python
class SignalProcessingCommLoopEqlms:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_d): ...
```

The result contains `out_y` as a 32-bit pattern and one-bit `out_updated`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_updated`: 1.
- `out_y`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_loop_eqlms`: equalizer and training state.

## Functional Behavior

State consists of eight signed 32-bit weights w, eight signed 32-bit samples
x (newest first), and a fill count capped at eight. Let wrap32 truncate an
integer to signed 32 bits, and trunc denote division rounded toward zero.
All intermediates below are exact integers; only the stated wraps apply.

For each accepted input, in this order:

1. Insert signed `in_x` at x[0], shift the previous x[0:7] to x[1:8], and
   increment the fill count, saturating at eight.
2. Compute E = sum(x[i]^2) and y = wrap32(sum(w[i]*x[i])) using the old weights.
3. Return y and `out_updated = (count == 8)`.
4. If count is eight and E is nonzero, simultaneously update every weight:
   w[i] = wrap32(w[i] + trunc((signed(in_d)-y)*x[i]/(2*E))).
   Otherwise retain all weights. `out_updated` denotes update eligibility,
   so it is one even when E=0 or every integer update rounds to zero.

For eight successive inputs (x=1, desired=128), the eighth output is 1 and
the weights become (7,7,7,7,8,7,7,7). A ninth input x=1 therefore outputs 57.
The desired sample does not change the current output, only later outputs.

## Reset Behavior

Synchronous active-high reset cancels pending output and clears the delay
line and count. Weights become (0,0,0,0,1,0,0,0). `out_valid`, `out_y`, and
`out_updated` clear to zero. No input is consumed while reset is asserted.

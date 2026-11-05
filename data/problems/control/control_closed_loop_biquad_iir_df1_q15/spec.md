# Biquad IIR TDF2 Q15

This block applies a two-state transposed-direct-form-II biquad recurrence using signed Q15 arithmetic and per-sample coefficients. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module biquad_iir_tdf2_q15 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_a2,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_b2,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);
```

### Behavior interface

```python
class BiquadIirTdf2Q15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_a0, in_a1, in_a2, in_b1, in_b2): ...
```

Call `process` once per accepted sample, passing each input as an integer in
0..65535 encoding its signed Q15 port bits. It returns exactly
`{"out_y": value}`, with integer `value` in 0..65535 encoding the signed result,
not a Python float or a negative integer. Zero still produces this dictionary,
not `None`. `reset()` clears both retained filter states.

## Modules

- `biquad_iir_tdf2_q15`: top-level transaction operator.

## Functional Behavior

Transfers occur on rising edges with valid and ready both high. Output payloads remain valid and unchanged under backpressure. The block supports ordered responses and simultaneous output consumption and input acceptance.

All inputs and the output are signed 16-bit values with 15 fractional bits, carried as raw port patterns. Define `sat(v)=max(-32768,min(32767,v))` and `mul(a,b)=sat(floor(a*b/32768))`. Negative products round toward negative infinity. Decode each input as signed before arithmetic.

From the old state `z1,z2`, compute:
```text
y       = sat(mul(x,a0) + z1)
z1_next = sat(mul(x,a1) + z2 - mul(b1,y))
z2_next = sat(mul(x,a2) - mul(b2,y))
```
All sums use widened signed arithmetic. Commit both next states together and emit `y`. Coefficients may change on each accepted request. This is the transposed direct-form-II recurrence; coefficient design and floating-point arithmetic are not part of the task.

## Reset Behavior

Reset is synchronous active-high `rst`. It cancels any pending response, clears `out_valid` and all output payloads to zero. Both retained filter states z1 and z2 reset to zero and advance only on accepted inputs. New requests can be accepted after reset is released.

# SignalProcessingCnnSoftmaxArmSoftmaxU8
Compute quantized softmax over four unsigned 8-bit logits, using a maximum-subtracted Q31 exponential and reciprocal normalization. Each accepted input produces one ordered output containing four unsigned 8-bit probabilities, approximately scaled by 256.
## Interface
### RTL interface
Module `signal_processing_cnn_softmax_arm_softmax_u8` has no parameters.

```systemverilog
module signal_processing_cnn_softmax_arm_softmax_u8 (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_input,in_mult,in_shift,in_diff_min,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_output,
    output logic out_error
);
endmodule
```

| Port | Direction | Width | Meaning |
| --- | --- | --- | --- |
| clk, rst | input | 1 each | Rising-edge clock; synchronous active-high reset |
| in_valid | input | 1 | Input payload valid |
| in_ready | output | 1 | Input can be accepted |
| in_input | input | 32 | Four uint8 logits; lane j occupies bits 8j+7:8j |
| in_mult | input | 32 | Signed nonnegative Q31 multiplier |
| in_shift | input | 32 | Signed left shift, 0 through 30 |
| in_diff_min | input | 32 | Signed nonpositive difference cutoff |
| out_valid | output | 1 | Output payload valid |
| out_ready | input | 1 | Output can be accepted |
| out_output | output | 32 | Four uint8 results in the same lane order |
| out_error | output | 1 | Invalid controls or overflowing included difference |

Transfers occur on a rising edge with valid and ready both asserted. A producer must hold valid and its complete payload stable while stalled. Outputs are ordered and may have arbitrary finite latency. Reset cancels pending transfers.
### Behavior interface
`SignalProcessingCnnSoftmaxArmSoftmaxU8()` provides `reset()` and
`process(in_input, in_mult, in_shift, in_diff_min)`.
Each `process(...)` call represents one accepted four-logit request and returns
one dictionary with exactly the keys `out_output` and `out_error`. Arguments
are unsigned 32-bit port bit patterns; the three controls have the signed
interpretation defined below. `out_output` is one unsigned 32-bit packed word,
not a list of four probabilities, and `out_error` is an unsigned one-bit value.
Invalid requests also return both fields with the specified error values.
The result is never `None` or a list.

There is no numerical state between calls. `reset()` returns `None` and emits
no record. Clock cycles, stalls and output consumption do not cause additional
model calls.
## Modules
One four-lane quantized softmax unit with a ready/valid transaction interface.
## Functional Behavior
Interpret the three controls as two's-complement int32. Reject negative `mult`, a shift outside [0,30], or positive `diff_min`. Let `d[j] = logit[j] - max(logits)`. Lanes with `d[j] < diff_min` contribute zero. For each included lane, also reject if `d[j] * 2**shift < -2**31`. Rejection returns `out_error=1, out_output=0`; otherwise `out_error=0`.

The following integer functions specify every rounding step; no host floating-point arithmetic or fused operations are implied. `trunc` truncates toward zero; `>>` is arithmetic right shift; `clip32` saturates to [-2**31,2**31-1].
```text
M(a,b):
  if a == b == -2**31: return 2**31-1
  nudge = 2**30 if a and b have the same sign, else 1-2**30
  return trunc((a*b+nudge)/2**31)
D(x,n):
  q = x >> n
  mask = 2**n-1
  return q + ((x & mask) > ((mask >> 1) + (q < 0)))
P(x,n) = clip32(x * 2**n)
E(v):
  a = (v & (2**24-1)) - 2**24
  remainder = a-v
  x = 32*a + 2**28
  x2 = M(x,x)
  poly = D(M(D(M(x2,x2),2) + M(x2,x),715827883) + x2,1)
  result = 1895147668 + M(1895147668,x+poly)
  constants = [1672461947,1302514674,790015084,290630308,
               39332535,720401,242]
  for j = 0..6:
    if remainder & 2**(24+j): result = M(result,constants[j])
  return 2**31-1 if v == 0, else result
R(v):
  half = (v+2**31) >> 1
  x = 1515870810 + M(half,-1010580540)
  repeat 3 times: x = x + P(M(x,2**29-M(half,x)),2)
  return P(x,1)
```
For an included lane set `e[j]=E(M(d[j]*2**shift,mult))`; otherwise set `e[j]=0`. Form `sum=D(e[0],12)+...+D(e[3],12)`. The maximum lane is always included, so this sum is positive. Let `h` be the number of leading zeros in the 32-bit representation of `sum`. Set `scale=R(sum*2**h-2**31)` and `n=35-h`. The final included-lane result is `clip(D(M(scale,e[j]),n),0,255)`; an excluded lane is zero.

Packing does not renormalize or force an exact byte sum of 256. Equal logits produce four 64s; a sharply dominant lane can saturate at 255. All intermediates use the integer widths/ranges implied above; the Q31 products are formed in at least 64 bits before rounding.
## Reset Behavior
On a rising edge with `rst=1`, clear pending output, drive `out_valid=0`, `out_output=0`, and `out_error=0`. There is no numerical state between transactions. Reset takes priority over input acceptance.

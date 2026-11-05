# ProjectionResidualS8

A quantized four-channel projection-residual block, not elementwise addition alone. X=in_a and the independent skip vector B=in_b each contain four INT8 lanes. W contains sixteen INT8 weights, row-major index 4*i+j; bias contains four INT16 lanes. Input offsets are signed nine-bit additive offsets (not subtracted zero points); in_zero_out is signed INT8. All three multipliers are nonnegative Q0.31 integers; all right shifts are unsigned 0..31 and the common left shift L is 0..7. For output lane i compute P[i]=bias[i]+sum_j(W[i,j]*(X[j]+offset_a)), A[i]=R(H(P[i]*2^L,mult_a),shift_a), S[i]=R(H((B[i]+offset_b)*2^L,mult_b),shift_b), then Y[i]=Clip8(R(H(A[i]+S[i],mult_out),shift_out)+zero_out). Do not combine the two branch roundings or clip a branch before the sum. The exact intermediates fit signed 32 bits. This explicitly composes the CMSIS-NN dense dot-product and two-rounding residual-add dataflows. There is no intermediate INT8 clipping between projection and residual addition; this fused graph is an adaptation, not a claim that a clipped standalone fully-connected call followed by an add is equivalent.

## Interface

### RTL interface

```systemverilog
module ProjectionResidualS8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_a,
    input logic [31:0] in_b,
    input logic [127:0] in_weights,
    input logic [63:0] in_bias,
    input logic [8:0] in_offset_a,
    input logic [8:0] in_offset_b,
    input logic [30:0] in_mult_a,
    input logic [30:0] in_mult_b,
    input logic [30:0] in_mult_out,
    input logic [4:0] in_shift_a,
    input logic [4:0] in_shift_b,
    input logic [4:0] in_shift_out,
    input logic [2:0] in_left_shift,
    input logic [7:0] in_zero_out,
    output logic [31:0] out_values
);
endmodule
```

### Behavior interface

```python
class ProjectionResidualS8:
    def reset(self): ...
    def process(self, in_a, in_b, in_weights, in_bias, in_offset_a, in_offset_b, in_mult_a, in_mult_b, in_mult_out, in_shift_a, in_shift_b, in_shift_out, in_left_shift, in_zero_out): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Unused
padding bits do not exist. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`ProjectionResidualS8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

A quantized four-channel projection-residual block, not elementwise addition alone. X=in_a and the independent skip vector B=in_b each contain four INT8 lanes. W contains sixteen INT8 weights, row-major index 4*i+j; bias contains four INT16 lanes. Input offsets are signed nine-bit additive offsets (not subtracted zero points); in_zero_out is signed INT8. All three multipliers are nonnegative Q0.31 integers; all right shifts are unsigned 0..31 and the common left shift L is 0..7. For output lane i compute P[i]=bias[i]+sum_j(W[i,j]*(X[j]+offset_a)), A[i]=R(H(P[i]*2^L,mult_a),shift_a), S[i]=R(H((B[i]+offset_b)*2^L,mult_b),shift_b), then Y[i]=Clip8(R(H(A[i]+S[i],mult_out),shift_out)+zero_out). Do not combine the two branch roundings or clip a branch before the sum. The exact intermediates fit signed 32 bits. This explicitly composes the CMSIS-NN dense dot-product and two-rounding residual-add dataflows. There is no intermediate INT8 clipping between projection and residual addition; this fused graph is an adaptation, not a claim that a clipped standalone fully-connected call followed by an add is equivalent.

Define H(a,b) for signed 32-bit a,b as follows: if both equal
-2^31, H=2^31-1. Otherwise set p=a*b in signed 64-bit precision and
n=2^30 for p>=0, or n=1-2^30 for p<0; H=trunc_toward_zero((p+n)/2^31).
Define R(x,e)=floor(x/2^e)+[r>t], with r=x mod 2^e in [0,2^e),
t=floor((2^e-1)/2)+[x<0]. This is nearest-integer rounding, ties away
from zero (including e=0 as the identity). These are two separate rounding
steps, not a fused division. Clip8 saturates to [-128,127].

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked packing example

A byte sequence `[1,2,3,4]` is packed as `0x04030201`; the low byte is element
zero. Signed byte `-1` is `0xff`. This example specifies packing, not a
restriction to small positive inputs.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

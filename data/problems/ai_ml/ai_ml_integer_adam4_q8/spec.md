# Adam4Q8

One bias-corrected four-parameter Adam update with bounded explicit state.

## Interface

### RTL interface

```systemverilog
module Adam4Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_params,
    input logic [63:0] in_gradient,
    input logic [63:0] in_mean,
    input logic [127:0] in_square,
    input logic [2:0] in_step_minus1,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class Adam4Q8:
    def reset(self): ...
    def process(self, in_params, in_gradient, in_mean, in_square, in_step_minus1): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 256.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Any unused output slots are zero as defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`Adam4Q8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

One bias-corrected four-parameter Adam update with bounded explicit state.

RNE is nearest integer with ties to even. Clip16 saturates to [-32768,32767]. All state is explicitly passed in and returned, not hidden between requests. p,g,m are INT16 Q8; v is UINT32 Q16. Let t=1+in_step_minus1 (1..8), A=2^t, B=A-1. Both beta coefficients are 1/2, learning rate is 1/16, epsilon 1/256. Compute mnew=RNE((m+g)/2), vnew=floor((v+g*g)/2), vhat=floor(vnew*A/B), d=floor(sqrt(vhat))+1, delta=RNE(16*mnew*A/(B*d)), pnew=Clip16(p-delta). Output pnew[4] at 16*i, mnew[4] at 64+16*i, and vnew[4] at 128+32*i. No clipping occurs before the exact vhat calculation (which can exceed 32 bits); vnew always fits UINT32. The declared moment quantization, integer square root and final saturation specialize Adam to fixed-point storage; this is not AdamW, AMSGrad or a claim of bit identity with floating-point Adam.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For t=1, p=256, g=16 and old m=v=0: mnew=8, vnew=128, vhat=256, d=17, delta=15 and pnew=241.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.

# Scalar RWKV6 weighted key/value recurrence

A source-grounded one-token recurrent update retaining the old-state output, current-token bonus and exponential state decay.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_rwkv_rwkv6_wkv_fwd (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_B,
    input  logic [31:0]  in_T,
    input  logic [31:0]  in_H,
    input  logic [31:0]  in_K,
    input  logic [31:0]  in_V,
    input  logic         in_reverse,
    input  logic         in_use_initial_state,
    input  logic         in_output_final_state,
    input  logic [31:0]  in_scale,
    input  logic [31:0]  in_q,
    input  logic [31:0]  in_k,
    input  logic [31:0]  in_v,
    input  logic [31:0]  in_w,
    input  logic [31:0]  in_u,
    input  logic [31:0]  in_initial_state,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_o,
    output logic [31:0]  out_final_state
);
```

### Behavior interface

```python
class AiMlSsmRwkvRwkv6WkvFwd:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_B, in_T, in_H, in_K, in_V, in_reverse,
                in_use_initial_state, in_output_final_state, in_scale,
                in_q, in_k, in_v, in_w, in_u, in_initial_state): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`, `out_o`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

The five dimension ports B,T,H,K,V are unsigned32 integers, not floating-point encodings. The three control flags are Boolean. All remaining data ports are raw binary32 words. This is the scalar one-token specialization B=T=H=K=V=1; any other shape returns zero for both output fields. With one token, reverse traversal has no effect.

For a legal shape, select s=`in_initial_state` when `in_use_initial_state=1`, otherwise +0. In the following order compute:

1. kv=mul(k,v), bonus=mul(kv,u), mix=add(s,bonus).
2. qs=mul(q,scale), o=mul(mix,qs).
3. d=E(w), state_new=add(mul(s,d),kv).

Return `out_o=o` and `out_final_state=state_new` when requested, otherwise zero. `w` is the natural-log decay, not a multiplier or a sign selector. In ordinary RWKV use it is nonpositive; zero preserves incoming state, negative values decay it continuously, and positive values grow it under the same numerical policy. The output uses the **old** state plus the u-weighted current outer product; the updated state is not substituted into o.

State is explicitly input/output, not hidden across transactions. A sequence is evaluated by feeding the returned final_state into the next call. Hiding final state does not alter o. For s=3, q=scale=1, k=v=u=0, w=0, the result is o=3 and state_new=3. For w=-1 the next state is approximately3/e, not zero.

### Numerical contract

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

The exponential uses range reduction and a positive piecewise-linear approximation, not a sign-based switch. Define `E2(x)` on a raw binary32 word. As an explicit approximation rule, either zero or any subnormal input returns exactly 1; this is not a rule of the basic FP32 arithmetic. For other inputs:

- NaN gives canonical NaN; negative infinity or finite x < -126 gives +0; positive infinity or x >= 128 gives +infinity.
- Otherwise q=floor(65536*x), n=floor(q/65536), f=q-65536*n, j=floor(f/2048), r=f-2048*j.
- For i=0..32, T[i] is the nearest-even integer to `2^(i/32)*2^30`. Set Y=T[j]+floor((T[j+1]-T[j])*r/2048).
- Return the nearest-even binary32 rounding of `Y*2^(n-30)`, with nearest-even rounding and signed-infinity overflow. The explicit x < -126 cutoff above belongs only to this approximation.

`E(x) = E2(mul(x, 0x3FB8AA3B))`, where the constant is binary32 log2(e). This defines both the table and the interpolation independently of either implementation. For -126 <= x < 128, E2 has relative error below 0.01% against 2^x (away from overflow); integer powers of two are exact. For E, the same 0.01% bound applies where its scaled argument is in this normal-output range. The approximation deliberately returns zero below its stated cutoff; basic FP32 arithmetic still uses gradual underflow. The task fixes observable arithmetic, not a required ROM or circuit architecture.


Each accepted input emits exactly one ordered output record. Transfers occur at rising clock edges when valid and ready are both high. Output valid and payload stay stable while stalled; inputs have no effect before acceptance.

## Reset Behavior

Synchronous active-high reset discards any pending output, deasserts `out_valid` and zeros output payloads. There is no persistent numerical state. When idle, output payloads are zero and the module can accept an input; `in_ready=1` after reset. A stalled result must not be overwritten.

# T5 normalized cross-attention with residual

One four-channel decoder token cross-attends to four four-channel encoder tokens using two width-two heads. This retains the source T5 pre-RMS normalization, supplied Q/K/V/output matrices, unscaled attention scores and residual addition. Dropout is disabled; no hidden cross-transaction cache or learned relative-bias generator is included.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_cross_t5_cross_attention (
    input  logic          clk,
    input  logic          rst,

    input  logic          in_valid,
    output logic          in_ready,
    input  logic [127:0]  in_hidden_states,
    input  logic [511:0]  in_key_value_states,
    input  logic [127:0]  in_attention_mask,
    input  logic [127:0]  in_ln_weight,
    input  logic [511:0]  in_q_weight,
    input  logic [511:0]  in_k_weight,
    input  logic [511:0]  in_v_weight,
    input  logic [511:0]  in_o_weight,

    output logic          out_valid,
    input  logic          out_ready,
    output logic [127:0]  out_layer_output,
    output logic [127:0]  out_position_bias,
    output logic [127:0]  out_attn_weights
);
```

### Behavior interface

```python
class AiMlTxAttnCrossT5CrossAttention:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hidden_states, in_key_value_states, in_attention_mask, in_ln_weight, in_q_weight, in_k_weight, in_v_weight, in_o_weight): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_weights`, `out_layer_output`, `out_position_bias`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

All data lanes are raw binary32 words. Packed lane zero occupies the least-significant 32 bits. Each accepted input produces one ordered output record. A transfer occurs on a rising edge with valid and ready both high. Output valid and payload stay stable while stalled; unaccepted inputs have no effect. Latency and architecture are unrestricted within the declared execution constraints.

## Modules

The named top-level module implements the full task with no external numerical state.

## Functional Behavior

The hidden vector and layer-normalization weight have four lanes each. Encoder lane index is `4*token+channel`; each supplied weight matrix is 4x4 row-major (`4*output_channel+input_channel`). The additive mask has one logit bias per encoder token, shared across heads.

Use `tree4(a,b,c,d)=add(add(a,b),add(c,d))`. For hidden input `x`, compute `mean=div(tree4(mul(x0,x0),...,mul(x3,x3)),4)`, `den=add(mean,0x358637BD)` (binary32 epsilon 1e-6), `r=div(1,sqrt(den))`, and `norm[d]=mul(gamma[d],mul(x[d],r))`. Square root is correctly rounded binary32 nearest-even with the gradual-underflow and special-value rules below, not an exponent-only estimate. It preserves +infinity and signed zero; negative nonzero inputs and NaNs produce canonical NaN.

Each matrix-vector row is the `tree4` sum of its four separately rounded products. Form `Q=Wq*norm`; for every encoder token, `K[t]=Wk*encoder[t]`, `V[t]=Wv*encoder[t]`. Head `h` uses channels `2h,2h+1`. Score for token `t` is `add(add(mul(Q[2h],K[t,2h]),mul(Q[2h+1],K[t,2h+1])),mask[t])`. There is **no** `1/sqrt(2)` factor: T5's selected source uses unscaled dot products.

Normalize each four-token score row below. Context channel `2h+d` is the `tree4` sum of its four probability/value terms. Project this four-channel context by `Wo`, then return `out_layer_output[d]=add(x[d],projected[d])`. `out_position_bias` echoes the raw input mask words. `out_attn_weights` returns head zero's four probabilities (head one is used internally). Zero Q weights yield uniform attention over finite unmasked logits; zero output weights preserve a finite residual input. Both dense and nonidentity matrices are functional inputs.

### Stable normalization

For each score row, any NaN makes every probability canonical NaN. An all-negative-infinity row produces all-zero probabilities. Positive-infinity maxima share probability equally; other positions get zero. Otherwise subtract the finite maximum, apply `E` to every difference, sum the numerators left-to-right from +0, and divide each numerator by that sum. A zero-probability value term contributes +0 without evaluating `0 * value`, so excluded values cannot contaminate the result. Nonzero/NaN weights follow ordinary floating arithmetic. Ties retain all contributions, not the first maximum.

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


## Reset Behavior

Synchronous active-high reset clears pending output, deasserts `out_valid`, zeros output payloads and leaves `in_ready=1`. There is no persistent numerical state. An unconsumed result is discarded by reset. Idle payloads are zero; a stalled result must not be overwritten.

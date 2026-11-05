# Stateful Gated DeltaNet recurrent kernel

A single-head recurrent kernel with query/key normalization, gated state decay,
key-based error correction and query readout. Each accepted token updates a
persistent `K x V` matrix and produces one output vector. Input projection,
short convolution, final gated normalization and output projection are separate
operators outside this kernel.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_deltanet_gated_deltanet_step #(
    parameter integer K = 8,
    parameter integer V = 8
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic in_start,
    input logic [32*K-1:0] in_q,
    input logic [32*K-1:0] in_k,
    input logic [32*V-1:0] in_v,
    input logic [31:0] in_g,
    input logic [31:0] in_beta,
    input logic [31:0] in_A_log,
    input logic [31:0] in_dt_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [32*V-1:0] out_o
);
```

`K` and `V` are positive dimensions. Lane `i` occupies bits `[32*i +: 32]`,
with lane zero in the least-significant word. All data lanes and scalar data
ports contain raw binary32 encodings. `in_start` is a one-bit control.

### Behavior interface

```python
class AiMlSsmDeltanetGatedDeltanetStep:
    def __init__(self, K=8, V=8): ...
    def reset(self): ...
    def process(self, in_start, in_q, in_k, in_v, in_g, in_beta,
                in_A_log, in_dt_bias): ...
```

Return exactly one dictionary on every call, never None or a list.
Every returned dictionary contains exactly this field:

- `out_o`: `32*V` bits, packed in the same lane order as the RTL.

Process arguments and returned fields are nonnegative integers of their declared
port widths. Floating-point signs are encoded in the binary32 words.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor dimensions must match the RTL parameters. One call represents one
accepted token, not one clock cycle. The model retains its matrix and sequence
configuration across calls.

### Transaction protocol

Transfers occur on rising clock edges with valid and ready high. Every accepted
input token produces exactly one ordered output vector. A stalled output keeps
`out_valid` asserted and `out_o` unchanged. Latency, initiation interval and
internal parallelism are implementation choices.

An accepted token with `in_start=1` starts a new sequence: use a zero matrix as
its prior state, latch `in_A_log` and `in_dt_bias`, then process that same token
using the new configuration. This is not a separate configuration transaction.
It does not cancel outputs owed to earlier tokens. With `in_start=0`, use the
previous token's updated matrix and the saved configuration. The values on
`in_A_log` and `in_dt_bias` are then ignored. `in_g` and `in_beta` remain
per-token inputs. Neither state nor configuration changes for an unaccepted
input. Tokens are evaluated in acceptance order, including across sequence
boundaries.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

The state uses key-first layout `H[key,value]`. `in_beta` is an already-activated
scalar gain (normally 0..1), not a beta logit. All input bit patterns have the
numerical behavior below, including when a configuration input is ignored.

Define `dot(a,b)` by computing `t=mul(a[0],b[0])`, then
`t=add(t,mul(a[i],b[i]))` for increasing `i=1..K-1`. Products and sums each round
separately. For `K=1`, the dot product is its single product, with no added zero.

Normalize each input vector `x` in `{q,k}` using
`r=sqrt(add(dot(x,x),0x358637BD))`, then `xn[i]=div(x[i],r)`.
The epsilon is the binary32 encoding of 1e-6 and is retained at zero.
Let `kf` be the nearest-even binary32 conversion of the positive integer `K`.
The query scale is `div(0x3F800000,sqrt(kf))`, with both operations rounded to
binary32. Multiply each normalized query component by this scale. Do not scale
the normalized key. These normalized/scaled vectors are named `q` and `k` below.

1. Select the prior matrix and saved configuration using `in_start` as specified above.
2. `dt=SP(add(in_g,saved_dt_bias))`, `log_decay=neg(mul(E(saved_A_log),dt))`, `decay=E(log_decay)`.
3. `D[i,j]=mul(H[i,j],decay)`, for `i=0..K-1`, `j=0..V-1`.
4. `residual[j]=sub(v[j],dot(D[:,j],k))`; `delta[j]=mul(in_beta,residual[j])`. The projection reads the **decayed** state.
5. `N[i,j]=add(D[i,j],mul(k[i],delta[j]))`.
6. `o[j]=dot(N[:,j],q)`. Pack these `V` words into `out_o` and retain `N` as the next token's matrix.

Every recurrence step is performed even when a gate is zero. IEEE-754 cases such
as zero multiplied by infinity therefore retain the specified numerical result.
The state is internal and is not included in the output payload.

SP approximates softplus without overflow: e=E(-abs(x)), w=div(e,add(2,e)), w2=mul(w,w). Start term=w,total=w. For d=3,5,7,9,11 in that order, set term=mul(term,w2), then total=add(total,div(term,float32(d))). Set correction=mul(2,total); return correction for a negative sign bit, otherwise add(x,correction). This gives canonical NaN for NaN, +infinity for +infinity and +0 for -infinity. The real series remainder is below 1.1e-7 when 0<=w<=1/3; input/operation rounding and E approximation are additional errors, not part of that remainder. This is a specified approximation, not a discontinuous sign table.

`neg` flips the sign bit. All sums and products round separately, with no fused
arithmetic or alternate reassociation.

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

Synchronous active-high reset clears H and both saved configuration words to
positive zero, discards pending work and output, deasserts `out_valid` and zeros
`out_o`. No input is accepted while reset is asserted. After reset the module
can accept a token. A token with `in_start=0` after reset uses the zero state
and zero configuration. Output payload is otherwise meaningful only while
`out_valid=1`.

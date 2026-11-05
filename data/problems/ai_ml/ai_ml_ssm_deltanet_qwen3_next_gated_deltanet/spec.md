# Qwen3-Next scalar gated delta mixer

One cached token update with persistent convolution and recurrent state, specialized to the dimensions and constants below. Input/output projection outside this boundary is not included. The fixed-point arithmetic is a declared hardware adaptation, not bit-identical floating-point model execution.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_deltanet_qwen3_next_gated_deltanet (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [31:0] in_query,
    input  logic signed [31:0] in_key,
    input  logic signed [31:0] in_value,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_a,
    input  logic signed [31:0] in_z,

    input  logic signed [31:0] in_conv_weight_q_prev,
    input  logic signed [31:0] in_conv_weight_q_cur,
    input  logic signed [31:0] in_conv_weight_k_prev,
    input  logic signed [31:0] in_conv_weight_k_cur,
    input  logic signed [31:0] in_conv_weight_v_prev,
    input  logic signed [31:0] in_conv_weight_v_cur,
    input  logic signed [31:0] in_dt_bias,
    input  logic signed [31:0] in_A,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [31:0] out_data
);
```

### Behavior interface

```python
class Qwen3NextGatedDeltaNet:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_query,
        in_key,
        in_value,
        in_b,
        in_a,
        in_z,
        in_conv_weight_q_prev,
        in_conv_weight_q_cur,
        in_conv_weight_k_prev,
        in_conv_weight_k_cur,
        in_conv_weight_v_prev,
        in_conv_weight_v_cur,
        in_dt_bias,
        in_A,
    ): ...
```

`process(...)` returns one dict per accepted input:
`{"out_data": <uint32 bit pattern>}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Latency and architecture may vary within the declared execution constraints.

## Functional Behavior

Data and state words are signed Q16.16 transported as their 32-bit encodings. Decode inputs before arithmetic. Define W(v) as signed two's-complement wrapping to 32 bits. Ordinary sums and differences use W. Define M(a,b)=W(trunc_toward_zero(a*b/65536)). All input bit patterns have defined results; extreme overflowing MACs need not retain the real-valued source's stability properties.

One value head and one key/value coordinate, with a two-tap causal convolution per query/key/value. State is three previous raw input samples and one recurrent scalar H. in_A supplies the already-exponentiated positive magnitude exp(A_log), not A_log itself. Nonpositive raw magnitudes have defined arithmetic but are outside that physical interpretation. Input/output projections and learned RMS weight are outside this specialization; the RMS weight is one.

1. For each q/k/v channel, c=G(W(M(previous_sample,weight_prev)+M(current_input,weight_cur))).
2. q=N(cq,cq*cq+EPS); k=N(ck,ck*ck+EPS). Key dimension one makes query scaling 1/sqrt(dim)=1; epsilon is still retained.
3. beta=B(in_b), dt=P(W(in_a+in_dt_bias)), decay=E(W(-M(in_A,dt))).
4. D=M(H,decay), residual=W(cv-M(D,k)), delta=M(residual,beta), Hnext=W(D+M(k,delta)). The residual reads the **decayed** state.
5. core=M(Hnext,q), normalized=N(core,core*core+EPS), out_data=M(normalized,G(in_z)). Qwen applies RMS normalization **before** its final gate, unlike Mamba2.
6. Commit Hnext and the three current raw q/k/v input samples. Return out_data in one mapping. All four states persist across accepted input records.


### Numerical functions

Let S=65536, Q=1099511627776 and L=762123384786. R(n,d) is signed nearest-integer division with exact ties to even (d>0). sat(v) clamps an integer to [-2147483648,2147483647]. These wider integer operations are used **only inside the nonlinear functions**; ordinary MACs still use the trunc_toward_zero multiply defined here.

The following integer recurrence defines the exponential approximation `E(v)`. It is part of the numerical contract, not a prescribed circuit or latency.

```text
F(v):                         # internal Q40 exponential; callers have v < 11*S
    if v <= -32*S: return 0
    k = floor((v*2^24)/L)
    r = v*2^24 - k*L
    term = total = Q
    for j = 1..12:
        term = R(term*r, Q*j)
        total = total + term
    return total*2^k if k >= 0 else R(total, 2^(-k))

E(v):
    if v >= 11*S: return 2147483647
    if v <= -16*S: return 0
    return sat(R(F(v), 2^24))
```

`L` is the nearest Q40 representation of ln(2); range reduction and all twelve polynomial terms are retained. The underflow and saturation branches do not replace finite nonzero exponentials inside the representable range. This is a fixed-point approximation contract, not IEEE-754 arithmetic; there are no NaN or infinity encodings.

Softplus `P(v)` and SiLU `G(v)` use stable formulas with a nonpositive exponential argument:

```text
P(v):
    e = F(-abs(v))
    z = R(e*Q, 2*Q+e)
    z2 = R(z*z, Q)
    term = total = z
    for j = 1..11:
        term = R(term*z2, Q)
        total = total + R(term, 2*j+1)
    return sat(max(v,0) + R(2*total, 2^24))

G(v):
    e = F(-abs(v))
    numerator = abs(v)*e if v < 0 else v*Q
    magnitude = R(numerator, Q+e)
    return -magnitude if v < 0 else magnitude
```

`P` approximates `log(1+exp(v/S))` and `G` approximates `(v/S)/(1+exp(-v/S))`, both returned in Q16.16. `abs` uses a wider integer, including for raw `-2147483648`.

Use exp_approx(v)=E(v), softplus_approx(v)=P(v) and silu_approx(v)=G(v). All arguments are decoded as signed 32-bit integers. Nonlinear saturation is explicit; it is not applied to surrounding multiply/add operations. These functions approximate the source operations rather than substitute constant sign buckets. No claim of global floating-point equivalence is made for overflowing fixed-point MACs.


Normalization retains epsilon instead of reducing every nonzero value to its sign. In Q32.32 units let EPS=4295, the nearest integer to 1e-6*2^32. For positive integer d define root(d)=floor(sqrt(d*2^32)) and N(v,d)=R(v*2^32,root(d)). Squares, sums, root and division are wide mathematical integer operations: **no 32-bit wrap, qmul or reciprocal clipping occurs in this normalization**. Thus N has Q16.16 output. This specifies a high-resolution integer square root, not a required circuit. Scalar L2/RMS normalization uses d=v*v+EPS; two-channel RMS uses d=floor((v0*v0+v1*v1)/2)+EPS.

Sigmoid B(v) uses e=F(-abs(v)) and returns R(S*(e if v<0 else Q),Q+e). SiLU is G(v), not multiplication by a coarse sigmoid bucket.


Each accepted input produces one ordered output record. State changes only on input acceptance. Transfers occur at rising edges with valid and ready high. Output valid and all payload fields remain stable until accepted; a stalled output cannot be overwritten.

## Reset Behavior

Synchronous active-high reset zeros all convolution/recurrent state and output payloads, discards pending output and deasserts out_valid. No transaction is accepted during reset. The module is ready for new input after reset. Payload values while out_valid is low are not compared, except the reset values just specified.

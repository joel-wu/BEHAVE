# Mamba2 cached two-channel mixer

One cached token update with persistent convolution and recurrent state, specialized to the dimensions and constants below. Input/output projection outside this boundary is not included. The fixed-point arithmetic is a declared hardware adaptation, not bit-identical floating-point model execution.

## Interface

### RTL interface

```systemverilog
module AiMlSsmMamba2HfMamba2Step (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [31:0] in_gate_0,
    input  logic signed [31:0] in_gate_1,
    input  logic signed [31:0] in_hidden_bc_0,
    input  logic signed [31:0] in_hidden_bc_1,
    input  logic signed [31:0] in_hidden_bc_2,
    input  logic signed [31:0] in_hidden_bc_3,
    input  logic signed [31:0] in_hidden_bc_4,
    input  logic signed [31:0] in_hidden_bc_5,
    input  logic signed [31:0] in_dt_0,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [31:0] out_y_0,
    output logic signed [31:0] out_y_1
);
```

### Behavior interface

```python
class AiMlSsmMamba2HfMamba2Step:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_gate_0,
        in_gate_1,
        in_hidden_bc_0,
        in_hidden_bc_1,
        in_hidden_bc_2,
        in_hidden_bc_3,
        in_hidden_bc_4,
        in_hidden_bc_5,
        in_dt_0,
    ):
        pass
```

The `process(...)` method returns one dict per accepted input transaction with keys:
`out_y_0`, `out_y_1`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y_0`, `out_y_1`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Latency and architecture may vary within the declared execution constraints.

## Functional Behavior

Data and state words are signed Q16.16 transported as their 32-bit encodings. Decode inputs before arithmetic. Define W(v) as signed two's-complement wrapping to 32 bits. Ordinary sums and differences use W. Define M(a,b)=W(floor(a*b/65536)). All input bit patterns have defined results; extreme overflowing MACs need not retain the real-valued source's stability properties.

Configuration: one head, two hidden coordinates, two state coordinates, six convolution channels, three taps, dt_bias=0.5, A=-1, D=1 and final RMS weights=1. State consists of conv_state[6][3] (oldest to newest) and H[2][2] (hidden coordinate, state coordinate). The six input channels are hidden0, hidden1, B0, B1, C0, C1, in that order.

The convolution weights, expressed as raw Q16.16 integers, are rows [16384,32768,65536], [8192,24576,49152], [32768,16384,8192], [-16384,16384,32768], [24576,8192,-8192], [16384,-16384,16384]. Biases are zero. For each channel shift its state left and insert the current in_hidden_bc_ch; sum the three separately narrowed M(tap,weight) products with W, then apply G to obtain the six activations x0,x1,B0,B1,C0,C1.

1. dt=P(W(in_dt_0+32768)); decay=E(M(dt,-65536)).
2. For i,j=0,1, T[i,j]=W(M(H[i,j],decay)+M(M(dt,Bj),xi)).
3. y[i]=W(M(T[i,0],C0)+M(T[i,1],C1)+xi).
4. v[i]=M(y[i],G(in_gate_i)). Mamba2 applies its gate **before** RMS normalization.
5. d=floor((v0*v0+v1*v1)/2)+EPS, out_y_i=N(v[i],d), using the normalization function below.
6. Commit the shifted convolution states and H=T. Emit out_y_0 and out_y_1 together. Both numerical states persist to the next accepted input.


### Numerical functions

Let S=65536, Q=1099511627776 and L=762123384786. R(n,d) is signed nearest-integer division with exact ties to even (d>0). sat(v) clamps an integer to [-2147483648,2147483647]. These wider integer operations are used **only inside the nonlinear functions**; ordinary MACs still use the floor multiply defined here.

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

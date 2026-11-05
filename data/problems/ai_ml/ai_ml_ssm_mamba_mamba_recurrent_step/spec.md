# ai_ml_ssm_mamba_mamba_recurrent_step

This block performs one single-token recurrent update of a Mamba selective state-space layer for one scalar inner channel. One accepted input transaction represents one decoding step. The block updates a depthwise causal-convolution shift register, computes the selective SSM state update with a discretised time step, and produces the corresponding output activation. The computation is a strict 1:1 transaction mapping: every accepted input produces exactly one output transaction containing the new output value, the updated convolution state, and the updated SSM state.

Data words are signed 32-bit Q16.16. Ordinary MACs retain wrapping additions and floor-shift multiplication; the nonlinear functions use the explicit wider, rounded approximation defined below. The specialization preserves input projection, causal convolution, selective discretization, all sixteen SSM states, skip/gate and output projection from Mamba.step. It is not a full model or a floating-point implementation. All input bit patterns have defined fixed-point results.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_mamba_mamba_recurrent_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_hidden_state,
    input  logic [31:0] in_conv_state_0,
    input  logic [31:0] in_conv_state_1,
    input  logic [31:0] in_conv_state_2,
    input  logic [31:0] in_conv_state_3,
    input  logic [31:0] in_ssm_state_0,
    input  logic [31:0] in_ssm_state_1,
    input  logic [31:0] in_ssm_state_2,
    input  logic [31:0] in_ssm_state_3,
    input  logic [31:0] in_ssm_state_4,
    input  logic [31:0] in_ssm_state_5,
    input  logic [31:0] in_ssm_state_6,
    input  logic [31:0] in_ssm_state_7,
    input  logic [31:0] in_ssm_state_8,
    input  logic [31:0] in_ssm_state_9,
    input  logic [31:0] in_ssm_state_10,
    input  logic [31:0] in_ssm_state_11,
    input  logic [31:0] in_ssm_state_12,
    input  logic [31:0] in_ssm_state_13,
    input  logic [31:0] in_ssm_state_14,
    input  logic [31:0] in_ssm_state_15,

    input  logic [31:0] in_proj_weight_x,
    input  logic [31:0] in_proj_weight_z,
    input  logic [31:0] in_proj_bias_x,
    input  logic [31:0] in_proj_bias_z,

    input  logic [31:0] conv1d_weight_0,
    input  logic [31:0] conv1d_weight_1,
    input  logic [31:0] conv1d_weight_2,
    input  logic [31:0] conv1d_weight_3,
    input  logic [31:0] conv1d_bias,

    input  logic [31:0] x_proj_weight_dt,
    input  logic [31:0] x_proj_weight_B_0,
    input  logic [31:0] x_proj_weight_B_1,
    input  logic [31:0] x_proj_weight_B_2,
    input  logic [31:0] x_proj_weight_B_3,
    input  logic [31:0] x_proj_weight_B_4,
    input  logic [31:0] x_proj_weight_B_5,
    input  logic [31:0] x_proj_weight_B_6,
    input  logic [31:0] x_proj_weight_B_7,
    input  logic [31:0] x_proj_weight_B_8,
    input  logic [31:0] x_proj_weight_B_9,
    input  logic [31:0] x_proj_weight_B_10,
    input  logic [31:0] x_proj_weight_B_11,
    input  logic [31:0] x_proj_weight_B_12,
    input  logic [31:0] x_proj_weight_B_13,
    input  logic [31:0] x_proj_weight_B_14,
    input  logic [31:0] x_proj_weight_B_15,
    input  logic [31:0] x_proj_weight_C_0,
    input  logic [31:0] x_proj_weight_C_1,
    input  logic [31:0] x_proj_weight_C_2,
    input  logic [31:0] x_proj_weight_C_3,
    input  logic [31:0] x_proj_weight_C_4,
    input  logic [31:0] x_proj_weight_C_5,
    input  logic [31:0] x_proj_weight_C_6,
    input  logic [31:0] x_proj_weight_C_7,
    input  logic [31:0] x_proj_weight_C_8,
    input  logic [31:0] x_proj_weight_C_9,
    input  logic [31:0] x_proj_weight_C_10,
    input  logic [31:0] x_proj_weight_C_11,
    input  logic [31:0] x_proj_weight_C_12,
    input  logic [31:0] x_proj_weight_C_13,
    input  logic [31:0] x_proj_weight_C_14,
    input  logic [31:0] x_proj_weight_C_15,

    input  logic [31:0] dt_proj_weight,
    input  logic [31:0] dt_proj_bias,

    input  logic [31:0] A_log_0,
    input  logic [31:0] A_log_1,
    input  logic [31:0] A_log_2,
    input  logic [31:0] A_log_3,
    input  logic [31:0] A_log_4,
    input  logic [31:0] A_log_5,
    input  logic [31:0] A_log_6,
    input  logic [31:0] A_log_7,
    input  logic [31:0] A_log_8,
    input  logic [31:0] A_log_9,
    input  logic [31:0] A_log_10,
    input  logic [31:0] A_log_11,
    input  logic [31:0] A_log_12,
    input  logic [31:0] A_log_13,
    input  logic [31:0] A_log_14,
    input  logic [31:0] A_log_15,

    input  logic [31:0] D_param,
    input  logic [31:0] out_proj_weight,
    input  logic [31:0] out_proj_bias,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_hidden_state,
    output logic [31:0] out_conv_state_0,
    output logic [31:0] out_conv_state_1,
    output logic [31:0] out_conv_state_2,
    output logic [31:0] out_conv_state_3,
    output logic [31:0] out_ssm_state_0,
    output logic [31:0] out_ssm_state_1,
    output logic [31:0] out_ssm_state_2,
    output logic [31:0] out_ssm_state_3,
    output logic [31:0] out_ssm_state_4,
    output logic [31:0] out_ssm_state_5,
    output logic [31:0] out_ssm_state_6,
    output logic [31:0] out_ssm_state_7,
    output logic [31:0] out_ssm_state_8,
    output logic [31:0] out_ssm_state_9,
    output logic [31:0] out_ssm_state_10,
    output logic [31:0] out_ssm_state_11,
    output logic [31:0] out_ssm_state_12,
    output logic [31:0] out_ssm_state_13,
    output logic [31:0] out_ssm_state_14,
    output logic [31:0] out_ssm_state_15
);
```

All data ports carry 32-bit two's-complement Q16.16 bit patterns (transported as `logic [31:0]`; the in-block arithmetic interprets them as `signed [31:0]`). This block is hard-wired for:
- batch size = 1 lane per transaction
- `d_inner = 1`
- `dt_rank = 1`
- `d_conv = 4`
- `d_state = 16`

### Behavior interface

```python
class ai_ml_ssm_mamba_mamba_recurrent_step:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_hidden_state,
        in_conv_state_0,
        in_conv_state_1,
        in_conv_state_2,
        in_conv_state_3,
        in_ssm_state_0,
        in_ssm_state_1,
        in_ssm_state_2,
        in_ssm_state_3,
        in_ssm_state_4,
        in_ssm_state_5,
        in_ssm_state_6,
        in_ssm_state_7,
        in_ssm_state_8,
        in_ssm_state_9,
        in_ssm_state_10,
        in_ssm_state_11,
        in_ssm_state_12,
        in_ssm_state_13,
        in_ssm_state_14,
        in_ssm_state_15,
        in_proj_weight_x,
        in_proj_weight_z,
        in_proj_bias_x,
        in_proj_bias_z,
        conv1d_weight_0,
        conv1d_weight_1,
        conv1d_weight_2,
        conv1d_weight_3,
        conv1d_bias,
        x_proj_weight_dt,
        x_proj_weight_B_0,
        x_proj_weight_B_1,
        x_proj_weight_B_2,
        x_proj_weight_B_3,
        x_proj_weight_B_4,
        x_proj_weight_B_5,
        x_proj_weight_B_6,
        x_proj_weight_B_7,
        x_proj_weight_B_8,
        x_proj_weight_B_9,
        x_proj_weight_B_10,
        x_proj_weight_B_11,
        x_proj_weight_B_12,
        x_proj_weight_B_13,
        x_proj_weight_B_14,
        x_proj_weight_B_15,
        x_proj_weight_C_0,
        x_proj_weight_C_1,
        x_proj_weight_C_2,
        x_proj_weight_C_3,
        x_proj_weight_C_4,
        x_proj_weight_C_5,
        x_proj_weight_C_6,
        x_proj_weight_C_7,
        x_proj_weight_C_8,
        x_proj_weight_C_9,
        x_proj_weight_C_10,
        x_proj_weight_C_11,
        x_proj_weight_C_12,
        x_proj_weight_C_13,
        x_proj_weight_C_14,
        x_proj_weight_C_15,
        dt_proj_weight,
        dt_proj_bias,
        A_log_0,
        A_log_1,
        A_log_2,
        A_log_3,
        A_log_4,
        A_log_5,
        A_log_6,
        A_log_7,
        A_log_8,
        A_log_9,
        A_log_10,
        A_log_11,
        A_log_12,
        A_log_13,
        A_log_14,
        A_log_15,
        D_param,
        out_proj_weight,
        out_proj_bias,
    ): ...
```

`process(...)` returns one dict per call with keys:
`out_hidden_state`, `out_conv_state_0`..`out_conv_state_3`, `out_ssm_state_0`..`out_ssm_state_15`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_conv_state_0`, `out_conv_state_1`, `out_conv_state_2`, `out_conv_state_3`,
  `out_hidden_state`, `out_ssm_state_0`, `out_ssm_state_1`, `out_ssm_state_10`,
  `out_ssm_state_11`, `out_ssm_state_12`, `out_ssm_state_13`, `out_ssm_state_14`,
  `out_ssm_state_15`, `out_ssm_state_2`, `out_ssm_state_3`, `out_ssm_state_4`,
  `out_ssm_state_5`, `out_ssm_state_6`, `out_ssm_state_7`, `out_ssm_state_8`,
  `out_ssm_state_9`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_mamba_mamba_recurrent_step`: top-level single-step recurrent update block.

## Functional Behavior

All data ports and architectural values are signed 32-bit Q16.16; nonlinear helpers use the wider integers specified below. The output transaction count is exactly one for every accepted input transaction. The block has no architectural state across transactions; all recurrent state is supplied on input ports and returned on output ports each transaction.

### Q16.16 multiply (`qmul`)

For any two signed 32-bit Q16.16 values `a` and `b`:

- `prod = a * b` using signed integer multiplication into a 64-bit signed result
- `qmul(a, b) = prod >>> 16`

`>>>` denotes arithmetic right shift by 16. The result is the low 32 bits of that shifted signed value as a hardware register assignment (modulo `2^32`). No saturation.

### Stable nonlinear functions

Let S=65536, Q=1099511627776 and L=762123384786. R(n,d) is signed nearest-integer division with exact ties to even (d>0). sat(v) clamps an integer to [-2147483648,2147483647]. These wider integer operations are used **only inside the nonlinear functions**; ordinary MACs still use the wrapping/floor qmul above.

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

### Step computation

Let:
- `hidden = in_hidden_state`
- `conv_state_old[i] = in_conv_state_i` for `i = 0..3`
- `ssm_state_old[n] = in_ssm_state_n` for `n = 0..15`

The block performs the following operations in order. All additions are 32-bit signed wrapping additions. All multiplications use `qmul`.

1. **Input projection**
   - `x_pre = qmul(hidden, in_proj_weight_x) + in_proj_bias_x`
   - `z_pre = qmul(hidden, in_proj_weight_z) + in_proj_bias_z`

2. **Causal depthwise convolution state update** (shift in `x_pre`)
   - `conv_state_new[0] = conv_state_old[1]`
   - `conv_state_new[1] = conv_state_old[2]`
   - `conv_state_new[2] = conv_state_old[3]`
   - `conv_state_new[3] = x_pre`

3. **Convolution sum + bias**
   - `conv_sum = qmul(conv_state_new[0], conv1d_weight_0) + qmul(conv_state_new[1], conv1d_weight_1) + qmul(conv_state_new[2], conv1d_weight_2) + qmul(conv_state_new[3], conv1d_weight_3)`
   - `conv_sum_b = conv_sum + conv1d_bias`

4. **SiLU activation on convolution path**
   - `x = silu_approx(conv_sum_b)`

5. **Projection to dt, B, and C**
   - `dt_raw = qmul(x, x_proj_weight_dt)`
   - For each `n = 0..15`:
     - `B[n] = qmul(x, x_proj_weight_B_n)`
     - `C[n] = qmul(x, x_proj_weight_C_n)`

6. **dt projection through softplus approximation**
   - `dt_pre = qmul(dt_raw, dt_proj_weight) + dt_proj_bias`
   - `dt = softplus_approx(dt_pre)`

7. **Continuous-time A parameter via exp approximation**
   - For each `n = 0..15`:
     - `A[n] = -exp_approx(A_log_n)` (negation of the Q16.16 result, computed as signed two's-complement on 32 bits)

8. **Discrete-time parameters**
   - For each `n = 0..15`:
     - `dA[n] = exp_approx(qmul(dt, A[n]))`
     - `dB[n] = qmul(dt, B[n])`

9. **SSM state update**
   - For each `n = 0..15`:
     - `ssm_state_new[n] = qmul(ssm_state_old[n], dA[n]) + qmul(x, dB[n])`

10. **SSM readout** (left-folded sum)
    - `y_ssm_0 = qmul(ssm_state_new[0], C[0])`
    - For `n = 1..15`: `y_ssm_n = y_ssm_{n-1} + qmul(ssm_state_new[n], C[n])`
    - `y_ssm = y_ssm_15`

11. **Skip connection**
    - `y_skip = qmul(D_param, x)`
    - `y_pre_gate = y_ssm + y_skip`

12. **SiLU gate**
    - `z = silu_approx(z_pre)`
    - `y = qmul(y_pre_gate, z)`

13. **Output projection**
    - `out_hidden_state = qmul(y, out_proj_weight) + out_proj_bias`

14. **State outputs**
    - `out_conv_state_i = conv_state_new[i]` for `i = 0..3`
    - `out_ssm_state_n = ssm_state_new[n]` for `n = 0..15`

The block is stateless across transactions because the recurrent states are fully supplied on the input ports and fully returned on the output ports every transaction.

Worked example for convolution state movement: if the input convolution state is `[c0, c1, c2, c3]`, then the output convolution state is `[c1, c2, c3, x_pre]`. The convolution sum uses the updated state, not the old state.

## Reset Behavior

The design uses synchronous active-high reset `rst`.

There is no architectural state. After reset, the block is ready to accept an input transaction. If the implementation registers outputs, all output data ports are driven to `32'h00000000` while reset is asserted.

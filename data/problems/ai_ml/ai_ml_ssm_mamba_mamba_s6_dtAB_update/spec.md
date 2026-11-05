# Mamba Selective State Update — Scalar Lane

Compute the selective state update for one state lane: optional additive step bias and softplus, exponential state decay, input injection, readout, optional skip connection and SiLU gating. State is explicitly supplied and returned so calls can be chained. This is the scalar `dstate=1` sub-operator, not the convolution or projection stages of a complete Mamba block.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_mamba_mamba_s6_dtAB_update (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_state, in_x, in_dt, in_A, in_B, in_C, in_D, in_z, in_dt_bias,
    input logic in_has_D, in_has_z, in_has_dt_bias, in_dt_softplus,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_state, out_out
);
```

The nine 32-bit numerical inputs and both numerical outputs use Q16.16 as defined below. The four one-bit controls enable the named optional operations. Disabled operands are ignored.

Input and output transfers occur on rising clock edges when valid and ready are both high and `rst` is low. The producer holds the entire input bundle and valid stable while stalled; the block holds all output fields and `out_valid` stable while stalled. One accepted input produces exactly one ordered output unless reset intervenes. No fixed pipeline latency is required.

### Behavior interface

```python
class ai_ml_ssm_mamba_mamba_s6_dtAB_update:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_state, in_x, in_dt, in_A, in_B, in_C, in_D, in_z,
                in_dt_bias, in_has_D, in_has_z, in_has_dt_bias, in_dt_softplus): ...
```

Arguments and returned fields are nonnegative port bit patterns. One call returns `{"out_state": next_state_bits, "out_out": output_bits}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_out`, `out_state`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_mamba_mamba_s6_dtAB_update`: scalar selective recurrence and ready/valid transport.

## Functional Behavior

All numerical lanes are signed 32-bit Q16.16: raw integer `v` represents `v/65536`. The buses carry their two's-complement bit patterns; all patterns are legal. Sequence indices, where present, are unsigned integers, not fixed point.

Let `S=65536`, `Q=1099511627776` (2^40), and `L=762123384786`. Define `R(n,d)` for positive `d` as nearest-integer division `n/d`, with exact half-way cases rounded to the even integer. Negative results use the same symmetric rule. Define `sat(v)=min(2147483647,max(-2147483648,v))`, `add(a,b)=sat(a+b)`, and `mul(a,b)=sat(R(a*b,S))`. Intermediates within these definitions are mathematical integers; only the stated boundaries saturate.

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

Decode numerical inputs as signed integers and perform these operations in order:

```text
dt = add(in_dt, in_dt_bias) if in_has_dt_bias else in_dt
dt = P(dt) if in_dt_softplus else dt
decay = E(mul(in_A, dt))
drive = mul(mul(in_B, dt), in_x)
next_state = add(mul(in_state, decay), drive)
output = mul(next_state, in_C)
output = add(output, mul(in_x, in_D)) if in_has_D else output
output = mul(output, G(in_z)) if in_has_z else output
```

Return the two's-complement encodings of `next_state` and `output`. Feed `out_state` to the next transaction's `in_state` to compose a recurrence. The block does not retain numerical state implicitly. With zero dt, no dt bias or softplus, and zero x, the state is unchanged because `E(0)=65536`; optional readout/gating still apply.

## Reset Behavior

Reset is synchronous and active-high. On a rising edge with `rst=1`, discard every pending output, clear `out_valid`, and set every output payload field to zero. No transfer is counted during reset. The numerical state is supplied explicitly, so reset clears transport state only; it does not modify a caller's saved state. After reset the block can accept a new transaction.

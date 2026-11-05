# Zamba2SSMDecode

Perform the recurrent state update and readout from the single-token decode
branch of Zamba2's Mamba mixer. The kernel retains two heads, two channels per
head, and three state coordinates per channel. It is deliberately named as an
SSM decode kernel, not a complete attention/Mamba hybrid layer.

## Interface

### RTL interface

```systemverilog
module zamba2_ssm_decode (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [31:0] in_decay,
    input logic [95:0] in_dB, in_C,
    input logic [31:0] in_D,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y,
    output logic [191:0] out_state
);
```

### Behavior interface

```python
class Zamba2SSMDecode:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_decay, in_dB, in_C, in_D): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_state`: 192.
- `out_y`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Each accepted ready/valid input is one token update and produces one output
record with `out_y` and `out_state`. Persist state across transactions.
A stalled output retains its complete payload. No token is applied twice.

## Modules

- `zamba2_ssm_decode`: recurrent state bank, scaled products and channel readout.

## Functional Behavior

All arithmetic words are signed 16-bit two's-complement Q8.8. Packed ports use
least-significant lane first. For head h in [0,1], channel d in [0,1], and state
coordinate n in [0,2]:

- `in_x` and `out_y` lane `2*h+d` denote x[h,d] and y[h,d].
- `in_decay` and `in_D` lane h denote a[h] and D[h].
- `in_dB` and `in_C` lane `3*h+n` denote b[h,n] and C[h,n].
- `out_state` lane `(2*h+d)*3+n` is the updated state S'[h,d,n].

Let wrap(v) reduce to signed 16 bits and
`mul(a,b)=wrap((signed16(a)*signed16(b)) >> 8)`.
Products are evaluated at full signed 32-bit precision; the shift rounds toward
negative infinity. All additions wrap immediately to 16 bits. There is no
saturation. All input bit patterns are defined.

For each channel, first compute every state coordinate:
`S'[h,d,n] = wrap(mul(S[h,d,n],a[h]) + mul(b[h,n],x[h,d]))`.
Starting from zero, accumulate `mul(S'[h,d,n],C[h,n])` in increasing n with
wrap after each addition. Then add `mul(D[h],x[h,d])` and wrap to obtain
y[h,d]. Commit S' as the persistent state and return both the packed state and
four channel results. In particular, readout uses the updated state, not the
old state; the two channels share coefficients but not state.

The source uses `S'=S*dA+dB*x`, followed by a contraction with C and a D*x skip
connection. Inputs a and b are already discretized coefficients; softplus,
exponentiation, convolution, projections, gating/norm, attention, and tensor
batch/sequence processing lie outside this kernel. Fixed Q8.8 arithmetic and
the 2x2x3 dimensions are explicit hardware adaptations, not claims of PyTorch
floating-point equivalence. Coefficients remain inputs, not identity constants.
Returning S' exposes the actual recurrent cache for checking and chaining.

## Reset Behavior

`rst` is synchronous active high. Clear all twelve state words, the output
payload and out_valid. No token is accepted during reset. The next token starts
from zero state. Reset discards any unconsumed output; otherwise output
backpressure must not clear or recompute recurrent state.

# Q16.16 two-by-two Householder QR

Compute a quantized two-by-two Householder QR factorization. Inputs, R entries, the stored reflector tail and tau coefficients are signed Q16.16 bit patterns. This is a fixed-point adaptation of LAPACK's reflector construction/application, not an exact floating-point or real-arithmetic factorization.

## Interface

### RTL interface

```systemverilog
module householder_qr2x2_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a00,
    output logic [31:0] out_a01,
    output logic [31:0] out_a10,
    output logic [31:0] out_a11,
    output logic [31:0] out_tau0,
    output logic [31:0] out_tau1
);
```

### Behavior interface

```python
class HouseholderQR2x2Step:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a00, in_a01, in_a10, in_a11):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a00`, `out_a01`, `out_a10`, `out_a11`, `out_tau0`, `out_tau1`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `householder_qr2x2_step`: the complete task kernel.

## Functional Behavior

Compute a quantized two-by-two Householder QR factorization. Inputs, R entries, the stored reflector tail and tau coefficients are signed Q16.16 bit patterns. This is a fixed-point adaptation of LAPACK's reflector construction/application, not an exact floating-point or real-arithmetic factorization.

Let a,b,c,d be the signed raw integers of the input matrix [[a,b],[c,d]]. Define mul(x,y)=floor(x*y/2^16), div(x,y)=trunc_toward_zero(x*2^16/y), with div(x,0)=0. Keep widened integer intermediates until output packing.

If c=0, return (a,b,0,d,0,0). Otherwise compute:

```text
norm = floor(sqrt(a*a+c*c))
beta = norm if a<0 else -norm
v = div(c,a-beta)
tau = div(beta-a,beta)
dot = b+mul(v,d)
w = mul(tau,dot)
r01 = b-w
r11 = d-mul(v,w)
```

The norm already has Q16.16 scale: its squared operands have 32 fractional bits, so there is no extra shift before square root. Output (out_a00,out_a01,out_a10,out_a11,out_tau0,out_tau1)=(beta,r01,v,r11,tau,0), each modulo 2^32. The implicit reflector vector is [1,v/2^16]; the second scalar reflector is identity. Quantization and output wrap can limit reconstruction accuracy. One input produces one output.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
